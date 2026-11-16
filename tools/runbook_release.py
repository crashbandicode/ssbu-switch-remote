"""Which TrainingRooms release introduced each runbook row's feature (the `- **Release:**` field), from git.

  runbook_release.py [--runbook DIR] [--repo TR_REPO] [--rev 35268287] [--write] [--json]

For each catalog row: its Code paths globs (expanded at --rev) minus the shared menu/UI plumbing every row lists; the
commit that ADDED each remaining file (`git log --diff-filter=A`); the release of a commit is the first release
boundary it is an ancestor of (the crate-version bump commits and release tags below). The row's release is the
NEWEST of its feature files (a feature can't predate a file it needs), capped by an `rcNN` that the row's Eden
evidence names (docs/media/rc11-*.gif: the feature was documented by then). Approximate by design: a refactor that
moved code into a new file makes a feature look newer; fix such a row by hand (its Release field is plain text, and
--write keeps a value that has `(hand)` in it).
"""
import argparse
import fnmatch
import json
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).resolve().parent))
import runbook_select as rs  # noqa: E402

# (label, commit-ish), oldest first: the crate-version bumps (Cargo.toml) and the release tags/ship commits.
BOUNDARIES = [('v0.1.0', 'v0.1.0'), ('rc.1', 'fbcaa695'), ('rc.2', '36d7f465'), ('rc.3', '888fdaaa'),
              ('rc.3.1', 'v0.2.0-rc.3.1'), ('rc.4', '83b8a9d9'), ('rc.5', 'e794ad69'), ('rc.6', 'd116953c'),
              ('rc.10', '32ee7595'), ('rc.11', '0c628724'), ('rc.12', 'v0.2.0-rc.12'), ('rc.13', 'v0.2.0-rc.13'),
              ('rc.14', 'a2bc4f10'), ('rc.15', '207db16c')]
ORDER = [b[0] for b in BOUNDARIES]
SHARED = ['src/model/menu/table.rs', 'src/model/menu/mod.rs', 'src/model/menu/options.rs', 'src/model/menu/chord.rs',
          'src/model/menu/input.rs', 'src/runtime/ui/menu_app.rs', 'src/runtime/ui/worker.rs', 'src/model/ui/frame.rs',
          'src/model/settings.rs', 'src/model/chords.rs', 'src/runtime/features/chords.rs', 'src/model/ui/adapter.rs',
          'src/model/cpu/settings.rs', 'src/model/cpu/state.rs', 'src/runtime/features/cpu.rs',
          'src/model/state/settings.rs', 'src/model/recording_menu.rs', 'src/model/macros/library.rs']


def git(repo, *args):
    return subprocess.run(['git', '-C', str(repo), *args], capture_output=True, text=True)


def release_of(repo, commit, cache):
    if commit not in cache:
        cache[commit] = 'pre-v0.1.0'
        for label, ref in BOUNDARIES:
            if git(repo, 'merge-base', '--is-ancestor', commit, ref).returncode == 0:
                cache[commit] = label
                break
        else:
            cache[commit] = 'rc.15'  # after the rc.15 bump: part of the card's build
    return cache[commit]


def newest(labels):
    known = [x for x in labels if x in ORDER]
    return max(known, key=ORDER.index) if known else (labels[0] if labels else None)


def compute(runbook, repo, rev):
    files = git(repo, 'ls-tree', '-r', '--name-only', rev, '--', 'src').stdout.split()
    added, rel_cache, out = {}, {}, {}
    for row in rs.load_rows(runbook):
        paths = [f for g in row['paths'] for f in files if rs.matches(f, g)]
        specific = [f for f in dict.fromkeys(paths) if f not in SHARED] or list(dict.fromkeys(paths))
        rels = []
        for f in specific[:12]:
            if f not in added:
                log = git(repo, 'log', '--diff-filter=A', '--format=%H', rev, '--', f).stdout.split()
                added[f] = log[-1] if log else None
            if added[f]:
                rels.append(release_of(repo, added[f], rel_cache))
        rel = newest(rels)
        text = (runbook / 'rows' / row['file']).read_text()
        sec = text.split(f'### {row["id"]} ', 1)[1].split('\n### ', 1)[0]
        eden = re.search(r'\*\*Eden:\*\*(.*)', sec)
        cap = re.findall(r'\brc\.?(\d{1,2})\b', eden.group(1)) if eden else []
        if cap and rel in ORDER:
            cap_label = f'rc.{min(int(c) for c in cap)}'
            if cap_label in ORDER and ORDER.index(cap_label) < ORDER.index(rel):
                rel = cap_label
        out[row['id']] = rel or 'unknown'
    return out


def write(runbook, releases):
    for md in sorted((runbook / 'rows').glob('*.md')):
        lines, cur, changed = md.read_text().splitlines(), None, False
        new = []
        for line in lines:
            m = rs.HEAD_RE.match(line)
            if m:
                cur = m.group(1)
            if cur in releases and re.match(r'^\s*-\s+\*\*Release:\*\*', line):
                if '(hand)' in line:
                    releases.pop(cur)
                else:
                    changed = True
                    continue  # rewritten below
            new.append(line)
            if cur in releases and re.match(r'^\s*-\s+\*\*Covered by:\*\*', line):
                new.append(f'- **Release:** {releases[cur]} (git: newest feature file; runbook_release.py)')
                changed = True
        if changed:
            md.write_text('\n'.join(new) + '\n')


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--runbook', default=str(rs.RUNBOOK))
    ap.add_argument('--repo', default=str(rs.TR_REPO))
    ap.add_argument('--rev', default='35268287')
    ap.add_argument('--write', action='store_true')
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args(argv)
    rb = Path(a.runbook)
    rel = compute(rb, Path(a.repo), a.rev)
    if a.write:
        write(rb, dict(rel))
    if a.json:
        print(json.dumps(rel, indent=1))
    else:
        counts = {}
        for v in rel.values():
            counts[v] = counts.get(v, 0) + 1
        print(json.dumps({'rows': len(rel), 'by_release': dict(sorted(counts.items(), key=lambda kv: (
            ORDER.index(kv[0]) if kv[0] in ORDER else -1)))}))
    return 0


if __name__ == '__main__':
    sys.exit(main())
