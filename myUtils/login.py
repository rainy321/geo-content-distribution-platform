import asyncio
import json
import sqlite3
from contextlib import closing

from playwright.async_api import async_playwright

from myUtils.auth import check_cookie
from utils.base_social_media import set_init_script
import uuid
from pathlib import Path
from conf import BASE_DIR, LOCAL_CHROME_HEADLESS, LOCAL_CHROME_PATH
from uploader.bilibili_uploader.runtime import run_biliup_command


_ZHIHU_LOGIN_ERROR_MESSAGES = {
    "browser_launch_failed": "无法启动 Chrome，请关闭冲突窗口后重试",
    "login_page_unavailable": "知乎登录页未成功打开，请检查网络后重试",
    "login_timeout": "等待登录超时，请在弹出的 Chrome 中完成验证后重试",
    "login_interrupted": "知乎登录窗口已关闭或流程中断，请重新尝试",
    "browser_closed": "Chrome 登录窗口已关闭，请重新尝试",
    "operator_cancelled": "已结束本次知乎登录等待",
    "session_save_failed": "登录已完成但会话保存失败，请检查本机数据目录权限",
    "unexpected_error": "知乎登录发生异常，请重试并查看本机日志",
}

_ZHIHU_LOGIN_ERROR_ACTIONS = {
    "browser_launch_failed": "关闭冲突的浏览器窗口后重试",
    "login_page_unavailable": "检查网络连接后重试",
    "login_timeout": "重新打开登录并在十分钟内完成验证",
    "login_interrupted": "重新尝试登录",
    "browser_closed": "重新打开登录窗口",
    "operator_cancelled": "无需操作",
    "session_save_failed": "检查本机数据目录权限后重试",
    "unexpected_error": "重试；如仍失败，请查看本机日志",
}


def _queue_login_event(status_queue, event_type: str, **fields) -> None:
    """Send a structured, secret-free event alongside the legacy SSE tokens."""

    if int(getattr(status_queue, "protocol_version", 1)) < 2:
        return
    payload = {"type": str(event_type)}
    payload.update(
        {
            str(key): str(value)
            for key, value in fields.items()
            if value is not None and str(value)
        }
    )
    status_queue.put(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    )


def _queue_zhihu_login_error(status_queue, code: str) -> None:
    normalized_code = (
        code if code in _ZHIHU_LOGIN_ERROR_MESSAGES else "unexpected_error"
    )
    _queue_login_event(
        status_queue,
        "error",
        code=normalized_code,
        message=_ZHIHU_LOGIN_ERROR_MESSAGES[normalized_code],
        action=_ZHIHU_LOGIN_ERROR_ACTIONS[normalized_code],
    )
    # Keep the original terminal token so older desktop frontends terminate
    # their EventSource instead of leaving a stale login session registered.
    status_queue.put("500")


def _login_database_path(database_path=None):
    return (
        Path(database_path).expanduser().resolve()
        if database_path is not None
        else Path(BASE_DIR / "db" / "database.db")
    )


def _login_cookies_directory(cookies_directory=None):
    return (
        Path(cookies_directory).expanduser().resolve()
        if cookies_directory is not None
        else Path(BASE_DIR / "cookiesFile")
    )


def _safe_login_log(
    platform: str,
    event: str,
    *,
    level: str = "info",
    error: BaseException | None = None,
) -> None:
    """Write a secret-free login diagnostic without depending on a console.

    Windowed PyInstaller processes can expose ``sys.stdout`` as ``None`` and
    Windows consoles can reject Unicode output under a legacy code page. A
    diagnostic must never abort the account-login coroutine, so this helper
    writes an ASCII-only event to the existing UTF-8 file logger and contains
    failures from the logging path itself. Business exceptions are still
    handled by their original callers and reported to the SSE queue.
    """

    normalized_level = str(level).lower()
    if normalized_level not in {"debug", "info", "success", "warning", "error"}:
        normalized_level = "info"

    normalized_platform = str(platform).lower()
    if normalized_platform not in {
        "baijiahao",
        "bilibili",
        "douyin",
        "kuaishou",
        "sohu",
        "tiktok",
        "toutiao",
        "weixin_channels",
        "xiaohongshu",
    }:
        normalized_platform = "unknown"

    normalized_event = str(event).lower()
    if normalized_event not in {
        "account_saved",
        "browser_launch_started",
        "cookie_validation_failed",
        "identity_confirmed",
        "login_failed",
        "login_page_navigation_started",
        "login_page_opened",
        "login_rejected",
        "login_started",
        "login_timeout",
        "navigation_retry",
        "qr_loaded",
        "session_id_created",
        "terminal_open_failed",
        "terminal_opened",
    }:
        normalized_event = "unknown"

    def _ascii_token(value, fallback):
        token = "".join(
            character
            for character in str(value).lower()
            if character.isascii() and (character.isalnum() or character in "_-.")
        )
        return token[:64] or fallback

    message = (
        f"login platform={normalized_platform} "
        f"event={normalized_event}"
    )
    if error is not None:
        message += f" error_type={_ascii_token(type(error).__name__, 'exception')}"

    try:
        from utils.log import login_logger

        login_logger.log(normalized_level.upper(), message)
    except Exception:
        # Logging is diagnostic-only. Never let a missing/invalid stdout sink
        # replace the actual browser, database, or platform outcome.
        return

