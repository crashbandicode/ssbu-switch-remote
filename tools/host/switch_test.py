#!/usr/bin/python3
"""Run playbook runner rows against the physical Switch.

  switch_test.py preflight --host IP [--port P] [--backend autopilot|switch]
  switch_test.py run ROWS.json --host IP [--port P] [--backend autopilot|switch] [--out DIR] [--mirror KEY=ROOT:PATH ...]
                 [--playbook DIR]

--backend autopilot (option d, DESIGN.md 12; needs scened.py serve) drives sys-autopilot; switch = the parked sysmodule.

`run` writes DIR/instances.json with one instance, `switch`, whose socket is `switch://IP:PORT`, and runs
the unchanged playbook runner (tools/eden-runner/runner.py) in this process with one shared SwitchClient:
the sysmodule serves one host client at a time, and log gates need the same connection. Each --mirror
(e.g. `diag_log=ultimate:training_rooms/events.log`) is copied from the console's SD to DIR/mirror/ every
second, and the instance names the local copy under KEY, so `log` gates work as they do in Eden.
Screenshots and recordings go through capd (start it first: capd.py serve).
"""
import argparse
import importlib
import json
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from connection_config import connection_setting
import switch_remote as sr  # noqa: E402

DEFAULT_PLAYBOOK = Path.home() / 'ssbu-mod-playbook'


class Mirror(threading.Thread):
    """Appends what a console file gains to a local copy, once a second. A file that shrank starts over."""

    def __init__(self, client, root, path, local, period=1.0):
        super().__init__(daemon=True)
        self.client, self.root, self.path, self.local, self.period = client, root, path, Path(local), period
        self.offset = 0
        self.stop = threading.Event()
        self.errors = 0
        self.local.parent.mkdir(parents=True, exist_ok=True)
        self.local.touch()

    def poll_once(self):
        try:
            chunk = self.client.file(self.root, self.path, self.offset)
        except sr.SwitchError as e:
            if e.code == 'not_found':
                return 0
            raise
        if chunk['size'] < self.offset:  # rotated or rewritten
            self.offset = 0
            with open(self.local, 'ab') as f:
                f.write(b'\n[switch_test: console file restarted]\n')
            return 0
        data = chunk['data']
        if data:
            with open(self.local, 'ab') as f:
                f.write(data)
            self.offset += len(data)
        return len(data)

    def run(self):
        while not self.stop.is_set():
            try:
                while self.poll_once() >= 65536:
                    pass
            except (OSError, ConnectionError, sr.SwitchError):
                self.errors += 1
            self.stop.wait(self.period)


def parse_mirror(spec):
    key, _, rest = spec.partition('=')
    root, _, path = rest.partition(':')
    if not key or not root or not path:
        raise ValueError(f'--mirror wants KEY=ROOT:PATH, got {spec!r}')
    return key, root, path


def make(args, **kw):
    if args.backend == 'usb':
        import usbpad
        return usbpad.UsbClient(args.device, capd_socket=args.capd, **kw)
    if args.backend == 'autopilot':
        import autopilot
        return autopilot.AutopilotClient(args.host, args.port or autopilot.AUTOPILOT_PORT,
                                         sr.read_token(args.token_file), capd_socket=args.capd, **kw)
    return sr.SwitchClient(args.host, args.port or sr.DEFAULT_PORT, sr.read_token(args.token_file), args.capd, **kw)


def instances_for(host, port, out, mirrors, backend='switch'):
    scheme = {'autopilot': 'autopilot', 'usb': 'usb'}.get(backend, 'switch')
    sock = f'usb://{host}' if backend == 'usb' else f'{scheme}://{host}:{port}'
    inst = {'socket': sock, 'screenshot_target': 'switch-capture', 'backend': 'switch'}
    for key, root, path in mirrors:
        inst[key] = str(out / 'mirror' / f'{key}.log')
    return {'switch': inst}


def preflight(client):
    status = client.request('status')
    ok = status.get('ok') and status['result'].get('state') != 'unreachable'
    result = status.get('result', {})
    report = {'sysmodule': 'reachable' if ok else 'unreachable', 'app': result.get('app'),
              'program': result.get('program'), 'gate': result.get('gate'), 'capture': result.get('capture')}
    report['ready'] = bool(ok and result.get('app') == 'running' and result.get('gate') == 'allowed'
                           and (result.get('capture') or {}).get('signal'))
    return report


def run(args):
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    mirrors = [parse_mirror(m) for m in args.mirror]
    client = make(args, actions_log=str(out / 'switch-actions.jsonl'))
    port = args.port or (4150 if args.backend == 'autopilot' else sr.DEFAULT_PORT)
    inst_path = out / 'instances.json'
    target = args.device if args.backend == 'usb' else args.host
    inst_path.write_text(json.dumps(instances_for(target, port, out, mirrors, args.backend), indent=1) + '\n')
    threads = [Mirror(client, root, path, out / 'mirror' / f'{key}.log') for key, root, path in mirrors]
    for t in threads:
        t.start()
    sys.path.insert(0, str(Path(args.playbook) / 'tools' / 'eden-runner'))
    runner = importlib.import_module('runner')
    runner.default_client_factory = lambda: (lambda socket_string: client)
    try:
        return runner.main(['run', str(args.rows), '--instances', str(inst_path), '--out', str(out / 'out')])
    finally:
        for t in threads:
            t.stop.set()
        if hasattr(client, 'close'):
            client.close()  # release everything (sys-autopilot has no lease; the pad stops its keep-alive)
        client.conn.close()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    for name in ('preflight', 'run'):
        p = sub.add_parser(name)
        p.add_argument('--host', help='Switch address, overrides local config')
        p.add_argument('--device', help='usb backend: the board\'s UART serial device')
        p.add_argument('--port', type=int)
        p.add_argument('--backend', choices=['usb', 'autopilot', 'switch'], default='usb')
        p.add_argument('--token-file')
        p.add_argument('--capd', default=sr.DEFAULT_CAPD)
        if name == 'run':
            p.add_argument('rows', type=Path)
            p.add_argument('--out', default=f'switch-runs/{time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())}')
            p.add_argument('--mirror', action='append', default=[])
            p.add_argument('--playbook', default=str(DEFAULT_PLAYBOOK))
    a = ap.parse_args(argv)
    if a.backend == 'usb':
        a.device = connection_setting(ap, 'device', '--device', a.device)
    else:
        a.host = connection_setting(ap, 'host', '--host', a.host)
    if a.cmd == 'preflight':
        client = make(a)
        report = preflight(client)
        print(json.dumps(report))
        return 0 if report['ready'] else 1
    return run(a)


if __name__ == '__main__':
    sys.exit(main())
