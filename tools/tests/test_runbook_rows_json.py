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


if __name__ == '__main__':
    unittest.main()
