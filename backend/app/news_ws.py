from __future__ import annotations

import asyncio
import logging
from typing import Literal

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from app.access import access_key_ok, auth_required, is_public_websocket, websocket_access_key
from app.fj_read import peek_max_newsid

logger = logging.getLogger(__name__)

NEWS_WATCH_INTERVAL_SECONDS = 15.0
NewsUpdateReason = Literal["fj", "ingest"]

router = APIRouter()
_watch_task: asyncio.Task[None] | None = None
_watch_stop: asyncio.Event | None = None


class NewsUpdatedData(BaseModel):
    reason: NewsUpdateReason


class NewsUpdatedEvent(BaseModel):
    type: Literal["news_updated"] = "news_updated"
    data: NewsUpdatedData


class NewsHub:
    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._clients.add(websocket)

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._clients.discard(websocket)

    async def broadcast(self, event: NewsUpdatedEvent) -> None:
        payload = event.model_dump_json()
        async with self._lock:
            clients = list(self._clients)
        stale: list[WebSocket] = []
        for websocket in clients:
            try:
                await websocket.send_text(payload)
            except Exception:
                stale.append(websocket)
        if stale:
            async with self._lock:
                for websocket in stale:
                    self._clients.discard(websocket)

    def client_count(self) -> int:
        return len(self._clients)


hub = NewsHub()


def news_updated_event(reason: NewsUpdateReason) -> NewsUpdatedEvent:
    return NewsUpdatedEvent(data=NewsUpdatedData(reason=reason))


async def broadcast_news_updated(reason: NewsUpdateReason) -> None:
    await hub.broadcast(news_updated_event(reason))


async def attach_news_socket(websocket: WebSocket) -> None:
    await hub.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await hub.disconnect(websocket)


async def watch_fj_news(
    stop: asyncio.Event,
    *,
    interval: float = NEWS_WATCH_INTERVAL_SECONDS,
) -> None:
    last = 0
    while not stop.is_set():
        current = await asyncio.to_thread(peek_max_newsid)
        if current > last:
            if last > 0:
                logger.info("fj news advanced %s -> %s", last, current)
                await broadcast_news_updated("fj")
            last = current
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except TimeoutError:
            continue


async def start_news_watch() -> None:
    await stop_news_watch()
    stop = asyncio.Event()
    global _watch_stop, _watch_task
    _watch_stop = stop
    _watch_task = asyncio.create_task(watch_fj_news(stop), name="fj-news-watch")


async def stop_news_watch() -> None:
    global _watch_stop, _watch_task
    if _watch_stop is not None:
        _watch_stop.set()
    task = _watch_task
    _watch_task = None
    _watch_stop = None
    if task is None:
        return
    try:
        await asyncio.wait_for(task, timeout=3)
    except TimeoutError:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    except asyncio.CancelledError:
        pass


@router.websocket("/ws/news")
async def news_ws(websocket: WebSocket) -> None:
    if auth_required() and is_public_websocket(websocket):
        if not access_key_ok(websocket_access_key(websocket)):
            await websocket.close(code=4401)
            return
    await attach_news_socket(websocket)
