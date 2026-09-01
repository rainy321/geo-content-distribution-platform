from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from urllib.parse import quote

from playwright.async_api import async_playwright

from conf import LOCAL_CHROME_PATH


ZHIHU_ME_URL = "https://www.zhihu.com/api/v4/me"
ZHIHU_MEMBER_ARTICLES_URL = (
    "https://www.zhihu.com/api/v4/members/{url_token}/articles"
    "?offset=0&limit=20&sort_by=created"
)


async def find_public_zhihu_article(
    title: str,
    account_file: str | Path,
    attempts: int = 1,
    *,
    interval_seconds: float = 2.0,
) -> dict[str, Any] | None:
    """Find an exact-title article owned by the logged-in Zhihu account.

    This is a read-only idempotency and reconciliation check. ``attempts`` is
    intentionally bounded because it polls platform visibility; it never
    repeats a publish click.
    """

    normalized_title = str(title or "").strip()
    cookie_path = Path(account_file)
    if not normalized_title:
        raise ValueError("知乎公开文章核验需要标题")
    if not cookie_path.is_file():
        raise ValueError("知乎账号 Cookie 文件不存在")
    if not 1 <= int(attempts) <= 5:
        raise ValueError("知乎公开文章核验次数必须在 1 到 5 之间")
    if interval_seconds < 0:
        raise ValueError("知乎公开文章核验间隔不能为负数")

    async with async_playwright() as playwright:
        launch_options: dict[str, Any] = {
            "headless": True,
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--lang=zh-CN",
                "--disable-infobars",
            ],
        }
        if LOCAL_CHROME_PATH:
            launch_options["executable_path"] = LOCAL_CHROME_PATH
        browser = await playwright.chromium.launch(**launch_options)
        browser_context = await browser.new_context(
            storage_state=str(cookie_path),
            extra_http_headers={
                "Accept": "application/json, text/plain, */*",
                "Referer": "https://www.zhihu.com/creator",
            },
        )
        request_context = browser_context.request
        try:
            me_response = await _safe_get(
                request_context,
                ZHIHU_ME_URL,
                stage="身份接口",
            )
            if not me_response.ok:
                raise RuntimeError(
                    f"知乎登录状态无效：身份接口 HTTP {me_response.status}"
                )
            me_data = await _safe_json(me_response, stage="身份接口")
            url_token = str(me_data.get("url_token") or "").strip()
            if not url_token:
                raise RuntimeError("知乎登录状态无效：身份接口缺少账号标识")

            articles_url = ZHIHU_MEMBER_ARTICLES_URL.format(
                url_token=quote(url_token, safe="")
            )
            for attempt_index in range(int(attempts)):
                response = await _safe_get(
                    request_context,
                    articles_url,
                    stage="公开文章接口",
                )
                if not response.ok:
                    raise RuntimeError(
                        f"知乎公开文章查询失败：HTTP {response.status}"
                    )
                payload = await _safe_json(response, stage="公开文章接口")
                matches = [
                    article
                    for article in (payload.get("data") or [])
                    if str(article.get("title") or "").strip() == normalized_title
                ]
                if matches:
                    return _normalize_article(matches[0])
                if attempt_index + 1 < int(attempts):
                    await asyncio.sleep(interval_seconds)
        finally:
            await browser_context.close()
            await browser.close()
    return None


async def _safe_get(request_context: Any, url: str, *, stage: str) -> Any:
    try:
        return await request_context.get(url, timeout=60_000)
    except Exception as exc:
        raise RuntimeError(
            f"知乎{stage}网络异常：{exc.__class__.__name__}"
        ) from None


async def _safe_json(response: Any, *, stage: str) -> dict[str, Any]:
    try:
        payload = await response.json()
    except Exception as exc:
        raise RuntimeError(
            f"知乎{stage}响应解析失败：{exc.__class__.__name__}"
        ) from None
    if not isinstance(payload, dict):
        raise RuntimeError(f"知乎{stage}响应格式异常")
    return payload


def _normalize_article(article: dict[str, Any]) -> dict[str, Any]:
    article_id = str(article.get("id") or "").strip()
    article_url = str(article.get("url") or "").strip()
    if not article_url and article_id:
        article_url = f"https://zhuanlan.zhihu.com/p/{article_id}"
    elif article_url.startswith("http://"):
        article_url = "https://" + article_url.removeprefix("http://")
    return {
        "id": article_id,
        "title": str(article.get("title") or "").strip(),
        "url": article_url,
        "created": article.get("created"),
        "updated": article.get("updated"),
    }
