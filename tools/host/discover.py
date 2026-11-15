#!/usr/bin/python3
"""Find the switch-remote sysmodule on the LAN: probe TCP <port> on every address of a /24, then confirm with
an authenticated PING. Light: one connect attempt per address per round, 0.4 s timeout, 64 at a time.

  discover.py [--net 172.30.254.0/24] [--port 47130] [--prefer 172.30.254.92] [--every 60] [--until-found SECONDS]

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


def confirm(ip, port, token):
    c = sr.SwitchClient(ip, port, token, capture_backend='capd', timeout=3)
    r = c.request('status')
    c.conn.close()
    res = r.get('result') or {}
    return r.get('ok') and res.get('state') != 'unreachable', res


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--net', default='172.30.254.0/24')
    ap.add_argument('--port', type=int, default=sr.DEFAULT_PORT)
    ap.add_argument('--prefer', action='append', default=['172.30.254.92'])
    ap.add_argument('--every', type=float, default=60)
    ap.add_argument('--until-found', type=float, default=0, help='keep polling this many seconds (0: one round)')
    a = ap.parse_args(argv)
    token = sr.read_token()
    net = ipaddress.ip_network(a.net)
    hosts = a.prefer + [str(h) for h in net.hosts() if str(h) not in a.prefer]
    deadline = time.time() + a.until_found
    while True:
        open_ips = asyncio.run(scan(hosts, a.port))
        found = None
        for ip in open_ips:
            ok, status = confirm(ip, a.port, token)
            if ok:
                found = (ip, status)
                break
        stamp = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        if found:
            ip, status = found
            print(json.dumps({'time': stamp, 'found': ip, 'app': status.get('app'), 'gate': status.get('gate'),
                              'version': status.get('version')}), flush=True)
            return 0
        print(json.dumps({'time': stamp, 'found': None, 'open_ports': open_ips}), flush=True)
        if time.time() + a.every > deadline:
            return 1
        time.sleep(a.every)


if __name__ == '__main__':
    sys.exit(main())
