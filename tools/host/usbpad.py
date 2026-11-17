#!/usr/bin/python3
"""The USB wired-controller backend (DESIGN.md section 13): `usb://<serial device>`.

The operator's ESP32-S3 board runs firmware/ (switch-pad): its OTG port is a HORI-style wired pad in the Switch
dock, and its UART port (CH343 bridge) is this host's serial device. The firmware speaks switch-remote's line
protocol, so this client is SwitchClient over a serial link:

  input.button / input.stick / input.reset   -> BTN / STICK / RESET (leases run on the device)
  macro.play / macro.status / macro.stop     -> MBEGIN + MEV + MPLAY: samples play at 60 Hz of the DEVICE clock
                                                (no host jitter); the runner's macros run unchanged
  switch.launch / terminate / dismiss / relaunch
                                             -> button sequences with capture-card still checks (sequences.py)
  switch.list / file / fetch                 -> the OPTIONAL sys-autopilot (files=autopilot://IP), else unsupported
  ui.* / recording.* / jobs.get              -> the capture card, as SwitchClient (OBS, else capd)
  status                                     -> the firmware's STATUS + the scene report as INFORMATION + capture
  pad.mode {"mode": "pro"|"gc"}              -> MODE: Pro (HORI) <-> GameCube adapter, waits for the re-enumeration
  input.trigger {"l", "r", "lease_ms"}       -> TRIG: GameCube analog triggers (GC mode; docs/GC-MODE.md)

There is no online gate on this route (primary direction 2026-11-16: a plain wired controller needs none). HOME
and CAPTURE stay refused as plain buttons; only the sequences press HOME. While connected the client sends PING
every 300 ms; the firmware goes neutral after 1 s without a line, so a crashed host releases the pad.

  usb:///dev/serial/by-id/usb-1a86_..._-if00      (or usb://ttyACM0 for /dev/ttyACM0)
"""
import os
import select
import socket
import tempfile
import termios
import threading
import time
import tty
from pathlib import Path

from sequences import LAUNCH_QUIET_MS, HomeSequences
from switch_remote import Connection, SwitchClient, SwitchError, parse_kv, read_token, trigger_value

BAUD = 921600
MODE_TIMEOUT_S = 30.0  # the console took >10 s to configure the adapter on hardware (2026-11-17 14:42Z)
KEEPALIVE_S = 0.3
SETTLE_S = 1.5
SERIAL_TOKEN = 'serial-link-no-token'  # the firmware accepts AUTH: the link is physical
SYSTEM_BITS = {'A': 0, 'X': 2, 'HOME': 18}
TAP_MS = 100


def device_path(spec):
    spec = spec[len('usb://'):] if spec.startswith('usb://') else spec
    return spec if spec.startswith('/') else '/dev/' + spec


class FdSock:
    """The two socket calls Connection uses (sendall, recv) over a tty file descriptor."""

    def __init__(self, fd, timeout):
        self.fd, self.timeout = fd, timeout

    def sendall(self, data):
        view = memoryview(data)
        while view:
            _, w, _ = select.select([], [self.fd], [], self.timeout)
            if not w:
                raise socket.timeout('serial write timed out')
            n = os.write(self.fd, view)
            view = view[n:]

    def recv(self, n):
        r, _, _ = select.select([self.fd], [], [], self.timeout)
        if not r:
            raise socket.timeout('no reply from the pad')
        data = os.read(self.fd, n)
        if not data:
            raise ConnectionError('serial device closed')
        return data

    def settimeout(self, t):
        self.timeout = t

    def close(self):
        os.close(self.fd)


