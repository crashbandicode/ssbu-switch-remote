"""The sys-autopilot driver (autopilot.py) against a fake sys-autopilot REST server written from its README (REST API
section, pinned 6ca6aac): bearer auth, /status, /input/{tap,hold,release,stick,clear}, /files. A fake scene source
stands in for scened.py; a fake capture serves stills for the LAUNCH/TERMINATE image gate."""
import json
import shutil
import socket
import sys
import tempfile
import threading
import time
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HOST = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HOST))
import autopilot  # noqa: E402
import switch_remote as sr  # noqa: E402
from PIL import Image, ImageEnhance  # noqa: E402

TOKEN = 'test-token-0123456789'
HOME_STILL = Path(__file__).resolve().parent / 'data' / 'home-ssbu-first-tile.jpg'
ALLOWED = {'gate': 'allowed', 'mode': '0x1020000', 'age_ms': 40}


class FakeScene:
    def __init__(self, st=None):
        self.st = dict(st or ALLOWED)

    def status(self):
        return dict(self.st)


class FakeAutopilot(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, sd):
        self.calls, self.held, self.sticks = [], set(), {'left': (0.0, 0.0), 'right': (0.0, 0.0)}
        self.sd, self.on_tap = sd, None
        super().__init__(('127.0.0.1', 0), Handler)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def reply(self, code, obj, raw=None):
        body = raw if raw is not None else json.dumps(obj).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json' if raw is None else 'application/octet-stream')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def authed(self):
        if self.headers.get('Authorization') != f'Bearer {TOKEN}':
            self.reply(401, {'error': 'unauthorized'})
            return False
        return True

    def do_GET(self):
        if not self.authed():
            return
        u = urllib.parse.urlparse(self.path)
        if u.path == '/status':
            self.server.calls.append(('GET', '/status', None))
            return self.reply(200, {'version': '1.5.0', 'firmware': '23.5.0', 'uptimeSeconds': 7,
                                    'controllerAttached': bool(self.server.held)})
        if u.path == '/files':
            q = urllib.parse.parse_qs(u.query)
            path = q['path'][0]
            self.server.calls.append(('GET', '/files', path))
            local = self.server.sd / path.lstrip('/')
            if path.endswith('/'):
                if not local.is_dir():
                    return self.reply(404, {'error': 'directory not found'})
                ents = [{'name': p.name, 'type': 'dir'} if p.is_dir() else
                        {'name': p.name, 'type': 'file', 'size': p.stat().st_size, 'mtime': 1700000000}
                        for p in sorted(local.iterdir())]
                return self.reply(200, {'path': path, 'entries': ents})
            if not local.is_file():
                return self.reply(404, {'error': 'not found'})
            data = local.read_bytes()
            off = int(q.get('offset', ['0'])[0])
            return self.reply(200, None, raw=data[off:])
        self.reply(404, {'error': 'not found'})

    def do_POST(self):
        if not self.authed():
            return
        n = int(self.headers.get('Content-Length') or 0)
        body = json.loads(self.rfile.read(n) or b'{}')
        s = self.server
        s.calls.append(('POST', self.path, body))
        if self.path == '/input/tap':
            time.sleep(body.get('durationMs', 100) / 1000.0)  # synchronous, as on the console
            if s.on_tap:
                s.on_tap(body['buttons'])
        elif self.path == '/input/hold':
            s.held |= set(body['buttons'])
        elif self.path == '/input/release':
            s.held -= set(body['buttons'])
        elif self.path == '/input/stick':
            s.sticks[body['side']] = (body['x'], body['y'])
        elif self.path == '/input/clear':
            s.held.clear()
            s.sticks = {'left': (0.0, 0.0), 'right': (0.0, 0.0)}
        else:
            return self.reply(404, {'error': 'not found'})
        self.reply(200, {'ok': True})


def closed_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='sr-ap-'))
        self.srv = FakeAutopilot(self.tmp / 'sd')
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.port = self.srv.server_address[1]
        self.scene = FakeScene()
        self.c = self.client()

    def client(self, **kw):
        kw.setdefault('scene', self.scene)
        return autopilot.AutopilotClient('127.0.0.1', kw.pop('port', self.port), kw.pop('token', TOKEN),
                                         own_port=kw.pop('own_port', closed_port()), capture_backend='capd',
                                         capd_socket='/nonexistent/capd.sock', still_dir=self.tmp / 'stills',
                                         settle_s=0.05, **kw)

    def tearDown(self):
        self.c.close()
        self.srv.shutdown()
        self.srv.server_close()
        shutil.rmtree(self.tmp)

    def posts(self, path):
        return [b for m, p, b in self.srv.calls if m == 'POST' and p == path]

    def taps(self):
        return [b['buttons'] for b in self.posts('/input/tap')]


