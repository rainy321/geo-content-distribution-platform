# -*- coding: utf-8 -*-
"""知乎文章发布（Playwright）。第一期仅文章。"""
from __future__ import annotations

import asyncio
import os
import re
from datetime import datetime
from pathlib import Path

from playwright.async_api import Error as PlaywrightError, Page, async_playwright, expect

from conf import LOCAL_CHROME_HEADLESS, LOCAL_CHROME_PATH
from utils.log import zhihu_logger
from services.browser_executable import browser_executable_options
from services.login_runtime import LoginCancelled
from services.zhihu_publish_flow import ZhihuAIStatementNotConfirmedError, ZhihuEditorNotReadyError

ZHIHU_LOGIN_URL = "https://www.zhihu.com/signin"
ZHIHU_WRITE_URL = "https://zhuanlan.zhihu.com/write"
ZHIHU_CREATOR_URL = "https://www.zhihu.com/creator"
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
ZHIHU_AI_CREATION_STATEMENT = "包含 AI 辅助创作 作者对内容负责"


def _build_launch_kwargs(headless: bool) -> dict:
    options = {
        "headless": headless,
        "args": [
            "--lang=zh-CN",
            "--start-maximized",
        ],
    }
    options.update(browser_executable_options(LOCAL_CHROME_PATH))
    return options


def _is_login_url(url: str) -> bool:
    u = (url or "").lower()
    return any(k in u for k in ("/signin", "/login", "passport", "account/login"))


async def _has_auth_cookie(context) -> bool:
    """知乎登录成功后会有 z_c0。"""
    try:
        cookies = await context.cookies()
    except Exception:
        return False
    return any(c.get("name") == "z_c0" for c in cookies)


async def _wait_until_logged_in(page: Page, context, timeout_ms: int = 200_000, *, cancel_event=None) -> bool:
    """等待人工登录完成（优先检测 z_c0）。"""
    elapsed = 0
    step = 1000
    while elapsed < timeout_ms:
        if cancel_event is not None and cancel_event.is_set():
            raise LoginCancelled()
        if page.is_closed():
            raise RuntimeError("Target page, context or browser has been closed")
        if await _has_auth_cookie(context):
            return True
        if (not _is_login_url(page.url)) and ("zhihu.com" in (page.url or "")):
            # 已离开登录页时再确认一次 cookie
            await page.wait_for_timeout(500)
            if cancel_event is not None and cancel_event.is_set():
                raise LoginCancelled()
            if await _has_auth_cookie(context):
                return True
        await page.wait_for_timeout(step)
        elapsed += step
    if cancel_event is not None and cancel_event.is_set():
        raise LoginCancelled()
    return await _has_auth_cookie(context)


async def cookie_auth(account_file) -> bool:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(**_build_launch_kwargs(headless=LOCAL_CHROME_HEADLESS))
        try:
            context = await browser.new_context(storage_state=account_file)
            page = await context.new_page()
            await page.goto(ZHIHU_WRITE_URL, timeout=60000)
            await page.wait_for_timeout(3000)
            if _is_login_url(page.url):
                zhihu_logger.error("[+] 知乎 cookie 失效")
                return False
            # 写文章页或创作中心标记
            markers = [
                'textarea[placeholder*="标题"]',
                'input[placeholder*="标题"]',
                'text=发布',
                'text=写文章',
            ]
            for sel in markers:
                try:
                    if await page.locator(sel).count():
                        zhihu_logger.success("[+] 知乎 cookie 有效")
                        return True
                except Exception:
                    continue
            if "zhihu.com" in page.url and not _is_login_url(page.url):
                zhihu_logger.success("[+] 知乎 cookie 有效（域名已登录）")
                return True
            zhihu_logger.error("[+] 知乎 cookie 失效")
            return False
        except Exception:
            zhihu_logger.warning("[+] cookie 校验未完成，保存后仍需重新验证")
            return False
        finally:
            await browser.close()


