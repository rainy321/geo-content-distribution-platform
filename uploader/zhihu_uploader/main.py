# -*- coding: utf-8 -*-
"""知乎文章发布（Playwright）。第一期仅文章。"""
from __future__ import annotations

import asyncio
import os
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from playwright.async_api import (
    Page,
    TimeoutError as PlaywrightTimeoutError,
    async_playwright,
)

from conf import LOCAL_CHROME_HEADLESS, LOCAL_CHROME_PATH
from utils.base_social_media import set_init_script
from utils.log import zhihu_logger

ZHIHU_LOGIN_URL = "https://www.zhihu.com/signin"
ZHIHU_WRITE_URL = "https://zhuanlan.zhihu.com/write"
ZHIHU_CREATOR_URL = "https://www.zhihu.com/creator"
ZHIHU_ME_URL = "https://www.zhihu.com/api/v4/me"
ZHIHU_LOGIN_TIMEOUT_MS = 600_000
ZHIHU_IDENTITY_PROBE_INITIAL_DELAY_MS = 5_000
ZHIHU_IDENTITY_PROBE_INTERVAL_MS = 30_000
TITLE_MAX_LEN = 100
COVER_MAX_BYTES = 10 * 1024 * 1024
COVER_EXTS = {".jpg", ".jpeg", ".png"}

# 知乎写文章页「创作声明」下拉选项
ZHIHU_CREATION_STATEMENT_OPTIONS = (
    "包含剧透",
    "包含医疗建议",
    "虚构创作",
    "包含理财内容",
    "包含 AI 辅助创作 作者对内容负责",
    "无声明",
)
ZHIHU_CREATION_STATEMENT_DEFAULT = "无声明"


class ZhihuLoginControlError(RuntimeError):
    """An expected operator/browser stop with a stable UI-facing code."""

    def __init__(self, message: str, *, code: str):
        super().__init__(message)
        self.login_error_code = code


def _build_launch_kwargs(headless: bool) -> dict:
    options = {
        "headless": headless,
        "args": [
            "--disable-blink-features=AutomationControlled",
            "--lang=zh-CN",
            "--disable-infobars",
            "--start-maximized",
        ],
    }
    if LOCAL_CHROME_PATH:
        options["executable_path"] = LOCAL_CHROME_PATH
    return options


def _is_login_url(url: str) -> bool:
    try:
        parsed = urlsplit(str(url or ""))
    except ValueError:
        return True
    hostname = (parsed.hostname or "").lower().rstrip(".")
    path = (parsed.path or "/").lower().rstrip("/") or "/"
    if hostname == "passport.zhihu.com":
        return True
    return path == "/signin" or path.startswith("/signin/") or (
        path == "/login" or path.startswith("/login/")
    ) or path.startswith("/account/login")


def _is_trusted_zhihu_url(url: str) -> bool:
    """Reject lookalike hosts before using a page as an authentication signal."""

    try:
        parsed = urlsplit(str(url or ""))
    except ValueError:
        return False
    hostname = (parsed.hostname or "").lower().rstrip(".")
    return parsed.scheme == "https" and (
        hostname == "zhihu.com" or hostname.endswith(".zhihu.com")
    )


async def _has_auth_cookie(page: Page, context) -> bool:
    """Validate Zhihu's auth cookie only inside a trusted Zhihu page."""

    if not _is_trusted_zhihu_url(page.url):
        return False
    try:
        cookies = await context.cookies()
    except Exception:
        return False
    now = time.time()
    for cookie in cookies:
        if cookie.get("name") != "z_c0":
            continue
        if not str(cookie.get("value") or "").strip():
            continue
        domain = (
            str(cookie.get("domain") or "")
            .lower()
            .strip()
            .lstrip(".")
            .rstrip(".")
        )
        if domain != "zhihu.com" and not domain.endswith(".zhihu.com"):
            continue
        try:
            expires = float(cookie.get("expires", -1))
        except (TypeError, ValueError):
            continue
        if expires != -1 and expires <= now:
            continue
        return True
    return False


async def _has_authenticated_identity(context, *, timeout_ms=10_000) -> bool:
    """Confirm the current account through Zhihu's read-only identity endpoint.

    This is the fallback for sessions whose current cookie layout no longer
    exposes ``z_c0``.  A successful HTTP response alone is insufficient: the
    payload must carry the stable member ``url_token`` used by the existing
    publication verifier.
    """

    response = None
    try:
        if int(timeout_ms) <= 0:
            return False
        response = await context.request.get(
            ZHIHU_ME_URL,
            timeout=max(1, min(10_000, int(timeout_ms))),
        )
        if not response.ok:
            return False
        payload = await response.json()
        return isinstance(payload, dict) and bool(
            str(payload.get("url_token") or "").strip()
        )
    except Exception as exc:
        zhihu_logger.debug(
            "知乎身份兜底校验未通过: {}", exc.__class__.__name__
        )
        return False
    finally:
        if response is not None:
            try:
                await response.dispose()
            except Exception:
                pass


