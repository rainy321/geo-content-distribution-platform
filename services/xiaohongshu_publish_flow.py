from __future__ import annotations

import re
from typing import Any


_PUBLIC_NOTE_URL_PATTERN = re.compile(
    r"https?://(?:www\.)?xiaohongshu\.com/(?:explore|discovery/item)/[a-f0-9]+",
    re.IGNORECASE,
)
_NOTE_ID_PATTERN = re.compile(r"[a-f0-9]{24}", re.IGNORECASE)
_NOTE_MANAGER_URL = "https://creator.xiaohongshu.com/new/note-manager"
_POSTED_ENDPOINT_MARKER = "/creator/note/user/posted"
_SUBMISSION_METHODS = frozenset({"POST", "PUT", "PATCH"})
_SUBMISSION_URL_MARKERS = ("publish", "note", "content")


async def click_exact_publish_and_observe(
    page: Any,
    *,
    scheduled: bool = False,
    title: str = "",
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
                if title.strip():
                    try:
                        reconciled = await _reconcile_posted_note(page, title.strip())
                    except Exception as exc:
                        detail = str(exc).strip() or exc.__class__.__name__
                        reconciled = {
                            "status": "processing",
                            "success": False,
                            "message": (
                                "小红书已显示发布成功页，但已发布列表对账失败："
                                f"{detail}"
                            ),
                        }
                    if reconciled:
                        return reconciled
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


async def _reconcile_posted_note(page: Any, title: str) -> dict[str, Any]:
    """Read creator evidence after one successful submit; never click publish."""

    posted_responses: list[Any] = []

    def record_response(response: Any) -> None:
        if _POSTED_ENDPOINT_MARKER in str(response.url or "").lower():
            posted_responses.append(response)

    page.on("response", record_response)
    try:
        await page.goto(_NOTE_MANAGER_URL, wait_until="domcontentloaded", timeout=60_000)
        await page.wait_for_timeout(5_000)
        candidates = page.get_by_text("已发布", exact=True)
        matches = []
        for index in range(await candidates.count()):
            candidate = candidates.nth(index)
            if await candidate.is_visible() and await candidate.is_enabled():
                matches.append(candidate)
        if len(matches) != 1:
            return {
                "status": "processing",
                "success": False,
                "message": (
                    "小红书已显示发布成功页，但“已发布”筛选控件数量异常："
                    f"期望 1 个，实际 {len(matches)} 个"
                ),
            }
        await matches[0].click(timeout=5_000)
        await page.wait_for_timeout(3_000)

        for response in reversed(posted_responses):
            if not 200 <= int(response.status) < 300:
                continue
            try:
                payload = await response.json()
            except Exception:
                continue
            note_id = _find_published_note_id(payload, title)
            if note_id:
                public_url = f"https://www.xiaohongshu.com/explore/{note_id}"
                return {
                    "status": "success",
                    "success": True,
                    "url": public_url,
                    "message": "小红书创作中心“已发布”列表与 posted 接口已核验",
                }

        title_candidates = page.get_by_text(title, exact=True)
        visible_titles = 0
        for index in range(await title_candidates.count()):
            if await title_candidates.nth(index).is_visible():
                visible_titles += 1
        if visible_titles == 1:
            return {
                "status": "processing",
                "success": False,
                "message": (
                    "小红书“已发布”列表已出现精确标题，但未取得 note ID；"
                    "请只读核对，不要重发"
                ),
            }
        return {
            "status": "processing",
            "success": False,
            "message": "小红书已显示发布成功页，但“已发布”列表尚未出现精确标题",
        }
    finally:
        try:
            page.remove_listener("response", record_response)
        except Exception:
            pass


def _find_published_note_id(value: Any, title: str) -> str:
    pending = [value]
    while pending:
        current = pending.pop()
        if isinstance(current, dict):
            current_title = str(
                current.get("display_title") or current.get("title") or ""
            ).strip()
            note_id = str(
                current.get("id")
                or current.get("note_id")
                or current.get("noteId")
                or ""
            ).strip()
            if current_title == title and _NOTE_ID_PATTERN.fullmatch(note_id):
                return note_id.lower()
            pending.extend(current.values())
        elif isinstance(current, (list, tuple)):
            pending.extend(current)
    return ""


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
