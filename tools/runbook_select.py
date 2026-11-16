"""Select the TrainingRooms hardware-runbook rows to re-run for a TR source change.

  runbook_select.py <git-range> [--repo TR_REPO] [--runbook DIR] [--json] [--files f1 f2 ...]

<git-range> is anything `git diff --name-only` accepts (e.g. `35268287..HEAD`, `abc123^!`). With --files the changed
paths are given directly (no git). The row -> code-path map is read from the runbook's `rows/*.md`: each
`### <ID> <name>` section's `- **Code paths:**` line (backticked globs, relative to the TR repo root) and `- **Class:**`.

Rules (conservative):
  1. a changed file matching a row's Code paths selects that row;
  2. a changed file matching `select-all.txt` (shared/core, build, data) selects EVERY row;
  3. a changed file matching `ignore.txt` (docs, host tests, tools, emulator harness) selects nothing;
  4. any other changed file (unmapped) selects EVERY row and is reported, so the map can be extended.
Exit 0; the output lists HW rows to run, then non-HW rows touched (PERF-EXCLUDED / NEEDS-ONLINE / NEEDS-2P).
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath

RUNBOOK = Path('/home/user/smash-training-lobby/artifacts/switch-hw-runbook')
TR_REPO = Path('/home/user/tr-fixes-20261113/a-full')
HEAD_RE = re.compile(r'^###\s+([A-Z]+-?\d+[A-Za-z]?)\s+(.*)$')
FIELD_RE = re.compile(r'^\s*-\s+\*\*(Class|Code paths|Est|Fixture|Session|Pilot):\*\*\s*(.*)$')


def load_globs(path):
    if not path.exists():
        return []
    return [ln.strip() for ln in path.read_text().splitlines() if ln.strip() and not ln.lstrip().startswith('#')]


def load_rows(runbook):
    rows = []
    for md in sorted((runbook / 'rows').glob('*.md')):
        cur = None
        for line in md.read_text().splitlines():
            m = HEAD_RE.match(line)
            if m:
                cur = {'id': m.group(1), 'name': m.group(2).strip(), 'file': md.name, 'class': '', 'paths': [],
                       'est': None, 'session': ''}
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
            elif key == 'Session':
                cur['session'] = val
    return [r for r in rows if r['paths'] or r['class']]


def matches(path, glob):
    p = PurePosixPath(path)
    if p.full_match(glob):
        return True
    # a bare directory glob (`src/runtime/ui/`) or a directory name means everything under it
    g = glob.rstrip('/')
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
    a = ap.parse_args(argv)
    runbook = Path(a.runbook)
    if a.files is None and not a.range:
        ap.error('give a git range or --files')
    changed = a.files if a.files is not None else changed_files(a.repo, a.range)
    rows = load_rows(runbook)
    picked, why, info = select(changed, rows, load_globs(runbook / 'select-all.txt'), load_globs(runbook / 'ignore.txt'))
    order = {r['id']: i for i, r in enumerate(rows)}
    sel = sorted(picked.values(), key=lambda r: order[r['id']])
    hw = [r for r in sel if kind(r['class']) == 'HW']
    other = [r for r in sel if kind(r['class']) != 'HW']
    result = {'range': a.range, 'changed': len(changed), 'rows_total': len(rows), **info,
              'hw': [{'id': r['id'], 'name': r['name'], 'session': r['session'], 'est_s': r['est'],
                      'because': why[r['id']][:3]} for r in hw],
              'non_hw': [{'id': r['id'], 'class': r['class']} for r in other],
              'hw_est_min': round(sum(r['est'] or 0 for r in hw) / 60, 1)}
    if a.json:
        print(json.dumps(result, indent=1))
        return 0
    print(f"range {a.range or '(files)'}: {len(changed)} changed file(s); runbook rows {len(rows)}")
    if info['all_rows']:
        print(f"ALL ROWS selected: shared={info['shared'][:5]} unmapped={info['unmapped'][:5]}")
    print(f"HW rows to run: {len(hw)} (~{result['hw_est_min']} min)")
    for r in hw:
        print(f"  {r['id']:8} {r['session'] or '-':10} {r['name']}   <- {', '.join(why[r['id']][:2])}")
    if other:
        print(f"non-HW rows touched (not run on hardware): {len(other)}")
        for r in other:
            print(f"  {r['id']:8} {r['class']}")
    if info['ignored'] and not hw:
        print(f"ignored only: {info['ignored'][:5]}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
