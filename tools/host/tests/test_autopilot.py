"""The sys-autopilot fallback backend (autopilot.py) against a fake sys-autopilot REST server built from its README
(REST API section, pinned 6ca6aac): bearer auth, /status, /input/{tap,hold,release,stick,clear}."""
import json
import socket
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HOST = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HOST))
import autopilot  # noqa: E402
import switch_remote as sr  # noqa: E402

TOKEN = 'test-token-0123456789'


class FakeAutopilot(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self):
        self.calls, self.held, self.sticks = [], set(), {'left': (0.0, 0.0), 'right': (0.0, 0.0)}
        super().__init__(('127.0.0.1', 0), Handler)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def reply(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
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
        if self.path == '/status':
            self.server.calls.append(('GET', '/status', None))
            return self.reply(200, {'version': '1.5.0', 'firmware': '23.5.0', 'uptimeSeconds': 7,
                                    'controllerAttached': bool(self.server.held)})
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


class Fallback(unittest.TestCase):
    def setUp(self):
        self.srv = FakeAutopilot()
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.port = self.srv.server_address[1]
        self.c = autopilot.AutopilotClient('127.0.0.1', self.port, TOKEN, own_port=closed_port(),
                                           capture_backend='capd', capd_socket='/nonexistent/capd.sock')

    def tearDown(self):
        self.c.cancel_leases()
        self.srv.shutdown()
        self.srv.server_close()

    def posts(self, path):
        return [b for m, p, b in self.srv.calls if m == 'POST' and p == path]

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
        self.assertEqual(self.srv.held, {'A'})  # the first lease was replaced
        self.c.request('input.button', {'button': 'a', 'state': 'release'})
        self.assertEqual(self.srv.held, set())
        time.sleep(0.4)
        self.assertEqual(len(self.posts('/input/release')), 1)  # no second release from a stale timer

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

    def test_wait_leases_returns_after_release(self):
        self.c.request('input.button', {'button': 'x', 'state': 'press', 'lease_ms': 100})
        self.c.wait_leases()
        self.assertEqual(self.srv.held, set())

    def test_system_buttons_refused_without_a_request(self):
        for b in ('home', 'capture', 'sl_left'):
            r = self.c.request('input.button', {'button': b})
            self.assertEqual(r['error']['code'], 'refused', b)
        self.assertEqual(self.srv.calls, [])

    def test_launch_macro_and_files_unsupported(self):
        for m in ('switch.launch', 'switch.terminate', 'switch.fetch', 'macro.play'):
            self.assertEqual(self.c.request(m, {'path': '/x'})['error']['code'], 'unsupported', m)
        self.assertEqual(self.srv.calls, [])

    def test_status_and_ping(self):
        s = self.c.request('status')['result']
        self.assertEqual((s['backend'], s['state'], s['version'], s['running']), ('autopilot', 'reachable', '1.5.0', None))
        self.assertIn('error', s['capture'])
        self.assertEqual(self.c.request('ping')['result'], {'pong': '1.5.0'})

    def test_bad_token_is_an_auth_error_and_never_echoed(self):
        c = autopilot.AutopilotClient('127.0.0.1', self.port, 'wrong-token-0123456789', own_port=closed_port())
        r = c.request('input.button', {'button': 'a'})
        self.assertEqual(r['error']['code'], 'auth')
        self.assertNotIn('wrong-token', json.dumps(r))
        self.assertNotIn(TOKEN, json.dumps(self.c.request('status')))

    def test_unreachable(self):
        c = autopilot.AutopilotClient('127.0.0.1', closed_port(), TOKEN, own_port=closed_port(), http_timeout=1.0,
                                      capture_backend='capd', capd_socket='/nonexistent/capd.sock')
        self.assertEqual(c.request('status')['result']['state'], 'unreachable')
        self.assertEqual(c.request('input.button', {'button': 'a'})['error']['code'], 'unreachable')

    def test_refuses_input_while_our_sysmodule_listens(self):
        with socket.socket() as own:
            own.bind(('127.0.0.1', 0))
            own.listen()
            c = autopilot.AutopilotClient('127.0.0.1', self.port, TOKEN, own_port=own.getsockname()[1])
            r = c.request('input.button', {'button': 'a'})
        self.assertEqual(r['error']['code'], 'two_drivers')
        self.assertEqual(self.posts('/input/tap'), [])
        self.assertTrue(c.request('input.reset')['ok'])  # releasing stays possible

    def test_smoke_fallback_check(self):
        import smoke
        import tempfile
        with tempfile.TemporaryDirectory() as out:
            s = smoke.Smoke.__new__(smoke.Smoke)
            s.c, s.out, s.n, s.backend = self.c, Path(out), 0, 'autopilot'
            (s.out / 'shots').mkdir()
            self.assertTrue(s.fallback_check()['ok'])
            lines = [json.loads(x) for x in (s.out / 'receipts.jsonl').read_text().splitlines()]
        taps = [b['buttons'] for b in self.posts('/input/tap')]
        self.assertEqual(taps, [['RIGHT'], ['LEFT']])
        self.assertEqual(self.posts('/input/release'), [{'buttons': ['ZL']}])
        self.assertTrue(next(x for x in lines if x['action'] == 'lease released')['reply']['ok'])
        self.assertEqual(self.srv.held, set())

    def test_make_client_schemes(self):
        c = sr.make_client(f'autopilot://127.0.0.1:{self.port}', token=TOKEN)
        self.assertIsInstance(c, autopilot.AutopilotClient)
        self.assertEqual(c.base, f'http://127.0.0.1:{self.port}')
        self.assertEqual(sr.make_client('autopilot://10.0.0.5', token=TOKEN).base, 'http://10.0.0.5:4150')
        self.assertIs(type(sr.make_client('switch://10.0.0.5', token=TOKEN)), sr.SwitchClient)
        with self.assertRaises(ValueError):
            sr.make_client('http://10.0.0.5', token=TOKEN)


if __name__ == '__main__':
    unittest.main()
