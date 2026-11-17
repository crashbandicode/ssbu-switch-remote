#!/usr/bin/python3
"""Flash the switch-pad firmware onto the ESP32-S3 board through its UART port, with a receipt (DESIGN.md 13).

  flash_pad.py --build <tools/build_pad.ps1 output dir> --out <receipt dir> [--port auto|/dev/...]
               [--esptool /home/user/venvs/esptool/bin/esptool.py]

1. Every image's sha256 must match the build's build.json.
2. The port: `auto` = the one CH343 (VID 1a86) under /dev/serial/by-id; anything else is refused.
3. The port must be readable and writable by this user (else: the operator's udev/dialout fix, DESIGN.md 13).
4. esptool read_mac (chip identity), then write_flash with the build's flasher_args.json (offsets, mode, freq, size);
   esptool verifies each region's hash after writing; every region must report "Hash of data verified".
5. After the hard reset the board must answer `VERSION` with switch-pad <expected version> over the same port.
The receipt (flash-receipt.json) holds the port, chip MAC, image sha256s, esptool's verify lines and the reply.
The board's OTG port may stay in the dock: flashing uses only the UART port.
"""
import argparse
import glob
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

DEFAULT_ESPTOOL = '/home/user/venvs/esptool/bin/esptool.py'
CH343_GLOB = '/dev/serial/by-id/*1a86*'
BAUD = 460800


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def resolve_port(port):
    if port != 'auto':
        return port
    found = sorted(glob.glob(CH343_GLOB))
    if len(found) != 1:
        raise SystemExit(f'refused: expected exactly one CH343 (VID 1a86) under /dev/serial/by-id, found {found}: '
                         'plug the board\'s UART port (beside BOOT/IO0) into this host, or pass --port')
    return found[0]


def check_build(build):
    info = json.loads((Path(build) / 'build.json').read_text())
    for name, want in info['images'].items():
        got = sha(Path(build) / name)
        if got != want['sha256']:
            raise SystemExit(f'refused: {name} sha256 {got} != build.json {want["sha256"]}')
    return info


def flash_command(esptool, port, build):
    fa = json.loads((Path(build) / 'flasher_args.json').read_text())
    files = []
    for offset, rel in sorted(fa['flash_files'].items(), key=lambda kv: int(kv[0], 16)):
        files += [offset, str(Path(build) / Path(rel).name)]
    return [esptool, '--chip', fa.get('extra_esptool_args', {}).get('chip', 'esp32s3'), '--port', port,
            '--baud', str(BAUD), '--before', 'default_reset', '--after', 'hard_reset', 'write_flash',
            *fa['write_flash_args'], *files], len(fa['flash_files'])


def run(cmd, timeout=180):
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return p.returncode, p.stdout + p.stderr


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--build', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--port', default='auto')
    ap.add_argument('--esptool', default=DEFAULT_ESPTOOL)
    ap.add_argument('--expect-version', default='0.2.1')
    ap.add_argument('--boot-wait', type=float, default=2.0)
    a = ap.parse_args(argv)
    out = Path(a.out)
    if (out / 'flash-receipt.json').exists():
        raise SystemExit('refused: flash-receipt.json exists; use a new --out')
    info = check_build(a.build)
    port = resolve_port(a.port)
    real = os.path.realpath(port)
    if not os.access(real, os.R_OK | os.W_OK):
        raise SystemExit(f'refused: {real} is not readable/writable by this user; the operator applies the serial '
                         'access fix (udev rule giving 1a86 to plugdev, or usermod -aG dialout), DESIGN.md 13')
    out.mkdir(parents=True, exist_ok=True)
    receipt = {'time': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'port': port, 'device': real,
               'rev': info['rev'], 'images': info['images'], 'ok': False}

    code, text = run([a.esptool, '--chip', 'esp32s3', '--port', port, '--baud', str(BAUD), 'read_mac'], 60)
    (out / 'esptool-read_mac.log').write_text(text)
    m = re.search(r'MAC:\s*([0-9a-f:]{17})', text, re.I)
    receipt['chip_mac'] = m.group(1).lower() if m else None
    if code != 0 or not m:
        receipt['error'] = 'read_mac failed (is the UART port the one beside BOOT/IO0?)'
        return finish(out, receipt)

    cmd, regions = flash_command(a.esptool, port, a.build)
    receipt['command'] = [Path(cmd[0]).name] + cmd[1:]
    code, text = run(cmd, 300)
    (out / 'esptool-write_flash.log').write_text(text)
    verified = [line.strip() for line in text.splitlines() if 'Hash of data verified' in line]
    receipt['verified'] = verified
    if code != 0 or len(verified) != regions:
        receipt['error'] = f'write_flash exit {code}, {len(verified)}/{regions} regions verified'
        return finish(out, receipt)

    time.sleep(a.boot_wait)
    import usbpad
    c = usbpad.UsbClient(port, capture_backend='capd', keepalive_s=0)
    r = c.request('switch.version')
    s = c.request('status')
    c.conn.close()
    receipt['version_reply'] = (r.get('result') or {}).get('reply') or r.get('error')
    receipt['status'] = s.get('result')
    want = f'OK version switch-pad {a.expect_version} '
    receipt['ok'] = isinstance(receipt['version_reply'], str) and receipt['version_reply'].startswith(want)
    if not receipt['ok']:
        receipt['error'] = f'the board did not answer {want.strip()!r}'
    return finish(out, receipt)


def finish(out, receipt):
    (out / 'flash-receipt.json').write_text(json.dumps(receipt, indent=1) + '\n')
    print(json.dumps({k: receipt.get(k) for k in ('ok', 'port', 'chip_mac', 'verified', 'version_reply', 'error')},
                     indent=1))
    return 0 if receipt['ok'] else 2


if __name__ == '__main__':
    sys.exit(main())
