from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
from pathlib import Path

from playwright.async_api import async_playwright

from utils.base_social_media import set_init_script


TITLE = "GEO 内容生成与分发链路测试说明"
PLATFORMS = {
    "channels": {
        "type": 2,
        "url": "https://channels.weixin.qq.com/platform/post/list",
    },
    "douyin": {
        "type": 3,
        "url": "https://creator.douyin.com/creator-micro/content/manage",
    },
    "kuaishou": {
        "type": 4,
        "url": "https://cp.kuaishou.com/article/manage/video?status=2",
    },
    "bilibili": {
        "type": 6,
        "url": "https://member.bilibili.com/platform/upload-manager/article",
    },
    "tiktok": {
        "type": 10,
        "url": "https://www.tiktok.com/tiktokstudio/content",
    },
}


def _account_path(
    database_path: Path,
    cookies_directory: Path,
    platform: str,
) -> Path:
    with sqlite3.connect(database_path) as connection:
        row = connection.execute(
            """
            SELECT filePath
            FROM user_info
            WHERE type = ? AND status = 1
            ORDER BY id DESC
            LIMIT 1
            """,
            (PLATFORMS[platform]["type"],),
        ).fetchone()
    if not row:
        raise RuntimeError(f"{platform} 没有可用账号")
    path = (cookies_directory / str(row[0])).resolve()
    path.relative_to(cookies_directory)
    if not path.is_file():
        raise FileNotFoundError(path)
    return path