# 统一获取浏览器启动配置（防风控+引入本地浏览器）
def get_browser_options():
    options = {
        'headless': LOCAL_CHROME_HEADLESS,
        'args': [
            '--disable-blink-features=AutomationControlled',  # 核心防爬屏蔽：去掉 window.navigator.webdriver 标签
            '--lang=zh-CN',
            '--disable-infobars',
            '--start-maximized'
        ]
    }
    # 如果用户在 conf.py 里配置了本地 Chrome，就用本地的，这样成功率极高
    if LOCAL_CHROME_PATH:
        options['executable_path'] = LOCAL_CHROME_PATH
    else:
        options['channel'] = 'chrome'

    return options


async def wait_for_toutiao_creator_backend(
    page,
    *,
    timeout_seconds=600,
    poll_interval_seconds=1,
):
    """Detect Toutiao's SPA login transition without waiting for page load."""

    if timeout_seconds < 0 or poll_interval_seconds <= 0:
        raise ValueError("等待时间必须为非负数，轮询间隔必须大于 0")
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    while True:
        current_url = str(page.url or "")
        if "/profile_v4" in current_url and "/auth/page/login" not in current_url:
            return True
        remaining = deadline - loop.time()
        if remaining <= 0:
            return False
        await page.wait_for_timeout(
            max(1, int(min(poll_interval_seconds, remaining) * 1000))
        )


async def _wait_for_interactive_login(
    page,
    context,
    *,
    original_url,
    authenticated_cookie_names=(),
    authenticated_url_fragments=(),
    timeout_seconds=600,
):
    """Wait for a headed login without relying on a single navigation event."""

    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(30, int(timeout_seconds))
    cookie_markers = {str(name) for name in authenticated_cookie_names}
    while loop.time() < deadline:
        current_url = str(page.url or "")
        if any(fragment in current_url for fragment in authenticated_url_fragments):
            return True
        cookie_names = {
            str(item.get("name") or "") for item in await context.cookies()
        }
        if cookie_markers.intersection(cookie_names):
            return True
        # Some creator centers complete login via an SPA route that does not emit
        # the main-frame event used by the previous implementation.
        if current_url != original_url and not any(
            marker in current_url.lower()
            for marker in ("/login", "/passport", "/auth/")
        ):
            return True
        await page.wait_for_timeout(1000)
    return False

