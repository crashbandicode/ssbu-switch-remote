import glob, os
from PIL import Image, ImageChops, ImageStat
d = '/home/user/ssbu-switch-remote-work/pad-first-test/latency/'
# Regions on the 320x180 grabs (x/4 of 1280x720): the controller indicator corner, and the selected-title label.
REG = {'b': (7, 160, 70, 180), 'dright': (9, 34, 180, 46), 'dleft': (9, 34, 180, 46), 'home': (7, 160, 70, 180)}
for button, box in REG.items():
    base = d + f'{button}-base.png'
    if not os.path.exists(base):
        continue
    t0 = os.stat(base).st_mtime_ns
    b = Image.open(base).convert('L').crop(box)
    prev = None
    found = None
    for p in sorted(glob.glob(d + f'{button}-[0-9][0-9][0-9].png')):
        im = Image.open(p).convert('L').crop(box)
        diff = ImageStat.Stat(ImageChops.difference(b, im)).mean[0]
        t = (os.stat(p).st_mtime_ns - t0) / 1e6
        if diff > 8:
            found = (prev, t, round(diff, 1), os.path.basename(p))
            break
        prev = t
    n = len(glob.glob(d + f'{button}-[0-9][0-9][0-9].png'))
    print(button, 'grabs', n, 'first change' if found else 'no regional change',
          f'between {found[0]:.0f} and {found[1]:.0f} ms (diff {found[2]}, {found[3]})' if found else '')
