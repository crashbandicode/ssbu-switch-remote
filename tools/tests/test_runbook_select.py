"""runbook_select.py: rule tests on a synthetic runbook, plus historical TR diffs against the real runbook
(skipped when the TR repo or the real rows are absent). Run: /usr/bin/python3 -I -m unittest discover tools/tests"""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import runbook_select as rs  # noqa: E402

ROWS = """# rows
### DC-01 Hitbox visualization: On
- **Class:** HW
- **Est:** 60
- **Code paths:** `src/runtime/features/hitbox.rs`, `src/runtime/ui/display/**`
### DC-02 Input delay readout
- **Class:** PERF-EXCLUDED (frame-exact timing)
- **Code paths:** `src/runtime/features/input_delay.rs`
### REC-01 Record a macro
- **Class:** HW
- **Est:** 90
- **Code paths:** `src/runtime/macros/`
### FS-09 Arena with a friend
- **Class:** NEEDS-2P (local wireless)
- **Code paths:** `src/runtime/lobby/**`
"""


class Rules(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        (d / 'rows').mkdir()
        (d / 'rows' / 'a.md').write_text(ROWS)
        (d / 'select-all.txt').write_text('# shared\nCargo.toml\nsrc/runtime/boot.rs\nsrc/runtime/seams/**\n')
        (d / 'ignore.txt').write_text('*.md\ndocs/**\ntests/**\nsrc/*_tests.rs\n')
        self.d = d
        self.rows = rs.load_rows(d)

    def tearDown(self):
        self.tmp.cleanup()

    def pick(self, files):
        p, _, info = rs.select(files, self.rows, rs.load_globs(self.d / 'select-all.txt'),
                               rs.load_globs(self.d / 'ignore.txt'))
        return sorted(p), info

    def test_parses_rows(self):
        self.assertEqual([r['id'] for r in self.rows], ['DC-01', 'DC-02', 'REC-01', 'FS-09'])
        self.assertEqual(self.rows[0]['paths'], ['src/runtime/features/hitbox.rs', 'src/runtime/ui/display/**'])
        self.assertEqual(rs.kind(self.rows[1]['class']), 'PERF-EXCLUDED')
        self.assertEqual(self.rows[0]['est'], 60)

    def test_feature_file_selects_its_row_only(self):
        self.assertEqual(self.pick(['src/runtime/features/hitbox.rs'])[0], ['DC-01'])

    def test_double_star_spans_dirs(self):
        self.assertEqual(self.pick(['src/runtime/ui/display/legend/key.rs'])[0], ['DC-01'])

    def test_bare_directory_glob(self):
        self.assertEqual(self.pick(['src/runtime/macros/crop.rs'])[0], ['REC-01'])

    def test_shared_selects_all(self):
        p, info = self.pick(['src/runtime/seams/seam_install.rs'])
        self.assertEqual(len(p), 4)
        self.assertTrue(info['all_rows'])

    def test_docs_and_tests_select_nothing(self):
        p, info = self.pick(['README.md', 'docs/menu/cpu.md', 'tests/x.rs', 'src/clip_input_tests.rs'])
        self.assertEqual(p, [])
        self.assertEqual(len(info['ignored']), 4)

    def test_unmapped_source_is_conservative(self):
        p, info = self.pick(['src/runtime/brand_new.rs'])
        self.assertEqual(len(p), 4)
        self.assertEqual(info['unmapped'], ['src/runtime/brand_new.rs'])

    def test_non_hw_rows_are_selected_but_classed(self):
        self.assertEqual(self.pick(['src/runtime/lobby/custom_stages.rs'])[0], ['FS-09'])


REPO = rs.TR_REPO
REAL = rs.RUNBOOK / 'rows'


def git_ok(rev):
    return subprocess.run(['git', '-C', str(REPO), 'cat-file', '-e', rev], capture_output=True).returncode == 0


@unittest.skipUnless(REPO.exists() and any(REAL.glob('*.md')) and git_ok('35268287') and git_ok('ad20a497'),
                     'TR repo or real runbook rows absent')
class Historical(unittest.TestCase):
    """Real TR history through the real runbook map."""

    def run_range(self, rng):
        changed = rs.changed_files(REPO, rng)
        rows = rs.load_rows(rs.RUNBOOK)
        p, why, info = rs.select(changed, rows, rs.load_globs(rs.RUNBOOK / 'select-all.txt'),
                                 rs.load_globs(rs.RUNBOOK / 'ignore.txt'))
        return changed, rows, p, info

    def test_docs_only_descendant_selects_nothing(self):
        # 35268287 -> ad20a497 changes docs only (the wiki the runbook was built from)
        changed, rows, p, info = self.run_range('35268287..ad20a497')
        self.assertTrue(changed)
        self.assertEqual(sorted(p), [], f"docs-only diff selected rows; unmapped={info['unmapped']}")

    def test_every_row_code_path_exists_at_35268287(self):
        files = subprocess.run(['git', '-C', str(REPO), 'ls-tree', '-r', '--name-only', '35268287'],
                               capture_output=True, text=True).stdout.split()
        dead = [(r['id'], g) for r in rs.load_rows(rs.RUNBOOK) for g in r['paths']
                if not any(rs.matches(f, g) for f in files)]
        self.assertEqual(dead, [], f'{len(dead)} code-path globs match no file at 35268287')

    def test_rc14_to_rc15_selects_hitbox_rows(self):
        # 400e2c69 (rc.14) -> 35268287 (rc.15) touched hitbox/article code: the article-hurtbox row must be picked
        if not git_ok('400e2c69'):
            self.skipTest('400e2c69 absent')
        changed, rows, p, info = self.run_range('400e2c69..35268287')
        self.assertTrue(p, 'rc.14 -> rc.15 selected no rows')


if __name__ == '__main__':
    unittest.main()
