#!/usr/bin/python3
"""One ESP32-pad press, then a capture-card still (DBI FTP plan r1, docs/FTP-PLAN-r1.md).

  pad_nav.py still LABEL                         a still only (no input)
  pad_nav.py press BUTTON LABEL [--confirm-a WHAT]
      BUTTON: up down left right b plus home | a (A needs --confirm-a, the item the previous still showed selected)
      Sends `press <button> 120`, waits --settle-s, takes a still. The agent checks each still before the next call.

Guards: one press per call; A is refused when the PRE-press still shows the HOME menu with a game tile selected
(SSBU first tile, by the image gate, or any HOME game-row selection ring); A is refused without --confirm-a.
Receipts: <out>/receipts.jsonl; stills: <out>/shots/NN-LABEL.png.
"""
import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

ALLOWED = {'up': 'up', 'down': 'down', 'left': 'left', 'right': 'right', 'b': 'b', 'plus': 'plus', 'home': 'home',
           'a': 'a'}


def game_row_selected(path):
    """True when a still shows the HOME menu with a game tile selected: its cyan selection ring runs down one of the
    gaps between tiles (x = 99, 369, 641, 910, 1180 at 1280x720), which are plain background otherwise. The colour
    test is a ratio (min(G,B) > 1.8 R, min(G,B) - R > 30), so the dimmed HOME screen (ring ~ (25,74,65)) counts too."""
    from PIL import Image
    with Image.open(path) as im:
        im = im.convert('RGB').resize((1280, 720))
        px = im.load()
        # Only the HOME menu counts: its background is neutral grey (bright ~235, or ~71 when dimmed). DBI, hbmenu
        # and other apps are not (a teal DBI dialog crossing the gap lines once looked like a ring: 2026-11-16).
        for x, y in ((60, 300), (40, 130), (1240, 470)):
            r, g, b = px[x, y]
            if max(r, g, b) - min(r, g, b) > 10 or min(r, g, b) < 40:
                return False
        for line in (99, 369, 641, 910, 1180):
            best = 0.0
            for x in range(line - 4, line + 5):
                hits = 0
                ys = range(200, 441, 4)
                for y in ys:
                    r, g, b = px[x, y]
                    m = min(g, b)
                    if m - r > 30 and m > 1.8 * max(r, 1):
                        hits += 1
                best = max(best, hits / len(ys))
            if best > 0.8:
                return True
        return False

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('cmd', choices=['still', 'press'])
    ap.add_argument('args', nargs='+')
    ap.add_argument('--confirm-a', default='')
    ap.add_argument('--launch-ssbu', action='store_true', help='A on HOME: only with the SSBU image gate passing')
    ap.add_argument('--close-dialog', action='store_true', help='A on the close dialog: never on plain HOME')
    ap.add_argument('--device', default='/dev/ttyACM0')
    ap.add_argument('--out', default='/home/user/ssbu-switch-remote-work/ftp-r1')
    ap.add_argument('--settle-s', type=float, default=1.5)
    ap.add_argument('--ms', type=int, default=120)
    a = ap.parse_args(argv)
    import homegate
    import usbpad
    out = Path(a.out)
    (out / 'shots').mkdir(parents=True, exist_ok=True)
    n = len(list((out / 'shots').glob('*.png')))
    c = usbpad.UsbClient(a.device, keepalive_s=0)

    def still(label):
        nonlocal n
        n += 1
        path = out / 'shots' / f'{n:02d}-{label}.png'
        r = c.request('ui.screenshot', {'path': str(path)})
        if not r.get('ok'):
            raise SystemExit(f'still failed: {r.get("error")}')
        v = homegate.check(path)
        return str(path), v

    rec = {'time': time.strftime('%H:%M:%S', time.gmtime()) + 'Z'}
    if a.cmd == 'still':
        rec['action'] = 'still'
        rec['shot'], v = still(a.args[0])
        rec['home_first_tile_ssbu'] = v['ok']
    else:
        button, label = a.args[0], a.args[1]
        if button not in ALLOWED:
            raise SystemExit(f'refused: {button!r} is not an allowed button {sorted(ALLOWED)}')
        if button == 'a':
            if not a.confirm_a:
                raise SystemExit('refused: A needs --confirm-a <the item the last still showed selected>')
            pre, v = still('pre-a-check')
            if a.launch_ssbu:
                # SSBU launch (allowed by the primary 2026-11-16): only with SSBU itself selected, per the image gate
                if not v['ok']:
                    raise SystemExit(f'refused: --launch-ssbu but the still is not HOME with SSBU selected ({pre})')
            elif a.close_dialog:
                # 'Close' in the close-software dialog: never on a plain HOME screen (A there would launch)
                if v['ok']:
                    raise SystemExit(f'refused: --close-dialog but the still is plain HOME with SSBU selected ({pre})')
            elif v['ok'] or game_row_selected(pre):
                raise SystemExit(f'refused: A while a HOME game tile is selected ({pre})')
            rec['pre_a_still'] = pre
            rec['mode'] = 'launch-ssbu' if a.launch_ssbu else 'close-dialog' if a.close_dialog else 'menu'
            rec['confirm_a'] = a.confirm_a
        with c.lock:
            c.conn.open()
            rec['press_wall'] = time.time()
            reply = c.conn.call(f'press {ALLOWED[button]} {a.ms}')
        rec.update({'action': f'press {button} {a.ms}', 'reply': reply})
        time.sleep(a.settle_s)
        rec['shot'], v = still(label)
        rec['home_first_tile_ssbu'] = v['ok']
    c.conn.close()
    with open(out / 'receipts.jsonl', 'a') as f:
        f.write(json.dumps(rec) + '\n')
    print(json.dumps(rec))
    return 0


if __name__ == '__main__':
    sys.exit(main())
