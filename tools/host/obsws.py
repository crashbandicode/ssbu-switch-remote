#!/usr/bin/python3
"""OBS as the capture backend (obs-websocket protocol v5), standard library only.

When OBS runs with the capture card as a source, OBS holds the V4L2 device (a UVC device normally serves
one opener), so screenshots and clips come from OBS: GetSourceScreenshot for stills, StartRecord/StopRecord
for clips, SaveReplayBuffer for "the last N seconds". capd.py (GStreamer) is the fallback when OBS is not
running. `pick_backend()` chooses at run time.

Credentials: OBS_WEBSOCKET_URL / OBS_WEBSOCKET_PASSWORD from the environment, else from the obs-mcp entry
in ~/.claude.json (mcpServers.obs-mcp.env). The password is never printed, logged or put in an error.

  obsws.py status | shot OUT.png [--width W] | record-start | record-stop | replay-save | inputs
  obsws.py split-check | split-enable        (per-row files: is manual file splitting on? / turn it on, once)

Per-row files (recording.session_split): one OBS recording, split into a new file at each row start with
SplitRecordFile; the new file is reported by the RecordFileChanged event (OBS splits at the next video keyframe, so
the event, not the request, says when the new file began). Splitting needs the record output's own "split_file"
setting: Simple output mode never sets it, so `split-enable` sets it (with the directory/format/extension the
output needs to name the next file) through SetOutputSettings. That lasts until OBS rebuilds its outputs (a restart,
or Apply in Settings > Output); `split-check` reports it.
"""
import base64
import collections
import hashlib
import json
import os
import select
import socket
import struct
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

CLAUDE_JSON = Path.home() / '.claude.json'
V4L2_KINDS = ('v4l2_input', 'v4l2_input_source')  # OBS's Linux "Video Capture Device" input kinds
EVENTS_OUTPUTS = 1 << 6  # obs-websocket v5 EventSubscription::Outputs (RecordStateChanged, RecordFileChanged)
STARTED, STOPPED = 'OBS_WEBSOCKET_OUTPUT_STARTED', 'OBS_WEBSOCKET_OUTPUT_STOPPED'
FILE_OUTPUTS = ('simple_file_output', 'adv_file_output')
RECORD_EXT = {'hybrid_mp4': 'mp4', 'hybrid_mov': 'mov', 'fragmented_mp4': 'mp4', 'fragmented_mov': 'mov',
              'mpegts': 'ts', 'm3u8': 'm3u8'}


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
        # Outputs events only: RecordStateChanged (started/stopped + outputPath) and RecordFileChanged (a split).
        identify = {'rpcVersion': 1, 'eventSubscriptions': EVENTS_OUTPUTS}
        self.events = collections.deque(maxlen=512)
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
            msg = self._receive()
            if msg.get('op') == 7 and msg['d'].get('requestId') == rid:
                status = msg['d'].get('requestStatus', {})
                if not status.get('result'):
                    raise ObsError(f'obs_{status.get("code")}', status.get('comment', request_type))
                return msg['d'].get('responseData') or {}

    def _receive(self):
        """One message; an event (op 5) is also kept, in order, for take_events/wait_event."""
        msg = json.loads(self.ws.recv_text())
        if msg.get('op') == 5:
            d = msg.get('d') or {}
            self.events.append({'type': d.get('eventType'), 'data': d.get('eventData') or {}, 'wall': time.time()})
        return msg

    def _readable(self, timeout):
        return bool(self.ws.buf) or bool(select.select([self.ws.sock], [], [], max(0.0, timeout))[0])

    def pump(self, timeout=0.0):
        """Read whatever arrives within timeout (events are kept)."""
        until = time.monotonic() + timeout
        while self._readable(until - time.monotonic()):
            self._receive()

    def take_events(self, event_type, pred=None):
        """Remove and return the kept events of a type (oldest first)."""
        taken, kept = [], collections.deque(maxlen=self.events.maxlen)
        for event in self.events:
            (taken if event['type'] == event_type and (pred is None or pred(event['data'])) else kept).append(event)
        self.events = kept
        return taken

    def wait_event(self, event_type, timeout, pred=None):
        """The first kept or arriving event of a type (removed from the buffer), or None after timeout."""
        until = time.monotonic() + timeout
        while True:
            found = self.take_events(event_type, pred)
            if found:
                for extra in found[1:]:  # later ones stay for the next caller
                    self.events.append(extra)
                return found[0]
            left = until - time.monotonic()
            if left <= 0:
                return None
            if self._readable(min(left, 0.25)):
                self._receive()

    def close(self):
        self.ws.close()


