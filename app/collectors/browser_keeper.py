import asyncio
import os
from contextlib import closing

from loguru import logger
from playwright.async_api import TimeoutError as PlaywrightTimeoutError, async_playwright

from app.browser.context import background_window_args, block_images
from app.browser.login import (
    ACCOUNT_SELECTORS,
    PASSWORD_SELECTORS,
    SUBMIT_SELECTORS,
    AutomatedLoginFailed,
    _first_visible,
)
from app.browser.session import storage_state_exists
from app.collectors.startup_api_client import NEWS_ENDPOINT_MARKERS
from app.config import get_settings
from app.parsers.financialjuice import parse_startup_payload
from app.storage.database import connect, init_db
from app.storage.repository import NewsRepository
from app.storage.sync_task_repository import SyncTaskRepository
from app.storage.task_status import TaskStatusRepository, utc_now


TASK_NAME = "browser_keeper"
COLLECT_TASK_NAME = "browser_collector"
COOKIE_SAVE_INTERVAL = 60
LOGIN_FORM_WAIT_MS = 10000

# Selectors for a "Login" trigger button that reveals the actual form
LOGIN_TRIGGER_SELECTORS = [
    "a:has-text('Login')",
    "a:has-text('Log in')",
    "a:has-text('Sign in')",
    "button:has-text('Login')",
    "button:has-text('Log in')",
    "button:has-text('Sign in')",
    "a[href*='/login']",
    "a[href*='/signin']",
    "[data-action='login']",
    ".login-btn",
    ".btn-login",
]


def update_task_status(status: str, **values: object) -> None:
    settings = get_settings()
    with closing(connect(settings.database_file)) as connection:
        init_db(connection)
        TaskStatusRepository(connection).upsert(TASK_NAME, status, **values)


def _batch_min_id(items: list) -> int:
    ids = [int(item.external_id) for item in items if item.external_id and item.external_id.isdigit()]
    return min(ids) if ids else 0


def _create_sync_tasks(db_max: int, batch_min: int) -> None:
    if batch_min <= 0:
        return
    settings = get_settings()
    with closing(connect(settings.database_file)) as conn:
        init_db(conn)
        repo = SyncTaskRepository(conn)
        if repo.has_start(batch_min):
            return
        if db_max == 0:
            row = conn.execute("SELECT value FROM gui_settings WHERE key = 'history_since'").fetchone()
            history_since = row["value"] if row else ""
            if not history_since:
                return
            task_id = repo.create(batch_min, target_since=history_since)
            logger.info("browser_collector: created history task id={} start={} since={}", task_id, batch_min, history_since)
            print(f"browser_collector: created history task start={batch_min} since={history_since}", flush=True)
        elif batch_min > db_max + 1:
            task_id = repo.create(batch_min, end_news_id=db_max)
            logger.info("browser_collector: created gap task id={} start={} end={}", task_id, batch_min, db_max)
            print(f"browser_collector: created gap task start={batch_min} end={db_max}", flush=True)


async def _handle_news_response(response, stats: dict, settings) -> None:
    if not any(marker in response.url for marker in NEWS_ENDPOINT_MARKERS):
        return
    if response.status >= 400:
        return

    try:
        payload = await response.json()
    except Exception as exc:
        logger.debug("browser_collector: could not parse response JSON: {}", exc)
        return

    try:
        items = parse_startup_payload(payload)  # type: ignore[arg-type]
    except Exception as exc:
        logger.warning("browser_collector: failed to parse news payload: {}", exc)
        return

    if not items:
        return

    try:
        with closing(connect(settings.database_file)) as conn:
            init_db(conn)
            repo = NewsRepository(conn, table="browser_news")
            inserted, skipped, new_ids, min_skipped_id = repo.insert_many(items)
            total = repo.count()
    except Exception as exc:
        logger.warning("browser_collector: DB write failed: {}", exc)
        return

    stats["inserted"] += inserted
    stats["skipped"] += skipped
    stats["cycles"] += 1

    if new_ids:
        logger.info("browser_collector: {} new | ids={}", inserted, new_ids)
        print(f"browser_collector new NewsIDs: {new_ids}", flush=True)
    if skipped > 0 and min_skipped_id is not None:
        print(f"browser_collector skipped={skipped} min_skipped_id={min_skipped_id}", flush=True)
    if not new_ids and (skipped == 0 or min_skipped_id is None):
        logger.debug("browser_collector: {} new, {} skipped (cycle {})", inserted, skipped, stats["cycles"])

    # On first successful collection, create sync tasks for gap/history fill
    if not stats["first_done"]:
        stats["first_done"] = True
        batch_min = _batch_min_id(items)
        _create_sync_tasks(stats["db_max_before_first"], batch_min)

    try:
        with closing(connect(settings.database_file)) as conn:
            init_db(conn)
            TaskStatusRepository(conn).upsert(
                COLLECT_TASK_NAME,
                "collecting",
                cycle=stats["cycles"],
                inserted=stats["inserted"],
                skipped=stats["skipped"],
                total=total,
                pid=os.getpid(),
                last_finished_at=utc_now(),
                last_error="",
            )
    except Exception as exc:
        logger.debug("browser_collector: status update failed: {}", exc)


