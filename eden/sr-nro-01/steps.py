"""Project step hooks for the switch-remote NRO Eden proof (runner `run` steps).

The diag NRO in Eden reports the scene to 127.0.0.1:<port+1>. Eden's guest sockets are host sockets
(eden bsd.cpp:554,663-675), so the reports reach the sysmodule's host loopback build (the real sr::Service)
running on the Eden host. `scene_gate` asks that Service for STATUS and checks what the gate decided.
"""
import json
import socket
import time
from pathlib import Path


def _status(port, token):
    with socket.create_connection(('127.0.0.1', port), timeout=5) as s:
        f = s.makefile('rwb')
        f.write(f'AUTH {token}\n'.encode())
        f.flush()
        if not f.readline().startswith(b'OK'):
            raise RuntimeError('loopback refused the token')
        f.write(b'STATUS\n')
        f.flush()
        line = f.readline().decode().strip()
    return dict(p.split('=', 1) for p in line.split()[2:] if '=' in p)


def boot_setup(ctx):
    """Muted (core.volume 0), memory_layout_mode 0, saved, then Eden's GDB stub on before game.load (no-hold pattern),
    through the copied rc15 helper gdb_rc11.py (copied into this run folder by queued.ps1, not part of the repo)."""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import gdb_rc11
    out = {}
    for key, value in [('core.volume', 0), ('core.memory_layout_mode', 0)]:
        ctx.call('settings.set', {'key': key, 'value': value})
        out[key] = ctx.call('settings.get', {'key': key})
    ctx.call('settings.save', {})
    out['gdb'] = gdb_rc11.gdb_on(ctx)
    return out


def scene_gate(ctx, port, token_file, expect=None, online=None, cap_s=20):
    """Waits up to cap_s for the loopback Service's gate to read `expect` (e.g. allowed / online_mode).
    expect=None records the gate as it is (a measurement, not an assertion)."""
    token = Path(token_file).read_text().strip()
    deadline = time.time() + cap_s
    last = {}
    while time.time() < deadline:
        last = _status(port, token)
        if expect is None and last.get('scene_age_ms') not in (None, '0'):
            ctx.log({'scene_gate_recorded': last})
            return {'gate': last.get('gate'), 'scene_mode': last.get('scene_mode'), 'recorded_only': True}
        if last.get('gate') == expect:
            mode = int(last.get('scene_mode', '0x0'), 16)
            if online is not None and ((mode & 0x7f000000) == 0x07000000) != online:
                raise AssertionError(f'gate {expect} but mode {hex(mode)} online={online} expected')
            ctx.log({'scene_gate': last})
            return {'gate': last['gate'], 'scene_mode': last.get('scene_mode'),
                    'scene_age_ms': last.get('scene_age_ms'), 'scene_deluxe': last.get('scene_deluxe')}
        time.sleep(0.5)
    raise AssertionError(f'gate never became {expect}: last STATUS {json.dumps(last)}')
