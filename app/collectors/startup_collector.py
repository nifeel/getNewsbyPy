import asyncio
import os
from contextlib import closing
from typing import Any

from loguru import logger
from playwright.async_api import Response, TimeoutError as PlaywrightTimeoutError, async_playwright

from app.browser.login import has_local_credentials, relogin_with_retries
from app.browser.session import storage_state_exists
from app.config import get_settings
from app.collectors.startup_api_client import (
    LoginRequiredError,
    NEWS_ENDPOINT_MARKERS,
    StartupApiError,
    fetch_startup_payload,
    resolve_startup_url,
    write_cached_startup_url,
)
from app.parsers.financialjuice import parse_startup_payload
from app.storage.database import connect, init_db
from app.storage.repository import NewsRepository
from app.storage.task_status import TaskStatusRepository, utc_now


TASK_NAME = "startup_collector"


def update_task_status(status: str, **values: object) -> None:
    settings = get_settings()
    with closing(connect(settings.database_file)) as connection:
        init_db(connection)
        TaskStatusRepository(connection).upsert(TASK_NAME, status, **values)


async def read_startup_payload(response: Response) -> dict[str, Any] | None:
    if not any(marker in response.url for marker in NEWS_ENDPOINT_MARKERS):
        return None
    if response.status >= 400:
        logger.warning("Startup endpoint returned HTTP {}: {}", response.status, response.url)
        return None
    try:
        return await response.json()
    except Exception as exc:
        logger.warning("Unable to parse Startup response JSON: {}", exc)
        return None


async def collect_startup_news() -> tuple[int, int, int]:
    settings = get_settings()

    if not storage_state_exists(settings.storage_state_file):
        if has_local_credentials():
            logger.warning("Login state file is missing; attempting automatic login")
            await relogin_with_retries("missing login state file")
        else:
            raise FileNotFoundError(
                f"Login state not found: {settings.storage_state_file}. Run `python -m app.browser.login` first."
            )

    try:
        payload = await collect_startup_payload()
    except LoginRequiredError as exc:
        logger.warning("Login state appears expired: {}", exc)
        await relogin_with_retries(str(exc))
        payload = await collect_startup_payload()

    return store_startup_payload(payload)


async def collect_startup_payload() -> dict[str, Any]:
    settings = get_settings()

    if not storage_state_exists(settings.storage_state_file):
        raise FileNotFoundError(
            f"Login state not found: {settings.storage_state_file}. Run `python -m app.browser.login` first."
        )

    if settings.prefer_direct_startup:
        try:
            startup_url = resolve_startup_url(
                settings.startup_api_url,
                settings.startup_api_cache_file,
                settings.network_log_file,
            )
            logger.info("Fetching Startup payload directly from JSON API")
            payload = await fetch_startup_payload(startup_url, settings.storage_state_file)
            write_cached_startup_url(settings.startup_api_cache_file, startup_url)
            return payload
        except LoginRequiredError:
            raise
        except StartupApiError as exc:
            if not settings.browser_fallback_enabled:
                raise
            logger.warning("Direct Startup API fetch failed, falling back to browser: {}", exc)
            return await collect_startup_payload_with_browser()

    return await collect_startup_payload_with_browser()


async def collect_startup_payload_with_browser() -> dict[str, Any]:
    settings = get_settings()
    startup_payload_future: asyncio.Future[dict[str, Any]] = asyncio.Future()

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            channel=settings.browser_channel,
            headless=settings.headless,
        )
        context = await browser.new_context(storage_state=str(settings.storage_state_file))
        page = await context.new_page()

        async def handle_response(response: Response) -> None:
            if startup_payload_future.done():
                return
            payload = await read_startup_payload(response)
            if payload is not None and not startup_payload_future.done():
                write_cached_startup_url(settings.startup_api_cache_file, response.url)
                startup_payload_future.set_result(payload)

        page.on("response", lambda response: asyncio.create_task(handle_response(response)))

        try:
            logger.info("Opening {}", settings.target_url)
            await page.goto(settings.target_url, wait_until="domcontentloaded")
            password_input = page.locator("input[type='password']").first
            if await password_input.count() and await password_input.is_visible(timeout=1000):
                raise LoginRequiredError("Browser was redirected to a login page.")

            payload = await asyncio.wait_for(startup_payload_future, timeout=60)
        except TimeoutError as exc:
            try:
                password_visible = await page.locator("input[type='password']").first.is_visible(timeout=1000)
            except PlaywrightTimeoutError:
                password_visible = False
            if password_visible or "login" in page.url.lower() or "signin" in page.url.lower():
                raise LoginRequiredError("Browser could not capture Startup payload because login is required.") from exc
            raise
        finally:
            await context.close()
            await browser.close()

    return payload


def store_startup_payload(payload: dict[str, Any]) -> tuple[int, int, int]:
    settings = get_settings()
    items = parse_startup_payload(payload)
    logger.info("Parsed {} news items from Startup response", len(items))

    with closing(connect(settings.database_file)) as connection:
        init_db(connection)
        repository = NewsRepository(connection)
        inserted, skipped = repository.insert_many(items)
        total = repository.count()

    return inserted, skipped, total


def main() -> None:
    try:
        update_task_status("running", pid=os.getpid(), last_started_at=utc_now(), last_error="")
        inserted, skipped, total = asyncio.run(collect_startup_news())
    except KeyboardInterrupt:
        update_task_status("stopped", pid=os.getpid(), last_finished_at=utc_now())
        logger.info("Startup collector stopped by user")
        return
    except Exception as exc:
        update_task_status("error", pid=os.getpid(), last_finished_at=utc_now(), last_error=str(exc))
        raise

    update_task_status(
        "stopped",
        inserted=inserted,
        skipped=skipped,
        total=total,
        pid=os.getpid(),
        last_finished_at=utc_now(),
        last_error="",
    )
    print(f"Inserted: {inserted}")
    print(f"Skipped duplicates: {skipped}")
    print(f"Total stored: {total}")


if __name__ == "__main__":
    main()
