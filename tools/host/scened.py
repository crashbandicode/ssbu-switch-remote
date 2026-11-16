#!/usr/bin/python3
"""Host scene receiver and the fail-closed online gate (option d, primary ruling 2026-11-16).

The in-game NRO (config `scene_host=<this host's LAN IP>`) connects to TCP <port>+1 (default 47131), sends
`AUTH <token>`, then `SCENE <seq> 0x<mode> <deluxe -1|0|1> <guards 0|1>` every 100 ms. No replies are sent, and the
connection is closed at the first bad line (the sysmodule's scene channel, sysmodule/core/service.cpp
Service::handle). The latest report and its arrival time are kept; `gate()` gives the same verdict as the sysmodule's
core (sysmodule/core/core.cpp `gate`):

  no_scene_report | scene_report_stale (> 500 ms) | nro_guards_failed | online_mode (0x07xxxxxx family, NSO)
  | online_deluxe (Deluxe ONLINE bit and the mode word is not local wireless 0x0201xxxx) | allowed

Clients (autopilot.py) ask over a unix socket: one JSON line `{"method": "status"}` -> one JSON line. A client that
cannot reach this daemon treats the gate as closed (`no_scene_receiver`).

  scened.py serve [--port 47131] [--bind 0.0.0.0] [--socket /run/user/UID/switch-remote-scene.sock]
  scened.py status

Only private IPv4 peers may connect (LAN only); at most 4 scene connections; the token comes from
SWITCH_REMOTE_TOKEN_FILE (default ~/.config/switch-remote/token) and is never printed.
"""
import argparse
import hmac
import ipaddress
import json
import os
import socket
import socketserver
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

SCENE_PORT = 47131
MAX_AGE_MS = 500             # core kSceneMaxAgeMs
MAX_LINE = 128
MAX_CONNECTIONS = 4
DEFAULT_SOCKET = f'/run/user/{os.getuid()}/switch-remote-scene.sock'
PRIVATE = [ipaddress.ip_network(n) for n in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', '169.254.0.0/16',
                                             '127.0.0.0/8')]


def mode_is_online(mode):
    return (mode & 0x7f000000) == 0x07000000


def mode_is_local_wireless(mode):
    return (mode & 0x7fff0000) == 0x02010000


def parse_scene(line):
    """`SCENE <seq> 0x<mode> <deluxe> <guards>` -> dict, or None (the core's parse_request rules)."""
    f = line.split(' ')
    if len(f) != 5 or f[0] != 'SCENE' or not f[1].isdigit():
        return None
    h = f[2]
    if not (3 <= len(h) <= 10) or not h.startswith('0x') or any(c not in '0123456789abcdef' for c in h[2:]):
        return None
    if f[3] not in ('-1', '0', '1') or f[4] not in ('0', '1'):
        return None
    return {'seq': int(f[1]), 'mode': int(h[2:], 16), 'deluxe': int(f[3]), 'guards': f[4] == '1'}


def gate(report, now):
    """The verdict for `report` (or None) at monotonic time `now`."""
    if report is None:
        return 'no_scene_report'
    if now < report['received'] or (now - report['received']) * 1000 > MAX_AGE_MS:
        return 'scene_report_stale'
    if not report['guards']:
        return 'nro_guards_failed'
    if mode_is_online(report['mode']):
        return 'online_mode'
    if report['deluxe'] == 1 and not mode_is_local_wireless(report['mode']):
        return 'online_deluxe'
    return 'allowed'


def peer_allowed(ip):
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return a.version == 4 and any(a in n for n in PRIVATE)


