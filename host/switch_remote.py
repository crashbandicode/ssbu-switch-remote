#!/usr/bin/python3
"""switch-remote host client: an edenctl-compatible client for the physical Switch.

`SwitchClient.request(method, params)` returns the same envelope as Eden's control socket
({"id", "ok", "result"} or {"id", "ok": False, "error": {"code", "message"}}), so the playbook
runner (tools/eden-runner/runner.py) drives the console with its existing rows, segments and gates:

  input.button / input.stick / input.reset   -> the sysmodule's virtual Pro Controller
  macro.play / macro.status / macro.stop     -> samples uploaded and played by the sysmodule at 60 Hz
  ui.screenshot / recording.*                -> the capture card (capd.py)
  ui.tree                                    -> one GRenderWindow, so runner.shot() finds a target
  status                                     -> sysmodule STATUS + capture status; running=False when
                                                the game is gone or the console unreachable
  jobs.get                                   -> done (nothing here is deferred)
  switch.launch / terminate / relaunch / dismiss / detach / list / file / fetch / raw
                                             -> sysmodule-only methods (DESIGN.md sections 3-4)

The socket string `switch://HOST:PORT` selects this client (playbook-patch/). The token comes from
SWITCH_REMOTE_TOKEN_FILE (default ~/.config/switch-remote/token) and is never printed or logged.
CLI: switch_remote.py --host H [--port P] ping|status|shot OUT|press BUTTON [MS]|stick X Y [MS]|
     play FILE|launch|terminate|relaunch|dismiss|list ROOT [DIR]|fetch ROOT PATH OUT|call METHOD [JSON]
"""
import argparse
import json
import os
import socket
import sys
import threading
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

DEFAULT_PORT = 47130
DEFAULT_TOKEN_FILE = Path.home() / '.config' / 'switch-remote' / 'token'
DEFAULT_CAPD = f'/run/user/{os.getuid()}/switch-remote-capd.sock'
STICK_MAX = 32767
# Eden's button names, in macro-bit order (Eden src/yuzu/macro_file.cpp:323-331).
BUTTONS = ['a', 'b', 'x', 'y', 'lstick', 'rstick', 'l', 'r', 'zl', 'zr', 'plus', 'minus', 'left', 'up',
           'right', 'down', 'sl_left', 'sr_left', 'home', 'capture', 'sl_right', 'sr_right']
ALIASES = {'dleft': 'left', 'dup': 'up', 'dright': 'right', 'ddown': 'down', 'start': 'plus', 'select': 'minus'}
EDEN_DEFAULT_LEASE_MS = 1000   # CLIControl.md: held input default lease
EDEN_DEFAULT_TAP_MS = 100      # CLIControl.md: taps default to 100 ms
MACRO_SAMPLES_MAX = 4096


class SwitchError(RuntimeError):
    def __init__(self, code, message=''):
        super().__init__(f'{code} {message}'.strip())
        self.code, self.message = code, message


def button_bit(name):
    name = ALIASES.get(name, name)
    if name not in BUTTONS:
        raise SwitchError('bad_arguments', f'unknown button {name!r}')
    bit = BUTTONS.index(name)
    if bit >= 16:
        raise SwitchError('refused', f'{name} is a system button')
    return bit


def stick_value(v):
    v = float(v)
    if v != v:  # NaN
        return 0
    return int(round(max(-1.0, min(1.0, v)) * STICK_MAX))


def macro_lines(doc):
    """MBEGIN/MEV lines for an .edenmacro.json document (format eden-input-macro, version 1)."""
    if doc.get('format') != 'eden-input-macro':
        raise SwitchError('bad_macro', 'not an eden-input-macro file')
    events = doc.get('events') or []
    duration = int(doc.get('duration_frames') or 0)
    if not events or not duration:
        raise SwitchError('bad_macro', 'no events or no duration')
    if len(events) > MACRO_SAMPLES_MAX:
        raise SwitchError('bad_macro', f'{len(events)} samples; the sysmodule holds {MACRO_SAMPLES_MAX}')
    lines = [f'MBEGIN {len(events)} {duration}']
    for e in events:
        sticks = [stick_value(v) for v in (e.get('sticks') or [0, 0, 0, 0])[:4]]
        lines.append(f'MEV {int(e["frame"])} {int(e.get("buttons", 0))} ' + ' '.join(map(str, sticks)))
    return lines


def parse_kv(text):
    out = {}
    for part in text.split():
        if '=' in part:
            k, v = part.split('=', 1)
            out[k] = v
    return out