class Input(Base):
    def test_tap_maps_to_input_tap_with_duration(self):
        r = self.c.request('input.button', {'button': 'dup', 'state': 'tap', 'duration_ms': 50})
        self.assertTrue(r['ok'], r)
        self.assertEqual(self.posts('/input/tap'), [{'buttons': ['UP'], 'durationMs': 50}])

    def test_press_is_released_by_the_host_lease(self):
        self.assertTrue(self.c.request('input.button', {'button': 'zl', 'state': 'press', 'lease_ms': 150})['ok'])
        self.assertEqual(self.srv.held, {'ZL'})
        time.sleep(0.5)
        self.assertEqual(self.srv.held, set())
        self.assertEqual(self.posts('/input/release'), [{'buttons': ['ZL']}])

    def test_renewed_press_keeps_holding_and_release_cancels_the_lease(self):
        self.c.request('input.button', {'button': 'a', 'state': 'press', 'lease_ms': 200})
        time.sleep(0.1)
        self.c.request('input.button', {'button': 'a', 'state': 'press', 'lease_ms': 400})
        time.sleep(0.2)
        self.assertEqual(self.srv.held, {'A'})
        self.c.request('input.button', {'button': 'a', 'state': 'release'})
        self.assertEqual(self.srv.held, set())
        time.sleep(0.4)
        self.assertEqual(len(self.posts('/input/release')), 1)

    def test_stick_is_recentred_by_the_lease(self):
        self.c.request('input.stick', {'stick': 'right', 'x': 2.0, 'y': -0.5, 'lease_ms': 100})
        self.assertEqual(self.srv.sticks['right'], (1.0, -0.5))
        time.sleep(0.4)
        self.assertEqual(self.srv.sticks['right'], (0.0, 0.0))

    def test_reset_clears_and_cancels_leases(self):
        self.c.request('input.button', {'button': 'b', 'state': 'press', 'lease_ms': 300})
        self.assertTrue(self.c.request('input.reset')['ok'])
        self.assertEqual(self.srv.held, set())
        time.sleep(0.5)
        self.assertEqual(self.posts('/input/release'), [])

    def test_system_buttons_refused_without_a_request(self):
        for b in ('home', 'capture', 'sl_left'):
            self.assertEqual(self.c.request('input.button', {'button': b})['error']['code'], 'refused', b)
        self.assertEqual(self.srv.calls, [])

    def test_not_part_of_the_driver(self):
        for m in ('switch.dismiss', 'switch.records', 'switch.put', 'switch.update', 'switch.restart'):
            self.assertEqual(self.c.request(m, {'path': '/x'})['error']['code'], 'unsupported', m)
        self.assertEqual(self.srv.calls, [])

    def test_status_and_ping(self):
        s = self.c.request('status')['result']
        self.assertEqual((s['backend'], s['state'], s['version'], s['gate'], s['running']),
                         ('autopilot', 'reachable', '1.5.0', 'allowed', True))
        self.scene.st = {'gate': 'no_scene_report'}
        self.assertFalse(self.c.request('status')['result']['running'])
        self.assertEqual(self.c.request('ping')['result'], {'pong': '1.5.0'})

    def test_bad_token_is_an_auth_error_and_never_echoed(self):
        c = self.client(token='wrong-token-0123456789')
        r = c.request('input.button', {'button': 'a'})
        self.assertEqual(r['error']['code'], 'auth')
        self.assertNotIn('wrong-token', json.dumps(r))
        self.assertNotIn(TOKEN, json.dumps(self.c.request('status')))

    def test_unreachable(self):
        c = self.client(port=closed_port(), http_timeout=1.0)
        self.assertEqual(c.request('status')['result']['state'], 'unreachable')
        self.assertEqual(c.request('input.button', {'button': 'a'})['error']['code'], 'unreachable')

    def test_refuses_input_while_our_sysmodule_listens(self):
        with socket.socket() as own:
            own.bind(('127.0.0.1', 0))
            own.listen()
            c = self.client(own_port=own.getsockname()[1])
            r = c.request('input.button', {'button': 'a'})
        self.assertEqual(r['error']['code'], 'two_drivers')
        self.assertEqual(self.posts('/input/tap'), [])
        self.assertTrue(c.request('input.reset')['ok'])

    def test_make_client_schemes(self):
        c = sr.make_client(f'autopilot://127.0.0.1:{self.port}', token=TOKEN)
        self.assertIsInstance(c, autopilot.AutopilotClient)
        self.assertEqual(c.base, f'http://127.0.0.1:{self.port}')
        self.assertEqual(sr.make_client('autopilot://10.0.0.5', token=TOKEN).base, 'http://10.0.0.5:4150')
        self.assertIs(type(sr.make_client('switch://10.0.0.5', token=TOKEN)), sr.SwitchClient)
        with self.assertRaises(ValueError):
            sr.make_client('http://10.0.0.5', token=TOKEN)


