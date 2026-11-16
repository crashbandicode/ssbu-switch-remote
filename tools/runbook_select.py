"""Select the TrainingRooms hardware-runbook rows to re-run for a TR source change.

  runbook_select.py <git-range> [--repo TR_REPO] [--runbook DIR] [--json] [--files f1 f2 ...]

<git-range> is anything `git diff --name-only` accepts (e.g. `35268287..HEAD`, `abc123^!`). With --files the changed
paths are given directly (no git). The catalog is the runbook's `rows/*.md`: each `### <ID> <name>` section's
`- **Code paths:**` (backticked globs, relative to the TR repo root), `- **Class:**`, `- **Est:**` and
`- **Covered by:**` (`self` = executed row; `<ID>` = covered by that representative; `pilot F0x` = the 12-row pilot).
A catalog row with `- **Replaces:** pilot F0x` (Class HW, self) is a corrected copy of that pilot row: `pilot F0x`
resolves to it. `- **Page:**` and `- **Start gate:**` are read for runbook_rows_json.py. `--ids` prints only the run
ids, comma-separated, for `runbook_rows_json.py --only`.

Rules (conservative):
  1. a changed file matching a row's Code paths selects that catalog row;
  2. a changed file matching `select-all.txt` (shared/core, build, data) selects EVERY catalog row;
  3. a changed file matching `ignore.txt` (docs, host tests, tools, emulator harness) selects nothing;
  4. any other changed file (unmapped) selects EVERY catalog row and is reported, so the map can be extended.
Selected catalog rows resolve to the rows to EXECUTE through `Covered by`; the run list follows the order of the
one-session script `SESSION.md` (row IDs in order of first appearance) when it exists.
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath

RUNBOOK = Path('/home/user/smash-training-lobby/artifacts/switch-hw-runbook')
TR_REPO = Path('/home/user/tr-fixes-20261113/a-full')
ID = r'[A-Z]+-\d+[A-Za-z]?|F\d\d'
HEAD_RE = re.compile(rf'^###\s+({ID})\s+(.*)$')
FIELD_RE = re.compile(r'^\s*-\s+\*\*(Class|Code paths|Est|Fixture|Pilot|Covered by|Replaces|Page|Start gate|Release):\*\*\s*(.*)$')


def load_globs(path):
    if not path.exists():
        return []
    return [ln.strip() for ln in path.read_text().splitlines() if ln.strip() and not ln.lstrip().startswith('#')]


def parse_cover(val):
    v = val.replace('`', '').strip()
    m = re.match(r'(?i)pilot\s+(F\d\d)', v)
    if m:
        return m.group(1).upper()
    m = re.match(rf'({ID})', v)
    if m:
        return m.group(1)
    return 'self'


def load_rows(runbook):
    rows = []
    for md in sorted((runbook / 'rows').glob('*.md')):
        cur = None
        for line in md.read_text().splitlines():
            m = HEAD_RE.match(line)
            if m:
                cur = {'id': m.group(1), 'name': m.group(2).strip(), 'file': md.name, 'class': '', 'paths': [],
                       'est': None, 'covered_by': 'self', 'replaces': None, 'page': None, 'fixture': '',
                       'start_gate': '', 'release': None}
                rows.append(cur)
                continue
            if cur is None:
                continue
            f = FIELD_RE.match(line)
            if not f:
                continue
            key, val = f.group(1), f.group(2).strip()
            if key == 'Class':
                cur['class'] = val
            elif key == 'Code paths':
                cur['paths'] = re.findall(r'`([^`]+)`', val)
            elif key == 'Est':
                n = re.search(r'\d+', val)
                cur['est'] = int(n.group()) if n else None
            elif key == 'Covered by':
                cur['covered_by'] = parse_cover(val)
            elif key == 'Replaces':  # a corrected copy of a pilot row: `pilot F04` resolves to this row
                m = re.search(r'F\d\d', val)
                cur['replaces'] = m.group() if m else None
            elif key == 'Page':
                cur['page'] = val.replace('`', '').strip() or None
            elif key == 'Fixture':
                cur['fixture'] = val
            elif key == 'Start gate':
                cur['start_gate'] = val
            elif key == 'Release':
                cur['release'] = val.split()[0] if val else None
    return [r for r in rows if r['paths'] or r['class']]


def pilot_aliases(rows):
    """{'F04': 'PF-04'}: pilot rows that a corrected catalog row (`Replaces: pilot F04`, Class HW, self) runs instead."""
    return {r['replaces']: r['id'] for r in rows
            if r.get('replaces') and kind(r['class']) == 'HW' and r['covered_by'] == 'self'}


def session_order(runbook):
    p = runbook / 'SESSION.md'
    if not p.exists():
        return {}
    order = {}
    for tok in re.findall(rf'\b({ID})\b', p.read_text()):
        order.setdefault(tok, len(order))
    return order


def matches(path, glob):
    p = PurePosixPath(path)
    if p.full_match(glob):
        return True
    g = glob.rstrip('/')  # a bare directory (`src/runtime/ui/`) means everything under it
    return '*' not in g and (path == g or path.startswith(g + '/'))


def kind(cls):
    return cls.split()[0].split('(')[0].strip().upper() if cls else 'UNKNOWN'


def select(changed, rows, select_all, ignore):
    picked, why, unmapped, shared, ignored = {}, {}, [], [], []
    every = False
    for f in changed:
        hit = [r for r in rows if any(matches(f, g) for g in r['paths'])]
        if any(matches(f, g) for g in select_all):
            shared.append(f)
            every = True
        elif hit:
            for r in hit:
                picked[r['id']] = r
                why.setdefault(r['id'], []).append(f)
        elif any(matches(f, g) for g in ignore):
            ignored.append(f)
        else:
            unmapped.append(f)
            every = True
    if every:
        for r in rows:
            picked.setdefault(r['id'], r)
            why.setdefault(r['id'], []).append('(all rows: shared or unmapped change)')
    return picked, why, {'shared': shared, 'unmapped': unmapped, 'ignored': ignored, 'all_rows': every}


def resolve(picked, rows):
    """Selected catalog rows -> the rows to execute. HW rows resolve through Covered by (a chain is followed);
    non-HW rows are reported, never executed."""
    by_id = {r['id']: r for r in rows}
    alias = pilot_aliases(rows)
    run, non_hw = {}, []
    for r in picked.values():
        if kind(r['class']) != 'HW':
            non_hw.append(r)
            continue
        target, seen = r['id'], set()
        while target in by_id and by_id[target]['covered_by'] != 'self' and target not in seen:
            seen.add(target)
            target = by_id[target]['covered_by']
        target = alias.get(target, target)
        run.setdefault(target, []).append(r['id'])
    return run, non_hw


def changed_files(repo, rng):
    out = subprocess.run(['git', '-C', str(repo), 'diff', '--name-only', rng], capture_output=True, text=True)
    if out.returncode != 0:
        raise SystemExit(f'git diff failed: {out.stderr.strip()}')
    return [ln for ln in out.stdout.splitlines() if ln]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('range', nargs='?')
    ap.add_argument('--repo', default=str(TR_REPO))
    ap.add_argument('--runbook', default=str(RUNBOOK))
    ap.add_argument('--files', nargs='*')
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--ids', action='store_true', help='print only the run ids, comma-separated '
                    '(for runbook_rows_json.py --only)')
    a = ap.parse_args(argv)
    runbook = Path(a.runbook)
    if a.files is None and not a.range:
        ap.error('give a git range or --files')
    changed = a.files if a.files is not None else changed_files(a.repo, a.range)
    rows = load_rows(runbook)
    by_id = {r['id']: r for r in rows}
    picked, why, info = select(changed, rows, load_globs(runbook / 'select-all.txt'), load_globs(runbook / 'ignore.txt'))
    run, non_hw = resolve(picked, rows)
    order = session_order(runbook)
    run_ids = sorted(run, key=lambda i: (order.get(i, 10 ** 6), i))
    est = sum((by_id[i]['est'] or 0) for i in run_ids if i in by_id)
    result = {'range': a.range, 'changed': len(changed), 'catalog_rows': len(rows), 'catalog_selected': len(picked),
              **info, 'run': [{'id': i, 'name': by_id[i]['name'] if i in by_id else '(pilot row, ledger PLAN.md)',
                               'covers': sorted(run[i]), 'est_s': by_id[i]['est'] if i in by_id else None}
                              for i in run_ids],
              'non_hw': [{'id': r['id'], 'class': r['class']} for r in non_hw], 'run_est_min': round(est / 60, 1)}
    if a.json:
        print(json.dumps(result, indent=1))
        return 0
    if a.ids:
        print(','.join(run_ids))
        return 0
    print(f"range {a.range or '(files)'}: {len(changed)} changed file(s); catalog {len(rows)} rows, "
          f"{len(picked)} selected")
    if info['all_rows']:
        print(f"ALL ROWS selected: shared={info['shared'][:5]} unmapped={info['unmapped'][:5]}")
    print(f"rows to EXECUTE (session order): {len(run_ids)} (~{result['run_est_min']} min)")
    for i in run_ids:
        name = by_id[i]['name'] if i in by_id else '(pilot row)'
        print(f"  {i:8} {name}   covers {len(run[i])}: {', '.join(sorted(run[i])[:6])}")
    if non_hw:
        print(f"non-HW catalog rows touched (not run on hardware): {len(non_hw)}")
        for r in non_hw:
            print(f"  {r['id']:8} {r['class']}")
    if info['ignored'] and not run_ids:
        print(f"ignored only: {info['ignored'][:5]}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
