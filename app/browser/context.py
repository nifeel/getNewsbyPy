import os
import subprocess

from playwright.async_api import BrowserContext, Route

from app.browser.session import storage_state_exists
from app.config import get_settings

CHROME_ORPHAN_PATTERN = "window-position=-32000"
BROWSER_VIEWPORT = {"width": 1280, "height": 720}


def background_window_args(headless: bool, memory_optimize: bool = False) -> list[str]:
    """构建 Chrome 启动参数。

    非无头模式下将窗口移出屏幕，避免抢夺焦点。
    默认关闭 GPU 与声音，降低 Xvfb 下的功耗。
    若 *memory_optimize* 为 True，添加标记以减少 Chrome 长期运行的内存占用。
    """
    args = [
        "--disable-gpu",
        "--mute-audio",
        "--disable-audio-output",
    ]
    if not headless:
        args.append("--window-position=-32000,-32000")
        args.append("--window-size=1280,720")
    # 反检测：隐藏 Playwright 自动化标记
    args.extend([
        "--disable-blink-features=AutomationControlled",
        "--disable-features=IsolateOrigins,site-per-process",
        "--no-sandbox",
        "--disable-setuid-sandbox",
        "--disable-dev-shm-usage",
        "--disable-web-security" if not headless else "--disable-web-security",
    ])
    if memory_optimize:
        args.extend([
            "--memory-pressure-off",
            "--disable-renderer-backgrounding",
            "--js-flags=--max_old_space_size=512",
        ])
    return args


async def _abort_if_image(route: Route) -> None:
    if route.request.resource_type == "image":
        await route.abort()
    else:
        await route.continue_()


async def block_images(context: BrowserContext) -> None:
    await context.route("**/*", _abort_if_image)


def reap_orphan_chromes() -> None:
    """清掉上次未退出的采集 Chrome（用移出屏幕的窗口位置作指纹）。"""
    if os.name == "nt":
        return
    subprocess.run(
        ["pkill", "-f", CHROME_ORPHAN_PATTERN],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