def read_token(path=None):
    path = Path(path or os.environ.get('SWITCH_REMOTE_TOKEN_FILE') or DEFAULT_TOKEN_FILE)
    token = path.read_text().strip()
    if not 16 <= len(token) <= 64:
        raise SwitchError('bad_token', f'token in {path} must be 16-64 characters')
    return token


class Connection:
    """One authenticated line connection to the sysmodule."""

    def __init__(self, host, port, token, timeout=10.0):
        self.host, self.port, self.token, self.timeout = host, port, token, timeout
        self.sock = None
        self.buf = b''

    def open(self):
        self.close()
        self.sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        self.sock.settimeout(self.timeout)
        self.buf = b''
        self.send_line(f'AUTH {self.token}')
        reply = self.read_line()
        if not reply.startswith('OK'):
            self.close()
            raise SwitchError('auth', reply)

    def close(self):
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
        self.sock = None

    def send_line(self, line):
        self.sock.sendall(line.encode('ascii') + b'\n')

    def read_line(self):
        while b'\n' not in self.buf:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError('sysmodule closed the connection')
            self.buf += chunk
        line, self.buf = self.buf.split(b'\n', 1)
        return line.decode('ascii', 'replace')

    def read_bytes(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError('sysmodule closed the connection')
            self.buf += chunk
        data, self.buf = self.buf[:n], self.buf[n:]
        return data

    def call(self, line):
        """Sends one request; returns the reply line, raising SwitchError on ERR."""
        self.send_line(line)
        reply = self.read_line()
        if reply.startswith('ERR'):
            parts = reply.split(' ', 2)
            raise SwitchError(parts[1] if len(parts) > 1 else 'error', parts[2] if len(parts) > 2 else '')
        return reply


class SwitchClient:
    def __init__(self, host, port=DEFAULT_PORT, token=None, capd_socket=DEFAULT_CAPD, timeout=10.0,
                 screenshot_width=1280, actions_log=None):
        self.conn = Connection(host, port, token or read_token(), timeout)
        self.capd_socket = capd_socket
        self.screenshot_width = screenshot_width
        self.lock = threading.Lock()
        self.actions_log = actions_log
        self.recording = None

    # --- envelope ---------------------------------------------------------------------------------------------
    def request(self, method, params=None):
        rid = uuid.uuid4().hex
        params = params or {}
        try:
            result = self._dispatch(method, params)
            reply = {'id': rid, 'ok': True, 'result': result}
        except SwitchError as e:
            reply = {'id': rid, 'ok': False, 'error': {'code': e.code, 'message': e.message}}
        except (OSError, ConnectionError) as e:
            self.conn.close()
            reply = {'id': rid, 'ok': False, 'error': {'code': 'unreachable', 'message': str(e)}}
        if self.actions_log and (method.startswith('input.') or method.startswith('macro.')
                                 or method.startswith('switch.')):
            with open(self.actions_log, 'a') as f:
                f.write(json.dumps({'wall': time.time(), 'method': method, 'params': params,
                                    'ok': reply['ok']}) + '\n')
        return reply

    def _sys(self, line):
        with self.lock:
            if self.conn.sock is None:
                self.conn.open()
            return self.conn.call(line)

    def _capd(self, method, params=None):
        import capd
        try:
            reply = capd.call(self.capd_socket, method, params)
        except OSError as e:
            raise SwitchError('capture_unavailable', f'capd at {self.capd_socket}: {e}') from e
        if not reply.get('ok'):
            err = reply.get('error') or {}
            raise SwitchError(err.get('code', 'capture_failed'), err.get('message', ''))
        return reply['result']

    # --- methods ----------------------------------------------------------------------------------------------
    def _dispatch(self, method, p):
        if method == 'ping':
            return {'pong': self._sys('PING').split()[-1]}
        if method == 'status':
            return self.status()
        if method == 'input.button':
            bit = button_bit(p['button'])
            state = p.get('state', 'tap')
            if state == 'tap':
                ms = int(p.get('duration_ms', EDEN_DEFAULT_TAP_MS))
            elif state == 'press':
                ms = int(p.get('lease_ms', EDEN_DEFAULT_LEASE_MS))
            elif state == 'release':
                ms = 1
            else:
                raise SwitchError('bad_arguments', f'state {state!r}')
            self._sys(f'BTN {bit} {state} {max(1, min(10000, ms))}')
            return {'accepted': True}
        if method == 'input.stick':
            which = {'left': 'l', 'right': 'r'}.get(p.get('stick', 'left'))
            if not which:
                raise SwitchError('bad_arguments', f'stick {p.get("stick")!r}')
            ms = int(p.get('lease_ms', EDEN_DEFAULT_LEASE_MS))
            self._sys(f'STICK {which} {stick_value(p.get("x", 0))} {stick_value(p.get("y", 0))} '
                      f'{max(1, min(10000, ms))}')
            return {'accepted': True}
        if method == 'input.reset':
            self._sys('RESET')
            return {'accepted': True}
        if method == 'macro.play':
            if 'path' not in p:
                raise SwitchError('bad_arguments', 'macro.play needs a path (library names are an Eden feature)')
            return self.play_macro(json.loads(Path(p['path']).read_text()))
        if method == 'macro.status':
            kv = parse_kv(self._sys('MSTATUS'))
            state = {'ready': 'idle', 'loading': 'idle'}.get(kv.get('state'), kv.get('state'))
            return {'state': state, 'frame': int(kv.get('frame', 0)), 'duration_frames': int(kv.get('duration', 0)),
                    'error': kv.get('error') or ''}
        if method == 'macro.stop':
            self._sys('MSTOP')
            return {'accepted': True}
        if method == 'macro.record':
            raise SwitchError('unsupported', 'recording macros is done in Eden')
        if method == 'ui.tree':
            return [{'id': 'switch-capture', 'class': 'GRenderWindow', 'visible': True}]
        if method == 'ui.screenshot':
            return self._capd('ui.screenshot', {'path': p['path'], 'width': p.get('width', self.screenshot_width)})
        if method == 'recording.start':
            return self.recording_start(p)
        if method == 'recording.stop':
            return self.recording_stop()
        if method == 'recording.status':
            return self._capd('recording.status')
        if method == 'jobs.get':
            return {'state': 'done'}
        if method in ('switch.launch', 'switch.terminate', 'switch.relaunch', 'switch.dismiss', 'switch.detach'):
            reply = self._sys(method.split('.', 1)[1].upper())
            return {'reply': reply}
        if method == 'switch.records':
            return self.records()
        if method == 'switch.list':
            return self.list(p['root'], p.get('dir', ''))
        if method == 'switch.file':
            return self.file(p['root'], p['path'], int(p.get('offset', 0)), int(p.get('max', 65536)))
        if method == 'switch.fetch':
            return self.fetch(p['root'], p['path'], p['out'])
        raise SwitchError('unknown_method', method)

    def status(self):
        out = {'backend': 'switch'}
        try:
            kv = parse_kv(self._sys('STATUS'))
            out.update(kv)
            out['running'] = kv.get('app') == 'running'
            out['state'] = 'running' if out['running'] else 'stopped'
        except (OSError, ConnectionError, SwitchError) as e:
            self.conn.close()
            out.update({'running': False, 'state': 'unreachable', 'error': str(e)})
        try:
            cap = self._capd('status')
            out['capture'] = {k: cap.get(k) for k in ('signal', 'frames', 'last_frame_age_ms')}
        except SwitchError as e:
            out['capture'] = {'error': str(e)}
        return out

    def play_macro(self, doc):
        lines = macro_lines(doc)
        with self.lock:
            if self.conn.sock is None:
                self.conn.open()
            self.conn.call(lines[0])
            # MEV lines get no reply unless they fail; send them in one write, then check with MSTATUS.
            self.conn.sock.sendall(('\n'.join(lines[1:]) + '\nMSTATUS\n').encode('ascii'))
            errors = []
            while True:
                line = self.conn.read_line()
                if line.startswith('OK macro'):
                    break
                errors.append(line)
            kv = parse_kv(line)
            if errors or kv.get('state') != 'ready':
                raise SwitchError('bad_macro', (errors[0] if errors else line))
            self.conn.call('MPLAY')
        return {'accepted': True, 'samples': len(lines) - 1}

    def list(self, root, directory=''):
        with self.lock:
            if self.conn.sock is None:
                self.conn.open()
            head = self.conn.call(f'LIST {root} {directory}'.rstrip())
            n = int(head.split()[-1])
            entries = []
            for _ in range(n):
                kind, size, mtime, name = self.conn.read_line().split(' ', 3)
                if kind != '-' and name not in ('.', '..'):
                    entries.append({'type': 'dir' if kind == 'd' else 'file', 'size': int(size),
                                    'mtime': int(mtime), 'name': name})
        return entries

    def records(self):
        """What the sysmodule sees of the HOME order: ns records and its first-tile verdict (receipt for LAUNCH)."""
        with self.lock:
            if self.conn.sock is None:
                self.conn.open()
            line = self.conn.call('RECORDS')  # OK records <n> first_tile=<verdict>
            n, head = int(line.split()[2]), parse_kv(line)
            rows = []
            for _ in range(n):
                rid, event, updated = self.conn.read_line().split()
                rows.append({'id': rid, 'last_event': int(event), 'last_updated': int(updated)})
        return {'first_tile': head.get('first_tile'), 'records': rows}

    def file(self, root, path, offset=0, max_bytes=65536):
        with self.lock:
            if self.conn.sock is None:
                self.conn.open()
            head = self.conn.call(f'FILE {root} {offset} {max_bytes} {path}')
            total, n = map(int, head.split()[2:4])
            data = self.conn.read_bytes(n)
        return {'size': total, 'offset': offset, 'data': data}

    def fetch(self, root, path, out):
        out = Path(out)
        out.parent.mkdir(parents=True, exist_ok=True)
        offset, size = 0, None
        with open(str(out) + '.part', 'wb') as f:
            while size is None or offset < size:
                chunk = self.file(root, path, offset)
                size = chunk['size']
                if not chunk['data']:
                    break
                f.write(chunk['data'])
                offset += len(chunk['data'])
        os.replace(str(out) + '.part', out)
        return {'path': str(out), 'size': offset}

    # --- recordings: MKV from capd, GIF on stop (Eden's recording.* writes a GIF at `path`) ---------------------
    def recording_start(self, p):
        if self.recording:
            raise SwitchError('busy', 'a recording is running')
        target = Path(p['path'])
        mkv = target.with_suffix('.mkv')
        result = self._capd('recording.start', {'path': str(mkv), 'duration_ms': p.get('duration_ms')})
        self.recording = {'target': target, 'mkv': mkv, 'fps': int(p.get('fps', 10)),
                          'width': int(p.get('width', 640)), 'started_wall': result['started_wall']}
        return {'accepted': True, 'started_wall': result['started_wall']}

    def recording_stop(self):
        rec = self.recording
        if not rec:
            raise SwitchError('idle', 'no recording is running')
        self.recording = None
        stop = self._capd('recording.stop')
        import gif
        info = gif.encode(rec['mkv'], rec['target'], fps=rec['fps'], width=rec['width'])
        return {'path': str(rec['target']), 'frames': info['frames'], 'dropped_frames': stop['dropped_frames'],
                'source': str(rec['mkv']), 'first_wall': stop['first_wall'], 'state': 'finished'}


def make_client(socket_string, **kw):
    """`switch://HOST:PORT` -> SwitchClient (the runner's client factory calls this)."""
    if not socket_string.startswith('switch://'):
        raise ValueError(f'not a switch:// address: {socket_string}')
    hostport = socket_string[len('switch://'):].rstrip('/')
    host, _, port = hostport.rpartition(':') if ':' in hostport else (hostport, '', '')
    return SwitchClient(host or hostport, int(port) if port else DEFAULT_PORT, **kw)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--host', required=True)
    ap.add_argument('--port', type=int, default=DEFAULT_PORT)
    ap.add_argument('--token-file')
    ap.add_argument('--capd', default=DEFAULT_CAPD)
    ap.add_argument('cmd')
    ap.add_argument('args', nargs='*')
    a = ap.parse_args(argv)
    c = SwitchClient(a.host, a.port, read_token(a.token_file), a.capd)
    cmd, args = a.cmd, a.args
    table = {
        'ping': lambda: c.request('ping'),
        'status': lambda: c.request('status'),
        'shot': lambda: c.request('ui.screenshot', {'path': os.path.abspath(args[0])}),
        'press': lambda: c.request('input.button', {'button': args[0], 'state': 'tap',
                                                    'duration_ms': int(args[1]) if len(args) > 1 else 100}),
        'stick': lambda: c.request('input.stick', {'stick': 'left', 'x': float(args[0]), 'y': float(args[1]),
                                                   'lease_ms': int(args[2]) if len(args) > 2 else 250}),
        'play': lambda: c.request('macro.play', {'path': os.path.abspath(args[0])}),
        'launch': lambda: c.request('switch.launch'),
        'terminate': lambda: c.request('switch.terminate'),
        'relaunch': lambda: c.request('switch.relaunch'),
        'dismiss': lambda: c.request('switch.dismiss'),
        'list': lambda: c.request('switch.list', {'root': args[0], 'dir': args[1] if len(args) > 1 else ''}),
        'fetch': lambda: c.request('switch.fetch', {'root': args[0], 'path': args[1], 'out': args[2]}),
        'call': lambda: c.request(args[0], json.loads(args[1]) if len(args) > 1 else {}),
    }
    if cmd not in table:
        ap.error(f'unknown command {cmd}')
    reply = table[cmd]()
    print(json.dumps(reply, default=lambda o: f'<{len(o)} bytes>' if isinstance(o, bytes) else str(o)))
    return 0 if reply.get('ok') else 1


if __name__ == '__main__':
    sys.exit(main())
