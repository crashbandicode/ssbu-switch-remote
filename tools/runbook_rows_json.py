"""Generate the playbook runner's rows.json (harness-lib tools/eden-runner/runner.py format) from the hardware runbook.

  runbook_rows_json.py [--runbook DIR] [--out rows.json] [--pilot PLAN.md] [--only ID,ID,...] [--acquire]

Rows: every EXECUTED catalog row (Class HW, `Covered by: self`) from rows/*.md, the pilot PLAN.md rows the catalog
names as `pilot F0x` (or the catalog row that `Replaces:` that pilot row), and the session rows `### T-nn` written
in SESSION.md (fixture transitions). Order: SESSION.md (row ids in order of first appearance). `--only` (e.g. from
`runbook_select.py <range> --ids`) keeps those rows, the rows they chain from, and the T rows of their SESSION.md
groups (`## G..` headings). Each row's hwstep token lists (the text after ` -- ` on its `hwstep.py` lines) become:
  press <btns> <ms> / stick <x> <y> <ms> / STICK l|r <x> <y> <ms> / clear / sleep:S
                         -> one segment per contiguous input run, as a recipe for recipe_macro.py
  still:LABEL            -> a shot step "<ID>-<LABEL>" (evidence for review after the run); with --acquire an image
                            gate that acquires its reference on the first run (REVIEW stop after each such row)
  page:<TR page>         -> hooks/tr_page.py:goto (the menu open on that page, found from the lit page tab)
  route:<hook>           -> routes:<hook> (harness-lib switch_boot / switch_to_training / switch_close)
  press home|capture     -> routes:switch_close (HOME can't be in a pad macro); listed under home_in_row
Evidence (--evidence session, the default): ONE continuous OBS recording for the whole run (plan "session_recording";
the runner marks every row start/end and shot in <out>/recording.jsonl) and no gif steps; tools/session_cut.py cuts
clips and stills offline. --evidence gif keeps the old wrapper (one gif step per command line, <= 120 s; ~7 s each
on hardware).
Waits (poll_rewrite, off with --no-poll): the sleep after `press b+up 100` becomes the tr-menu gate; the sleep before
a still with a LIVE reference (refs/live-refs.json) becomes that dim-tolerant image gate (an in-session verdict);
sleeps (<= 2 s) after a row's last input go, since the next row starts on a gate. A `gate:NAME:S` token waits on
gate NAME (S = the sleep it replaced, kept inside a held input).
Estimate: <out stem>.estimate.json, every runner row through the measured hardware cost model (COST).
Menu page: a row that starts on the Training HUD or on an open menu gets a goto step to its page (`- **Page:**`,
else the ID prefix, else the Fixture/Start gate text) when the page changes, when the row before may have turned
pages (an L/R press), or when it starts with the menu open. A row whose start gate is another row's screen (a
"chain" row: placing view, an editor) runs inside the runner row before it.
Every runner row starts on a gate ("training": hw ref image; "tr-menu": the lit-tab hook), and the plan's recover
is hooks/tr_page.py:home, so a failed row is followed by the next one (runner on_failure "continue").
Nothing here talks to hardware; the runner's hardware backend (harness-lib switch_hw.py) plays it.
"""
import argparse
import hashlib
import json
import math
import re
import shlex
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import runbook_select as rs  # noqa: E402

PILOT = Path('/home/user/smash-training-lobby/artifacts/switch-hw-ledger-rc15/PLAN.md')
HOOK = 'hooks/tr_page.py'
PAGES = ['CPU', 'Saves', 'Recording', 'Fighters & Stages', 'Display & Controls']
PREFIX_PAGE = {'CPU': 'CPU', 'SAV': 'Saves', 'REC': 'Recording', 'FS': 'Fighters & Stages', 'DC': 'Display & Controls'}
PILOT_PAGE = {'F01': 'Display & Controls', 'F02': 'Display & Controls', 'F03': 'Display & Controls',
              'F04': 'Display & Controls', 'F06': 'Recording', 'F07': 'Recording', 'F08': 'Recording',
              'F09': 'Recording', 'F10': None, 'F11': 'Recording'}
# Short gaps: tr_page.py reads the lit tab after each press and polls (goto: 4 looks, 0.4 s apart), so the gap is
# only the press settling, not a wait for the screen.
TR_SEGMENTS = {'tr-open': [{'buttons': 'b+up', 'hold': 100, 'gap': 0.35}],
               'tr-r': [{'buttons': 'r', 'hold': 100, 'gap': 0.45}],
               'tr-b': [{'buttons': 'b', 'hold': 100, 'gap': 0.5}]}