class Gate(Base):
    def test_every_closed_verdict_refuses_input_without_a_request(self):
        for g in ('no_scene_receiver', 'no_scene_report', 'scene_report_stale', 'nro_guards_failed', 'online_mode',
                  'online_deluxe'):
            self.scene.st = {'gate': g}
            for method, params in (('input.button', {'button': 'a'}),
                                   ('input.button', {'button': 'a', 'state': 'press'}),
                                   ('input.stick', {'x': 1, 'y': 0})):
                r = self.c.request(method, params)
                self.assertEqual((r['error']['code'], r['error']['message']), ('inject_refused', g), (g, method))
        self.assertEqual([c for c in self.srv.calls if c[0] == 'POST'], [])

    def test_local_wireless_lobby_allows_input(self):
        self.scene.st = {'gate': 'allowed', 'mode': '0x2010000', 'deluxe': 1, 'age_ms': 30}
        self.assertTrue(self.c.request('input.button', {'button': 'a'})['ok'])

    def test_release_and_reset_stay_possible_with_the_gate_closed(self):
        self.c.request('input.button', {'button': 'a', 'state': 'press', 'lease_ms': 5000})
        self.scene.st = {'gate': 'online_mode'}
        self.assertTrue(self.c.request('input.button', {'button': 'a', 'state': 'release'})['ok'])
        self.assertTrue(self.c.request('input.reset')['ok'])

    def test_watchdog_clears_held_input_when_the_gate_closes(self):
        self.c.request('input.button', {'button': 'zr', 'state': 'press', 'lease_ms': 5000})
        self.c.request('input.stick', {'x': 1, 'y': 0, 'lease_ms': 5000})
        self.assertEqual(self.srv.held, {'ZR'})
        self.scene.st = {'gate': 'online_mode'}
        deadline = time.time() + 1.0
        while self.srv.held and time.time() < deadline:
            time.sleep(0.02)
        self.assertEqual(self.srv.held, set())
        self.assertEqual(self.srv.sticks['left'], (0.0, 0.0))
        s = self.c.request('status')['result']
        self.assertEqual((s['gate_release']['gate'], s['leases']), ('online_mode', []))

    def test_scene_receiver_unreachable_closes_the_gate(self):
        import scened
        st = scened.query(str(self.tmp / 'no.sock'))
        self.assertEqual(st['gate'], 'no_scene_receiver')


def macro(*events, duration=30):
    return {'format': 'eden-input-macro', 'version': 1, 'duration_frames': duration,
            'events': [{'frame': f, 'buttons': b, 'sticks': s} for f, b, s in events]}


