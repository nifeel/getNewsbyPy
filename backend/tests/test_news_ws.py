import asyncio
import json

import pytest
from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient

from app.news_ws import (
    NewsUpdatedEvent,
    attach_news_socket,
    broadcast_news_updated,
    hub,
    news_updated_event,
    watch_fj_news,
)


class DummySocket:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def send_text(self, text: str) -> None:
        self.messages.append(text)


async def test_broadcast_news_updated_json_shape() -> None:
    dummy = DummySocket()
    hub._clients.add(dummy)  # type: ignore[arg-type]
    try:
        await broadcast_news_updated("fj")
        assert dummy.messages
        payload = json.loads(dummy.messages[0])
        assert payload == {"type": "news_updated", "data": {"reason": "fj"}}
        NewsUpdatedEvent.model_validate(payload)
    finally:
        hub._clients.discard(dummy)  # type: ignore[arg-type]


async def test_ingest_reason_is_distinct() -> None:
    event = news_updated_event("ingest")
    assert event.model_dump() == {"type": "news_updated", "data": {"reason": "ingest"}}


def test_websocket_accepts_and_cleans_up() -> None:
    app = FastAPI()

    @app.websocket("/api/ws/news")
    async def news_ws(websocket: WebSocket) -> None:
        await attach_news_socket(websocket)

    with TestClient(app) as client:
        with client.websocket_connect("/api/ws/news") as socket:
            assert hub.client_count() == 1
            socket.send_text("ping")
        assert hub.client_count() == 0


async def test_watch_broadcasts_after_newsid_increases(monkeypatch: pytest.MonkeyPatch) -> None:
    values = [10, 12]

    def peek() -> int:
        if values:
            return values.pop(0)
        return 12

    monkeypatch.setattr("app.news_ws.peek_max_newsid", peek)
    dummy = DummySocket()
    hub._clients.add(dummy)  # type: ignore[arg-type]
    stop = asyncio.Event()
    try:
        task = asyncio.create_task(watch_fj_news(stop, interval=0.01))
        for _ in range(50):
            if dummy.messages:
                break
            await asyncio.sleep(0.02)
        stop.set()
        await asyncio.wait_for(task, timeout=1)
        assert dummy.messages
        assert json.loads(dummy.messages[0])["data"]["reason"] == "fj"
    finally:
        stop.set()
        hub._clients.discard(dummy)  # type: ignore[arg-type]