async def _has_authenticated_session(
    page: Page,
    context,
    *,
    probe_identity=False,
    identity_timeout_ms=10_000,
) -> bool:
    """Return true only for a strong, account-bound authentication signal."""

    if not _is_trusted_zhihu_url(page.url):
        return False
    if await _has_auth_cookie(page, context):
        return True
    if not probe_identity:
        return False
    return await _has_authenticated_identity(
        context,
        timeout_ms=identity_timeout_ms,
    )


async def _page_is_interactive_zhihu(page: Page) -> bool:
    """Check whether a timed-out navigation still produced a usable Zhihu page."""

    try:
        if page.is_closed() or not _is_trusted_zhihu_url(page.url):
            return False
        ready_state = await page.evaluate("document.readyState")
        if ready_state in {"interactive", "complete"}:
            return bool(await page.locator("body").count())
        # A slowly loading login page can remain in ``loading`` while its form
        # is already usable.  Require an actual interactive control, not merely
        # a trusted URL, before tolerating the timeout.
        return bool(
            await page.locator(
                'input, button, [role="button"], [contenteditable="true"]'
            ).count()
        )
    except Exception:
        return False


async def _goto_zhihu_page(
    page: Page,
    url: str,
    *,
    timeout_ms: int = 60_000,
) -> bool:
    """Navigate to Zhihu and tolerate only a demonstrably usable timeout.

    Returns ``True`` for a normal navigation and ``False`` when Playwright
    timed out after the trusted page had already become interactive.
    """

    try:
        await page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
        completed = True
    except PlaywrightTimeoutError:
        if not await _page_is_interactive_zhihu(page):
            raise
        completed = False
        zhihu_logger.warning("知乎页面加载超时，但页面已可交互，继续当前流程")
    try:
        await page.bring_to_front()
    except Exception:
        pass
    return completed


async def _wait_until_logged_in(
    page: Page,
    context,
    timeout_ms: int = ZHIHU_LOGIN_TIMEOUT_MS,
    *,
    cancel_event=None,
) -> bool:
    """等待人工登录完成，使用 cookie 或身份接口的强认证信号。"""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(0, int(timeout_ms)) / 1000
    step = 1000
    next_identity_probe = (
        loop.time() + ZHIHU_IDENTITY_PROBE_INITIAL_DELAY_MS / 1000
    )
    previous_url = str(page.url or "")
    while loop.time() < deadline:
        if cancel_event is not None and cancel_event.is_set():
            raise ZhihuLoginControlError(
                "知乎登录已由操作者取消",
                code="operator_cancelled",
            )
        if page.is_closed():
            raise ZhihuLoginControlError(
                "知乎登录浏览器已关闭",
                code="browser_closed",
            )
        if await _has_authenticated_session(page, context):
            return True
        current_url = str(page.url or "")
        left_login_page = (
            current_url != previous_url
            and _is_trusted_zhihu_url(current_url)
            and not _is_login_url(current_url)
        )
        now = loop.time()
        if left_login_page or now >= next_identity_probe:
            remaining_ms = max(0, int((deadline - now) * 1000))
            if await _has_authenticated_session(
                page,
                context,
                probe_identity=True,
                identity_timeout_ms=min(10_000, remaining_ms),
            ):
                return True
            next_identity_probe = (
                loop.time() + ZHIHU_IDENTITY_PROBE_INTERVAL_MS / 1000
            )
        previous_url = current_url
        remaining_ms = max(0, int((deadline - loop.time()) * 1000))
        if remaining_ms <= 0:
            break
        wait_ms = min(step, remaining_ms)
        try:
            await page.wait_for_timeout(wait_ms)
        except Exception:
            if page.is_closed():
                raise ZhihuLoginControlError(
                    "知乎登录浏览器已关闭",
                    code="browser_closed",
                ) from None
            raise
    if cancel_event is not None and cancel_event.is_set():
        raise ZhihuLoginControlError(
            "知乎登录已由操作者取消",
            code="operator_cancelled",
        )
    if page.is_closed():
        raise ZhihuLoginControlError(
            "知乎登录浏览器已关闭",
            code="browser_closed",
        )
    remaining_ms = max(0, int((deadline - loop.time()) * 1000))
    return await _has_authenticated_session(
        page,
        context,
        probe_identity=True,
        identity_timeout_ms=remaining_ms,
    )


