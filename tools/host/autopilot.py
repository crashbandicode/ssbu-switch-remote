#!/usr/bin/python3
"""The console driver (option d, primary ruling 2026-11-16): sys-autopilot's REST API behind the edenctl envelope.

sys-autopilot 1.5.0 (TooTallNate; id 4200000000004150) owns the virtual Pro Controller; this client adds what it lacks,
on the host:

  online gate     every injected input needs a scene report under 500 ms old saying offline or local wireless
                  (scened.py receives the NRO's reports; same verdicts as the sysmodule core). Fail closed: no
                  receiver, no report, stale, guards failed, NSO family or Deluxe-online -> `inject_refused <gate>`.
                  A watchdog clears all input within ~100 ms when the gate closes while anything is held.
  leases          press/stick hold until lease_ms unless renewed (sys-autopilot has none)
  macros          .edenmacro.json played host-side: each sample's state is sent at frame/60 s with hold/release/
                  stick calls. HTTP-timed (LAN jitter): fine for menus, NOT frame-exact (status reports late_ms_max)
  launch          HOME -> still -> A, only when NO fresh scene report exists (none for 5 s: the game isn't running)
                  and the capture still shows SSBU as the selected HOME first tile (homegate.py, reviewed refs)
  terminate       HOME -> still (SSBU first tile) -> X -> still (the close dialog changed the screen) -> A, then waits
                  for the scene reports to stop
  logs            switch.list / switch.file / switch.fetch over /files, limited to the sysmodule's six roots

Refused: HOME/CAPTURE as plain buttons (only inside the fixed sequences); switch.dismiss/records/put/update/restart/
shutdown (not part of option d). Residual risk (accepted 2026-11-16): anyone holding sys-autopilot's token can
bypass this gate; the token stays host-only (0600, never printed or committed) and the console is internet-blocked.

One virtual controller at a time: inputs are refused `two_drivers` while switch-remote's own port 47130 answers.
Address: `autopilot://HOST[:4150]`.
"""
import json
import socket
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from switch_remote import (ALIASES, BUTTONS, DEFAULT_PORT, MACRO_SAMPLES_MAX, SwitchClient, SwitchError, button_bit,
                           read_token)
from sequences import LAUNCH_QUIET_MS, HomeSequences

AUTOPILOT_PORT = 4150
MAX_MS = 10000                 # sys-autopilot caps durations at 10 s
OWN_CHECK_EVERY_S = 30.0
WATCHDOG_S = 0.1
SETTLE_S = 1.5                 # HOME menu animation before a still
FRAME_S = 1 / 60
# The sysmodule's log roots (sysmodule/core/core.hpp kRootNames / kRootPaths).
ROOTS = {'crash_reports': '/atmosphere/crash_reports', 'fatal_reports': '/atmosphere/fatal_reports',
         'fatal_errors': '/atmosphere/fatal_errors', 'erpt_reports': '/atmosphere/erpt_reports',
         'ultimate': '/ultimate', 'skyline': '/atmosphere/contents/01006A800016E000/romfs/skyline'}


def autopilot_button(name):
    """Our edenctl button name -> sys-autopilot's (A B X Y L R ZL ZR PLUS MINUS UP DOWN LEFT RIGHT LSTICK RSTICK)."""
    button_bit(name)  # validates and refuses HOME / CAPTURE / SL / SR
    return ALIASES.get(name, name).upper()


def mask_names(mask):
    return [BUTTONS[i].upper() for i in range(16) if mask >> i & 1]


def sd_path(root, path=''):
    if root not in ROOTS:
        raise SwitchError('bad_root', f'{root!r}: one of {sorted(ROOTS)}')
    parts = [p for p in str(path).split('/') if p]
    if any(p in ('.', '..') or '\\' in p for p in parts):
        raise SwitchError('bad_path', 'no . or .. components')
    return '/'.join([ROOTS[root]] + parts)


