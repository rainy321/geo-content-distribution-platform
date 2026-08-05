# Zhihu Article (type=9) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Zhihu article-only publishing (`type=9`) via Playwright, wired through Web UI + examples, matching the Sohu article pattern.

**Architecture:** Cookie login SSE → `cookiesFile/*.json` + `user_info.type=9` → `POST /postVideo` with forced `contentType=article` → `post_article_zhihu` → `ZhiHuArticle` fills `zhuanlan.zhihu.com/write` (title/body/optional cover) and publishes unless `dry_run`.

**Tech Stack:** Python 3, Playwright async, Flask, Vue 3 + Element Plus, SQLite `user_info`

**Spec:** `docs/superpowers/specs/2026-08-03-zhihu-article-design.md`

---

## File map

| File | Responsibility |
|------|----------------|
| `uploader/zhihu_uploader/main.py` | `cookie_auth`, `zhihu_cookie_gen`, `zhihu_setup`, `ZhiHuArticle` |
| `uploader/zhihu_uploader/__init__.py` | Public exports |
| `utils/log.py` | `zhihu_logger` |
| `myUtils/auth.py` | `cookie_auth_zhihu` + `check_cookie` case 9 |
| `myUtils/login.py` | Web SSE `zhihu_cookie_gen(id, status_queue)` |
| `myUtils/postVideo.py` | `post_article_zhihu` |
| `sau_backend.py` | type=9 dispatch in `/postVideo`, `/postVideoBatch`, login |
| `sau_frontend/src/stores/account.js` | `9: '知乎'` |
| `sau_frontend/src/views/AccountManagement.vue` | 知乎 tab + login maps |
| `sau_frontend/src/views/PublishCenter.vue` | platform 9 article-only UI |
| `examples/get_zhihu_cookie.py` | Local cookie setup |
| `examples/upload_article_to_zhihu.py` | Local dry_run publish |
| `tests/test_zhihu_article.py` | Construction / validation unit tests |

---

### Task 1: Logger + ZhiHuArticle unit-tested skeleton

**Files:**
- Modify: `utils/log.py`
- Create: `uploader/zhihu_uploader/__init__.py`
- Create: `uploader/zhihu_uploader/main.py`
- Create: `tests/test_zhihu_article.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_zhihu_article.py
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from uploader.zhihu_uploader.main import ZhiHuArticle


class TestZhiHuArticle(unittest.TestCase):
    def test_rejects_empty_title(self):
        with TemporaryDirectory() as td:
            account = Path(td) / "a.json"
            account.write_text("{}", encoding="utf-8")
            with self.assertRaises(ValueError):
                ZhiHuArticle(title="  ", body="正文", tags=[], publish_date=0, account_file=account)

    def test_rejects_empty_body(self):
        with TemporaryDirectory() as td:
            account = Path(td) / "a.json"
            account.write_text("{}", encoding="utf-8")
            with self.assertRaises(ValueError):
                ZhiHuArticle(title="有效标题", body="  ", tags=[], publish_date=0, account_file=account)

    def test_accepts_title_body_and_optional_cover(self):
        with TemporaryDirectory() as td:
            account = Path(td) / "a.json"
            account.write_text("{}", encoding="utf-8")
            cover = Path(td) / "c.jpg"
            cover.write_bytes(b"fake")
            app = ZhiHuArticle(
                title="知乎测试标题",
                body="第一段\n\n第二段",
                tags=["测试"],
                publish_date=0,
                account_file=account,
                dry_run=True,
                cover_path=str(cover),
            )
            self.assertEqual(app.title, "知乎测试标题")
            self.assertTrue(app.dry_run)
            self.assertEqual(app.cover_path, str(cover.resolve()) if hasattr(Path(cover), "resolve") else str(cover))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_zhihu_article -v`

Expected: FAIL with `ModuleNotFoundError` or `ImportError` for `uploader.zhihu_uploader`

- [ ] **Step 3: Add logger**

