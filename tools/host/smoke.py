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
  smoke.py --backend autopilot --host IP --out DIR fallback-check
                                                  the sys-autopilot fallback (DESIGN.md 11): status, then on the HOME
                                                  menu RIGHT and LEFT taps (cursor moves to tile 2 and back), a held
                                                  ZL released by its lease, RESET; a shot after each. step/stick/bye too.

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
import switch_remote as sr  # noqa: E402

LIBRARY = Path.home() / 'ssbu-mod-playbook' / 'tools' / 'eden-runner' / 'library'
SSBU = '0x1006a800016e000'


class Smoke:
    def __init__(self, host, port, out, backend='switch'):
        log = str(Path(out) / 'switch-actions.jsonl')
        if backend == 'autopilot':
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

    def preflight(self):
        self.call('status')
        self.call('switch.records', shot=False)
        r = self.c.request('switch.list', {'root': 'crash_reports'})
        n = len(r.get('result') or [])
        newest = sorted((e for e in (r.get('result') or [])), key=lambda e: e['mtime'])[-3:]
        self.receipt('crash_reports listing', {'ok': r.get('ok'), 'count': n, 'newest': newest}, shot=False)
        self.call('switch.list', {'root': 'ultimate', 'dir': 'switch-remote'}, shot=False)

    def launch(self):
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
        mode = int(st.get('scene_mode', '0x0'), 16)
        ok = (mode & 0x7fff0000) == 0x02010000 and st.get('gate') == 'allowed'
        return self.receipt('local-wireless check', {'ok': ok, 'scene_mode': hex(mode), 'gate': st.get('gate'),
                                                      'scene_deluxe': st.get('scene_deluxe'),
                                                      'scene_age_ms': st.get('scene_age_ms')})

    def terminate(self):
        before = {e['name'] for e in (self.c.request('switch.list', {'root': 'crash_reports'}).get('result') or [])}
        self.call('switch.terminate')
        self.wait_status('application gone', lambda s: s.get('app') == 'none', 30)
        after = self.c.request('switch.list', {'root': 'crash_reports'}).get('result') or []
        new = [e['name'] for e in after if e['name'] not in before]
        logs = self.out / 'logs'
        for name in new:
            self.c.request('switch.fetch', {'root': 'crash_reports', 'path': name, 'out': str(logs / 'crash_reports' / name)})
        for rel in ('switch-remote/events.log', 'switch-remote/sysmodule.log'):
            self.c.request('switch.fetch', {'root': 'ultimate', 'path': rel, 'out': str(logs / rel)})
        return self.receipt('logs fetched', {'ok': True, 'new_crash_reports': new, 'dir': str(logs)}, shot=False)

    def fallback_check(self):
        st = self.call('status')
        if not st.get('ok') or st['result'].get('state') != 'reachable':
            return st
        for button in ('dright', 'dleft'):
            self.call('input.button', {'button': button, 'state': 'tap', 'duration_ms': 100})
            time.sleep(0.5)
        self.call('input.button', {'button': 'zl', 'state': 'press', 'lease_ms': 300}, shot=False)
        self.c.wait_leases()
        held = self.c.request('status').get('result', {})
        self.receipt('lease released', {'ok': not held.get('leases'), 'status': held}, shot=False)
        return self.call('input.reset')

    def bye(self):
        if self.backend == 'autopilot':
            self.c.close()
            return self.receipt('released (input.clear)', {'ok': True}, shot=False)
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
    ap.add_argument('--host', required=True)
    ap.add_argument('--port', type=int)
    ap.add_argument('--backend', choices=['switch', 'autopilot'], default='switch')
    ap.add_argument('--out', required=True)
    ap.add_argument('cmd')
    ap.add_argument('args', nargs='*')
    a = ap.parse_args(argv)
    s = Smoke(a.host, a.port, a.out, a.backend)
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
    elif a.cmd == 'fallback-check' and a.backend == 'autopilot':
        s.fallback_check()
    else:
        ap.error(f'unknown command {a.cmd}')
    if a.backend == 'autopilot':
        s.c.wait_leases()  # never exit with a host-side lease pending
    s.c.conn.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
