"""One hardware-ledger step at Eden speed: pad inputs back to back over the ESP32 UART while an OBS screenshot burst
records the screen; writes a GIF (640 wide, <=15 fps, <=10 MB), optional gate stills, and a receipt.

  hwstep.py --name F01-foo --out DIR [--fps 10] [--lead-s 0.5] [--tail-s 2] [--no-gif] -- TOKEN ...

TOKENs, in order:
  sleep:S          wait S seconds (the pad is pinged every 0.4 s, so a long `press` lease is not cut by the 1 s watchdog)
  still:LABEL      a full-size PNG still now: DIR/NAME-LABEL.png (a gate)
  anything else    one pad line, e.g. `press a 100`, `press zl+up 120`, `stick 0 -1 300`, `clear`
Receipt: DIR/receipts.jsonl (one line per step). Run with /usr/bin/python3 -I.
"""
import argparse
import base64
import io
import json
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

MAX_GIF = 10 * 1024 * 1024


def grab_loop(stop, frames, fps, width, errors):
    import obsws
    try:
        client = obsws.ObsClient()
        source = obsws.ObsCapture(client).source
    except Exception as e:  # noqa: BLE001
        errors.append(f'obs connect: {e}')
        return
    period = 1.0 / fps
    nxt = time.monotonic()
    while not stop.is_set():
        try:
            d = client.request('GetSourceScreenshot', {'sourceName': source, 'imageFormat': 'jpg',
                                                       'imageWidth': width, 'imageCompressionQuality': 85})
            frames.append((time.time(), base64.b64decode(d['imageData'].split(',', 1)[1])))
        except Exception as e:  # noqa: BLE001
            errors.append(f'shot: {e}')
        nxt += period
        delay = nxt - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        else:
            nxt = time.monotonic()
    client.close()


def build_gif(frames, path, fps):
    from PIL import Image
    if not frames:
        return None
    imgs, durs = [], []
    for i, (t, raw) in enumerate(frames):
        im = Image.open(io.BytesIO(raw)).convert('RGB')
        imgs.append(im.quantize(colors=128, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE))
        nxt = frames[i + 1][0] if i + 1 < len(frames) else t + 1.0 / fps
        durs.append(max(int(round((nxt - t) * 1000)), 1000 // 15))  # real timing, never above 15 fps
    step = 1
    while True:
        sel = imgs[::step]
        d = [sum(durs[i:i + step]) for i in range(0, len(durs), step)]
        buf = io.BytesIO()
        sel[0].save(buf, format='GIF', save_all=True, append_images=sel[1:], duration=d, loop=0, optimize=True)
        if buf.tell() <= MAX_GIF or step >= 8:
            path.write_bytes(buf.getvalue())
            return {'frames': len(sel), 'bytes': buf.tell(), 'decimate': step,
                    'seconds': round(sum(d) / 1000, 2)}
        step += 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--name', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--device', default='/dev/ttyACM0')
    ap.add_argument('--fps', type=float, default=10)
    ap.add_argument('--width', type=int, default=640)
    ap.add_argument('--lead-s', type=float, default=0.5)
    ap.add_argument('--tail-s', type=float, default=2.0)
    ap.add_argument('--no-gif', action='store_true')
    ap.add_argument('tokens', nargs='+')
    a = ap.parse_args(argv)
    a.fps = min(a.fps, 15)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    import usbpad
    pad = usbpad.UsbClient(a.device, keepalive_s=0)
    rec = {'name': a.name, 'start': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'steps': [], 'stills': {}}
    stop, frames, errors = threading.Event(), [], []
    th = None
    if not a.no_gif:
        th = threading.Thread(target=grab_loop, args=(stop, frames, a.fps, a.width, errors), daemon=True)
        th.start()
        time.sleep(a.lead_s)

    def still(label):
        p = out / f'{a.name}-{label}.png'
        r = pad.request('ui.screenshot', {'path': str(p)})
        rec['stills'][label] = str(p) if r.get('ok') else f'failed: {r.get("error")}'

    def wait(s):
        end = time.monotonic() + s
        while True:
            left = end - time.monotonic()
            if left <= 0:
                return
            time.sleep(min(0.4, left))
            if end - time.monotonic() > 0.05:
                pad.conn.call('PING')

    try:
        with pad.lock:
            pad.conn.open()
        for tok in a.tokens:
            if tok.startswith('sleep:'):
                wait(float(tok[6:]))
                continue
            if tok.startswith('still:'):
                still(tok[6:])
                continue
            t = time.time()
            try:
                reply = pad.conn.call(tok)
            except Exception as e:  # noqa: BLE001  (an ERR reply is evidence, not a crash)
                reply = f'ERR {e}'
            rec['steps'].append({'t': round(t, 3), 'sent': tok, 'reply': reply})
            if str(reply).startswith('ERR'):
                rec['error'] = f'{tok}: {reply}'
                break
        wait(a.tail_s)
    finally:
        try:
            pad.conn.call('clear')
        except Exception:  # noqa: BLE001
            pass
        stop.set()
        if th:
            th.join(timeout=10)
        pad.conn.close()
    if frames:
        gif = out / f'{a.name}.gif'
        rec['gif'] = str(gif)
        rec['gif_info'] = build_gif(frames, gif, a.fps)
        (out / f'{a.name}-first.jpg').write_bytes(frames[0][1])
        (out / f'{a.name}-last.jpg').write_bytes(frames[-1][1])
    rec['capture_errors'] = errors[:5]
    with open(out / 'receipts.jsonl', 'a') as f:
        f.write(json.dumps(rec) + '\n')
    print(json.dumps({k: rec[k] for k in rec if k != 'steps'} | {'n_steps': len(rec['steps'])}))
    return 1 if rec.get('error') else 0


if __name__ == '__main__':
    sys.exit(main())
