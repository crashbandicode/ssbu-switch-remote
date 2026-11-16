#!/usr/bin/python3
"""Stills only (no input): one every --every seconds for --seconds, named by seconds since --t0 (a press's wall time),
plus a contact sheet. For boot/title/menu timing in the hardware smoke.

  pad_watch.py --t0 WALL --seconds 40 --every 2.5 --label smokeA-boot [--out DIR]
"""
import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--t0', type=float, required=True)
    ap.add_argument('--seconds', type=float, default=40)
    ap.add_argument('--every', type=float, default=2.5)
    ap.add_argument('--label', required=True)
    ap.add_argument('--out', default='/home/user/ssbu-switch-remote-work/smoke-ab')
    a = ap.parse_args(argv)
    import usbpad
    from PIL import Image, ImageStat
    out = Path(a.out) / 'watch' / a.label
    out.mkdir(parents=True, exist_ok=True)
    c = usbpad.UsbClient('/dev/ttyACM0', keepalive_s=0)
    shots = []
    end = time.time() + a.seconds
    while time.time() < end:
        t = time.time()
        path = out / f'{a.label}-t{t - a.t0:06.1f}s.png'
        r = c.request('ui.screenshot', {'path': str(path)})
        if r.get('ok'):
            with Image.open(path) as im:
                lum = ImageStat.Stat(im.convert('L').resize((64, 36))).mean[0]
            shots.append({'t': round(t - a.t0, 1), 'shot': str(path), 'lum': round(lum, 1)})
            print(json.dumps(shots[-1]), flush=True)
        time.sleep(max(0.0, a.every - (time.time() - t)))
    cols = 4
    thumbs = [Image.open(s['shot']).convert('RGB').resize((320, 180)) for s in shots]
    if thumbs:
        rows = (len(thumbs) + cols - 1) // cols
        sheet = Image.new('RGB', (cols * 320, rows * 180))
        for i, im in enumerate(thumbs):
            sheet.paste(im, ((i % cols) * 320, (i // cols) * 180))
        sheet.save(out / 'contact.png')
    (out / 'index.json').write_text(json.dumps({'t0': a.t0, 'shots': shots}, indent=1))
    print(json.dumps({'contact': str(out / 'contact.png'), 'n': len(shots)}))
    return 0


if __name__ == '__main__':
    sys.exit(main())