class Receiver:
    """The scene listener. `status()` is what clients see; tests use it in-process."""

    def __init__(self, token, bind='0.0.0.0', port=SCENE_PORT, clock=time.monotonic):
        self.token, self.clock = token.encode(), clock
        self.lock = threading.Lock()
        self.report, self.peer, self.connections, self.rejected = None, None, set(), {}
        recv = self

        class Handler(socketserver.StreamRequestHandler):
            timeout = 5.0  # AUTH must come promptly; reports arrive every 100 ms

            def handle(self):
                recv._serve(self)

        class Server(socketserver.ThreadingTCPServer):
            daemon_threads = True
            allow_reuse_address = True

            def verify_request(self, request, client_address):
                if not peer_allowed(client_address[0]):
                    recv._reject('peer_not_private')
                    return False
                with recv.lock:
                    if len(recv.connections) >= MAX_CONNECTIONS:
                        recv.rejected['too_many'] = recv.rejected.get('too_many', 0) + 1
                        return False
                    recv.connections.add(request)
                return True

            def shutdown_request(self, request):
                with recv.lock:  # also called for rejected requests: only accepted ones are in the set
                    recv.connections.discard(request)
                super().shutdown_request(request)

        self.server = Server((bind, port), Handler)
        self.port = self.server.server_address[1]
        self.thread = None

    def _reject(self, why):
        with self.lock:
            self.rejected[why] = self.rejected.get(why, 0) + 1

    def _readline(self, h):
        line = h.rfile.readline(MAX_LINE + 1)
        if not line or len(line) > MAX_LINE or not line.endswith(b'\n'):
            return None
        return line[:-1].rstrip(b'\r').decode('ascii', 'replace')

    def _serve(self, h):
        first = self._readline(h)
        if first is None or not first.startswith('AUTH ') or not hmac.compare_digest(first[5:].encode(), self.token):
            self._reject('auth')
            return
        while True:
            try:
                line = self._readline(h)
            except (OSError, socket.timeout):
                return
            if line is None:
                return
            r = parse_scene(line)
            if r is None:
                self._reject('bad_line')
                return
            r['received'] = self.clock()
            with self.lock:
                self.report, self.peer = r, h.client_address[0]

    def start(self):
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self

    def stop(self):
        self.server.shutdown()
        self.server.server_close()

    def status(self):
        with self.lock:
            r, peer, rejected = self.report, self.peer, dict(self.rejected)
        now = self.clock()
        out = {'gate': gate(r, now), 'peer': peer, 'rejected': rejected}
        if r is not None:
            out.update({'seq': r['seq'], 'mode': f'0x{r["mode"]:x}', 'deluxe': r['deluxe'], 'guards': r['guards'],
                        'age_ms': int((now - r['received']) * 1000)})
        return out


def query(socket_path=DEFAULT_SOCKET, timeout=1.0):
    """The daemon's status; a daemon that can't be reached closes the gate."""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            s.connect(socket_path)
            s.sendall(b'{"method": "status"}\n')
            buf = b''
            while not buf.endswith(b'\n'):
                chunk = s.recv(4096)
                if not chunk:
                    break
                buf += chunk
        return json.loads(buf)
    except (OSError, ValueError) as e:
        return {'gate': 'no_scene_receiver', 'error': f'{socket_path}: {e}'}


def serve(args):
    import switch_remote as sr
    recv = Receiver(sr.read_token(), args.bind, args.port).start()
    path = Path(args.socket)
    if path.exists():
        path.unlink()

    class Unix(socketserver.StreamRequestHandler):
        def handle(self):
            self.rfile.readline(256)
            self.wfile.write((json.dumps(recv.status()) + '\n').encode())

    class UnixServer(socketserver.ThreadingUnixStreamServer):
        daemon_threads = True

    us = UnixServer(str(path), Unix)
    os.chmod(path, 0o600)
    print(json.dumps({'listening': f'{args.bind}:{recv.port}', 'socket': str(path)}), flush=True)
    try:
        us.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        recv.stop()
        path.unlink(missing_ok=True)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('cmd', choices=['serve', 'status'])
    ap.add_argument('--port', type=int, default=SCENE_PORT)
    ap.add_argument('--bind', default='0.0.0.0')
    ap.add_argument('--socket', default=DEFAULT_SOCKET)
    a = ap.parse_args(argv)
    if a.cmd == 'serve':
        serve(a)
        return 0
    print(json.dumps(query(a.socket)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
