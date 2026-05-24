"""实时浏览器服务（登录管理器 + 实时新闻收集器）。

该服务管理系统唯一的 Chrome 窗口：
  - 启动 Chrome，导航至 FinancialJuice，必要时执行登录。
  - 设置 gui_settings.login_status（'ok' / 'checking' / 'failed'）。
  - 定期检查会话是否仍然有效；若失效则重新登录。
  - 拦截 WebSocket 推送和 HTTP API 响应，将实时新闻
    写入 ``news`` 表。

用法::

    python -m app.services.realtime_browser <task_id>
"""

import asyncio
import json
import os
import sys
from contextlib import closing

from loguru import logger
from playwright.async_api import TimeoutError as PlaywrightTimeoutError, async_playwright

from app.browser.context import background_window_args, block_images
from app.browser.login import (
    AutomatedLoginFailed,
    LoginCredentialsMissing,
    _save_context_state,
)
from app.browser.session import ensure_parent_dir
from app.collectors.startup_api_client import NEWS_ENDPOINT_MARKERS
from app.config import get_settings
from app.parsers.financialjuice import parse_startup_payload
from app.storage.database import connect, init_db, SOURCE_METHOD_BROWSER, SOURCE_METHOD_BROWSER_WS
from app.storage.repository import NewsRepository
from app.storage.task_log import write_task_log
from app.storage.task_repository import TaskRepository
from app.storage.task_status import cst_now


# 仅在用户未登录时可见的元素（被动检测，不执行点击）
LOGGED_OUT_INDICATORS = [
    "input[type='password']",
    "#signup",
    "#LoginTab",
    "form input[name='password']",
    "form input[name='Password']",
]


class RestartBrowser(Exception):
    """在主循环内抛出，用于触发干净的浏览器重启。"""


CLOUDFLARE_MAX_RETRIES = 4


# ---------------------------------------------------------------------------
# 浏览器进程内存辅助函数
# ---------------------------------------------------------------------------


def _get_browser_rss(browser) -> int:
    """返回浏览器进程的 RSS（以 kB 为单位，不可用时返回 0）。"""
    try:
        proc = browser.process
        if proc is None:
            return 0
        pid = proc.pid
        if pid is None:
            return 0
        # macOS / Linux — ps -o rss= 返回的 RSS 单位为 kB
        with os.popen(f"ps -o rss= -p {pid}") as f:
            line = f.read().strip()
            return int(line) if line else 0
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# gui_settings 辅助函数
# ---------------------------------------------------------------------------


def set_login_status(status: str) -> None:
    """将 login_status 写入 gui_settings 表。

    Args:
        status: ``'ok'``、``'checking'`` 或 ``'failed'`` 之一。
    """
    settings = get_settings()
    with closing(connect(settings.database_file)) as conn:
        init_db(conn)
        conn.execute(
            """
            INSERT INTO gui_settings (key, value, updated_at)
            VALUES ('login_status', ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = excluded.updated_at
            """,
            (status, cst_now()),
        )
        conn.commit()
    logger.debug("login_status set to '{}'", status)


# ---------------------------------------------------------------------------
# 登录事件日志
# ---------------------------------------------------------------------------


def _write_login_event(settings, event: str, detail: str = "") -> None:
    try:
        with closing(connect(settings.database_file)) as conn:
            init_db(conn)
            conn.execute(
                "INSERT INTO login_events (event, detail, created_at) VALUES (?, ?, ?)",
                (event, detail, cst_now()),
            )
            conn.commit()
    except Exception as exc:
        logger.debug("realtime_browser: login_events write failed: {}", exc)


def _log(settings, task_id: int, level: str, message: str) -> None:
    """写入 task_log 记录的快捷函数。"""
    try:
        write_task_log(settings.database_file, task_id, "realtime_browser", level, message)
    except Exception as exc:
        logger.warning("realtime_browser: task_log write failed: {}", exc)


# ---------------------------------------------------------------------------
# 登录检测（被动检测 — 无需点击）
# ---------------------------------------------------------------------------