# The harness-lib hw-training reference (library/routes/refs/hw-refs.json, the "Total Damage" label of the Training
# panel), copied into the runbook's refs/ so the plan is self-contained. Checked by hooks/tr_page.py:image_gate, the
# dim-tolerant compare (harness-lib routes.dim_compare + structure_ok): the runner's own image gate compares absolute
# pixels, which the console's auto-dimmed screen fails (hw-run-2 F04, 2026-11-16).
TRAINING_GATE = {'kind': 'hook', 'module': HOOK, 'function': 'image_gate', 'reference': 'refs/hw-training.png',
                 'sha256': 'ebe8cabbd0a9b27fb256bf5ad10a75eea3b331721200e50d4e89873009bbf458',
                 'roi': [1096, 142, 1238, 176], 'frame': [1280, 720], 'tolerance': 18, 'cap': 30, 'interval': 0.25}
MENU_GATE = {'kind': 'hook', 'module': HOOK, 'function': 'menu_open_gate', 'cap': 10, 'interval': 0.25}
# One continuous OBS recording per session (harness-lib runner, playbook a80d514): no per-command gif steps; clips and
# stills are cut offline from <out>/recording.jsonl (tools/session_cut.py).
SESSION_RECORDING = {'marks': ['row-start', 'row-end', 'shot'], 'chapters': True, 'log': 'recording.jsonl'}
LIVE_REFS = 'refs/live-refs.json'
LIVE_CAP = 8

# The measured hardware cost model (hw-run-2 actions.jsonl 2026-11-16 13:08-13:12Z, hw-proof-1, pilot receipts).
COST = {'play': 0.15,        # macro.play upload + completion poll beyond the macro's own frames (median 0.13 s)
        'shot': 0.15,        # OBS GetSourceScreenshot round trip, 1080p PNG (0.06-0.2 s)
        'mark': 0.05,        # session_mark: one GetRecordStatus
        'gate': 0.25,        # an image/hook gate that passes on its first poll
        'menu_gate': 0.45,   # tr-menu after B+Up: the menu needs ~0.2-0.3 s to draw, then one poll
        'goto': 2.4,         # home (closed: one look + gate) + tr-open + one look + final look
        'goto_r': 0.7,       # each R page turn (tr-r) inside a goto
        'goto_close': 0.9,   # open=False: tr-b + the training gate
        'gif': 7.0,          # gif mode only: recording.start + stop + mp4 finalize + gif publish
        'run': 0.3}          # another checking hook
ROUTE_COST = {'routes:switch_boot': 101.0, 'routes:switch_to_training': 39.0, 'routes:switch_close': 4.0}
MENU_OPEN = 'press b+up 100'
TRAILING_MAX = 2.0


def poll_rewrite(row_id, token_lists, live=None):
    """Fixed sleeps -> gate polling where a gate exists (the runner proceeds the moment the gate matches):
      1. the sleep right after `press b+up 100` (the TR menu opening; every executed row expects it to open) becomes
         the tr-menu gate (the lit page tab, polled every 0.25 s);
      2. the sleep right before a still that has a LIVE reference (refs/live-refs.json: row, still label) becomes that
         image gate: the row is verdicted in session, and it moves on as soon as the screen matches;
      3. the sleeps after the last input of the row's last command go: the next row starts on a gate (the Training
         HUD reference or the goto hook), which polls instead.
    Returns (new token lists, {'menu': n, 'live': [(label, gate)], 'trailing_s': s})."""
    live = live or {}
    stats = {'menu': 0, 'live': [], 'trailing_s': 0.0}
    out = []
    for li, toks in enumerate(token_lists):
        new = []
        i = 0
        while i < len(toks):
            t = toks[i]
            if t == MENU_OPEN and i + 1 < len(toks) and toks[i + 1].startswith('sleep:'):
                new += [t, f'gate:tr-menu:{toks[i + 1][6:]}']
                stats['menu'] += 1
                i += 2
                continue
            if t.startswith('sleep:') and i + 1 < len(toks) and toks[i + 1].startswith('still:') \
                    and (row_id, toks[i + 1][6:]) in live:
                gate = live[(row_id, toks[i + 1][6:])]
                new.append(f'gate:{gate}:{t[6:]}')
                stats['live'].append((toks[i + 1][6:], gate))
                i += 1
                continue
            if t.startswith('still:') and (row_id, t[6:]) in live and not (
                    new and new[-1].startswith(f'gate:{live[(row_id, t[6:])]}:')):
                gate = live[(row_id, t[6:])]
                new.append(f'gate:{gate}:0')
                stats['live'].append((t[6:], gate))
            new.append(t)
            i += 1
        out.append(new)
    if out:  # 3. sleeps after the last input or still of the last command line
        last = out[-1]
        k = len(last)
        while k and last[k - 1].startswith('sleep:'):
            k -= 1
        trailing = sum(float(t[6:]) for t in last[k:])
        if trailing <= TRAILING_MAX:  # longer ones wait out a load (fighter, stage) the HUD gate can't see
            stats['trailing_s'] = trailing
            out[-1] = last[:k]
    return out, stats


