#!/usr/bin/python3
"""switch-remote capture daemon: screenshots and recordings from the HDMI capture card.

The Switch's HDMI goes into a USB capture card (on ubuntu-surface: AVerMedia "Live Gamer Ultra 2.1",
/dev/video4, UVC). One GStreamer pipeline keeps the card open and holds the latest MJPEG frame in
memory, so a screenshot costs a JPEG decode, not a pipeline start (a cold one-shot grab took 730 ms,
evidence/capture-probe/). A recording is a stream copy of the card's MJPEG into Matroska (about 1% of
one CPU, measured 2026-11-15) plus a sidecar with each frame's host wall time, so a clip can be cut at
an event the input timeline logged. GIFs are encoded later (gif.py), preferably on another host.

Control: JSON lines over a Unix socket, the same envelope as Eden's control socket
({"id","method","params"} -> {"id","ok","result"|"error"}):

  ping                                   -> {"pong": true, "frames": n, "last_frame_age_ms": ms}
  ui.screenshot {"path", "width"?}       -> {"path", "width", "height", "uniform", "frame_time"}
  recording.start {"path", "duration_ms"?} -> {"accepted": true, "started_wall": t}
  recording.stop                         -> {"path", "frames", "dropped_frames", "first_wall", "last_wall"}
  recording.status                       -> {"state", "frames", "dropped_frames", "path", "error"}
  status                                 -> {"device", "caps", "frames", "last_frame_age_ms", "signal"}

Usage:
  capd.py serve [--device /dev/video4] [--socket PATH] [--caps CAPS]
  capd.py call METHOD ['{"json":"params"}'] [--socket PATH]
  capd.py grab OUT.png [--device ...]      one-shot, no daemon (slow start)
"""
import argparse
import io
import json
import os
import socket
import socketserver
import sys
import threading
import time
from pathlib import Path

DEFAULT_DEVICE = '/dev/video4'
DEFAULT_CAPS = 'image/jpeg,width=1920,height=1080,framerate=60/1'
DEFAULT_SOCKET = f'/run/user/{os.getuid()}/switch-remote-capd.sock'
# A frame whose pixels have a standard deviation below this is "uniform" (no signal or a black screen).
UNIFORM_STDDEV = 2.0
# A gap between two recorded frames longer than this many nominal frame times counts as dropped frames.
NOMINAL_FPS = 60


def dropped_frames(walls, fps=NOMINAL_FPS):
    """Frames missing from a run of arrival times: each gap of k nominal intervals hides k-1 frames.

    Gaps are rounded, so arrival jitter under half a frame time is not counted."""
    period = 1.0 / fps
    missing = 0
    for a, b in zip(walls, walls[1:]):
        k = round((b - a) / period)
        if k > 1:
            missing += k - 1
    return missing


NO_SIGNAL_REF = Path(__file__).resolve().parent / 'refs' / 'avermedia-no-signal-64x36.png'
# Mean absolute difference (0-255) under which a frame is the card's own "No Signal" screen.
NO_SIGNAL_TOLERANCE = 12.0
_no_signal_ref = None


def thumb(im):
    from PIL import Image
    return im.convert('L').resize((64, 36), Image.BILINEAR)


def is_no_signal(small):
    """True when a 64x36 greyscale thumbnail is the capture card's "No Signal" screen
    (AVerMedia Live Gamer Ultra 2.1 shows it after about 1.3 s without HDMI; evidence/capture-probe/)."""
    global _no_signal_ref
    from PIL import Image, ImageChops, ImageStat
    if _no_signal_ref is None:
        ref = Image.open(NO_SIGNAL_REF).convert('L')
        # Compare where the screen has its text and frame (bright in the reference) and over the whole frame;
        # a black frame matches the dark background but not the text.
        _no_signal_ref = (ref, ref.point(lambda v: 255 if v > 80 else 0))
    ref, mask = _no_signal_ref
    diff = ImageChops.difference(small, ref)
    return (ImageStat.Stat(diff, mask).mean[0] < 4 * NO_SIGNAL_TOLERANCE
            and ImageStat.Stat(diff).mean[0] < NO_SIGNAL_TOLERANCE)


