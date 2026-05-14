"""Xray stats querying and formatting.

The bot reads lifetime traffic from web-admin's persisted state file
(`/opt/lockdown-web/stats_state.json`) plus the static baseline
(`/opt/lockdown-web/web/baseline.py`). This way bot and web-admin show
the same numbers and survive xray counter resets.

Falls back to a direct xray-stats query if web-admin state is missing.
"""
import importlib.util
import json
import subprocess
from typing import Dict

from config import XRAY_STATS_SERVER

STATE_FILE = "/opt/lockdown-web/stats_state.json"
BASELINE_FILE = "/opt/lockdown-web/web/baseline.py"


def _load_baseline() -> Dict[str, Dict[str, int]]:
    try:
        spec = importlib.util.spec_from_file_location("_lockdown_baseline", BASELINE_FILE)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.BASELINE
    except Exception:
        return {}


def _load_accumulated() -> Dict[str, Dict[str, int]]:
    try:
        with open(STATE_FILE) as f:
            data = json.load(f)
        return data.get("accumulated", {})
    except Exception:
        return {}


def _query_xray_live() -> Dict[str, Dict[str, int]]:
    try:
        result = subprocess.run(
            ["xray", "api", "statsquery", f"--server={XRAY_STATS_SERVER}"],
            capture_output=True, text=True, timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return {}
    if result.returncode != 0:
        return {}
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {}
    stats: Dict[str, Dict[str, int]] = {}
    for stat in data.get("stat", []):
        name = stat.get("name", "")
        value = int(stat.get("value", 0))
        parts = name.split(">>>")
        if len(parts) == 4 and parts[0] == "user" and parts[2] == "traffic":
            email, direction = parts[1], parts[3]
            stats.setdefault(email, {"uplink": 0, "downlink": 0})
            if direction in ("uplink", "downlink"):
                stats[email][direction] = value
    return stats


def query_user_stats() -> Dict[str, Dict[str, int]]:
    """Lifetime traffic per user (baseline + accumulator).

    Reads web-admin's persisted state. Falls back to a live xray query
    if the state files are unavailable.
    """
    baseline = _load_baseline()
    accumulated = _load_accumulated()
    if not baseline and not accumulated:
        return _query_xray_live()

    result: Dict[str, Dict[str, int]] = {}
    for email in set(baseline) | set(accumulated):
        b = baseline.get(email, {"uplink": 0, "downlink": 0})
        a = accumulated.get(email, {"uplink": 0, "downlink": 0})
        result[email] = {
            "uplink": int(b.get("uplink", 0)) + int(a.get("uplink", 0)),
            "downlink": int(b.get("downlink", 0)) + int(a.get("downlink", 0)),
        }
    return result


def format_bytes(b: int) -> str:
    size = float(b)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024:
            return f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} PB"