def load_live(rb):
    """{(row, still label): gate name} and the gate specs, from refs/live-refs.json (only references kept by the
    cross-check of refs/build_live_refs.py)."""
    path = Path(rb) / LIVE_REFS
    if not path.is_file():
        return {}, {}
    manifest = json.loads(path.read_text())
    where, gates = {}, {}
    for name, ref in manifest.items():
        if ref.get('verdict') != 'kept':
            continue
        gates[name] = {'kind': 'hook', 'module': HOOK, 'function': 'image_gate', 'reference': ref['reference'],
                       'sha256': ref['sha256'], 'roi': ref['roi'], 'frame': [1280, 720],
                       'tolerance': ref['tolerance'], 'cap': LIVE_CAP, 'interval': 0.25, 'live_verdict': ref['proves']}
        for row, label in ref.get('rows', []):
            where[(row, label)] = name
    return where, gates


def model_seconds(row, segments):
    """A runner row's modelled wall time from its steps (COST above; macros at 60 frames per second)."""
    total = 0.0

    def walk(steps):
        nonlocal total
        for s in steps:
            if 'gif' in s:
                total += COST['gif']
                walk(s['gif'].get('steps', []))
            elif 'play' in s:
                seg = segments.get(s['play'], {})
                total += COST['play'] + sum(a.get('frames', 0) + a.get('gap_frames', 0)
                                            for a in seg.get('recipe', [])) / FPS
            elif 'shot' in s:
                total += COST['shot'] + COST['mark']
            elif 'gate' in s:
                total += COST['menu_gate'] if s['gate'] == 'tr-menu' else COST['gate']
            elif 'wait_ms' in s:
                total += s['wait_ms'] / 1000
            elif 'run' in s:
                if s['run'].endswith(':goto'):
                    total += COST['goto'] + COST['goto_r'] * s.get('est_r', 1) + \
                        (0 if s['args'].get('open') else COST['goto_close'])
                else:
                    total += ROUTE_COST.get(s['run'], COST['run'])
    walk(row['steps'])
    return total + 2 * COST['mark']
HOME_ROWS = []
# harness-lib routes hooks (playbook tools/eden-runner, 1bd4ae1) boot and end the session. routes:tr_menu is not used:
# its hw-tr-menu reference is the Display & Controls key-hint line, and the menu opens on the page used last.
BOOT_ROW = {'id': 'S-BOOT', 'name': 'HOME -> SSBU -> main -> Training (routes hooks), menu proven on D&C', 'covers': [],
            'start': 'home-ssbu-selected',
            'steps': [{'run': 'routes:switch_boot', 'check': True}, {'run': 'routes:switch_to_training', 'check': True},
                      {'run': f'{HOOK}:goto', 'args': {'page': 'Display & Controls', 'open': False}, 'check': True}],
            'evidence': ['boot log line', 'training HUD', 'TR menu tab']}
CLOSE_ROW = {'id': 'S-CLOSE', 'name': 'close SSBU -> HOME, SSBU selected', 'covers': [], 'end': 'home-ssbu-selected',
             'steps': [{'run': f'{HOOK}:home', 'check': True}, {'run': 'routes:switch_close', 'check': True}],
             'evidence': ['HOME']}


def section(text, row_id):
    """(fields, hwstep token lists) of the `### <row_id> …` section, in order."""
    out, fields, inside = [], {}, False
    for line in text.splitlines():
        m = rs.HEAD_RE.match(line)
        if m:
            inside = m.group(1) == row_id
            continue
        if inside and line.startswith('## '):
            inside = False
        if not inside:
            continue
        f = rs.FIELD_RE.match(line)
        if f:
            fields[f.group(1)] = f.group(2).strip()
        if 'hwstep.py' in line and ' -- ' in line:
            try:
                out.append(shlex.split(line.split(' -- ', 1)[1]))
            except ValueError:
                out.append(line.split(' -- ', 1)[1].split())
    return fields, out


def section_tokens(text, row_id):
    return section(text, row_id)[1]


