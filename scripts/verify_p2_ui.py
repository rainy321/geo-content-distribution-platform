from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from playwright.sync_api import sync_playwright


def verify(
    base_url: str,
    screenshot_path: str | Path,
    *,
    access_password: str = "",
) -> dict:
    errors: list[str] = []
    checks: dict[str, bool] = {}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        page = context.new_page()
        page.on(
            "console",
            lambda message: errors.append(message.text)
            if message.type == "error"
            else None,
        )
        page.on("pageerror", lambda error: errors.append(str(error)))

        if access_password:
            response = context.request.post(
                f"{base_url}/api/auth/login",
                data={"password": access_password},
            )
            payload = response.json() if response.ok else {}
            if not response.ok or not (payload.get("data") or {}).get(
                "authenticated"
            ):
                raise RuntimeError(
                    f"运营访问门登录失败：HTTP {response.status}"
                )

        page.goto(f"{base_url}/#/content-create", wait_until="domcontentloaded")
        page.wait_for_timeout(1200)
        checks["content_creation_loaded"] = page.get_by_text("AI 内容创作").count() >= 1
        checks["batch_entry_visible"] = page.get_by_text("批量生成草稿").count() == 1
        page.get_by_text("批量生成草稿").click()
        checks["batch_dialog_visible"] = page.get_by_text("BATCH / 最多 5 篇").count() == 1
        page.keyboard.press("Escape")
        page.get_by_label("管理内容模板").click()
        checks["template_registry_visible"] = page.get_by_text("行业问题拆解").count() >= 1
        page.keyboard.press("Escape")

        page.goto(f"{base_url}/#/content-library", wait_until="domcontentloaded")
        page.wait_for_timeout(800)
        page.get_by_text("Excel 导入").click()
        checks["excel_import_visible"] = page.get_by_text("IMPORT / XLSX OR CSV").count() == 1
        page.keyboard.press("Escape")

        page.goto(f"{base_url}/#/publish-center", wait_until="domcontentloaded")
        page.wait_for_timeout(1200)
        checks["ten_platforms_rendered"] = page.locator(".platform-option").count() == 10
        checks["tiktok_visible"] = page.get_by_text("TikTok", exact=True).count() >= 1
        page.locator(".platform-option", has_text="Bilibili").click()
        checks["video_asset_required"] = page.get_by_text("共享视频资产").count() == 1
        page.screenshot(path=str(screenshot_path), full_page=True)

        page.goto(f"{base_url}/#/account-management", wait_until="domcontentloaded")
        page.wait_for_timeout(1000)
        checks["p2_accounts_visible"] = page.get_by_text("扩展图文与视频渠道").count() == 1
        checks["tiktok_account_visible"] = page.get_by_text("TikTok", exact=True).count() >= 1
        checks["no_error_overlay"] = page.locator(
            ".vite-error-overlay, #webpack-dev-server-client-overlay"
        ).count() == 0
        checks["page_not_blank"] = bool(page.locator("body").inner_text().strip())
        context.close()
        browser.close()

    return {"ok": all(checks.values()) and not errors, "checks": checks, "errors": errors}


def _parse_args():
    parser = argparse.ArgumentParser(
        description="验证 GEO Web 的模板、批量导入和 P2 分发界面。"
    )
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:5409",
        help="已经启动的 GEO Web 地址",
    )
    parser.add_argument(
        "--screenshot",
        type=Path,
        default=Path(__file__).resolve().parent.parent / ".tmp" / "p2-ui.png",
        help="发布中心验收截图保存位置",
    )
    parser.add_argument(
        "--access-password-env",
        default="APP_ACCESS_PASSWORD",
        help="从哪个环境变量读取运营访问口令；未设置时按无访问门环境验收",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    target = args.base_url
    screenshot = args.screenshot
    screenshot.parent.mkdir(parents=True, exist_ok=True)
    access_password = str(os.getenv(args.access_password_env, "")).strip()
    result = verify(
        target.rstrip("/"),
        screenshot,
        access_password=access_password,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["ok"] else 1)