async def _page_needs_login(page) -> bool:
    """如果当前页面上有任何未登录指示器可见，则返回 True。"""
    for selector in LOGGED_OUT_INDICATORS:
        try:
            if await page.locator(selector).first.is_visible(timeout=1500):
                logger.warning("realtime_browser: *** LOGGED OUT detected: {} ***", selector)
                return True
        except PlaywrightTimeoutError:
            continue
        except Exception as exc:
            logger.debug("realtime_browser: selector check error ({}): {}", selector, exc)
    logger.info("realtime_browser: no logged-out indicators, assuming logged in.")
    return False


# ---------------------------------------------------------------------------
# 新闻收集辅助函数
# ---------------------------------------------------------------------------


async def _handle_news_response(
    response, task_id: int, stats: dict, settings
) -> None:
    """存储来自匹配的 HTTP API 响应的新闻。"""
    if not any(marker in response.url for marker in NEWS_ENDPOINT_MARKERS):
        return
    if response.status >= 400:
        return
    try:
        payload = await response.json()
    except Exception as exc:
        logger.debug("realtime_browser: could not parse response JSON: {}", exc)
        return
    try:
        items = parse_startup_payload(payload)
    except Exception as exc:
        logger.warning("realtime_browser: failed to parse news payload: {}", exc)
        return
    if not items:
        return
    try:
        with closing(connect(settings.database_file)) as conn:
            init_db(conn)
            repo = NewsRepository(conn)
            inserted, skipped, new_ids, min_skipped_id = repo.insert_many(
                items, source_method=SOURCE_METHOD_BROWSER, task_id=task_id
            )
            max_inserted_id = max(new_ids) if new_ids else None
    except Exception as exc:
        logger.warning("realtime_browser: DB write failed: {}", exc)
        return
    stats["inserted"] += inserted
    stats["skipped"] += skipped
    stats["cycles"] += 1
    if new_ids:
        logger.warning("realtime_browser: *** DB wrote {} news | ids={} ***", inserted, new_ids)
        print(f"realtime_browser new NewsIDs: {new_ids}", flush=True)
    if skipped > 0 and min_skipped_id is not None:
        print(f"realtime_browser skipped={skipped} min_skipped_id={min_skipped_id}", flush=True)
    if max_inserted_id is not None:
        try:
            with closing(connect(settings.database_file)) as conn:
                init_db(conn)
                TaskRepository(conn).update(
                    task_id, "running", current_news_id=max_inserted_id
                )
        except Exception as exc:
            logger.debug("realtime_browser: task progress update failed: {}", exc)


def _parse_ws_frame(raw: str | bytes) -> list:
    """从 Centrifuge WebSocket 推送帧中提取 NewsItems。"""
    try:
        msg = json.loads(raw)
    except Exception:
        return []
    if not msg:
        return []
    push = msg.get("push")
    if not isinstance(push, dict):
        return []
    pub = push.get("pub")
    if not isinstance(pub, dict):
        return []
    data = pub.get("data")
    if not data:
        return []
    logger.debug("realtime_browser: ws push data: {}", str(data)[:300])
    if isinstance(data, dict) and data.get("ev") == "sendUpdates":
        msg_str = data.get("msg")
        if not msg_str:
            return []
        try:
            raw_items = json.loads(msg_str)
        except Exception:
            return []
        if isinstance(raw_items, list):
            return parse_startup_payload({"d": {"News": raw_items}})
        if isinstance(raw_items, dict):
            return parse_startup_payload({"d": {"News": [raw_items]}})
        return []
    if isinstance(data, dict):
        items = parse_startup_payload({"d": {"News": [data]}})
        if items:
            return items
        return parse_startup_payload(data)
    if isinstance(data, list):
        return parse_startup_payload({"d": {"News": data}})
    return []