class ObsCapture:
    """capd's capture methods over OBS."""

    def __init__(self, client, source=None, device='/dev/video4'):
        self.client = client
        self.source = source or self.find_source(device)
        self.recording_started = None
        self.current_file = None  # the file the active recording writes now (from RecordStateChanged / RecordFileChanged)

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
            self.recording_started = self.start_confirmed(params.get('idle_cap', 20.0))
            return {'accepted': True, 'started_wall': self.recording_started, 'approximate_start': True}
        if method == 'recording.stop':
            path, finalized, _ = self.stop_confirmed()
            started, self.recording_started = self.recording_started, None
            return {'path': path, 'frames': None, 'dropped_frames': None,
                    'first_wall': started, 'approximate_start': True, 'finalized': finalized}
        if method == 'recording.status':
            return self.client.request('GetRecordStatus')
        if method == 'recording.split_check':
            return split_check(self.client)
        if method.startswith('recording.session_'):
            return self.session(method.split('_', 1)[1], params)
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

    def session(self, op, params):
        """One continuous recording for a whole hardware session (the runbook cuts clips and stills from it offline),
        with no clip encoding: start / mark / stop.
          start -> {started_wall, already_active}       StartRecord unless a recording is already running (a rerun)
          mark  -> {active, rec_ms, timecode, wall, frame, chapter_ok}
                   rec_ms is GetRecordStatus.outputDuration (the recording's own clock), frame = rec_ms at 60 fps;
                   chapter=true also adds a hybrid-MP4 chapter named label (CreateRecordChapter, OBS 30.2+); a
                   refused chapter is reported, never raised
          split -> mark's fields + {split_ok, new_path, file_rec_ms, file_wall, closed: [{path, finalized}],
                   late_paths, error?}: SplitRecordFile, then wait (cap_s, default 10) for RecordFileChanged; the new
                   file begins at OBS's next keyframe, which the event reports, so file_rec_ms (rec_ms read right
                   after the event) is that file's 0. closed = the file the split ended, once its size held.
                   No event within the cap: split_ok false, error split_unacknowledged (never a Start/Stop instead).
                   late_paths: RecordFileChanged events that arrived outside a split (a late acknowledgement).
          ensure -> {active_before, restarted, path, started_wall?}: at a row start, StartRecord (confirmed active)
                   when OBS is not recording; raises when it can't start
          stop  -> {path, stopped_wall, finalized, was_active, late_paths}"""
        now = time.time()
        status = self.client.request('GetRecordStatus')
        active = bool(status.get('outputActive'))
        if op == 'start':
            return {'started_wall': self.start_confirmed(params.get('idle_cap', 20.0)), 'already_active': False,
                    'path': self.current_file}
        if op == 'ensure':
            # Before a row's first input: never run a row unrecorded. OBS idle (stopped by hand, a crash, a lost
            # output) -> StartRecord and return only once it IS recording (start_confirmed); an ObsError otherwise.
            if active:
                return {'active_before': True, 'restarted': False, 'path': self.current_file}
            started = self.start_confirmed(params.get('idle_cap', 20.0), params.get('cap_s', 5.0))
            return {'active_before': False, 'restarted': True, 'started_wall': started, 'path': self.current_file,
                    'rec_ms': 0}
        if op in ('split', 'stop'):
            self.client.pump(0.05)  # an event in flight now is a late one, not this split's
            late = self._late_file_changes()
        if op == 'split':
            return self.split(status, now, params, late)
        if op == 'mark':
            ms = status.get('outputDuration')
            out = {'active': active, 'rec_ms': ms if active else None, 'timecode': status.get('outputTimecode'),
                   'wall': now, 'frame': round(ms * 60 / 1000) if active and isinstance(ms, (int, float)) else None,
                   'chapter_ok': None}
            if active and params.get('chapter') and params.get('label'):
                try:
                    self.client.request('CreateRecordChapter', {'chapterName': str(params['label'])[:120]})
                    out['chapter_ok'] = True
                except ObsError as e:
                    out['chapter_ok'] = False
                    out['chapter_error'] = e.code
            return out
        if op == 'stop':
            if not active:
                return {'path': None, 'stopped_wall': now, 'finalized': False, 'was_active': False, 'late_paths': late}
            path, finalized, was_active = self.stop_confirmed(cap=30.0)
            return {'path': path, 'stopped_wall': time.time(), 'finalized': finalized, 'was_active': was_active,
                    'late_paths': late}
        raise KeyError(f'recording.session_{op}')

    def _late_file_changes(self):
        """RecordFileChanged events nobody waited for (a split acknowledged after its cap): follow them."""
        paths = [e['data'].get('newOutputPath') for e in self.client.take_events('RecordFileChanged')]
        if paths:
            self.current_file = paths[-1]
        return paths

    def split(self, status, now, params, late):
        active = bool(status.get('outputActive'))
        ms = status.get('outputDuration') if active else None
        out = {'active': active, 'rec_ms': ms, 'timecode': status.get('outputTimecode'), 'wall': now,
               'frame': round(ms * 60 / 1000) if isinstance(ms, (int, float)) else None, 'chapter_ok': None,
               'split_ok': False, 'new_path': None, 'file_rec_ms': None, 'file_wall': None,
               'prev_path': self.current_file, 'closed': [], 'late_paths': late}
        if not active:
            out['error'] = 'not_recording'
            return out
        try:
            self.client.request('SplitRecordFile')
        except ObsError as e:
            out.update(error=e.code, error_message=e.message[:200])
            return out
        event = self.client.wait_event('RecordFileChanged', float(params.get('cap_s', 10.0)))
        if event is None:
            out['error'] = 'split_unacknowledged'
            return out
        prev, self.current_file = self.current_file, event['data'].get('newOutputPath')
        after = self.client.request('GetRecordStatus')
        fms = after.get('outputDuration')
        out.update(split_ok=True, new_path=self.current_file, file_wall=event['wall'], file_rec_ms=fms,
                   prev_path=prev)
        if prev:
            out['closed'].append({'path': prev, 'finalized': self.wait_stable(prev, cap=5.0, stable_s=0.5)})
        if params.get('chapter') and params.get('label'):
            try:
                self.client.request('CreateRecordChapter', {'chapterName': str(params['label'])[:120]})
                out['chapter_ok'] = True
            except ObsError as e:
                out.update(chapter_ok=False, chapter_error=e.code)
        return out

    @staticmethod
    def wait_stable(path, cap=5.0, stable_s=0.5, poll=0.1):
        """True once the file exists and its size has held for stable_s (a split's old file is finished by the
        muxer as the new one opens); False at the cap."""
        until = time.monotonic() + cap
        last, since = None, None
        while time.monotonic() < until:
            p = Path(path)
            size = p.stat().st_size if p.is_file() else None
            if size is None or size != last:
                last, since = size, time.monotonic()
            elif time.monotonic() - since >= stable_s:
                return True
            time.sleep(poll)
        return False

    def status_active(self):
        return bool(self.client.request('GetRecordStatus').get('outputActive'))

    def start_confirmed(self, idle_cap=20.0, active_cap=5.0):
        """StartRecord only on an idle OBS, and return only once OBS says it IS recording (the wall time then).
        StartRecord answers before the output is active (hw-run-2b: a 0.45 s clip's stop got 501 "not recording",
        the start went on as a stray recording, and the next start got 500). So:
          - refuse (ObsError busy) while an output stays active or the newest file still grows past idle_cap;
          - after StartRecord, poll GetRecordStatus until outputActive (cap active_cap);
          - on a refusal (500/501) reconcile with GetRecordStatus: active = started; else once more after 2 s."""
        if not self.wait_idle(idle_cap):
            if self.status_active():
                raise ObsError('busy', 'an OBS recording output is still active; not starting another')
            raise ObsError('busy', 'the last OBS output file is still growing; not starting another')
        self.client.pump(0)
        self.client.take_events('RecordStateChanged')  # stale state changes must not name this recording's file
        self.client.take_events('RecordFileChanged')
        self.current_file = None
        for attempt in (1, 2):
            try:
                self.client.request('StartRecord')
            except ObsError:
                if self.status_active():
                    return self._until_active(active_cap)
                if attempt == 2:
                    raise
                time.sleep(2.0)
                self.wait_idle(idle_cap)
                continue
            return self._until_active(active_cap)

    def _until_active(self, cap):
        """Wait until OBS records: outputActive, then its RecordStateChanged STARTED event (which names the file;
        up to 2 s more, as the event may trail the status). Returns the wall time it was seen active."""
        until = time.monotonic() + cap
        while time.monotonic() < until:
            if self.status_active():
                seen = time.time()
                started = self.client.wait_event('RecordStateChanged', 2.0,
                                                 lambda d: d.get('outputState') == STARTED)
                if started:
                    self.current_file = started['data'].get('outputPath') or self.current_file
                return seen
            time.sleep(0.1)
        raise ObsError('not_recording', f'StartRecord answered but OBS was not recording after {cap} s')

    def stop_confirmed(self, cap=15.0):
        """(path, finalized, was_active): StopRecord, then wait for RecordStateChanged STOPPED (its outputPath is the
        file that ended; StopRecord's own answer comes before OBS has stopped), then until outputActive is false and
        the file's size has held for 1 s. A refused stop (501 "not recording" or 500) is reconciled with
        GetRecordStatus: inactive = nothing to stop; still active = one more StopRecord after 0.5 s."""
        path = None
        self.client.pump(0)
        self.client.take_events('RecordStateChanged', lambda d: d.get('outputState') == STOPPED)
        for attempt in (1, 2):
            try:
                path = self.client.request('StopRecord').get('outputPath')
                break
            except ObsError:
                if not self.status_active():
                    return None, False, False
                if attempt == 2:
                    raise
                time.sleep(0.5)
        stopped = self.client.wait_event('RecordStateChanged', cap, lambda d: d.get('outputState') == STOPPED)
        if stopped and stopped['data'].get('outputPath'):
            path = stopped['data']['outputPath']
        # After a split, OBS 32.2 still names the FIRST file in StopRecord and in the STOPPED event (live,
        # 2026-11-16): the file that ended is the one the last RecordFileChanged named.
        self.client.pump(0.05)
        self._late_file_changes()
        if self.current_file:
            path = self.current_file
        self.current_file = None
        return path, self.wait_finalized(path, cap=cap), True

    def newest_output(self):
        """The newest file in OBS's record directory (the output a start must not race), or None."""
        try:
            folder = Path(self.client.request('GetRecordDirectory').get('recordDirectory') or '')
        except ObsError:
            return None
        try:
            files = [p for p in folder.iterdir() if p.suffix.lower() in ('.mp4', '.mkv', '.mov', '.flv')]
        except OSError:
            return None
        return max(files, key=lambda p: p.stat().st_mtime) if files else None

    def wait_idle(self, cap=20.0, poll=0.25, stable_s=1.0):
        """True once no record output is active and the newest output file's size has held for stable_s."""
        until = time.monotonic() + cap
        held_since, last = None, None
        while time.monotonic() < until:
            if self.client.request('GetRecordStatus').get('outputActive'):
                held_since, last = None, None
            else:
                newest = self.newest_output()
                size = newest.stat().st_size if newest and newest.is_file() else None
                if size is None:
                    return True
                if size != last or held_since is None:
                    held_since, last = time.monotonic(), size
                elif time.monotonic() - held_since >= stable_s:
                    return True
            time.sleep(poll)
        return False

    def wait_finalized(self, path, cap=15.0, poll=0.2, stable_s=1.0):
        """StopRecord answers before OBS has finished the file (an MP4 writes its index last), so a clip read at
        once can decode no frames at all (hw-run-2, 2026-11-16: every clip under ~3 s, "no frames decoded").
        Wait until the record output is no longer active and the file's size has held for stable_s. False at the
        cap (the caller still tries the file)."""
        until = time.monotonic() + cap
        last, held_since = None, None
        while time.monotonic() < until:
            active = self.client.request('GetRecordStatus').get('outputActive')
            size = Path(path).stat().st_size if path and Path(path).is_file() else None
            if active or size is None:
                last, held_since = None, None
            elif size != last:
                last, held_since = size, time.monotonic()
            elif time.monotonic() - held_since >= stable_s:
                return True
            time.sleep(poll)
        return False


