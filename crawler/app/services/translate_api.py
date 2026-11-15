"""本机翻译 HTTP 接口。标题回填线程与外部调用共用腾讯云配额。"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from loguru import logger

from app.config import get_settings
from app.services.translate_tmt import TranslateRequestError, main as run_title_worker, normalize_texts, translate_texts

MAX_BODY_BYTES = 1024 * 1024


def api_body(code: int, message: str, data: Any) -> dict[str, Any]:
    return {"code": code, "message": message, "data": data}


def _write_json(handler: BaseHTTPRequestHandler, http_status: int, payload: dict[str, Any]) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(http_status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


class TranslateHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        logger.info("translate_api: " + format, *args)

    def do_GET(self) -> None:
        if self.path.split("?", 1)[0] != "/health":
            _write_json(self, 200, api_body(404, "接口不存在", None))
            return
        _write_json(self, 200, api_body(200, "ok", {"status": "ok"}))

    def do_POST(self) -> None:
        if self.path.split("?", 1)[0] != "/translate":
            _write_json(self, 200, api_body(404, "接口不存在", None))
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            _write_json(self, 200, api_body(400, "Content-Length 无效", None))
            return
        if length <= 0 or length > MAX_BODY_BYTES:
            _write_json(self, 200, api_body(400, "请求体过大或为空", None))
            return
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            _write_json(self, 200, api_body(400, "JSON 无法解析", None))
            return
        if not isinstance(payload, dict):
            _write_json(self, 200, api_body(400, "JSON 必须是对象", None))
            return
        try:
            texts = normalize_texts(payload.get("q"))
        except TranslateRequestError as exc:
            _write_json(self, 200, api_body(400, str(exc), None))
            return
        source = str(payload.get("source") or "en").strip() or "en"
        target = str(payload.get("target") or "zh").strip() or "zh"
        try:
            translated = translate_texts(texts, source=source, target=target)
        except Exception:
            logger.exception("translate_api: tmt failed")
            _write_json(self, 500, api_body(500, "翻译服务暂时不可用", None))
            return
        items = [
            {"text": src, "translated": dst}
            for src, dst in zip(texts, translated, strict=True)
        ]
        _write_json(
            self,
            200,
            api_body(200, "ok", {"source": source, "target": target, "items": items}),
        )


def main() -> None:
    settings = get_settings()
    worker = threading.Thread(target=run_title_worker, name="title-zh-worker", daemon=True)
    worker.start()
    server = ThreadingHTTPServer((settings.tmt_api_host, settings.tmt_api_port), TranslateHandler)
    logger.info(
        "translate_api: listening on http://{}:{}",
        settings.tmt_api_host,
        settings.tmt_api_port,
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