def _handle_ws_frame(
    raw: str | bytes, task_id: int, stats: dict, settings
) -> None:
    """将 WebSocket 帧中的 NewsItems 存储到 news 表中。"""
    items = _parse_ws_frame(raw)
    if not items:
        return
    try:
        with closing(connect(settings.database_file)) as conn:
            init_db(conn)
            repo = NewsRepository(conn)
            inserted, skipped, new_ids, min_skipped_id = repo.insert_many(
                items, source_method=SOURCE_METHOD_BROWSER_WS, task_id=task_id
            )
    except Exception as exc:
        logger.warning("realtime_browser: ws DB write failed: {}", exc)
        return
    stats["inserted"] += inserted
    stats["skipped"] += skipped
    stats["cycles"] += 1
    if new_ids:
        logger.warning("realtime_browser: *** DB wrote {} news (ws) | ids={} ***", inserted, new_ids)
        print(f"realtime_browser ws new NewsIDs: {new_ids}", flush=True)
    max_inserted_id = max(new_ids) if new_ids else None
    if max_inserted_id is not None:
        try:
            with closing(connect(settings.database_file)) as conn:
                init_db(conn)
                TaskRepository(conn).update(
                    task_id, "running", current_news_id=max_inserted_id
                )
        except Exception as exc:
            logger.debug("realtime_browser: ws task progress update failed: {}", exc)


# ---------------------------------------------------------------------------
# 登录辅助函数
# ---------------------------------------------------------------------------


