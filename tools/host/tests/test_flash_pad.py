"""flash_pad.py with a fake esptool (records its arguments, prints esptool's verify lines) and the switch-pad pty
simulator standing in for the board after its reset."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HOST = Path(__file__).resolve().parents[1]
REPO = HOST.parent.parent
sys.path.insert(0, str(HOST))
import flash_pad  # noqa: E402

PADSIM = REPO / 'firmware' / 'out' / 'padsim'
FAKE = '''#!/usr/bin/python3
import json, sys
open(sys.argv[0] + '.calls', 'a').write(json.dumps(sys.argv[1:]) + '\\n')
if 'read_mac' in sys.argv:
    print('Chip is ESP32-S3 (QFN56) (revision v0.2)'); print('MAC: 3c:84:27:aa:bb:cc')
elif 'write_flash' in sys.argv:
    for _ in range(%d):
        print('Wrote 1 bytes at 0x0 in 0.1 seconds...'); print('Hash of data verified.')
'''


def fake_build(d, images=('bootloader.bin', 'partition-table.bin', 'switch_pad.bin')):
    d.mkdir(parents=True)
    imgs = {}
    for n in images:
        (d / n).write_bytes(n.encode() * 10)
        imgs[n] = {'bytes': len(n) * 10, 'sha256': flash_pad.sha(d / n)}
    (d / 'build.json').write_text(json.dumps({'rev': 'abc', 'images': imgs}))
    (d / 'flasher_args.json').write_text(json.dumps({
        'write_flash_args': ['--flash_mode', 'dio', '--flash_size', '4MB', '--flash_freq', '80m'],
        'flash_files': {'0x0': 'bootloader/bootloader.bin', '0x10000': 'switch_pad.bin',
                        '0x8000': 'partition_table/partition-table.bin'},
        'extra_esptool_args': {'chip': 'esp32s3'}}))


@unittest.skipUnless(PADSIM.exists(), 'make -C firmware out/padsim')
class Flash(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='sr-flash-'))
        self.sim = subprocess.Popen([str(PADSIM)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        self.tty = self.sim.stdout.readline().strip()
        self.build = self.tmp / 'build'
        fake_build(self.build)

    def tearDown(self):
        self.sim.stdin.close()
        self.sim.wait(5)
        self.sim.stdout.close()
        shutil.rmtree(self.tmp)

    def esptool(self, verified=3):
        p = self.tmp / 'esptool.py'
        p.write_text(FAKE % verified)
        p.chmod(0o755)
        return p

    def flash(self, *extra, verified=3):
        e = self.esptool(verified)
        code = flash_pad.main(['--build', str(self.build), '--out', str(self.tmp / 'r'), '--port', self.tty,
                               '--esptool', str(e), '--boot-wait', '0', *extra])
        calls = [json.loads(x) for x in Path(str(e) + '.calls').read_text().splitlines()]
        return code, json.loads((self.tmp / 'r' / 'flash-receipt.json').read_text()), calls

    def test_flash_receipt(self):
        code, r, calls = self.flash()
        self.assertEqual(code, 0, r)
        self.assertTrue(r['ok'])
        self.assertEqual(r['chip_mac'], '3c:84:27:aa:bb:cc')
        self.assertEqual(len(r['verified']), 3)
        self.assertTrue(r['version_reply'].startswith('OK version switch-pad 0.1.0'))
        wf = calls[1]
        self.assertIn('write_flash', wf)
        tail = wf[wf.index('write_flash') + 1:]
        self.assertEqual(tail[:6], ['--flash_mode', 'dio', '--flash_size', '4MB', '--flash_freq', '80m'])
        self.assertEqual(tail[6::2], ['0x0', '0x8000', '0x10000'])  # ascending offsets
        self.assertEqual([Path(x).name for x in tail[7::2]], ['bootloader.bin', 'partition-table.bin', 'switch_pad.bin'])

    def test_unverified_region_fails(self):
        code, r, _ = self.flash(verified=2)
        self.assertEqual(code, 2)
        self.assertIn('2/3 regions verified', r['error'])

    def test_wrong_version_fails(self):
        code, r, _ = self.flash('--expect-version', '9.9.9')
        self.assertEqual(code, 2)
        self.assertFalse(r['ok'])

    def test_refusals(self):
        (self.build / 'switch_pad.bin').write_bytes(b'tampered')
        with self.assertRaises(SystemExit) as cm:
            flash_pad.main(['--build', str(self.build), '--out', str(self.tmp / 'r'), '--port', self.tty])
        self.assertIn('sha256', str(cm.exception))
        orig = flash_pad.CH343_GLOB
        flash_pad.CH343_GLOB = str(self.tmp / 'none*')
        try:
            with self.assertRaises(SystemExit) as cm:
                flash_pad.resolve_port('auto')
            self.assertIn('exactly one CH343', str(cm.exception))
        finally:
            flash_pad.CH343_GLOB = orig

    def test_real_build_output_shapes_the_command(self):
        real = Path('/home/user/ssbu-switch-remote-work/pad/433f357')
        if not real.exists():
            self.skipTest('no local build')
        flash_pad.check_build(real)
        cmd, n = flash_pad.flash_command('esptool.py', '/dev/x', real)
        self.assertEqual(n, 3)
        self.assertEqual(cmd[1:3], ['--chip', 'esp32s3'])
        self.assertTrue(all(os.path.exists(c) for c in cmd if c.endswith('.bin')))


if __name__ == '__main__':
    unittest.main()