def infer_page(row_id, fields):
    if fields.get('Page'):
        page = fields['Page'].replace('`', '').strip()
        return None if page.lower() in ('none', '-', 'n/a') else page
    prefix = row_id.split('-')[0]
    if prefix in PREFIX_PAGE:
        return PREFIX_PAGE[prefix]
    if row_id in PILOT_PAGE:
        return PILOT_PAGE[row_id]
    text = f"{fields.get('Fixture', '')} {fields.get('Start gate', '')}"
    hits = [p for p in PAGES if re.search(rf'\b(on|page) {re.escape(p)}\b', text)]
    return hits[0] if len(hits) == 1 else None


def first_input(tokens):
    for toks in tokens:
        for t in toks:
            if not t.startswith(('sleep:', 'still:')):
                return t
    return ''


def start_kind(fields, tokens):
    """'training' (Training HUD, menu closed), 'tr-menu' (menu open at page level) or 'chain' (another row's screen)."""
    first = first_input(tokens)
    if first.startswith(('page:', 'route:')) or first in ('press b+up 100', 'press minus 100'):
        return 'training'
    text = fields.get('Start gate', '').lower()
    if re.search(r'menu open|title |recording page|open on', text) and 'menu closed' not in text:
        return 'tr-menu'
    if re.search(r'training hud|menu closed|^training\b|^training,', text):
        return 'training'
    if re.search(r'f0\d end|placing view|editor|ready \(|^ready|fetch|pop-up|from x-|after ', text):
        return 'chain'
    return 'training' if 'training' in text else 'chain'


def turns_pages(tokens):
    """True when an L or R press (or a relaunch) may leave the menu on another page than the row's own."""
    for toks in tokens:
        for t in toks:
            p = t.split()
            if (p[:1] == ['press'] and len(p) == 3 and p[1] in ('l', 'r')) or t.startswith('route:'):
                return True
    return False


def last_page_token(tokens):
    page = None
    for toks in tokens:
        for t in toks:
            if t.startswith('page:'):
                page = t[5:]
    return page


def seconds(recipe_or_tokens):
    total = 0.0
    for t in recipe_or_tokens:
        if t.startswith('sleep:'):
            total += float(t[6:])
        elif t.startswith('still:'):
            total += 0.8
        else:
            p = t.split()
            if p and p[-1].isdigit():
                total += int(p[-1]) / 1000
    return total


FPS = 60
SYSTEM = {'home', 'capture'}
BUTTONS = set('a b x y l r zl zr plus minus lstick rstick up down left right'.split())


def frames(ms):
    return int(round(ms * FPS / 1000))


class Timeline:
    """hwstep's pad semantics: every pad token starts a timed LEASE at the current time and returns at once; only
    sleep:S advances time (hwstep.py docstring; `stick 0 -1 250` then `press b 100` = Down+B). So overlapping leases
    are one input state (R held 7 s while D-pad presses come and go = R+Up, then R, then R+Left ...)."""

    def __init__(self):
        self.t, self.leases = 0, []  # (start_ms, end_ms, kind, value)

    def add(self, kind, value, ms):
        self.leases.append((self.t, self.t + ms, kind, value))

    def clear(self):
        self.leases = [(s, min(e, self.t), k, v) for s, e, k, v in self.leases if s < self.t]

    def busy(self):
        return any(e > self.t for _, e, _, _ in self.leases)

    def end(self):
        return max([self.t] + [e for _, e, _, _ in self.leases])

    def state(self, at):
        btn, sticks = set(), {'l': (0.0, 0.0), 'r': (0.0, 0.0)}
        for s, e, k, v in sorted(self.leases, key=lambda lease: lease[0]):
            if s <= at < e:
                if k == 'press':
                    btn |= set(v)
                else:
                    sticks[k] = v
        return frozenset(btn), sticks['l'], sticks['r']

    def intervals(self):
        """[(frames, buttons, lstick, rstick)] from 0 to the end, adjacent equal states merged."""
        total = self.end()
        cuts = sorted({0, total, *[min(max(x, 0), total) for s, e, _, _ in self.leases for x in (s, e)]})
        out = []
        for a, b in zip(cuts, cuts[1:]):
            n = frames(b) - frames(a)
            if n <= 0:
                continue
            st = self.state(a)
            if out and out[-1][1:] == st:
                out[-1] = (out[-1][0] + n, *st)
            else:
                out.append((n, *st))
        return out


