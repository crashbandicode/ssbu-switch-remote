#!/usr/bin/python3
"""Back up and restore the switch-pad board's flash over its UART port, with receipts (docs/GC-MODE.md, flash plan).

  pad_backup.py backup  --out DIR [--compare-build BUILD_DIR] [--port auto|/dev/...] [--size 0x110000]
  pad_backup.py restore --backup DIR [--port ...]

backup:
  1. `VERSION` over the UART (what is running now; the board answers before esptool resets it).
  2. esptool read_mac, then read_flash 0x0..size (default 0x110000: bootloader, partition table, NVS/phy and the
     1 MB factory app) into DIR/backup.bin; its sha256 goes into DIR/backup-receipt.json.
  3. With --compare-build, each image of that build (flasher_args.json offsets) must equal the bytes read back at
     its offset: that proves which firmware the backup holds (e.g. f9208f2 = switch-pad 0.1.2).
restore (the rollback):
  1. backup.bin's sha256 must match its receipt.
  2. esptool write_flash 0x0 backup.bin (esptool verifies the region: "Hash of data verified").
  3. After the reset the board must answer `VERSION` exactly as it did at backup time.
Reading or writing resets the board: the console sees the pad unplugged meanwhile. Run only in a console-free window.
"""
import argparse
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import flash_pad  # noqa: E402

DEFAULT_SIZE = 0x110000


def version(port):
    import usbpad
    c = usbpad.UsbClient(port, capture_backend='capd', keepalive_s=0)
    try:
        r = c.request('switch.version')
        return (r.get('result') or {}).get('reply') or f'error: {r.get("error")}'
    finally:
        c.conn.close()


def compare(data, build):
    """Each image of `build` against the bytes read back at its flasher_args.json offset."""
    fa = json.loads((Path(build) / 'flasher_args.json').read_text())
    out = {}
    for offset, rel in fa['flash_files'].items():
        img = (Path(build) / Path(rel).name).read_bytes()
        at = int(offset, 16)
        out[Path(rel).name] = {'offset': offset, 'bytes': len(img),
                               'match': at + len(img) <= len(data) and data[at:at + len(img)] == img}
    return out


def backup(a):
    out = Path(a.out)
    if (out / 'backup-receipt.json').exists():
        raise SystemExit('refused: backup-receipt.json exists; use a new --out')
    port = flash_pad.resolve_port(a.port)
    out.mkdir(parents=True, exist_ok=True)
    size = int(a.size, 0)
    receipt = {'time': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'port': port,
               'device': os.path.realpath(port), 'size': hex(size), 'ok': False}
    receipt['version_reply'] = version(port)
    code, text = flash_pad.run([a.esptool, '--chip', 'esp32s3', '--port', port, '--baud', str(flash_pad.BAUD),
                                'read_mac'], 60)
    (out / 'esptool-read_mac.log').write_text(text)
    m = re.search(r'MAC:\s*([0-9a-f:]{17})', text, re.I)
    receipt['chip_mac'] = m.group(1).lower() if m else None
    if code != 0 or not m:
        receipt['error'] = 'read_mac failed'
        return finish(out / 'backup-receipt.json', receipt)
    image = out / 'backup.bin'
    code, text = flash_pad.run([a.esptool, '--chip', 'esp32s3', '--port', port, '--baud', str(flash_pad.BAUD),
                                '--before', 'default_reset', '--after', 'hard_reset', 'read_flash', '0x0', hex(size),
                                str(image)], 600)
    (out / 'esptool-read_flash.log').write_text(text)
    if code != 0 or not image.exists() or image.stat().st_size != size:
        receipt['error'] = f'read_flash exit {code}, {image.stat().st_size if image.exists() else 0}/{size} bytes'
        return finish(out / 'backup-receipt.json', receipt)
    data = image.read_bytes()
    receipt['sha256'] = hashlib.sha256(data).hexdigest()
    if a.compare_build:
        receipt['compare_build'] = str(a.compare_build)
        receipt['images'] = compare(data, a.compare_build)
        if not all(v['match'] for v in receipt['images'].values()):
            receipt['error'] = 'the flash does not hold --compare-build'
            return finish(out / 'backup-receipt.json', receipt)
    receipt['ok'] = True
    return finish(out / 'backup-receipt.json', receipt)


def restore(a):
    src = Path(a.backup)
    info = json.loads((src / 'backup-receipt.json').read_text())
    if not info.get('ok'):
        raise SystemExit('refused: that backup did not complete (ok=false)')
    image = src / 'backup.bin'
    if flash_pad.sha(image) != info['sha256']:
        raise SystemExit('refused: backup.bin sha256 differs from its receipt')
    port = flash_pad.resolve_port(a.port)
    stamp = time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())
    receipt = {'time': stamp, 'port': port, 'backup': str(src), 'sha256': info['sha256'], 'ok': False}
    code, text = flash_pad.run([a.esptool, '--chip', 'esp32s3', '--port', port, '--baud', str(flash_pad.BAUD),
                                '--before', 'default_reset', '--after', 'hard_reset', 'write_flash', '0x0',
                                str(image)], 600)
    (src / f'esptool-restore-{stamp}.log').write_text(text)
    receipt['verified'] = [x.strip() for x in text.splitlines() if 'Hash of data verified' in x]
    if code != 0 or len(receipt['verified']) != 1:
        receipt['error'] = f'write_flash exit {code}, {len(receipt["verified"])}/1 regions verified'
        return finish(src / f'restore-receipt-{stamp}.json', receipt)
    time.sleep(a.boot_wait)
    receipt['version_reply'] = version(port)
    receipt['ok'] = receipt['version_reply'] == info['version_reply']
    if not receipt['ok']:
        receipt['error'] = f'VERSION {receipt["version_reply"]!r} != backup time {info["version_reply"]!r}'
    return finish(src / f'restore-receipt-{stamp}.json', receipt)


def finish(path, receipt):
    path.write_text(json.dumps(receipt, indent=1) + '\n')
    print(json.dumps({k: receipt.get(k) for k in ('ok', 'port', 'chip_mac', 'sha256', 'version_reply', 'images',
                                                  'verified', 'error') if k in receipt}, indent=1))
    return 0 if receipt['ok'] else 2


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    b = sub.add_parser('backup')
    b.add_argument('--out', required=True)
    b.add_argument('--compare-build')
    b.add_argument('--size', default=hex(DEFAULT_SIZE))
    r = sub.add_parser('restore')
    r.add_argument('--backup', required=True)
    r.add_argument('--boot-wait', type=float, default=2.0)
    for p in (b, r):
        p.add_argument('--port', default='auto')
        p.add_argument('--esptool', default=flash_pad.DEFAULT_ESPTOOL)
    a = ap.parse_args(argv)
    return backup(a) if a.cmd == 'backup' else restore(a)


if __name__ == '__main__':
    sys.exit(main())
