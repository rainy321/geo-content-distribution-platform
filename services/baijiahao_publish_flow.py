from __future__ import annotations

import re
from typing import Any


_PUBLIC_ARTICLE_URL_PATTERN = re.compile(
    r"https?://baijiahao\.baidu\.com/s\?id=\d+"
)
_SUBMISSION_METHODS = frozenset({"POST", "PUT", "PATCH"})
_OBSERVED_RESPONSE_URL_MARKERS = (
    "publish",
    "submit",
    "commit",
    "article",
    "news",
    "content",
)
_NON_SUBMISSION_URL_MARKERS = (
    "autosave",
    "auto-save",
    "draft",
    "save",
    "upload",
    "cover",
    "image",
    "material",
    "topic",
    "report",
    "track",
    "event",
)
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
_REJECTED_STATES = frozenset(
    {"blocked", "denied", "error", "failed", "failure", "invalid", "rejected"}
)
_REJECTED_MESSAGES = (
    "失败",
    "错误",
    "未通过",
    "不符合",
    "不能为空",
    "请填写",
    "请上传",
    "无权限",
    "被拒绝",
    "blocked",
    "denied",
    "failed",
    "invalid",
    "rejected",
)
_MANUAL_ACTION_MESSAGES = (
    "验证",
    "登录",
    "扫码",
    "人机",
    "风控",
    "captcha",
    "risk",
)


async def click_exact_publish_and_observe(
    page: Any,
    *,
    title: str = "",
) -> dict[str, Any]:
    """Click one exact Baijiahao publish button and report observed evidence."""

    responses: list[Any] = []

    def record_response(response: Any) -> None:
        method = str(response.request.method or "").upper()
        url = str(response.url or "").lower()
        if method in _SUBMISSION_METHODS and any(
            marker in url for marker in _OBSERVED_RESPONSE_URL_MARKERS
        ):
            responses.append(response)

    page.on("response", record_response)
    try:
        button = await _require_single_exact_button(page, "发布")
        await button.click(timeout=5_000)
        try:
            await page.wait_for_timeout(1_500)
            await _confirm_dialog_if_present(page)
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

    public_url = _extract_public_article_url(_safe_page_url(page))
    if public_url:
        return {
            "status": "success",
            "success": True,
            "url": public_url,
            "message": "百家号已跳转到公开文章页面",
        }

    evidence = await _summarize_responses(responses)
    if evidence["public_url"]:
        return {
            "status": "success",
            "success": True,
            "url": evidence["public_url"],
            "message": "百家号发布接口已返回公开文章链接",
        }
    public_url = await _find_correlated_public_link(
        page,
        title,
        evidence["submission_id"],
    )
    if public_url:
        return {
            "status": "success",
            "success": True,
            "url": public_url,
            "message": "百家号内容列表已按同标题和提交编号核验公开文章",
        }
    if evidence["accepted"]:
        return {
            "status": "processing",
            "success": False,
            "message": (
                "百家号已接受文章提交，正在等待平台审核或公开链接；"
                "系统不会自动重复提交，请稍后核验平台结果"
            ),
        }
    if evidence["rejected_message"]:
        detail = evidence["rejected_message"]
        status = (
            "need_action"
            if any(marker in detail.lower() for marker in _MANUAL_ACTION_MESSAGES)
            else "failed"
        )
        return {
            "status": status,
            "success": False,
            "message": (
                f"百家号明确拒绝文章提交：{detail}；"
                "本次未确认发布成功，系统不会自动重试"
            ),
        }
    if evidence["submission_request_count"]:
        return {
            "status": "processing",
            "success": False,
            "message": (
                "已观察到百家号发布请求，但平台响应未明确接受或拒绝；"
                "请先到内容列表核验，系统不会自动重试"
            ),
        }
    if evidence["request_count"]:
        return {
            "status": "processing",
            "success": False,
            "message": (
                "仅观察到百家号编辑器写入或自动保存请求，未观察到明确发布确认；"
                "请先到内容列表核验，系统不会自动重试"
            ),
        }
    raise RuntimeError("点击百家号发布后未观察到文章提交请求")


async def _require_single_exact_button(scope: Any, label: str) -> Any:
    candidates = scope.get_by_role("button", name=label, exact=True)
    matches = []
    for index in range(await candidates.count()):
        candidate = candidates.nth(index)
        if await candidate.is_visible() and await candidate.is_enabled():
            matches.append(candidate)
    if len(matches) != 1:
        raise RuntimeError(
            f"百家号精确发布按钮数量异常：期望 1 个，实际 {len(matches)} 个"
        )
    return matches[0]


async def _confirm_dialog_if_present(page: Any) -> None:
    dialogs = page.locator('[role="dialog"]:visible')
    if not await dialogs.count():
        return
    dialog = dialogs.last
    matches = []
    for label in ("确认发布", "确认并发布", "确定"):
        candidates = dialog.get_by_role("button", name=label, exact=True)
        for index in range(await candidates.count()):
            candidate = candidates.nth(index)
            if await candidate.is_visible() and await candidate.is_enabled():
                matches.append(candidate)
    if len(matches) != 1:
        raise RuntimeError(
            f"百家号精确确认按钮数量异常：期望 1 个，实际 {len(matches)} 个"
        )
    await matches[0].click(timeout=5_000)
    await page.wait_for_timeout(1_000)


