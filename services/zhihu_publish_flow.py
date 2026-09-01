from __future__ import annotations

import re
from typing import Any


_ARTICLE_URL_PATTERN = re.compile(r"https://zhuanlan\.zhihu\.com/p/\d+")
_SUBMISSION_METHODS = frozenset({"POST", "PUT", "PATCH"})
_SUBMISSION_URL_MARKERS = ("article", "publish")


async def click_exact_publish_and_observe(page: Any) -> dict[str, Any]:
    """Click Zhihu's exact publish action and report only observable outcomes.

    The current editor renders both ``发布设置`` and ``发布``. A fuzzy text
    lookup can select the settings control, so this helper requires one exact,
    visible, enabled ``发布`` button. It never reports success unless the page
    exposes a published article URL or an explicit successful publish signal.
    """

    submission_responses: list[Any] = []

    def record_response(response: Any) -> None:
        method = str(response.request.method or "").upper()
        url = str(response.url or "").lower()
        if method in _SUBMISSION_METHODS and any(
            marker in url for marker in _SUBMISSION_URL_MARKERS
        ):
            submission_responses.append(response)

    page.on("response", record_response)
    try:
        candidates = page.get_by_role("button", name="发布", exact=True)
        publish_buttons = []
        for index in range(await candidates.count()):
            candidate = candidates.nth(index)
            if await candidate.is_visible() and await candidate.is_enabled():
                publish_buttons.append(candidate)
        if len(publish_buttons) != 1:
            raise RuntimeError(
                f"知乎精确发布按钮数量异常：期望 1 个，实际 {len(publish_buttons)} 个"
            )

        await publish_buttons[0].click(timeout=5_000)
        try:
            await page.wait_for_timeout(1_500)
            await _confirm_publish_dialog_if_present(page)
            await page.wait_for_timeout(6_000)
        except Exception as exc:
            if not _is_navigation_context_error(exc):
                raise
            # A successful publish can navigate away while the old editor
            # context is being inspected. Let the new page settle and evaluate
            # its URL instead of converting that navigation into a failure.
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=10_000)
            except Exception:
                pass
    finally:
        try:
            page.remove_listener("response", record_response)
        except Exception:
            pass

    current_url = str(page.url or "")
    direct_url = _extract_article_url(current_url)
    if direct_url:
        return {
            "status": "success",
            "success": True,
            "url": direct_url,
            "message": "知乎已跳转到公开文章页面",
        }

    response_evidence = await _summarize_submission_responses(submission_responses)
    if response_evidence["article_url"]:
        return {
            "status": "success",
            "success": True,
            "url": response_evidence["article_url"],
            "message": "知乎发布接口已返回文章链接",
        }
    if response_evidence["explicit_publish_success"]:
        return {
            "status": "processing",
            "success": False,
            "message": "知乎发布接口已接受提交，但未返回公开文章链接，需平台侧核验",
        }
    if response_evidence["request_count"]:
        return {
            "status": "processing",
            "success": False,
            "message": "已观察到知乎文章写入请求，但没有明确发布成功信号",
        }
    raise RuntimeError("点击知乎发布后未观察到文章提交请求")


async def _confirm_publish_dialog_if_present(page: Any) -> None:
    dialogs = page.locator('[role="dialog"]:visible')
    if not await dialogs.count():
        return
    dialog = dialogs.last
    for label in ("确认发布", "发布", "确定"):
        candidates = dialog.get_by_role("button", name=label, exact=True)
        for index in range(await candidates.count()):
            candidate = candidates.nth(index)
            if await candidate.is_visible() and await candidate.is_enabled():
                await candidate.click(timeout=5_000)
                await page.wait_for_timeout(1_000)
                return
    raise RuntimeError("知乎发布确认弹窗已出现，但未找到可用的确认按钮")


async def _summarize_submission_responses(responses: list[Any]) -> dict[str, Any]:
    article_url = ""
    explicit_publish_success = False
    successful_count = 0
    for response in responses:
        status = int(response.status)
        if not 200 <= status < 300:
            continue
        successful_count += 1
        response_url = str(response.url or "")
        if "publish" in response_url.lower():
            explicit_publish_success = True
        try:
            payload = await response.json()
        except Exception:
            payload = None
        payload_text = str(payload or "")
        article_url = article_url or _extract_article_url(payload_text)
        if isinstance(payload, dict):
            state = str(
                payload.get("status")
                or payload.get("state")
                or payload.get("publish_status")
                or ""
            ).lower()
            if state in {"published", "success", "submitted"}:
                explicit_publish_success = True
    return {
        "request_count": len(responses),
        "successful_request_count": successful_count,
        "explicit_publish_success": explicit_publish_success,
        "article_url": article_url,
    }


def _extract_article_url(value: str) -> str:
    match = _ARTICLE_URL_PATTERN.search(value)
    return match.group(0) if match else ""


def _is_navigation_context_error(exc: Exception) -> bool:
    detail = str(exc).lower()
    return "execution context was destroyed" in detail and "navigation" in detail