# 抖音登录
async def douyin_cookie_gen(
    id, status_queue, *, database_path=None, cookies_directory=None,
    timeout_seconds=600,
):
    async with async_playwright() as playwright:
        options = get_browser_options()
        options["headless"] = False
        # Login must stay visible so the account owner can complete QR/security checks.
        browser = await playwright.chromium.launch(**options)
        # Setup context however you like.
        context = await browser.new_context()  # Pass any options
        context = await set_init_script(context)
        # Pause the page, and start recording manually.
        page = await context.new_page()
        await page.goto("https://creator.douyin.com/")
        original_url = page.url
        img_locator = page.get_by_role("img", name="二维码")
        # 获取 src 属性值
        src = await img_locator.get_attribute("src")
        _safe_login_log("douyin", "qr_loaded", level="success")
        status_queue.put(src)
        authenticated = await _wait_for_interactive_login(
            page,
            context,
            original_url=original_url,
            authenticated_cookie_names=("sessionid", "sessionid_ss", "sid_guard"),
            authenticated_url_fragments=("/creator-micro/",),
            timeout_seconds=timeout_seconds,
        )
        if not authenticated:
            _safe_login_log("douyin", "login_timeout", level="warning")
            await page.close()
            await context.close()
            await browser.close()
            status_queue.put("500")
            return None
        uuid_v1 = uuid.uuid1()
        _safe_login_log("douyin", "session_id_created")
        # 确保cookiesFile目录存在
        cookies_dir = _login_cookies_directory(cookies_directory)
        cookies_dir.mkdir(parents=True, exist_ok=True)
        await context.storage_state(path=cookies_dir / f"{uuid_v1}.json")
        result = await check_cookie(
            3, f"{uuid_v1}.json", cookies_directory=cookies_dir
        )
        if not result:
            status_queue.put("500")
            await page.close()
            await context.close()
            await browser.close()
            return None
        await page.close()
        await context.close()
        await browser.close()
        with closing(sqlite3.connect(_login_database_path(database_path))) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                                INSERT INTO user_info (type, filePath, userName, status)
                                VALUES (?, ?, ?, ?)
                                ''', (3, f"{uuid_v1}.json", id, 1))
            conn.commit()
            _safe_login_log("douyin", "account_saved", level="success")
        status_queue.put("200")


# 视频号登录
async def get_tencent_cookie(
    id, status_queue, *, database_path=None, cookies_directory=None,
    timeout_seconds=600,
):
    async with async_playwright() as playwright:
        options = get_browser_options()
        options['args'] = ['--lang=en-GB']
        options['headless'] = False
        # Make sure to run headed.
        browser = await playwright.chromium.launch(**options)
        # Setup context however you like.
        context = await browser.new_context()  # Pass any options
        # Pause the page, and start recording manually.
        context = await set_init_script(context)
        page = await context.new_page()
        await page.goto("https://channels.weixin.qq.com")
        original_url = page.url

        # 等待 iframe 出现（最多等 60 秒）
        iframe_locator = page.frame_locator("iframe").first

        # 获取 iframe 中的第一个 img 元素
        img_locator = iframe_locator.get_by_role("img").first

        # 获取 src 属性值
        src = await img_locator.get_attribute("src")
        _safe_login_log("weixin_channels", "qr_loaded", level="success")
        status_queue.put(src)

        authenticated = await _wait_for_interactive_login(
            page,
            context,
            original_url=original_url,
            authenticated_cookie_names=("wxuin", "data_ticket", "ticket"),
            authenticated_url_fragments=("/platform/",),
            timeout_seconds=timeout_seconds,
        )
        if not authenticated:
            status_queue.put("500")
            _safe_login_log("weixin_channels", "login_timeout", level="warning")
            await page.close()
            await context.close()
            await browser.close()
            return None
        uuid_v1 = uuid.uuid1()
        _safe_login_log("weixin_channels", "session_id_created")
        # 确保cookiesFile目录存在
        cookies_dir = _login_cookies_directory(cookies_directory)
        cookies_dir.mkdir(parents=True, exist_ok=True)
        await context.storage_state(path=cookies_dir / f"{uuid_v1}.json")
        result = await check_cookie(
            2, f"{uuid_v1}.json", cookies_directory=cookies_dir
        )
        if not result:
            status_queue.put("500")
            await page.close()
            await context.close()
            await browser.close()
            return None
        await page.close()
        await context.close()
        await browser.close()

        with closing(sqlite3.connect(_login_database_path(database_path))) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                                INSERT INTO user_info (type, filePath, userName, status)
                                VALUES (?, ?, ?, ?)
                                ''', (2, f"{uuid_v1}.json", id, 1))
            conn.commit()
            _safe_login_log("weixin_channels", "account_saved", level="success")
        status_queue.put("200")

