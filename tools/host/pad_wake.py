#!/usr/bin/python3
"""Wake a docked Switch with the ESP32 pad's HOME button, watching the capture card (primary 2026-11-16 ~04:30Z).

  pad_wake.py wake --out DIR [--device auto] [--max 10] [--every 2] [--press-ms 150] [--gap-s 1.0]
      HOME pressed for --press-ms, --gap-s apart, at most --max times; an OBS still after every --every presses;
      stops as soon as a still is not black (mean luminance above --lit). Never presses anything but HOME.
  pad_wake.py poll --out DIR [--seconds 5]
      the console's poll rate: the firmware queues a new HID report each time the console takes the previous one,
      so the growth of STATUS `reports` per second is the console's IN polls per second (capped at 1000 by the
      firmware's 1 ms report task).
Receipts: DIR/receipts.jsonl; stills: DIR/shots/.
"""
import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def luminance(path):
    from PIL import Image, ImageStat
    with Image.open(path) as im:
        return ImageStat.Stat(im.convert('L').resize((64, 36))).mean[0]


class Session:
    def __init__(self, device, out):
        import pad_v0_test
        import usbpad
        if device == 'auto':
            device = pad_v0_test.detect()['device']
        if not device:
            raise SystemExit('no single board serial device found (pad_v0_test.py detect)')
        self.out = Path(out)
        (self.out / 'shots').mkdir(parents=True, exist_ok=True)
        self.c = usbpad.UsbClient(device, keepalive_s=0)
        self.log = open(self.out / 'receipts.jsonl', 'a')
        self.n = len(list((self.out / 'shots').glob('*.png')))

    def line(self, cmd):
        with self.c.lock:
            if self.c.conn.sock is None:
                self.c.conn.open()
            return self.c.conn.call(cmd)

    def receipt(self, action, reply, shot=None):
        rec = {'time': time.strftime('%H:%M:%S', time.gmtime()) + 'Z', 'wall': time.time(), 'action': action,
               'reply': reply}
        if shot:
            self.n += 1
            path = self.out / 'shots' / f'{self.n:02d}-{shot}.png'
            s = self.c.request('ui.screenshot', {'path': str(path)})
            if s.get('ok'):
                rec['shot'], rec['luminance'] = str(path), round(luminance(path), 2)
            else:
                rec['shot'] = s.get('error')
        self.log.write(json.dumps(rec, default=str) + '\n')
        self.log.flush()
        print(json.dumps(rec, default=str), flush=True)
        return rec

    def status(self):
        from switch_remote import parse_kv
        return parse_kv(self.line('STATUS'))

    def close(self):
        try:
            self.line('clear')
        except Exception:  # noqa: BLE001 - best effort on the way out
            pass
        self.c.conn.close()
        self.log.close()


def wake(a):
    s = Session(a.device, a.out)
    try:
        st = s.status()
        s.receipt('status before', st)
        first = s.receipt('still before', {}, shot='before')
        if first.get('luminance', 0) > a.lit:
            s.receipt('summary', {'ok': True, 'presses': 0, 'note': 'screen already on'})
            return 0
        for i in range(1, a.max + 1):
            s.receipt(f'press home {a.press_ms} (#{i})', s.line(f'press home {a.press_ms}'))
            time.sleep(a.gap_s)
            if i % a.every == 0 or i == a.max:
                r = s.receipt(f'still after {i} presses', {}, shot=f'after-{i}-home')
                if r.get('luminance', 0) > a.lit:
                    s.receipt('summary', {'ok': True, 'presses': i, 'luminance': r['luminance']})
                    return 0
        s.receipt('summary', {'ok': False, 'presses': a.max, 'note': 'screen still dark'})
        return 2
    finally:
        s.close()


def poll(a):
    s = Session(a.device, a.out)
    try:
        t0, r0 = time.monotonic(), int(s.status()['reports'])
        time.sleep(a.seconds)
        t1, st = time.monotonic(), s.status()
        rate = (int(st['reports']) - r0) / (t1 - t0)
        s.receipt('console poll rate', {'reports_per_s': round(rate, 1), 'interval_ms': round(1000 / rate, 2)
                                        if rate else None, 'window_s': round(t1 - t0, 2), 'status': st})
        return 0
    finally:
        s.close()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('cmd', choices=['wake', 'poll'])
    ap.add_argument('--out', default='/home/user/ssbu-switch-remote-work/pad-first-test')
    ap.add_argument('--device', default='auto')
    ap.add_argument('--max', type=int, default=10)
    ap.add_argument('--every', type=int, default=2)
    ap.add_argument('--press-ms', type=int, default=150)
    ap.add_argument('--gap-s', type=float, default=1.0)
    ap.add_argument('--lit', type=float, default=8.0, help='mean luminance (0-255) above which the screen is on')
    ap.add_argument('--seconds', type=float, default=5.0)
    a = ap.parse_args(argv)
    return wake(a) if a.cmd == 'wake' else poll(a)


if __name__ == '__main__':
    sys.exit(main())
