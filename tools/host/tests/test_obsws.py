"""obsws.py against a fake obs-websocket v5 server (handshake, Hello/Identify auth, requests)."""
import base64
import hashlib
import json
import socket
import socketserver
import struct
import sys
import tempfile
import threading
import unittest
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import obsws  # noqa: E402
import switch_remote as sr  # noqa: E402

PASSWORD = 'fake-obs-password-not-real'


def tiny_png(w=4, h=2):
    raw = b''.join(b'\x00' + b'\x10\x20\x30' * w for _ in range(h))

    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b''))


class FakeObs:
    """Serves one obs-websocket session at a time on 127.0.0.1."""

    def __init__(self, password=PASSWORD, inputs=None, replay_dir=None):
        self.password, self.calls = password, []
        self.inputs = inputs if inputs is not None else [
            {'inputName': 'Mic', 'inputKind': 'pulse_input_capture'},
            {'inputName': 'Switch', 'inputKind': 'v4l2_input', 'settings': {'device_id': '/dev/video4'}}]
        self.replay_dir = replay_dir
        outer = self

        class H(socketserver.BaseRequestHandler):
            def handle(self):
                outer.session(self.request)

        class S(socketserver.ThreadingMixIn, socketserver.TCPServer):
            daemon_threads = True
            allow_reuse_address = True

        self.server = S(('127.0.0.1', 0), H)
        self.url = f'ws://127.0.0.1:{self.server.server_address[1]}'
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    # --- server-side websocket ---
    @staticmethod
    def send(sock, obj):
        data = json.dumps(obj).encode()
        n = len(data)
        head = bytes([0x81]) + (bytes([n]) if n < 126 else bytes([126]) + struct.pack('>H', n) if n < 65536
                                else bytes([127]) + struct.pack('>Q', n))
        sock.sendall(head + data)

    @staticmethod
    def recv(f):
        head = f.read(2)
        if len(head) < 2:  # the client went away
            return None
        b0, b1 = head
        n = b1 & 0x7f
        if n == 126:
            n = struct.unpack('>H', f.read(2))[0]
        elif n == 127:
            n = struct.unpack('>Q', f.read(8))[0]
        mask = f.read(4)
        payload = bytes(b ^ mask[i % 4] for i, b in enumerate(f.read(n)))
        if b0 & 0x0f == 0x8:
            return None
        return json.loads(payload)

    def session(self, sock):
        f = sock.makefile('rb')
        head = b''
        while not head.endswith(b'\r\n\r\n'):
            head += f.read(1)
        key = [line.split(b':', 1)[1].strip() for line in head.split(b'\r\n') if line.lower().startswith(b'sec-websocket-key')][0]
        accept = base64.b64encode(hashlib.sha1(key + b'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').digest())
        sock.sendall(b'HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n'
                     b'Sec-WebSocket-Accept: ' + accept + b'\r\n\r\n')
        salt, challenge = 'c2FsdA==', 'Y2hhbGxlbmdl'
        self.send(sock, {'op': 0, 'd': {'obsWebSocketVersion': '5.6.0', 'rpcVersion': 1,
                                        'authentication': {'salt': salt, 'challenge': challenge}}})
        ident = self.recv(f)
        if ident['d'].get('authentication') != obsws.auth_string(self.password, salt, challenge):
            sock.sendall(b'\x88\x02\x0f\xa8')  # close 4008 authentication failed
            return
        self.send(sock, {'op': 2, 'd': {'negotiatedRpcVersion': 1}})
        while True:
            msg = self.recv(f)
            if msg is None:
                return
            d = msg['d']
            self.calls.append((d['requestType'], d.get('requestData')))
            ok, data = True, {}
            t = d['requestType']
            if t == 'GetInputList':
                data = {'inputs': [{k: v for k, v in i.items() if k != 'settings'} for i in self.inputs]}
            elif t == 'GetInputSettings':
                i = [x for x in self.inputs if x['inputName'] == d['requestData']['inputName']][0]
                data = {'inputSettings': i.get('settings', {})}
            elif t == 'GetSourceScreenshot':
                data = {'imageData': 'data:image/png;base64,' + base64.b64encode(tiny_png()).decode()}
            elif t == 'StopRecord':
                if not getattr(self, 'session_active', False) and not getattr(self, 'pending_active', 0):
                    ok = False  # OBS 501: not recording
                else:
                    self.session_active, self.pending_active = False, 0
                    if self.replay_dir:  # like OBS: the answer comes first, the output stays active a little longer
                        path = self.replay_dir / '2026-11-15 22-00-00.mp4'
                        path.write_bytes(b'mp4')
                        self.active_after_stop = 2
                        data = {'outputPath': str(path)}
                    else:
                        data = {'outputPath': '/home/x/obs/2026-11-15 22-00-00.mkv'}
            elif t == 'GetRecordStatus':
                if getattr(self, 'active_after_stop', 0):
                    self.active_after_stop -= 1
                    data = {'outputActive': True}
                elif getattr(self, 'pending_active', 0):  # like OBS: StartRecord answers before the output runs
                    self.pending_active -= 1
                    self.session_active = self.pending_active == 0
                    data = {'outputActive': False}
                elif getattr(self, 'session_active', False):
                    data = {'outputActive': True, 'outputDuration': 12345, 'outputTimecode': '00:00:12.345'}
                else:
                    data = {'outputActive': False}
            elif t == 'StartRecord':
                if getattr(self, 'refuse_start', 0):
                    self.refuse_start -= 1
                    ok = False
                    if getattr(self, 'start_anyway', False):  # refused, but OBS started (reconcile by status)
                        self.session_active = True
                else:
                    self.pending_active = 2
            elif t == 'CreateRecordChapter':
                ok = getattr(self, 'chapters', True)
            elif t == 'GetRecordDirectory':
                data = {'recordDirectory': str(self.replay_dir or '/nonexistent')}
            elif t == 'GetLastReplayBufferReplay':
                data = {'savedReplayPath': str(self.replay_dir / 'Replay.mkv') if self.replay_dir else None}
            elif t == 'SaveReplayBuffer':
                data = {}
            else:
                ok = False
            self.send(sock, {'op': 7, 'd': {'requestType': t, 'requestId': d['requestId'],
                                            'requestStatus': {'result': ok, 'code': 100 if ok else 204},
                                            'responseData': data}})