async def _verify(
    platform: str,
    account_path: Path,
    screenshot_directory: Path,
    *,
    headless: bool = True,
) -> dict[str, object]:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=headless)
        if platform == "bilibili":
            account_payload = json.loads(account_path.read_text(encoding="utf-8"))
            raw_cookies = account_payload.get("cookie_info", {}).get("cookies", [])
            context = await browser.new_context()
            await context.add_cookies(
                [
                    {
                        "name": str(item.get("name") or ""),
                        "value": str(item.get("value") or ""),
                        "domain": ".bilibili.com",
                        "path": "/",
                        "expires": float(item.get("expires") or -1),
                        "httpOnly": bool(item.get("http_only")),
                        "secure": bool(item.get("secure")),
                        "sameSite": "Lax",
                    }
                    for item in raw_cookies
                    if item.get("name") and item.get("value")
                ]
            )
        else:
            context = await browser.new_context(storage_state=account_path)
        context = await set_init_script(context)
        try:
            page = await context.new_page()
            await page.goto(
                PLATFORMS[platform]["url"],
                wait_until="domcontentloaded",
                timeout=60_000,
            )
            await page.wait_for_timeout(8000)
            content_ready = True
            if platform == "tiktok":
                try:
                    await page.wait_for_function(
                        """() => {
                            const text = (document.body?.innerText || '').trim();
                            return text.length >= 80
                                || text.includes('暂无内容')
                                || text.includes('No content')
                                || text.includes('Upload your first video');
                        }""",
                        timeout=45_000,
                    )
                except Exception:
                    content_ready = False
            current_url = str(page.url or "").lower()
            login_required = any(
                marker in current_url
                for marker in ("/login", "/passport", "accounts.google.com")
            )
            if login_required:
                content_ready = False
            tab_labels = {
                "douyin": ["全部", "已发布", "审核中", "未通过"],
                "kuaishou": ["全部作品", "已发布", "待发布", "未通过"],
                "bilibili": ["全部稿件", "进行中", "已通过", "未通过"],
                "channels": ["全部", "已发表", "审核中", "未通过", "草稿"],
                "tiktok": [],
            }[platform]
            checked_tabs = []
            title_found = False
            matching_links: list[str] = []
            title_contexts: list[dict[str, object]] = []
            found_tab = ""
            labels_to_check = tab_labels or [""]
            for label in labels_to_check:
                if label:
                    tab = page.get_by_text(label, exact=True).first
                    try:
                        if await tab.count() and await tab.is_visible():
                            await tab.click()
                            await page.wait_for_timeout(3500)
                    except Exception:
                        pass
                body_text = "\n".join(await page.locator("body").all_inner_texts())
                checked_tabs.append(label or "default")
                if TITLE not in body_text:
                    continue
                title_found = True
                found_tab = label or "default"
                matching_links = await page.locator("a").evaluate_all(
                    """(links, title) => links
                        .filter(link => {
                            const text = `${link.innerText || ''} ${link.title || ''}`;
                            return text.includes(title);
                        })
                        .map(link => link.href)
                        .filter(Boolean)
                    """,
                    TITLE,
                )
                title_contexts = await page.evaluate(
                    """title => {
                        const nodes = Array.from(document.querySelectorAll('body *'))
                            .filter(el => (el.innerText || '').includes(title))
                            .sort((a, b) => (a.innerText || '').length - (b.innerText || '').length)
                            .slice(0, 3);
                        return nodes.map(node => {
                            let root = node;
                            for (let i = 0; i < 5 && root.parentElement; i += 1) {
                                const links = Array.from(root.querySelectorAll('a[href]'));
                                if (links.length) break;
                                root = root.parentElement;
                            }
                            return {
                                tag: root.tagName,
                                id: root.id || '',
                                className: String(root.className || '').slice(0, 300),
                                data: Object.fromEntries(Object.entries(root.dataset || {}).slice(0, 20)),
                                links: Array.from(root.querySelectorAll('a[href]')).map(a => a.href),
                            };
                        });
                    }""",
                    TITLE,
                )
                break
            screenshot_directory.mkdir(parents=True, exist_ok=True)
            screenshot_path = screenshot_directory / f"{platform}_published_list.png"
            await page.screenshot(path=str(screenshot_path), full_page=True)
            rendered_body_text = "\n".join(
                await page.locator("body").all_inner_texts()
            ).strip()
            body_text_length = len(rendered_body_text)
            return {
                "platform": platform,
                "title_found": title_found,
                "content_ready": content_ready,
                "verification_state": (
                    "login_required"
                    if login_required
                    else "found"
                    if title_found
                    else "not_found"
                    if content_ready
                    else "inconclusive"
                ),
                "page_url": page.url,
                "login_required": login_required,
                "page_title": await page.title(),
                "body_text_length": body_text_length,
                "matching_links": list(dict.fromkeys(matching_links)),
                "found_tab": found_tab,
                "checked_tabs": checked_tabs,
                "title_contexts": title_contexts,
                "screenshot": str(screenshot_path),
            }
        finally:
            await context.close()
            await browser.close()


async def main_async(args: argparse.Namespace) -> None:
    database_path = args.database.expanduser().resolve()
    cookies_directory = args.cookies.expanduser().resolve()
    screenshot_directory = args.screenshots.expanduser().resolve()
    selected = args.platform or ["douyin", "kuaishou", "channels"]
    for platform in selected:
        account_path = _account_path(
            database_path,
            cookies_directory,
            platform,
        )
        try:
            result = await _verify(
                platform,
                account_path,
                screenshot_directory,
                headless=not args.headful,
            )
        except Exception as exc:
            result = {
                "platform": platform,
                "title_found": False,
                "error": f"{exc.__class__.__name__}: {exc}",
            }
        print("VERIFY_RESULT " + json.dumps(result, ensure_ascii=False), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="只读核验 P2 平台内容管理页。")
    parser.add_argument(
        "--platform",
        action="append",
        choices=list(PLATFORMS),
    )
    parser.add_argument("--database", type=Path, default=Path("db/database.db"))
    parser.add_argument("--cookies", type=Path, default=Path("cookiesFile"))
    parser.add_argument(
        "--screenshots",
        type=Path,
        default=Path("cookiesFile/p2-verification"),
    )
    parser.add_argument(
        "--headful",
        action="store_true",
        help="使用可见浏览器核验，适合无头模式无法加载的创作者中心。",
    )
    asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    main()