class AutopilotClient(HomeSequences, SwitchClient):
    def __init__(self, host, port=AUTOPILOT_PORT, token=None, own_port=DEFAULT_PORT, http_timeout=5.0, scene=None,
                 still_dir=None, settle_s=SETTLE_S, homegate_refs=None, **kw):
        super().__init__(host, own_port, token or read_token(), **kw)
        self.base = f'http://{host}:{port}'
        self.own_port, self.http_timeout, self.settle_s = own_port, http_timeout, settle_s
        self.scene = scene                      # an object with .status() (scened.Receiver), else the daemon
        self.homegate_refs = homegate_refs
        self.still_dir = Path(still_dir or Path(tempfile.gettempdir()) / 'switch-remote-stills')
        self.leases, self.lease_lock, self.lease_error = {}, threading.Lock(), ''
        self.own_checked = 0.0
        self.macro = {'state': 'idle', 'frame': 0, 'duration_frames': 0, 'error': '', 'late_ms_max': 0}
        self.macro_thread, self.macro_stop = None, threading.Event()
        self.watchdog, self.gate_release = None, None

    # --- HTTP -------------------------------------------------------------------------------------------------
    def _http(self, method, path, body=None, extra_timeout=0.0, raw=False):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.base + path, data=data, method=method)
        req.add_header('Authorization', 'Bearer ' + self.conn.token)
        if data is not None:
            req.add_header('Content-Type', 'application/json')
        try:
            with urllib.request.urlopen(req, timeout=self.http_timeout + extra_timeout) as r:
                payload = r.read()
        except urllib.error.HTTPError as e:
            with e:
                detail = e.read()[:200].decode('utf-8', 'replace')
            if e.code in (401, 403):
                raise SwitchError('auth', f'sys-autopilot refused the token (HTTP {e.code})') from e
            if e.code == 404:
                raise SwitchError('not_found', detail) from e
            raise SwitchError(f'http_{e.code}', detail) from e
        if raw:
            return payload
        try:
            return json.loads(payload) if payload else {}
        except ValueError:
            return {'raw': payload[:200].decode('utf-8', 'replace')}

    # --- the gate ---------------------------------------------------------------------------------------------
    def scene_status(self):
        if self.scene is not None:
            return self.scene.status()
        import scened
        return scened.query()

    def _require_gate(self):
        st = self.scene_status()
        if st.get('gate') != 'allowed':
            raise SwitchError('inject_refused', st.get('gate') or 'no_scene_receiver')
        return st

    def _own_sysmodule_listening(self):
        try:
            with socket.create_connection((self.conn.host, self.own_port), timeout=1.0):
                return True
        except OSError:
            return False

    def _check_single_driver(self):
        now = time.monotonic()
        if now - self.own_checked >= OWN_CHECK_EVERY_S:
            if self._own_sysmodule_listening():
                raise SwitchError('two_drivers', f'switch-remote listens on {self.own_port}: stop it (SHUTDOWN) '
                                  'before driving through sys-autopilot; one virtual controller only')
            self.own_checked = now

    def _input(self, path, body=None, extra_timeout=0.0, gated=True):
        self._check_single_driver()
        if gated:
            self._require_gate()
        return self._http('POST', path, body if body is not None else {}, extra_timeout)

    def _clear(self):
        return self._http('POST', '/input/clear', {})

    def _watch(self):
        """While anything is held or a macro plays: clear everything as soon as the gate closes."""
        while True:
            with self.lease_lock:
                busy = bool(self.leases) or (self.macro_thread is not None and self.macro_thread.is_alive())
                if not busy:
                    self.watchdog = None
                    return
            g = self.scene_status().get('gate')
            if g != 'allowed':
                self.macro_stop.set()
                self.cancel_leases()
                self.gate_release = {'wall': time.time(), 'gate': g}
                try:
                    self._clear()
                except (OSError, SwitchError) as e:
                    self.lease_error = f'gate release: {e}'
            time.sleep(WATCHDOG_S)

    def _ensure_watchdog(self):
        with self.lease_lock:
            if self.watchdog is None:
                self.watchdog = threading.Thread(target=self._watch, daemon=True)
                self.watchdog.start()

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
        self._ensure_watchdog()

    def _expire(self, key, expire):
        with self.lease_lock:
            if self.leases.get(key) is not threading.current_thread():
                return
            del self.leases[key]
        try:
            expire()
        except (OSError, SwitchError) as e:
            self.lease_error = f'{key}: {e}'

    def wait_leases(self):
        """Blocks until every pending lease has expired and any macro has ended (a one-shot CLI must not exit with
        input held: sys-autopilot would keep it)."""
        while True:
            with self.lease_lock:
                pending = list(self.leases.values())
                macro = self.macro_thread
            if macro is not None and macro.is_alive():
                macro.join()
                continue
            if not pending:
                return
            for t in pending:
                t.join()

    def cancel_leases(self):
        with self.lease_lock:
            for t in self.leases.values():
                t.cancel()
            self.leases.clear()

    # --- macros (host-timed) ------------------------------------------------------------------------------------
    def _parse_macro(self, doc):
        if doc.get('format') != 'eden-input-macro':
            raise SwitchError('bad_macro', 'not an eden-input-macro file')
        events = sorted(doc.get('events') or [], key=lambda e: int(e['frame']))
        duration = int(doc.get('duration_frames') or 0)
        if not events or not duration:
            raise SwitchError('bad_macro', 'no events or no duration')
        if len(events) > MACRO_SAMPLES_MAX:
            raise SwitchError('bad_macro', f'{len(events)} samples (max {MACRO_SAMPLES_MAX})')
        out = []
        for e in events:
            mask = int(e.get('buttons', 0))
            if mask >> 16:
                raise SwitchError('bad_macro', f'frame {e["frame"]}: system buttons are not played')
            sticks = [max(-1.0, min(1.0, float(v))) for v in (e.get('sticks') or [0, 0, 0, 0])[:4]]
            sticks += [0.0] * (4 - len(sticks))
            out.append((int(e['frame']), mask, sticks))
        return out, duration

    def _play(self, events, duration):
        m = self.macro
        held, sticks = 0, [0.0] * 4
        t0 = time.monotonic()
        try:
            for frame, mask, st in events + [(duration, 0, [0.0] * 4)]:
                delay = t0 + frame * FRAME_S - time.monotonic()
                if delay > 0 and self.macro_stop.wait(delay):
                    break
                if self.macro_stop.is_set():
                    break
                m['late_ms_max'] = max(m['late_ms_max'], int(max(0.0, -delay) * 1000))
                m['frame'] = frame
                self._require_gate()
                if mask & ~held:
                    self._http('POST', '/input/hold', {'buttons': mask_names(mask & ~held)})
                if held & ~mask:
                    self._http('POST', '/input/release', {'buttons': mask_names(held & ~mask)})
                held = mask
                for side, i in (('left', 0), ('right', 2)):
                    if st[i:i + 2] != sticks[i:i + 2]:
                        self._http('POST', '/input/stick', {'side': side, 'x': st[i], 'y': st[i + 1]})
                sticks = st
            m['state'] = 'stopped' if self.macro_stop.is_set() else 'finished'
        except (OSError, SwitchError) as e:
            m['state'], m['error'] = 'error', getattr(e, 'message', '') or str(e)
            if isinstance(e, SwitchError) and e.code == 'inject_refused':
                m['error'] = e.message
        finally:
            try:
                self._clear()
            except (OSError, SwitchError):
                pass

    def play_macro(self, doc):
        events, duration = self._parse_macro(doc)
        if self.macro_thread is not None and self.macro_thread.is_alive():
            raise SwitchError('busy', 'a macro is playing')
        self._check_single_driver()
        self._require_gate()
        self.macro = {'state': 'playing', 'frame': 0, 'duration_frames': duration, 'error': '', 'late_ms_max': 0}
        self.macro_stop.clear()
        self.macro_thread = threading.Thread(target=self._play, args=(events, duration), daemon=True)
        self.macro_thread.start()
        self._ensure_watchdog()
        return {'accepted': True, 'samples': len(events), 'duration_frames': duration, 'timing': 'host_http'}

    # --- fixed sequences (LAUNCH re-ruling, 2026-11-16) ---------------------------------------------------------
    def _tap_system(self, name):
        """HOME / X / A inside a fixed sequence only (not gated: the sequences carry their own checks)."""
        self._check_single_driver()
        return self._http('POST', '/input/tap', {'buttons': [name], 'durationMs': 100}, extra_timeout=0.1)

    # --- logs ------------------------------------------------------------------------------------------------
    def _files(self, sd, offset=None):
        q = {'path': sd}
        if offset:
            q['offset'] = str(offset)
        return '/files?' + urllib.parse.urlencode(q, quote_via=urllib.parse.quote)

    def list(self, root, directory=''):
        r = self._http('GET', self._files(sd_path(root, directory) + '/'))
        return [{'type': e.get('type'), 'size': int(e.get('size', 0)), 'mtime': int(e.get('mtime', 0)),
                 'name': e['name']} for e in r.get('entries', []) if e.get('name') not in ('.', '..')]

    def file(self, root, path, offset=0, max_bytes=65536):
        data = self._http('GET', self._files(sd_path(root, path), offset), raw=True)
        return {'size': offset + len(data), 'offset': offset, 'data': data[:max_bytes]}

    def fetch(self, root, path, out):
        out = Path(out)
        out.parent.mkdir(parents=True, exist_ok=True)
        data = self._http('GET', self._files(sd_path(root, path)), raw=True, extra_timeout=30)
        Path(str(out) + '.part').write_bytes(data)
        Path(str(out) + '.part').replace(out)
        return {'path': str(out), 'size': len(data)}

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
            elif state == 'release':  # releasing is always allowed
                self._lease(f'btn:{name}', None, None)
                self._input('/input/release', {'buttons': [name]}, gated=False)
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
            self.macro_stop.set()
            self.cancel_leases()
            self._clear()
            return {'accepted': True}
        if method == 'macro.play':
            if 'path' not in p:
                raise SwitchError('bad_arguments', 'macro.play needs a path (library names are an Eden feature)')
            return self.play_macro(json.loads(Path(p['path']).read_text()))
        if method == 'macro.status':
            return dict(self.macro)
        if method == 'macro.stop':
            self.macro_stop.set()
            return {'accepted': True}
        if method == 'macro.record':
            raise SwitchError('unsupported', 'recording macros is done in Eden')
        if method == 'switch.launch':
            return self.launch()
        if method == 'switch.terminate':
            return self.terminate()
        if method == 'switch.relaunch':
            t = self.terminate()
            time.sleep(3.0)
            return {'terminate': t, 'launch': self.launch()}
        if method == 'switch.list':
            return self.list(p['root'], p.get('dir', ''))
        if method == 'switch.file':
            return self.file(p['root'], p['path'], int(p.get('offset', 0)), int(p.get('max', 65536)))
        if method == 'switch.fetch':
            return self.fetch(p['root'], p['path'], p['out'])
        if method.startswith('switch.'):
            raise SwitchError('unsupported', f'{method} is not part of the sys-autopilot driver (DESIGN.md 12); card '
                              'installs go through tools/card/own_install.py')
        return super()._dispatch(method, p)

    def status(self):
        out = {'backend': 'autopilot'}
        sc = self.scene_status()
        out['scene'] = sc
        out['gate'] = sc.get('gate')
        fresh = sc.get('gate') not in ('no_scene_report', 'no_scene_receiver', None) and \
            sc.get('age_ms', LAUNCH_QUIET_MS) < LAUNCH_QUIET_MS
        out.update({'running': fresh, 'app': 'running' if fresh else 'none_or_unknown'})
        try:
            st = self._http('GET', '/status')
            out.update({'state': 'reachable', 'version': st.get('version'), 'firmware': st.get('firmware'),
                        'controller_attached': st.get('controllerAttached'), 'uptime_s': st.get('uptimeSeconds'),
                        'leases': sorted(self.leases), 'macro': self.macro['state']})
            if self.lease_error:
                out['lease_error'] = self.lease_error
            if self.gate_release:
                out['gate_release'] = self.gate_release
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
        self.macro_stop.set()
        self.cancel_leases()
        try:
            self._clear()
        except (OSError, SwitchError):
            pass


assert all(autopilot_button(b) for b in BUTTONS[:16])
