"""own_install.py on a fake card tree: UMS directly, MTP through the real gio on a file:// URI."""
import contextlib
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import own_install as oi  # noqa: E402

NRO = 'atmosphere/contents/01006A800016E000/romfs/skyline/plugins/libssbu_switch_remote.nro'
NSP = 'atmosphere/contents/4200000000005352/exefs.nsp'
OTHER = 'atmosphere/contents/01006A800016E000/romfs/skyline/plugins/libtraining_rooms.nro'
CONFIG = 'ultimate/switch-remote/config.txt'


class Card(unittest.TestCase):
    transport = 'ums'

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='sr-card-'))
        self.card = self.tmp / 'card'
        (self.card / OTHER).parent.mkdir(parents=True)
        (self.card / OTHER).write_bytes(b'other plugin')
        self.root = str(self.card) if self.transport == 'ums' else self.card.as_uri()

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def job(self, name, files, order='primary ruling 2026-11-15 22:3xZ'):
        j = self.tmp / name
        for rel, data in files.items():
            (j / 'sd' / rel).parent.mkdir(parents=True, exist_ok=True)
            (j / 'sd' / rel).write_bytes(data)
        if order is not None:
            (j / 'order.txt').write_text(order)
        return j

    def run_tool(self, *args):
        with contextlib.redirect_stdout(io.StringIO()):
            return oi.main([*args, '--transport', self.transport, '--root', self.root])

    def test_add_then_replace_with_rollback_and_receipts(self):
        j1 = self.job('j1', {NRO: b'nro v1', NSP: b'nsp v1', CONFIG: b'token=secretsecretsecret\n'})
        self.assertEqual(self.run_tool('preflight', str(j1)), 0)
        self.assertEqual(self.run_tool('install', str(j1)), 0)
        r = json.loads((j1 / 'install-receipt.json').read_text())
        self.assertTrue(r['ok'])
        self.assertEqual({f['action'] for f in r['files']}, {'added'})
        self.assertEqual(r['other_files_changed'], [])
        self.assertNotIn('secretsecret', json.dumps(r) + (j1 / 'INSTALL.md').read_text())
        self.assertEqual((self.card / NRO).read_bytes(), b'nro v1')
        # Reinstall the NRO: the old one is kept on the card and read back.
        j2 = self.job('j2', {NRO: b'nro v2'})
        self.run_tool('preflight', str(j2))
        self.assertEqual(self.run_tool('install', str(j2)), 0)
        r2 = json.loads((j2 / 'install-receipt.json').read_text())
        row = r2['files'][0]
        self.assertEqual((row['action'], row['rollback_readback_ok']), ('replaced', True))
        self.assertEqual((self.card / row['rollback_copy']).read_bytes(), b'nro v1')
        self.assertEqual((j2 / 'before' / NRO).read_bytes(), b'nro v1')
        self.assertEqual((self.card / OTHER).read_bytes(), b'other plugin')

    def test_refuses_other_files(self):
        j = self.job('bad', {OTHER: b'evil'})
        with self.assertRaises(SystemExit) as cm:
            self.run_tool('preflight', str(j))
        self.assertIn('not switch-remote files', str(cm.exception))
        self.assertEqual((self.card / OTHER).read_bytes(), b'other plugin')

    def test_refuses_without_order_and_rerun_and_drift(self):
        with self.assertRaises(SystemExit):
            self.run_tool('preflight', str(self.job('noorder', {NRO: b'x'}, order=None)))
        j = self.job('j', {NRO: b'x'})
        self.run_tool('preflight', str(j))
        with self.assertRaises(SystemExit):
            self.run_tool('preflight', str(j))
        (self.card / OTHER).write_bytes(b'changed by someone else')
        with self.assertRaises(SystemExit) as cm:
            self.run_tool('install', str(j))
        self.assertIn('changed since preflight', str(cm.exception))
        self.assertFalse((self.card / NRO).exists())

    def test_install_needs_preflight(self):
        with self.assertRaises(SystemExit):
            self.run_tool('install', str(self.job('nopre', {NRO: b'x'})))

    def test_missing_card_is_reported(self):
        with self.assertRaises(SystemExit) as cm:
            oi.transport(self.transport, str(self.tmp / 'nope') if self.transport == 'ums'
                         else (self.tmp / 'nope').as_uri())
        self.assertIn('card not visible', str(cm.exception))


@unittest.skipUnless(shutil.which('gio'), 'gio not installed')
class CardOverMtpTransport(Card):
    transport = 'mtp'


if __name__ == '__main__':
    unittest.main()
