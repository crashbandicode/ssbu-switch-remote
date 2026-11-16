"""scened.py: the host scene receiver and the fail-closed gate, over real loopback TCP (the NRO's side of the
channel: AUTH, then SCENE lines, no replies)."""
import json
import os
import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

HOST = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HOST))
import scened  # noqa: E402

TOKEN = 'test-token-0123456789'


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class Receiver(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.r = scened.Receiver(TOKEN, '127.0.0.1', 0, clock=self.clock).start()

    def tearDown(self):
        self.r.stop()

    def nro(self, *lines, auth=TOKEN):
        s = socket.create_connection(('127.0.0.1', self.r.port), timeout=2)
        s.sendall(f'AUTH {auth}\n'.encode() + b''.join(l.encode() + b'\n' for l in lines))
        return s

    def wait_seq(self, seq):
        deadline = time.time() + 2
        while time.time() < deadline:
            if self.r.status().get('seq') == seq:
                return self.r.status()
            time.sleep(0.01)
        self.fail(f'seq {seq} never arrived: {self.r.status()}')

    def test_offline_report_opens_the_gate(self):
        with self.nro('SCENE 1 0x1020000 -1 1'):
            st = self.wait_seq(1)
        self.assertEqual((st['gate'], st['mode'], st['guards'], st['peer']), ('allowed', '0x1020000', True, '127.0.0.1'))

    def test_verdicts_match_the_sysmodule_core(self):
        cases = [('0x7010000 -1 1', 'online_mode'), ('0x87020400 0 1', 'online_mode'),
                 ('0x2010000 1 1', 'allowed'),       # local wireless with Deluxe ONLINE: allowed
                 ('0x2010000 -1 1', 'allowed'), ('0x1020000 1 1', 'online_deluxe'), ('0x0 0 1', 'allowed'),
                 ('0x1020000 0 0', 'nro_guards_failed')]
        with self.nro(*[f'SCENE {i + 1} {body}' for i, (body, _) in enumerate(cases)]):
            st = self.wait_seq(len(cases))  # every line parsed over the wire; the last one is guards=0
        self.assertEqual(st['gate'], 'nro_guards_failed')
        for body, want in cases:
            mode, deluxe, guards = body.split()
            r = {'mode': int(mode, 16), 'deluxe': int(deluxe), 'guards': guards == '1', 'received': 0.0}
            self.assertEqual(scened.gate(r, 0.1), want, body)

    def test_no_report_and_stale(self):
        self.assertEqual(self.r.status()['gate'], 'no_scene_report')
        with self.nro('SCENE 5 0x0 -1 1'):
            self.wait_seq(5)
        self.clock.t += 0.4
        self.assertEqual(self.r.status()['gate'], 'allowed')
        self.clock.t += 0.2
        st = self.r.status()
        self.assertEqual((st['gate'], st['age_ms']), ('scene_report_stale', 600))

    def test_bad_token_or_bad_line_closes_and_changes_nothing(self):
        with self.nro('SCENE 1 0x0 -1 1', auth='wrong-token-0123456789') as s:
            self.assertEqual(s.recv(16), b'')  # closed, no reply
        with self.nro('SCENE 2 0x0 -1 1', 'SCENE x 0x0 -1 1', 'SCENE 3 0x0 -1 1') as s:
            self.wait_seq(2)
            self.assertEqual(s.recv(16), b'')
        time.sleep(0.1)
        st = self.r.status()
        self.assertEqual(st['seq'], 2)
        self.assertEqual(st['rejected'], {'auth': 1, 'bad_line': 1})

    def test_parse_scene_is_strict(self):
        for bad in ('SCENE 1 0x0 -1', 'SCENE 1 0X0 -1 1', 'SCENE 1 0x123456789 -1 1', 'SCENE -1 0x0 -1 1',
                    'SCENE 1 0xG -1 1', 'SCENE 1 0x0 2 1', 'SCENE 1 0x0 -1 2', 'SCENE  1 0x0 -1 1', 'PING'):
            self.assertIsNone(scened.parse_scene(bad), bad)
        self.assertEqual(scened.parse_scene('SCENE 9 0xff10000 0 1'),
                         {'seq': 9, 'mode': 0xff10000, 'deluxe': 0, 'guards': True})

    def test_private_peers_only(self):
        for ip in ('172.30.254.53', '10.1.2.3', '172.31.0.1', '127.0.0.1', '169.254.1.1'):
            self.assertTrue(scened.peer_allowed(ip), ip)
        for ip in ('8.8.8.8', '172.32.0.1', '100.64.0.1', '::1', 'nonsense'):
            self.assertFalse(scened.peer_allowed(ip), ip)

    def test_token_never_in_status(self):
        with self.nro('SCENE 1 0x0 -1 1'):
            self.wait_seq(1)
        self.assertNotIn(TOKEN, json.dumps(self.r.status()))


class Daemon(unittest.TestCase):
    def test_serve_answers_over_the_unix_socket(self):
        import subprocess
        tmp = Path(tempfile.mkdtemp(prefix='sr-scened-'))
        tok = tmp / 'token'
        tok.write_text(TOKEN)
        tok.chmod(0o600)
        sock = tmp / 's.sock'
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        env = dict(os.environ, SWITCH_REMOTE_TOKEN_FILE=str(tok))
        p = subprocess.Popen([sys.executable, '-I', str(HOST / 'scened.py'), 'serve', '--bind', '127.0.0.1',
                              '--port', str(port), '--socket', str(sock)], env=env, stdout=subprocess.PIPE, text=True)
        try:
            self.assertIn('listening', p.stdout.readline())
            self.assertEqual(scened.query(str(sock))['gate'], 'no_scene_report')
            with socket.create_connection(('127.0.0.1', port), timeout=2) as s:
                s.sendall(f'AUTH {TOKEN}\nSCENE 1 0x0 -1 1\n'.encode())
                deadline = time.time() + 2
                while scened.query(str(sock))['gate'] != 'allowed' and time.time() < deadline:
                    time.sleep(0.02)
                self.assertEqual(scened.query(str(sock))['gate'], 'allowed')
            self.assertEqual(oct(sock.stat().st_mode & 0o777), '0o600')
        finally:
            p.terminate()
            p.wait(5)
            p.stdout.close()
            for f in tmp.iterdir():
                f.unlink()
            tmp.rmdir()


if __name__ == '__main__':
    unittest.main()