class Macros(Base):
    def play(self, doc):
        path = self.tmp / 'm.edenmacro.json'
        path.write_text(json.dumps(doc))
        r = self.c.request('macro.play', {'path': str(path)})
        self.assertTrue(r['ok'], r)
        self.c.wait_leases()
        return self.c.request('macro.status')['result']

    def test_samples_become_hold_release_and_stick_calls(self):
        a, right = 1 << 0, 1 << 14
        st = self.play(macro((0, a, [0, 0, 0, 0]), (6, 0, [0, 0, 0, 0]), (12, right, [0.5, 0, 0, 0]),
                             (18, 0, [0, 0, 0, 0]), duration=24))
        self.assertEqual(st['state'], 'finished', st)
        self.assertEqual(self.posts('/input/hold'), [{'buttons': ['A']}, {'buttons': ['RIGHT']}])
        self.assertEqual(self.posts('/input/release'), [{'buttons': ['A']}, {'buttons': ['RIGHT']}])
        self.assertEqual(self.posts('/input/stick'), [{'side': 'left', 'x': 0.5, 'y': 0.0},
                                                      {'side': 'left', 'x': 0.0, 'y': 0.0}])
        self.assertIn('late_ms_max', st)
        self.assertEqual(self.srv.held, set())

    def test_library_segment_plays(self):
        seg = Path.home() / 'ssbu-mod-playbook/tools/eden-runner/library/segments/tap-a.edenmacro.json'
        if not seg.exists():
            self.skipTest('playbook library not present')
        st = self.play(json.loads(seg.read_text()))
        self.assertEqual(st['state'], 'finished', st)
        self.assertIn({'buttons': ['A']}, self.posts('/input/hold'))

    def test_gate_closing_mid_macro_stops_and_clears(self):
        doc = macro((0, 1, [0, 0, 0, 0]), (60, 0, [0, 0, 0, 0]), duration=120)
        path = self.tmp / 'm.edenmacro.json'
        path.write_text(json.dumps(doc))
        self.c.request('macro.play', {'path': str(path)})
        time.sleep(0.2)
        self.scene.st = {'gate': 'online_mode'}
        self.c.wait_leases()
        st = self.c.request('macro.status')['result']
        self.assertIn(st['state'], ('error', 'stopped'))
        self.assertEqual(self.srv.held, set())

    def test_refused_macros(self):
        self.scene.st = {'gate': 'no_scene_report'}
        path = self.tmp / 'm.edenmacro.json'
        path.write_text(json.dumps(macro((0, 1, [0, 0, 0, 0]))))
        self.assertEqual(self.c.request('macro.play', {'path': str(path)})['error']['code'], 'inject_refused')
        path.write_text(json.dumps(macro((0, 1 << 18, [0, 0, 0, 0]))))  # HOME
        self.assertEqual(self.c.request('macro.play', {'path': str(path)})['error']['code'], 'bad_macro')


class Sequences(Base):
    """LAUNCH / TERMINATE (LAUNCH re-ruling, 2026-11-16) with stills from a fake capture."""

    def setUp(self):
        super().setUp()
        self.home = Image.open(HOME_STILL).convert('RGB')
        self.frames = []
        c = self.c

        def capd(method, params=None):
            if method == 'ui.screenshot':
                im = self.frames.pop(0) if self.frames else self.home
                im.save(params['path'])
                return {'path': params['path']}
            raise sr.SwitchError('capture_unavailable', 'fake')
        c._capd = capd

    def test_launch_when_no_report_and_home_first_tile(self):
        self.scene.st = {'gate': 'no_scene_report'}
        r = self.c.request('switch.launch')
        self.assertTrue(r['ok'], r)
        self.assertEqual(self.taps(), [['HOME'], ['A']])
        self.assertTrue(Path(r['result']['still']).exists())

    def test_launch_refused_while_the_game_reports(self):
        for st in ({'gate': 'allowed', 'age_ms': 50}, {'gate': 'scene_report_stale', 'age_ms': 1200},
                   {'gate': 'online_mode', 'age_ms': 50}, {'gate': 'no_scene_receiver'}):
            self.scene.st = st
            self.assertEqual(self.c.request('switch.launch')['error']['code'], 'app_running', st)
        self.assertEqual(self.taps(), [])

    def test_launch_after_reports_stopped_long_enough(self):
        self.scene.st = {'gate': 'scene_report_stale', 'age_ms': 6000}
        self.assertTrue(self.c.request('switch.launch')['ok'])

    def test_launch_stops_after_home_when_the_still_is_not_the_first_tile(self):
        self.scene.st = {'gate': 'no_scene_report'}
        self.frames = [self.home.transpose(Image.FLIP_LEFT_RIGHT)]
        r = self.c.request('switch.launch')
        self.assertEqual(r['error']['code'], 'not_home_first_tile')
        self.assertEqual(self.taps(), [['HOME']])

    def test_terminate_home_x_a_and_waits_for_the_reports_to_stop(self):
        dialog = ImageEnhance.Brightness(self.home).enhance(0.35)  # the close dialog dims the HOME menu
        self.frames = [self.home, dialog]

        def on_tap(buttons):
            if buttons == ['A']:
                self.scene.st = {'gate': 'no_scene_report'}
        self.srv.on_tap = on_tap
        r = self.c.request('switch.terminate')
        self.assertTrue(r['ok'], r)
        self.assertEqual(self.taps(), [['HOME'], ['X'], ['A']])
        self.assertTrue(r['result']['scene_stopped'])

    def test_terminate_without_a_dialog_never_presses_a(self):
        self.frames = [self.home, self.home]
        r = self.c.request('switch.terminate')
        self.assertEqual(r['error']['code'], 'close_dialog_not_seen')
        self.assertEqual(self.taps(), [['HOME'], ['X']])

    def test_terminate_refused_when_home_is_not_the_first_tile(self):
        self.frames = [Image.new('RGB', (1280, 720))]
        self.assertEqual(self.c.request('switch.terminate')['error']['code'], 'not_home_first_tile')
        self.assertEqual(self.taps(), [['HOME']])


