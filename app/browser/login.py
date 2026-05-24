import asyncio
import re

from loguru import logger
from playwright.async_api import TimeoutError as PlaywrightTimeoutError, async_playwright

from app.browser.context import background_window_args, block_images
from app.browser.session import ensure_parent_dir
from app.config import get_settings


class LoginCredentialsMissing(RuntimeError):
    pass


class AutomatedLoginFailed(RuntimeError):
    pass


ACCOUNT_SELECTORS = [
    "input[type='email']",
    "input[name='email']",
    "input[name='Email']",
    "input[name='username']",
    "input[name='UserName']",
    "input[name='login']",
    "input[type='text']",
]
PASSWORD_SELECTORS = [
    "input[type='password']",
    "input[name='password']",
    "input[name='Password']",
]
SUBMIT_SELECTORS = [
    "button[type='submit']",
    "input[type='submit']",
    "button:has-text('Log in')",
    "button:has-text('Login')",
    "button:has-text('Sign in')",
    "button:has-text('Sign In')",
]
LOGIN_URL_PATTERN = re.compile(r"/(login|signin|sign-in)(?:[/?#]|$)", re.IGNORECASE)


def has_local_credentials() -> bool:
    settings = get_settings()
    return bool(settings.account and settings.password)


async def _first_visible(page: Page, selectors: list[str]):
    for selector in selectors:
        locator = page.locator(selector).first
        try:
            if await locator.count() and await locator.is_visible(timeout=1000):
                return locator
        except PlaywrightTimeoutError:
            continue
    return None


async def _page_needs_login(page: Page) -> bool:
    password_input = await _first_visible(page, PASSWORD_SELECTORS)
    return password_input is not None or LOGIN_URL_PATTERN.search(page.url) is not None


async def _save_context_state(context: BrowserContext) -> None:
    settings = get_settings()
    ensure_parent_dir(settings.storage_state_file)
    await context.storage_state(path=str(settings.storage_state_file))
    logger.info("Saved login state to {}", settings.storage_state_file)


async def save_login_state() -> None:
    settings = get_settings()
    storage_state_path = settings.storage_state_file
    ensure_parent_dir(storage_state_path)
    ensure_parent_dir(settings.browser_profile_dir / ".keep")

    logger.info("Opening browser for manual login: {}", settings.login_url)

    async with async_playwright() as playwright:
        context = await playwright.chromium.launch_persistent_context(
            user_data_dir=str(settings.browser_profile_dir),
            channel=settings.browser_channel,
            headless=settings.headless,
        )
        await block_images(context)
        page = await context.new_page()

        await page.goto(settings.login_url, wait_until="domcontentloaded")

        print()
        print("Browser opened. Please log in to FinancialJuice manually.")
        print("After you confirm the page is logged in, return here and press Enter.")
        input("> ")

        await context.storage_state(path=str(storage_state_path))
        logger.info("Saved login state to {}", storage_state_path)

        await context.close()


async def save_login_state_with_credentials() -> None:
    settings = get_settings()
    if not settings.account or not settings.password:
        raise LoginCredentialsMissing("Set FJ_ACCOUNT and FJ_PASSWORD before using automatic login.")

    ensure_parent_dir(settings.storage_state_file)
    ensure_parent_dir(settings.browser_profile_dir / ".keep")

    logger.info("Opening browser for automatic login: {}", settings.login_url)

    async with async_playwright() as playwright:
        context = await playwright.chromium.launch_persistent_context(
            user_data_dir=str(settings.browser_profile_dir),
            channel=settings.browser_channel,
            headless=settings.headless,
            args=background_window_args(settings.headless),
            viewport={"width": 1920, "height": 1080},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            locale="en-US",
            timezone_id="America/New_York",
        )
        await block_images(context)
        page = await context.new_page()
        try:
            await page.goto(settings.login_url, wait_until="domcontentloaded")

            # 快速检查是否被 Cloudflare 拦截
            cf_blocked = await page.evaluate("() => document.title.includes('Cloudflare')")
            if cf_blocked:
                raise AutomatedLoginFailed("Cloudflare blocked the login page")

            if not await _page_needs_login(page):
                # 页面加载后没有出现登录表单。如果存储状态文件缺失或为空，
                # 则尝试查找并点击登录激活链接。
                if not (settings.storage_state_file.exists() and settings.storage_state_file.stat().st_size > 50):
                    logger.info("No saved session, looking for sign-in trigger.")
                    signin_selectors = [
                        "a[href='#signup']",
                        "a:has-text('Sign In')",
                        "button:has-text('Sign In')",
                        "a:has-text('Log In')",
                        "a:has-text('Login')",
                        "a:has-text('Log in')",
                    ]
                    for sel in signin_selectors:
                        try:
                            el = page.locator(sel).first
                            if await el.is_visible(timeout=1000):
                                logger.info("login: clicking sign-in trigger: {}", sel)
                                await el.click()
                                await asyncio.sleep(2)
                                break
                        except Exception:
                            continue
                    # 重新检查登录表单是否已出现
                    if not await _page_needs_login(page):
                        raise AutomatedLoginFailed("Could not find any login form or trigger on the page.")
                else:
                    await _save_context_state(context)
                    return

            account_input = await _first_visible(page, ACCOUNT_SELECTORS)
            password_input = await _first_visible(page, PASSWORD_SELECTORS)
            if account_input is None or password_input is None:
                raise AutomatedLoginFailed("Could not find visible account/password fields on the login page.")

            await account_input.fill(settings.account)
            await password_input.fill(settings.password)

            submit = await _first_visible(page, SUBMIT_SELECTORS)
            if submit is not None:
                await submit.click()
            else:
                await password_input.press("Enter")

            timeout_ms = settings.login_success_timeout_seconds * 1000
            try:
                await page.wait_for_load_state("networkidle", timeout=timeout_ms)
            except PlaywrightTimeoutError:
                logger.debug("Timed out waiting for networkidle after login submit; checking page state anyway.")

            if await _page_needs_login(page):
                raise AutomatedLoginFailed("Automatic login did not leave the login page.")

            await _save_context_state(context)
        finally:
            await context.close()


async def relogin_with_retries(reason: str = "login state expired") -> None:
    settings = get_settings()
    if not has_local_credentials():
        raise LoginCredentialsMissing(
            "Login state expired and automatic login is not configured. Set FJ_ACCOUNT and FJ_PASSWORD."
        )

    attempts = max(settings.login_retry_attempts, 1)
    interval_seconds = max(settings.login_retry_interval_seconds, 0)
    for attempt in range(1, attempts + 1):
        try:
            logger.warning(
                "Automatic login attempt {}/{} after {}",
                attempt,
                attempts,
                reason,
            )
            await save_login_state_with_credentials()
            logger.info("Automatic login succeeded on attempt {}/{}", attempt, attempts)
            return
        except Exception as exc:
            if attempt >= attempts:
                raise AutomatedLoginFailed(f"Automatic login failed after {attempts} attempts: {exc}") from exc
            logger.warning(
                "Automatic login attempt {}/{} failed: {}. Retrying in {}s",
                attempt,
                attempts,
                exc,
                interval_seconds,
            )
            await asyncio.sleep(interval_seconds)


def main() -> None:
    settings = get_settings()
    if settings.account and settings.password:
        asyncio.run(save_login_state_with_credentials())
    else:
        asyncio.run(save_login_state())


if __name__ == "__main__":
    main()