def record_output(client):
    """(name, kind) of the UI's record output, or (None, None)."""
    for o in client.request('GetOutputList').get('outputs', []):
        if o.get('outputName') in FILE_OUTPUTS:
            return o['outputName'], o.get('outputKind')
    return None, None


def split_check(client):
    """Can this OBS split the session recording per row? Read-only.
    {ok, why, obs_version, websocket_version, output, output_kind, split_file, directory, format, extension}"""
    version = client.request('GetVersion')
    name, kind = record_output(client)
    settings = client.request('GetOutputSettings', {'outputName': name}).get('outputSettings', {}) if name else {}
    def param(category, key):
        return client.request('GetProfileParameter', {'parameterCategory': category,
                                                      'parameterName': key}).get('parameterValue')
    mode = param('Output', 'Mode') or 'Simple'
    out = {'obs_version': version.get('obsVersion'), 'websocket_version': version.get('obsWebSocketVersion'),
           'mode': mode, 'output': name, 'output_kind': kind, 'split_file': settings.get('split_file'),
           'directory': settings.get('directory'), 'format': settings.get('format'),
           'extension': settings.get('extension')}
    if mode == 'Advanced':
        # Advanced mode: Settings > Output > Recording > Automatic File Splitting (profile AdvOut.RecSplitFile*);
        # OBS writes the output's split_file/directory only at StartRecord, so the profile is what to check.
        out.update(rec_split_file=param('AdvOut', 'RecSplitFile'), rec_split_type=param('AdvOut', 'RecSplitFileType'),
                   rec_type=param('AdvOut', 'RecType'), rec_format=param('AdvOut', 'RecFormat2'))
    if 'SplitRecordFile' not in version.get('availableRequests', []):
        why = 'this obs-websocket has no SplitRecordFile (OBS 30+ needed)'
    elif not name:
        why = f'no record output named {" or ".join(FILE_OUTPUTS)}'
    elif mode == 'Advanced':
        why = None
        if str(out['rec_split_file']).lower() != 'true':
            why = ('Advanced output: Recording > Automatic File Splitting is off (AdvOut.RecSplitFile='
                   f'{out["rec_split_file"]!r}); set it on, "Only split manually"')
        elif out['rec_type'] not in (None, 'Standard'):
            why = f'Advanced output: recording type {out["rec_type"]!r} (Custom Output) cannot split'
        elif out['rec_split_type'] != 'Manual':
            out['warning'] = f'AdvOut.RecSplitFileType is {out["rec_split_type"]!r}: OBS also splits on its own'
    elif settings.get('split_file') is not True:
        why = (f'{name}.split_file is {settings.get("split_file")!r}: manual file splitting is off '
               '(run `obsws.py split-enable` once; it resets when OBS restarts or Settings > Output is applied)')
    elif not settings.get('directory'):
        why = f'{name}.directory is empty: a split file would be named under / (run `obsws.py split-enable`)'
    else:
        why = None
    out.update(ok=why is None, why=why)
    return out


