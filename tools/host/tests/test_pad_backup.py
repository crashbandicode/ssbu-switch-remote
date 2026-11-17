"""pad_backup.py with a fake esptool (read_flash writes an image holding a fake build at its offsets) and the
switch-pad pty simulator standing in for the board."""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HOST = Path(__file__).resolve().parents[1]
REPO = HOST.parent.parent
sys.path.insert(0, str(HOST))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import pad_backup  # noqa: E402
from test_flash_pad import fake_build  # noqa: E402

PADSIM = REPO / 'firmware' / 'out' / 'padsim'
FAKE = '''#!/usr/bin/python3
import json, sys
open(sys.argv[0] + '.calls', 'a').write(json.dumps(sys.argv[1:]) + '\\n')
build = %r
if 'read_mac' in sys.argv:
    print('MAC: 3c:84:27:aa:bb:cc')
elif 'read_flash' in sys.argv:
    i = sys.argv.index('read_flash')
    size, path = int(sys.argv[i + 2], 16), sys.argv[i + 3]
    data = bytearray(b'\\xff' * size)
    for off, name in ((0x0, 'bootloader.bin'), (0x8000, 'partition-table.bin'), (0x10000, 'switch_pad.bin')):
        img = open(build + '/' + name, 'rb').read()
        data[off:off + len(img)] = img
    open(path, 'wb').write(bytes(data))
elif 'write_flash' in sys.argv:
    print('Hash of data verified.')
'''


@unittest.skipUnless(PADSIM.exists(), 'make -C firmware out/padsim')
class Backup(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='sr-backup-'))
        self.sim = subprocess.Popen([str(PADSIM)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        self.tty = self.sim.stdout.readline().strip()
        self.build = self.tmp / 'build'
        fake_build(self.build)
        self.esptool = self.tmp / 'esptool.py'
        self.esptool.write_text(FAKE % str(self.build))
        self.esptool.chmod(0o755)

    def tearDown(self):
        self.sim.stdin.close()
        self.sim.wait(5)
        self.sim.stdout.close()
        shutil.rmtree(self.tmp)

    def calls(self):
        return [json.loads(x) for x in Path(str(self.esptool) + '.calls').read_text().splitlines()]

    def backup(self, *extra):
        return pad_backup.main(['backup', '--out', str(self.tmp / 'b'), '--port', self.tty,
                                '--esptool', str(self.esptool), *extra])

    def test_backup_compares_and_restore_verifies(self):
        self.assertEqual(self.backup('--compare-build', str(self.build)), 0)
        r = json.loads((self.tmp / 'b' / 'backup-receipt.json').read_text())
        self.assertTrue(r['ok'])
        self.assertEqual((r['size'], r['chip_mac']), ('0x110000', '3c:84:27:aa:bb:cc'))
        self.assertTrue(r['version_reply'].startswith('OK version switch-pad '))
        self.assertTrue(all(v['match'] for v in r['images'].values()))
        self.assertEqual((self.tmp / 'b' / 'backup.bin').stat().st_size, 0x110000)
        self.assertEqual(self.calls()[1][-4:], ['read_flash', '0x0', '0x110000', str(self.tmp / 'b' / 'backup.bin')])
        code = pad_backup.main(['restore', '--backup', str(self.tmp / 'b'), '--port', self.tty,
                                '--esptool', str(self.esptool), '--boot-wait', '0'])
        self.assertEqual(code, 0)
        self.assertEqual(self.calls()[-1][-3:], ['write_flash', '0x0', str(self.tmp / 'b' / 'backup.bin')])
        rr = json.loads(next((self.tmp / 'b').glob('restore-receipt-*.json')).read_text())
        self.assertTrue(rr['ok'] and rr['version_reply'] == r['version_reply'])

    def test_wrong_build_and_tampered_backup_are_refused(self):
        other = self.tmp / 'other'
        fake_build(other, images=('bootloader.bin', 'partition-table.bin', 'switch_pad.bin'))
        (other / 'switch_pad.bin').write_bytes(b'a different app')
        self.assertEqual(self.backup('--compare-build', str(other)), 2)
        r = json.loads((self.tmp / 'b' / 'backup-receipt.json').read_text())
        self.assertFalse(r['images']['switch_pad.bin']['match'])
        with self.assertRaises(SystemExit) as cm:
            pad_backup.main(['restore', '--backup', str(self.tmp / 'b'), '--port', self.tty])
        self.assertIn('did not complete', str(cm.exception))
        shutil.rmtree(self.tmp / 'b')
        self.assertEqual(self.backup(), 0)
        (self.tmp / 'b' / 'backup.bin').write_bytes(b'tampered')
        with self.assertRaises(SystemExit) as cm:
            pad_backup.main(['restore', '--backup', str(self.tmp / 'b'), '--port', self.tty])
        self.assertIn('sha256', str(cm.exception))


if __name__ == '__main__':
    unittest.main()
