from __future__ import annotations

import hmac

from fastapi import Request, WebSocket
from fastapi.responses import JSONResponse

from app.config import settings

ACCESS_HEADER = "x-access-key"
PUBLIC_ACCESS_HEADER = "x-public-access"
PUBLIC_API_PATHS = {"/api/health", "/api/access/check"}
_PUBLIC_ACCESS_VALUES = {"1", "true", "yes"}


def expected_access_key() -> str:
    return settings.access_key.strip()


def auth_required() -> bool:
    return bool(expected_access_key())


def is_public_request(request: Request) -> bool:
    value = request.headers.get(PUBLIC_ACCESS_HEADER, "").strip().lower()
    return value in _PUBLIC_ACCESS_VALUES


def is_public_websocket(websocket: WebSocket) -> bool:
    value = websocket.headers.get(PUBLIC_ACCESS_HEADER, "").strip().lower()
    return value in _PUBLIC_ACCESS_VALUES


def public_auth_required(request: Request) -> bool:
    return auth_required() and is_public_request(request)


def access_key_ok(provided: str | None) -> bool:
    expected = expected_access_key()
    if not expected:
        return True
    got = (provided or "").strip()
    if len(got) != len(expected):
        return False
    return hmac.compare_digest(got, expected)


def request_access_key(request: Request) -> str:
    header = request.headers.get(ACCESS_HEADER)
    if header:
        return header
    return request.query_params.get("access_key") or ""


def websocket_access_key(websocket: WebSocket) -> str:
    header = websocket.headers.get(ACCESS_HEADER)
    if header:
        return header
    return websocket.query_params.get("access_key") or ""


async def access_key_middleware(request: Request, call_next):
    if not public_auth_required(request):
        return await call_next(request)
    if request.method == "OPTIONS":
        return await call_next(request)
    path = request.url.path
    if not path.startswith("/api/"):
        return await call_next(request)
    if path in PUBLIC_API_PATHS:
        return await call_next(request)
    if access_key_ok(request_access_key(request)):
        return await call_next(request)
    return JSONResponse(
        {"code": 401, "message": "需要访问密钥", "data": None},
        status_code=401,
    )
