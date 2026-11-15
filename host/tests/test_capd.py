"""Host tests for capd.py's pure parts. Run: /usr/bin/python3 -I -m unittest discover -s host/tests"""
import io
import sys
import unittest
from pathlib import Path

HOST = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HOST))
import capd  # noqa: E402

EVIDENCE = HOST.parent / 'evidence' / 'capture-probe' / 'no-signal-frame.jpg'


def jpeg(colour=(0, 0, 0), size=(1920, 1080), draw=None):
    from PIL import Image
    im = Image.new('RGB', size, colour)
    if draw:
        draw(im)
    out = io.BytesIO()
    im.save(out, 'JPEG', quality=90)
    return out.getvalue()


class DroppedFrames(unittest.TestCase):
    def test_even_arrivals_drop_nothing(self):
        walls = [i / 60 for i in range(120)]
        self.assertEqual(capd.dropped_frames(walls), 0)

    def test_gap_counts_missing_frames(self):
        walls = [0, 1 / 60, 4 / 60, 5 / 60]  # frames 2 and 3 missing
        self.assertEqual(capd.dropped_frames(walls), 2)

    def test_jitter_under_half_a_frame_is_not_a_drop(self):
        walls = [0, 1.4 / 60, 2.2 / 60, 3 / 60]
        self.assertEqual(capd.dropped_frames(walls), 0)

    def test_short_runs(self):
        self.assertEqual(capd.dropped_frames([]), 0)
        self.assertEqual(capd.dropped_frames([1.0]), 0)


class FrameStats(unittest.TestCase):
    def test_black_frame_is_uniform_not_no_signal(self):
        w, h, uniform, no_signal = capd.frame_stats(jpeg())
        self.assertEqual((w, h), (1920, 1080))
        self.assertTrue(uniform)
        self.assertFalse(no_signal)

    @unittest.skipUnless(EVIDENCE.exists(), 'probe frame not present')
    def test_card_no_signal_screen_is_detected(self):
        _, _, uniform, no_signal = capd.frame_stats(EVIDENCE.read_bytes())
        self.assertFalse(uniform)
        self.assertTrue(no_signal)

    def test_game_like_frame_is_signal(self):
        def paint(im):
            from PIL import ImageDraw
            d = ImageDraw.Draw(im)
            for i in range(0, 1920, 120):
                d.rectangle([i, 0, i + 60, 1080], fill=(200, 40, 40))
        _, _, uniform, no_signal = capd.frame_stats(jpeg((30, 90, 160), draw=paint))
        self.assertFalse(uniform)
        self.assertFalse(no_signal)


class Png(unittest.TestCase):
    def test_write_png_scales_to_width(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / 'x.png'
            w, h = capd.write_png(jpeg((10, 20, 30)), out, 1280)
            self.assertEqual((w, h), (1280, 720))
            self.assertTrue(out.exists())
            self.assertFalse(Path(str(out) + '.part').exists())


if __name__ == '__main__':
    unittest.main()
