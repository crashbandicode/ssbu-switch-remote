"""Per-feature OBS clips for an interactive hardware driver (one OBS recording, split into a new file per feature).

  feature_clip.py start                 start the session recording (waits until OBS is really recording)
  feature_clip.py split LABEL           split here: the file that starts now belongs to feature LABEL (prints its path
                                        once OBS reports it; the previous file is closed)
  feature_clip.py stop                  stop the recording (waits until the last file is finalized)
  feature_clip.py status                OBS record status

Uses switch-remote's client (recording.session_start / session_split / session_stop: start waits for outputActive,
split waits for RecordFileChanged, stop waits for a stable file; 500/501 reconciled). OBS must have manual file
splitting on (Settings > Output > Recording > Automatic File Splitting: Only split manually); `obsws.py split-check`.
Every call appends a line to --log (default ./feature-clips.jsonl): {event, label, wall, result}. Run with
/usr/bin/python3 -I. Never changes OBS settings.
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('cmd', choices=['start', 'split', 'stop', 'status'])
    ap.add_argument('label', nargs='?')
    ap.add_argument('--log', default='feature-clips.jsonl')
    ap.add_argument('--host', default='usb:///dev/ttyACM0')
    a = ap.parse_args(argv)
    import switch_remote
    c = switch_remote.make_client(a.host)
    method = {'start': 'recording.session_start', 'split': 'recording.session_split',
              'stop': 'recording.session_stop', 'status': 'status'}[a.cmd]
    params = {'label': a.label} if a.cmd == 'split' else {}
    if a.cmd == 'split' and not a.label:
        ap.error('split needs a LABEL (the feature id)')
    r = c.request(method, params)
    rec = {'event': a.cmd, 'label': a.label, 'wall': time.time(), 'result': r}
    with open(a.log, 'a') as f:
        f.write(json.dumps(rec) + '\n')
    print(json.dumps(rec))
    return 0 if (not isinstance(r, dict) or r.get('ok', True)) else 1


if __name__ == '__main__':
    sys.exit(main())