async def _do_login(page, context, task_id: int, settings, suppress_nav: list) -> bool:
    """在当前页面上使用已知的 FinancialJuice 字段 ID 重新登录。

    AJAX 登出后，#signup 模态框已经可见。我们切换到
    登录标签页，填写已知表单字段，提交，并验证是否成功。
    """
    # 来自 FinancialJuice HTML（ASP.NET WebForms）的已知稳定字段 ID
    FJ_EMAIL_SEL  = "#ctl00_SignInSignUp_loginForm1_inputEmail"
    FJ_PW_SEL     = "#ctl00_SignInSignUp_loginForm1_inputPassword"
    FJ_BTN_SEL    = "#ctl00_SignInSignUp_loginForm1_btnLogin"
    FJ_TAB_TRIGGER = "a[href='#LoginTab']"
    FJ_LOGIN_TAB  = "#LoginTab"

    try:
        if not settings.account or not settings.password:
            raise LoginCredentialsMissing("Set FJ_ACCOUNT and FJ_PASSWORD for automatic relogin.")

        attempts = max(settings.login_retry_attempts, 1)
        last_exc: Exception | None = None

        for attempt in range(1, attempts + 1):
            logger.warning("realtime_browser: login attempt {}/{}", attempt, attempts)

            # 步骤 0：如果 signup 模态框不在 DOM 中，搜索任何
            # 可能触发登录模态框的可点击元素。
            has_modal = await page.evaluate("() => !!document.querySelector('#signup')")
            if not has_modal:
                logger.info("realtime_browser: #signup not in DOM, searching for login trigger on page")
                # 转储页面信息以了解结构
                page_info = await page.evaluate("""() => {
                    return {
                        title: document.title,
                        bodyStart: (document.body ? document.body.innerText.slice(0, 500) : ''),
                        visibleEls: Array.from(document.querySelectorAll('a, button, [role=button]')).map(el => ({
                            tag: el.tagName,
                            id: el.id,
                            cls: el.className.slice(0, 80),
                            text: (el.textContent || '').trim().slice(0, 40),
                            href: el.href || '',
                            rect: el.getBoundingClientRect ? JSON.stringify({t: el.getBoundingClientRect().top, l: el.getBoundingClientRect().left, w: el.getBoundingClientRect().width, h: el.getBoundingClientRect().height}) : ''
                        })).slice(0, 30),
                        bootstrap: typeof $ !== 'undefined' && typeof $.fn !== 'undefined' && typeof $.fn.modal !== 'undefined',
                    };
                }""")
                logger.info("realtime_browser: page title='{}'", page_info['title'])
                logger.info("realtime_browser: body text starts: {}", (page_info['bodyStart'] or '')[:200])
                for el in page_info.get('visibleEls', []):
                    logger.info("realtime_browser: el: tag={} id={} text='{}' href='{}' rect={}", el['tag'], el['id'], el['text'], el['href'], el.get('rect', ''))
                # 如果被 Cloudflare 拦截，等待后重新加载页面
                if 'Cloudflare' in (page_info.get('title') or '') or 'blocked' in ((page_info.get('bodyStart') or '')[:200]).lower():
                    logger.warning("realtime_browser: Cloudflare blocked, waiting and retrying navigation")
                    await asyncio.sleep(8)
                    try:
                        await page.goto(settings.target_url, wait_until="domcontentloaded", timeout=20000)
                    except Exception:
                        pass
                    continue

            # 步骤 1：如果登录标签页尚未激活，则切换到该标签页
            try:
                login_tab_cls = await page.locator(FJ_LOGIN_TAB).get_attribute("class", timeout=2000)
            except Exception:
                login_tab_cls = ""

            if "active" not in (login_tab_cls or ""):
                try:
                    trigger = page.locator(FJ_TAB_TRIGGER).first
                    if await trigger.is_visible(timeout=2000):
                        logger.info("realtime_browser: clicking LoginTab trigger")
                        await trigger.click()
                        await asyncio.sleep(1)
                except Exception as exc:
                    logger.debug("realtime_browser: tab trigger click failed: {}", exc)

            # 步骤 2：等待邮箱字段出现在 DOM 中
            email_loc = page.locator(FJ_EMAIL_SEL)
            pw_loc    = page.locator(FJ_PW_SEL)
            btn_loc   = page.locator(FJ_BTN_SEL)

            # 强制显示：从邮箱字段向上遍历，覆盖所有 display:none 样式
            try:
                await page.evaluate(f"""() => {{
                    const em = document.querySelector('{FJ_EMAIL_SEL}');
                    if (!em) return;
                    let el = em.parentElement;
                    while (el && el.id !== 'signup' && el !== document.body) {{
                        if (getComputedStyle(el).display === 'none')
                            el.style.display = 'block';
                        el = el.parentElement;
                    }}
                }}""")
            except Exception:
                pass

            try:
                email_visible = await email_loc.is_visible(timeout=3000)
            except Exception:
                email_visible = False

            if not email_visible:
                last_exc = AutomatedLoginFailed("Login email field not visible")
                logger.warning("realtime_browser: attempt {}: email field not visible", attempt)
                await asyncio.sleep(2)
                continue

            # 步骤 3：填写凭据并提交
            logger.info("realtime_browser: filling credentials (attempt {})", attempt)
            await email_loc.fill(settings.account)
            await pw_loc.fill(settings.password)

            try:
                btn_visible = await btn_loc.is_visible(timeout=1000)
            except Exception:
                btn_visible = False

            if btn_visible:
                await btn_loc.click()
            else:
                await pw_loc.press("Enter")

            # 步骤 4：等待登录模态框关闭（成功信号）
            timeout_ms = settings.login_success_timeout_seconds * 1000
            try:
                await page.wait_for_selector(
                    "#signup", state="hidden", timeout=timeout_ms
                )
                logger.info("realtime_browser: #signup modal closed — login likely succeeded")
            except Exception:
                logger.debug("realtime_browser: wait for modal close timed out or page navigated; checking state anyway.")

            await asyncio.sleep(1)

            # 步骤 5：验证
            if not await _page_needs_login(page):
                set_login_status("ok")
                with closing(connect(settings.database_file)) as conn:
                    init_db(conn)
                    TaskRepository(conn).update(task_id, "running", pid=os.getpid(), last_error="")
                logger.warning("realtime_browser: *** LOGIN SUCCEEDED on attempt {} ***", attempt)
                _write_login_event(settings, "login_ok", f"attempt={attempt}")
                await _save_context_state(context)
                return True

            last_exc = AutomatedLoginFailed("Still needs login after submit")
            logger.warning("realtime_browser: attempt {}: still needs login after submit", attempt)
            await asyncio.sleep(2)

        raise last_exc or AutomatedLoginFailed("Login failed after all attempts")

    except (AutomatedLoginFailed, LoginCredentialsMissing) as exc:
        logger.error("realtime_browser: login failed: {}", exc)
        _write_login_event(settings, "login_failed", str(exc))
        set_login_status("failed")
        with closing(connect(settings.database_file)) as conn:
            init_db(conn)
            TaskRepository(conn).update(
                task_id, "error", pid=os.getpid(), last_error=str(exc)
            )
        return False