class Obs(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.tmp = Path(self.dir.name)
        (self.tmp / 'Replay.mkv').write_bytes(b'x')
        self.obs = FakeObs(replay_dir=self.tmp)

    def tearDown(self):
        self.obs.close()
        self.dir.cleanup()

    def client(self, password=PASSWORD):
        return obsws.ObsClient(self.obs.url, password)

    def test_auth_string_matches_the_v5_formula(self):
        secret = base64.b64encode(hashlib.sha256(b'pwsalt').digest()).decode()
        expected = base64.b64encode(hashlib.sha256((secret + 'chal').encode()).digest()).decode()
        self.assertEqual(obsws.auth_string('pw', 'salt', 'chal'), expected)

    def test_wrong_password_is_refused_without_echoing_it(self):
        with self.assertRaises(obsws.ObsError) as cm:
            self.client('wrong-password-xyz')
        self.assertEqual(cm.exception.code, 'auth')
        self.assertNotIn('wrong-password-xyz', str(cm.exception))

    def test_finds_the_card_source_and_screenshots(self):
        c = self.client()
        cap = obsws.ObsCapture(c)
        self.assertEqual(cap.source, 'Switch')
        out = self.tmp / 'shot.png'
        r = cap.handle('ui.screenshot', {'path': str(out), 'width': 1280})
        self.assertEqual((r['width'], r['height'], r['backend']), (4, 2, 'obs'))
        self.assertEqual(out.read_bytes()[:8], b'\x89PNG\r\n\x1a\n')
        self.assertIn(('GetSourceScreenshot', {'sourceName': 'Switch', 'imageFormat': 'png', 'imageWidth': 1280}),
                      self.obs.calls)
        c.close()

    def test_record_and_replay(self):
        cap = obsws.ObsCapture(self.client())
        self.assertTrue(cap.handle('recording.start', {})['accepted'])
        stop = cap.handle('recording.stop', {})
        self.assertTrue(stop['path'].endswith('.mp4'))
        self.assertTrue(stop['approximate_start'])
        # it waited for the output to stop (two active polls) and the file size to hold before answering
        self.assertTrue(stop['finalized'])
        self.assertGreaterEqual([c[0] for c in self.obs.calls].count('GetRecordStatus'), 4)
        self.assertFalse(obsws.ObsCapture.wait_finalized(cap, str(self.tmp / 'missing.mp4'), cap=0.5))
        replay = cap.handle('replay.save', {})
        self.assertEqual(replay['path'], str(self.tmp / 'Replay.mkv'))

    def test_session_recording_start_mark_stop(self):
        cap = obsws.ObsCapture(self.client())
        types = lambda: [c[0] for c in self.obs.calls]  # noqa: E731
        idle = cap.handle('recording.session_mark', {'label': 'X-01 start', 'chapter': True})
        self.assertEqual((idle['active'], idle['rec_ms'], idle['frame'], idle['chapter_ok']), (False, None, None, None))
        self.obs.refuse_start = 1  # OBS refuses once (obs_500 while a file finalizes): retried after 2 s
        start = cap.handle('recording.session_start', {})
        self.assertFalse(start['already_active'])
        self.assertEqual(types().count('StartRecord'), 2)
        self.assertIn('GetRecordDirectory', types())  # it waited for the newest output file to hold its size
        # it returned only once OBS reported the output active (StartRecord answers first: two inactive polls)
        self.assertTrue(self.obs.session_active)
        n = types().count('StartRecord')
        # an output still active: refused, never a second StartRecord (hw-run-2b's stray recording + obs_500)
        with self.assertRaises(obsws.ObsError) as cm:
            cap.handle('recording.session_start', {'idle_cap': 0.5})
        self.assertEqual(cm.exception.code, 'busy')
        self.assertEqual(types().count('StartRecord'), n)
        mark = cap.handle('recording.session_mark', {'label': 'DC-02 start', 'chapter': True})
        self.assertEqual((mark['active'], mark['rec_ms'], mark['frame'], mark['chapter_ok']), (True, 12345, 741, True))
        self.assertIn(('CreateRecordChapter', {'chapterName': 'DC-02 start'}), self.obs.calls)
        self.obs.chapters = False  # an OBS or format without chapters: reported, not raised
        self.assertFalse(cap.handle('recording.session_mark', {'label': 'x', 'chapter': True})['chapter_ok'])
        self.assertIsNone(cap.handle('recording.session_mark', {'label': 'y'})['chapter_ok'])
        self.obs.session_active = False
        self.assertFalse(cap.handle('recording.session_stop', {})['was_active'])
        self.obs.session_active = True  # the fake's StopRecord ends it
        stop = cap.handle('recording.session_stop', {})
        self.assertTrue(stop['was_active'])
        self.assertTrue(stop['path'].endswith('.mp4'))
        self.assertTrue(stop['finalized'])
        with self.assertRaises(KeyError):
            cap.handle('recording.session_pause', {})

    def test_start_and_stop_reconcile_refusals_with_the_record_status(self):
        cap = obsws.ObsCapture(self.client())
        types = lambda: [c[0] for c in self.obs.calls]  # noqa: E731
        # a refused StartRecord (500) after which OBS records anyway: reconciled as started, no second start
        self.obs.refuse_start, self.obs.start_anyway = 1, True
        cap.handle('recording.start', {})
        self.assertEqual(types().count('StartRecord'), 1)
        stop = cap.handle('recording.stop', {})
        self.assertTrue(stop['finalized'])
        # two refusals with OBS idle: an error carrying OBS's code
        self.obs.refuse_start, self.obs.start_anyway = 2, False
        with self.assertRaises(obsws.ObsError):
            cap.handle('recording.start', {'idle_cap': 2})
        # a stop with nothing recording (OBS 501): reconciled to "no output", not an exception
        stop = cap.handle('recording.stop', {})
        self.assertEqual((stop['path'], stop['finalized']), (None, False))

    def test_no_source_for_the_device(self):
        self.obs.inputs = [{'inputName': 'Cam', 'inputKind': 'v4l2_input', 'settings': {'device_id': '/dev/video0'}}]
        with self.assertRaises(obsws.ObsError) as cm:
            obsws.ObsCapture(self.client())
        self.assertEqual(cm.exception.code, 'no_source')

    def test_pick_backend_prefers_obs_then_falls_back(self):
        kind, cap = obsws.pick_backend(obs_factory=self.client)
        self.assertEqual((kind, cap.source), ('obs', 'Switch'))

        def down():
            raise ConnectionRefusedError('refused')
        kind, why = obsws.pick_backend(obs_factory=down)
        self.assertEqual(kind, 'capd')
        self.assertIn('OBS unavailable', why)

    def test_switch_client_routes_session_recording_to_obs_without_gif(self):
        c = sr.SwitchClient('127.0.0.1', 1, 't' * 20, capd_socket='/nonexistent', obs_factory=self.client)
        self.obs.session_active = True
        r = c.request('recording.session_mark', {'label': 'T-01 start'})
        self.assertTrue(r['ok'], r)
        self.assertEqual(r['result']['rec_ms'], 12345)
        r = c.request('recording.session_stop', {})
        self.assertTrue(r['ok'], r)
        self.assertTrue(r['result']['path'].endswith('.mp4'))
        self.assertNotIn('frames', r['result'])  # no clip/GIF encoding on the session path

    def test_switch_client_routes_screenshots_to_obs(self):
        c = sr.SwitchClient('127.0.0.1', 1, 't' * 20, capd_socket='/nonexistent', obs_factory=self.client)
        out = self.tmp / 'via-client.png'
        r = c.request('ui.screenshot', {'path': str(out)})
        self.assertTrue(r['ok'], r)
        self.assertEqual(r['result']['backend'], 'obs')
        forced = sr.SwitchClient('127.0.0.1', 1, 't' * 20, capd_socket='/nonexistent', capture_backend='obs',
                                 obs_factory=lambda: (_ for _ in ()).throw(ConnectionRefusedError()))
        r = forced.request('ui.screenshot', {'path': str(out)})
        self.assertEqual(r['error']['code'], 'capture_unavailable')

    def test_credentials_from_claude_json_and_env(self):
        cfg = self.tmp / 'claude.json'
        cfg.write_text(json.dumps({'mcpServers': {'obs-mcp': {'env': {'OBS_WEBSOCKET_URL': 'ws://h:1',
                                                                       'OBS_WEBSOCKET_PASSWORD': 'p'}}}}))
        self.assertEqual(obsws.load_credentials({}, cfg), ('ws://h:1', 'p'))
        self.assertEqual(obsws.load_credentials({'OBS_WEBSOCKET_URL': 'ws://e:2'}, cfg), ('ws://e:2', None))
        self.assertEqual(obsws.load_credentials({}, self.tmp / 'missing.json'), ('ws://localhost:4455', None))


if __name__ == '__main__':
    unittest.main()
