"""runbook_rows_json.py compression pieces: gate polling instead of fixed sleeps, session evidence (no gif steps)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import runbook_rows_json as rj  # noqa: E402


class PollRewrite(unittest.TestCase):
    def test_menu_open_sleep_becomes_the_tr_menu_gate(self):
        toks, st = rj.poll_rewrite('DC-02', [['press b+up 100', 'sleep:1.0', 'still:menu-open', 'press a 100']])
        self.assertEqual(toks[0][:3], ['press b+up 100', 'gate:tr-menu:1.0', 'still:menu-open'])
        self.assertEqual(st['menu'], 1)

    def test_live_reference_replaces_the_sleep_before_its_still(self):
        live = {('PF-08', 'armed'): 'live-drill-armed'}
        toks, st = rj.poll_rewrite('PF-08', [['press a 100', 'sleep:2.0', 'still:armed', 'press zr+down 100']], live)
        self.assertEqual(toks[0][:3], ['press a 100', 'gate:live-drill-armed:2.0', 'still:armed'])
        self.assertEqual(st['live'], [('armed', 'live-drill-armed')])
        # after the menu gate, a live still still gets its own gate
        live = {('PF-01', 'menu-open'): 'live-page-footer'}
        toks, _ = rj.poll_rewrite('PF-01', [['press b+up 100', 'sleep:1.0', 'still:menu-open', 'press b 100']], live)
        self.assertEqual(toks[0][:4], ['press b+up 100', 'gate:tr-menu:1.0', 'gate:live-page-footer:0', 'still:menu-open'])

    def test_short_trailing_sleeps_go_long_ones_stay(self):
        toks, st = rj.poll_rewrite('X', [['press a 100', 'sleep:0.5'], ['press b 100', 'still:closed', 'sleep:1.0']])
        self.assertEqual(toks[1], ['press b 100', 'still:closed'])
        self.assertEqual(toks[0], ['press a 100', 'sleep:0.5'])  # only the last command line
        self.assertEqual(st['trailing_s'], 1.0)
        toks, st = rj.poll_rewrite('X', [['press b 100', 'sleep:8.0']])  # a fighter load: kept
        self.assertEqual(toks[0], ['press b 100', 'sleep:8.0'])


class Builder(unittest.TestCase):
    def test_session_mode_has_no_gif_steps_and_gates_inline(self):
        b = rj.Builder(gifs=False)
        steps = b.body('DC-02', [['press b+up 100', 'gate:tr-menu:1.0', 'still:menu-open', 'press b 100', 'sleep:0.5']])
        kinds = [next(iter(s)) for s in steps]
        self.assertNotIn('gif', kinds)
        self.assertEqual(kinds[:3], ['play', 'gate', 'shot'])
        self.assertEqual(steps[1], {'gate': 'tr-menu'})

    def test_gif_mode_wraps_like_before(self):
        steps = rj.Builder(gifs=True).body('DC-02', [['press b+up 100', 'sleep:1.0', 'still:menu-open']])
        self.assertEqual(list(steps[0]), ['gif'])

    def test_a_gate_inside_a_held_input_keeps_the_sleep(self):
        b = rj.Builder(gifs=False)
        steps = b.body('SAV-40', [['press r 3000', 'gate:live-x:1.0', 'press up 100', 'sleep:0.5']])
        self.assertNotIn({'gate': 'live-x'}, steps)


class Vocabulary(unittest.TestCase):
    """GATES.md tokens, Expected/shots for the judge, clean-state steps (switch-remote-2, 2026-11-16)."""

    def test_mark_and_check_tokens(self):
        b = rj.Builder(gifs=False)
        steps = b.body('FS-01', [['mark:FS-01-before', 'press a 100', 'check:FS-01-on', 'still:after']])
        self.assertEqual(steps[0], {'mark': 'FS-01-before'})
        self.assertIn({'gate': 'FS-01-on', 'effect': True}, steps)
        self.assertLess(next(i for i, s in enumerate(steps) if 'play' in s), steps.index({'gate': 'FS-01-on',
                                                                                            'effect': True}))
        self.assertEqual(b.checks, {'FS-01': ['FS-01-on']})
        with self.assertRaises(SystemExit):  # names start with the row id
            rj.Builder(gifs=False).body('FS-01', [['check:FS-02-on']])
        self.assertEqual(rj.first_input([['mark:FS-01-x', 'press a 100']]), 'press a 100')

    def test_seek_p1_practice_off_home_tokens(self):
        steps = rj.Builder(gifs=False).body('PF-08', [['seek:Recording|Combos & Sequences|+ Record drill…',
                                                        'press a 100', 'practice-off', 'home', 'p1:Mario']])
        self.assertEqual(steps[0], {'run': 'hooks/tr_seek.py:seek', 'check': True,
                                    'args': {'page': 'Recording', 'section': 'Combos & Sequences',
                                             'row': '+ Record drill…'}})
        self.assertEqual([s.get('run') for s in steps[2:]], ['hooks/clean_state.py:practice_games_off', 'hooks/tr_page.py:home',
                                                             'hooks/tr_seek.py:p1_is'])
        self.assertEqual(rj.start_kind({}, [['seek:Recording|Options|Loop']]), 'training')
        self.assertTrue(rj.turns_pages([['seek:Recording|Options|Loop']]))
        with self.assertRaises(SystemExit):
            rj.Builder(gifs=False).body('X', [['seek:Nowhere|a|b']])

    def test_x_guard_set_and_p1_tokens(self):
        # X on an open page saves the defaults: refused; X on the HUD before any menu is fine; audited rows pass
        self.assertTrue(rj.x_guard('ZZ-01', [['press b+up 100', 'press down 100', 'press x 100']]))
        self.assertTrue(rj.x_guard('ZZ-01', [['page:Saves'], ['press b 100', 'press x 100']]))  # maybe a page
        self.assertEqual(rj.x_guard('ZZ-01', [['press x 100', 'press b+up 100', 'home', 'press x 100']]), [])
        self.assertEqual(rj.x_guard('PF-06', [['press b+up 100', 'press x 100']]), [])  # X_ALLOWED: the editor
        steps = rj.Builder(gifs=False).body('FS-01', [['set:Fighters & Stages|Player 1 and CPU|Player Swapping|On',
                                                       'home', 'p1-mark', 'press zl+right 100', 'p1-changed']])
        self.assertEqual(steps[0]['args'], {'page': 'Fighters & Stages', 'section': 'Player 1 and CPU',
                                            'row': 'Player Swapping', 'value': 'On'})
        self.assertEqual([s.get('run') for s in steps if 'run' in s],
                         ['hooks/clean_state.py:set_row', 'hooks/tr_page.py:home', 'hooks/clean_state.py:p1_mark',
                          'hooks/clean_state.py:p1_changed'])
        with self.assertRaises(SystemExit):
            rj.Builder(gifs=False).body('X', [['set:Saves|a|b|Maybe']])
        table = rj.known_issue_table([{'id': 'FS-05', 'name': 'Player Character: grid pick'},
                                      {'id': 'FS-66', 'name': 'placing'}, {'id': 'DC-01', 'name': 'HBV'}])
        self.assertEqual((table['a']['rows'], table['b']['rows']), (['FS-05'], ['FS-66']))

    def test_labelled_shots_and_vocabulary_checks(self):
        steps = [{'run': 'hooks/clean_state.py:ensure_clean', 'check': True}, {'shot': 'R-before'}, {'play': 'R-s01'},
                 {'shot': 'R-mid'}, {'play': 'R-s02'}, {'shot': 'R-after'}]
        self.assertEqual([s['phase'] for s in rj.labelled_shots(steps, 1)], ['before', 'step', 'after'])
        gates = {'R-on': {'kind': 'change', 'mark': 'R-b', 'roi': [0, 0, 9, 9], 'min_diff': 4, 'effect': True}}
        rows = [{'id': 'R', 'steps': [{'play': 'x'}, {'gate': 'R-on', 'effect': True}, {'gate': 'R-x', 'effect': True}]}]
        errors = rj.check_vocabulary(rows, None, gates, set())
        self.assertTrue(any('not defined' in e for e in errors))
        self.assertTrue(any('needs mark:R-b' in e for e in errors))
        self.assertTrue(any('min_diff 4 < 6' in e for e in errors))

    def test_effect_gates_files_and_modes(self):
        import json
        import tempfile
        d = Path(tempfile.mkdtemp())
        (d / 'fs.json').write_text(json.dumps({'gates': {
            'FS-01-log': {'kind': 'log', 'source': 'console_log', 'pattern': 'x', 'cap': 10},
            'FS-01-img': {'kind': 'image', 'reference': 'refs/effect/a.png', 'roi': [0, 0, 4, 4], 'tolerance': 9,
                          'scale': 'fit', 'cap': 10, 'effect': True}}}))
        (d / 'fs.validation.json').write_text('{"not": "gates"}')
        gates, _ = rj.load_effect_gates(d)
        self.assertTrue(all(g['effect'] for g in gates.values()))
        self.assertEqual((gates['FS-01-img']['kind'], gates['FS-01-img']['function']), ('hook', 'image_gate'))
        self.assertEqual(rj.modes_of('FS-65', {}), ['btt'])
        self.assertEqual(rj.modes_of('FS-65', {'Mode-on': 'none'}), [])
        self.assertEqual(rj.modes_of('X-01', {'Mode-on': 'eggs, drill'}), ['eggs', 'drill'])
        with self.assertRaises(SystemExit):
            rj.modes_of('X-01', {'Mode-on': 'lava'})


if __name__ == '__main__':
    unittest.main()
