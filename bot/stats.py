"""Xray stats API querying and formatting helpers."""
import json
import subprocess
from typing import Dict

from config import XRAY_STATS_SERVER


def query_user_stats() -> Dict[str, Dict[str, int]]:
    """
    Query Xray stats API.
    Returns {email: {"uplink": bytes, "downlink": bytes}}.
    """
    result = subprocess.run(
        ["xray", "api", "statsquery", f"--server={XRAY_STATS_SERVER}"],
        capture_output=True,
        text=True,
        timeout=10,
    )
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
        # Format: user>>>EMAIL>>>traffic>>>uplink|downlink
        parts = name.split(">>>")
        if len(parts) == 4 and parts[0] == "user" and parts[2] == "traffic":
            email = parts[1]
            direction = parts[3]
            if email not in stats:
                stats[email] = {"uplink": 0, "downlink": 0}
            if direction in ("uplink", "downlink"):
                stats[email][direction] = value
    return stats


def format_bytes(b: int) -> str:
    size = float(b)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024:
            return f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} PB"
