import asyncio
import json
import logging
import random
import subprocess
import time
from typing import Dict, List, Optional

log = logging.getLogger(__name__)

Snapshot = Dict[str, Dict[str, int]]


def _query_xray(server: str = "127.0.0.1:10085", timeout: int = 10) -> Snapshot:
    try:
        result = subprocess.run(
            ["xray", "api", "statsquery", f"--server={server}"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        log.warning("xray subprocess failed: %s", e)
        return {}

    if result.returncode != 0:
        return {}

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {}

    stats: Snapshot = {}
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


class _MockGenerator:
    """Monotonically growing fake counters for local dev."""

    def __init__(self) -> None:
        self._users = ["alice", "bob", "carol", "dave", "eve"]
        self._state: Snapshot = {
            u: {"uplink": random.randint(10_000_000, 500_000_000),
                "downlink": random.randint(100_000_000, 5_000_000_000)}
            for u in self._users
        }

    def tick(self) -> Snapshot:
        for user, row in self._state.items():
            row["uplink"] += random.randint(1_000, 500_000)
            row["downlink"] += random.randint(10_000, 5_000_000)
        return {u: dict(v) for u, v in self._state.items()}


class StatsPoller:
    """Single background poller → pub-sub broadcasting to WS subscribers.

    Why single poller: avoid spawning an xray subprocess per WS client.
    """

    def __init__(self, interval_sec: float = 2.0, mock: bool = False) -> None:
        self.interval = interval_sec
        self.mock = mock
        self._snapshot: Snapshot = {}
        self._last_ts: float = 0.0
        self._task: Optional[asyncio.Task] = None
        self._subscribers: List[asyncio.Queue] = []
        self._stopped = asyncio.Event()
        self._mock_gen = _MockGenerator() if mock else None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="stats-poller")

    async def stop(self) -> None:
        self._stopped.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    def latest(self) -> dict:
        return {"snapshot": self._snapshot, "ts": self._last_ts, "mock": self.mock}

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=8)
        self._subscribers.append(q)
        if self._snapshot:
            q.put_nowait(self.latest())
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        try:
            self._subscribers.remove(q)
        except ValueError:
            pass

    async def _run(self) -> None:
        log.info("StatsPoller started (mock=%s, interval=%ss)", self.mock, self.interval)
        while not self._stopped.is_set():
            try:
                if self.mock and self._mock_gen is not None:
                    snap = self._mock_gen.tick()
                else:
                    snap = await asyncio.to_thread(_query_xray)
                self._snapshot = snap
                self._last_ts = time.time()
                await self._broadcast()
            except Exception as e:
                log.exception("poller iteration failed: %s", e)
            try:
                await asyncio.wait_for(self._stopped.wait(), timeout=self.interval)
            except asyncio.TimeoutError:
                pass

    async def _broadcast(self) -> None:
        msg = self.latest()
        dead: List[asyncio.Queue] = []
        for q in self._subscribers:
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                dead.append(q)
        for q in dead:
            self.unsubscribe(q)
