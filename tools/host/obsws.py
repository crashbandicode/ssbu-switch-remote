#!/usr/bin/python3
"""OBS as the capture backend (obs-websocket protocol v5), standard library only.

When OBS runs with the capture card as a source, OBS holds the V4L2 device (a UVC device normally serves
one opener), so screenshots and clips come from OBS: GetSourceScreenshot for stills, StartRecord/StopRecord
for clips, SaveReplayBuffer for "the last N seconds". capd.py (GStreamer) is the fallback when OBS is not
running. `pick_backend()` chooses at run time.

Credentials: OBS_WEBSOCKET_URL / OBS_WEBSOCKET_PASSWORD from the environment, else from the obs-mcp entry
in ~/.claude.json (mcpServers.obs-mcp.env). The password is never printed, logged or put in an error.

  obsws.py status | shot OUT.png [--width W] | record-start | record-stop | replay-save | inputs
"""
import base64
import hashlib
import json
import os
import socket
import struct
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

CLAUDE_JSON = Path.home() / '.claude.json'
V4L2_KINDS = ('v4l2_input', 'v4l2_input_source')  # OBS's Linux "Video Capture Device" input kinds


class ObsError(RuntimeError):
    def __init__(self, code, message=''):
        super().__init__(f'{code} {message}'.strip())
        self.code, self.message = code, message


def load_credentials(env=None, claude_json=CLAUDE_JSON):
    """(url, password or None). Never print the result."""
    env = os.environ if env is None else env
    url, password = env.get('OBS_WEBSOCKET_URL'), env.get('OBS_WEBSOCKET_PASSWORD')
    if not url:
        try:
            cfg = json.loads(Path(claude_json).read_text())
            obs_env = cfg.get('mcpServers', {}).get('obs-mcp', {}).get('env', {})
            url, password = obs_env.get('OBS_WEBSOCKET_URL'), password or obs_env.get('OBS_WEBSOCKET_PASSWORD')
        except (OSError, ValueError):
            pass
    return url or 'ws://localhost:4455', password


def auth_string(password, salt, challenge):
    """obs-websocket v5: base64(sha256(base64(sha256(password + salt)) + challenge))."""
    secret = base64.b64encode(hashlib.sha256((password + salt).encode()).digest()).decode()
    return base64.b64encode(hashlib.sha256((secret + challenge).encode()).digest()).decode()