# 快手登录
async def get_ks_cookie(
    id, status_queue, *, database_path=None, cookies_directory=None,
    timeout_seconds=600,
):
    async with async_playwright() as playwright:
        options = get_browser_options()
        options['args'] = ['--lang=en-GB']
        options['headless'] = False
        # Make sure to run headed.
        browser = await playwright.chromium.launch(**options)
        # Setup context however you like.
        context = await browser.new_context()  # Pass any options
        context = await set_init_script(context)
        # Pause the page, and start recording manually.
        page = await context.new_page()
        await page.goto("https://cp.kuaishou.com")

        # 定位并点击“立即登录”按钮（类型为 link）
        await page.get_by_role("link", name="立即登录").click()
        await page.get_by_text("扫码登录").click()
        img_locator = page.get_by_role("img", name="qrcode")
        # 获取 src 属性值
        src = await img_locator.get_attribute("src")
        original_url = page.url
        _safe_login_log("kuaishou", "qr_loaded", level="success")
        status_queue.put(src)
        authenticated = await _wait_for_interactive_login(
            page,
            context,
            original_url=original_url,
            authenticated_cookie_names=("userId", "kuaishou.server.web_st"),
            authenticated_url_fragments=("/profile", "/article/", "/dashboard"),
            timeout_seconds=timeout_seconds,
        )
        if not authenticated:
            status_queue.put("500")
            _safe_login_log("kuaishou", "login_timeout", level="warning")
            await page.close()
            await context.close()
            await browser.close()
            return None
        uuid_v1 = uuid.uuid1()
        _safe_login_log("kuaishou", "session_id_created")
        # 确保cookiesFile目录存在
        cookies_dir = _login_cookies_directory(cookies_directory)
        cookies_dir.mkdir(parents=True, exist_ok=True)
        await context.storage_state(path=cookies_dir / f"{uuid_v1}.json")
        result = await check_cookie(
            4, f"{uuid_v1}.json", cookies_directory=cookies_dir
        )
        if not result:
            status_queue.put("500")
            await page.close()
            await context.close()
            await browser.close()
            return None
        await page.close()
        await context.close()
        await browser.close()

        with closing(sqlite3.connect(_login_database_path(database_path))) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                                        INSERT INTO user_info (type, filePath, userName, status)
                                        VALUES (?, ?, ?, ?)
                                        ''', (4, f"{uuid_v1}.json", id, 1))
            conn.commit()
            _safe_login_log("kuaishou", "account_saved", level="success")
        status_queue.put("200")

# 小红书登录
async def xiaohongshu_cookie_gen(
    id, status_queue, *, database_path=None, cookies_directory=None
):
    """Run the maintained Xiaohongshu QR flow and persist the account locally."""

    from uploader.xiaohongshu_uploader.main import (
        xiaohongshu_cookie_gen as run_xiaohongshu_login,
    )

    status_queue.put("MANUAL_LOGIN")
    uuid_v1 = uuid.uuid1()
    file_name = f"{uuid_v1}.json"
    cookies_dir = _login_cookies_directory(cookies_directory)
    cookies_dir.mkdir(parents=True, exist_ok=True)
    account_file = cookies_dir / file_name

    try:
        result = await run_xiaohongshu_login(
            str(account_file),
            headless=False,
        )
        if not result.get("success"):
            _safe_login_log("xiaohongshu", "login_rejected", level="warning")
            status_queue.put("500")
            return None

        with closing(sqlite3.connect(_login_database_path(database_path))) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO user_info (type, filePath, userName, status)
                VALUES (?, ?, ?, ?)
                """,
                (1, file_name, id, 1),
            )
            conn.commit()
            _safe_login_log("xiaohongshu", "account_saved", level="success")
        status_queue.put("200")
        return True
    except Exception as exc:
        _safe_login_log("xiaohongshu", "login_failed", level="error", error=exc)
        status_queue.put("500")
        return None

# 百家号登录（手动登录，非二维码）
async def baijiahao_cookie_gen(
    id, status_queue, *, database_path=None, cookies_directory=None
):
    browser = None
    context = None
    page = None
    try:
        status_queue.put("MANUAL_LOGIN")
        _safe_login_log("baijiahao", "login_started")
        async with async_playwright() as playwright:
            options = get_browser_options()
            options['headless'] = False
            browser = await playwright.chromium.launch(**options)
            context = await browser.new_context()
            context = await set_init_script(context)
            page = await context.new_page()
            await page.goto(
                "https://baijiahao.baidu.com/builder/theme/bjh/login",
                timeout=60000,
                wait_until="domcontentloaded",
            )
            _safe_login_log("baijiahao", "login_page_opened")
            try:
                await page.wait_for_url(
                    "https://baijiahao.baidu.com/builder/rc/**",
                    timeout=200_000,
                )
                _safe_login_log("baijiahao", "identity_confirmed", level="success")
            except asyncio.TimeoutError:
                _safe_login_log("baijiahao", "login_timeout", level="warning")
                status_queue.put("500")
                return None

            uuid_v1 = uuid.uuid1()
            _safe_login_log("baijiahao", "session_id_created")
            cookies_dir = _login_cookies_directory(cookies_directory)
            cookies_dir.mkdir(parents=True, exist_ok=True)
            await context.storage_state(path=cookies_dir / f"{uuid_v1}.json")

            result = await check_cookie(
                5, f"{uuid_v1}.json", cookies_directory=cookies_dir
            )
            if not result:
                _safe_login_log(
                    "baijiahao", "cookie_validation_failed", level="warning"
                )

            with closing(sqlite3.connect(_login_database_path(database_path))) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO user_info (type, filePath, userName, status)
                    VALUES (?, ?, ?, ?)
                ''', (5, f"{uuid_v1}.json", id, 1 if result else 0))
                conn.commit()
                _safe_login_log("baijiahao", "account_saved", level="success")
            status_queue.put("200")
            return True
    except Exception as e:
        _safe_login_log("baijiahao", "login_failed", level="error", error=e)
        try:
            status_queue.put("500")
        except Exception:
            pass
        return None
    finally:
        try:
            if page:
                await page.close()
        except Exception:
            pass
        try:
            if context:
                await context.close()
        except Exception:
            pass
        try:
            if browser:
                await browser.close()
        except Exception:
            pass