class Logs(Base):
    def setUp(self):
        super().setUp()
        cr = self.tmp / 'sd' / 'atmosphere' / 'crash_reports'
        cr.mkdir(parents=True)
        (cr / '01700000000_0100.log').write_bytes(b'crash report body')
        (self.tmp / 'sd' / 'ultimate' / 'switch-remote').mkdir(parents=True)
        (self.tmp / 'sd' / 'ultimate' / 'switch-remote' / 'events.log').write_bytes(b'line one\nline two\n')

    def test_list_file_fetch_inside_the_roots(self):
        ls = self.c.request('switch.list', {'root': 'crash_reports'})['result']
        self.assertEqual([(e['name'], e['size']) for e in ls], [('01700000000_0100.log', 17)])
        f = self.c.request('switch.file', {'root': 'ultimate', 'path': 'switch-remote/events.log', 'offset': 5})
        self.assertEqual(f['result']['data'], b'one\nline two\n')
        out = self.tmp / 'out' / 'events.log'
        r = self.c.request('switch.fetch', {'root': 'ultimate', 'path': 'switch-remote/events.log', 'out': str(out)})
        self.assertTrue(r['ok'], r)
        self.assertEqual(out.read_bytes(), b'line one\nline two\n')

    def test_switch_test_mirror_over_autopilot(self):
        import switch_test
        local = self.tmp / 'mirror' / 'events.log'
        m = switch_test.Mirror(self.c, 'ultimate', 'switch-remote/events.log', local)
        self.assertEqual(m.poll_once(), 18)
        (self.tmp / 'sd' / 'ultimate' / 'switch-remote' / 'events.log').write_bytes(b'line one\nline two\nthree\n')
        self.assertEqual(m.poll_once(), 6)
        self.assertEqual(local.read_bytes(), b'line one\nline two\nthree\n')
        missing = switch_test.Mirror(self.c, 'ultimate', 'switch-remote/none.log', self.tmp / 'mirror' / 'n.log')
        self.assertEqual(missing.poll_once(), 0)
        inst = switch_test.instances_for('10.0.0.5', 4150, self.tmp, [], 'autopilot')
        self.assertEqual(inst['switch']['socket'], 'autopilot://10.0.0.5:4150')

    def test_roots_and_traversal_refused(self):
        for p in ({'root': 'atmosphere', 'path': 'x'}, {'root': 'ultimate', 'path': '../atmosphere/config'},
                  {'root': 'ultimate', 'path': 'a/../../b'}):
            code = self.c.request('switch.file', p)['error']['code']
            self.assertIn(code, ('bad_root', 'bad_path'), p)
        self.assertEqual([c for c in self.srv.calls if c[1] == '/files'], [])


class Smoke(Base):
    def smoke(self):
        import smoke
        s = smoke.Smoke.__new__(smoke.Smoke)
        s.c, s.out, s.n, s.backend = self.c, self.tmp / 'smoke', 0, 'autopilot'
        (s.out / 'shots').mkdir(parents=True, exist_ok=True)
        return s

    def test_smoke_driver_check_proves_the_gate_refuses_on_home(self):
        self.scene.st = {'gate': 'no_scene_report'}
        r = self.smoke().driver_check()
        self.assertTrue(r['ok'], r)
        self.assertEqual(r['reply']['error']['message'], 'no_scene_report')
        self.assertEqual(self.taps(), [])

    def test_smoke_lease_check_and_lan_check(self):
        s = self.smoke()
        self.assertTrue(s.lease_check()['ok'])
        self.assertEqual(self.posts('/input/release'), [{'buttons': ['ZL']}])
        self.scene.st = {'gate': 'allowed', 'mode': '0x2010000', 'deluxe': 1, 'age_ms': 30}
        r = s.lan_check()
        self.assertTrue(r['ok'], r)


if __name__ == '__main__':
    unittest.main()