In `utils/log.py`, after `sohu_logger` line:

```python
zhihu_logger = create_logger('zhihu', 'logs/zhihu.log')
```

- [ ] **Step 4: Implement skeleton uploader**

Create `uploader/zhihu_uploader/__init__.py`:

```python
# -*- coding: utf-8 -*-
from uploader.zhihu_uploader.main import ZhiHuArticle, cookie_auth, zhihu_cookie_gen, zhihu_setup

__all__ = ["ZhiHuArticle", "cookie_auth", "zhihu_cookie_gen", "zhihu_setup"]
```

Create `uploader/zhihu_uploader/main.py` with at least:

```python
# -*- coding: utf-8 -*-
"""知乎文章发布（Playwright）。第一期仅文章。"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime
from pathlib import Path

from playwright.async_api import Page, async_playwright

from conf import LOCAL_CHROME_HEADLESS, LOCAL_CHROME_PATH
from utils.base_social_media import set_init_script
from utils.log import zhihu_logger

ZHIHU_LOGIN_URL = "https://www.zhihu.com/signin"
ZHIHU_WRITE_URL = "https://zhuanlan.zhihu.com/write"
ZHIHU_CREATOR_URL = "https://www.zhihu.com/creator"
TITLE_MAX_LEN = 100
COVER_MAX_BYTES = 10 * 1024 * 1024
COVER_EXTS = {".jpg", ".jpeg", ".png"}


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
    u = (url or "").lower()
    return any(k in u for k in ("/signin", "/login", "passport", "account/login"))


async def cookie_auth(account_file) -> bool:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(**_build_launch_kwargs(headless=LOCAL_CHROME_HEADLESS))
        try:
            context = await browser.new_context(storage_state=account_file)
            context = await set_init_script(context)
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
        except Exception as exc:
            zhihu_logger.warning(f"[+] cookie 校验异常，按失效处理: {exc}")
            return False
        finally:
            await browser.close()


async def zhihu_cookie_gen(account_file):
    """CLI/本地调试：打开登录页，手动登录后保存 cookie。"""
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(**_build_launch_kwargs(headless=False))
        context = await browser.new_context()
        context = await set_init_script(context)
        page = await context.new_page()
        await page.goto(ZHIHU_LOGIN_URL, timeout=60000)
        zhihu_logger.info("请在浏览器中完成知乎登录，登录成功后回到终端继续…")
        await page.pause()
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
            if self.dry_run:
                zhihu_logger.info("dry_run=True，跳过点击发布")
                await page.wait_for_timeout(2000)
                return
            await self.click_publish(page)
            zhihu_logger.success("知乎文章发布流程已完成点击发布")
        finally:
            await context.close()
            await browser.close()

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
```

Note: If `cover_path` assertion in the test is brittle, assert `app.cover_path.endswith("c.jpg")` instead of full resolve equality.

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m unittest tests.test_zhihu_article -v`

Expected: PASS (3 tests)

- [ ] **Step 6: Commit** (only if user asked to commit)

```bash
git add utils/log.py uploader/zhihu_uploader tests/test_zhihu_article.py
git commit -m "feat(zhihu): add article uploader skeleton and unit tests"
```

---

### Task 2: Wire auth / login / postVideo / backend

**Files:**
- Modify: `myUtils/auth.py`
- Modify: `myUtils/login.py`
- Modify: `myUtils/postVideo.py`
- Modify: `sau_backend.py`

- [ ] **Step 1: auth.py — add Zhihu cookie check**

After `cookie_auth_sohu`:

```python
async def cookie_auth_zhihu(account_file):
    from uploader.zhihu_uploader.main import cookie_auth as zhihu_cookie_auth
    return await zhihu_cookie_auth(account_file)
```

In `check_cookie` match, before `case _:`:

```python
        # 知乎
        case 9:
            return await cookie_auth_zhihu(Path(BASE_DIR / "cookiesFile" / file_path))