async def _summarize_responses(responses: list[Any]) -> dict[str, Any]:
    public_url = ""
    accepted = False
    rejected_message = ""
    submission_id = ""
    submission_request_count = 0
    for response in responses:
        response_url = str(getattr(response, "url", "") or "").lower()
        if not _is_explicit_submission_url(response_url):
            continue
        submission_request_count += 1
        response_status = _response_status(response)
        try:
            payload = await response.json()
        except Exception:
            payload = None
        if not 200 <= response_status < 300:
            accepted = False
            rejected_message = _payload_rejection_message(payload) or f"HTTP {response_status}"
            continue
        public_url = public_url or _extract_public_article_url(str(payload or ""))
        rejection = _payload_rejection_message(payload)
        if rejection:
            accepted = False
            rejected_message = rejection
            continue
        if _payload_indicates_accepted(payload) or _payload_has_success_code(payload):
            accepted = True
            rejected_message = ""
            submission_id = _payload_submission_id(payload) or submission_id
    return {
        "request_count": len(responses),
        "submission_request_count": submission_request_count,
        "accepted": accepted,
        "public_url": public_url,
        "rejected_message": rejected_message,
        "submission_id": submission_id,
    }


def _is_explicit_submission_url(value: str) -> bool:
    normalized = str(value or "").strip().lower()
    if not normalized or any(
        marker in normalized for marker in _NON_SUBMISSION_URL_MARKERS
    ):
        return False
    return bool(
        re.search(
            r"(?:^|[/_?&=.-])(publish|submit|commit)(?:$|[/_?&=.-])",
            normalized,
        )
    )


def _response_status(response: Any) -> int:
    try:
        return int(getattr(response, "status", 0))
    except (TypeError, ValueError):
        return 0


def _payload_indicates_accepted(payload: Any) -> bool:
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


def _payload_has_success_code(payload: Any) -> bool:
    pending = [payload]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            if value.get("success") is True or value.get("accepted") is True:
                return True
            if any(
                key in value and value.get(key) in (0, "0", 200, "200")
                for key in ("code", "errno", "err_no", "error_code")
            ):
                return True
            pending.extend(value.values())
        elif isinstance(value, (list, tuple)):
            pending.extend(value)
    return False


def _payload_submission_id(payload: Any) -> str:
    pending = [payload]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            for key in (
                "article_id",
                "articleId",
                "news_id",
                "newsId",
                "content_id",
                "contentId",
                "item_id",
                "itemId",
            ):
                candidate = str(value.get(key) or "").strip()
                if candidate.isdigit():
                    return candidate
            pending.extend(value.values())
        elif isinstance(value, (list, tuple)):
            pending.extend(value)
    return ""


def _payload_rejection_message(payload: Any) -> str:
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
            messages = [
                str(value.get(key) or "").strip()
                for key in (
                    "message",
                    "msg",
                    "errmsg",
                    "error",
                    "reason",
                    "description",
                    "detail",
                )
            ]
            message = next((item for item in messages if item), "")
            failed_code = any(
                key in value
                and value.get(key) not in (None, "", 0, "0", 200, "200", True)
                for key in ("code", "errno", "error_code")
            )
            explicitly_failed = (
                state in _REJECTED_STATES
                or value.get("success") is False
                or value.get("accepted") is False
                or failed_code
            )
            message_is_rejection = any(
                marker in message.lower() for marker in _REJECTED_MESSAGES
            )
            if explicitly_failed and (message or state):
                return (message or state)[:300]
            if message_is_rejection:
                return message[:300]
            pending.extend(value.values())
        elif isinstance(value, (list, tuple)):
            pending.extend(value)
    return ""


async def _find_correlated_public_link(
    page: Any,
    title: str,
    submission_id: str,
) -> str:
    normalized_title = str(title or "").strip()
    normalized_submission_id = str(submission_id or "").strip()
    if not normalized_title or not normalized_submission_id:
        return ""
    try:
        candidates = page.locator('a[href*="baijiahao.baidu.com/s"]')
        for index in range(await candidates.count()):
            candidate = candidates.nth(index)
            text = str(await candidate.inner_text() or "").strip()
            if text != normalized_title:
                continue
            href = str(await candidate.get_attribute("href") or "").strip()
            public_url = _extract_public_article_url(href)
            article_id_match = re.search(r"[?&]id=(\d+)", public_url)
            if (
                article_id_match
                and article_id_match.group(1) == normalized_submission_id
            ):
                return public_url
    except Exception:
        return ""
    return ""


def _extract_public_article_url(value: str) -> str:
    match = _PUBLIC_ARTICLE_URL_PATTERN.search(value)
    return match.group(0) if match else ""


def _is_navigation_context_error(exc: Exception) -> bool:
    detail = str(exc).lower()
    return (
        ("execution context was destroyed" in detail and "navigation" in detail)
        or "target page, context or browser has been closed" in detail
        or "target closed" in detail
        or "page has been closed" in detail
    )


def _safe_page_url(page: Any) -> str:
    try:
        return str(page.url or "")
    except Exception:
        return ""