def recipe_of(intervals):
    """Intervals -> (lead neutral frames, recipe for recipe_macro.py, uses the right stick)."""
    lead, recipe, rstick = 0, [], False
    for n, btn, ls, rs_ in intervals:
        if rs_ != (0.0, 0.0):
            rstick = True
        if not btn and ls == (0.0, 0.0) and rs_ == (0.0, 0.0):
            if recipe:
                recipe[-1]['gap_frames'] += n
            else:
                lead += n
            continue
        if btn:
            item = {'buttons': '+'.join(sorted(btn)), 'frames': n, 'gap_frames': 0}
            if ls != (0.0, 0.0):
                item['with_stick'] = [round(ls[0], 4), round(ls[1], 4)]
        else:
            item = {'stick': [round(ls[0], 4), round(ls[1], 4), round(n * 1000 / FPS)], 'frames': n, 'gap_frames': 0}
        if rs_ != (0.0, 0.0):
            item['rstick'] = [round(rs_[0], 4), round(rs_[1], 4)]
        recipe.append(item)
    return lead, recipe, rstick


def macro_doc(name, intervals, game_id='01006A800016E000'):
    """The eden-input-macro file for intervals that use the right stick (recipe_macro.py has no right stick): the
    same format recipe_macro.compile_recipe writes, with sticks [lx, ly, rx, ry]."""
    bits = {'a': 1, 'b': 2, 'x': 4, 'y': 8, 'lstick': 16, 'rstick': 32, 'l': 64, 'r': 128, 'zl': 256, 'zr': 512,
            'plus': 1024, 'minus': 2048, 'left': 4096, 'up': 8192, 'right': 16384, 'down': 32768}
    motion = [{'accel': [0, 0, -1], 'delta_us': 1000, 'gyro': [0, 0, 0]}] * 2
    events, frame = [], 0
    for n, btn, ls, rs_ in intervals:
        mask = sum(bits[b] for b in btn)
        events.append({'buttons': mask, 'frame': frame, 'motion': motion, 'sticks': [ls[0], ls[1], rs_[0], rs_[1]],
                       'triggers': [0, 0]})
        frame += max(n, 2) if (btn or ls != (0.0, 0.0) or rs_ != (0.0, 0.0)) else n
    events.append({'buttons': 0, 'frame': frame, 'motion': motion, 'sticks': [0, 0, 0, 0], 'triggers': [0, 0]})
    return {'bindings': {'gamepad': '', 'keyboard': ''}, 'duration_frames': frame + 1, 'events': events,
            'format': 'eden-input-macro', 'game_id': game_id, 'markers': [], 'name': name,
            'parent': {'marker': '', 'name': ''}, 'player': 0, 'version': 1}


