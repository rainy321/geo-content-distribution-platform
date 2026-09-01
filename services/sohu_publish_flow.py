from __future__ import annotations

import re
from typing import Any


_PUBLIC_ARTICLE_URL_PATTERN = re.compile(
    r"https?://(?:www\.)?sohu\.com/a/\d+_\d+/?"
)
_SUBMISSION_METHODS = frozenset({"POST", "PUT", "PATCH"})
_SUBMISSION_URL_MARKERS = ("publish", "article", "news", "content")
_PUBLISH_LABELS = ("发布", "立即发布")
_CONFIRM_LABELS = ("确认发布", "确认并发布", "立即发布", "发布")
_ACCEPTED_STATES = frozenset(
    {"accepted", "auditing", "published", "publishing", "submitted", "success"}
)
_ACCEPTED_MESSAGES = (
    "提交成功",
    "发布成功",
    "已提交",
    "提交审核",
    "审核中",
    "accepted",
    "auditing",
    "published",
    "submitted",
)


async def click_exact_publish_and_observe(page: Any) -> dict[str, Any]:
    """Click Sohu's exact publish action and return observable evidence only."""

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
        _label, publish_button = await _require_single_exact_button(
            page,
            _PUBLISH_LABELS,
            stage="发布",
        )
        await publish_button.click(timeout=5_000)
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

    public_url = _extract_public_article_url(str(page.url or ""))
    if public_url:
        return {
            "status": "success",
            "success": True,
            "url": public_url,
            "message": "搜狐号已跳转到公开文章页面",
        }

    evidence = await _summarize_submission_responses(submission_responses)
    if evidence["public_url"]:
        return {
            "status": "success",
            "success": True,
            "url": evidence["public_url"],
            "message": "搜狐号发布接口已返回公开文章链接",
        }
    if evidence["accepted"]:
        return {
            "status": "processing",
            "success": False,
            "message": "搜狐号已接受文章提交，正在等待平台审核或公开链接",
        }
    if evidence["request_count"]:
        return {
            "status": "processing",
            "success": False,
            "message": "已观察到搜狐号文章写入请求，但未取得明确发布结果，请到平台后台核对",
        }
    raise RuntimeError("点击搜狐号发布后未观察到文章提交请求")


async def _confirm_publish_dialog_if_present(page: Any) -> None:
    dialogs = page.locator('[role="dialog"]:visible')
    if not await dialogs.count():
        return
    dialog = dialogs.last
    _label, button = await _require_single_exact_button(
        dialog,
        _CONFIRM_LABELS,
        stage="确认",
    )
    await button.click(timeout=5_000)
    await page.wait_for_timeout(1_000)


async def _require_single_exact_button(
    scope: Any,
    labels: tuple[str, ...],
    *,
    stage: str,
) -> tuple[str, Any]:
    matches: list[tuple[str, Any]] = []
    for label in labels:
        candidates = scope.get_by_role("button", name=label, exact=True)
        for index in range(await candidates.count()):
            candidate = candidates.nth(index)
            if await candidate.is_visible() and await candidate.is_enabled():
                matches.append((label, candidate))
    if len(matches) != 1:
        raise RuntimeError(
            f"搜狐号精确{stage}按钮数量异常：期望 1 个，实际 {len(matches)} 个"
        )
    return matches[0]


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
        public_url = public_url or _extract_public_article_url(str(payload or ""))
        if _payload_indicates_accepted_submission(payload):
            accepted = True
    return {
        "request_count": len(responses),
        "accepted": accepted,
        "public_url": public_url,
    }


def _payload_indicates_accepted_submission(payload: Any) -> bool:
    pending = [payload]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            state = str(
                value.get("status")
                or value.get("state")
                or value.get("publish_status")
                or ""
            ).strip().lower()
            if state in _ACCEPTED_STATES:
                return True
            for key in ("message", "msg", "reason", "description", "detail"):
                message = str(value.get(key) or "").strip().lower()
                if any(marker in message for marker in _ACCEPTED_MESSAGES):
                    return True
            pending.extend(value.values())
        elif isinstance(value, (list, tuple)):
            pending.extend(value)
    return False


def _extract_public_article_url(value: str) -> str:
    match = _PUBLIC_ARTICLE_URL_PATTERN.search(value)
    return match.group(0) if match else ""


def _is_navigation_context_error(exc: Exception) -> bool:
    detail = str(exc).lower()
    return "execution context was destroyed" in detail and "navigation" in detail