class WebSocket:
    """A minimal RFC 6455 client: text frames, masking, ping/pong, close."""

    def __init__(self, url, timeout=3.0):
        u = urlparse(url)
        if u.scheme != 'ws':
            raise ObsError('unsupported_url', 'only ws:// is supported')
        self.sock = socket.create_connection((u.hostname, u.port or 80), timeout=timeout)
        self.sock.settimeout(timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        req = (f'GET {u.path or "/"} HTTP/1.1\r\nHost: {u.hostname}:{u.port or 80}\r\nUpgrade: websocket\r\n'
               f'Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n'
               'Sec-WebSocket-Protocol: obswebsocket.json\r\n\r\n')
        self.sock.sendall(req.encode())
        head = b''
        while b'\r\n\r\n' not in head:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ObsError('handshake_failed', 'connection closed')
            head += chunk
        head, self.buf = head.split(b'\r\n\r\n', 1)
        lines = head.decode('latin1').split('\r\n')
        if ' 101 ' not in lines[0] + ' ':
            raise ObsError('handshake_failed', lines[0])
        accept = base64.b64encode(hashlib.sha1((key + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
        headers = {k.strip().lower(): v.strip() for k, _, v in (l.partition(':') for l in lines[1:])}
        if headers.get('sec-websocket-accept') != accept:
            raise ObsError('handshake_failed', 'bad Sec-WebSocket-Accept')

    def _read(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ObsError('closed', 'OBS closed the connection')
            self.buf += chunk
        data, self.buf = self.buf[:n], self.buf[n:]
        return data

    def send_text(self, text):
        self._send(0x1, text.encode())

    def _send(self, opcode, payload):
        head = bytes([0x80 | opcode])
        n = len(payload)
        if n < 126:
            head += bytes([0x80 | n])
        elif n < 65536:
            head += bytes([0x80 | 126]) + struct.pack('>H', n)
        else:
            head += bytes([0x80 | 127]) + struct.pack('>Q', n)
        mask = os.urandom(4)
        self.sock.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))

    def recv_text(self):
        message = b''
        while True:
            b0, b1 = self._read(2)
            opcode, n = b0 & 0x0f, b1 & 0x7f
            if n == 126:
                n = struct.unpack('>H', self._read(2))[0]
            elif n == 127:
                n = struct.unpack('>Q', self._read(8))[0]
            mask = self._read(4) if b1 & 0x80 else None
            payload = self._read(n)
            if mask:
                payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
            if opcode == 0x9:  # ping
                self._send(0xA, payload)
                continue
            if opcode == 0x8:
                raise ObsError('closed', f'close frame {payload[:2].hex()}')
            if opcode in (0x0, 0x1, 0x2):
                message += payload
                if b0 & 0x80:
                    return message.decode()

    def close(self):
        try:
            self._send(0x8, b'')
            self.sock.close()
        except OSError:
            pass


class ObsClient:
    def __init__(self, url=None, password=None, timeout=3.0):
        if url is None:
            url, password = load_credentials()
        self.ws = WebSocket(url, timeout)
        hello = json.loads(self.ws.recv_text())
        if hello.get('op') != 0:
            raise ObsError('protocol', 'no Hello')
        identify = {'rpcVersion': 1}
        auth = hello['d'].get('authentication')
        if auth:
            if not password:
                raise ObsError('auth', 'OBS wants a password and none is configured')
            identify['authentication'] = auth_string(password, auth['salt'], auth['challenge'])
        self.ws.send_text(json.dumps({'op': 1, 'd': identify}))
        try:
            reply = json.loads(self.ws.recv_text())
        except ObsError as e:
            raise ObsError('auth', 'OBS closed the connection at Identify (wrong password?)') from e
        if reply.get('op') != 2:
            raise ObsError('auth', 'not identified')

    def request(self, request_type, data=None):
        rid = uuid.uuid4().hex
        self.ws.send_text(json.dumps({'op': 6, 'd': {'requestType': request_type, 'requestId': rid,
                                                     'requestData': data or {}}}))
        while True:
            msg = json.loads(self.ws.recv_text())
            if msg.get('op') == 7 and msg['d'].get('requestId') == rid:
                status = msg['d'].get('requestStatus', {})
                if not status.get('result'):
                    raise ObsError(f'obs_{status.get("code")}', status.get('comment', request_type))
                return msg['d'].get('responseData') or {}

    def close(self):
        self.ws.close()


class ObsCapture:
    """capd's capture methods over OBS."""

    def __init__(self, client, source=None, device='/dev/video4'):
        self.client = client
        self.source = source or self.find_source(device)
        self.recording_started = None

    def find_source(self, device):
        inputs = self.client.request('GetInputList').get('inputs', [])
        v4l2 = [i for i in inputs if i.get('inputKind') in V4L2_KINDS]
        for i in v4l2:
            settings = self.client.request('GetInputSettings', {'inputName': i['inputName']}).get('inputSettings', {})
            if settings.get('device_id') == device:
                return i['inputName']
        raise ObsError('no_source', f'no OBS video capture input uses {device} '
                                    f'({len(v4l2)} V4L2 inputs, {len(inputs)} inputs)')

    def handle(self, method, params):
        if method == 'status':
            rec = self.client.request('GetRecordStatus')
            return {'backend': 'obs', 'source': self.source, 'signal': None, 'recording': rec}
        if method == 'ui.screenshot':
            data = {'sourceName': self.source, 'imageFormat': 'png'}
            if params.get('width'):
                data['imageWidth'] = int(params['width'])
            image = self.client.request('GetSourceScreenshot', data)['imageData']
            raw = base64.b64decode(image.split(',', 1)[1])
            path = Path(params['path'])
            tmp = Path(str(path) + '.part')
            tmp.write_bytes(raw)
            os.replace(tmp, path)
            w, h = struct.unpack('>II', raw[16:24]) if raw[:8] == b'\x89PNG\r\n\x1a\n' else (None, None)
            return {'path': str(path), 'width': w, 'height': h, 'backend': 'obs', 'frame_time': time.time()}
        if method == 'recording.start':
            self.client.request('StartRecord')
            self.recording_started = time.time()
            return {'accepted': True, 'started_wall': self.recording_started, 'approximate_start': True}
        if method == 'recording.stop':
            out = self.client.request('StopRecord')
            started, self.recording_started = self.recording_started, None
            return {'path': out.get('outputPath'), 'frames': None, 'dropped_frames': None,
                    'first_wall': started, 'approximate_start': True}
        if method == 'recording.status':
            return self.client.request('GetRecordStatus')
        if method == 'replay.save':
            saved = time.time()
            self.client.request('SaveReplayBuffer')
            path = None
            for _ in range(50):  # OBS writes the file asynchronously
                path = self.client.request('GetLastReplayBufferReplay').get('savedReplayPath')
                if path and Path(path).exists():
                    break
                time.sleep(0.1)
            return {'path': path, 'saved_wall': saved}
        raise KeyError(method)


def pick_backend(device='/dev/video4', obs_factory=None):
    """('obs', ObsCapture) when OBS answers and has the card as a source, else ('capd', reason)."""
    try:
        client = (obs_factory or ObsClient)()
    except (OSError, ObsError) as e:
        return 'capd', f'OBS unavailable: {type(e).__name__}: {getattr(e, "code", "")}'.strip()
    try:
        return 'obs', ObsCapture(client, device=device)
    except ObsError as e:
        client.close()
        return 'capd', f'OBS has no source for {device}: {e.code}'


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('cmd', choices=['status', 'shot', 'record-start', 'record-stop', 'replay-save', 'inputs'])
    ap.add_argument('out', nargs='?')
    ap.add_argument('--width', type=int)
    ap.add_argument('--device', default='/dev/video4')
    a = ap.parse_args(argv)
    client = ObsClient()
    try:
        if a.cmd == 'inputs':
            print(json.dumps(client.request('GetInputList')))
            return 0
        cap = ObsCapture(client, device=a.device)
        method = {'status': 'status', 'shot': 'ui.screenshot', 'record-start': 'recording.start',
                  'record-stop': 'recording.stop', 'replay-save': 'replay.save'}[a.cmd]
        print(json.dumps(cap.handle(method, {'path': a.out and os.path.abspath(a.out), 'width': a.width})))
    finally:
        client.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