class Builder:
    def __init__(self, acquire=False, base=None, gifs=False):
        self.acquire, self.base, self.gifs = acquire, base, gifs
        self.segments, self.gates = {}, {}
        self.held_stills = []  # stills taken while a lease is held: the gif shows them (a shot would end the hold)

    def command(self, row_id, toks, n):
        """One hwstep command line -> steps (gif-wrapped input runs; run/goto steps between)."""
        out, inner = [], []
        tl = [Timeline()]

        def flush():
            line = tl[0]
            tl[0] = Timeline()
            if not line.leases:
                if line.t:
                    inner.append({'wait_ms': line.t})
                return
            lead, recipe, rstick = recipe_of(line.intervals())
            if lead:
                inner.append({'wait_ms': round(lead * 1000 / FPS)})
            if not recipe:
                return
            seg = f'{row_id}-s{1 + len([s for s in self.segments if s.startswith(f"{row_id}-s")]):02d}'
            total = sum(a['frames'] + a['gap_frames'] for a in recipe)
            entry = {'recipe': recipe, 'cap': int(math.ceil(total / FPS)) + 30}
            if rstick:  # written here; recipe_macro.py skips a segment that has a file and a sha256
                rel = f'segments/{seg}.edenmacro.json'
                path = self.base / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                body = json.dumps(macro_doc(seg, line.intervals()), sort_keys=True, separators=(',', ':'))
                path.write_text(body)
                entry.update(file=rel, sha256=hashlib.sha256(body.encode()).hexdigest(),
                             compiled={'by': 'runbook_rows_json.py (right stick)', 'fps': FPS})
            self.segments[seg] = entry
            inner.append({'play': seg})

        def close_gif():
            nonlocal inner
            flush()
            if inner and self.gifs:
                length = min(120, max(10, int(math.ceil(seconds(toks))) + 5))
                out.append({'gif': {'name': f'{row_id}-c{n}', 'preset': 'evidence', 'length': length,
                                    'steps': inner}})
            elif inner:  # session evidence: the steps run bare; the one session recording is the clip
                out.extend(inner)
            inner = []

        for t in toks:
            line = tl[0]
            if t.startswith('sleep:'):
                line.t += int(round(float(t[6:]) * 1000))
            elif t.startswith('gate:'):  # poll_rewrite: wait on a gate instead of a fixed sleep (gate:NAME:S)
                name, _, was = t[5:].partition(':')
                if line.busy() and line.end() - line.t <= 300:
                    line.t = line.end()  # the press just sent (a 100 ms tap) completes, then the gate polls
                if line.busy():  # inside a held input a gate would end the hold: keep the original sleep
                    line.t += int(round(float(was or 0) * 1000))
                    continue
                flush()
                inner.append({'gate': name})
            elif t.startswith('still:'):
                g = f'{row_id}-{t[6:]}'
                if line.busy():
                    self.held_stills.append(g)
                    continue
                flush()
                if self.acquire:
                    self.gates[g] = {'kind': 'image', 'acquire': True, 'reference': f'refs/{g}.png',
                                     'roi': [0, 0, 1280, 720], 'tolerance': 8, 'cap': 15}
                    inner.append({'gate': g})
                inner.append({'shot': g})
            elif t.startswith('page:'):
                page = t[5:]
                if page not in PAGES:
                    raise SystemExit(f'{row_id}: unknown page token {t!r}')
                close_gif()
                out.append({'run': f'{HOOK}:goto', 'args': {'page': page, 'open': True}, 'check': True})
            elif t.startswith('route:'):
                close_gif()
                out.append({'run': f'routes:{t[6:]}', 'check': True})
            else:
                parts = t.split()
                if parts[0] == 'press' and len(parts) == 3 and set(parts[1].split('+')) & SYSTEM:
                    # HOME/Capture can't be in a pad macro (firmware ERR system_button); harness-lib 1bd4ae1: HOME
                    # lives only in routes hooks, so a HOME inside a row is the close route
                    close_gif()
                    out.append({'run': 'routes:switch_close', 'check': True,
                                'note': f'row pressed {parts[1]}: mapped to switch_close; review'})
                    HOME_ROWS.append(row_id)
                elif parts[0] == 'press' and len(parts) == 3 and set(parts[1].split('+')) <= BUTTONS:
                    line.add('press', tuple(parts[1].split('+')), int(parts[2]))
                elif parts[0] in ('stick', 'STICK') and len(parts) in (4, 5):
                    if parts[0] == 'STICK':  # STICK l|r <x> <y> <ms>, console units
                        side, x, y, ms = parts[1], float(parts[2]) / 32767, float(parts[3]) / 32767, int(parts[4])
                    else:
                        side, x, y, ms = 'l', float(parts[1]), float(parts[2]), int(parts[3])
                    if side not in ('l', 'r'):
                        raise SystemExit(f'{row_id}: unsupported token {t!r}')
                    line.add(side, (x, y), ms)
                elif parts == ['clear']:
                    line.clear()
                else:
                    raise SystemExit(f'{row_id}: unsupported token {t!r}')
        close_gif()
        return out

    def body(self, row_id, token_lists):
        steps = []
        for n, toks in enumerate(token_lists, 1):
            steps += self.command(row_id, toks, n)
        return steps


def mark_checks(steps, checked=False):
    """Every play needs a start check: a gate or a checking hook since the last play, else "unchecked": true."""
    for step in steps:
        if 'gif' in step:
            checked = mark_checks(step['gif']['steps'], checked)
        elif 'play' in step:
            if not checked:
                step['unchecked'] = True
            checked = False
        elif 'gate' in step or ('run' in step and step.get('check')):
            checked = True
        elif 'run' in step:
            checked = False
    return checked