async def cookie_auth(account_file) -> bool:
    async with async_playwright() as playwright:
        browser = None
        try:
            browser = await playwright.chromium.launch(
                **_build_launch_kwargs(headless=LOCAL_CHROME_HEADLESS)
            )
            context = await browser.new_context(storage_state=account_file)
            context = await set_init_script(context)
            page = await context.new_page()
            await _goto_zhihu_page(page, ZHIHU_WRITE_URL, timeout_ms=60_000)
            await page.wait_for_timeout(2000)
            if _is_login_url(page.url):
                zhihu_logger.error("[+] 知乎 cookie 失效")
                return False
            if await _has_authenticated_session(
                page,
                context,
                probe_identity=True,
            ):
                zhihu_logger.success("[+] 知乎 cookie 有效（账号身份已确认）")
                return True
            zhihu_logger.error("[+] 知乎 cookie 失效")
            return False
        except Exception as exc:
            zhihu_logger.warning(f"[+] cookie 校验异常，按失效处理: {exc}")
            return False
        finally:
            if browser is not None:
                await browser.close()


async def zhihu_cookie_gen(account_file):
    """CLI/本地调试：打开登录页，手动登录后保存 cookie。"""
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(**_build_launch_kwargs(headless=False))
        context = await browser.new_context()
        context = await set_init_script(context)
        page = await context.new_page()
        await _goto_zhihu_page(page, ZHIHU_LOGIN_URL, timeout_ms=60_000)
        zhihu_logger.info("请在浏览器中完成知乎登录；确认账号身份后会自动保存…")
        ok = await _wait_until_logged_in(
            page,
            context,
            timeout_ms=ZHIHU_LOGIN_TIMEOUT_MS,
        )
        if not ok:
            await context.close()
            await browser.close()
            raise RuntimeError("知乎登录超时：未确认账号身份，请重新登录")
        Path(account_file).parent.mkdir(parents=True, exist_ok=True)
        await context.storage_state(path=account_file)
        zhihu_logger.success(f"cookie saved: {account_file}")
        await context.close()
        await browser.close()


async def zhihu_setup(account_file, handle=False):
    if not os.path.exists(account_file) or not await cookie_auth(account_file):
        if not handle:
            return False
        zhihu_logger.error("cookie文件不存在或已失效，即将打开浏览器，请手动登录知乎")
        await zhihu_cookie_gen(account_file)
    return True