```

- [ ] **Step 2: login.py — Web SSE login**

Copy `sohu_cookie_gen` pattern as `zhihu_cookie_gen(id, status_queue)`:
- `status_queue.put("MANUAL_LOGIN")`
- headed Chromium via existing `get_browser_options()`
- `set_init_script` on context (unlike Sohu)
- `page.goto("https://www.zhihu.com/signin")`
- `wait_for_url` until URL contains `zhihu.com` and not signin/login (timeout ~200s)
- save `cookiesFile/{uuid}.json`
- `check_cookie(9, ...)`
- `INSERT INTO user_info (type, filePath, userName, status)` with `type=9`
- `status_queue.put("200")` / `"500"` on failure
- close page/context/browser in `finally`

- [ ] **Step 3: postVideo.py — dispatcher**

Import:

```python
from uploader.zhihu_uploader.main import ZhiHuArticle
```

Add:

```python
def post_article_zhihu(
    title,
    body,
    tags,
    account_file,
    enableTimer=False,
    videos_per_day=1,
    daily_times=None,
    start_days=0,
    dry_run=False,
    cover_path="",
):
    """知乎文章发布（type=9, contentType=article）。封面可选，jpg/jpeg/png，≤10MB。"""
    from utils.files_times import generate_schedule_time_next_day

    account_paths = [Path(BASE_DIR / "cookiesFile" / file) for file in account_file]

    resolved_cover = ""
    if cover_path and str(cover_path).strip():
        raw = str(cover_path).strip()
        name_only = Path(raw.replace("\\", "/")).name
        candidates = [
            Path(raw),
            Path(BASE_DIR / "videoFile" / raw),
            Path(BASE_DIR / "videoFile" / name_only),
            Path(BASE_DIR / raw),
        ]
        for cand in candidates:
            try:
                if cand.is_file():
                    if cand.suffix.lower() not in {".jpg", ".jpeg", ".png"}:
                        print(f"⚠️ 知乎封面格式不支持，将跳过: {cand}", flush=True)
                        break
                    if cand.stat().st_size > 10 * 1024 * 1024:
                        print(f"⚠️ 知乎封面超过 10MB，将跳过: {cand}", flush=True)
                        break
                    resolved_cover = str(cand.resolve())
                    break
            except Exception:
                continue
        if cover_path and not resolved_cover:
            print(f"⚠️ 知乎封面路径未找到，将跳过: {cover_path}", flush=True)

    if enableTimer:
        publish_datetimes = generate_schedule_time_next_day(1, videos_per_day, daily_times, start_days)
        publish_date = publish_datetimes[0]
    else:
        publish_date = 0

    for cookie in account_paths:
        print(f"🟢 知乎文章发布开始: 账号={cookie}, dryRun={dry_run}", flush=True)
        app = ZhiHuArticle(
            title=title,
            body=body,
            tags=tags,
            publish_date=publish_date,
            account_file=cookie,
            dry_run=dry_run,
            cover_path=resolved_cover or None,
        )
        if dry_run:
            app.headless = False
        asyncio.run(app.main(), debug=False)
```

Ensure `asyncio` / `Path` / `BASE_DIR` imports already exist in this file (they do for other platforms).

- [ ] **Step 4: sau_backend.py — imports and type=9**

Update imports to include `zhihu_cookie_gen` and `post_article_zhihu`.

In `/postVideo` (and mirror batch where applicable):

```python
ARTICLE_CAPABLE_PLATFORMS = (5, 7, 8, 9)
if type == 8:
    content_type = 'article'
if type == 9:
    content_type = 'article'
is_zhihu_article = (type == 9)
```

In validation: article title/body required already covers type 9 via `is_article`.

In `match type` add:

```python
                case 9:
                    post_article_zhihu(
                        title,
                        article_body,
                        tags,
                        account_list,
                        enableTimer,
                        videos_per_day,
                        daily_times,
                        start_days,
                        dry_run,
                        thumbnail_path,
                    )
