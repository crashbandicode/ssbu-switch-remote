#!/usr/bin/python3
"""Capture Skyline's TCP log during a launch (primary 2026-11-16 ~05:45Z, morning sequence).

Skyline's logger serves TCP 6969 on the console (2023 builds: TcpLogger; 2026: DualLogger = TCP + kernel log). It
reports each plugin LoadModule (success/failure, with the ro error code), "Running `main` for …" and "Finished running
`main` for …". Start this just BEFORE the launch press; it retries the connection every --retry-s, appends every
received chunk (raw bytes) to the log with a UTC timestamp header, and reconnects after a drop.

  skyline_tcplog.py --label smokeA-r3-1 [--host SWITCH] [--port 6969] [--seconds 600]
      -> tools/host/out/skyline-tcp-<label>.log
Read-only: it only connects and reads.
"""
import argparse
import signal
import socket
import sys
import time
from pathlib import Path

from connection_config import connection_setting

OUT_DIR = Path(__file__).resolve().parent / 'out'


def stamp():
    t = time.time()
    return time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime(t)) + f'.{int(t * 1000) % 1000:03d}Z'


def run(host, port, out, seconds, retry_s=0.5, connect_timeout=1.0):
    stop = {'now': False}
    signal.signal(signal.SIGTERM, lambda *_: stop.update(now=True))
    end = time.time() + seconds
    stats = {'connects': 0, 'bytes': 0}
    with open(out, 'ab') as log:
        log.write(f'### {stamp()} skyline_tcplog start host={host}:{port}\n'.encode())
        log.flush()
        while not stop['now'] and time.time() < end:
            try:
                s = socket.create_connection((host, port), timeout=connect_timeout)
            except OSError:
                time.sleep(retry_s)
                continue
            stats['connects'] += 1
            log.write(f'### {stamp()} connected (#{stats["connects"]})\n'.encode())
            log.flush()
            s.settimeout(0.5)
            try:
                while not stop['now'] and time.time() < end:
                    try:
                        data = s.recv(65536)
                    except socket.timeout:
                        continue
                    if not data:
                        break
                    stats['bytes'] += len(data)
                    log.write(f'### {stamp()} +{len(data)}\n'.encode() + data + b'\n')
                    log.flush()
            except OSError as e:
                log.write(f'### {stamp()} socket error {type(e).__name__}\n'.encode())
            finally:
                s.close()
            log.write(f'### {stamp()} disconnected\n'.encode())
            log.flush()
        log.write(f'### {stamp()} stop connects={stats["connects"]} bytes={stats["bytes"]}\n'.encode())
    return stats


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--label', required=True)
    ap.add_argument('--host', help='Switch address, overrides local config')
    ap.add_argument('--port', type=int, default=6969)
    ap.add_argument('--seconds', type=float, default=600)
    ap.add_argument('--retry-s', type=float, default=0.5)
    ap.add_argument('--out-dir', default=str(OUT_DIR))
    a = ap.parse_args(argv)
    a.host = connection_setting(ap, 'host', '--host', a.host)
    out = Path(a.out_dir) / f'skyline-tcp-{a.label}.log'
    stats = run(a.host, a.port, out, a.seconds, a.retry_s)
    print(f'{out} connects={stats["connects"]} bytes={stats["bytes"]}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
