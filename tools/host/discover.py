#!/usr/bin/python3
"""Find the switch-remote sysmodule on the LAN: probe TCP <port> on every address of a /24, then confirm with
an authenticated PING. Light: one connect attempt per address per round, 0.4 s timeout, 64 at a time.

  discover.py [--net CIDR] [--port 47130] [--prefer HOST] [--every 60] [--until-found SECONDS]
  discover.py --backend autopilot ...   (the sys-autopilot fallback: port 4150, confirmed with GET /status)

Prints one JSON line per round; exits 0 with {"found": ip} once a sysmodule answers, 1 when --until-found runs out.
The token comes from SWITCH_REMOTE_TOKEN_FILE (default ~/.config/switch-remote/token) and is never printed.
"""
import argparse
import asyncio
import ipaddress
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from connection_config import connection_setting
import switch_remote as sr  # noqa: E402


async def port_open(ip, port, timeout):
    try:
        _, w = await asyncio.wait_for(asyncio.open_connection(ip, port), timeout)
    except (OSError, asyncio.TimeoutError):
        return False
    w.close()
    try:
        await w.wait_closed()
    except OSError:
        pass
    return True


async def scan(hosts, port, timeout=0.4, width=64):
    sem = asyncio.Semaphore(width)

    async def one(ip):
        async with sem:
            return ip if await port_open(ip, port, timeout) else None

    return [ip for ip in await asyncio.gather(*(one(h) for h in hosts)) if ip]


def confirm(ip, port, token, backend='switch'):
    if backend == 'autopilot':
        import autopilot
        c = autopilot.AutopilotClient(ip, port, token, capture_backend='capd', http_timeout=3)
    else:
        c = sr.SwitchClient(ip, port, token, capture_backend='capd', timeout=3)
    r = c.request('status')
    c.conn.close()
    res = r.get('result') or {}
    return r.get('ok') and res.get('state') != 'unreachable', res


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--net', help='LAN CIDR, overrides the local network setting')
    ap.add_argument('--backend', choices=['switch', 'autopilot'], default='switch')
    ap.add_argument('--port', type=int)
    ap.add_argument('--prefer', action='append', help='probe a host before scanning the network')
    ap.add_argument('--every', type=float, default=60)
    ap.add_argument('--until-found', type=float, default=0, help='keep polling this many seconds (0: one round)')
    a = ap.parse_args(argv)
    if a.port is None:
        a.port = 4150 if a.backend == 'autopilot' else sr.DEFAULT_PORT
    network = connection_setting(ap, 'network', '--net', a.net)
    try:
        net = ipaddress.ip_network(network)
    except ValueError:
        ap.error('network must be a CIDR range')
    preferred = a.prefer
    if preferred is None:
        configured_host = connection_setting(ap, 'host', '--prefer', required=False)
        preferred = [configured_host] if configured_host else []
    hosts = preferred + [str(h) for h in net.hosts() if str(h) not in preferred]
    token = sr.read_token()
    deadline = time.time() + a.until_found
    while True:
        open_ips = asyncio.run(scan(hosts, a.port))
        found = None
        for ip in open_ips:
            ok, status = confirm(ip, a.port, token, a.backend)
            if ok:
                found = (ip, status)
                break
        stamp = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        if found:
            ip, status = found
            print(json.dumps({'time': stamp, 'found': ip, 'backend': a.backend, 'app': status.get('app'), 'gate': status.get('gate'),
                              'version': status.get('version')}), flush=True)
            return 0
        print(json.dumps({'time': stamp, 'found': None, 'open_ports': open_ips}), flush=True)
        if time.time() + a.every > deadline:
            return 1
        time.sleep(a.every)


if __name__ == '__main__':
    sys.exit(main())