def parse_session(rb):
    """SESSION.md -> (T-row texts by id, {group title: [ids]})."""
    p = rb / 'SESSION.md'
    if not p.exists():
        return '', {}
    text = p.read_text()
    groups, cur = {}, None
    for line in text.splitlines():
        if line.startswith('## '):
            cur = line[3:].strip()
            groups[cur] = []
            continue
        if cur is not None:
            for tok in re.findall(rf'\b({rs.ID})\b', line):
                if tok not in groups[cur]:
                    groups[cur].append(tok)
    return text, groups


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--runbook', default=str(rs.RUNBOOK))
    ap.add_argument('--pilot', default=str(PILOT))
    ap.add_argument('--out')
    ap.add_argument('--only', help='comma-separated row ids to run (runbook_select.py --ids); default: all')
    ap.add_argument('--acquire', action='store_true',
                    help='stills become acquire-on-first-run image gates (a REVIEW stop after each new row)')
    ap.add_argument('--evidence', choices=['session', 'gif'], default='session',
                    help='session (default): one continuous OBS recording (plan "session_recording"), no gif steps; '
                         'gif: the old per-command gif wrappers')
    ap.add_argument('--no-poll', action='store_true',
                    help='keep every fixed sleep (no tr-menu / live-reference gates, no trailing-sleep drop)')
    a = ap.parse_args(argv)
    rb = Path(a.runbook)
    rows = rs.load_rows(rb)
    by_id = {r['id']: r for r in rows}
    alias = rs.pilot_aliases(rows)
    texts = {md.name: md.read_text() for md in (rb / 'rows').glob('*.md')}
    pilot_text = Path(a.pilot).read_text() if Path(a.pilot).exists() else ''
    session_text, groups = parse_session(rb)
    t_ids = [m.group(1) for m in re.finditer(r'^###\s+((?:T|SC)-\d+[A-Za-z]?)\s', session_text, re.M)]

    covers, _ = rs.resolve({r['id']: r for r in rows}, rows)  # Covered-by chains and pilot aliases followed
    covers = {k: covers[k] for k in sorted(covers, key=lambda k: [r['id'] for r in rows].index(k)
                                           if k in by_id else -1)}
    for t in t_ids:
        covers.setdefault(t, [])
    order = rs.session_order(rb)
    run_ids = sorted(covers, key=lambda i: (order.get(i, 10 ** 6), list(covers).index(i)))

    def source(rid):
        if rid in by_id:
            return section(texts[by_id[rid]['file']], rid), by_id[rid]['name']
        if rid in t_ids:
            return section(session_text, rid), f'session {rid}'
        return section(pilot_text, rid), f'pilot {rid}'

    meta = {}
    for rid in run_ids:
        (fields, toks), name = source(rid)
        meta[rid] = {'fields': fields, 'toks': toks, 'name': name, 'page': infer_page(rid, fields),
                     'start': start_kind(fields, toks)}

    # SESSION.md `## Not in the unattended run …`: rows that need the operator (consent, a route, an FTP read)
    held = {i for title, ids in groups.items() if title.lower().startswith('not in the unattended run') for i in ids}
    if not a.only:
        run_ids = [i for i in run_ids if i not in held]
    if a.only:
        want = {alias.get(i.strip(), i.strip()) for i in a.only.split(',') if i.strip()}
        unknown = sorted(want - set(run_ids))
        if unknown:
            raise SystemExit(f'--only: not executed rows: {unknown}')
        keep = set(want)
        for rid in list(want):  # a chain row needs the rows it starts from
            i = run_ids.index(rid)
            while i > 0 and meta[run_ids[i]]['start'] == 'chain':
                i -= 1
                keep.add(run_ids[i])
        for ids in groups.values():
            if keep & set(ids):
                keep |= {i for i in ids if i in t_ids}
        run_ids = [i for i in run_ids if i in keep]

    out = Path(a.out) if a.out else rb / 'rows.json'
    gifs = a.evidence == 'gif'
    b = Builder(acquire=a.acquire, base=out.parent, gifs=gifs)
    plan = {'game_id': '01006A800016E000', 'start': 'home-ssbu-selected', 'backend': 'hardware',
            'source': 'switch-hw-runbook (generated by ssbu-switch-remote/tools/runbook_rows_json.py)',
            'segments': {}, 'gates': {'training': dict(TRAINING_GATE), 'tr-menu': dict(MENU_GATE)},
            'recover': [{'run': f'{HOOK}:home', 'check': True}], 'rows': []}
    if not gifs:
        plan['session_recording'] = dict(SESSION_RECORDING)
    live_where, live_gates = ({}, {}) if (a.no_poll or a.acquire) else load_live(rb)
    plan['gates'].update(live_gates)
    for name, recipe in TR_SEGMENTS.items():
        b.segments[name] = {'recipe': recipe, 'cap': 15}
    missing, unordered, gotos, merged = [], [], 0, {}
    polled = {'menu_gates': 0, 'live_gates': {}, 'trailing_s_dropped': 0.0}
    # a group "## ... (showcase:NAME)": the ids of its "Run order:" line (not ids its prose mentions)
    showcase = {i: m.group(1) for m in re.finditer(r'^## .*\(showcase:([\w-]+)\)\s*\n+Run order: (.*)$', session_text,
                                                   re.M) for i in re.findall(rf'\b({rs.ID})\b', m.group(2))}
    r_presses = {}
    cur_page, dirty = None, True
    out_rows = []
    for rid in run_ids:
        m = meta[rid]
        if not m['toks']:
            missing.append(rid)
            continue
        if rid not in order:
            unordered.append(rid)
        toks = m['toks']
        if not a.no_poll:
            toks, st = poll_rewrite(rid, toks, live_where)
            polled['menu_gates'] += st['menu']
            polled['trailing_s_dropped'] += st['trailing_s']
            if st['live']:
                polled['live_gates'][rid] = st['live']
        steps = b.body(rid, toks)
        if m['start'] == 'chain' and out_rows:
            host = out_rows[-1]
            host['covers'] = sorted(set(host['covers']) | {rid, *covers[rid]})
            host['chained'].append(rid)
            host['steps'] += steps
            merged[rid] = host['id']
        else:
            pre, start = [], m['start']
            page = m['page']
            if page and (start == 'tr-menu' or dirty or page != cur_page):
                pre.append({'run': f'{HOOK}:goto', 'args': {'page': page, 'open': start == 'tr-menu'}, 'check': True})
                gotos += 1
                # the menu reopens on the page used last: R presses from there (unknown after a page turn: 2)
                r_presses[rid] = (PAGES.index(page) - PAGES.index(cur_page)) % len(PAGES) \
                    if cur_page in PAGES and not dirty else 2
                start = 'training'  # goto starts with home: it reaches the Training HUD from any TR screen
            elif start == 'tr-menu':
                pre.append({'gate': 'tr-menu'})
            else:
                start = 'training'
                pre.append({'gate': 'training'})
            if rid in showcase:  # SESSION.md group "(showcase:NAME)": a mark + showcase.jsonl line as it starts
                pre.insert(0, {'run': f'{HOOK}:showcase', 'args': {'name': showcase[rid], 'row': rid}})
            row = {'id': rid, 'name': m['name'], 'covers': sorted(covers[rid]), 'start': start, 'chained': [],
                   'steps': pre + steps}
            if rid in showcase:
                row['showcase'] = showcase[rid]
            if start == 'tr-menu':
                row['recover'] = [{'run': f'{HOOK}:home', 'check': True}, {'play': 'tr-open', 'unchecked': True}]
            out_rows.append(row)
        if m['page']:
            cur_page = m['page']
        cur_page = last_page_token(m['toks']) or cur_page
        dirty = turns_pages(m['toks'])
    rows_out = [dict(BOOT_ROW), *out_rows, dict(CLOSE_ROW)]
    for i, row in enumerate(rows_out):
        mark_checks(row['steps'])
        if i + 1 < len(rows_out):
            row['end'] = rows_out[i + 1]['start'] if 'start' in rows_out[i + 1] else 'training'
        if i > 0 and 'start' not in row:
            row['start'] = rows_out[i - 1]['end']
        if not row.get('chained'):
            row.pop('chained', None)
        row.setdefault('evidence', [f'{row["id"]} gifs and shots' if gifs else
                                    f'{row["id"]} shots; clip cut offline from the session recording'])
    plan['rows'] = rows_out
    plan['segments'] = b.segments
    plan['gates'].update(b.gates)
    old = json.loads(out.read_text()) if out.exists() else {}
    for name, seg in plan['segments'].items():  # keep compiled files whose recipe is unchanged
        prev = old.get('segments', {}).get(name)
        if prev and prev.get('recipe') == seg['recipe'] and prev.get('file') and (out.parent / prev['file']).is_file():
            seg.update({k: prev[k] for k in ('file', 'sha256', 'compiled') if k in prev})
    out.write_text(json.dumps(plan, indent=1) + '\n')
    # the measured cost model, per runner row (estimate.json beside rows.json; RUNBOOK.md / SESSION.md quote it)
    est = {}
    for row in rows_out:
        s = model_seconds(row, plan['segments'])
        s += COST['goto_r'] * (r_presses.get(row['id'], 0) - 1) * any(
            st.get('run', '').endswith(':goto') for st in row['steps'])
        est[row['id']] = round(s, 1)
    est_path = out.with_name(out.stem + '.estimate.json')
    est_path.write_text(json.dumps({'cost_model': COST, 'route_cost': ROUTE_COST, 'evidence': a.evidence,
                                    'total_s': round(sum(est.values())), 'rows': est}, indent=1) + '\n')
    print(json.dumps({'out': str(out), 'rows': len(plan['rows']), 'segments': len(plan['segments']),
                      'gates': len(plan['gates']), 'goto_steps': gotos, 'chained': merged,
                      'no_commands': missing, 'not_in_session': unordered, 'held_for_operator': sorted(held),
                      'home_in_row': sorted(set(HOME_ROWS)), 'stills_in_a_hold': b.held_stills,
                      'evidence': a.evidence, 'menu_gates': polled['menu_gates'],
                      'live_gates': polled['live_gates'], 'trailing_s_dropped': round(polled['trailing_s_dropped'], 1),
                      'model_total_s': round(sum(est.values())), 'model_total_h': round(sum(est.values()) / 3600, 2),
                      'plan_sha256': hashlib.sha256(out.read_bytes()).hexdigest()}))
    return 0


if __name__ == '__main__':
    sys.exit(main())