async def zhihu_cookie_gen(account_file):
    """CLI/本地调试：打开登录页，手动登录后保存 cookie。"""
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(**_build_launch_kwargs(headless=False))
        context = await browser.new_context()
        page = await context.new_page()
        await page.goto(ZHIHU_LOGIN_URL, timeout=60000)
        zhihu_logger.info("请在浏览器中完成知乎登录；检测到登录凭证 z_c0 后会自动保存…")
        ok = await _wait_until_logged_in(page, context, timeout_ms=200_000)
        if not ok:
            await context.close()
            await browser.close()
            raise RuntimeError("知乎登录超时：未检测到登录凭证 z_c0，请重新登录")
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
        self.title = (title or "").strip()[:TITLE_MAX_LEN]
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
        page = await context.new_page()
        try:
            await page.goto(ZHIHU_WRITE_URL, timeout=90000, wait_until="domcontentloaded")
            if _is_login_url(page.url):
                raise RuntimeError("知乎 cookie 已失效，请重新登录")
            try:
                await self._wait_for_editor_ready(page)
                await self._assert_empty_editor(page)
                await self.fill_title(page)
                await self.fill_body(page)
                await self._verify_content(page)
            except ZhihuEditorNotReadyError:
                raise
            except Exception as exc:
                zhihu_logger.warning("知乎编辑准备未完成 code=editor_preparation_failed")
                raise ZhihuEditorNotReadyError("知乎编辑器尚未就绪，需要人工核对；本次未点击发布",
                                               reason_code='editor_preparation_failed') from exc
            if self.cover_path:
                await self.upload_cover(page)
            await self.apply_creation_statement(page)
            await self._verify_content(page)
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

    async def _wait_for_editor_ready(self, page: Page, *, timeout_seconds=20.0) -> None:
        """Wait for recognizable editable fields; never click, type or submit."""
        deadline = asyncio.get_running_loop().time() + timeout_seconds

        async def ready(selectors):
            for selector in selectors:
                field = page.locator(selector).first
                if await field.count() and await field.is_visible():
                    try:
                        if await field.is_enabled(timeout=500) and await field.is_editable(timeout=500):
                            return True
                    except PlaywrightError:
                        # A visible wrapper or a control still being replaced
                        # is not an editable writer field. Keep waiting within
                        # the shared budget instead of treating it as ready.
                        continue
            return False

        title_selectors = ('textarea[placeholder*="标题"]', 'input[placeholder*="标题"]')
        body_selectors = ('.public-DraftEditor-content', '[contenteditable="true"]',
                          '.ProseMirror', 'div[role="textbox"]', '.ql-editor')
        while True:
            if await ready(title_selectors) and await ready(body_selectors):
                return
            if asyncio.get_running_loop().time() >= deadline:
                raise ZhihuEditorNotReadyError("知乎编辑器尚未就绪，需要人工核对；本次未点击发布")
            await asyncio.sleep(min(0.2, max(0, deadline-asyncio.get_running_loop().time())))

    async def apply_creation_statement(self, page: Page) -> None:
        """选择写文章页「创作声明」下拉项。"""
        label = self.creation_statement or ZHIHU_CREATION_STATEMENT_DEFAULT
        if label == ZHIHU_AI_CREATION_STATEMENT:
            # The real GEO adapter uses this label for AI-assisted articles.
            # A warning-only legacy selector must not allow the following
            # publish action to run with an absent or different declaration.
            try:
                await self._apply_required_ai_creation_statement(page, label)
            except Exception as exc:
                zhihu_logger.warning(f"知乎 AI 声明核对未完成 code=ai_statement_unconfirmed exception_type={type(exc).__name__}")
                raise ZhihuAIStatementNotConfirmedError("知乎 AI 创作声明尚未确认，需要人工核对后再发布") from exc
            return
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

    async def _apply_required_ai_creation_statement(self, page: Page, label: str, *, ready_timeout_seconds=5.0) -> None:
        def normalized(value):
            return "".join((value or "").split())

        async def visible(locator):
            return [locator.nth(index) for index in range(await locator.count())
                    if await locator.nth(index).is_visible()]

        rows = page.locator('div:has(> label:has-text("创作声明"))')
        deadline = asyncio.get_running_loop().time() + ready_timeout_seconds
        scrolls = 0
        while True:
            matches = await visible(rows)
            if len(matches) > 1:
                zhihu_logger.warning("知乎 AI 声明核对停止 code=ai_statement_region_ambiguous")
                raise RuntimeError("创作声明区域无法唯一确认")
            controls = (await visible(matches[0].locator('button[role="combobox"], button.Select-button'))
                        if len(matches) == 1 else [])
            if len(controls) > 1:
                zhihu_logger.warning("知乎 AI 声明核对停止 code=ai_statement_control_ambiguous")
                raise RuntimeError("创作声明选择框无法唯一确认")
            if len(controls) == 1 and await controls[0].is_enabled():
                combo = controls[0]
                await combo.scroll_into_view_if_needed(timeout=3000)
                break
            if asyncio.get_running_loop().time() >= deadline:
                code = 'ai_statement_region_not_ready' if len(matches) != 1 else 'ai_statement_control_not_ready'
                zhihu_logger.warning(f"知乎 AI 声明核对停止 code={code}")
                raise RuntimeError("创作声明区域或选择框尚未就绪")
            if not matches and scrolls < 3:
                await page.mouse.wheel(0, 600)
                scrolls += 1
            await asyncio.sleep(.2)
        if normalized(await combo.inner_text()) == normalized(label):
            zhihu_logger.info("知乎 AI 声明已核对 code=ai_statement_already_confirmed")
            return

        await combo.click(timeout=5000)
        candidates = page.get_by_role("option", name=label, exact=True)
        zhihu_logger.info("知乎 AI 声明核对阶段 code=ai_statement_option_wait")
        await candidates.first.wait_for(state="visible", timeout=5000)
        options = await visible(candidates)
        if len(options) != 1 or not await options[0].is_enabled():
            zhihu_logger.warning("知乎 AI 声明核对停止 code=ai_statement_option_not_unique_or_enabled")
            raise RuntimeError("AI 创作声明选项无法唯一确认")
        await options[0].click(timeout=5000)
        # Re-read the visible control. Do not treat an option click as
        # confirmation, choose another label, force a click, or submit anyway.
        expected_text = re.compile(r"^\s*" + r"\s*".join(re.escape(c) for c in normalized(label)) + r"\s*$")
        zhihu_logger.info("知乎 AI 声明核对阶段 code=ai_statement_readback_wait")
        await expect(combo).to_have_text(expected_text, timeout=5000)
        matches = await visible(rows)
        controls = (await visible(matches[0].locator('button[role="combobox"], button.Select-button'))
                    if len(matches) == 1 else [])
        if (len(controls) != 1 or not await controls[0].is_enabled()
                or normalized(await controls[0].inner_text()) != normalized(label)):
            zhihu_logger.warning("知乎 AI 声明核对停止 code=ai_statement_readback_mismatch")
            raise RuntimeError("AI 创作声明选择结果未确认")

    @staticmethod
    def _normalized_editor_text(value: str) -> str:
        return (value or '').replace('\r\n', '\n').replace('\r', '\n').replace('\u00a0', ' ')

    @staticmethod
    def _content_error(code: str, message: str):
        zhihu_logger.warning(f"知乎编辑内容核对停止 code={code}")
        return ZhihuEditorNotReadyError(message, reason_code=code)

    async def _title_field(self, page: Page):
        candidates = [
            page.locator('textarea[placeholder*="标题"]').first,
            page.locator('input[placeholder*="标题"]').first,
            page.get_by_placeholder("请输入标题").first,
            page.locator('[class*="WriteIndex"] textarea').first,
        ]
        for loc in candidates:
            if await loc.count() and await loc.is_visible() and await loc.is_enabled():
                return loc
        raise self._content_error('editor_not_ready', '未找到可用的知乎标题输入框；本次未点击发布')

    async def _body_editor(self, page: Page):
        selectors = [
            ".public-DraftEditor-content",
            '[contenteditable="true"]',
            ".ProseMirror",
            'div[role="textbox"]',
            ".ql-editor",
        ]
        for sel in selectors:
            loc = page.locator(sel).first
            if await loc.count() and await loc.is_visible() and await loc.is_enabled():
                return loc
        raise self._content_error('editor_not_ready', '未找到可用的知乎正文编辑器；本次未点击发布')

    async def _read_body_text(self, editor) -> str:
        value = await editor.evaluate('''e => {
            if (e.tagName === 'TEXTAREA' || e.tagName === 'INPUT') return e.value;
            const blocks = [...e.querySelectorAll('[data-block="true"]')].filter(block => {
                const parent = block.parentElement?.closest('[data-block="true"]');
                return !parent || !e.contains(parent);
            });
            if (blocks.length) return blocks.map(block => {
                const value = block.innerText ?? block.textContent ?? '';
                return value === '\\n' ? '' : value;
            }).join('\\n');
            return e.innerText ?? e.textContent ?? '';
        }''')
        value = self._normalized_editor_text(value)
        return '' if value == '\n' else value

    async def _assert_empty_editor(self, page: Page) -> None:
        title = await self._title_field(page)
        body = await self._body_editor(page)
        if (self._normalized_editor_text(await title.input_value()).strip()
                or (await self._read_body_text(body)).strip()):
            raise self._content_error('editor_existing_content', '知乎编辑器已有草稿内容，已停止以免覆盖；本次未点击发布')

    async def fill_title(self, page: Page) -> None:
        title = await self._title_field(page)
        if self._normalized_editor_text(await title.input_value()).strip():
            raise self._content_error('editor_existing_content', '知乎标题已有内容，已停止以免覆盖；本次未点击发布')
        await title.fill(self.title)
        if self._normalized_editor_text(await title.input_value()) != self._normalized_editor_text(self.title):
            raise self._content_error('title_readback_mismatch', '知乎标题未通过回读核对；本次未点击发布')
        zhihu_logger.info("知乎文章标题已通过回读核对")

    async def fill_body(self, page: Page) -> None:
        editor = await self._body_editor(page)
        if (await self._read_body_text(editor)).strip():
            raise self._content_error('editor_existing_content', '知乎正文已有内容，已停止以免覆盖；本次未点击发布')
        await editor.click(timeout=5000)
        # Normal browser input updates the observed Draft.js editor. Never
        # infer success from a clipboard shortcut, clear existing text, or
        # replay a partial insertion. The readback decides whether to proceed.
        await page.keyboard.insert_text(self.body)
        expected = self._normalized_editor_text(self.body)
        deadline = asyncio.get_running_loop().time() + 2.0
        observed = await self._read_body_text(editor)
        while observed != expected and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(.1)
            observed = await self._read_body_text(editor)
        if observed != expected:
            code = 'body_input_empty' if not observed.strip() else 'body_readback_mismatch'
            raise self._content_error(code, '知乎正文未完整写入或未通过回读核对；本次未点击发布')
        await asyncio.sleep(.3)
        if await self._read_body_text(editor) != expected:
            raise self._content_error('body_readback_mismatch', '知乎正文在输入后发生变化，未通过回读核对；本次未点击发布')
        zhihu_logger.info(f"知乎正文已通过回读核对，字符数={len(observed)}")

    async def _verify_content(self, page: Page) -> None:
        title = await self._title_field(page)
        editor = await self._body_editor(page)
        for observation in range(2):
            if self._normalized_editor_text(await title.input_value()) != self._normalized_editor_text(self.title):
                raise self._content_error('title_readback_mismatch', '知乎标题未通过最终回读核对；本次未点击发布')
            if await self._read_body_text(editor) != self._normalized_editor_text(self.body):
                raise self._content_error('body_readback_mismatch', '知乎正文未通过最终回读核对；本次未点击发布')
            if observation == 0:
                await asyncio.sleep(.3)

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
