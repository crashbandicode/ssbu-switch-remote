"""The reviewed HOME first-tile image gate (homegate.py): positives survive re-encoding, brightness and resolution;
every negative (cursor elsewhere, another game in slot 1, another title, non-HOME frames) is refused."""
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HOST = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HOST))
import homegate as hg  # noqa: E402
from PIL import Image, ImageDraw, ImageEnhance  # noqa: E402

STILL = Path(__file__).resolve().parent / 'data' / 'home-ssbu-first-tile.jpg'  # smoke-01 000-now.png at q92


def jpeg(im, q):
    b = io.BytesIO()
    im.save(b, 'JPEG', quality=q)
    return Image.open(io.BytesIO(b.getvalue())).convert('RGB')


class HomeGate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = Image.open(STILL).convert('RGB')
        cls.bg = cls.src.getpixel((60, 300))

    def verdict(self, im):
        return hg.check(im, require_review=False)

    def test_positives(self):
        for name, im in {
            'q92 copy': self.src,
            'q60, 6 % brighter': ImageEnhance.Brightness(jpeg(self.src, 60)).enhance(1.06),
            '1080p': self.src.resize((1920, 1080), Image.BICUBIC),
            '1080p round trip': jpeg(self.src.resize((1920, 1080)).resize((1280, 720)), 75),
        }.items():
            r = self.verdict(im)
            self.assertTrue(r['ok'], (name, r))
            for k, v in r['scores'].items():
                self.assertLess(v, hg.THRESHOLDS[k] * 0.6, (name, k, r))

    def test_cursor_on_slot_two_is_refused(self):
        c = self.src.copy()
        d = ImageDraw.Draw(c)
        d.rectangle((96, 183, 373, 460), outline=self.bg, width=10)
        d.rectangle((36, 138, 480, 184), fill=self.bg)
        d.text((84, 146), 'Super Mario Bros. Wonder', fill=(40, 190, 200))
        d.rectangle((370, 185, 640, 458), outline=(80, 230, 210), width=6)
        c.paste(self.src.crop((110, 198, 360, 448)), (110, 198))
        r = self.verdict(c)
        self.assertFalse(r['ok'])
        self.assertIn('ring', r['reason'])
        self.assertIn('label', r['reason'])

    def test_another_game_in_slot_one_is_refused(self):
        for box in ((380, 198, 630, 448), (650, 198, 900, 448)):
            c = self.src.copy()
            c.paste(self.src.crop(box), (110, 198))
            r = self.verdict(c)
            self.assertFalse(r['ok'])
            self.assertIn('art', r['reason'])

    def test_another_title_label_is_refused(self):
        c = self.src.copy()
        d = ImageDraw.Draw(c)
        d.rectangle((80, 140, 480, 182), fill=self.bg)
        d.text((84, 146), 'Super Mario Bros. Wonder', fill=(40, 190, 200))
        r = self.verdict(c)
        self.assertEqual(r['reason'], 'mismatch:label')

    def test_non_home_frames_are_refused(self):
        for im in (Image.new('RGB', (1280, 720)), Image.new('RGB', (1280, 720), self.bg),
                   self.src.transpose(Image.FLIP_LEFT_RIGHT)):
            self.assertFalse(self.verdict(im)['ok'])

    def test_unreviewed_references_refuse(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            refs = tmp / 'refs'
            shutil.copytree(hg.REFS, refs)
            review = json.loads((refs / 'review.json').read_text())
            review['reviewed_by'] = None
            (refs / 'review.json').write_text(json.dumps(review))
            r = hg.check(self.src, refs=refs)
            self.assertEqual((r['ok'], r['reason']), (False, 'image_gate_unreviewed'))
        finally:
            shutil.rmtree(tmp)

    def test_committed_references_are_reviewed(self):
        review = json.loads((hg.REFS / 'review.json').read_text())
        self.assertTrue(review['reviewed_by'])
        self.assertTrue(hg.check(self.src)['ok'])


if __name__ == '__main__':
    unittest.main()
