"""Generate the playbook runner's rows.json (tools/eden-runner/runner.py format) from the hardware runbook catalog.

  runbook_rows_json.py [--runbook DIR] [--out rows.json] [--pilot PLAN.md]

Takes every EXECUTED row (Class HW, `Covered by: self`) from rows/*.md, plus the pilot PLAN.md rows that the catalog
names as `pilot F0x`, in the order of SESSION.md (row ids in order of first appearance; catalog order otherwise). Each
row's hwstep token list (the text after ` -- ` on its `hwstep.py` command lines) becomes:
  press <btns> <ms> / stick <x> <y> <ms> / sleep:S   -> one segment per contiguous input run, as a recipe
                                                        ({"buttons","hold","gap"} / {"stick":[x,y,ms],"gap"})
  still:LABEL                                        -> an image gate "<ID>-<LABEL>" (acquire on the first run,
                                                        full frame) + a shot step
and the row's steps are wrapped in a gif step (preset "evidence"). A play with no gate since the last play gets
"unchecked": true (the first segment of a row whose start is checked by the previous row's last gate).
Nothing here talks to hardware; the runner's hardware backend (harness-lib) plays it.
"""
import argparse
import json
import re
import shlex
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import runbook_select as rs  # noqa: E402

PILOT = Path('/home/user/smash-training-lobby/artifacts/switch-hw-ledger-rc15/PLAN.md')
HOME_ROWS = []
# harness-lib routes hooks (playbook tools/eden-runner, 1bd4ae1): the session boots and ends through them
BOOT_ROW = {'id': 'S-BOOT', 'name': 'HOME -> SSBU -> main -> Training (routes hooks)', 'covers': [],
            'steps': [{'run': 'routes:switch_boot', 'check': True}, {'run': 'routes:switch_to_training', 'check': True},
                      {'run': 'routes:tr_menu', 'check': True}], 'evidence': ['boot log line', 'training HUD']}
CLOSE_ROW = {'id': 'S-CLOSE', 'name': 'close SSBU -> HOME, SSBU selected', 'covers': [],
             'steps': [{'run': 'routes:switch_close', 'check': True}], 'evidence': ['HOME']}


def section_tokens(text, row_id):
    """hwstep token lists inside the `### <row_id> …` section, in order."""
    out, inside = [], False
    for line in text.splitlines():
        m = rs.HEAD_RE.match(line)
        if m:
            inside = m.group(1) == row_id
            continue
        if inside and line.startswith('## '):
            inside = False
        if inside and 'hwstep.py' in line and ' -- ' in line:
            try:
                out.append(shlex.split(line.split(' -- ', 1)[1]))
            except ValueError:
                out.append(line.split(' -- ', 1)[1].split())
    return out


