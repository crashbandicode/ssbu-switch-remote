"""gif.py's pure parts: the wall-clock cut window and the frame picker."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import gif  # noqa: E402

ROWS = [{'n': i, 'pts': i * 16_666_667, 'wall': 1000.0 + i / 60} for i in range(600)]


class CutWindow(unittest.TestCase):
    def test_cut_window_from_sidecar(self):
        first, last = gif.window_pts(ROWS, 1002.0, 1003.0)
        self.assertEqual(first, 120 * 16_666_667)
        self.assertEqual(last, 180 * 16_666_667)
        self.assertEqual(gif.window_pts(ROWS), (None, None))
        self.assertEqual(gif.window_pts(ROWS, start_wall=1009.9)[0], 594 * 16_666_667)
        with self.assertRaises(ValueError):
            gif.window_pts(ROWS, 2000.0, 2001.0)

    def test_pick_frames_resamples_to_fps(self):
        pts = [r['pts'] for r in ROWS]
        keep = gif.pick_frames(pts, 15)  # 60 -> 15 fps: every 4th frame
        self.assertEqual(keep[:4], [0, 4, 8, 12])
        self.assertEqual(len(keep), 150)
        keep = gif.pick_frames(pts, 10, 120 * 16_666_667, 180 * 16_666_667)
        self.assertEqual(keep[0], 120)
        self.assertLessEqual(keep[-1], 180)
        self.assertEqual(len(keep), 11)

    def test_pick_frames_with_drops_keeps_real_time(self):
        pts = [i * 16_666_667 for i in range(60) if i not in range(10, 20)]  # frames 10-19 dropped
        keep = gif.pick_frames(pts, 10)
        # 59 source frames span 0.983 s: 10 slots of 0.1 s, so the GIF lasts as long as the take.
        self.assertEqual(len(keep), 10)
        shown = [pts[i] for i in keep]
        # The gap covers 167-317 ms: the slots at 200 and 300 ms both show frame 9 (150 ms).
        self.assertEqual(shown[2], shown[3], 'the slot inside the gap repeats the last good frame')
        self.assertEqual(shown[2], 9 * 16_666_667)
        # Every slot shows a frame no newer than its time, and time never runs backwards.
        self.assertTrue(all(s <= k * 100_000_000 + 12_500_000 for k, s in enumerate(shown)))
        self.assertEqual(shown, sorted(shown))


if __name__ == '__main__':
    unittest.main()
