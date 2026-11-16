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
AP_NSP = 'atmosphere/contents/4200000000004150/exefs.nsp'
AP_TOOLBOX = 'atmosphere/contents/4200000000004150/toolbox.json'
AP_FLAG = 'atmosphere/contents/4200000000004150/flags/boot2.flag'
AP_INI = 'config/sys-autopilot/config.ini'


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
        j.mkdir()
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

    def test_option_d_install_then_a_later_boot2_session(self):
        # One session: add sys-autopilot without its flag, remove our sysmodule's flag, update NRO + config.
        flag = 'atmosphere/contents/4200000000005352/flags/boot2.flag'
        (self.card / flag).parent.mkdir(parents=True)
        (self.card / flag).write_bytes(b'')
        j = self.job('d1', {AP_NSP: b'autopilot', AP_TOOLBOX: b'{}', AP_INI: b'token = secretsecretsecret\n',
                            NRO: b'nro 0.2.0', CONFIG: b'token=secretsecretsecret\nscene_host=172.30.254.66\n'})
        (j / 'remove.txt').write_text(flag + '\n')
        self.run_tool('preflight', str(j))
        self.assertEqual(self.run_tool('install', str(j)), 0)
        r = json.loads((j / 'install-receipt.json').read_text())
        self.assertTrue(r['ok'])
        self.assertEqual({f['dest']: f['action'] for f in r['files']}[flag], 'removed')
        self.assertFalse((self.card / AP_FLAG).exists())
        self.assertNotIn('secretsecret', json.dumps(r) + (j / 'INSTALL.md').read_text())
        # Later, once sys-autopilot served /status: its boot2.flag in a receipted session of its own.
        j2 = self.job('d2', {AP_FLAG: b''})
        self.run_tool('preflight', str(j2))
        self.assertEqual(self.run_tool('install', str(j2)), 0)
        self.assertTrue((self.card / AP_FLAG).exists())

    def test_remove_list_deletes_only_allowlisted_files_with_receipts(self):
        (self.card / AP_FLAG).parent.mkdir(parents=True)
        (self.card / AP_FLAG).write_bytes(b'')
        (self.card / NRO).parent.mkdir(parents=True, exist_ok=True)
        (self.card / NRO).write_bytes(b'nro old')
        j = self.job('rm', {})
        (j / 'remove.txt').write_text(f'# after an ovlSysmodules start\n{AP_FLAG}\n{NRO}\n{AP_TOOLBOX}\n')
        self.run_tool('preflight', str(j))
        self.assertEqual(self.run_tool('install', str(j)), 0)
        r = json.loads((j / 'install-receipt.json').read_text())
        self.assertTrue(r['ok'])
        rows = {f['dest']: f for f in r['files']}
        self.assertEqual(rows[AP_FLAG]['action'], 'removed')
        self.assertNotIn('rollback_copy', rows[AP_FLAG])
        self.assertEqual(rows[AP_TOOLBOX]['action'], 'already_absent')
        self.assertEqual((self.card / rows[NRO]['rollback_copy']).read_bytes(), b'nro old')
        self.assertEqual((j / 'before' / NRO).read_bytes(), b'nro old')
        self.assertFalse((self.card / AP_FLAG).exists())
        self.assertFalse((self.card / NRO).exists())
        self.assertEqual((self.card / OTHER).read_bytes(), b'other plugin')

    def test_remove_refuses_other_files_and_conflicts(self):
        j = self.job('rmbad', {})
        (j / 'remove.txt').write_text(OTHER + '\n')
        with self.assertRaises(SystemExit) as cm:
            self.run_tool('preflight', str(j))
        self.assertIn('not removable', str(cm.exception))
        j2 = self.job('rmboth', {NRO: b'x'})
        (j2 / 'remove.txt').write_text(NRO + '\n')
        with self.assertRaises(SystemExit) as cm:
            self.run_tool('preflight', str(j2))
        self.assertIn('both written and removed', str(cm.exception))
        self.assertEqual((self.card / OTHER).read_bytes(), b'other plugin')

    def test_missing_card_is_reported(self):
        with self.assertRaises(SystemExit) as cm:
            oi.transport(self.transport, str(self.tmp / 'nope') if self.transport == 'ums'
                         else (self.tmp / 'nope').as_uri())
        self.assertIn('card not visible', str(cm.exception))


