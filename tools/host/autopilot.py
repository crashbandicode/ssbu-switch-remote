#!/usr/bin/python3
"""Fallback console driver: sys-autopilot's REST API behind the same edenctl envelope as SwitchClient.

Primary ruling 2026-11-16: sys-autopilot (TooTallNate, pinned 6ca6aac, built by tools/build_autopilot.ps1) is kept as a
FALLBACK driver for when switch-remote's sysmodule does not listen. This backend is deliberately thin:

  ping / status                  -> GET /status (no process status: running/app are reported as unknown)
  input.button tap               -> POST /input/tap {"buttons":[NAME],"durationMs"}   (the console holds, then releases)
  input.button press / release   -> POST /input/hold | /input/release, with a HOST-side lease: the press is released
                                    when lease_ms expires unless renewed (sys-autopilot itself has no lease)
  input.stick                    -> POST /input/stick {"side","x","y"}, recentred by a host-side lease
  input.reset                    -> POST /input/clear (and every pending lease is cancelled)
  ui.* / recording.* / jobs.get  -> the capture card, exactly as SwitchClient (OBS, else capd)
  macro.* / switch.*             -> refused `unsupported` (no frame-timed macros, no launch/terminate/files here)

Rules kept from switch-remote: HOME and CAPTURE are refused (system buttons); the token comes from the same token file
(sys-autopilot's config.ini `token =` carries it) and is never printed. There is NO online gate on this path: the
console is LAN-only (operator, 2026-11-16), and the fallback is used only for the hardware smoke (DESIGN.md 11).

One virtual controller at a time: before the first input this client checks that switch-remote's own port (47130)
does not answer; if it does, inputs are refused `two_drivers` until our sysmodule is stopped (DESIGN.md 11).
Address: `autopilot://HOST[:4150]`.
"""
import json
import socket
import threading
import time
import urllib.error
import urllib.request

from switch_remote import DEFAULT_PORT, SwitchClient, SwitchError, button_bit, read_token, BUTTONS, ALIASES

AUTOPILOT_PORT = 4150
MAX_MS = 10000          # sys-autopilot caps durations at 10 s
OWN_CHECK_EVERY_S = 30.0


def autopilot_button(name):
    """Our edenctl button name -> sys-autopilot's (A B X Y L R ZL ZR PLUS MINUS UP DOWN LEFT RIGHT LSTICK RSTICK)."""
    button_bit(name)  # validates and refuses HOME / CAPTURE / SL / SR
    return ALIASES.get(name, name).upper()


assert all(autopilot_button(b) for b in BUTTONS[:16])