```

Submit messages:

```python
    elif is_zhihu_article and dry_run:
        submit_msg = "知乎文章任务已提交：仅预览不发布"
    elif is_zhihu_article:
        submit_msg = "知乎文章发布任务已提交，正在后台执行"
```

In `run_async_function` login match:

```python
            case '9':
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(zhihu_cookie_gen(id, status_queue))
```

Also update `/postVideoBatch` the same way as Sohu (`if type == 9: content_type = 'article'` + `case 9: post_article_zhihu(...)`).

- [ ] **Step 5: Smoke import check**

Run:

```bash
python -c "from uploader.zhihu_uploader import ZhiHuArticle, zhihu_setup; from myUtils.postVideo import post_article_zhihu; print('ok')"
```

Expected: `ok`

- [ ] **Step 6: Commit** (only if user asked)

```bash
git add myUtils/auth.py myUtils/login.py myUtils/postVideo.py sau_backend.py
git commit -m "feat(zhihu): wire auth, login SSE, and postVideo type=9"
```

---

### Task 3: Frontend account + publish UI

**Files:**
- Modify: `sau_frontend/src/stores/account.js`
- Modify: `sau_frontend/src/views/AccountManagement.vue`
- Modify: `sau_frontend/src/views/PublishCenter.vue`

- [ ] **Step 1: account.js**

```javascript
    8: '搜狐',
    9: '知乎'
```

- [ ] **Step 2: AccountManagement.vue**

Mirror the Sohu tab pane as a 「知乎」 tab (`name="zhihu"`), empty state 「暂无知乎账号数据」, filter `platform === '知乎'`.

Add option `<el-option label="知乎" value="知乎" />`.

Maps:
- platform string → type: `'知乎': 9` / `'知乎': '9'`
- tag type color map entry for 知乎
- login hint: `已打开浏览器，请在知乎登录页完成登录，登录完成后系统将自动继续`

- [ ] **Step 3: PublishCenter.vue**

```javascript
  { key: 9, name: '知乎' }
```

```javascript
const articleCapablePlatforms = [5, 7, 8, 9]
const articleOnlyPlatforms = [8, 9]
```

Platform name map: `9: '知乎'`.

UI hints (alongside existing Sohu conditionals; prefer `articleOnlyPlatforms.includes(...)` where already used):
- Content-type hint when platform is 9: `知乎第一期仅支持文章（zhuanlan.zhihu.com/write）`
- Hide video radio for article-only (already via `!== 8` — change to `!articleOnlyPlatforms.includes(tab.selectedPlatform)`)
- Title hint for 知乎: 必填；maxlength keep 100 unless product says otherwise
- Cover: optional single image, jpg/jpeg/png, max 10MB — reuse single-cover uploader path used by Toutiao/Baijiahao (`coverMode === 'single'` + non-Sohu branch), OR extend Sohu-style single upload for both 8 and 9. Prefer: treat type 9 like Toutiao optional single cover (`thumbnailPath`), not Sohu `coverImages`.
- Cover size helper: `if (tab?.selectedPlatform === 9) return 10 * 1024 * 1024`
- Submit success messages for 知乎 dry_run / publish similar to Sohu
- Do **not** show Sohu `info_source` / `workStatement` radios for type 9

On platform switch into article-only: force `contentType = 'article'` (already handled by `articleOnlyPlatforms`).

- [ ] **Step 4: Frontend sanity**

Run from `sau_frontend` if convenient: `npm run build` or at least ensure no syntax errors in edited Vue files.

- [ ] **Step 5: Commit** (only if user asked)

```bash
git add sau_frontend/src/stores/account.js sau_frontend/src/views/AccountManagement.vue sau_frontend/src/views/PublishCenter.vue
git commit -m "feat(zhihu): add account and publish UI for type=9"
```

---

### Task 4: Example scripts + end-to-end verification

**Files:**
- Create: `examples/get_zhihu_cookie.py`
- Create: `examples/upload_article_to_zhihu.py`

- [ ] **Step 1: Cookie example**

```python
"""知乎 cookie 登录示例。

用法：
  python examples/get_zhihu_cookie.py

登录成功后 cookie 保存在 cookies/zhihu_uploader/account.json
"""