class SerialConnection(Connection):
    """Connection over the board's UART: raw 8N1 at 921,600 baud. Boot noise (ROM banner) is skipped."""

    def __init__(self, path, baud=BAUD, timeout=5.0):
        super().__init__(path, 0, SERIAL_TOKEN, timeout)
        self.path, self.baud = path, baud

    def open(self):
        self.close()
        fd = os.open(self.path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        try:
            tty.setraw(fd)
            attrs = termios.tcgetattr(fd)
            speed = getattr(termios, f'B{self.baud}', termios.B115200)
            attrs[4] = attrs[5] = speed
            attrs[2] |= termios.CLOCAL | termios.CREAD
            attrs[2] &= ~termios.HUPCL  # closing must not drop DTR/RTS (the board's auto-reset lines)
            termios.tcsetattr(fd, termios.TCSANOW, attrs)
        except termios.error:
            pass  # a pty in tests
        self.sock, self.buf = FdSock(fd, self.timeout), b''
        # The board may have just reset: retry AUTH for a few seconds, skipping non-protocol lines.
        deadline = time.monotonic() + max(self.timeout, 3.0)
        self.sock.settimeout(0.5)
        while time.monotonic() < deadline:
            self.sock.sendall(b'\nAUTH ' + SERIAL_TOKEN.encode() + b'\n')
            try:
                while True:
                    line = self.read_line()
                    if line.startswith('OK auth'):
                        self.sock.settimeout(self.timeout)
                        return
            except (socket.timeout, TimeoutError):
                continue
        self.close()
        raise SwitchError('unreachable', f'no switch-pad answer on {self.path}')

    def read_line(self):
        line = super().read_line()
        return line.rstrip('\r')


PI_PORT = 47140


def pi_address(spec):
    hostport = spec[len('pi://'):].rstrip('/') if spec.startswith('pi://') else spec
    host, _, port = hostport.rpartition(':') if ':' in hostport else (hostport, '', '')
    return host or hostport, int(port) if port else PI_PORT


class UsbClient(HomeSequences, SwitchClient):
    require_quiet = False  # no gate on a wired pad; launch relies on the image gate alone

    def __init__(self, device, baud=BAUD, files=None, scene=None, still_dir=None, settle_s=SETTLE_S,
                 homegate_refs=None, keepalive_s=KEEPALIVE_S, timeout=5.0, **kw):
        kw.pop('token', None)
        super().__init__('usb', 0, SERIAL_TOKEN, timeout=timeout, **kw)
        self.serial_device = device_path(device)  # (self.device is SwitchClient's V4L2 capture device)
        self.conn = SerialConnection(self.serial_device, baud, timeout)
        self.files = files  # an AutopilotClient (or anything with list/file/fetch), optional
        self.scene, self.settle_s, self.homegate_refs = scene, settle_s, homegate_refs
        self.still_dir = Path(still_dir or Path(tempfile.gettempdir()) / 'switch-remote-stills')
        self.keepalive_s, self.alive = keepalive_s, None
        self.alive_stop = threading.Event()

    # --- link -------------------------------------------------------------------------------------------------
    def _sys(self, line):
        reply = super()._sys(line)
        self._ensure_keepalive()
        return reply

    def play_macro(self, doc):
        result = super().play_macro(doc)  # the upload bypasses _sys
        self._ensure_keepalive()
        return result

    def _ensure_keepalive(self):
        if self.keepalive_s and (self.alive is None or not self.alive.is_alive()):
            self.alive_stop.clear()
            self.alive = threading.Thread(target=self._keepalive, daemon=True)
            self.alive.start()

    def _keepalive(self):
        while not self.alive_stop.wait(self.keepalive_s):
            if not self.lock.acquire(timeout=self.keepalive_s):
                continue  # a long upload holds the link; its MEV lines feed the watchdog anyway
            try:
                if self.conn.sock is not None:
                    self.conn.call('PING')
            except (OSError, ConnectionError, SwitchError):
                self.conn.close()
            finally:
                self.lock.release()

    def stop_keepalive(self):
        self.alive_stop.set()
        if self.alive is not None:
            self.alive.join(2)

    def close(self):
        self.stop_keepalive()
        with self.lock:
            try:
                if self.conn.sock is not None:
                    self.conn.call('RESET')
            except (OSError, ConnectionError, SwitchError):
                pass
            self.conn.close()

    def wait_leases(self):
        """CLI one-shots: wait for a playing macro to end (leases run on the device and need the keep-alive)."""
        while True:
            st = self.request('macro.status').get('result') or {}
            if st.get('state') not in ('playing',):
                return
            time.sleep(0.1)

    # --- sequences --------------------------------------------------------------------------------------------
    def _tap_system(self, name):
        self._sys(f'BTN {SYSTEM_BITS[name]} tap {TAP_MS}')
        time.sleep(TAP_MS / 1000 + 0.05)

    def scene_status(self):
        if self.scene is not None:
            return self.scene.status()
        import scened
        return scened.query()

    # --- methods ----------------------------------------------------------------------------------------------
    def _dispatch(self, method, p):
        if method == 'switch.launch':
            return self.launch()
        if method == 'switch.terminate':
            return self.terminate()
        if method == 'switch.dismiss':
            return self.dismiss()
        if method == 'switch.relaunch':
            t = self.terminate()
            time.sleep(3.0)
            return {'terminate': t, 'launch': self.launch()}
        if method in ('switch.list', 'switch.file', 'switch.fetch'):
            if self.files is None:
                raise SwitchError('unsupported', 'log reads need the optional sys-autopilot (files=autopilot://IP) '
                                  'or a card session')
            return self.files._dispatch(method, p)
        if method == 'switch.version':
            return {'reply': self._sys('VERSION')}
        if method == 'pad.mode':
            return self.mode(p.get('mode'), float(p.get('timeout_s', MODE_TIMEOUT_S)))
        if method == 'input.trigger':
            ms = int(p.get('lease_ms', 1000))
            self._sys(f'TRIG {trigger_value(p.get("l", 0))} {trigger_value(p.get("r", 0))} {max(1, min(10000, ms))}')
            return {'accepted': True}
        if method.startswith('switch.'):
            raise SwitchError('unsupported', f'{method}: switch-pad is a wired controller (DESIGN.md 13)')
        return super()._dispatch(method, p)

    def mode(self, want=None, timeout_s=MODE_TIMEOUT_S):
        """`MODE [pro|gc]` (docs/GC-MODE.md). A switch detaches the pad for 500 ms and the console re-enumerates it;
        this waits until STATUS shows the new mode mounted (usb_mounted=1), or raises mode_not_mounted."""
        if want not in (None, 'pro', 'gc'):
            raise SwitchError('bad_arguments', f'mode {want!r}: pro or gc')
        t0 = time.monotonic()
        reply = self._sys(f'MODE {want}' if want else 'MODE')
        parts = reply.split()
        if len(parts) < 3 or parts[:2] != ['OK', 'mode']:
            raise SwitchError('bad_reply', reply)
        out = {'mode': parts[2], 'reply': reply}
        if 'switching' not in parts[3:]:
            return out
        kv = {}
        while time.monotonic() - t0 < timeout_s:
            time.sleep(0.1)
            kv = parse_kv(self._sys('STATUS'))
            if kv.get('mode') == parts[2] and kv.get('usb_mounted') == '1':
                out.update({'mounted': True, 'switch_s': round(time.monotonic() - t0, 3),
                            'usb': kv.get('usb'), 'gc_init': kv.get('gc_init'), 'mounts': kv.get('mounts')})
                return out
        raise SwitchError('mode_not_mounted', f'{parts[2]} not enumerated by the console within {timeout_s} s '
                          f'(usb_mounted={kv.get("usb_mounted")}); `mode pro` returns to the HORI pad')

    def list(self, root, directory=''):
        return self._dispatch('switch.list', {'root': root, 'dir': directory})

    def file(self, root, path, offset=0, max_bytes=65536):
        if self.files is None:
            raise SwitchError('unsupported', 'log reads need the optional sys-autopilot')
        return self.files.file(root, path, offset, max_bytes)

    def status(self):
        out = {'backend': 'usb', 'device': self.serial_device}
        try:
            kv = parse_kv(self._sys('STATUS'))
            out.update(kv)
            out['state'] = 'reachable'
        except (OSError, ConnectionError, SwitchError) as e:
            self.conn.close()
            out.update({'state': 'unreachable', 'error': str(e)})
        sc = self.scene_status()  # information only on this route
        out['scene'] = sc
        out['gate'] = 'not_applicable'
        fresh = sc.get('gate') not in ('no_scene_report', 'no_scene_receiver', None) and \
            sc.get('age_ms', LAUNCH_QUIET_MS) < LAUNCH_QUIET_MS
        out['running'] = fresh if sc.get('gate') != 'no_scene_receiver' else None
        out['app'] = 'running' if fresh else 'unknown'
        try:
            cap = self._capd('status')
            out['capture'] = {k: cap.get(k) for k in ('backend', 'signal', 'frames', 'last_frame_age_ms', 'error')
                              if cap.get(k) is not None}
            out['capture'].setdefault('backend', 'capd')
        except SwitchError as e:
            out['capture'] = {'error': str(e)}
        return out


class PiClient(UsbClient):
    """The same pad over a network line link: a Raspberry Pi Zero 2 W in USB gadget mode (DESIGN.md 13a) running
    the switch-pad core as a daemon (`pi://HOST[:47140]`). The link is a network, so it is token-authenticated
    (the switch-remote token); everything else is UsbClient: device-timed macros, keep-alive, sequences."""

    def __init__(self, spec, token=None, timeout=5.0, **kw):
        host, port = pi_address(spec)
        super().__init__(f'pi-{host}', timeout=timeout, **kw)
        self.serial_device = f'pi://{host}:{port}'
        self.conn = Connection(host, port, token or read_token(), timeout)