async def _wait_for_password_field(page, timeout_ms: int) -> bool:
    try:
        await page.wait_for_selector("input[type='password']", timeout=timeout_ms, state="visible")
        return True
    except PlaywrightTimeoutError:
        return False


async def _page_needs_login(page) -> bool:
    # Case 1: password field already visible
    if await _wait_for_password_field(page, 3000):
        logger.info("Browser keeper: login form visible (password field found).")
        return True

    # Case 2: FinancialJuice auto-opens the #signup modal when not logged in.
    # The modal intercepts clicks on the outer Login button, so use JS to switch tab.
    try:
        if await page.locator("#signup").count() > 0 and await page.locator("#signup").is_visible():
            logger.info("Browser keeper: #signup modal detected, calling activeTab('LoginTab').")
            await page.evaluate("if (typeof activeTab === 'function') activeTab('LoginTab')")
            if await _wait_for_password_field(page, 5000):
                logger.info("Browser keeper: login tab active, password field visible.")
                return True
    except Exception as exc:
        logger.debug("Browser keeper: modal check error: {}", exc)

    # Case 3: login trigger button — use force=True to bypass any overlay
    for selector in LOGIN_TRIGGER_SELECTORS:
        try:
            loc = page.locator(selector).first
            if await loc.count() > 0:
                logger.info("Browser keeper: clicking login trigger (force): {}", selector)
                await loc.click(force=True)
                if await _wait_for_password_field(page, 5000):
                    return True
        except Exception:
            continue

    logger.info("Browser keeper: no login form detected, assuming already logged in.")
    return False


async def _log_page_inputs(page, selector: str = "input") -> None:
    try:
        count = await page.locator(selector).count()
        logger.warning("Browser keeper: {} input(s) found for selector {!r}:", count, selector)
        for i in range(count):
            inp = page.locator(selector).nth(i)
            t = await inp.get_attribute("type") or "text"
            n = await inp.get_attribute("name") or ""
            ph = await inp.get_attribute("placeholder") or ""
            id_ = await inp.get_attribute("id") or ""
            vis = await inp.is_visible()
            logger.warning("  [{}] type={!r} name={!r} id={!r} placeholder={!r} visible={}", i, t, n, id_, ph, vis)
    except Exception as exc:
        logger.debug("Could not enumerate inputs: {}", exc)


async def _login_in_page(page, settings) -> None:
    # Save diagnostic screenshot
    try:
        os.makedirs("data", exist_ok=True)
        await page.screenshot(path="data/keeper_login_attempt.png")
        logger.info("Browser keeper: screenshot saved → data/keeper_login_attempt.png")
    except Exception as exc:
        logger.debug("Screenshot failed: {}", exc)

    # Scope search to the login modal first to avoid matching unrelated inputs (e.g. search box)
    MODAL_ACCOUNT_SELECTORS = [
        "#signup input[type='email']",
        "#signup input[name='email']",
        "#signup input[name='Email']",
        "#signup input[name='username']",
        "#signup input[name='UserName']",
        "#signup input[name='login']",
        "#LoginTab input[type='email']",
        "#LoginTab input[type='text']",
        "#signup input[type='text']:visible",
    ]
    account_input = await _first_visible(page, MODAL_ACCOUNT_SELECTORS)
    if account_input is None:
        account_input = await _first_visible(page, ACCOUNT_SELECTORS)
    if account_input is None:
        await _log_page_inputs(page, "#signup input")
        raise AutomatedLoginFailed("Could not find account/email input field.")

    MODAL_PASSWORD_SELECTORS = [
        "#signup input[type='password']",
        "#LoginTab input[type='password']",
    ]
    password_input = await _first_visible(page, MODAL_PASSWORD_SELECTORS)
    if password_input is None:
        password_input = await _first_visible(page, PASSWORD_SELECTORS)
    if password_input is None:
        raise AutomatedLoginFailed("Could not find password input field.")

    logger.info("Browser keeper: filling credentials.")
    await account_input.click()
    await account_input.fill(settings.account)
    await password_input.click()
    await password_input.fill(settings.password)

    # Check "Remember me" if present
    REMEMBER_SELECTORS = [
        "#signup input[type='checkbox']",
        "#LoginTab input[type='checkbox']",
        "input[name*='remember' i]",
        "input[id*='remember' i]",
    ]
    for sel in REMEMBER_SELECTORS:
        try:
            chk = page.locator(sel).first
            if await chk.count() > 0 and not await chk.is_checked():
                await chk.check()
                logger.info("Browser keeper: checked 'Remember me'.")
                break
        except Exception:
            continue

    MODAL_SUBMIT_SELECTORS = [
        "#signup button[type='submit']",
        "#signup input[type='submit']",
        "#LoginTab button[type='submit']",
        "#LoginTab button:has-text('Log in')",
        "#LoginTab button:has-text('Login')",
        "#signup button:has-text('Log in')",
        "#signup button:has-text('Login')",
    ]
    submit = await _first_visible(page, MODAL_SUBMIT_SELECTORS)
    if submit is None:
        submit = await _first_visible(page, SUBMIT_SELECTORS)
    if submit is not None:
        await submit.click()
    else:
        await password_input.press("Enter")

    # Wait for the password field to disappear as confirmation of success
    try:
        await page.wait_for_selector("input[type='password']", timeout=settings.login_success_timeout_seconds * 1000, state="hidden")
    except PlaywrightTimeoutError:
        await page.screenshot(path="data/keeper_login_failed.png")
        raise AutomatedLoginFailed("Login did not succeed (password field still visible after submit).")

    logger.info("Browser keeper: login succeeded.")


