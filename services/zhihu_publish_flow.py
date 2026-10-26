from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit


_ARTICLE_URL_PATTERN = re.compile(r"https://zhuanlan\.zhihu\.com/p/[0-9]+")
_SUBMISSION_METHODS = frozenset({"POST", "PUT", "PATCH"})
_SUBMISSION_URL_MARKERS = ("article", "publish")
_RESPONSE_HOSTS = frozenset({'www.zhihu.com', 'zhuanlan.zhihu.com', 'api.zhihu.com'})


class ZhihuAIStatementNotConfirmedError(RuntimeError):
    """The declaration check stopped before the uploader's publish action."""


class ZhihuEditorNotReadyError(RuntimeError):
    """The editor preparation failed before any uploader publish action."""

    def __init__(self, message="", *, reason_code="editor_not_ready"):
        super().__init__(message)
        allowed = {'editor_not_ready', 'editor_preparation_failed', 'editor_existing_content',
                   'title_readback_mismatch', 'body_readback_mismatch', 'body_input_empty',
                   'markdown_unsupported', 'markdown_import_controls_unconfirmed',
                   'markdown_import_unconfirmed', 'markdown_semantic_mismatch'}
        self.reason_code = reason_code if reason_code in allowed else 'editor_not_ready'


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
        url = urlsplit(str(response.url or ""))
        if (method in _SUBMISSION_METHODS and url.scheme == 'https'
                and url.netloc in _RESPONSE_HOSTS
                and any(marker in url.path.lower() for marker in _SUBMISSION_URL_MARKERS)):
            submission_responses.append(response)

    confirmation_pending = False
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
            confirmation_pending = not await _confirm_publish_dialog_if_present(page)
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

    if confirmation_pending:
        return {
            'status': 'processing', 'success': False,
            'message': '知乎仍有待确认的弹窗，请先在平台核对；不会重复点击发布',
        }
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
    return {
        'status': 'processing', 'success': False,
        'message': '已点击知乎发布，但尚未观察到明确提交结果，请先核对平台状态；不会自动重试',
    }


async def _confirm_publish_dialog_if_present(page: Any) -> bool:
    dialogs = page.locator('[role="dialog"]:visible')
    if not await dialogs.count():
        return True
    dialog = dialogs.last
    for label in ("确认发布", "发布", "确定"):
        candidates = dialog.get_by_role("button", name=label, exact=True)
        for index in range(await candidates.count()):
            candidate = candidates.nth(index)
            if await candidate.is_visible() and await candidate.is_enabled():
                await candidate.click(timeout=5_000)
                await page.wait_for_timeout(1_000)
                return True
    return False


async def _summarize_submission_responses(responses: list[Any]) -> dict[str, Any]:
    article_url = ""
    explicit_publish_success = False
    successful_count = 0
    for response in responses:
        status = int(response.status)
        if not 200 <= status < 300:
            continue
        successful_count += 1
        try:
            payload = await response.json()
        except Exception:
            payload = None
        nodes = _result_nodes(payload)
        states = {str(node.get(key) or '').lower()
                  for node in nodes for key in ('status', 'state', 'publish_status')}
        negative = {'draft', 'saved', 'autosaved', 'unpublished', 'unsubmitted',
                    'pending', 'pending_review', 'under_review', 'reviewing', 'processing', 'failed', 'error'}
        if (states & negative or any(
                node.get('is_draft') is True or node.get('published') is False
                or node.get('is_published') is False or node.get('success') is False
                or bool(node.get('error')) or bool(node.get('errors')) for node in nodes)):
            continue
        parsed = urlsplit(str(response.url or ''))
        publish_request = (parsed.scheme == 'https' and parsed.netloc in _RESPONSE_HOSTS
                           and parsed.path.rstrip('/').rsplit('/', 1)[-1].lower() == 'publish')
        published = 'published' in states
        submitted = 'submitted' in states
        if publish_request or published or submitted:
            explicit_publish_success = True
            # A submitted/review-pending article may not be public yet.
            # A draft/save URL from another response is never reused here.
            if submitted and not published:
                continue
            for node in nodes:
                for key in ('article_url', 'url', 'share_url'):
                    value = node.get(key)
                    candidate = _extract_article_url(value) if isinstance(value, str) else ''
                    article_url = article_url or candidate
    return {
        "request_count": len(responses),
        "successful_request_count": successful_count,
        "explicit_publish_success": explicit_publish_success,
        "article_url": article_url,
    }


def _extract_article_url(value: str) -> str:
    # The editor auto-saves to /p/<id>/edit. Prefix extraction would turn that
    # private draft route (or an arbitrary longer string) into false success.
    return value if _ARTICLE_URL_PATTERN.fullmatch(value) else ''


def _result_nodes(payload: Any, depth: int = 0) -> list[dict]:
    """Inspect result fields, never article text, references or history URLs."""
    if not isinstance(payload, dict) or depth > 3:
        return []
    result = [payload]
    for key in ('data', 'result', 'article', 'publish_result'):
        result.extend(_result_nodes(payload.get(key), depth + 1))
    return result


def _is_navigation_context_error(exc: Exception) -> bool:
    detail = str(exc).lower()
    return "execution context was destroyed" in detail and "navigation" in detail
