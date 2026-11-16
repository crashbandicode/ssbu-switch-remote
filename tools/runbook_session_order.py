"""Write the runbook's SESSION.md in the operator's order (2026-11-16): showcase first, then newest -> oldest release.

  runbook_session_order.py [--runbook DIR] [--estimate rows.estimate.json] [--write]

Order (the operator, verbatim: "start with the newest features first then work your way back. I'd like to see the
quicker cpu cycling, eggsercise, break the targets, and drills work"):
  G01 baseline T-01 (every page to its saved defaults, P1 Mario, CPU Mario)
  G02a-d SHOWCASE: quicker CPU cycling, Eggs-ercise, Break the Targets, input drills + SC-02 grading (rows.json "showcase")
  G03 the LEG-* legend checks
  then one group per release, newest first (`- **Release:**`, tools/runbook_release.py), pages grouped inside
  the last group: T-08, X-14 (restart), REC-77 (10-minute cap, optional), then the clean-up T-09.
Constraints kept:
  - dependencies: an executed row whose Fixture/Start gate names another executed row (or a row it covers) runs
    after it, in the same release group (the group of the newest member of that dependency cluster), so no page
    reset falls between a fixture and its user;
  - chained rows (start on another row's screen) stay right after their host; the fighter-mismatch block keeps its
    transitions T-04 (CPU Olimar) and T-05 (back to Mario); the buff block SAV-22..SAV-34 stays together;
  - page resets: before the first row of a page in a release group, a transition row resets that page to its saved
    defaults if an earlier group used the page (replaces the old per-group T-02/T-06/T-07).
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import runbook_select as rs  # noqa: E402
import runbook_rows_json as rj  # noqa: E402

RELEASES = ['pre-v0.1.0', 'v0.1.0', 'rc.1', 'rc.2', 'rc.3', 'rc.3.1', 'rc.4', 'rc.5', 'rc.6', 'rc.10', 'rc.11', 'rc.12',
            'rc.13', 'rc.14', 'rc.15', 'legend']
PAGE_ORDER = ['Fighters & Stages', 'Recording', 'Saves', 'CPU', 'Display & Controls', None]
SHOWCASE = [  # (key, title, rows in order; SC-01 is a session row written here)
    ('cpu-cycling', 'quicker CPU cycling (Cycle Preload keeps the next fighters ready)', ['FS-38', 'FS-39', 'SC-01']),
    ('eggsercise', 'Eggs-ercise', ['FS-54', 'FS-55', 'FS-57', 'FS-58', 'FS-59', 'FS-61', 'FS-62', 'FS-63']),
    ('break-the-targets', 'Break the Targets', ['FS-65', 'FS-68', 'FS-70']),
    # SC-02 (catalog row, rows/showcase.md) sits right after PF-08: that direct Record makes its 4-step drill the
    # grading target, so the grading take needs no list order guessed; PF-09 (Crop) reads the list after it.
    ('drills', 'input drills: record, Make combo, Crop (Eden-exact PF rows), grading, combo drill',
     ['PF-05', 'PF-07', 'PF-06', 'PF-08', 'SC-02', 'PF-09', 'PF-11', 'REC-24', 'REC-25', 'REC-26']),
]
BLOCKS = [['T-04', 'REC-43', 'REC-44', 'REC-46', 'T-05'],
          ['SAV-12', 'SAV-22', 'SAV-23', 'SAV-24', 'SAV-25', 'SAV-26', 'SAV-27', 'SAV-28', 'SAV-29', 'SAV-30', 'SAV-33',
           'SAV-34']]
TAIL = ['T-08', 'X-14', 'REC-77']
TOP = ('"press zl+down 100" sleep:0.3 "press zl+up 100" sleep:0.3 "press zl+up 100" sleep:0.3 "press zl+up 100" '
       'sleep:0.3 "press zl+up 100" sleep:0.3 "press zl+up 100" sleep:0.3 "press zl+up 100" sleep:0.5')
SLUG = {'CPU': 'cpu', 'Saves': 'saves', 'Recording': 'rec', 'Fighters & Stages': 'fs', 'Display & Controls': 'dc'}
SC01 = '''### SC-01 Quicker CPU cycling: six Cycle CPU chords in a row with Cycle Preload 5 (showcase)
- **Class:** session showcase row (not a catalog row; exercises the Cycle CPU chord and Cycle Preload of the rows above; no ids here, so the run order is unchanged)
- **Page:** none
- **Est:** 40
- **Start gate:** Training HUD, menu closed; Cycle Preload 5 (the row before restored it), CPU cycle list All (T-01)
- **Why:** The operator wants to see the quicker CPU cycling: six ZL + D-pad Left presses 2.5 s apart; each loads
  the CPU's next fighter at once (preloaded), visible in the clip; then the CPU Character grid puts Mario back.
```
/usr/bin/python3 -I tools/host/hwstep.py --name SC-01-cycles --out $RUNBOOK/out/SC-01 -- still:before "press zl+left 100" sleep:2.5 still:cycle-1 "press zl+left 100" sleep:2.5 still:cycle-2 "press zl+left 100" sleep:2.5 still:cycle-3 "press zl+left 100" sleep:2.5 still:cycle-4 "press zl+left 100" sleep:2.5 still:cycle-5 "press zl+left 100" sleep:2.5 still:cycle-6
/usr/bin/python3 -I tools/host/hwstep.py --name SC-01-cpu-mario --out $RUNBOOK/out/SC-01 -- "page:Fighters & Stages" ''' + TOP + ''' "press down 100" sleep:0.5 "press down 100" sleep:0.5 still:cc-row "press a 100" sleep:1.2 still:cc-grid "press a 100" sleep:0.8 "press b 100" sleep:0.6 "press b 100" sleep:8.0 still:cpu-mario
```
'''


def page_reset(tid, page):
    s = SLUG[page]
    return (f'### {tid} {page} page back to its saved defaults (before this release group uses it again)\n'
            '- **Class:** session transition (not a catalog row)\n- **Page:** none\n- **Est:** 12\n'
            '- **Start gate:** Training HUD, menu closed\n'
            '- **Why:** an earlier group used this page; its rows start from defaults (Reset page to defaults: TOP, Up '
            'wraps to the last row, A asks, A resets).\n```\n'
            f'/usr/bin/python3 -I tools/host/hwstep.py --name {tid}-{s} --out $RUNBOOK/out/{tid} -- "page:{page}" {TOP} '
            f'"press up 100" sleep:0.5 still:{s}-reset-row "press a 100" sleep:0.8 still:{s}-ask "press a 100" '
            f'sleep:1.0 still:{s}-reset "press b 100" sleep:1.0 still:closed\n```\n')


def t_blocks(text):
    out = {}
    # a transition block ends with its command fence (a group table may follow it)
    for m in re.finditer(r'^### (T-\d+[A-Za-z]?) .*?^```[^\n]*\n.*?^```[ \t]*$', text, re.M | re.S):
        out[m.group(1)] = m.group(0).rstrip() + '\n'
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--runbook', default=str(rs.RUNBOOK))
    ap.add_argument('--estimate', help='rows.estimate.json (model seconds per runner row); default beside rows.json')
    ap.add_argument('--write', action='store_true')
    a = ap.parse_args(argv)
    rb = Path(a.runbook)
    text = (rb / 'SESSION.md').read_text()
    old_order = rs.session_order(rb)
    tb = t_blocks(text)
    held_m = re.search(r'^## Not in the unattended run.*', text, re.M | re.S)
    held_text = held_m.group(0) if held_m else ''
    held = set(re.findall(rf'\b({rs.ID})\b', held_text))
    est_path = Path(a.estimate) if a.estimate else rb / 'rows.estimate.json'
    est = json.loads(est_path.read_text())['rows'] if est_path.is_file() else {}
    rows = rs.load_rows(rb)
    by_id = {r['id']: r for r in rows}
    run, _ = rs.resolve(by_id, rows)  # representative -> covered ids
    executed = [i for i in run if i in by_id and i not in held]
    rep_of = {c: rep for rep, cs in run.items() for c in cs}
    texts = {md.name: md.read_text() for md in (rb / 'rows').glob('*.md')}
    info = {}
    for rid in executed:
        fields, toks = rj.section(texts[by_id[rid]['file']], rid)
        info[rid] = {'release': by_id[rid].get('release') or 'v0.1.0', 'page': rj.infer_page(rid, fields),
                     'start': rj.start_kind(fields, toks) if toks else 'training',
                     'refs': set(re.findall(rf'\b({rs.ID})\b', fields.get('Fixture', '') + ' ' +
                                            fields.get('Start gate', ''))),
                     'old': old_order.get(rid, 10 ** 5), 'name': by_id[rid]['name'], 'est': by_id[rid]['est']}
    ex = set(executed)
    # chained rows ride with the row before them in the old session order
    host = {}
    seq = sorted(ex, key=lambda i: info[i]['old'])
    for prev, cur in zip(seq, seq[1:]):
        if info[cur]['start'] == 'chain':
            host[cur] = host.get(prev, prev)
    # dependency edges (prerequisite -> dependent), through Covered by, between units
    unit = lambda i: host.get(i, i)  # noqa: E731
    deps = {}
    for rid in ex:
        for ref in info[rid]['refs']:
            pre = ref if ref in ex else rep_of.get(ref)
            if pre in ex and unit(pre) != unit(rid):
                deps.setdefault(unit(rid), set()).add(unit(pre))
    showcase_ids = {r for _, _, ids in SHOWCASE for r in ids}
    fixed = showcase_ids | set(TAIL) | {r for b in BLOCKS for r in b}
    # clusters: union-find over dependencies (showcase/tail rows are placed by hand and not merged)
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    units = sorted({unit(i) for i in ex if unit(i) not in fixed and i not in fixed}, key=lambda u: info[u]['old'])
    blocks = [[r for r in b if r in ex or r.startswith('T-')] for b in BLOCKS]
    for b in blocks:  # a block is one unit, keyed by its first catalog row
        members = [r for r in b if r in ex]
        if members:
            units.append(members[0])
    block_of = {r: b for b in blocks for r in b if r in ex}
    for u in units:
        for p in deps.get(u, ()):
            p = block_of.get(p, [p])[0] if p in block_of else p
            if p in units:
                parent[find(u)] = find(p)
    for u in units:
        find(u)

    def rel(u):
        members = [m for m in block_of.get(u, [u]) if m in ex]
        return max((RELEASES.index(info[m]['release']) if info[m]['release'] in RELEASES else 1) for m in members)
    cluster_rel = {}
    for u in units:
        cluster_rel[find(u)] = max(cluster_rel.get(find(u), 0), rel(u))
    groups = {}
    for u in units:
        groups.setdefault(cluster_rel[find(u)], []).append(u)

    def page_key(u):
        p = info[u]['page'] if u in info else None
        return PAGE_ORDER.index(p) if p in PAGE_ORDER else len(PAGE_ORDER)

    def topo(us):
        left, done, out = set(us), set(), []
        while left:
            ready = [u for u in left if not (deps.get(u, set()) & left)]
            u = min(ready or left, key=lambda x: (page_key(x), info[x]['old'] if x in info else 0))
            out.append(u)
            left.discard(u)
            done.add(u)
        return out

    lines = [HEADER]
    intended = []
    total = 0.0
    used_pages, tn = set(), [0]

    def row_est(i):
        return est.get(i, info[i]['est'] if i in info else 0) or 0

    def chained(i):
        return sorted([c for c, h in host.items() if h == i], key=lambda c: info[c]['old'])

    def block_text(t):
        if t == 'SC-01':
            return SC01
        return tb.get(t, '')

    def t_est(t):
        if t in est:
            return est[t]
        m = re.search(r'\*\*Est:\*\*\s*(\d+)', block_text(t))
        return float(m.group(1)) if m else 0.0

    def emit_group(gid, title, fixture, run_order, showcase=None):
        """run_order: catalog ids and transition ids (T-*, SC-01) in run order; the Run order line comes first, so the
        first appearance of every id in SESSION.md is its run position."""
        nonlocal total
        body, sub, tsum, n = [], 0.0, 0.0, 0
        for i in run_order:
            if i in info:
                if host.get(i):
                    continue  # chained: runs inside its host row (listed in the host's Covers)
                n += 1
                cov = sorted(set(c for c in run.get(i, []) if c != i) | set(chained(i)))
                e = row_est(i)
                sub += e
                body.append(f'| {n} | {i} | {info[i]["name"][:90]} | {info[i]["release"]} | {e} | {", ".join(cov)} |')
            else:
                tsum += t_est(i)
        total += sub + tsum
        lines.append(f'## {gid} {title}' + (f' (showcase:{showcase})' if showcase else '') + '\n')
        lines.append(f'Run order: {", ".join(run_order)}.\n')
        intended.extend(run_order)
        lines.append(f'- **Fixture:** {fixture}\n- **Est subtotal (model):** {round(sub + tsum)} s ({n} rows '
                     f'{round(sub)} s + transitions {round(tsum)} s) = {round((sub + tsum) / 60, 1)} min.\n')
        for t in run_order:
            if t not in info:
                lines.append(block_text(t))
        if body:
            lines.append('| # | ID | Row | Release | Est (model s) | Covers |\n|---|---|---|---|---|---|\n'
                         + '\n'.join(body) + '\n')

    def with_chains(ids):
        out = []
        for i in ids:
            out.append(i)
            out += chained(i)
        return out

    emit_group('G01', 'Baseline', 'Training stage, P1 Mario, CPU Mario, every page at its saved defaults.', ['T-01'])
    for k, (key, title, ids) in enumerate(SHOWCASE):
        ids = with_chains([i for i in ids if i in ex or i == 'SC-01'])
        emit_group(f'G02{"abcd"[k]}', f'Showcase {"abcd"[k]}: {title}',
                   'Training; each row sets its own options and restores them.', ids, showcase=key)
        used_pages |= {info[i]['page'] for i in ids if i in info and info[i]['page']}
    leg = sorted([i for i in ex if i.startswith('LEG-')])
    if leg:
        emit_group('G03', 'Legend change checks (LEG-*)', 'Training; the LEGEND release NRO on the card; menu page '
                   'Display & Controls.', leg)
        used_pages.add('Display & Controls')
    gno = 4
    for r in sorted(groups, reverse=True):
        order = []
        for u in topo(groups[r]):
            order += block_of[u] if u in block_of else with_chains([u])
        order = [i for i in order if not i.startswith('LEG-')]
        if not order:
            continue
        seq_out, seen = [], set()
        for i in order:
            p = info[i]['page'] if i in info else None
            if p and p not in seen and p in used_pages and not host.get(i):
                tn[0] += 1
                tid = f'T-{100 + tn[0]}'
                tb[tid] = page_reset(tid, p)
                seq_out.append(tid)
            if p:
                seen.add(p)
            seq_out.append(i)
        used_pages |= seen
        emit_group(f'G{gno:02d}', f'Release {RELEASES[r]} features',
                   f'Training, P1 Mario, CPU Mario; pages {", ".join(p for p in PAGE_ORDER if p in seen)}.', seq_out)
        gno += 1
    tail = [i for i in TAIL if i in ex or i in tb]
    emit_group(f'G{gno:02d}', 'Restart and the long capture', 'Training stage, P1 Mario, CPU Mario; Saves page at '
               'defaults (T-08). REC-77 (10 minutes) is optional: a STOP file before it loses nothing else.', tail)
    emit_group(f'G{gno + 1:02d}', 'Clean-up', 'every page at its saved defaults.', ['T-09'])
    lines.append(f'## Session total\n\n- Modelled: **{round(total)} s = {round(total / 3600, 2)} h** (rows.estimate.json '
                 'per runner row; S-BOOT and S-CLOSE are in rows.estimate.json and in RUNBOOK.md).\n')
    lines.append(held_text)
    out = '\n'.join(lines)
    seen = []
    for tok in re.findall(rf'\b({rs.ID})\b', out):
        if tok not in seen:
            seen.append(tok)
    # the generator runs ids in order of FIRST appearance: that must be the run order written above
    wanted = set(intended)
    appear = [s for s in seen if s in wanted]
    misplaced = [(k, got, want) for k, (got, want) in enumerate(zip(appear, intended)) if got != want][:5]
    if a.write:
        (rb / 'SESSION.md').write_text(out)
    print(json.dumps({'groups': gno + 1, 'page_resets': tn[0], 'order_mismatch': misplaced, 'total_s': round(total),
                      'first_15': [i for i in re.findall(r'^\| \d+ \| ([A-Z]+-\d+[A-Za-z]?) ', out, re.M)][:15]}))
    return 0


HEADER = '''# SESSION: the one hardware session of the TrainingRooms rc.15 + LEGEND runbook (switch-remote-2)

Written by `ssbu-switch-remote/tools/runbook_session_order.py` (2026-11-16), the operator's order: "start with the
newest features first then work your way back. I'd like to see the quicker cpu cycling, eggsercise, break the
targets, and drills work". One boot, one unattended runner invocation. The runner adds S-BOOT (HOME, SSBU, Training
through the harness-lib routes hooks) before G01 and S-CLOSE after the last group. Evidence is ONE continuous OBS
recording (rows.json "session_recording"); clips and stills are cut offline (tools/session_cut.py).

The row IDs below are the order (`runbook_select.py` and `runbook_rows_json.py` read the first appearance of each
ID; a group's "Run order" line is authoritative where transitions sit between rows). Est is the measured cost
model (rows.estimate.json, RUNBOOK.md "Cost model"), not the lanes' guesses. Showcase groups carry
`(showcase:<name>)`: their rows get a showcase mark (hooks/tr_page.py:showcase, <out>/showcase.jsonl) as they start.
'''

if __name__ == '__main__':
    sys.exit(main())
