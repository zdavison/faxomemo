"""Persistent record of when each channel last ran a scheduled print.

Survives restarts so the device can tell a schedule it slept through from
one it already printed.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Dict

logger = logging.getLogger(__name__)

STATE_FILENAME = ".schedule_state.json"


def _state_path() -> str:
    import app.config as config_module

    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(config_module.__file__)))
    return os.path.join(base_dir, STATE_FILENAME)


def load_last_fired() -> Dict[int, datetime]:
    """Map of channel position to the last scheduled slot it printed."""
    path = _state_path()
    try:
        with open(path) as state_file:
            raw = json.load(state_file)
    except FileNotFoundError:
        return {}
    except Exception:
        logger.warning("Unreadable schedule state at %s; ignoring", path, exc_info=True)
        return {}

    entries = raw.get("last_fired") if isinstance(raw, dict) else None
    if not isinstance(entries, dict):
        return {}

    last_fired: Dict[int, datetime] = {}
    for position, timestamp in entries.items():
        try:
            last_fired[int(position)] = datetime.fromisoformat(str(timestamp))
        except Exception:
            logger.warning("Discarding malformed schedule state entry %r", position)
    return last_fired


def record_fired(position: int, fired_at: datetime) -> None:
    """Remember that ``position`` printed the scheduled slot ``fired_at``."""
    last_fired = load_last_fired()
    last_fired[int(position)] = fired_at

    payload = {
        "last_fired": {
            str(pos): moment.isoformat() for pos, moment in last_fired.items()
        }
    }

    path = _state_path()
    temp_path = f"{path}.tmp"
    try:
        with open(temp_path, "w") as state_file:
            json.dump(payload, state_file, indent=2)
            state_file.flush()
            os.fsync(state_file.fileno())
        os.replace(temp_path, path)
    except Exception:
        logger.warning("Failed to persist schedule state to %s", path, exc_info=True)
        try:
            os.remove(temp_path)
        except OSError:
            pass