# 今日头条登录（扫码/手动，非二维码 SSE 推送，与百家号类似）
async def toutiao_cookie_gen(
    id, status_queue, *, database_path=None, cookies_directory=None,
    timeout_seconds=600,
):
    browser = None
    context = None
    page = None
    try:
        # 尽早通知前端进入「手动登录」态，避免浏览器启动慢时一直转圈
        status_queue.put("MANUAL_LOGIN")
        _safe_login_log("toutiao", "login_started")
        async with async_playwright() as playwright:
            # 登录必须有头模式，方便用户扫码
            options = get_browser_options()
            options['headless'] = False
            _safe_login_log("toutiao", "browser_launch_started")
            browser = await playwright.chromium.launch(**options)
            context = await browser.new_context()
            context = await set_init_script(context)
            page = await context.new_page()
            _safe_login_log("toutiao", "login_page_navigation_started")
            await page.goto("https://mp.toutiao.com/auth/page/login", timeout=60000, wait_until="domcontentloaded")
            _safe_login_log("toutiao", "login_page_opened")
            if await wait_for_toutiao_creator_backend(
                page,
                timeout_seconds=timeout_seconds,
            ):
                _safe_login_log("toutiao", "identity_confirmed", level="success")
            else:
                _safe_login_log("toutiao", "login_timeout", level="warning")
                status_queue.put("500")
                return None

            uuid_v1 = uuid.uuid1()
            _safe_login_log("toutiao", "session_id_created")
            cookies_dir = _login_cookies_directory(cookies_directory)
            cookies_dir.mkdir(parents=True, exist_ok=True)
            await context.storage_state(path=cookies_dir / f"{uuid_v1}.json")

            # cookie 校验失败也不要卡死前端；仍写入账号，状态后续可再验证
            result = await check_cookie(
                7, f"{uuid_v1}.json", cookies_directory=cookies_dir
            )
            if not result:
                _safe_login_log(
                    "toutiao", "cookie_validation_failed", level="warning"
                )

            with closing(sqlite3.connect(_login_database_path(database_path))) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO user_info (type, filePath, userName, status)
                    VALUES (?, ?, ?, ?)
                ''', (7, f"{uuid_v1}.json", id, 1 if result else 0))
                conn.commit()
                _safe_login_log("toutiao", "account_saved", level="success")
            status_queue.put("200")
            return True
    except Exception as e:
        _safe_login_log("toutiao", "login_failed", level="error", error=e)
        try:
            status_queue.put("500")
        except Exception:
            pass
            return None
    finally:
        try:
            if page:
                await page.close()
        except Exception:
            pass
        try:
            if context:
                await context.close()
        except Exception:
            pass
        try:
            if browser:
                await browser.close()
        except Exception:
            pass


async def sohu_cookie_gen(
    id, status_queue, *, database_path=None, cookies_directory=None
):
    browser = None
    context = None
    page = None
    try:
        status_queue.put("MANUAL_LOGIN")
        _safe_login_log("sohu", "login_started")
        async with async_playwright() as playwright:
            options = get_browser_options()
            options['headless'] = False
            _safe_login_log("sohu", "browser_launch_started")
            browser = await playwright.chromium.launch(**options)
            # 搜狐后台为 qiankun 微前端，禁止注入 stealth（会破坏 createElement）
            context = await browser.new_context()
            page = await context.new_page()
            _safe_login_log("sohu", "login_page_navigation_started")
            await page.goto("https://mp.sohu.com/mpfe/v4/login", timeout=60000, wait_until="domcontentloaded")
            _safe_login_log("sohu", "login_page_opened")
            try:
                await page.wait_for_url(
                    lambda url: (
                        "mp.sohu.com" in str(url)
                        and "/login" not in str(url).lower()
                        and "passport.sohu.com" not in str(url).lower()
                    ),
                    timeout=200_000,
                )
                _safe_login_log("sohu", "identity_confirmed", level="success")
            except asyncio.TimeoutError:
                _safe_login_log("sohu", "login_timeout", level="warning")
                status_queue.put("500")
                return None

            uuid_v1 = uuid.uuid1()
            _safe_login_log("sohu", "session_id_created")
            cookies_dir = _login_cookies_directory(cookies_directory)
            cookies_dir.mkdir(parents=True, exist_ok=True)
            await context.storage_state(path=cookies_dir / f"{uuid_v1}.json")

            result = await check_cookie(
                8, f"{uuid_v1}.json", cookies_directory=cookies_dir
            )
            if not result:
                _safe_login_log("sohu", "cookie_validation_failed", level="warning")

            with closing(sqlite3.connect(_login_database_path(database_path))) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO user_info (type, filePath, userName, status)
                    VALUES (?, ?, ?, ?)
                ''', (8, f"{uuid_v1}.json", id, 1 if result else 0))
                conn.commit()
                _safe_login_log("sohu", "account_saved", level="success")
            status_queue.put("200")
            return True
    except Exception as e:
        _safe_login_log("sohu", "login_failed", level="error", error=e)
        try:
            status_queue.put("500")
        except Exception:
            pass
        return None
    finally:
        try:
            if page:
                await page.close()
        except Exception:
            pass
        try:
            if context:
                await context.close()
        except Exception:
            pass
        try:
            if browser:
                await browser.close()
        except Exception:
            pass


