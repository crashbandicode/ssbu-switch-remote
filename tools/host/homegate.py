#!/usr/bin/python3
"""Reviewed image gate: does a capture-card still show the HOME menu with SSBU as the SELECTED first tile?

Used by the host-side LAUNCH (HOME -> still gate -> A) and TERMINATE (HOME -> still gate -> X -> A) sequences of
the sys-autopilot driver (primary ruling 2026-11-16, LAUNCH re-ruling item 1). Three regions of the still are
compared with reference crops cut from one reviewed capture (refs/home-first-tile-ssbu/review.json):

  art     the first tile's artwork (SSBU, not another game in slot 1)
  ring    the cyan selection ring around the first tile (the cursor is on slot 1)
  label   the selected-title label above the row ("Super Smash Bros. Ultimate")

Each region is scaled down and compared by mean absolute difference (0-255); all three must be under their
thresholds. Boxes are fractions of the frame, so any capture width works. The gate refuses when the references are
not marked reviewed.

  homegate.py check STILL.png        -> JSON verdict
  homegate.py make-refs SOURCE.png   -> rewrites the reference crops (then review them and update review.json)
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

REFS = Path(__file__).resolve().parent / 'refs' / 'home-first-tile-ssbu'
# Fractions of a 1280x720 HOME-menu capture (smoke-01/shots/000-now.png, 2026-11-16 01:3xZ, OBS "Switch" source).
BOXES = {
    'art': (110 / 1280, 198 / 720, 360 / 1280, 448 / 720),
    'ring': (96 / 1280, 183 / 720, 374 / 1280, 461 / 720),
    'label': (36 / 1280, 138 / 720, 480 / 1280, 184 / 720),
}
SIZES = {'art': (64, 64), 'ring': (70, 70), 'label': (222, 23)}
# Calibrated 2026-11-16 (tests/test_homegate.py, same cases): positives (JPEG q60 + 6 % brighter, 1080p, a 1080p
# round trip; the test still is itself a q92 copy, so q60 is a double pass) score art <= 7.8, ring <= 0.3 %, label
# <= 5.3 %; negatives (cursor on slot 2, another game's art in
# slot 1, another title label, black/grey frames) score art >= 71, ring >= 9.7 %, label >= 19 % where they differ.
THRESHOLDS = {'art': 25.0, 'ring': 4.0, 'label': 10.0}
CYAN_MIN = 50


def _region(im, name):
    from PIL import Image
    w, h = im.size
    x0, y0, x1, y1 = BOXES[name]
    crop = im.crop((round(x0 * w), round(y0 * h), round(x1 * w), round(y1 * h))).resize(SIZES[name], Image.BILINEAR)
    if name == 'art':
        return crop
    # The ring and the label text are cyan on a grey background: compare a binarized "cyan-ness" mask
    # (min(G, B) - R > 50; ring ~129, text ~185, background 0), which a brightness change does not move.
    from PIL import ImageChops
    r, g, b = crop.split()
    out = ImageChops.subtract(ImageChops.darker(g, b), r).point(lambda v: 255 if v > CYAN_MIN else 0)
    if name == 'ring':
        # Keep only the ring: blank the interior (the art region has its own check).
        from PIL import ImageDraw
        d = ImageDraw.Draw(out)
        rw, rh = SIZES['ring']
        d.rectangle((5, 5, rw - 6, rh - 6), fill=0)
    return out


def _score(name, a, b):
    """art: mean absolute difference (0-255); ring/label: percent of mask pixels that differ."""
    from PIL import ImageChops, ImageStat
    st = ImageStat.Stat(ImageChops.difference(a, b))
    mad = sum(st.mean) / len(st.mean)
    return mad if name == 'art' else mad / 2.55


def load_refs(refs=REFS):
    from PIL import Image
    review = json.loads((refs / 'review.json').read_text())
    return review, {n: Image.open(refs / f'{n}.png').copy() for n in BOXES}


def check(still, refs=REFS, require_review=True):
    """Verdict for the image at `still` (path or PIL image)."""
    from PIL import Image
    review, ref = load_refs(refs)
    if require_review and not review.get('reviewed_by'):
        return {'ok': False, 'reason': 'image_gate_unreviewed', 'refs': str(refs)}
    im = still if hasattr(still, 'crop') else Image.open(still)
    im = im.convert('RGB')
    scores = {n: round(_score(n, _region(im, n), ref[n]), 2) for n in BOXES}
    failed = [n for n in BOXES if scores[n] >= THRESHOLDS[n]]
    return {'ok': not failed, 'reason': 'home_first_tile_ssbu' if not failed else 'mismatch:' + ','.join(failed),
            'scores': scores, 'thresholds': THRESHOLDS, 'size': list(im.size)}


def make_refs(source, refs=REFS):
    from PIL import Image
    refs.mkdir(parents=True, exist_ok=True)
    im = Image.open(source).convert('RGB')
    for n in BOXES:
        _region(im, n).save(refs / f'{n}.png')
    data = Path(source).read_bytes()
    review = {'source': str(source), 'source_sha256': hashlib.sha256(data).hexdigest(), 'source_size': list(im.size),
              'boxes': BOXES, 'sizes': SIZES, 'reviewed_by': None, 'reviewed_at': None, 'notes': ''}
    (refs / 'review.json').write_text(json.dumps(review, indent=1) + '\n')
    return review


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('cmd', choices=['check', 'make-refs'])
    ap.add_argument('image')
    a = ap.parse_args(argv)
    if a.cmd == 'check':
        r = check(a.image)
        print(json.dumps(r))
        return 0 if r['ok'] else 1
    print(json.dumps(make_refs(a.image)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
