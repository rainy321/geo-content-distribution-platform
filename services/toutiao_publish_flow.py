from __future__ import annotations

import re
from typing import Any


_PUBLIC_ARTICLE_URL_PATTERN = re.compile(
    r"https?://(?:www\.)?toutiao\.com/(?:article|item)/\d+/?"
)
_SUBMISSION_METHODS = frozenset({"POST", "PUT", "PATCH"})
_SUBMISSION_URL_MARKERS = ("publish", "graphic", "article")


async def click_exact_publish_and_observe(page: Any) -> dict[str, Any]:
    """Click Toutiao's exact publish action and report observable evidence only.

    A successful click is not equivalent to a public article. The helper only
    returns ``success`` when a public Toutiao article URL is observable. A
    successful platform submission without that URL remains ``processing`` so
    the caller cannot accidentally retry and create a duplicate article.
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
                f"今日头条精确发布按钮数量异常：期望 1 个，实际 {len(publish_buttons)} 个"
            )

        await publish_buttons[0].click(timeout=5_000)
        try:
            await page.wait_for_timeout(1_500)
            await _confirm_publish_dialog_if_present(page)
            await page.wait_for_timeout(6_000)
        except Exception as exc:
            if not _is_navigation_context_error(exc):
                raise
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
    direct_url = _extract_public_article_url(current_url)
    if direct_url:
        return {
            "status": "success",
            "success": True,
            "url": direct_url,
            "message": "今日头条已跳转到公开文章页面",
        }

    evidence = await _summarize_submission_responses(submission_responses)
    if evidence["public_url"]:
        return {
            "status": "success",
            "success": True,
            "url": evidence["public_url"],
            "message": "今日头条发布接口已返回公开文章链接",
        }
    if evidence["accepted"]:
        return {
            "status": "processing",
            "success": False,
            "message": "今日头条已接受文章提交，正在等待平台审核或公开链接",
        }
    if evidence["request_count"]:
        return {
            "status": "processing",
            "success": False,
            "message": "已观察到今日头条文章写入请求，但未取得明确发布结果，请到平台后台核对",
        }
    raise RuntimeError("点击今日头条发布后未观察到文章提交请求")


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
    raise RuntimeError("今日头条发布确认弹窗已出现，但未找到可用的确认按钮")


async def _summarize_submission_responses(
    responses: list[Any],
) -> dict[str, Any]:
    public_url = ""
    accepted = False
    for response in responses:
        status = int(response.status)
        if not 200 <= status < 300:
            continue
        try:
            payload = await response.json()
        except Exception:
            payload = None
        payload_text = str(payload or "")
        public_url = public_url or _extract_public_article_url(payload_text)
        if "publish" in str(response.url or "").lower():
            accepted = True
        if isinstance(payload, dict):
            state = str(
                payload.get("status")
                or payload.get("state")
                or payload.get("publish_status")
                or ""
            ).lower()
            code = payload.get("code")
            if state in {"published", "success", "submitted", "auditing"}:
                accepted = True
            if code in {0, "0"}:
                accepted = True
    return {
        "request_count": len(responses),
        "accepted": accepted,
        "public_url": public_url,
    }


def _extract_public_article_url(value: str) -> str:
    match = _PUBLIC_ARTICLE_URL_PATTERN.search(value)
    return match.group(0) if match else ""


def _is_navigation_context_error(exc: Exception) -> bool:
    detail = str(exc).lower()
    return "execution context was destroyed" in detail and "navigation" in detail
