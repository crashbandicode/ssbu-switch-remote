#!/usr/bin/python3
"""ESP32 v0 hardware check (primary 2026-11-16 ~03:56Z), after flashing, with the Switch docked and the board's OTG
port in the dock:

  pad_v0_test.py detect                    the board's serial device (CH343 / Espressif) and any wrong-port plug
  pad_v0_test.py run --out DIR [--device auto|/dev/...]
      0. VERSION + status: usb_mounted=1 means the dock enumerated the pad
      1. still (expect the HOME menu, SSBU first) -> `press a 100` -> 3 s -> still (expect the game starting)
      2. `press home 100` -> 2 s -> still (expect the HOME menu, SSBU first tile = the image gate passes)
      Every step is a receipt line in DIR/receipts.jsonl with its still in DIR/shots/.
"""
import argparse
import glob
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def detect():
    out = {'by_id': sorted(glob.glob('/dev/serial/by-id/*')), 'tty': sorted(glob.glob('/dev/ttyACM*') +
                                                                            glob.glob('/dev/ttyUSB*'))}
    try:
        lsusb = subprocess.run(['lsusb'], capture_output=True, text=True, timeout=10).stdout
    except OSError:
        lsusb = ''
    out['ch343'] = [l for l in lsusb.splitlines() if '1a86:55d' in l]           # the board's UART bridge
    out['espressif'] = [l for l in lsusb.splitlines() if '303a:' in l]         # OTG port, factory/boot firmware
    out['hori_pad_here'] = [l for l in lsusb.splitlines() if '0f0d:0092' in l]  # OTG port plugged into THIS PC
    cands = [p for p in out['by_id'] if '1a86' in p] or out['tty']
    out['device'] = cands[0] if len(cands) == 1 else None
    out['access'] = bool(out['device']) and os.access(os.path.realpath(out['device']), os.R_OK | os.W_OK)
    if out['hori_pad_here']:
        out['warning'] = 'the board\'s OTG port is plugged into this PC: it belongs in the Switch dock'
    return out


def run(a):
    import homegate
    import usbpad
    d = a.device if a.device != 'auto' else detect()['device']
    if not d:
        raise SystemExit('no single board serial device found; run `pad_v0_test.py detect`')
    out = Path(a.out)
    (out / 'shots').mkdir(parents=True, exist_ok=True)
    c = usbpad.UsbClient(d, keepalive_s=0)
    log = open(out / 'receipts.jsonl', 'a')

    def receipt(action, reply, shot=None):
        line = {'time': time.strftime('%H:%M:%SZ', time.gmtime()), 'action': action, 'reply': reply}
        if shot:
            path = out / 'shots' / f'{time.strftime("%H%M%S", time.gmtime())}-{shot}.png'
            s = c.request('ui.screenshot', {'path': str(path)})
            line['shot'] = str(path) if s.get('ok') else s.get('error')
            if s.get('ok'):
                line['home_first_tile'] = homegate.check(path, require_review=False)
        log.write(json.dumps(line, default=str) + '\n')
        log.flush()
        print(json.dumps(line, default=str), flush=True)
        return line

    def raw(cmd):
        r = c.request('switch.version') if cmd == 'VERSION' else None
        if r is not None:
            return r
        try:
            with c.lock:
                if c.conn.sock is None:
                    c.conn.open()
                return {'ok': True, 'reply': c.conn.call(cmd)}
        except Exception as e:  # noqa: BLE001 - recorded in the receipt
            return {'ok': False, 'error': str(e)}

    receipt('version', raw('VERSION'))
    st = receipt('status', raw('status'))
    mounted = 'usb_mounted=1' in str(st['reply'])
    receipt('dock enumerated the pad', {'ok': mounted})
    before = receipt('still before press a', {}, shot='before-a')
    receipt('press a 100', raw('press a 100'))
    time.sleep(3)
    after = receipt('still after press a', {}, shot='after-a')
    receipt('press home 100', raw('press home 100'))
    time.sleep(2)
    home = receipt('still after press home', {}, shot='after-home')
    summary = {'dock_enumerated': mounted,
               'before_a_home_first_tile': (before.get('home_first_tile') or {}).get('ok'),
               'after_a_left_home': (after.get('home_first_tile') or {}).get('ok') is False,
               'after_home_home_first_tile': (home.get('home_first_tile') or {}).get('ok')}
    summary['ok'] = all(summary.values())
    receipt('summary', summary)
    raw('clear')
    c.conn.close()
    log.close()
    return 0 if summary['ok'] else 2


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('cmd', choices=['detect', 'run'])
    ap.add_argument('--out', default='/home/user/ssbu-switch-remote-work/pad-v0-test')
    ap.add_argument('--device', default='auto')
    a = ap.parse_args(argv)
    if a.cmd == 'detect':
        print(json.dumps(detect(), indent=1))
        return 0
    return run(a)


if __name__ == '__main__':
    sys.exit(main())
