from playwright.async_api import Browser, BrowserContext, Route, async_playwright

from app.browser.session import storage_state_exists
from app.config import get_settings


def background_window_args(headless: bool) -> list[str]:
    """When not headless, move the browser window off-screen so it never steals focus."""
    if headless:
        return []
    return ["--window-position=-32000,-32000"]


async def _abort_if_image(route: Route) -> None:
    if route.request.resource_type == "image":
        await route.abort()
    else:
        await route.continue_()


async def block_images(context: BrowserContext) -> None:
    await context.route("**/*", _abort_if_image)


class BrowserSession:
    """Temporary browser session for background HTTP requests.

    Uses browser.launch() (not launch_persistent_context) so multiple
    collectors can run simultaneously without profile directory conflicts.
    Cookies are loaded from storage_state.json at start and saved on exit,
    kept fresh by browser_keeper which runs for the lifetime of the server.
    """

    def __init__(self) -> None:
        self._playwright = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None

    async def __aenter__(self) -> "BrowserSession":
        settings = get_settings()
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            channel=settings.browser_channel,
            headless=settings.headless,
            args=background_window_args(settings.headless),
        )
        storage_state = (
            str(settings.storage_state_file)
            if storage_state_exists(settings.storage_state_file)
            else None
        )
        self._context = await self._browser.new_context(storage_state=storage_state)
        await block_images(self._context)
        return self

    async def __aexit__(self, *_args: object) -> None:
        if self._context is not None:
            settings = get_settings()
            try:
                await self._context.storage_state(path=str(settings.storage_state_file))
            except Exception:
                pass
            await self._context.close()
            self._context = None
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None

    @property
    def context(self) -> BrowserContext:
        if self._context is None:
            raise RuntimeError("BrowserSession is not started — use `async with BrowserSession()`")
        return self._context