def build_row(row_id, token_lists, name, covers):
    segments, gates, steps = {}, {}, []
    recipe, seg_n, checked = [], 0, False

    def flush():
        nonlocal recipe, seg_n, checked
        if not recipe:
            return
        seg_n += 1
        seg = f'{row_id}-s{seg_n:02d}'
        segments[seg] = {'recipe': recipe, 'cap': 60}
        step = {'play': seg}
        if not checked:
            step['unchecked'] = True
        steps.append(step)
        recipe, checked = [], False

    for toks in token_lists:
        i = 0
        while i < len(toks):
            t = toks[i]
            if t.startswith('sleep:'):
                s = float(t[6:])
                if recipe:
                    recipe[-1]['gap'] = round(recipe[-1].get('gap', 0) + s, 3)
                else:
                    steps.append({'wait_ms': int(s * 1000)})
                i += 1
            elif t.startswith('still:'):
                flush()
                g = f'{row_id}-{t[6:]}'
                gates[g] = {'kind': 'image', 'acquire': True, 'reference': f'refs/{g}.png',
                            'roi': [0, 0, 1280, 720], 'tolerance': 8, 'cap': 15}
                steps += [{'gate': g}, {'shot': g}]
                checked = True
                i += 1
            else:
                parts = t.split()
                if parts[0] == 'press' and len(parts) == 3 and set(parts[1].split('+')) & {'home', 'capture'}:
                    # HOME/Capture can't be in a pad macro (firmware ERR system_button): a backend call instead
                    # (harness-lib switch_hw: switch.launch / switch.terminate / system-button call)
                    # harness-lib 1bd4ae1: HOME lives only in routes hooks; a HOME inside a row is the close route
                    flush()
                    steps.append({'run': 'routes:switch_close', 'check': True,
                                  'note': f'row pressed {parts[1]}: mapped to switch_close; review'})
                    HOME_ROWS.append(row_id)
                    checked = True
                elif parts[0] == 'press' and len(parts) == 3:
                    item = {'buttons': parts[1], 'hold': int(parts[2]), 'gap': 0}
                    prev = recipe[-1] if recipe else None
                    if prev and 'stick' in prev and not prev['gap']:  # stick then press, no sleep: a chord
                        recipe.pop()
                        item['with_stick'] = prev['stick'][:2]
                    recipe.append(item)
                elif parts[0] in ('stick', 'STICK') and len(parts) in (4, 5):
                    if parts[0] == 'STICK':  # STICK l|r <x> <y> <ms>, console units
                        side, x, y, ms = parts[1], float(parts[2]) / 32767, float(parts[3]) / 32767, int(parts[4])
                    else:
                        side, x, y, ms = 'l', float(parts[1]), float(parts[2]), int(parts[3])
                    key = 'stick' if side == 'l' else 'rstick'
                    prev = recipe[-1] if recipe else None
                    if key == 'stick' and prev and 'buttons' in prev and not prev['gap'] and 'with_stick' not in prev:
                        prev['with_stick'] = [round(x, 4), round(y, 4)]  # press then stick, no sleep: a chord
                    else:
                        recipe.append({key: [round(x, 4), round(y, 4), ms], 'gap': 0})
                elif parts[0] == 'clear':
                    recipe.append({'clear': True, 'gap': 0})
                else:
                    raise SystemExit(f'{row_id}: unsupported token {t!r}')
                i += 1
    flush()
    row = {'id': row_id, 'name': name, 'covers': covers,
           'steps': [{'gif': {'name': row_id, 'preset': 'evidence', 'steps': steps}}] if steps else [],
           'evidence': [f'{row_id} gif', *gates]}
    return row, segments, gates


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--runbook', default=str(rs.RUNBOOK))
    ap.add_argument('--pilot', default=str(PILOT))
    ap.add_argument('--out')
    a = ap.parse_args(argv)
    rb = Path(a.runbook)
    rows = rs.load_rows(rb)
    texts = {md.name: md.read_text() for md in (rb / 'rows').glob('*.md')}
    pilot_text = Path(a.pilot).read_text() if Path(a.pilot).exists() else ''
    covers = {}
    for r in rows:
        if rs.kind(r['class']) == 'HW':
            covers.setdefault(r['covered_by'] if r['covered_by'] != 'self' else r['id'], []).append(r['id'])
    by_id = {r['id']: r for r in rows}
    order = rs.session_order(rb)
    run_ids = sorted(covers, key=lambda i: (order.get(i, 10 ** 6), list(covers).index(i)))
    plan = {'game_id': '01006A800016E000', 'start': 'home-ssbu-selected', 'backend': 'hardware',
            'source': 'switch-hw-runbook (generated by ssbu-switch-remote/tools/runbook_rows_json.py)',
            'segments': {}, 'gates': {}, 'rows': []}
    missing = []
    for rid in run_ids:
        if rid in by_id:
            toks = section_tokens(texts[by_id[rid]['file']], rid)
            name = by_id[rid]['name']
        else:  # a pilot row (F0x)
            toks = section_tokens(pilot_text, rid)
            name = f'pilot {rid}'
        if not toks:
            missing.append(rid)
            continue
        row, segs, gates = build_row(rid, toks, name, sorted(covers[rid]))
        plan['rows'].append(row)
        plan['segments'].update(segs)
        plan['gates'].update(gates)
    plan['rows'] = [BOOT_ROW, *plan['rows'], CLOSE_ROW]
    plan['start'] = 'home-ssbu-selected'
    out = Path(a.out) if a.out else rb / 'rows.json'
    out.write_text(json.dumps(plan, indent=1) + '\n')
    print(json.dumps({'out': str(out), 'rows': len(plan['rows']), 'segments': len(plan['segments']),
                      'gates': len(plan['gates']), 'no_commands': missing, 'home_in_row': sorted(set(HOME_ROWS))}))
    return 0


if __name__ == '__main__':
    sys.exit(main())