async def zhihu_cookie_gen(
    id, status_queue, *, database_path=None, cookies_directory=None
):
    browser = None
    context = None
    page = None
    new_cookie_path = None
    database_saved = False
    stage = "browser_launch"
    try:
        _queue_login_event(
            status_queue,
            "stage",
            stage="opening_browser",
            message="正在打开 Chrome 知乎登录页",
            action="等待 Chrome 登录窗口打开",
        )
        from utils.log import zhihu_logger

        zhihu_logger.info("开始知乎人工登录")
        async with async_playwright() as playwright:
            options = get_browser_options()
            options['headless'] = False
            browser = await playwright.chromium.launch(**options)
            stage = "context_create"
            context = await browser.new_context()
            context = await set_init_script(context)
            page = await context.new_page()
            stage = "login_page"
            from uploader.zhihu_uploader.main import (
                ZHIHU_LOGIN_URL,
                ZHIHU_LOGIN_TIMEOUT_MS,
                ZhihuLoginControlError,
                _goto_zhihu_page,
                _wait_until_logged_in,
            )

            await _goto_zhihu_page(
                page,
                ZHIHU_LOGIN_URL,
                timeout_ms=60_000,
            )
            # Emit the legacy manual-login stage only after Chrome has actually
            # reached a usable page.  Protocol 2 clients then receive the more
            # specific structured stage immediately afterwards.
            status_queue.put("MANUAL_LOGIN")
            _queue_login_event(
                status_queue,
                "stage",
                stage="waiting_for_account",
                message="请在 Chrome 中完成知乎登录和必要验证",
                action="在十分钟内完成账号登录和必要验证",
            )
            zhihu_logger.info("知乎登录页已打开，等待账号身份确认")
            stage = "login_wait"
            ok = await _wait_until_logged_in(
                page,
                context,
                timeout_ms=ZHIHU_LOGIN_TIMEOUT_MS,
                cancel_event=getattr(status_queue, "cancel_event", None),
            )
            if not ok:
                zhihu_logger.warning("知乎登录超时：未确认账号身份")
                _queue_zhihu_login_error(status_queue, "login_timeout")
                return None
            cancel_event = getattr(status_queue, "cancel_event", None)
            if cancel_event is not None and cancel_event.is_set():
                raise ZhihuLoginControlError(
                    "知乎登录已由操作者取消",
                    code="operator_cancelled",
                )
            zhihu_logger.success("知乎登录成功，账号身份已确认")

            stage = "session_save"
            uuid_v1 = uuid.uuid1()
            cookies_dir = _login_cookies_directory(cookies_directory)
            cookies_dir.mkdir(parents=True, exist_ok=True)
            new_cookie_path = cookies_dir / f"{uuid_v1}.json"
            await context.storage_state(path=new_cookie_path)

            # The session was just verified in this live context using z_c0 or
            # Zhihu's account-bound identity endpoint.  Starting a second
            # browser here used to turn a successful login into a false 500 if
            # that redundant check hit a transient launch/network failure.
            # Regular account checks and every real publish still revalidate
            # the persisted session before platform mutation.
            stage = "database_save"
            existing_account_id = getattr(
                status_queue,
                "existing_account_id",
                None,
            )
            old_cookie_filename = None
            with closing(sqlite3.connect(_login_database_path(database_path))) as conn:
                cursor = conn.cursor()
                conn.execute("BEGIN IMMEDIATE")
                try:
                    if existing_account_id is not None:
                        existing_account = cursor.execute(
                            """
                            SELECT id, filePath
                            FROM user_info
                            WHERE id = ? AND type = ? AND userName = ?
                            """,
                            (existing_account_id, 9, id),
                        ).fetchone()
                        if existing_account is None:
                            raise RuntimeError(
                                "existing Zhihu account binding mismatch"
                            )
                        old_cookie_filename = str(existing_account[1] or "")
                        cursor.execute(
                            """
                            UPDATE user_info
                            SET filePath = ?, status = 1,
                                last_checked_at = CURRENT_TIMESTAMP
                            WHERE id = ? AND type = ? AND userName = ?
                            """,
                            (new_cookie_path.name, existing_account_id, 9, id),
                        )
                        if cursor.rowcount != 1:
                            raise RuntimeError(
                                "existing Zhihu account update lost its binding"
                            )
                    else:
                        cursor.execute(
                            """
                            INSERT INTO user_info (
                                type, filePath, userName, status, last_checked_at
                            ) VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                            """,
                            (9, new_cookie_path.name, id, 1),
                        )
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise
            database_saved = True
            if old_cookie_filename and old_cookie_filename != new_cookie_path.name:
                try:
                    old_cookie_path = (cookies_dir / old_cookie_filename).resolve()
                    if old_cookie_path.parent == cookies_dir.resolve():
                        old_cookie_path.unlink(missing_ok=True)
                    else:
                        zhihu_logger.warning(
                            "忽略不安全的旧知乎会话文件路径"
                        )
                except Exception as cleanup_error:
                    zhihu_logger.warning(
                        "旧知乎会话文件清理失败 error_type={}",
                        cleanup_error.__class__.__name__,
                    )
            zhihu_logger.success("知乎账号会话已安全保存")
            _queue_login_event(
                status_queue,
                "success",
                message="知乎账号登录成功",
                action="继续配置发布任务",
            )
            status_queue.put("200")
            return True
    except Exception as e:
        from utils.log import zhihu_logger

        if new_cookie_path is not None and not database_saved:
            try:
                resolved_cookie = new_cookie_path.resolve()
                cookies_root = _login_cookies_directory(
                    cookies_directory
                ).resolve()
                if resolved_cookie.parent == cookies_root:
                    resolved_cookie.unlink(missing_ok=True)
            except Exception as cleanup_error:
                zhihu_logger.warning(
                    "知乎未登记会话文件清理失败 error_type={}",
                    cleanup_error.__class__.__name__,
                )
        error_code = {
            "browser_launch": "browser_launch_failed",
            "context_create": "browser_launch_failed",
            "login_page": "login_page_unavailable",
            "login_wait": "login_interrupted",
            "session_save": "session_save_failed",
            "database_save": "session_save_failed",
        }.get(stage, "unexpected_error")
        control_error_code = getattr(e, "login_error_code", "")
        if control_error_code in {"browser_closed", "operator_cancelled"}:
            error_code = control_error_code
            zhihu_logger.warning(
                "知乎登录结束 stage={} reason={}",
                stage,
                control_error_code,
            )
        else:
            zhihu_logger.exception(
                "知乎登录异常 stage={} error_type={}",
                stage,
                e.__class__.__name__,
            )
        _queue_zhihu_login_error(status_queue, error_code)
        return None
    finally:
        try:
            if page:
                await page.close()
        except Exception:
            pass
        try:
            if context:
                await context.close()
        except Exception:
            pass
        try:
            if browser:
                await browser.close()
        except Exception:
            pass


