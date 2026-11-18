"""Local device settings shared by the host command-line tools."""
import json
import os
from pathlib import Path

DEFAULT_CONNECTION_CONFIG = Path(__file__).resolve().parents[2] / ".switch-remote.json"


def connection_setting(parser, key, option, value=None, *, required=True):
    """Resolve a CLI connection setting with a private local configuration fallback."""
    if value:
        return value
    override = os.environ.get('SWITCH_REMOTE_CONFIG')
    path = Path(override).expanduser() if override else DEFAULT_CONNECTION_CONFIG
    try:
        config = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        if override:
            parser.error('the file selected by SWITCH_REMOTE_CONFIG does not exist')
        config = {}
    except (OSError, UnicodeError, ValueError):
        parser.error('the Switch Remote connection config must be a readable JSON object')
    if not isinstance(config, dict):
        parser.error('the Switch Remote connection config must be a JSON object')
    value = config.get(key)
    if value is not None:
        if not isinstance(value, str) or not value.strip():
            parser.error(f'connection config field {key!r} must be a nonempty string')
        return value.strip()
    if required:
        parser.error(f'pass {option} or set {key!r} in .switch-remote.json')
    return None
