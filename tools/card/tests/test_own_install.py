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

    def test_ns_autoverify_probe_stage1_switches_and_rollback(self):
        nsav = 'atmosphere/contents/4200000000004156'
        observe = 'config/ns-autoverify/observe-only'
        disabled = 'config/ns-autoverify/disabled'
        probe = 'switch/nsav-probe/nsav-probe.nro'
        # P0: the probe NRO alone.
        j0 = self.job('nsav-p0', {probe: b'NRO0 probe'})
        self.run_tool('preflight', str(j0))
        self.assertEqual(self.run_tool('install', str(j0)), 0)
        self.assertEqual((self.card / probe).read_bytes(), b'NRO0 probe')
        # Stage 1: exefs + toolbox + observe-only, no boot2.flag.
        j = self.job('nsav-s1', {f'{nsav}/exefs.nsp': b'PFS0 nsav', f'{nsav}/toolbox.json': b'{}', observe: b''})
        self.run_tool('preflight', str(j))
        self.assertEqual(self.run_tool('install', str(j)), 0)
        self.assertTrue(json.loads((j / 'install-receipt.json').read_text())['ok'])
        self.assertFalse((self.card / nsav / 'flags').exists())
        # ovlSysmodules' A created the flag: a removal job deletes it.
        (self.card / nsav / 'flags').mkdir()
        (self.card / nsav / 'flags' / 'boot2.flag').write_bytes(b'')
        j2 = self.job('nsav-noflag', {})
        (j2 / 'remove.txt').write_text(f'{nsav}/flags/boot2.flag\n')
        self.run_tool('preflight', str(j2))
        self.assertEqual(self.run_tool('install', str(j2)), 0)
        self.assertFalse((self.card / nsav / 'flags' / 'boot2.flag').exists())
        # Active: leave observe-only. Soft rollback: the kill switch.
        j3 = self.job('nsav-active', {})
        (j3 / 'remove.txt').write_text(observe + '\n')
        self.run_tool('preflight', str(j3))
        self.assertEqual(self.run_tool('install', str(j3)), 0)
        self.assertFalse((self.card / observe).exists())
        j4 = self.job('nsav-off', {disabled: b''})
        self.run_tool('preflight', str(j4))
        self.assertEqual(self.run_tool('install', str(j4)), 0)
        self.assertTrue((self.card / disabled).exists())
        # Full rollback: module files and the probe.
        j5 = self.job('nsav-rm', {})
        (j5 / 'remove.txt').write_text(f'{nsav}/exefs.nsp\n{nsav}/toolbox.json\n{nsav}/flags/boot2.flag\n{probe}\n')
        self.run_tool('preflight', str(j5))
        self.assertEqual(self.run_tool('install', str(j5)), 0)
        self.assertFalse((self.card / nsav / 'exefs.nsp').exists())
        self.assertFalse((self.card / probe).exists())
        # Anything else under its folder is still refused.
        j6 = self.job('nsav-bad', {f'{nsav}/main': b'x'})
        with self.assertRaises(SystemExit):
            self.run_tool('preflight', str(j6))

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

    def test_another_projects_plugin_only_with_authorization_and_go(self):
        deluxe = 'atmosphere/contents/01006A800016E000/romfs/skyline/plugins/libssbu_online_deluxe_rewrite.nro'
        (self.card / deluxe).write_bytes(b'deluxe 0.10.7')
        new = b'deluxe 0.10.8'
        j = self.job('g1', {deluxe: new})
        with self.assertRaises(SystemExit) as cm:
            self.run_tool('preflight', str(j))
        self.assertIn('not switch-remote files', str(cm.exception))
        auth = [{'dest': deluxe, 'sha256': oi.sha(new), 'bytes': len(new), 'authorization': 'operator OK (test)',
                 'source': 'test'}]
        (j / 'authorized.json').write_text(json.dumps(auth))
        self.run_tool('preflight', str(j))
        with self.assertRaises(SystemExit) as cm:
            self.run_tool('install', str(j))
        self.assertIn('go.txt', str(cm.exception))
        self.assertEqual((self.card / deluxe).read_bytes(), b'deluxe 0.10.7')  # nothing written without go
        (j / 'go.txt').write_text('primary OK (test)')
        self.assertEqual(self.run_tool('install', str(j)), 0)
        r = json.loads((j / 'install-receipt.json').read_text())
        row = r['files'][0]
        self.assertEqual((row['action'], row['readback_ok'], r['other_files_changed']), ('replaced', True, []))
        self.assertEqual((self.card / row['rollback_copy']).read_bytes(), b'deluxe 0.10.7')
        self.assertEqual((j / 'before' / deluxe).read_bytes(), b'deluxe 0.10.7')
        self.assertEqual((self.card / OTHER).read_bytes(), b'other plugin')

    def test_tr_plugin_install_unchanged_with_sysmodule_allowlist(self):
        # Regression (ns-autoverify allowlist, 2026-11-17): a TR plugin install behaves exactly as before, even with
        # the ns-autoverify folder and probe present on the card, and authorized.json still only reaches plugin NROs.
        tr = OTHER
        nsav_nsp = 'atmosphere/contents/4200000000004156/exefs.nsp'
        probe = 'switch/nsav-probe/nsav-probe.nro'
        for rel, data in ((nsav_nsp, b'PFS0 nsav'), (probe, b'NRO0 probe')):
            (self.card / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.card / rel).write_bytes(data)
        new = b'training_rooms rc.16'
        j = self.job('tr', {tr: new})
        with self.assertRaises(SystemExit) as cm:
            self.run_tool('preflight', str(j))
        self.assertIn('not switch-remote files', str(cm.exception))  # unchanged: TR needs an authorization
        (j / 'authorized.json').write_text(json.dumps([{'dest': tr, 'sha256': oi.sha(new), 'bytes': len(new),
                                                        'authorization': 'operator OK (test)', 'source': 'test'}]))
        self.run_tool('preflight', str(j))
        pre = json.loads((j / 'preflight-receipt.json').read_text())
        self.assertEqual(list(pre['files']), [tr])
        self.assertEqual(pre['files'][tr]['card_sha256_before'], oi.sha(b'other plugin'))
        self.assertIn(nsav_nsp, pre['watched_before'])  # the module folder is watched, not written
        with self.assertRaises(SystemExit) as cm:
            self.run_tool('install', str(j))
        self.assertIn('go.txt', str(cm.exception))  # unchanged: the go gate
        self.assertEqual((self.card / tr).read_bytes(), b'other plugin')
        (j / 'go.txt').write_text('primary OK (test)')
        self.assertEqual(self.run_tool('install', str(j)), 0)
        r = json.loads((j / 'install-receipt.json').read_text())
        self.assertTrue(r['ok'])
        self.assertEqual(len(r['files']), 1)
        row = r['files'][0]
        self.assertEqual((row['dest'], row['action'], row['readback_ok'], row['rollback_readback_ok']),
                         (tr, 'replaced', True, True))
        self.assertEqual((self.card / row['rollback_copy']).read_bytes(), b'other plugin')
        self.assertEqual((j / 'before' / tr).read_bytes(), b'other plugin')
        self.assertEqual((self.card / tr).read_bytes(), new)
        self.assertEqual(r['other_files_changed'], [])
        self.assertEqual((self.card / nsav_nsp).read_bytes(), b'PFS0 nsav')
        self.assertEqual((self.card / probe).read_bytes(), b'NRO0 probe')
        # authorized.json cannot be used to reach the sysmodule folder (it stays plugin-NRO only).
        j2 = self.job('tr-bad', {nsav_nsp: b'x'})
        (j2 / 'authorized.json').write_text(json.dumps([{'dest': nsav_nsp, 'sha256': oi.sha(b'x'), 'bytes': 1,
                                                         'authorization': 'x', 'source': 'x'}]))
        with self.assertRaises(SystemExit) as cm:
            self.run_tool('preflight', str(j2))
        self.assertIn('not a plugin NRO', str(cm.exception))

    def test_authorization_is_exact(self):
        deluxe = 'atmosphere/contents/01006A800016E000/romfs/skyline/plugins/libssbu_online_deluxe_rewrite.nro'
        j = self.job('g2', {deluxe: b'tampered'})
        (j / 'authorized.json').write_text(json.dumps([{'dest': deluxe, 'sha256': oi.sha(b'expected'), 'bytes': 8,
                                                        'authorization': 'x', 'source': 'x'}]))
        with self.assertRaises(SystemExit) as cm:
            self.run_tool('preflight', str(j))
        self.assertIn('does not match its authorization', str(cm.exception))
        j3 = self.job('g3', {'atmosphere/config/system_settings.ini': b'x'})
        (j3 / 'authorized.json').write_text(json.dumps([{'dest': 'atmosphere/config/system_settings.ini',
                                                         'sha256': oi.sha(b'x'), 'bytes': 1, 'authorization': 'x'}]))
        with self.assertRaises(SystemExit) as cm:
            self.run_tool('preflight', str(j3))
        self.assertIn('not a plugin NRO', str(cm.exception))

    def test_bisect_move_out_and_back(self):
        P = 'atmosphere/contents/01006A800016E000/romfs/skyline/plugins/'
        D = 'ultimate/switch-remote/disabled/'
        (self.card / NRO).write_bytes(b'our nro r1')
        j = self.job('b1', {})
        (j / 'moves.txt').write_text(f'# bisect step 1\n{NRO} -> {D}libssbu_switch_remote.nro\n')
        self.run_tool('preflight', str(j))
        self.assertEqual(self.run_tool('install', str(j)), 0)
        r = json.loads((j / 'install-receipt.json').read_text())
        row = r['files'][0]
        self.assertEqual((row['action'], row['readback_ok'], row['src_absent'], r['other_files_changed']),
                         ('moved', True, True, []))
        self.assertFalse((self.card / NRO).exists())
        self.assertEqual((self.card / D / 'libssbu_switch_remote.nro').read_bytes(), b'our nro r1')
        self.assertEqual((j / 'before' / NRO).read_bytes(), b'our nro r1')
        self.assertEqual((self.card / OTHER).read_bytes(), b'other plugin')
        back = self.job('b1-rollback', {})
        (back / 'moves.txt').write_text(f'{D}libssbu_switch_remote.nro -> {NRO}\n')
        self.run_tool('preflight', str(back))
        self.assertEqual(self.run_tool('install', str(back)), 0)
        self.assertEqual((self.card / NRO).read_bytes(), b'our nro r1')

    def test_bisect_swap_moves_then_writes_with_authorization(self):
        P = 'atmosphere/contents/01006A800016E000/romfs/skyline/plugins/'
        tr = P + 'libtraining_rooms.nro'
        (self.card / tr).write_bytes(b'tr rc15')
        rc14 = b'tr rc14'
        j = self.job('b2', {tr: rc14})
        (j / 'moves.txt').write_text(f'{tr} -> ultimate/switch-remote/disabled/libtraining_rooms.nro\n')
        (j / 'authorized.json').write_text(json.dumps([{'dest': tr, 'sha256': oi.sha(rc14), 'bytes': len(rc14),
                                                        'authorization': 'bisect (test)', 'source': 'test'}]))
        (j / 'go.txt').write_text('ok (test)')
        self.run_tool('preflight', str(j))
        self.assertEqual(self.run_tool('install', str(j)), 0)
        r = json.loads((j / 'install-receipt.json').read_text())
        self.assertEqual([x['action'] for x in r['files']], ['moved', 'added'])
        self.assertTrue(r['ok'])
        self.assertEqual((self.card / tr).read_bytes(), b'tr rc14')
        self.assertEqual((self.card / 'ultimate/switch-remote/disabled/libtraining_rooms.nro').read_bytes(), b'tr rc15')

    def test_moves_are_restricted(self):
        for line in ('atmosphere/config/system_settings.ini -> ultimate/switch-remote/disabled/system_settings.ini',
                     f'{NRO} -> ultimate/switch-remote/disabled/other_name.nro',
                     f'{NRO} -> ultimate/elsewhere/libssbu_switch_remote.nro'):
            j = self.job(f'bad{abs(hash(line)) % 1000}', {})
            (j / 'moves.txt').write_text(line + '\n')
            with self.assertRaises(SystemExit) as cm:
                self.run_tool('preflight', str(j))
            self.assertIn('move not allowed', str(cm.exception))

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
        sr = self.card / 'ultimate/switch-remote'
        (sr / 'backups').mkdir(parents=True)
        (sr / 'sysmodule.log').write_bytes(b'boot breadcrumb\n')
        (sr / 'config.txt').write_bytes(b'token=secretsecretsecret\n')
        (sr / 'backups/pre-1.exefs.nsp').write_bytes(b'old binary')
        (self.card / 'ultimate/training_rooms').mkdir(parents=True)
        (self.card / 'ultimate/training_rooms/events.log').write_bytes(b'x' * 100)
        out = self.tmp / 'fetched'
        orig = oi.FETCH_MAX_BYTES
        oi.FETCH_MAX_BYTES = 50
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                oi.main(['fetch', str(out), '--transport', 'ftp', '--root', self.root, '--path', 'ultimate/switch-remote',
                         '--path', 'atmosphere/crash_reports', '--list', 'ultimate', '--list', 'atmosphere/contents/01006A800016E000'])
        finally:
            oi.FETCH_MAX_BYTES = orig
        m = json.loads((out / 'fetch-manifest.json').read_text())
        self.assertEqual(list(m['files']), ['ultimate/switch-remote/sysmodule.log'])
        self.assertEqual(m['skipped'], {'ultimate/switch-remote/backups/pre-1.exefs.nsp': 'excluded',
                                        'ultimate/switch-remote/config.txt': 'excluded'})
        self.assertEqual(m['absent'], ['atmosphere/crash_reports'])
        self.assertIn('ultimate/training_rooms/events.log', m['listings']['ultimate'])
        self.assertIn(OTHER, m['listings']['atmosphere/contents/01006A800016E000'])
        self.assertEqual((out / 'ultimate/switch-remote/sysmodule.log').read_bytes(), b'boot breadcrumb\n')
        self.assertFalse((out / 'ultimate/switch-remote/config.txt').exists())
        self.assertEqual(self.ftp.commands.count('RETR'), 2)  # one file, read twice
        writes = {'STOR', 'DELE', 'RNFR', 'RNTO', 'MKD'}
        self.assertFalse(writes & set(self.ftp.commands))
        with self.assertRaises(SystemExit):
            oi.fetch(out, oi.transport('ftp', self.root), ['ultimate'])  # an existing manifest is never overwritten

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
