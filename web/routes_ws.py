import asyncio
import json
import logging
import time

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect, status

from .auth import authorize_ws_token
from .config import Settings, get_settings

log = logging.getLogger(__name__)
router = APIRouter()

HEARTBEAT_INTERVAL_SEC = 30.0


@router.websocket("/ws/stats")
async def ws_stats(
    ws: WebSocket,
    token: str | None = Query(default=None),
    settings: Settings = Depends(get_settings),
) -> None:
    try:
        authorize_ws_token(token, settings)
    except Exception:
        await ws.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await ws.accept()
    poller = ws.app.state.poller
    queue = poller.subscribe()

    sender = asyncio.create_task(_sender_loop(ws, queue), name="ws-sender")
    heartbeat = asyncio.create_task(_heartbeat_loop(ws), name="ws-heartbeat")
    reader = asyncio.create_task(_reader_loop(ws), name="ws-reader")

    done, pending = await asyncio.wait(
        [sender, heartbeat, reader],
        return_when=asyncio.FIRST_COMPLETED,
    )
    for task in pending:
        task.cancel()
    poller.unsubscribe(queue)
    for task in done:
        exc = task.exception()
        if exc and not isinstance(exc, (WebSocketDisconnect, asyncio.CancelledError)):
            log.warning("ws task %s ended with: %s", task.get_name(), exc)


async def _sender_loop(ws: WebSocket, queue: asyncio.Queue) -> None:
    while True:
        payload = await queue.get()
        await ws.send_text(json.dumps({"type": "snapshot", **payload}))


async def _heartbeat_loop(ws: WebSocket) -> None:
    while True:
        await asyncio.sleep(HEARTBEAT_INTERVAL_SEC)
        await ws.send_text(json.dumps({"type": "ping", "ts": time.time()}))


async def _reader_loop(ws: WebSocket) -> None:
    while True:
        raw = await ws.receive_text()
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if msg.get("type") == "pong":
            continue
