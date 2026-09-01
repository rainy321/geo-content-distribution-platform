from __future__ import annotations

import re
from typing import Any


_PUBLIC_NOTE_URL_PATTERN = re.compile(
    r"https?://(?:www\.)?xiaohongshu\.com/(?:explore|discovery/item)/[a-f0-9]+",
    re.IGNORECASE,
)
_SUBMISSION_METHODS = frozenset({"POST", "PUT", "PATCH"})
_SUBMISSION_URL_MARKERS = ("publish", "note", "content")


async def click_exact_publish_and_observe(
    page: Any,
    *,
    scheduled: bool = False,
) -> dict[str, Any]:
    """Click one exact Xiaohongshu action and never retry the submission."""

    responses: list[Any] = []

    def record_response(response: Any) -> None:
        method = str(response.request.method or "").upper()
        url = str(response.url or "").lower()
        if method in _SUBMISSION_METHODS and any(
            marker in url for marker in _SUBMISSION_URL_MARKERS
        ):
            responses.append(response)

    label = "定时发布" if scheduled else "发布"
    page.on("response", record_response)
    try:
        button = await _require_single_exact_button(page, label)
        await button.click(timeout=5_000)
        for _ in range(30):
            await page.wait_for_timeout(500)
            current_url = str(page.url or "")
            public_url = _extract_public_note_url(current_url)
            if public_url:
                return {
                    "status": "success",
                    "success": True,
                    "url": public_url,
                    "message": "小红书已跳转到公开笔记页面",
                }
            if "/publish/success" in current_url:
                return {
                    "status": "processing",
                    "success": False,
                    "message": "小红书已显示发布成功页，等待取得可核验的公开笔记链接",
                }
            blocking = await _detect_manual_verification(page)
            if blocking:
                raise RuntimeError(blocking)
    finally:
        try:
            page.remove_listener("response", record_response)
        except Exception:
            pass

    public_url = await _extract_public_url_from_responses(responses)
    if public_url:
        return {
            "status": "success",
            "success": True,
            "url": public_url,
            "message": "小红书发布接口已返回公开笔记链接",
        }
    if responses:
        return {
            "status": "processing",
            "success": False,
            "message": "已观察到小红书内容提交请求，但最终状态未知，请先到平台后台核对",
        }
    raise RuntimeError("点击小红书发布后未观察到内容提交请求，页面结构可能变化")


async def _require_single_exact_button(scope: Any, label: str) -> Any:
    candidates = scope.get_by_role("button", name=label, exact=True)
    matches = []
    for index in range(await candidates.count()):
        candidate = candidates.nth(index)
        if await candidate.is_visible() and await candidate.is_enabled():
            matches.append(candidate)
    if len(matches) != 1:
        raise RuntimeError(
            f"小红书精确{label}按钮数量异常：期望 1 个，实际 {len(matches)} 个；页面结构可能变化"
        )
    return matches[0]


async def _detect_manual_verification(page: Any) -> str:
    try:
        body = ((await page.inner_text("body")) or "")[:4000]
    except Exception:
        return ""
    for marker in ("获取验证码", "请输入验证码", "安全验证", "请完成验证"):
        if marker in body:
            return f"小红书出现{marker}，需要人工确认"
    return ""


async def _extract_public_url_from_responses(responses: list[Any]) -> str:
    for response in responses:
        if not 200 <= int(response.status) < 300:
            continue
        try:
            payload = await response.json()
        except Exception:
            continue
        public_url = _extract_public_note_url(str(payload or ""))
        if public_url:
            return public_url
    return ""


def _extract_public_note_url(value: str) -> str:
    match = _PUBLIC_NOTE_URL_PATTERN.search(value)
    return match.group(0) if match else ""
