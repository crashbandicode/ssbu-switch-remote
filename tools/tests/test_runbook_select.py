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


COVER_ROWS = """### CPU-01 CPU level: 1
- **Class:** HW
- **Covered by:** `CPU-03`
- **Code paths:** `src/runtime/features/cpu_level.rs`
### CPU-02 CPU level: 5
- **Class:** HW
- **Covered by:** CPU-03
- **Code paths:** `src/runtime/features/cpu_level.rs`
### CPU-03 CPU level: 9
- **Class:** HW
- **Est:** 40
- **Covered by:** self
- **Code paths:** `src/runtime/features/cpu_level.rs`
### DC-05 Hitbox key
- **Class:** HW
- **Covered by:** pilot F02
- **Code paths:** `src/runtime/ui/legend.rs`
### DC-06 Input delay
- **Class:** PERF-EXCLUDED (input delay)
- **Covered by:** self
- **Code paths:** `src/runtime/ui/legend.rs`
"""


class Coverage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        (d / 'rows').mkdir()
        (d / 'rows' / 'c.md').write_text(COVER_ROWS)
        (d / 'SESSION.md').write_text('1. F02 (pilot)\n2. CPU-03 level 9\n')
        self.d = d
        self.rows = rs.load_rows(d)

    def tearDown(self):
        self.tmp.cleanup()

    def test_covered_rows_resolve_to_their_representative(self):
        p, _, _ = rs.select(['src/runtime/features/cpu_level.rs'], self.rows, [], [])
        run, non_hw = rs.resolve(p, self.rows)
        self.assertEqual(sorted(run), ['CPU-03'])
        self.assertEqual(sorted(run['CPU-03']), ['CPU-01', 'CPU-02', 'CPU-03'])

    def test_pilot_cover_and_perf_excluded(self):
        p, _, _ = rs.select(['src/runtime/ui/legend.rs'], self.rows, [], [])
        run, non_hw = rs.resolve(p, self.rows)
        self.assertEqual(sorted(run), ['F02'])
        self.assertEqual([r['id'] for r in non_hw], ['DC-06'])

    def test_session_order(self):
        order = rs.session_order(self.d)
        self.assertLess(order['F02'], order['CPU-03'])

    def test_cli_json(self):
        import io, contextlib, json
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rs.main(['--runbook', str(self.d), '--json', '--files', 'src/runtime/features/cpu_level.rs'])
        out = json.loads(buf.getvalue())
        self.assertEqual([r['id'] for r in out['run']], ['CPU-03'])
        self.assertEqual(out['run_est_min'], 0.7)


class PilotReplacement(unittest.TestCase):
    """A corrected copy of a pilot row (`Replaces: pilot F04`) runs instead of the pilot row."""

    def test_pilot_cover_resolves_to_the_replacing_row(self):
        rows = ROWS_REPLACE
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / 'rows').mkdir()
            (d / 'rows' / 'p.md').write_text(rows)
            loaded = rs.load_rows(d)
        self.assertEqual(rs.pilot_aliases(loaded), {'F04': 'PF-04'})
        p, _, _ = rs.select(['src/runtime/features/hitbox.rs'], loaded, [], [])
        run, _ = rs.resolve(p, loaded)
        self.assertEqual(run, {'PF-04': ['DC-29', 'PF-04']})


ROWS_REPLACE = """### DC-29 Article hurtboxes
- **Class:** HW
- **Covered by:** pilot F04
- **Code paths:** `src/runtime/features/hitbox.rs`
### PF-04 Article hurtboxes, Eden setup
- **Class:** HW
- **Covered by:** self
- **Replaces:** pilot F04
- **Page:** Fighters & Stages
- **Code paths:** `src/runtime/features/hitbox.rs`
"""


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