import asyncio
from pathlib import Path

from conf import BASE_DIR
from uploader.zhihu_uploader.main import zhihu_setup

if __name__ == "__main__":
    account_file = Path(BASE_DIR / "cookies" / "zhihu_uploader" / "account.json")
    account_file.parent.mkdir(parents=True, exist_ok=True)
    cookie_result = asyncio.run(zhihu_setup(str(account_file), handle=True))
    print(f"zhihu cookie setup: {cookie_result}, path={account_file}")
```

- [ ] **Step 2: Upload example**

```python
"""知乎文章发布示例（支持 dry_run）。

用法：
1. python examples/get_zhihu_cookie.py
2. 修改 title / body / account_file
3. python examples/upload_article_to_zhihu.py
"""

import asyncio
from pathlib import Path

from conf import BASE_DIR
from uploader.zhihu_uploader.main import ZhiHuArticle, zhihu_setup


if __name__ == "__main__":
    account_file = Path(BASE_DIR) / "cookies" / "zhihu_uploader" / "account.json"

    title = "测试知乎文章标题"
    body = (
        "这是一篇用于联调的测试正文。\n\n"
        "第二段：换行应能保留。\n"
        "第三段：确认 dry_run 时不会点击发布。"
    )
    dry_run = True

    cookie_ok = asyncio.run(zhihu_setup(account_file, handle=True))
    if not cookie_ok:
        raise SystemExit("知乎 cookie 未就绪")

    cover_path = None  # 可选：Path(BASE_DIR) / "videoFile" / "cover.jpg"

    app = ZhiHuArticle(
        title=title,
        body=body,
        tags=["测试"],
        publish_date=0,
        account_file=account_file,
        dry_run=dry_run,
        cover_path=cover_path,
    )
    if dry_run:
        app.headless = False
    asyncio.run(app.main(), debug=False)
```

- [ ] **Step 3: Automated checks**

```bash
python -m unittest tests.test_zhihu_article -v
python -c "from myUtils.login import zhihu_cookie_gen; from sau_backend import app; print('backend import ok')"
```

Expected: tests PASS; import ok (if `sau_backend` import pulls Flask app name — adjust to whatever the module exports; if import side-effects start server, instead only import the functions via grepping that symbols exist).

Safer check:

```bash
python -c "import ast; ast.parse(open('sau_backend.py',encoding='utf-8').read()); print('syntax ok')"
```

- [ ] **Step 4: Manual acceptance (from spec)**

1. 账号管理添加知乎并登录 → DB `type=9`
2. 发布中心选知乎仅图文，提交任务
3. `dry_run` 填表不点发布
4. 失效 cookie 日志提示重新登录

During manual dry_run, if selectors miss (title/editor/cover/publish), update candidate selectors in `uploader/zhihu_uploader/main.py` against live DOM; keep multi-candidate pattern.

- [ ] **Step 5: Commit** (only if user asked)

```bash
git add examples/get_zhihu_cookie.py examples/upload_article_to_zhihu.py
git commit -m "feat(zhihu): add cookie and article upload examples"
```

---

## Spec coverage checklist

| Spec item | Task |
|-----------|------|
| type=9 article-only | Task 2–3 |
| Playwright write page | Task 1 |
| title + body + optional cover + dry_run | Task 1–2 |
| Web + examples, no CLI/skill | Task 3–4 (CLI intentionally omitted) |
| auth/login/postVideo/backend/frontend | Task 2–3 |
| Acceptance criteria | Task 4 Step 4 |

## Out of scope (do not implement)

- 视频 / 想法 / 回答
- 话题标签、专栏、定时
- `sau zhihu` CLI / skills
- DB schema migration