# Bilibili登录（通过biliup CLI，在新终端窗口中扫码）
async def bilibili_cookie_gen(
    id, status_queue, *, database_path=None, cookies_directory=None,
    timeout_seconds=600,
):
    import subprocess
    import sys
    from uploader.bilibili_uploader.runtime import ensure_biliup_binary

    account_dir = _login_cookies_directory(cookies_directory)
    account_dir.mkdir(parents=True, exist_ok=True)
    account_file = account_dir / f"bilibili_{id}.json"

    # 获取正确的biliup二进制路径（不是pip安装的Python版）
    biliup_path = str(ensure_biliup_binary(force_check=False))

    # 通知前端已打开终端，请手动登录
    status_queue.put("MANUAL_LOGIN")

    try:
        # 在新终端窗口中运行 biliup login
        if sys.platform == 'win32':
            subprocess.Popen(
                [biliup_path, '-u', str(account_file), 'login'],
                creationflags=subprocess.CREATE_NEW_CONSOLE
            )
        else:
            subprocess.Popen(
                ['x-terminal-emulator', '-e', biliup_path, '-u', str(account_file), 'login']
            )
        _safe_login_log("bilibili", "terminal_opened", level="success")
    except Exception as e:
        _safe_login_log("bilibili", "terminal_open_failed", level="error", error=e)
        status_queue.put("500")
        return

    # 轮询等待cookie文件生成（用户在终端完成扫码后biliup会创建文件）
    for _ in range(max(15, int(timeout_seconds / 2))):
        await asyncio.sleep(2)
        if account_file.exists():
            result = run_biliup_command(["-u", str(account_file), "renew"])
            if result.returncode == 0:
                _safe_login_log("bilibili", "identity_confirmed", level="success")
                with closing(sqlite3.connect(_login_database_path(database_path))) as conn:
                    conn.cursor().execute(
                        'INSERT INTO user_info (type, filePath, userName, status) VALUES (?, ?, ?, ?)',
                        (6, f"bilibili_{id}.json", id, 1))
                    conn.commit()
                status_queue.put("200")
                return

    _safe_login_log("bilibili", "login_timeout", level="warning")
    status_queue.put("500")


