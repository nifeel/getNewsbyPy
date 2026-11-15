import asyncio
import contextlib
from collections.abc import Awaitable, Callable

from loguru import logger
from playwright.async_api import Page, Response, WebSocket, async_playwright

from app.browser.session import ensure_parent_dir, storage_state_exists
from app.config import get_settings


NETWORK_RESOURCE_TYPES = {"xhr", "fetch", "eventsource"}


def compact_text(text: str, limit: int) -> str:
    compacted = " ".join(text.split())
    if len(compacted) <= limit:
        return compacted
    return f"{compacted[:limit]}..."


async def safe_response_preview(response: Response, limit: int) -> str:
    content_type = response.headers.get("content-type", "")
    if not any(kind in content_type.lower() for kind in ("json", "text", "javascript")):
        return ""

    try:
        body = await response.text()
    except Exception as exc:
        return f"<unable to read response body: {exc}>"

    return compact_text(body, limit)


async def handle_response(response: Response, preview_chars: int) -> None:
    request = response.request
    if request.resource_type not in NETWORK_RESOURCE_TYPES:
        return

    content_type = response.headers.get("content-type", "")
    preview = await safe_response_preview(response, preview_chars)

    logger.info(
        "HTTP {} {} {} {}",
        response.status,
        request.resource_type.upper(),
        content_type,
        response.url,
    )
    if preview:
        logger.info("HTTP preview: {}", preview)


def handle_websocket(websocket: WebSocket, preview_chars: int) -> None:
    logger.info("WebSocket opened {}", websocket.url)

    websocket.on(
        "framereceived",
        lambda payload: logger.info(
            "WebSocket received {} {}",
            websocket.url,
            compact_text(str(payload), preview_chars),
        ),
    )
    websocket.on(
        "framesent",
        lambda payload: logger.debug(
            "WebSocket sent {} {}",
            websocket.url,
            compact_text(str(payload), preview_chars),
        ),
    )
    websocket.on("close", lambda: logger.info("WebSocket closed {}", websocket.url))


def schedule(task_factory: Callable[[], Awaitable[None]]) -> None:
    task = asyncio.create_task(task_factory())
    task.add_done_callback(log_task_exception)


def log_task_exception(task: asyncio.Task[None]) -> None:
    with contextlib.suppress(asyncio.CancelledError):
        exc = task.exception()
        if exc:
            logger.warning("Network handler failed: {}", exc)


async def configure_page(page: Page, preview_chars: int) -> None:
    page.on("response", lambda response: schedule(lambda: handle_response(response, preview_chars)))
    page.on("websocket", lambda websocket: handle_websocket(websocket, preview_chars))


async def run_network_sniffer() -> None:
    settings = get_settings()
    storage_state_path = settings.storage_state_file

    if not storage_state_exists(storage_state_path):
        raise FileNotFoundError(
            f"Login state not found: {storage_state_path}. Run `python -m app.browser.login` first."
        )

    ensure_parent_dir(settings.network_log_file)
    logger.add(settings.network_log_file, rotation="10 MB", retention=5, encoding="utf-8")

    logger.info("Starting network sniffer for {}", settings.target_url)
    logger.info("Using storage state {}", storage_state_path)
    logger.info("Writing logs to {}", settings.network_log_file)

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            channel=settings.browser_channel,
            headless=settings.headless,
        )
        context = await browser.new_context(storage_state=str(storage_state_path))
        page = await context.new_page()

        await configure_page(page, settings.response_preview_chars)
        await page.goto(settings.target_url, wait_until="domcontentloaded")

        print()
        print("Network sniffer is running. Press Ctrl+C to stop.")
        print(f"Page: {settings.target_url}")
        print(f"Log: {settings.network_log_file}")

        try:
            await asyncio.Event().wait()
        finally:
            await context.close()
            await browser.close()


def main() -> None:
    try:
        asyncio.run(run_network_sniffer())
    except KeyboardInterrupt:
        logger.info("Network sniffer stopped by user")


if __name__ == "__main__":
    main()
