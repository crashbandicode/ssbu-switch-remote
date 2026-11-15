#!/usr/bin/python3
"""Cut and encode a capture-card recording into a GIF: capd.py's MJPEG Matroska (with a per-frame wall-time
sidecar) or an OBS recording / replay-buffer file (any format GStreamer's decodebin reads; no sidecar, so its
start time is approximate).

The recording's sidecar (`<mkv>.frames.jsonl`: n, pts, wall per frame) ties frames to host wall time,
the same clock the client's input log uses, so a clip can start at an observed event.

  gif.py IN.mkv OUT.gif [--preset docs|evidence] [--fps N] [--width W] [--start-wall T] [--end-wall T]

Presets follow the playbook (docs/GIFS.md): docs = 1280 wide, at most 10 s, at most 15 fps;
evidence = 640 wide, at most 15 fps, at most 10 MB. Frames keep real time (1/fps each).
Encoding is CPU work: run it at nice 19 under the memory/time guard, or on the Yoga/XPS.
"""
import argparse
import json
import sys
from pathlib import Path

PRESETS = {'docs': {'width': 1280, 'fps': 15, 'max_s': 10.0, 'max_bytes': None},
           'evidence': {'width': 640, 'fps': 15, 'max_s': None, 'max_bytes': 10 * 1024 * 1024}}


def load_sidecar(mkv):
    path = Path(str(mkv) + '.frames.jsonl')
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def window_pts(rows, start_wall=None, end_wall=None):
    """(first pts, last pts) in ns of the frames whose wall time lies in [start_wall, end_wall].

    None for an open end. Raises when the window holds no frame."""
    if start_wall is None and end_wall is None:
        return None, None
    chosen = [r for r in rows if (start_wall is None or r['wall'] >= start_wall)
              and (end_wall is None or r['wall'] <= end_wall)]
    if not chosen:
        raise ValueError('no recorded frame in the requested window')
    return chosen[0]['pts'], chosen[-1]['pts']


def pick_frames(pts_list, fps, first=None, last=None):
    """Indexes of decoded frames (pts ascending, ns) for a GIF at `fps` covering [first, last] in real time.

    Output slots lie on a fixed grid of 1/fps from the first frame; each slot shows the newest source frame
    at or before it, so a run of dropped frames repeats the last good one and the clip keeps real time."""
    step = 1e9 / fps
    tol = step / 8  # a source frame this early still counts for the slot
    idx = [i for i, pts in enumerate(pts_list)
           if (first is None or pts >= first) and (last is None or pts <= last)]
    if not idx:
        return []
    t0, t_end = pts_list[idx[0]], pts_list[idx[-1]]
    keep, j, slot = [], 0, 0
    while t0 + slot * step <= t_end + tol:
        t = t0 + slot * step
        while j + 1 < len(idx) and pts_list[idx[j + 1]] <= t + tol:
            j += 1
        keep.append(idx[j])
        slot += 1
    return keep

def decode(mkv, width):
    """(pts ns, PIL RGB image) per frame of the recording, scaled to `width`."""
    import gi
    gi.require_version('Gst', '1.0')
    from gi.repository import Gst
    from PIL import Image
    Gst.init(None)
    pipe = Gst.parse_launch(
        f'filesrc location="{mkv}" ! decodebin ! videoconvert ! videoscale ! '
        f'video/x-raw,format=RGB,width={width},pixel-aspect-ratio=1/1 ! appsink name=s sync=false')
    sink = pipe.get_by_name('s')
    pipe.set_state(Gst.State.PLAYING)
    frames = []
    while True:
        sample = sink.emit('try-pull-sample', 5 * Gst.SECOND)
        if sample is None:
            break
        caps = sample.get_caps().get_structure(0)
        w, h = caps.get_value('width'), caps.get_value('height')
        buf = sample.get_buffer()
        ok, info = buf.map(Gst.MapFlags.READ)
        if ok:
            stride = len(info.data) // h
            frames.append((buf.pts, Image.frombuffer('RGB', (w, h), bytes(info.data), 'raw', 'RGB', stride, 1).copy()))
            buf.unmap(info)
    pipe.set_state(Gst.State.NULL)
    return frames


def write_gif(images, out, fps, colours=128):
    """One shared palette from the first, middle and last frames, no dithering (playbook encode.py's method)."""
    from PIL import Image
    probe = [images[0], images[len(images) // 2], images[-1]]
    w, h = probe[0].size
    sheet = Image.new('RGB', (w, h * len(probe)))
    for i, im in enumerate(probe):
        sheet.paste(im, (0, h * i))
    palette = sheet.quantize(colors=colours, method=Image.Quantize.MEDIANCUT)
    frames = [im.quantize(palette=palette, dither=Image.Dither.NONE) for im in images]
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    frames[0].save(out, save_all=True, append_images=frames[1:], duration=round(1000 / fps), loop=0,
                   optimize=False, disposal=1)


def encode(mkv, out, fps=10, width=640, start_wall=None, end_wall=None, preset=None):
    if preset:
        p = PRESETS[preset]
        width, fps = p['width'], min(fps or p['fps'], p['fps'])
    fps = max(1, min(15, int(fps)))
    first, last = window_pts(load_sidecar(mkv), start_wall, end_wall)
    decoded = decode(mkv, width)
    if not decoded:
        raise RuntimeError(f'no frames decoded from {mkv}')
    keep = pick_frames([pts for pts, _ in decoded], fps, first, last)
    images = [decoded[i][1] for i in keep]
    if preset and PRESETS[preset]['max_s']:
        images = images[:int(PRESETS[preset]['max_s'] * fps)]
    write_gif(images, out, fps)
    size = Path(out).stat().st_size
    limit = PRESETS[preset]['max_bytes'] if preset else None
    info = {'path': str(out), 'frames': len(images), 'fps': fps, 'width': width, 'bytes': size,
            'seconds': round(len(images) / fps, 2), 'source': str(mkv), 'start_wall': start_wall,
            'end_wall': end_wall}
    if limit and size > limit:
        Path(out).unlink()
        raise RuntimeError(f'{out} would be {size} bytes, over the {preset} limit of {limit}')
    Path(str(out) + '.json').write_text(json.dumps(info, indent=1) + '\n')
    return info


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('mkv')
    ap.add_argument('out')
    ap.add_argument('--preset', choices=sorted(PRESETS))
    ap.add_argument('--fps', type=int, default=10)
    ap.add_argument('--width', type=int, default=640)
    ap.add_argument('--start-wall', type=float)
    ap.add_argument('--end-wall', type=float)
    a = ap.parse_args(argv)
    print(json.dumps(encode(a.mkv, a.out, a.fps, a.width, a.start_wall, a.end_wall, a.preset)))


if __name__ == '__main__':
    sys.exit(main())
