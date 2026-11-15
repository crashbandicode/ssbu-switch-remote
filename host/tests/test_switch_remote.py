"""The host client against the sysmodule's host loopback build (sysmodule/out/loopback: the real sr::Service
with a fake platform). Run: make -C sysmodule out/loopback && /usr/bin/python3 -I -m unittest discover -s host/tests"""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

HOST = Path(__file__).resolve().parents[1]
REPO = HOST.parent
sys.path.insert(0, str(HOST))
import switch_remote as sr  # noqa: E402

LOOPBACK = REPO / 'sysmodule' / 'out' / 'loopback'
PLAYBOOK = Path('/home/user/ssbu-mod-playbook')
TAP_A = PLAYBOOK / 'tools' / 'eden-runner' / 'library' / 'segments' / 'tap-a.edenmacro.json'
TOKEN = 'test-token-0123456789'


def free_port():
    """A port P with P and P+1 free (the scene channel listens on P+1)."""
    while True:
        with socket.socket() as a, socket.socket() as b:
            a.bind(('127.0.0.1', 0))
            port = a.getsockname()[1]
            if port >= 65000:
                continue
            try:
                b.bind(('127.0.0.1', port + 1))
            except OSError:
                continue
            return port


class Loopback:
    def __init__(self):
        self.dir = Path(tempfile.mkdtemp(prefix='sr-loopback-'))
        self.root = self.dir / 'sd'
        (self.root / 'atmosphere' / 'crash_reports').mkdir(parents=True)
        (self.root / 'ultimate' / 'training_rooms').mkdir(parents=True)
        self.pad_log = self.dir / 'pad.jsonl'
        cfg = self.dir / 'config.txt'
        cfg.write_text(f'token={TOKEN}\npeers=loopback\n')
        for _ in range(5):  # another process can take the ports between the probe and the bind
            self.port = free_port()
            self.proc = subprocess.Popen([str(LOOPBACK), '--port', str(self.port), '--root', str(self.root),
                                          '--pad-log', str(self.pad_log), '--config', str(cfg)],
                                         stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
            line = self.proc.stdout.readline()
            if line:
                json.loads(line)
                break
            self.proc.wait()
        else:
            raise RuntimeError('loopback did not start')
        self.scene_stop = threading.Event()
        self.scene_thread = None

    def client(self, **kw):
        return sr.SwitchClient('127.0.0.1', self.port, TOKEN, capd_socket=str(self.dir / 'capd.sock'), **kw)

    def feed_scene(self, mode=0x01020000, deluxe=-1, guards=1):
        """Plays the in-game NRO: AUTH, then SCENE lines every 100 ms on port+1. A second call changes the
        report on the same connection, as the NRO does when the game changes scene."""
        self.scene_now = (mode, deluxe, guards)
        if self.scene_thread:
            time.sleep(0.3)
            return
        self.scene_stop = threading.Event()

        def run():
            with socket.create_connection(('127.0.0.1', self.port + 1)) as s:
                s.sendall(f'AUTH {TOKEN}\n'.encode())
                seq = 0
                while not self.scene_stop.is_set():
                    seq += 1
                    m, dx, g = self.scene_now
                    s.sendall(f'SCENE {seq} 0x{m:x} {dx} {g}\n'.encode())
                    time.sleep(0.1)

        self.scene_thread = threading.Thread(target=run, daemon=True)
        self.scene_thread.start()
        time.sleep(0.3)

    def stop_scene(self):
        if self.scene_thread:
            self.scene_stop.set()
            self.scene_thread.join()
            self.scene_thread = None
            time.sleep(0.6)  # past the 500 ms freshness limit

    def pad(self):
        if not self.pad_log.exists():
            return []
        return [json.loads(line) for line in self.pad_log.read_text().splitlines()]

    def close(self):
        self.stop_scene()
        self.proc.kill()
        self.proc.wait()
        shutil.rmtree(self.dir, ignore_errors=True)


def pressed_runs(states, bit):
    """[(start wall, end wall)] of runs where `bit` is held in the pad log."""
    runs, start = [], None
    for s in states:
        if s.get('event') != 'state':
            continue
        held = bool(s['buttons'] >> bit & 1)
        if held and start is None:
            start = s['wall']
        elif not held and start is not None:
            runs.append((start, s['wall']))
            start = None
    return runs


@unittest.skipUnless(LOOPBACK.exists(), 'build sysmodule/out/loopback first (make -C sysmodule out/loopback)')
class AgainstLoopback(unittest.TestCase):
    def setUp(self):
        self.lb = Loopback()
        self.c = self.lb.client()

    def tearDown(self):
        self.c.conn.close()
        self.lb.close()

    def test_bad_token_is_refused(self):
        bad = sr.SwitchClient('127.0.0.1', self.lb.port, 'x' * 20, capd_socket='/nonexistent')
        r = bad.request('ping')
        self.assertFalse(r['ok'])
        self.assertEqual(r['error']['code'], 'auth')

    def test_status_maps_fields(self):
        r = self.c.request('status')
        self.assertTrue(r['ok'])
        s = r['result']
        self.assertFalse(s['running'])
        self.assertEqual(s['app'], 'none')
        self.assertEqual(s['gate'], 'no_scene_report')
        self.assertIn('error', s['capture'])  # no capd in this test

    def test_input_refused_without_scene(self):
        r = self.c.request('input.button', {'button': 'a', 'state': 'tap', 'duration_ms': 100})
        self.assertFalse(r['ok'])
        self.assertEqual(r['error']['code'], 'inject_refused')
        self.assertEqual(r['error']['message'], 'no_scene_report')
        self.assertEqual(pressed_runs(self.lb.pad(), 0), [])

    def test_online_scene_refuses(self):
        self.lb.feed_scene(mode=0x07010000)
        r = self.c.request('input.button', {'button': 'a', 'state': 'tap'})
        self.assertEqual((r['ok'], r['error']['message']), (False, 'online_mode'))
        self.lb.feed_scene(mode=0x01020000, deluxe=1)
        r = self.c.request('input.button', {'button': 'a', 'state': 'tap'})
        self.assertEqual(r['error']['message'], 'online_deluxe')
        self.lb.feed_scene(mode=0x01020000, guards=0)
        r = self.c.request('input.button', {'button': 'a', 'state': 'tap'})
        self.assertEqual(r['error']['message'], 'nro_guards_failed')

    def test_stale_scene_closes_the_gate_and_releases_input(self):
        self.lb.feed_scene()
        r = self.c.request('input.button', {'button': 'b', 'state': 'press', 'lease_ms': 5000})
        self.assertTrue(r['ok'], r)
        time.sleep(0.2)
        self.lb.stop_scene()  # the NRO went quiet: crash, HOME, online...
        states = [s for s in self.lb.pad() if s.get('event') == 'state']
        self.assertTrue(states and states[-1]['buttons'] & 2 == 0, 'B must be released once the report is stale')
        self.assertEqual(len(pressed_runs(self.lb.pad(), 1)), 1)
        self.assertEqual(self.c.request('status')['result']['gate'], 'scene_report_stale')

    def test_input_button_tap_maps_to_btn(self):
        self.lb.feed_scene()
        r = self.c.request('input.button', {'button': 'a', 'state': 'tap', 'duration_ms': 100})
        self.assertTrue(r['ok'], r)
        time.sleep(0.3)
        runs = pressed_runs(self.lb.pad(), 0)
        self.assertEqual(len(runs), 1)
        self.assertAlmostEqual(runs[0][1] - runs[0][0], 0.100, delta=0.03)
        r = self.c.request('input.stick', {'stick': 'left', 'x': 0, 'y': 1.0, 'lease_ms': 200})
        self.assertTrue(r['ok'], r)
        time.sleep(0.05)
        ys = [s['ly'] for s in self.lb.pad() if s.get('event') == 'state']
        self.assertIn(32767, ys)
        self.assertFalse(self.c.request('input.button', {'button': 'home', 'state': 'tap'})['ok'])

    def test_macro_events_parity_with_eden_file(self):
        doc = json.loads(TAP_A.read_text())
        # The lines carry exactly the Eden file's samples.
        lines = sr.macro_lines(doc)
        self.assertEqual(lines[0], f'MBEGIN {len(doc["events"])} {doc["duration_frames"]}')
        self.assertEqual([int(line.split()[1]) for line in lines[1:]], [e['frame'] for e in doc['events']])
        self.assertEqual([int(line.split()[2]) for line in lines[1:]], [e['buttons'] for e in doc['events']])
        self.lb.feed_scene()
        r = self.c.request('macro.play', {'path': str(TAP_A)})
        self.assertTrue(r['ok'], r)
        deadline = time.time() + 5
        while time.time() < deadline:
            st = self.c.request('macro.status')['result']
            if st['state'] == 'finished':
                break
            time.sleep(0.05)
        self.assertEqual(st['state'], 'finished', st)
        # Eden: A from frame 1 to frame 5 -> held 4 frames (66.7 ms) at 60 Hz.
        runs = pressed_runs(self.lb.pad(), 0)
        self.assertEqual(len(runs), 1)
        self.assertAlmostEqual(runs[0][1] - runs[0][0], 4 / 60, delta=0.02)

    def test_macro_refused_mid_play_when_scene_goes_online(self):
        long_hold = {'format': 'eden-input-macro', 'duration_frames': 120,
                     'events': [{'frame': 0, 'buttons': 1, 'sticks': [0, 0, 0, 0]},
                                {'frame': 100, 'buttons': 0, 'sticks': [0, 0, 0, 0]}]}
        self.lb.feed_scene()
        self.assertTrue(self.c.play_macro(long_hold)['accepted'])
        time.sleep(0.3)
        self.lb.feed_scene(mode=0x07010000)
        st = self.c.request('macro.status')['result']
        self.assertEqual((st['state'], st['error']), ('error', 'online_mode'))
        states = [s for s in self.lb.pad() if s.get('event') == 'state']
        self.assertEqual(states[-1]['buttons'] & 1, 0)

    def test_launch_only_without_application(self):
        (self.lb.root / 'fake-app').write_text('81 01006a800016e000\n')
        time.sleep(0.3)
        r = self.c.request('switch.launch')
        self.assertEqual((r['ok'], r['error']['message']), (False, 'application_running'))
        (self.lb.root / 'fake-app').unlink()
        time.sleep(0.3)
        r = self.c.request('switch.launch')
        self.assertTrue(r['ok'], r)
        time.sleep(2.2)
        home = pressed_runs(self.lb.pad(), 18)
        a = pressed_runs(self.lb.pad(), 0)
        self.assertEqual((len(home), len(a)), (1, 1))
        self.assertGreater(a[0][0] - home[0][1], 1.4)  # HOME, wait 1.5 s, then A

    def test_terminate_and_exit_count(self):
        (self.lb.root / 'fake-app').write_text('81 01006a800016e000\n')
        time.sleep(0.3)
        s = self.c.request('status')['result']
        self.assertTrue(s['running'])
        self.assertEqual(s['program'], '0x1006a800016e000')
        r = self.c.request('switch.terminate')
        self.assertTrue(r['ok'], r)
        self.assertFalse((self.lb.root / 'fake-app').exists())
        s = self.c.request('status')['result']
        self.assertEqual((s['running'], s['exits']), (False, '1'))

    def test_list_and_file_roots(self):
        report = self.lb.root / 'atmosphere' / 'crash_reports' / '01700000000_01006a800016e000.log'
        report.write_bytes(b'Atmosphere Crash Report\n' * 5000)  # 120 KB: two chunks
        (self.lb.root / 'ultimate' / 'training_rooms' / 'events.log').write_text('install line\n')
        r = self.c.request('switch.list', {'root': 'crash_reports'})
        self.assertTrue(r['ok'], r)
        self.assertEqual([e['name'] for e in r['result']], [report.name])
        self.assertEqual(r['result'][0]['size'], report.stat().st_size)
        out = self.lb.dir / 'fetched.log'
        r = self.c.request('switch.fetch', {'root': 'crash_reports', 'path': report.name, 'out': str(out)})
        self.assertTrue(r['ok'], r)
        self.assertEqual(out.read_bytes(), report.read_bytes())
        r = self.c.request('switch.list', {'root': 'ultimate', 'dir': 'training_rooms'})
        self.assertEqual([e['name'] for e in r['result']], ['events.log'])
        for bad in ('../x', '/etc/passwd', 'a//b'):
            r = self.c.request('switch.file', {'root': 'ultimate', 'path': bad})
            self.assertEqual(r['error']['code'], 'bad_path', bad)
        self.assertEqual(self.c.request('switch.list', {'root': 'nand'})['error']['code'], 'bad_root')

    def test_dropped_connection_reports_not_running(self):
        self.assertTrue(self.c.request('ping')['ok'])
        self.lb.proc.kill()
        self.lb.proc.wait()
        s = self.c.request('status')
        self.assertTrue(s['ok'])
        self.assertEqual((s['result']['running'], s['result']['state']), (False, 'unreachable'))
        self.assertEqual(self.c.request('input.reset')['error']['code'], 'unreachable')


class FakeCapd:
    """A capd stand-in on a Unix socket that records calls."""

    def __init__(self, path):
        import socketserver
        self.calls = []
        outer = self

        class H(socketserver.StreamRequestHandler):
            def handle(self):
                for line in self.rfile:
                    req = json.loads(line)
                    outer.calls.append((req['method'], req.get('params')))
                    result = {'status': {'signal': True, 'frames': 9, 'last_frame_age_ms': 5},
                              'ui.screenshot': {'path': (req.get('params') or {}).get('path'), 'width': 1280}}
                    self.wfile.write(json.dumps({'id': req['id'], 'ok': True,
                                                 'result': result.get(req['method'], {})}).encode() + b'\n')

        self.server = socketserver.UnixStreamServer(path, H)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class Capture(unittest.TestCase):
    def test_screenshot_goes_to_capd(self):
        with tempfile.TemporaryDirectory() as d:
            capd = FakeCapd(f'{d}/capd.sock')
            try:
                c = sr.SwitchClient('127.0.0.1', 1, TOKEN, capd_socket=f'{d}/capd.sock')
                r = c.request('ui.screenshot', {'target': 'switch-capture', 'path': f'{d}/x.png'})
                self.assertTrue(r['ok'], r)
                self.assertEqual(capd.calls[-1], ('ui.screenshot', {'path': f'{d}/x.png', 'width': 1280}))
                tree = c.request('ui.tree', {'brief': True})['result']
                self.assertEqual(tree[0]['class'], 'GRenderWindow')
            finally:
                capd.close()

    def test_status_merges_sysmodule_and_capture(self):
        with tempfile.TemporaryDirectory() as d:
            capd = FakeCapd(f'{d}/capd.sock')
            try:
                c = sr.SwitchClient('127.0.0.1', free_port(), TOKEN, capd_socket=f'{d}/capd.sock', timeout=1)
                s = c.request('status')['result']
                self.assertEqual((s['running'], s['state']), (False, 'unreachable'))
                self.assertEqual(s['capture'], {'signal': True, 'frames': 9, 'last_frame_age_ms': 5})
            finally:
                capd.close()


class RunnerFactory(unittest.TestCase):
    @unittest.skipUnless((PLAYBOOK / 'tools' / 'eden-runner' / 'runner.py').exists(), 'playbook not cloned')
    def test_runner_factory_switch_scheme(self):
        """The proposed playbook patch applies to the runner and selects SwitchClient for switch:// sockets."""
        with tempfile.TemporaryDirectory() as d:
            tree = Path(d) / 'pb'
            shutil.copytree(PLAYBOOK / 'tools' / 'eden-runner', tree / 'tools' / 'eden-runner')
            patch = REPO / 'playbook-patch' / '0001-runner-switch-backend.patch'
            subprocess.run(['git', 'apply', str(patch)], cwd=tree, check=True)
            env_before = os.environ.get('SWITCH_REMOTE_DIR')
            os.environ['SWITCH_REMOTE_DIR'] = str(HOST)
            sys.path.insert(0, str(tree / 'tools' / 'eden-runner'))
            try:
                import importlib
                runner = importlib.import_module('runner')
                tokfile = Path(d) / 'token'
                tokfile.write_text(TOKEN)
                os.environ['SWITCH_REMOTE_TOKEN_FILE'] = str(tokfile)
                client = runner.default_client_factory()('switch://172.30.254.92:47130')
                self.assertIsInstance(client, sr.SwitchClient if 'switch_remote' not in sys.modules
                                      else sys.modules['switch_remote'].SwitchClient)
                self.assertEqual((client.conn.host, client.conn.port), ('172.30.254.92', 47130))
            finally:
                sys.path.remove(str(tree / 'tools' / 'eden-runner'))
                sys.modules.pop('runner', None)
                sys.modules.pop('harness', None)
                os.environ.pop('SWITCH_REMOTE_TOKEN_FILE', None)
                if env_before is None:
                    os.environ.pop('SWITCH_REMOTE_DIR', None)
                else:
                    os.environ['SWITCH_REMOTE_DIR'] = env_before


class Pure(unittest.TestCase):
    def test_button_names(self):
        self.assertEqual(sr.button_bit('zr'), 9)
        self.assertEqual(sr.button_bit('ddown'), 15)
        with self.assertRaises(sr.SwitchError):
            sr.button_bit('capture')
        with self.assertRaises(sr.SwitchError):
            sr.button_bit('nope')

    def test_stick_values(self):
        self.assertEqual(sr.stick_value(1), 32767)
        self.assertEqual(sr.stick_value(-2), -32767)
        self.assertEqual(sr.stick_value(float('nan')), 0)

    def test_make_client_parses_address(self):
        c = sr.make_client('switch://10.0.0.5:5000', token=TOKEN)
        self.assertEqual((c.conn.host, c.conn.port), ('10.0.0.5', 5000))
        c = sr.make_client('switch://10.0.0.5', token=TOKEN)
        self.assertEqual(c.conn.port, sr.DEFAULT_PORT)
        with self.assertRaises(ValueError):
            sr.make_client('/tmp/eden.sock', token=TOKEN)

    def test_macro_lines_refuse_bad_files(self):
        with self.assertRaises(sr.SwitchError):
            sr.macro_lines({'format': 'other'})
        with self.assertRaises(sr.SwitchError):
            sr.macro_lines({'format': 'eden-input-macro', 'events': [], 'duration_frames': 5})


if __name__ == '__main__':
    unittest.main()