@unittest.skipUnless(shutil.which('gio'), 'gio not installed')
class CardOverMtpTransport(Card):
    transport = 'mtp'


class CardOverDbiFtp(Card):
    """The same suite over DBI's FTP route (operator note 2026-11-16 ~04:16Z), against a fake server with DBI's
    quirks (NLST ignores its argument; rename onto an existing file refused)."""
    transport = 'ftp'

    def setUp(self):
        super().setUp()
        from fake_ftp import FakeFtp
        self.ftp = FakeFtp(self.card)
        self.root = f'ftp://127.0.0.1:{self.ftp.port}'
        self._creds = oi.ftp_credentials
        oi.ftp_credentials = lambda bookmarks=None: ('switch', 'secret-pass')

    def tearDown(self):
        oi.ftp_credentials = self._creds
        self.ftp.stop()
        super().tearDown()

    def test_missing_card_is_reported(self):
        with self.assertRaises(SystemExit) as cm:
            oi.transport('ftp', 'ftp://127.0.0.1:1')
        self.assertIn('card not visible', str(cm.exception))

    def test_every_operation_types_binary(self):
        self.test_add_then_replace_with_rollback_and_receipts()
        self.assertGreater(self.ftp.commands.count('TYPE'), 5)
        # every connection sets TYPE I after login (ftplib adds its own TYPE per transfer)
        self.assertGreaterEqual(self.ftp.commands.count('TYPE'), self.ftp.commands.count('USER'))

    def test_fetch_is_read_only(self):
        (self.card / 'ultimate/switch-remote').mkdir(parents=True)
        (self.card / 'ultimate/switch-remote/sysmodule.log').write_bytes(b'boot breadcrumb\n')
        out = self.tmp / 'fetched'
        with contextlib.redirect_stdout(io.StringIO()):
            oi.main(['fetch', str(out), '--transport', 'ftp', '--root', self.root, '--path', 'ultimate/switch-remote',
                     '--path', 'atmosphere/crash_reports'])
        m = json.loads((out / 'fetch-manifest.json').read_text())
        self.assertEqual(list(m['files']), ['ultimate/switch-remote/sysmodule.log'])
        self.assertEqual(m['absent'], ['atmosphere/crash_reports'])
        self.assertEqual((out / 'ultimate/switch-remote/sysmodule.log').read_bytes(), b'boot breadcrumb\n')
        writes = {'STOR', 'DELE', 'RNFR', 'RNTO', 'MKD'}
        self.assertFalse(writes & set(self.ftp.commands))

    def test_credentials_come_from_the_bookmark_and_are_never_printed(self):
        oi.ftp_credentials = self._creds
        bm = self.tmp / 'bookmarks'
        bm.write_text(f'file:///home Home\nftp://switch:secret-pass@127.0.0.1:{self.ftp.port}/ Switch SD (FTP)\n')
        self.assertEqual(oi.ftp_credentials(bm), ('switch', 'secret-pass'))
        j = self.job('j', {NRO: b'x'})
        oi.ftp_credentials = lambda bookmarks=None: self._creds(bm)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            oi.main(['preflight', str(j), '--transport', 'ftp', '--root', self.root])
            oi.main(['install', str(j), '--transport', 'ftp', '--root', self.root])
        text = buf.getvalue() + ''.join(p.read_text() for p in j.glob('*.json')) + (j / 'INSTALL.md').read_text()
        self.assertNotIn('secret-pass', text)


if __name__ == '__main__':
    unittest.main()
