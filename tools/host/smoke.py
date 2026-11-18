#!/usr/bin/python3
"""The first hardware smoke, a phase at a time, with a receipt line and a screenshot after every action.

  smoke.py --host IP --out DIR preflight          status, capture, screenshot, RECORDS, crash-report count
  smoke.py --host IP --out DIR launch             LAUNCH (only without an application), wait for SSBU and an offline scene
  smoke.py --host IP --out DIR play SEGMENT...    library segments (playbook tools/eden-runner/library), shot after each
  smoke.py --host IP --out DIR step BUTTON [MS]   one tap (route building only), shot after
  smoke.py --host IP --out DIR stick X Y [MS]     one stick push, shot after
  smoke.py --host IP --out DIR lan-check          status in a local-wireless lobby: expects a 0x0201xxxx scene, gate allowed
  smoke.py --host IP --out DIR online-check       status + one refused tap (expects inject_refused online_*; NSO only)
  smoke.py --host IP --out DIR terminate          TERMINATE, then the new crash reports and our logs fetched
  smoke.py --host IP --out DIR bye                RESET, DETACH, BYE (clean disconnect)

--backend autopilot (option d, the default driver since 2026-11-16; DESIGN.md 12) takes the same steps:
  preflight (no RECORDS), launch (HOME -> image-gated still -> A, then the scene reports must start), play/step/stick
  (gated by scened.py), lan-check/online-check, terminate (HOME -> still -> X -> still -> A), bye, plus
  driver-check   status, a still, and one tap that must be REFUSED (no game reporting: no_scene_report)

--backend usb --device /dev/serial/by-id/... (the ESP32-S3 wired pad, DESIGN.md 13; the primary input route) takes the
same steps (launch/terminate/dismiss are still-checked button sequences; no gate), plus
  pad-check      VERSION, STATUS usb_mounted=1 (the dock enumerated the pad) and a still

Receipts: DIR/receipts.jsonl (one line per action: wall time, action, reply, screenshot). Screenshots: DIR/shots/.
The token is read from SWITCH_REMOTE_TOKEN_FILE / ~/.config/switch-remote/token and never written anywhere.
"""
import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from connection_config import connection_setting
import switch_remote as sr  # noqa: E402

LIBRARY = Path.home() / 'ssbu-mod-playbook' / 'tools' / 'eden-runner' / 'library'
SSBU = '0x1006a800016e000'