class AutopilotClient(SwitchClient):
    def __init__(self, host, port=AUTOPILOT_PORT, token=None, own_port=DEFAULT_PORT, http_timeout=5.0, **kw):
        super().__init__(host, own_port, token or read_token(), **kw)
        self.base = f'http://{host}:{port}'
        self.own_port, self.http_timeout = own_port, http_timeout
        self.leases, self.lease_lock, self.lease_error = {}, threading.Lock(), ''
        self.own_checked = 0.0

    # --- HTTP -------------------------------------------------------------------------------------------------
    def _http(self, method, path, body=None, extra_timeout=0.0):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.base + path, data=data, method=method)
        req.add_header('Authorization', 'Bearer ' + self.conn.token)
        if data is not None:
            req.add_header('Content-Type', 'application/json')
        try:
            with urllib.request.urlopen(req, timeout=self.http_timeout + extra_timeout) as r:
                raw = r.read()
        except urllib.error.HTTPError as e:
            with e:
                detail = e.read()[:200].decode('utf-8', 'replace')
            if e.code in (401, 403):
                raise SwitchError('auth', f'sys-autopilot refused the token (HTTP {e.code})') from e
            raise SwitchError(f'http_{e.code}', detail) from e
        try:
            return json.loads(raw) if raw else {}
        except ValueError:
            return {'raw': raw[:200].decode('utf-8', 'replace')}

    def _own_sysmodule_listening(self):
        try:
            with socket.create_connection((self.conn.host, self.own_port), timeout=1.0):
                return True
        except OSError:
            return False

    def _input(self, path, body=None, extra_timeout=0.0):
        now = time.monotonic()
        if now - self.own_checked >= OWN_CHECK_EVERY_S:
            if self._own_sysmodule_listening():
                raise SwitchError('two_drivers', f'switch-remote listens on {self.own_port}: stop it (SHUTDOWN or '
                                  'ovlSysmodules) before driving through sys-autopilot; one virtual controller only')
            self.own_checked = now
        return self._http('POST', path, body if body is not None else {}, extra_timeout)

    # --- host-side leases -------------------------------------------------------------------------------------
    def _lease(self, key, ms, expire):
        with self.lease_lock:
            old = self.leases.pop(key, None)
            if old:
                old.cancel()
            if ms is None:
                return
            t = threading.Timer(ms / 1000.0, self._expire, (key, expire))
            t.daemon = True
            self.leases[key] = t
            t.start()

    def _expire(self, key, expire):
        with self.lease_lock:
            if self.leases.get(key) is None or self.leases[key] is not threading.current_thread():
                return
            del self.leases[key]
        try:
            expire()
        except (OSError, SwitchError) as e:
            self.lease_error = f'{key}: {e}'

    def wait_leases(self):
        """Blocks until every pending lease has expired (a one-shot CLI must not exit with a button held)."""
        while True:
            with self.lease_lock:
                pending = list(self.leases.values())
            if not pending:
                return
            for t in pending:
                t.join()

    def cancel_leases(self):
        with self.lease_lock:
            for t in self.leases.values():
                t.cancel()
            self.leases.clear()

    # --- methods ----------------------------------------------------------------------------------------------
    def _dispatch(self, method, p):
        if method == 'ping':
            return {'pong': str(self._http('GET', '/status').get('version', ''))}
        if method == 'status':
            return self.status()
        if method == 'input.button':
            name = autopilot_button(p['button'])
            state = p.get('state', 'tap')
            if state == 'tap':
                ms = max(1, min(MAX_MS, int(p.get('duration_ms', 100))))
                self._lease(f'btn:{name}', None, None)
                self._input('/input/tap', {'buttons': [name], 'durationMs': ms}, extra_timeout=ms / 1000.0)
            elif state == 'press':
                ms = max(1, min(MAX_MS, int(p.get('lease_ms', 1000))))
                self._input('/input/hold', {'buttons': [name]})
                self._lease(f'btn:{name}', ms, lambda: self._http('POST', '/input/release', {'buttons': [name]}))
            elif state == 'release':
                self._lease(f'btn:{name}', None, None)
                self._input('/input/release', {'buttons': [name]})
            else:
                raise SwitchError('bad_arguments', f'state {state!r}')
            return {'accepted': True}
        if method == 'input.stick':
            side = p.get('stick', 'left')
            if side not in ('left', 'right'):
                raise SwitchError('bad_arguments', f'stick {side!r}')
            x = max(-1.0, min(1.0, float(p.get('x', 0))))
            y = max(-1.0, min(1.0, float(p.get('y', 0))))
            ms = max(1, min(MAX_MS, int(p.get('lease_ms', 1000))))
            self._input('/input/stick', {'side': side, 'x': x, 'y': y})
            self._lease(f'stick:{side}', ms,
                        lambda: self._http('POST', '/input/stick', {'side': side, 'x': 0.0, 'y': 0.0}))
            return {'accepted': True}
        if method == 'input.reset':
            self.cancel_leases()
            self._http('POST', '/input/clear', {})
            return {'accepted': True}
        if method.startswith('macro.') or method.startswith('switch.'):
            raise SwitchError('unsupported', f'{method} is not available through the sys-autopilot fallback '
                              '(input, status and capture only; DESIGN.md 11)')
        return super()._dispatch(method, p)

    def status(self):
        out = {'backend': 'autopilot', 'running': None, 'app': 'unknown'}
        try:
            st = self._http('GET', '/status')
            out.update({'state': 'reachable', 'version': st.get('version'), 'firmware': st.get('firmware'),
                        'controller_attached': st.get('controllerAttached'), 'uptime_s': st.get('uptimeSeconds'),
                        'leases': sorted(self.leases)})
            if self.lease_error:
                out['lease_error'] = self.lease_error
        except (OSError, SwitchError) as e:
            out.update({'state': 'unreachable', 'error': str(e)})
        try:
            cap = self._capd('status')
            out['capture'] = {k: cap.get(k) for k in ('backend', 'signal', 'frames', 'last_frame_age_ms', 'error')
                              if cap.get(k) is not None}
            out['capture'].setdefault('backend', 'capd')
        except SwitchError as e:
            out['capture'] = {'error': str(e)}
        return out

    def close(self):
        """Releases everything this client holds (best effort)."""
        self.cancel_leases()
        try:
            self._http('POST', '/input/clear', {})
        except (OSError, SwitchError):
            pass