def split_enable(client):
    """Turn on manual file splitting on the UI's record output, the minimum OBS needs (SetOutputSettings; Simple
    output mode has no UI for it): split_file plus the directory/format/extension/allow_spaces the output uses to name
    a split file, taken from the profile. No size or time limit (max_time_sec/max_size_mb stay 0). Refused while
    recording: OBS reads split_file when a recording starts. Returns {before, after, set}."""
    if client.request('GetRecordStatus').get('outputActive'):
        raise ObsError('busy', 'OBS is recording; split settings apply from the next StartRecord')
    name, _kind = record_output(client)
    if not name:
        raise ObsError('no_output', f'no record output named {" or ".join(FILE_OUTPUTS)}')
    before = client.request('GetOutputSettings', {'outputName': name}).get('outputSettings', {})

    def param(category, key):
        return client.request('GetProfileParameter', {'parameterCategory': category,
                                                      'parameterName': key}).get('parameterValue')
    mode = param('Output', 'Mode') or 'Simple'
    section = 'AdvOut' if mode == 'Advanced' else 'SimpleOutput'
    fmt = param(section, 'RecFormat2') or 'hybrid_mp4'
    no_space = str(param(section, 'FileNameWithoutSpace') or param('Output', 'FileNameWithoutSpace') or '')
    values = {'split_file': True,
              'directory': client.request('GetRecordDirectory').get('recordDirectory'),
              'format': param('Output', 'FilenameFormatting') or '%CCYY-%MM-%DD %hh-%mm-%ss',
              'extension': RECORD_EXT.get(fmt, fmt),
              'allow_spaces': no_space.lower() not in ('true', '1')}
    if not values['directory']:
        raise ObsError('no_directory', 'GetRecordDirectory returned no directory')
    client.request('SetOutputSettings', {'outputName': name, 'outputSettings': values})
    after = client.request('GetOutputSettings', {'outputName': name}).get('outputSettings', {})
    return {'output': name, 'mode': mode, 'before': before, 'set': values, 'after': after}


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
    ap.add_argument('cmd', choices=['status', 'shot', 'record-start', 'record-stop', 'replay-save', 'inputs',
                                    'split-check', 'split-enable'])
    ap.add_argument('out', nargs='?')
    ap.add_argument('--width', type=int)
    ap.add_argument('--device', default='/dev/video4')
    a = ap.parse_args(argv)
    client = ObsClient()
    try:
        if a.cmd == 'inputs':
            print(json.dumps(client.request('GetInputList')))
            return 0
        if a.cmd in ('split-check', 'split-enable'):
            result = split_check(client) if a.cmd == 'split-check' else split_enable(client)
            print(json.dumps(result, indent=1))
            return 0 if result.get('ok', True) else 1
        cap = ObsCapture(client, device=a.device)
        method = {'status': 'status', 'shot': 'ui.screenshot', 'record-start': 'recording.start',
                  'record-stop': 'recording.stop', 'replay-save': 'replay.save'}[a.cmd]
        print(json.dumps(cap.handle(method, {'path': a.out and os.path.abspath(a.out), 'width': a.width})))
    finally:
        client.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