async def tiktok_cookie_gen(
    id, status_queue, *, database_path=None, cookies_directory=None,
    timeout_seconds=600,
):
    """Open a headed TikTok login and persist the browser session for Web use."""

    status_queue.put("MANUAL_LOGIN")
    cookies_dir = _login_cookies_directory(cookies_directory)
    cookies_dir.mkdir(parents=True, exist_ok=True)
    file_name = f"tiktok_{uuid.uuid1()}.json"
    account_file = cookies_dir / file_name

    try:
        async with async_playwright() as playwright:
            options = get_browser_options()
            options["headless"] = False
            browser = await playwright.chromium.launch(**options)
            context = await browser.new_context()
            context = await set_init_script(context)
            page = await context.new_page()
            try:
                navigation_error = None
                for attempt in range(1, 4):
                    try:
                        await page.goto(
                            "https://www.tiktok.com/login?lang=en",
                            timeout=60_000,
                            wait_until="domcontentloaded",
                        )
                        navigation_error = None
                        break
                    except Exception as exc:
                        navigation_error = exc
                        _safe_login_log("tiktok", "navigation_retry", level="warning")
                        await page.wait_for_timeout(2000)
                if navigation_error is not None:
                    raise RuntimeError("TikTok 登录页连续三次连接失败") from navigation_error
                authenticated = False
                for _ in range(max(30, int(timeout_seconds))):
                    cookies = await context.cookies()
                    cookie_names = {str(item.get("name") or "") for item in cookies}
                    if cookie_names.intersection({"sessionid", "sessionid_ss", "sid_tt"}):
                        authenticated = True
                        break
                    await page.wait_for_timeout(1000)
                if not authenticated:
                    status_queue.put("500")
                    return None
                await context.storage_state(path=account_file)
            finally:
                await context.close()
                await browser.close()

        result = await check_cookie(10, file_name, cookies_directory=cookies_dir)
        with closing(sqlite3.connect(_login_database_path(database_path))) as conn:
            conn.execute(
                """
                INSERT INTO user_info (type, filePath, userName, status)
                VALUES (?, ?, ?, ?)
                """,
                (10, file_name, id, 1 if result else 0),
            )
            conn.commit()
        status_queue.put("200" if result else "500")
        return bool(result)
    except Exception as exc:
        _safe_login_log("tiktok", "login_failed", level="error", error=exc)
        status_queue.put("500")
        return None


# a = asyncio.run(xiaohongshu_cookie_gen(4,None))