class Smoke:
    def __init__(self, host, port, out, backend='switch', device=None):
        log = str(Path(out) / 'switch-actions.jsonl')
        if backend == 'usb':
            import usbpad
            self.c = usbpad.UsbClient(device, actions_log=log)
        elif backend == 'autopilot':
            import autopilot
            self.c = autopilot.AutopilotClient(host, port or autopilot.AUTOPILOT_PORT, sr.read_token(), actions_log=log)
        else:
            self.c = sr.SwitchClient(host, port or sr.DEFAULT_PORT, sr.read_token(), actions_log=log)
        self.backend = backend
        self.out = Path(out)
        (self.out / 'shots').mkdir(parents=True, exist_ok=True)
        self.n = len(list((self.out / 'shots').glob('*.png')))

    def receipt(self, action, reply, shot=True):
        path = None
        if shot:
            self.n += 1
            path = self.out / 'shots' / f'{self.n:03d}-{action.replace(" ", "_")[:40]}.png'
            s = self.c.request('ui.screenshot', {'path': str(path)})
            path = str(path) if s.get('ok') else f'screenshot failed: {s.get("error")}'
        line = {'wall': time.time(), 'time': time.strftime('%H:%M:%SZ', time.gmtime()), 'action': action,
                'reply': reply, 'shot': path}
        with open(self.out / 'receipts.jsonl', 'a') as f:
            f.write(json.dumps(line, default=str) + '\n')
        print(json.dumps(line, default=str), flush=True)
        return reply

    def call(self, method, params=None, shot=True):
        return self.receipt(method + (' ' + json.dumps(params) if params else ''), self.c.request(method, params), shot)

    def wait_status(self, label, pred, cap):
        deadline, last = time.time() + cap, None
        while time.time() < deadline:
            last = self.c.request('status').get('result', {})
            if pred(last):
                return self.receipt(f'wait {label}', {'ok': True, 'status': last})
            time.sleep(1)
        return self.receipt(f'wait {label} TIMEOUT', {'ok': False, 'status': last})

    def scene(self, st):
        """(mode, gate, deluxe, age) from either backend's status."""
        if self.backend in ('autopilot', 'usb'):
            sc = st.get('scene') or {}
            return sc.get('mode', '0x0'), st.get('gate'), sc.get('deluxe'), sc.get('age_ms')
        return st.get('scene_mode', '0x0'), st.get('gate'), st.get('scene_deluxe'), st.get('scene_age_ms')

    def preflight(self):
        self.call('status')
        if self.backend != 'autopilot':
            self.call('switch.records', shot=False)
        r = self.c.request('switch.list', {'root': 'crash_reports'})
        n = len(r.get('result') or [])
        newest = sorted((e for e in (r.get('result') or [])), key=lambda e: e['mtime'])[-3:]
        self.receipt('crash_reports listing', {'ok': r.get('ok'), 'count': n, 'newest': newest}, shot=False)
        self.call('switch.list', {'root': 'ultimate', 'dir': 'switch-remote'}, shot=False)

    def pad_check(self):
        v = self.call('switch.version', shot=False)
        st = self.call('status')
        mounted = (st.get('result') or {}).get('usb_mounted') == '1'
        return self.receipt('pad enumerated by the dock', {'ok': bool(v.get('ok')) and mounted, 'status': st},
                            shot=False)

    def launch(self):
        if self.backend == 'usb':
            r = self.call('switch.launch')
            if not r.get('ok'):
                return r
            time.sleep(15)  # boot to the title screen; the still shows it (the scene report, if any, is info)
            return self.call('status')
        if self.backend == 'autopilot':
            r = self.call('switch.launch')
            if not r.get('ok'):
                return r
            # The NRO's reports are the proof that SSBU (with the plugin) is running.
            self.wait_status('scene reports started', lambda s: s.get('running'), 120)
            return self.wait_status('offline scene report (gate allowed)', lambda s: s.get('gate') == 'allowed', 180)
        self.call('switch.records', shot=False)
        r = self.call('switch.launch')
        if not r.get('ok'):
            return r
        self.wait_status('ssbu running', lambda s: s.get('app') == 'running' and s.get('program') == SSBU, 60)
        return self.wait_status('offline scene report (gate allowed)', lambda s: s.get('gate') == 'allowed', 180)

    def play(self, names):
        for name in names:
            seg = LIBRARY / 'segments' / f'{name}.edenmacro.json'
            r = self.call('macro.play', {'path': str(seg)}, shot=False)
            if not r.get('ok'):
                return r
            deadline = time.time() + 120
            while time.time() < deadline:
                st = self.c.request('macro.status').get('result', {})
                if st.get('state') in ('finished', 'error', 'idle'):
                    break
                time.sleep(0.1)
            self.receipt(f'segment {name}', {'ok': st.get('state') == 'finished', 'macro': st})
            time.sleep(1.0)  # let the screen settle before the next segment; the shot above is the check

    def online_check(self):
        self.call('status', shot=False)
        r = self.c.request('input.button', {'button': 'a', 'state': 'tap'})
        refused = not r.get('ok') and r['error']['code'] == 'inject_refused'
        return self.receipt('online refusal check', {'ok': refused, 'reply': r})

    def lan_check(self):
        st = self.c.request('status').get('result', {})
        mode, gate, deluxe, age = self.scene(st)
        mode = int(mode or '0x0', 16)
        ok = (mode & 0x7fff0000) == 0x02010000 and gate == 'allowed'
        return self.receipt('local-wireless check', {'ok': ok, 'scene_mode': hex(mode), 'gate': gate,
                                                      'scene_deluxe': deluxe, 'scene_age_ms': age})

    def terminate(self):
        before = {e['name'] for e in (self.c.request('switch.list', {'root': 'crash_reports'}).get('result') or [])}
        self.call('switch.terminate')
        if self.backend == 'usb':
            return self.receipt('terminated (button sequence)', {'ok': True}, shot=True)
        if self.backend == 'autopilot':
            self.wait_status('scene reports stopped', lambda s: not s.get('running'), 30)
        else:
            self.wait_status('application gone', lambda s: s.get('app') == 'none', 30)
        after = self.c.request('switch.list', {'root': 'crash_reports'}).get('result') or []
        new = [e['name'] for e in after if e['name'] not in before]
        logs = self.out / 'logs'
        for name in new:
            self.c.request('switch.fetch', {'root': 'crash_reports', 'path': name, 'out': str(logs / 'crash_reports' / name)})
        for rel in ('switch-remote/events.log', 'switch-remote/sysmodule.log'):
            self.c.request('switch.fetch', {'root': 'ultimate', 'path': rel, 'out': str(logs / rel)})
        return self.receipt('logs fetched', {'ok': True, 'new_crash_reports': new, 'dir': str(logs)}, shot=False)

    def driver_check(self):
        """sys-autopilot answers; with no game reporting, a tap must be refused by the host gate (fail closed)."""
        st = self.call('status')
        if not st.get('ok') or st['result'].get('state') != 'reachable':
            return st
        r = self.c.request('input.button', {'button': 'a', 'state': 'tap'})
        refused = not r.get('ok') and r['error']['code'] == 'inject_refused'
        return self.receipt('gate refuses without a scene report', {'ok': refused, 'reply': r})

    def lease_check(self):
        """In game (gate allowed): a held ZL is released by its host lease."""
        self.call('input.button', {'button': 'zl', 'state': 'press', 'lease_ms': 300}, shot=False)
        self.c.wait_leases()
        held = self.c.request('status').get('result', {})
        return self.receipt('lease released', {'ok': not held.get('leases'), 'status': held}, shot=False)

    def bye(self):
        if self.backend in ('autopilot', 'usb'):
            self.c.close()
            return self.receipt('released (input.clear / RESET)', {'ok': True}, shot=False)
        for m in ('input.reset', 'switch.detach'):
            self.call(m, shot=False)
        try:
            self.c.conn.call('BYE')
        except (sr.SwitchError, OSError, ConnectionError):
            pass
        self.c.conn.close()
        return self.receipt('disconnected', {'ok': True}, shot=False)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--host', help='Switch address, overrides local config')
    ap.add_argument('--device', help='usb backend: the board\'s UART serial device')
    ap.add_argument('--port', type=int)
    ap.add_argument('--backend', choices=['switch', 'autopilot', 'usb'], default='switch')
    ap.add_argument('--out', required=True)
    ap.add_argument('cmd')
    ap.add_argument('args', nargs='*')
    a = ap.parse_args(argv)
    if a.backend == 'usb':
        a.device = connection_setting(ap, 'device', '--device', a.device or a.host)
    else:
        a.host = connection_setting(ap, 'host', '--host', a.host)
    s = Smoke(a.host, a.port, a.out, a.backend, a.device)
    if a.cmd == 'preflight':
        s.preflight()
    elif a.cmd == 'launch':
        s.launch()
    elif a.cmd == 'play':
        s.play(a.args)
    elif a.cmd == 'step':
        s.call('input.button', {'button': a.args[0], 'state': 'tap', 'duration_ms': int(a.args[1]) if len(a.args) > 1 else 100})
    elif a.cmd == 'stick':
        ms = int(a.args[2]) if len(a.args) > 2 else 250
        s.call('input.stick', {'stick': 'left', 'x': float(a.args[0]), 'y': float(a.args[1]), 'lease_ms': ms})
    elif a.cmd == 'lan-check':
        s.lan_check()
    elif a.cmd == 'online-check':
        s.online_check()
    elif a.cmd == 'terminate':
        s.terminate()
    elif a.cmd == 'bye':
        s.bye()
    elif a.cmd in ('driver-check', 'fallback-check') and a.backend == 'autopilot':
        s.driver_check()
    elif a.cmd == 'lease-check' and a.backend == 'autopilot':
        s.lease_check()
    elif a.cmd == 'pad-check' and a.backend == 'usb':
        s.pad_check()
    elif a.cmd == 'dismiss':
        s.call('switch.dismiss')
    else:
        ap.error(f'unknown command {a.cmd}')
    if a.backend in ('autopilot', 'usb'):
        s.c.wait_leases()  # never exit with a host-side lease pending / a device macro mid-play
    if a.backend == 'usb':
        s.c.stop_keepalive()
    s.c.conn.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