# ---------------------------------------------------------------------------
# 主协程
# ---------------------------------------------------------------------------


async def run_realtime_browser(task_id: int) -> None:
    """拥有 Chrome 窗口：管理登录状态并可选择收集 WebSocket 新闻。"""
    settings = get_settings()

    set_login_status("checking")

    with closing(connect(settings.database_file)) as conn:
        init_db(conn)
        TaskRepository(conn).update(
            task_id, "running", pid=os.getpid(), last_started_at=cst_now(), last_error=""
        )

    collect_stats: dict = {"inserted": 0, "skipped": 0, "cycles": 0}

    async with async_playwright() as pw:
        while True:  # 重启循环 — 在 KeyboardInterrupt 时退出
            _browser_start_time = asyncio.get_event_loop().time()
            storage_state_path = settings.storage_state_file
            has_valid_state = (
                storage_state_path.exists()
                and storage_state_path.stat().st_size > 50
            )
            storage_state = str(storage_state_path) if has_valid_state else None
            browser = await pw.chromium.launch(
                channel=settings.browser_channel,
                headless=settings.headless,
                args=background_window_args(settings.headless, settings.browser_memory_optimize),
            )
            context = await browser.new_context(
                storage_state=storage_state,
                viewport={"width": 1920, "height": 1080},
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                locale="en-US",
                timezone_id="America/New_York",
                extra_http_headers={
                    "Accept-Language": "en-US,en;q=0.9",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                },
            )
            await block_images(context)
            page = await context.new_page()
    
            # recheck_event：登录状态可能发生变化时设置
            recheck_event = asyncio.Event()
    
            # 暴露 Python 回调，以便 JS MutationObserver 能立即通知我们
            def _on_login_form_detected() -> None:
                logger.info("realtime_browser: MutationObserver detected login form")
                recheck_event.set()
    
            await page.expose_function("__fjLoginNeeded", _on_login_form_detected)
    
            async def _inject_login_observer() -> None:
                """注入一个 MutationObserver，当登录表单出现时触发 __fjLoginNeeded。"""
                try:
                    await page.evaluate("""() => {
                        if (window.__fjLoginObserver) {
                            window.__fjLoginObserver.disconnect();
                            window.__fjLoginObserver = null;
                        }
                        const SELECTORS = [
                            'input[type="password"]',
                            '#signup',
                            '#LoginTab',
                            'form input[name="password"]',
                            'form input[name="Password"]',
                        ];
                        function isShown(el) {
                            // Walk up the tree; stop if any ancestor is display:none.
                            // Works for position:fixed elements (offsetParent is always null there).
                            let node = el;
                            while (node && node !== document.documentElement) {
                                if (getComputedStyle(node).display === 'none') return false;
                                node = node.parentElement;
                            }
                            return true;
                        }
                        function hasLoginForm() {
                            return SELECTORS.some(sel => {
                                const el = document.querySelector(sel);
                                return el && isShown(el);
                            });
                        }
                        let notifying = false;
                        window.__fjLoginObserver = new MutationObserver(() => {
                            if (!notifying && hasLoginForm()) {
                                notifying = true;
                                window.__fjLoginNeeded().finally(
                                    () => setTimeout(() => { notifying = false; }, 10000)
                                );
                            }
                        });
                        window.__fjLoginObserver.observe(document.documentElement, {
                            childList: true,
                            subtree: true,
                            attributes: true,
                            attributeFilter: ['class', 'style', 'hidden'],
                        });
                    }""")
                    logger.debug("realtime_browser: MutationObserver injected")
                except Exception as exc:
                    logger.debug("realtime_browser: MutationObserver injection failed: {}", exc)
    
            # 每次页面加载时重新注入观察器（涵盖初始加载和重新登录）
            page.on("load", lambda: asyncio.create_task(_inject_login_observer()))
    
            # suppress_nav[0] = True，当我们自己触发导航时
            suppress_nav: list = [False]
    
            # 导航到目标页面（含 Cloudflare 重试）
            for cf_retry in range(CLOUDFLARE_MAX_RETRIES):
                try:
                    await page.goto(
                        settings.target_url, wait_until="domcontentloaded", timeout=30000
                    )
                except Exception:
                    pass
                if cf_retry == 0:
                    logger.info("realtime_browser: page loaded.")
                title = (await page.evaluate("() => document.title") or "")
                if "Cloudflare" not in title:
                    break
                logger.warning(
                    "realtime_browser: Cloudflare challenge (attempt {}/{}), retrying in 8s",
                    cf_retry + 1,
                    CLOUDFLARE_MAX_RETRIES,
                )
                await asyncio.sleep(8)
            else:
                logger.error("realtime_browser: Cloudflare still blocking after {} retries", CLOUDFLARE_MAX_RETRIES)
                with closing(connect(settings.database_file)) as conn:
                    init_db(conn)
                    TaskRepository(conn).update(
                        task_id, "error", pid=os.getpid(), last_error="Cloudflare timeout"
                    )
                set_login_status("failed")
    
            # 初始登录检查
            cf_blocked = "Cloudflare" in (await page.evaluate("() => document.title") or "")
            if cf_blocked:
                logger.info("realtime_browser: Cloudflare present, entering login retry loop")
                await _do_login(page, context, task_id, settings, suppress_nav)
            elif await _page_needs_login(page) or not has_valid_state:
                if not has_valid_state:
                    logger.info("realtime_browser: no saved session, forcing login.")
                await _do_login(page, context, task_id, settings, suppress_nav)
            else:
                set_login_status("ok")
                with closing(connect(settings.database_file)) as conn:
                    init_db(conn)
                    TaskRepository(conn).update(task_id, "running", pid=os.getpid(), last_error="")
    
            # 注册实时新闻拦截器
            page.on(
                "response",
                lambda r: asyncio.create_task(
                    _handle_news_response(r, task_id, collect_stats, settings)
                ),
            )
    
            def _on_websocket(ws) -> None:
                logger.info("realtime_browser: WebSocket opened: {}", ws.url)
                if "rt.financialjuice.com" not in ws.url:
                    return
                ws.on(
                    "framereceived",
                    lambda payload: _handle_ws_frame(
                        payload, task_id, collect_stats, settings
                    ),
                )
    
            page.on("websocket", _on_websocket)
    
            def _on_framenavigated(frame) -> None:
                if frame != page.main_frame or suppress_nav[0]:
                    return
                logger.info("realtime_browser: external navigation to {}", frame.url)
                recheck_event.set()
    
            page.on("framenavigated", _on_framenavigated)
            logger.info("realtime_browser: task_id={} collection active", task_id)
            _log(settings, task_id, "INFO", "browser launched, collection active")
    
            last_check = asyncio.get_event_loop().time()
    
            async def _run_login_check(reason: str = "periodic") -> None:
                nonlocal last_check
                last_check = asyncio.get_event_loop().time()
                logger.info("realtime_browser: login check ({}), page url={}", reason, page.url)
                set_login_status("checking")
                try:
                    needs_login = await _page_needs_login(page)
                except Exception as exc:
                    logger.warning("realtime_browser: error checking login state: {}", exc)
                    with closing(connect(settings.database_file)) as conn:
                        init_db(conn)
                        TaskRepository(conn).update(
                            task_id, "error", pid=os.getpid(), last_error=str(exc)
                        )
                    return
                logger.info("realtime_browser: needs_login={}", needs_login)
                if needs_login:
                    logger.info("realtime_browser: session expired, re-logging in.")
                    _write_login_event(settings, "logged_out", f"reason={reason}")
                    await _do_login(page, context, task_id, settings, suppress_nav)
                else:
                    set_login_status("ok")
                    with closing(connect(settings.database_file)) as conn:
                        init_db(conn)
                        TaskRepository(conn).update(
                            task_id, "running", pid=os.getpid(), last_error=""
                        )
    
            try:
                while True:
                    try:
                        await asyncio.wait_for(
                            recheck_event.wait(),
                            timeout=settings.login_check_interval_seconds,
                        )
                        recheck_event.clear()
                        # 短暂暂停，让页面完成稳定
                        await asyncio.sleep(2)
                        await _run_login_check(reason="event")
                    except asyncio.TimeoutError:
                        await _run_login_check(reason="periodic")
    
                    # 浏览器健康检查（周期性 — 每次登录检查后运行）
                    _elapsed = asyncio.get_event_loop().time() - _browser_start_time
                    if _elapsed >= settings.browser_restart_hours * 3600:
                        logger.warning(
                            "realtime_browser: scheduled restart (%.1f hours)",
                            _elapsed / 3600,
                        )
                        _log(settings, task_id, "WARNING", f"scheduled restart after {_elapsed/3600:.1f}h")
                        await _save_context_state(context)
                        raise RestartBrowser()
                    if not browser.is_connected():
                        logger.warning("realtime_browser: browser disconnected")
                        _log(settings, task_id, "WARNING", "browser disconnected, restarting")
                        raise RestartBrowser()
                    _rss_kb = _get_browser_rss(browser)
                    if _rss_kb and settings.browser_max_memory_mb and _rss_kb > settings.browser_max_memory_mb * 1024:
                        logger.warning(
                            "realtime_browser: memory threshold exceeded (%d MB > %d MB)",
                            _rss_kb // 1024,
                            settings.browser_max_memory_mb,
                        )
                        _log(settings, task_id, "WARNING",
                             f"memory {_rss_kb//1024}MB > {settings.browser_max_memory_mb}MB, restarting")
                        await _save_context_state(context)
                        raise RestartBrowser()

    
            except RestartBrowser:
                logger.warning("realtime_browser: restarting browser")
                _log(settings, task_id, "INFO", "browser restarted")
            except (asyncio.CancelledError, KeyboardInterrupt):
                _log(settings, task_id, "INFO", "browser stopped by user")
                break
            finally:
                try:
                    ensure_parent_dir(settings.storage_state_file)
                    await context.storage_state(path=str(settings.storage_state_file))
                    logger.info(
                        "realtime_browser: saved storage state to {}",
                        settings.storage_state_file,
                    )
                except Exception:
                    pass
                await context.close()
                await browser.close()

    with closing(connect(settings.database_file)) as conn:
        init_db(conn)
        TaskRepository(conn).update(
            task_id, "stopped", pid=os.getpid(), last_finished_at=cst_now()
        )
    set_login_status("failed")
    logger.info(
        "realtime_browser: stopped. inserted={} skipped={} cycles={}",
        collect_stats["inserted"],
        collect_stats["skipped"],
        collect_stats["cycles"],
    )


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python -m app.services.realtime_browser <task_id>", file=sys.stderr)
        sys.exit(1)
    try:
        task_id = int(sys.argv[1])
    except ValueError:
        print(f"Invalid task_id: {sys.argv[1]!r}", file=sys.stderr)
        sys.exit(1)

    settings = get_settings()
    settings.log_dir.mkdir(parents=True, exist_ok=True)
    logger.add(
        str(settings.log_dir / "realtime_browser.log"),
        rotation="10 MB",
        retention=5,
        encoding="utf-8",
        level="DEBUG",
    )

    try:
        asyncio.run(run_realtime_browser(task_id))
    except KeyboardInterrupt:
        logger.info("realtime_browser: stopped by user")
        print("realtime_browser stopped.", flush=True)


if __name__ == "__main__":
    main()