async def _try_login(page, context, settings) -> None:
    if settings.account and settings.password:
        try:
            await _login_in_page(page, settings)
            await context.storage_state(path=str(settings.storage_state_file))
            update_task_status("running", pid=os.getpid(), last_error="")
        except AutomatedLoginFailed as exc:
            logger.error("Browser keeper: auto-login failed: {}", exc)
            update_task_status("error", pid=os.getpid(), last_error=str(exc))
    else:
        logger.warning("Browser keeper: login required but no credentials configured.")
        update_task_status("error", pid=os.getpid(), last_error="login required")


async def run_keeper() -> None:
    settings = get_settings()
    update_task_status("running", pid=os.getpid(), last_started_at=utc_now(), last_error="")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            channel=settings.browser_channel,
            headless=settings.headless,
            args=background_window_args(settings.headless),
        )
        storage_state = (
            str(settings.storage_state_file)
            if storage_state_exists(settings.storage_state_file)
            else None
        )
        context = await browser.new_context(storage_state=storage_state)
        await block_images(context)
        page = await context.new_page()

        if settings.browser_collect_enabled:
            with closing(connect(settings.database_file)) as conn:
                init_db(conn)
                db_max_before_first = NewsRepository(conn).max_news_id()
            collect_stats = {
                "inserted": 0,
                "skipped": 0,
                "cycles": 0,
                "first_done": False,
                "db_max_before_first": db_max_before_first,
            }
            page.on(
                "response",
                lambda r: asyncio.create_task(_handle_news_response(r, collect_stats, settings)),
            )
            logger.info("browser_collector: response interception enabled (db_max={})", db_max_before_first)
            with closing(connect(settings.database_file)) as conn:
                init_db(conn)
                TaskStatusRepository(conn).upsert(
                    COLLECT_TASK_NAME,
                    "running",
                    cycle=0,
                    inserted=0,
                    skipped=0,
                    total=0,
                    pid=os.getpid(),
                    last_started_at=utc_now(),
                    last_error="",
                    message="waiting for page responses",
                )

        try:
            await page.goto(settings.target_url, wait_until="domcontentloaded", timeout=30000)
            logger.info("Browser keeper: page loaded.")
        except Exception as exc:
            logger.warning("Browser keeper: navigation failed: {}", exc)
            update_task_status("error", pid=os.getpid(), last_error=str(exc))

        if await _page_needs_login(page):
            await _try_login(page, context, settings)
        else:
            update_task_status("running", pid=os.getpid(), last_error="")

        last_save = asyncio.get_event_loop().time()
        last_login_check = asyncio.get_event_loop().time()
        try:
            while True:
                await asyncio.sleep(5)
                now = asyncio.get_event_loop().time()

                if now - last_login_check >= settings.login_check_interval_seconds:
                    last_login_check = now
                    if await _page_needs_login(page):
                        logger.info("Browser keeper: login expired, re-logging in.")
                        await _try_login(page, context, settings)

                if now - last_save >= COOKIE_SAVE_INTERVAL:
                    await context.storage_state(path=str(settings.storage_state_file))
                    last_save = now

        except (asyncio.CancelledError, KeyboardInterrupt):
            pass
        finally:
            try:
                await context.storage_state(path=str(settings.storage_state_file))
            except Exception:
                pass
            await context.close()
            await browser.close()

    update_task_status("stopped", pid=os.getpid(), last_finished_at=utc_now())
    if settings.browser_collect_enabled:
        with closing(connect(settings.database_file)) as conn:
            init_db(conn)
            TaskStatusRepository(conn).upsert(
                COLLECT_TASK_NAME, "stopped", pid=os.getpid(), last_finished_at=utc_now()
            )


def main() -> None:
    from pathlib import Path as _Path
    _Path("data/logs").mkdir(parents=True, exist_ok=True)
    logger.add("data/logs/browser_keeper.log", rotation="10 MB", retention=5, encoding="utf-8", level="DEBUG")

    try:
        asyncio.run(run_keeper())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
