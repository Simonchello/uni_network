import json
import logging
from pathlib import Path
from threading import Lock
from typing import Dict

from .baseline import BASELINE

log = logging.getLogger(__name__)

Snapshot = Dict[str, Dict[str, int]]


class StatsHistory:
    """Tracks lifetime accumulated traffic per user.

    Handles xray counter resets: if a counter decreases between polls, treat
    the new value as fresh-after-reset and add it as a positive delta.
    Persists state across web-admin restarts in a JSON file. Merges with
    a fixed historical baseline before exposing.
    """

    def __init__(self, state_file: Path) -> None:
        self.state_file = state_file
        self._lock = Lock()
        self._last_seen: Snapshot = {}
        self._accumulated: Snapshot = {}
        self._load()

    def _load(self) -> None:
        try:
            data = json.loads(self.state_file.read_text())
            self._last_seen = data.get("last_seen", {})
            self._accumulated = data.get("accumulated", {})
            log.info("stats history loaded: %d users", len(self._accumulated))
        except FileNotFoundError:
            log.info("stats history empty (no state file at %s)", self.state_file)
        except Exception:
            log.exception("failed to load stats history; starting fresh")
            self._last_seen = {}
            self._accumulated = {}

    def _save(self) -> None:
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.state_file.with_suffix(".tmp")
            tmp.write_text(json.dumps({
                "last_seen": self._last_seen,
                "accumulated": self._accumulated,
            }))
            tmp.replace(self.state_file)
        except Exception:
            log.exception("failed to save stats history")

    def merge(self, current: Snapshot) -> Snapshot:
        """Update internal state with the current xray snapshot, return
        cumulative totals (baseline + tracked accumulator) per user."""
        with self._lock:
            for email, cur in current.items():
                last = self._last_seen.get(email, {"uplink": 0, "downlink": 0})
                acc = self._accumulated.get(email, {"uplink": 0, "downlink": 0})
                for direction in ("uplink", "downlink"):
                    c = int(cur.get(direction, 0))
                    l = int(last.get(direction, 0))
                    delta = c - l if c >= l else c
                    acc[direction] = int(acc.get(direction, 0)) + delta
                self._accumulated[email] = acc
                self._last_seen[email] = {
                    "uplink": int(cur.get("uplink", 0)),
                    "downlink": int(cur.get("downlink", 0)),
                }
            self._save()

            result: Snapshot = {}
            for email in set(BASELINE) | set(self._accumulated):
                base = BASELINE.get(email, {"uplink": 0, "downlink": 0})
                acc = self._accumulated.get(email, {"uplink": 0, "downlink": 0})
                result[email] = {
                    "uplink": int(base["uplink"]) + int(acc.get("uplink", 0)),
                    "downlink": int(base["downlink"]) + int(acc.get("downlink", 0)),
                }
            return result
