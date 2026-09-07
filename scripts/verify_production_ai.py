from __future__ import annotations

import argparse
import asyncio
import json
import time
from typing import Any

from playwright.async_api import Page, async_playwright


DEFAULT_BASE_URL = "https://geo-content-distribution-platform.vercel.app"


async def _fetch_json(
    page: Page,
    path: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return await page.evaluate(
        """async ({path, method, payload}) => {
            const response = await fetch(path, {
                method,
                credentials: 'include',
                headers: payload ? {'Content-Type': 'application/json'} : {},
                body: payload ? JSON.stringify(payload) : undefined,
            });
            let body = null;
            try {
                body = await response.json();
            } catch (_error) {
                body = {code: response.status, msg: '响应不是 JSON', data: null};
            }
            return {status: response.status, body};
        }""",
        {"path": path, "method": method, "payload": payload},
    )


async def _wait_for_operator_login(
    page: Page,
    *,
    timeout_seconds: float,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error_type = ""
    while time.monotonic() < deadline:
        try:
            result = await _fetch_json(page, "/backend/api/auth/status")
            data = (result.get("body") or {}).get("data") or {}
            if data.get("authenticated") is True:
                return
        except Exception as exc:
            # Submitting the access gate can replace the document while the
            # poll is in flight. Treat that navigation race as transient.
            last_error_type = type(exc).__name__
        await page.wait_for_timeout(1000)
    suffix = f"（最后错误：{last_error_type}）" if last_error_type else ""
    raise TimeoutError(
        f"等待运营口令登录超过 {timeout_seconds:g} 秒{suffix}"
    )


async def verify(base_url: str, *, login_timeout: float) -> dict[str, Any]:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()
        try:
            await page.goto(
                base_url,
                wait_until="domcontentloaded",
                timeout=60_000,
            )
            print(
                "AI_VERIFY_WAITING_FOR_OPERATOR_LOGIN "
                + json.dumps({"base_url": base_url}, ensure_ascii=True),
                flush=True,
            )
            await _wait_for_operator_login(
                page,
                timeout_seconds=login_timeout,
            )

            config = await _fetch_json(page, "/backend/api/ai/config-status")
            config_data = (config.get("body") or {}).get("data") or {}
            if config.get("status") != 200 or not config_data.get(
                "server_configured"
            ):
                raise RuntimeError("生产后端未检测到完整 AI 配置")

            projects = await _fetch_json(page, "/backend/api/projects")
            project_items = (projects.get("body") or {}).get("data") or []
            if projects.get("status") != 200 or not project_items:
                raise RuntimeError("生产后端没有可用于验收的项目")
            project_id = int(project_items[0]["id"])

            started_at = time.perf_counter()
            generated = await _fetch_json(
                page,
                "/backend/api/articles/generate",
                method="POST",
                payload={
                    "project_id": project_id,
                    "topic": "企业如何建立可审计的 GEO 内容生成与自动分发工作流",
                    "keywords": ["GEO", "内容生成", "自动分发"],
                    "length": 600,
                    "content_type": "行业科普",
                    "target_platform": "通用内容媒体平台",
                },
            )
            elapsed_ms = round((time.perf_counter() - started_at) * 1000)
            body = generated.get("body") or {}
            if generated.get("status") != 200 or body.get("code") != 200:
                message = str(body.get("msg") or "生产 AI 请求失败")[:300]
                raise RuntimeError(
                    f"生产 AI 请求返回 HTTP {generated.get('status')}: {message}"
                )

            article = body.get("data") or {}
            return {
                "ok": True,
                "elapsed_ms": elapsed_ms,
                "title_length": len(str(article.get("title") or "")),
                "summary_length": len(str(article.get("summary") or "")),
                "content_length": len(str(article.get("content") or "")),
                "tags_count": len(article.get("tags") or []),
                "faq_count": len(article.get("faq") or []),
                "saved": False,
                "published": False,
            }
        finally:
            await context.close()
            await browser.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="在人工通过运营访问门后执行一次不保存、不发布的生产 AI 验收。"
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--login-timeout", type=float, default=600.0)
    args = parser.parse_args()

    normalized_base_url = str(args.base_url).strip().rstrip("/")
    if not normalized_base_url.startswith("https://"):
        parser.error("base-url 必须使用 HTTPS")
    if args.login_timeout <= 0:
        parser.error("login-timeout 必须大于 0")

    try:
        result = asyncio.run(
            verify(normalized_base_url, login_timeout=args.login_timeout)
        )
    except Exception as exc:
        print(
            "AI_VERIFY_FAILED "
            + json.dumps(
                {"error_type": type(exc).__name__, "message": str(exc)[:300]},
                ensure_ascii=True,
            ),
            flush=True,
        )
        return 1

    print("AI_VERIFY_RESULT " + json.dumps(result, ensure_ascii=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