class ZhiHuArticle(object):
    """知乎文章发布。"""

    def __init__(
        self,
        title,
        body,
        tags,
        publish_date: datetime | int,
        account_file,
        dry_run=False,
        cover_path=None,
        creation_statement=None,
    ):
        self.title = (title or "").strip()
        if len(self.title) > TITLE_MAX_LEN:
            raise ValueError(
                f"知乎文章标题不能超过 {TITLE_MAX_LEN} 字，请返回平台预检编辑"
            )
        if not self.title:
            raise ValueError("知乎文章标题不能为空")
        self.body = (body or "").strip()
        if not self.body:
            raise ValueError("知乎文章正文不能为空")
        self.tags = tags or []
        self.publish_date = publish_date
        self.account_file = account_file
        self.dry_run = bool(dry_run)
        self.headless = LOCAL_CHROME_HEADLESS if not self.dry_run else False
        self.cover_path = self._normalize_cover(cover_path)
        self.creation_statement = self._normalize_creation_statement(creation_statement)

    @staticmethod
    def _normalize_cover(cover_path) -> str | None:
        if not cover_path or not str(cover_path).strip():
            return None
        p = Path(str(cover_path).strip())
        if not p.is_file():
            zhihu_logger.warning(f"封面不存在，将跳过: {p}")
            return None
        if p.suffix.lower() not in COVER_EXTS:
            zhihu_logger.warning(f"封面格式不支持（需 jpg/jpeg/png），将跳过: {p}")
            return None
        if p.stat().st_size > COVER_MAX_BYTES:
            zhihu_logger.warning(f"封面超过 10MB，将跳过: {p}")
            return None
        return str(p.resolve())

    @staticmethod
    def _normalize_creation_statement(creation_statement) -> str:
        if creation_statement is None or creation_statement == "":
            return ZHIHU_CREATION_STATEMENT_DEFAULT
        if isinstance(creation_statement, (list, tuple)):
            raw = next((str(x).strip() for x in creation_statement if str(x).strip()), "")
        else:
            raw = str(creation_statement).strip()
        if not raw:
            return ZHIHU_CREATION_STATEMENT_DEFAULT

        aliases = {
            "无": "无声明",
            "无特别声明": "无声明",
            "剧透": "包含剧透",
            "医疗": "包含医疗建议",
            "医疗建议": "包含医疗建议",
            "虚构": "虚构创作",
            "理财": "包含理财内容",
            "理财内容": "包含理财内容",
            "ai": "包含 AI 辅助创作 作者对内容负责",
            "AI": "包含 AI 辅助创作 作者对内容负责",
            "AI辅助": "包含 AI 辅助创作 作者对内容负责",
            "包含AI辅助创作": "包含 AI 辅助创作 作者对内容负责",
            "包含 AI 辅助创作": "包含 AI 辅助创作 作者对内容负责",
        }
        text = aliases.get(raw, aliases.get(raw.lower(), raw))
        if text in ZHIHU_CREATION_STATEMENT_OPTIONS:
            return text
        matched = next(
            (opt for opt in ZHIHU_CREATION_STATEMENT_OPTIONS if opt.startswith(text) or text in opt),
            None,
        )
        if matched:
            return matched
        zhihu_logger.warning(f"未知创作声明「{raw}」，回退为「{ZHIHU_CREATION_STATEMENT_DEFAULT}」")
        return ZHIHU_CREATION_STATEMENT_DEFAULT

    async def upload(self, playwright) -> None:
        browser = await playwright.chromium.launch(**_build_launch_kwargs(headless=self.headless))
        context = await browser.new_context(storage_state=str(self.account_file))
        context = await set_init_script(context)
        page = await context.new_page()
        try:
            await page.goto(ZHIHU_WRITE_URL, timeout=90000, wait_until="domcontentloaded")
            await page.wait_for_timeout(2000)
            if _is_login_url(page.url):
                raise RuntimeError("知乎 cookie 已失效，请重新登录")
            await self.fill_title(page)
            await self.fill_body(page)
            if self.cover_path:
                await self.upload_cover(page)
            await self.apply_creation_statement(page)
            if self.dry_run:
                zhihu_logger.warning("🛑 【仅预览不发布】已跳过点击发布按钮")
                zhihu_logger.info("👀 请在浏览器窗口核对：标题、正文、封面、创作声明")
                zhihu_logger.info("⏳ 页面将保持打开约 120 秒后自动关闭（也可手动关浏览器）")
                try:
                    preview = Path(self.account_file).with_name("zhihu_article_dry_run_preview.png")
                    await page.screenshot(full_page=True, path=str(preview))
                    zhihu_logger.info(f"📸 预览截图已保存: {preview}")
                except Exception as exc:
                    zhihu_logger.debug(f"预览截图失败: {exc}")
                for i in range(120):
                    await asyncio.sleep(1)
                    if i in (30, 60, 90):
                        zhihu_logger.info(f"⏳ 预览中…还剩约 {120 - i} 秒自动关闭")
                zhihu_logger.info("🛑 预览结束，关闭浏览器（未点击发布）")
                return
            await self.click_publish(page)
            zhihu_logger.success("知乎文章发布流程已完成点击发布")
        finally:
            await context.close()
            await browser.close()

    async def apply_creation_statement(self, page: Page) -> None:
        """选择写文章页「创作声明」下拉项。"""
        label = self.creation_statement or ZHIHU_CREATION_STATEMENT_DEFAULT
        try:
            # 创作声明在编辑区下方，先滚入视口
            for _ in range(10):
                if await page.locator('label:has-text("创作声明")').count():
                    break
                await page.mouse.wheel(0, 500)
                await page.wait_for_timeout(200)

            row = page.locator('div:has(> label:has-text("创作声明"))').first
            if not await row.count():
                zhihu_logger.warning("未找到创作声明区域，请 dry_run 人工确认")
                return

            combo = row.locator('button[role="combobox"], button.Select-button').first
            if not await combo.count():
                zhihu_logger.warning("未找到创作声明下拉按钮，请 dry_run 人工确认")
                return

            await combo.scroll_into_view_if_needed()
            current = ((await combo.inner_text()) or "").strip().replace("\n", " ")
            if current == label:
                zhihu_logger.info(f"创作声明已是「{label}」")
                return

            await combo.click(timeout=5000)
            await page.wait_for_timeout(500)

            # 选项在 Popover 中：button.Select-option[role=option]
            option = page.get_by_role("option", name=label, exact=True).first
            if not await option.count():
                option = page.locator(f'button.Select-option[role="option"]').filter(has_text=label).first
            if not await option.count():
                # 再试一次打开
                await combo.click(timeout=3000, force=True)
                await page.wait_for_timeout(600)
                option = page.get_by_role("option", name=label, exact=True).first

            if not await option.count():
                zhihu_logger.warning(f"下拉中未找到创作声明选项「{label}」")
                try:
                    await page.keyboard.press("Escape")
                except Exception:
                    pass
                return

            await option.click(timeout=5000)
            await page.wait_for_timeout(400)

            after = ((await combo.inner_text()) or "").strip().replace("\n", " ")
            if after == label or label in after:
                zhihu_logger.success(f"已选择创作声明: {label}")
            else:
                zhihu_logger.warning(f"创作声明点击后显示为「{after}」，期望「{label}」")
        except Exception as exc:
            zhihu_logger.warning(f"设置创作声明失败（可忽略）: {exc}")

    async def fill_title(self, page: Page) -> None:
        candidates = [
            page.locator('textarea[placeholder*="标题"]').first,
            page.locator('input[placeholder*="标题"]').first,
            page.get_by_placeholder("请输入标题").first,
            page.locator('[class*="WriteIndex"] textarea').first,
        ]
        title_field = None
        for loc in candidates:
            try:
                if await loc.count() and await loc.is_visible():
                    title_field = loc
                    break
            except Exception:
                continue
        if not title_field:
            raise RuntimeError("未找到知乎文章标题输入框")
        await title_field.click(force=True)
        await title_field.fill("")
        await title_field.fill(self.title)
        zhihu_logger.info(f"已填写文章标题: {self.title}")

    async def fill_body(self, page: Page) -> None:
        selectors = [
            ".public-DraftEditor-content",
            '[contenteditable="true"]',
            ".ProseMirror",
            'div[role="textbox"]',
            ".ql-editor",
        ]
        editor = None
        for sel in selectors:
            loc = page.locator(sel).first
            try:
                if await loc.count() and await loc.is_visible():
                    editor = loc
                    break
            except Exception:
                continue
        if not editor:
            raise RuntimeError("未找到知乎文章正文编辑器")
        await editor.click(force=True)
        await page.wait_for_timeout(200)
        try:
            await page.context.grant_permissions(["clipboard-read", "clipboard-write"])
        except Exception:
            pass
        await page.keyboard.press("Control+A")
        await page.keyboard.press("Backspace")
        try:
            await page.evaluate(
                """async (t) => { await navigator.clipboard.writeText(t); }""",
                self.body,
            )
            await page.keyboard.press("Control+V")
        except Exception:
            for para in self.body.split("\n"):
                await page.keyboard.type(para, delay=10)
                await page.keyboard.press("Enter")
        zhihu_logger.info(f"已填写正文，长度={len(self.body)}")

    async def upload_cover(self, page: Page) -> None:
        # 多候选：封面入口文案/上传 input；实现时以真实页面为准微调
        openers = [
            'text=添加封面',
            'text=设置封面',
            'text=文章封面',
            'button:has-text("封面")',
        ]
        for sel in openers:
            try:
                loc = page.locator(sel).first
                if await loc.count() and await loc.is_visible():
                    await loc.click(timeout=3000)
                    await page.wait_for_timeout(500)
                    break
            except Exception:
                continue
        file_input = page.locator('input[type="file"]').last
        if not await file_input.count():
            zhihu_logger.warning("未找到封面文件选择框，跳过封面")
            return
        await file_input.set_input_files(self.cover_path)
        await page.wait_for_timeout(1500)
        for confirm in ('button:has-text("确定")', 'button:has-text("完成")', 'button:has-text("确认")'):
            try:
                btn = page.locator(confirm).first
                if await btn.count() and await btn.is_visible():
                    await btn.click(timeout=2000)
                    break
            except Exception:
                continue
        zhihu_logger.info(f"已尝试上传封面: {self.cover_path}")

    async def click_publish(self, page: Page) -> None:
        candidates = [
            page.get_by_role("button", name="发布").first,
            page.locator('button:has-text("发布")').first,
        ]
        for loc in candidates:
            try:
                if await loc.count() and await loc.is_visible():
                    await loc.click(timeout=5000)
                    await page.wait_for_timeout(3000)
                    # 二次确认弹窗
                    for confirm in ('button:has-text("确定")', 'button:has-text("确认发布")'):
                        try:
                            c = page.locator(confirm).first
                            if await c.count() and await c.is_visible():
                                await c.click(timeout=3000)
                                await page.wait_for_timeout(2000)
                        except Exception:
                            pass
                    return
            except Exception:
                continue
        raise RuntimeError("未找到知乎发布按钮")

    async def main(self):
        async with async_playwright() as playwright:
            await self.upload(playwright)
