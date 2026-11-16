#!/usr/bin/python3
"""Replay a verified pad route: each step = one press (pad_nav.py), then the new still must match the reference still
recorded at the same step of an earlier, human-checked run (mean |diff| of the frame below the top clock strip,
greyscale 320x180). The first mismatch stops the route; nothing further is pressed.

  pad_route.py ROUTE.json [--out DIR] [--max-diff 6]
ROUTE.json: [{"press": "down", "ref": "<still>", "label": "R1-down"}, {"press": "a", "ref": ..., "confirm_a": "..."}]
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def frame_diff(a, b, region=(0, 88, 1280, 720)):
    """Mean |diff| (0-255) of a region (1280x720 coordinates; default: below the top clock strip)."""
    from PIL import Image, ImageChops, ImageStat
    with Image.open(a) as x, Image.open(b) as y:
        x = x.convert('L').resize((1280, 720)).crop(tuple(region)).resize((320, max(8, (region[3] - region[1]) // 4)))
        y = y.convert('L').resize((1280, 720)).crop(tuple(region)).resize((320, max(8, (region[3] - region[1]) // 4)))
        return ImageStat.Stat(ImageChops.difference(x, y)).mean[0]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('route')
    ap.add_argument('--out', default='/home/user/ssbu-switch-remote-work/ftp-r2')
    ap.add_argument('--max-diff', type=float, default=0.7)  # same state <= 0.04, adjacent >= 1.42 (r1 stills)
    a = ap.parse_args(argv)
    steps = json.loads(Path(a.route).read_text())
    for i, st in enumerate(steps):
        cmd = ['/usr/bin/python3', '-I', str(HERE / 'pad_nav.py'), 'press', st['press'], st['label'], '--out', a.out]
        if st.get('confirm_a'):
            cmd += ['--confirm-a', st['confirm_a']]
        if st.get('settle_s'):
            cmd += ['--settle-s', str(st['settle_s'])]
        p = subprocess.run(cmd, capture_output=True, text=True)
        if p.returncode != 0:
            print(json.dumps({'step': i, 'label': st['label'], 'stop': 'pad_nav refused or failed',
                              'stderr': p.stderr.strip()[-300:]}), flush=True)
            return 2
        r = json.loads(p.stdout)
        d = frame_diff(r['shot'], st['ref'], st.get('region', (0, 88, 1280, 720)))
        ok = d <= a.max_diff
        if 'dbi_index' in st:  # DBI menus: the highlighted row must be exactly the expected one
            import dbi_menu
            idx, _ = dbi_menu.highlighted(r['shot'])
            ok = ok and idx == st['dbi_index']
        print(json.dumps({'step': i, 'label': st['label'], 'diff': round(d, 2), 'match': ok, 'shot': r['shot'],
                          'ref': Path(st['ref']).name}),
              flush=True)
        if not ok:
            return 3
    return 0


if __name__ == '__main__':
    sys.exit(main())