def frame_stats(jpeg_bytes):
    """(width, height, uniform, no_signal) of a JPEG frame."""
    from PIL import Image, ImageStat
    with Image.open(io.BytesIO(jpeg_bytes)) as im:
        size = im.size
        im.draft('L', (im.width // 8, im.height // 8))  # cheap: DCT-scaled greyscale
        small = thumb(im)
    uniform = ImageStat.Stat(small).stddev[0] < UNIFORM_STDDEV
    return size[0], size[1], uniform, is_no_signal(small)


def write_png(jpeg_bytes, path, width=None):
    """Decode a JPEG frame and save it as PNG, optionally scaled to `width` (aspect kept)."""
    from PIL import Image
    with Image.open(io.BytesIO(jpeg_bytes)) as im:
        im = im.convert('RGB')
        if width and width != im.width:
            im = im.resize((width, round(im.height * width / im.width)), Image.LANCZOS)
        tmp = Path(str(path) + '.part')
        im.save(tmp, 'PNG')
        os.replace(tmp, path)
        return im.width, im.height


class Capture:
    """The card pipeline: v4l2src -> caps -> tee -> appsink (latest frame) [+ a recording branch]."""

    def __init__(self, device, caps):
        import gi
        gi.require_version('Gst', '1.0')
        from gi.repository import Gst
        Gst.init(None)
        self.Gst = Gst
        self.device, self.caps = device, caps
        self.lock = threading.Lock()
        self.latest = None          # (jpeg bytes, wall time)
        self.frames = 0
        self.rec = None             # dict while recording
        self.rec_lock = threading.Lock()
        desc = (f'v4l2src device={device} do-timestamp=true ! {caps} ! tee name=t '
                't. ! queue leaky=downstream max-size-buffers=2 ! appsink name=latest emit-signals=true '
                'max-buffers=1 drop=true sync=false')
        self.pipeline = Gst.parse_launch(desc)
        self.tee = self.pipeline.get_by_name('t')
        self.pipeline.get_by_name('latest').connect('new-sample', self._on_sample)
        self.pipeline.set_state(Gst.State.PLAYING)

    def _on_sample(self, sink):
        sample = sink.emit('pull-sample')
        buf = sample.get_buffer()
        ok, info = buf.map(self.Gst.MapFlags.READ)
        if ok:
            data = bytes(info.data)
            buf.unmap(info)
            with self.lock:
                self.latest = (data, time.time())
                self.frames += 1
        return self.Gst.FlowReturn.OK

    def latest_frame(self, max_age=1.0, wait=2.0):
        until = time.monotonic() + wait
        while True:
            with self.lock:
                latest = self.latest
            if latest and time.time() - latest[1] <= max_age:
                return latest
            if time.monotonic() > until:
                raise RuntimeError('no frame from the capture card '
                                   f'(last {"never" if not latest else f"{time.time() - latest[1]:.1f}s ago"})')
            time.sleep(0.01)

    # --- recording branch ---------------------------------------------------------------------------------------
    def start_recording(self, path, duration_ms=None):
        Gst = self.Gst
        with self.rec_lock:
            if self.rec:
                raise RuntimeError('a recording is already running')
            path = Path(path)
            if path.exists():
                raise RuntimeError(f'{path} exists')
            path.parent.mkdir(parents=True, exist_ok=True)
            bin_ = Gst.parse_bin_from_description(
                f'queue name=rq max-size-buffers=0 max-size-time=0 max-size-bytes=268435456 ! matroskamux ! '
                f'filesink name=rf location={path}', True)
            walls = []
            sidecar = open(str(path) + '.frames.jsonl', 'w')

            def probe(pad, info):
                buf = info.get_buffer()
                now = time.time()
                walls.append(now)
                sidecar.write(json.dumps({'n': len(walls) - 1, 'pts': buf.pts, 'wall': now}) + '\n')
                return Gst.PadProbeReturn.OK

            bin_.get_by_name('rq').get_static_pad('sink').add_probe(Gst.PadProbeType.BUFFER, probe)
            self.pipeline.add(bin_)
            tee_pad = self.tee.request_pad_simple('src_%u')
            tee_pad.link(bin_.get_static_pad('sink'))
            bin_.sync_state_with_parent()
            self.rec = {'path': str(path), 'bin': bin_, 'tee_pad': tee_pad, 'walls': walls, 'sidecar': sidecar,
                        'started': time.time(), 'error': None, 'timer': None}
            if duration_ms:
                t = threading.Timer(duration_ms / 1000, self._auto_stop)
                t.daemon = True
                t.start()
                self.rec['timer'] = t
            return {'accepted': True, 'started_wall': self.rec['started'], 'path': str(path)}

    def _auto_stop(self):
        try:
            self.stop_recording()
        except RuntimeError:
            pass

    def stop_recording(self):
        Gst = self.Gst
        with self.rec_lock:
            rec = self.rec
            if not rec:
                raise RuntimeError('no recording is running')
            if rec['timer']:
                rec['timer'].cancel()
            tee_pad, bin_ = rec['tee_pad'], rec['bin']
            done = threading.Event()

            def unlink(pad, info):
                pad.unlink(bin_.get_static_pad('sink'))
                bin_.get_static_pad('sink').send_event(Gst.Event.new_eos())
                done.set()
                return Gst.PadProbeReturn.REMOVE

            tee_pad.add_probe(Gst.PadProbeType.IDLE, unlink)
            done.wait(2)
            # Wait for the muxer to finish the file (EOS reaches the filesink).
            bus_deadline = time.monotonic() + 5
            sink = bin_.get_by_name('rf')
            while time.monotonic() < bus_deadline:
                pad = sink.get_static_pad('sink')
                if pad.get_sticky_event(Gst.EventType.EOS, 0):
                    break
                time.sleep(0.05)
            bin_.set_state(Gst.State.NULL)
            self.pipeline.remove(bin_)
            self.tee.release_request_pad(tee_pad)
            rec['sidecar'].close()
            walls = rec['walls']
            self.rec = None
            return {'path': rec['path'], 'frames': len(walls), 'dropped_frames': dropped_frames(walls),
                    'first_wall': walls[0] if walls else None, 'last_wall': walls[-1] if walls else None,
                    'frames_file': rec['path'] + '.frames.jsonl'}

    def recording_status(self):
        with self.rec_lock:
            rec = self.rec
            if not rec:
                return {'state': 'idle'}
            walls = list(rec['walls'])
            return {'state': 'recording', 'path': rec['path'], 'frames': len(walls),
                    'dropped_frames': dropped_frames(walls), 'error': rec['error']}

    # --- methods ------------------------------------------------------------------------------------------------
    def handle(self, method, params):
        if method == 'ping':
            with self.lock:
                age = None if not self.latest else round((time.time() - self.latest[1]) * 1000)
            return {'pong': True, 'frames': self.frames, 'last_frame_age_ms': age}
        if method == 'status':
            with self.lock:
                latest = self.latest
            signal = None
            if latest:
                _, _, uniform, no_signal = frame_stats(latest[0])
                signal = not (uniform or no_signal)
            return {'device': self.device, 'caps': self.caps, 'frames': self.frames,
                    'last_frame_age_ms': None if not latest else round((time.time() - latest[1]) * 1000),
                    'signal': signal, 'recording': self.recording_status()}
        if method == 'ui.screenshot':
            data, wall = self.latest_frame()
            path = params.get('path')
            if not path:
                raise RuntimeError('path is required')
            w, h = write_png(data, path, params.get('width'))
            _, _, uniform, no_signal = frame_stats(data)
            return {'path': path, 'width': w, 'height': h, 'uniform': uniform, 'no_signal': no_signal,
                    'frame_time': wall, 'backend': 'capture-card'}
        if method == 'recording.start':
            return self.start_recording(params['path'], params.get('duration_ms'))
        if method == 'recording.stop':
            return self.stop_recording()
        if method == 'recording.status':
            return self.recording_status()
        raise KeyError(method)


def serve(args):
    cap = Capture(args.device, args.caps)
    path = Path(args.socket)
    if path.exists():
        try:
            socket.socket(socket.AF_UNIX).connect(str(path))
            raise SystemExit(f'{path} is live: another capd is running')
        except ConnectionRefusedError:
            path.unlink()

    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            for line in self.rfile:
                try:
                    req = json.loads(line)
                    rid = req.get('id')
                except ValueError:
                    self.wfile.write(b'{"ok":false,"error":{"code":"bad_json"}}\n')
                    continue
                try:
                    result = cap.handle(req.get('method'), req.get('params') or {})
                    reply = {'id': rid, 'ok': True, 'result': result}
                except KeyError as e:
                    reply = {'id': rid, 'ok': False, 'error': {'code': 'unknown_method', 'message': str(e)}}
                except Exception as e:  # noqa: BLE001 - every failure goes back to the caller
                    reply = {'id': rid, 'ok': False, 'error': {'code': 'failed', 'message': str(e)}}
                self.wfile.write(json.dumps(reply).encode() + b'\n')
                self.wfile.flush()

    class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
        daemon_threads = True

    server = Server(str(path), Handler)
    os.chmod(path, 0o600)
    print(json.dumps({'serving': str(path), 'device': args.device, 'caps': args.caps}), flush=True)
    try:
        server.serve_forever()
    finally:
        path.unlink(missing_ok=True)


def call(socket_path, method, params=None, timeout=15):
    with socket.socket(socket.AF_UNIX) as s:
        s.settimeout(timeout)
        s.connect(socket_path)
        s.sendall(json.dumps({'id': 1, 'method': method, 'params': params or {}}).encode() + b'\n')
        data = b''
        while not data.endswith(b'\n'):
            chunk = s.recv(65536)
            if not chunk:
                break
            data += chunk
    return json.loads(data)


def grab(args):
    import gi
    gi.require_version('Gst', '1.0')
    from gi.repository import Gst
    Gst.init(None)
    start = time.monotonic()
    pipe = Gst.parse_launch(f'v4l2src device={args.device} num-buffers=3 ! {args.caps} ! appsink name=s sync=false')
    sink = pipe.get_by_name('s')
    pipe.set_state(Gst.State.PLAYING)
    data = None
    for _ in range(3):
        sample = sink.emit('try-pull-sample', 5 * Gst.SECOND)
        if sample is None:
            break
        buf = sample.get_buffer()
        ok, info = buf.map(Gst.MapFlags.READ)
        data = bytes(info.data)
        buf.unmap(info)
    pipe.set_state(Gst.State.NULL)
    if data is None:
        raise SystemExit('no frame from the capture card')
    w, h = write_png(data, args.out, args.width)
    _, _, uniform, no_signal = frame_stats(data)
    print(json.dumps({'path': args.out, 'width': w, 'height': h, 'uniform': uniform, 'no_signal': no_signal,
                      'ms': round((time.monotonic() - start) * 1000)}))


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest='cmd', required=True)
    s = sub.add_parser('serve')
    s.add_argument('--device', default=DEFAULT_DEVICE)
    s.add_argument('--caps', default=DEFAULT_CAPS)
    s.add_argument('--socket', default=DEFAULT_SOCKET)
    c = sub.add_parser('call')
    c.add_argument('method')
    c.add_argument('params', nargs='?', default='{}')
    c.add_argument('--socket', default=DEFAULT_SOCKET)
    g = sub.add_parser('grab')
    g.add_argument('out')
    g.add_argument('--device', default=DEFAULT_DEVICE)
    g.add_argument('--caps', default=DEFAULT_CAPS)
    g.add_argument('--width', type=int)
    args = p.parse_args(argv)
    if args.cmd == 'serve':
        serve(args)
    elif args.cmd == 'call':
        print(json.dumps(call(args.socket, args.method, json.loads(args.params))))
    else:
        grab(args)


if __name__ == '__main__':
    sys.exit(main())
