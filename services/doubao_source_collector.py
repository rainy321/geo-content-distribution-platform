"""Collect one consumer-web answer and its displayed sources, with local evidence.

This module never calls private Doubao endpoints, solves challenges, or invents
URLs. Selectors for answer/source UI must be checked against the current client.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import re
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from services.browser_runtime import launch_context
from services.doubao_answer_state import is_deferred_answer
from services.doubao_answer_identity import prove_progress_body_elision
from services.doubao_submission_transport import EDITOR_READBACK_JS, LITERAL_PREAMBLE, submission_fence, submission_text


DOUBAO_URL = "https://www.doubao.com/chat/"
DEFAULT_QUESTION = "北京同于野科技有限公司的懂AI中转站是什么？请结合可查询到的公开网络信息介绍其功能；如果资料不足，请明确说明。"
HK_TIME = timezone(timedelta(hours=8))


@dataclass(frozen=True)
class DoubaoSelectors:
    # Current message/completion/search-material selectors observed 2026-10-12.
    # Other variants remain conservative candidates; re-check after UI changes.
    editor: str = 'div.tiptap[contenteditable="true"], textarea'
    user_message: str = '[data-message-role="user"], [data-testid="user_message"], [data-testid="send_message"]'
    assistant_message: str = '[data-message-role="assistant"], [data-testid="assistant_message"], [data-testid="receive_message"]'
    answer_text: str = '[data-testid="message_text_content"], [data-testid="message-content"], [class*="markdown-body"]'
    completion: str = '[data-testid="message_action_copy"], [data-testid="message_copy_button"], button[aria-label="复制"]'
    busy: str = '[aria-busy="true"], [data-testid="chat_input_stop_button"], button[aria-label="停止生成"]'
    source_trigger: str = '[data-testid="search-references-button"], [data-testid="reference-button"], [data-plugin-identifier*="search_query_result_block"] [data-copy-ignore]'
    source_panel: str = '[data-testid="search-references"], [data-testid="reference-list"], [data-plugin-identifier*="search_query_result_block"]'
    source_card: str = '[data-testid="reference-item"], [data-testid="source-item"], a[data-thinking-box-tool-call="true"]'
    source_title: str = '[data-testid="reference-title"], [data-testid="source-title"], [data-thinking-box-tool-call] > div, h3, h4'
    inline_citation: str = 'a[data-citation], button[data-citation], [data-testid="citation-link"], span.container-sWvQla'
    citation_popup: str = '[role="tooltip"].semi-popover-wrapper-show:not(.semi-tooltip-animation-hide)'
    citation_popup_title: str = '.title-ehsWfR'
    citation_popup_label: str = '.footer-website-VEYotM'
    login_dialog: str = '[data-testid="login_modal"], [role="dialog"]:has(input[type="tel"])'
    challenge: str = '[id*="captcha"], [class*="captcha-verify"], iframe[src*="captcha"]'
    model_label: str = '[data-testid="chat_input_action_model"]'


@dataclass(frozen=True)
class CollectorConfig:
    question: str = DEFAULT_QUESTION
    output_root: Path = Path("runtime/doubao/evidence")
    profile_dir: Path = Path("runtime/doubao/browser-profile")
    headed: bool = False
    timeout_seconds: float = 180
    navigation_timeout_seconds: float = 45
    stable_seconds: float = 3
    manual_wait_seconds: float | None = 0
    executable_path: str | None = None
    fallback_executable_path: str | None = None
    client_mode: str = "consumer"
    requested_model_label: str | None = None
    submission_format: str = 'plain'
    selectors: DoubaoSelectors = DoubaoSelectors()

    def __post_init__(self) -> None:
        if self.client_mode not in {"consumer", "work"}:
            raise ValueError("client_mode 必须是 consumer 或 work")
        if self.submission_format not in {'plain', 'verbatim_fenced'}:
            raise ValueError('submission_format 必须是 plain 或 verbatim_fenced')
        if self.requested_model_label is not None and (not self.requested_model_label.strip() or len(self.requested_model_label) > 100):
            raise ValueError("模型标签必须是 1 到 100 字的界面文本")
        if not self.question.strip():
            raise ValueError("问题不能为空")
        for name in ("timeout_seconds", "navigation_timeout_seconds", "stable_seconds"):
            value = getattr(self, name)
            if not 0 < value <= 600:
                raise ValueError(f"{name} 必须在 0 到 600 秒之间")
        if self.manual_wait_seconds is not None and not 0 <= self.manual_wait_seconds <= 600:
            raise ValueError("人工验证等待时间必须在 0 到 600 秒之间")
        if (self.manual_wait_seconds is None or self.manual_wait_seconds) and not self.headed:
            raise ValueError("人工验证等待需要 --headed 可见浏览器")


class ManualWindowClosedError(ValueError):
    """The owner closed the current request instead of continuing verification."""


class SubmissionReceipt:
    """Bind a disappeared user bubble to one narrowly observed conversation.

    Normal literal user-message evidence remains preferred. The fallback only
    covers the consumer client's local-ID to canonical-ID transition after a
    manual challenge; it is invalidated permanently by other top-level routes.
    """

    def __init__(self, page, question, baseline_count, *, submitted_text=None,
                 editor_readback=None, submission_format='plain', baseline_user_count=None):
        self.page, self.question, self.baseline_count = page, question, baseline_count
        self.question_sha256 = hashlib.sha256(question.encode('utf-8')).hexdigest()
        expected_submission = submitted_text if submitted_text is not None else question
        self.submitted_text_sha256 = hashlib.sha256(expected_submission.encode('utf-8')).hexdigest()
        self.editor_readback_verified = editor_readback is not None and editor_readback == expected_submission
        self.submission_format, self.baseline_user_count = submission_format, baseline_user_count
        self.attempts, self.observed_url, self.observed_at = 0, None, None
        self.navigation_urls, self.invalid_reason = [], None
        self.manual_action_seen, self.navigation_tracking = False, False
        self.deferred_assistant_count = None
        if hasattr(page, 'on') and hasattr(page, 'main_frame'):
            def navigated(frame):
                if frame == page.main_frame:
                    self._navigate(frame.url)
            page.on('framenavigated', navigated)
            self.navigation_tracking = True

    @staticmethod
    def _route(value):
        try:
            parsed = urlsplit(value)
            if (parsed.scheme != 'https' or parsed.hostname != 'www.doubao.com' or parsed.port not in (None, 443)
                    or parsed.username or parsed.password or parsed.query or parsed.fragment):
                return None
            match = re.fullmatch(r'/chat/(local_\d+|\d+)/?', parsed.path)
            return ('local' if match.group(1).startswith('local_') else 'canonical', parsed.path.rstrip('/')) if match else None
        except (TypeError, ValueError):
            return None

    def attempted(self):
        self.attempts += 1
        if self.attempts != 1:
            self.invalid_reason = 'multiple_submission_attempts'

    def _navigate(self, url):
        if not self.observed_url or (self.navigation_urls and self.navigation_urls[-1] == url):
            return
        self.navigation_urls.append(url)
        if len(self.navigation_urls) > 8:
            self.invalid_reason = 'navigation_chain_too_long'
            return
        routes = [self._route(value) for value in self.navigation_urls]
        if not all(routes) or any(route[0] != 'canonical' for route in routes[1:]) or len(set(route for route in routes[1:])) > 1:
            self.invalid_reason = 'conversation_navigation_changed'
        elif routes[0][0] == 'canonical' and any(route != routes[0] for route in routes[1:]):
            self.invalid_reason = 'conversation_navigation_changed'

    def observe(self, page, question, snapshot):
        if page is not self.page or question != self.question or self.attempts != 1:
            self.invalid_reason = 'request_identity_changed'
            return False, None
        url = snapshot.get('page_url')
        if self.observed_url:
            self._navigate(url)
        if self.invalid_reason:
            return False, None
        if isinstance(snapshot.get('user_message_count'), int) and snapshot['user_message_count'] > 1:
            self.invalid_reason = 'multiple_user_messages'
            return False, None
        literal = snapshot.get('question_verified') is True and self.submission_format == 'plain'
        fenced = (snapshot.get('verbatim_question_verified') is True and self.submission_format == 'verbatim_fenced'
                  and self.editor_readback_verified and self.baseline_count == 0 and self.baseline_user_count == 0
                  and snapshot.get('user_message_count') == 1)
        if literal or fenced:
            if not self.observed_url:
                self.observed_url, self.observed_at = url, datetime.now(HK_TIME).isoformat()
                self.navigation_urls = [url]
            return True, 'literal_user_dom' if literal else 'verbatim_fenced_dom_with_editor_readback'
        if snapshot.get('user_message_count', 0):
            # React may first mount one incomplete user bubble, then render
            # its exact text. Until literal receipt exists this is unverified,
            # not evidence that a previously confirmed request was replaced.
            if self.observed_url:
                self.invalid_reason = 'different_user_message'
            return False, None
        routes = [self._route(value) for value in self.navigation_urls]
        permitted = (self.navigation_tracking and self.manual_action_seen and self.baseline_count == 0
                     and len(routes) >= 2 and all(routes) and routes[0][0] == 'local'
                     and routes[-1][0] == 'canonical' and len(set(routes[1:])) == 1
                     and snapshot.get('user_message_count') == 0
                     and type(snapshot.get('assistant_count')) is int
                     and snapshot.get('assistant_count') in {1, self.deferred_assistant_count,
                         self.deferred_assistant_count + 1 if self.deferred_assistant_count is not None else 1})
        return bool(permitted), 'bound_local_to_canonical_after_manual' if permitted else None

    def observed_deferred(self, snapshot, *, verified):
        # Only a notice already bound to this exact submission may authorize
        # one appended agent result, within the same immutable navigation chain.
        count = snapshot.get('assistant_count')
        if verified and type(count) is int and count > self.baseline_count and is_deferred_answer(snapshot.get('answer')):
            self.deferred_assistant_count = count

    def evidence(self):
        return {'question_sha256': self.question_sha256, 'submission_attempts': self.attempts,
                'baseline_assistant_count': self.baseline_count, 'observed_url': self.observed_url,
                'baseline_user_count': self.baseline_user_count, 'submission_format': self.submission_format,
                'submitted_text_sha256': self.submitted_text_sha256, 'editor_readback_verified': self.editor_readback_verified,
                'observed_at': self.observed_at, 'navigation_urls': list(self.navigation_urls),
                'navigation_tracking': self.navigation_tracking, 'manual_action_seen': self.manual_action_seen,
                'deferred_assistant_count': self.deferred_assistant_count,
                'invalid_reason': self.invalid_reason}


def _capture_submission(page, config, run_dir, result, snapshot, submission):
    verified, method = submission.observe(page, config.question, snapshot)
    if verified and method in {'literal_user_dom', 'verbatim_fenced_dom_with_editor_readback'} and not result['question_submitted']:
        result['question_submitted'] = True
        (run_dir / 'submission-observed.json').write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding='utf-8')
    result['submission_receipt'] = submission.evidence()
    if verified:
        result['question_verification_method'] = method
        if snapshot.get('question_presentation_variant'):
            result['question_presentation_variant'] = snapshot['question_presentation_variant']
    return verified


class CompletionTracker:
    """Quiet text alone is insufficient: require a fresh answer and UI end marker."""

    def __init__(self, baseline_count: int, stable_seconds: float = 3) -> None:
        self.baseline_count = baseline_count
        self.stable_seconds = stable_seconds
        self.last_text = ""
        self.changed_at = 0.0

    def observe(self, snapshot: dict[str, Any], now: float, *, submission_verified: bool | None = None) -> bool:
        text = snapshot.get("answer", "").strip()
        if text != self.last_text:
            self.last_text = text
            self.changed_at = now
        return bool(
            text
            and snapshot.get("assistant_count", 0) > self.baseline_count
            and (snapshot.get("question_verified") if submission_verified is None else submission_verified)
            and snapshot.get("completion_marker")
            and not snapshot.get("busy")
            and not _blocked_reason(snapshot)
            and not is_deferred_answer(text)
            and now - self.changed_at >= self.stable_seconds
        )


def normalize_ui_sources(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Preserve literal UI evidence and missing URLs; never infer links from titles."""
    sources: list[dict[str, Any]] = []
    seen: dict[tuple[str | None, str], dict[str, Any]] = {}
    for item in items:
        if item.get("scope") not in {"source_panel", "inline_citation"}:
            continue
        title = str(item.get("title") or "").strip()
        raw_href = str(item.get("href") or "").strip()
        raw_url = str(item.get("observed_url") or raw_href).strip()
        try:
            parsed = urlsplit(raw_url)
            valid = parsed.scheme in {"http", "https"} and bool(parsed.hostname) and not parsed.username and not parsed.password
        except ValueError:
            valid = False
        url = raw_url if valid else None
        domain = parsed.hostname.casefold() if valid else None
        if not title and not url:
            continue
        identity = (url, "" if url else title)
        linked_text = str(item.get("linked_answer_text") or "").strip() or None
        if identity in seen:
            entry = seen[identity]
            if title and title not in entry["ui_titles"]:
                entry["ui_titles"].append(title)
            if item["scope"] == "source_panel" and entry["ui_scope"] == "inline_citation":
                entry["title"] = title or entry["title"]
                entry["title_kind"] = item.get("title_kind", "ui_label")
            evidence_file = item.get("evidence_file")
            if evidence_file and evidence_file not in entry["evidence_files"]:
                entry["evidence_files"].append(evidence_file)
            if linked_text and linked_text not in entry["linked_answer_fragments"]:
                entry["linked_answer_fragments"].append(linked_text)
            if item["scope"] == "inline_citation":
                entry["evidence_type"] = "ui_citation"
                entry["linked_answer_text"] = entry["linked_answer_text"] or linked_text
            continue
        entry = {
            "title": title or None,
            "ui_titles": [title] if title else [],
            "title_kind": item.get("title_kind", "ui_label"),
            "domain": domain,
            "url": url,
            "ui_href": raw_href or None,
            "url_observation_method": item.get("url_observation_method", "dom_href" if raw_href else "not_exposed"),
            "url_status": ("navigation_observed" if item.get("observed_url") else "displayed_not_opened") if url else ("unsupported_scheme" if raw_url else "not_exposed"),
            "citation_order": len(sources) + 1,
            "evidence_type": "ui_citation" if item["scope"] == "inline_citation" else "ui_source",
            "linked_answer_text": linked_text,
            "linked_answer_fragments": [linked_text] if linked_text else [],
            "ui_scope": item["scope"],
            "ui_order": item.get("ui_order"),
            "evidence_file": item.get("evidence_file"),
            "evidence_files": [item["evidence_file"]] if item.get("evidence_file") else [],
        }
        seen[identity] = entry
        sources.append(entry)
    return sources


SNAPSHOT_JS = r"""({s, question, allowExternalPanels, submissionFormat, literalPreamble, literalFence}) => {
    const visible = e => !!e && e.getClientRects().length > 0 &&
        getComputedStyle(e).visibility !== 'hidden' && getComputedStyle(e).display !== 'none';
    const all = (root, selector) => [...root.querySelectorAll(selector)].filter(visible);
    const text = e => e ? (e.innerText || e.textContent || '').trim() : '';
    const assistants = all(document, s.assistant_message);
    const answerRoot = assistants.at(-1);
    const matches = answerRoot ? all(answerRoot, s.answer_text) : [];
    const bodies = matches.filter(e => !matches.some(other => other !== e && other.contains(e)));
    const answer = bodies.map(text).filter(Boolean).join('\n\n');
    const users = all(document, s.user_message);
    const normalize = t => t.replace(/\s+/g, '');
    const latestUser = users.at(-1);
    const user = latestUser && normalize(text(all(latestUser, s.answer_text)[0]) || text(latestUser)) === normalize(question) ? latestUser : null;
    const questionVerified = !!user;
    let verbatimQuestionVerified = false;
    let questionPresentationVariant = null;
    if (submissionFormat === 'verbatim_fenced' && users.length === 1 && latestUser) {
        const candidates = all(latestUser, s.answer_text);
        const userBodies = candidates.filter(e => !candidates.some(other => other !== e && other.contains(e)));
        if (userBodies.length === 1) {
            const body = userBodies[0], codes = all(body, 'pre code'), pres = all(body, 'pre');
            if (codes.length === 1 && pres.length === 1 && codes[0].closest('pre') === pres[0]) {
                const literal = codes[0].textContent || '';
                const rest = body.cloneNode(true);
                rest.querySelectorAll('pre,button,[role="button"],[data-copy-ignore="true"]').forEach(e => e.remove());
                // Some Doubao renderers retain the exact authored outer
                // fence inside pre/code, adding one boundary newline before
                // its closing fence. Match complete generated representations;
                // never trim or normalize the logical task payload.
                const variants = [
                    ['literal_code_payload', question],
                    ['literal_code_payload_trailing_newline', question + '\n'],
                    ['literal_code_contains_outer_fence', literalFence + 'text\n' + question + '\n' + literalFence],
                    ['literal_code_contains_outer_fence_extra_boundary_newline', literalFence + 'text\n' + question + '\n\n' + literalFence]
                ];
                const matched = variants.find(([, content]) => literal === content);
                verbatimQuestionVerified = !!matched
                    && normalize(rest.textContent || '') === normalize(literalPreamble || '');
                if (verbatimQuestionVerified) questionPresentationVariant = matched[0];
            }
        }
    }
    const sources = [];
    const add = (node, scope, index) => {
        const a = node.matches('a[href]') ? node : all(node, 'a[href]')[0];
        const titleNode = all(node, s.source_title)[0];
        sources.push({scope, title: text(titleNode) || (a && a.getAttribute('title')) || text(a) || text(node),
            title_kind: titleNode || (a && a.getAttribute('title')) ? 'ui_title' : 'ui_label',
            href: a ? a.href : null, ui_order: index + 1,
            linked_answer_text: scope === 'inline_citation' ? text(node.closest('p,li,blockquote')) || null : null});
    };
    bodies.flatMap(body => all(body, s.inline_citation)).forEach((node, i) => add(node, 'inline_citation', i));
    // Dialogs and ordinary answer hyperlinks are deliberately not source panels.
    const panels = [...new Set([...(answerRoot ? all(answerRoot, s.source_panel) : []),
        ...(allowExternalPanels ? all(document, s.source_panel) : [])])];
    panels.forEach(panel => {
        const cards = all(panel, s.source_card);
        (cards.length ? cards : all(panel, 'a[href]')).forEach((node, i) => add(node, 'source_panel', i));
    });
    const controls = answerRoot ? all(answerRoot, 'button,[role="button"]') : [];
    const triggerCount = answerRoot ? all(answerRoot, s.source_trigger).length +
        controls.filter(e => /^(查看)?(全部)?(\d+\s*(个|篇|条))?\s*(参考资料|搜索来源|来源|引用)(\s*\d+\s*(个|篇|条)?)?$/.test(text(e))).length : 0;
    return {
        page_url: location.href, page_title: document.title,
        assistant_count: assistants.length, answer, question_verified: questionVerified, user_message_count: users.length,
        verbatim_question_verified: verbatimQuestionVerified,
        question_presentation_variant: questionPresentationVariant,
        user_message_dom: user || verbatimQuestionVerified ? latestUser.outerHTML : null,
        completion_marker: !!answerRoot && all(answerRoot, s.completion).length > 0,
        busy: all(document, s.busy).length > 0,
        challenge: all(document, s.challenge).length > 0 || /请选择所有符合|拖拽到这里|请完成安全验证/.test(document.body.innerText),
        login_required: all(document, s.login_dialog).length > 0 || all(document, '[data-testid="to_login_button"]').length > 0,
        session_reset: new URLSearchParams(location.search).get('from_logout') === '1',
        rate_limited: /请求过于频繁|操作频繁，请|访问过于频繁/.test(document.body.innerText),
        service_unavailable: /^算力紧张，办公能力得晚点才能用了/.test(answer.trim()),
        login_state: all(document, '[data-testid="to_login_button"]').length ? 'guest' : 'unknown',
        client_mode: /工作/.test(text(all(document, '[data-testid="workspace-switch-chip"]')[0])) || /^豆包工作/.test(document.title) ? 'work' : 'consumer',
        model_label: text(all(document, s.model_label)[0]) || null,
        source_trigger_count: triggerCount, source_panel_count: panels.length,
        source_ui_summaries: answerRoot ? all(answerRoot, '[data-plugin-identifier*="search_query_result_block"] [data-copy-ignore]').map(text) : [],
        sources, answer_dom: bodies.length ? bodies.map(e => e.outerHTML).join('\n') : null,
        source_panel_dom: panels.map(e => e.outerHTML)
    };
}"""


async def read_snapshot(page: Any, config: CollectorConfig, *, allow_external_panels: bool = False) -> dict[str, Any]:
    payload = {"s": asdict(config.selectors), "question": config.question, "allowExternalPanels": allow_external_panels,
               'submissionFormat': config.submission_format, 'literalPreamble': LITERAL_PREAMBLE,
               'literalFence': submission_fence(config.question) if config.submission_format == 'verbatim_fenced' else None}
    for attempt in range(3):
        try:
            return await page.evaluate(SNAPSHOT_JS, payload)
        except Exception as exc:
            if attempt == 2 or not any(reason in str(exc) for reason in ("Execution context was destroyed", "Cannot find context")):
                raise
            await asyncio.sleep(0.25)  # navigation recovery only; never re-submit
    raise RuntimeError("snapshot_unavailable")


async def _first_visible(locator: Any) -> Any:
    for index in range(await locator.count()):
        item = locator.nth(index)
        if await item.is_visible():
            return item
    return None


async def _navigate_to_doubao(page: Any, timeout_seconds: float) -> None:
    """Retry one failed page load, never a question or a challenged request."""
    for attempt in range(2):
        try:
            await page.goto(DOUBAO_URL, wait_until="domcontentloaded", timeout=timeout_seconds * 1000)
            return
        except Exception as exc:
            if "net::ERR_ABORTED" in str(exc) and getattr(page, "url", "") == DOUBAO_URL:
                # A normal client-side redirect can cancel goto while the actual
                # UI has loaded. Only accept the fixed page with a visible editor.
                try:
                    await page.locator(DoubaoSelectors().editor).first.wait_for(state="visible", timeout=5000)
                    return
                except Exception:
                    pass
            network_error = re.search(r"net::(ERR_CONNECTION_CLOSED|ERR_CONNECTION_RESET|ERR_TIMED_OUT)", str(exc))
            if attempt or not network_error or page.is_closed():
                raise
            await asyncio.sleep(1)


def _browser_error_reason(exc: Exception) -> str:
    if isinstance(exc, ManualWindowClosedError):
        return 'manual_verification_window_closed'
    if isinstance(exc, ValueError) and str(exc).startswith("model_"):
        return str(exc)
    if isinstance(exc, ValueError) and str(exc).startswith(('submission_', 'editor_')):
        return str(exc)
    code = re.search(r"net::(ERR_[A-Z_]+)", str(exc))
    return f"network_error:{code.group(1)}" if code else f"browser_error:{type(exc).__name__}"


async def _expand_sources(page: Any, config: CollectorConfig, run_dir: Path, *, progress: Callable | None = None) -> tuple[list[dict[str, Any]], list[str], bool]:
    # Controls belong to the latest answer, so sidebar/history links cannot leak
    # into the citation list. Each source control is clicked at most once.
    roots = page.locator(config.selectors.assistant_message)
    root = await _first_visible(roots.last)
    if root is None:
        return [], [], False
    snapshot = await read_snapshot(page, config)
    items = [{**item, "evidence_file": "answer.png"} for item in snapshot["sources"]]
    screenshots: list[str] = []
    controls = root.locator(config.selectors.source_trigger)
    if not await controls.count():
        controls = root.get_by_role("button", name=re.compile(r"^(查看)?(全部)?(\d+\s*(个|篇|条))?\s*(参考资料|搜索来源|来源|引用)(\s*\d+\s*(个|篇|条)?)?$"))
    control_count = await controls.count()
    incomplete = control_count > 30 or bool(snapshot["source_trigger_count"] and not control_count)
    def report(phase, processed, total):
        if progress:
            try:
                progress({'event': 'source_progress', 'phase': phase, 'processed': processed, 'total': max(processed, total)})
            except Exception:
                # UI telemetry must not alter evidence or replay a browser action.
                pass
    panel_total = sum([await controls.nth(index).is_visible() for index in range(min(control_count, 30))])
    panel_processed = 0
    report('source_panels', panel_processed, panel_total)
    for index in range(min(await controls.count(), 30)):
        control = controls.nth(index)
        if not await control.is_visible():
            continue
        try:
            already_open = await control.evaluate("""(e, s) => {
                const panel = e.closest(s.source_panel);
                if (!panel) return false;
                const cards = panel.querySelectorAll(s.source_card);
                return [...(cards.length ? cards : panel.querySelectorAll('a[href]'))]
                    .some(node => node.getClientRects().length && getComputedStyle(node).visibility !== 'hidden');
            }""", asdict(config.selectors))
            if already_open:
                continue
            await control.click(timeout=3000)
            panel = page.locator(config.selectors.source_panel)
            await panel.first.wait_for(state="visible", timeout=5000)
            # Allow lazy source cards to appear; a recognized empty panel is not
            # enough to claim extraction succeeded.
            await page.wait_for_timeout(750)
            filename = f"sources-{index + 1}.png"
            await page.screenshot(path=str(run_dir / filename), full_page=True)
            screenshots.append(filename)
            expanded = await read_snapshot(page, config, allow_external_panels=True)
            if not expanded["sources"]:
                incomplete = True
            items.extend({**item, "evidence_file": filename} for item in expanded["sources"])
            (run_dir / f"sources-{index + 1}.json").write_text(json.dumps(expanded, ensure_ascii=False, indent=2), encoding="utf-8")
            await page.keyboard.press("Escape")
        except Exception:
            incomplete = True
        finally:
            panel_processed += 1
            report('source_panels', panel_processed, panel_total)
    if snapshot["source_trigger_count"] and not items:
        incomplete = True
    citations = root.locator(f':is({config.selectors.answer_text}) :is({config.selectors.inline_citation})')
    if await citations.count() > 30:
        incomplete = True
    citation_total = sum([await citations.nth(index).is_visible() for index in range(min(await citations.count(), 30))])
    citation_processed = 0
    report('inline_citations', citation_processed, citation_total)
    for index in range(min(await citations.count(), 30)):
        control = citations.nth(index)
        if await control.evaluate("e => e.matches('a[href]')"):
            if await control.is_visible():
                citation_processed += 1
                report('inline_citations', citation_processed, citation_total)
            continue
        if await control.is_visible():
            try:
                observed, filename = await _resolve_citation_navigation(page, control, run_dir, index, selectors=config.selectors, client_mode=config.client_mode)
                if observed:
                    # Upgrade the same visible marker with its UI-observed target.
                    for item in items:
                        if item["scope"] == "inline_citation" and item.get("ui_order") == index + 1 and not item.get("href"):
                            item.update(observed, evidence_file=filename)
                if filename:
                    screenshots.append(filename)
            finally:
                citation_processed += 1
                report('inline_citations', citation_processed, citation_total)
    return items, screenshots, incomplete


def _citation_title_match(source_title: str, document: dict) -> str | None:
    """Require a public headline, or the original exact document-title proof."""
    fold = lambda value: re.sub(r'\s+', '', value or '').casefold()
    headlines = list(dict.fromkeys(fold(item.get('text')) for item in document.get('headlines', []) if item.get('text')))
    expected = fold(source_title)
    if len(headlines) == 1 and headlines[0] == expected:
        return 'visible_h1_exact'
    # The observed Toutiao layout appends the public author to a source-card
    # title. Strip it only when both the unique headline and that same public
    # author corroborate the exact parts; never infer a URL or match a domain.
    try:
        parsed = urlsplit(document.get('url') or '')
        toutiao_article = parsed.hostname in {'www.toutiao.com', 'm.toutiao.com'} and re.fullmatch(r'/article/\d+/?', parsed.path)
    except ValueError:
        toutiao_article = False
    base, separator, author = source_title.rpartition('_')
    if (toutiao_article and separator and base and author and len(headlines) == 1
            and headlines[0] == fold(base)
            and any(fold(item.get('text')) == fold(author) for item in document.get('authors', []))):
        return 'visible_h1_and_public_author_exact'
    if fold(document.get('title')) == expected:
        return 'document_title_exact'
    return None


def _http_navigation_url(value):
    try:
        parsed = urlsplit(value or '')
        return value if parsed.scheme in {'https', 'http'} and parsed.hostname and not parsed.username and not parsed.password else None
    except ValueError:
        return None


async def _inspect_citation_destination(popup, source_title, deadline):
    """A context page event can precede the initial public navigation by seconds."""
    document = {'url': popup.url, 'title': None, 'headlines': [], 'authors': []}
    while time.monotonic() < deadline and not popup.is_closed():
        try:
            remaining = max(.01, deadline - time.monotonic())
            document = await asyncio.wait_for(popup.evaluate("""() => {
                const visible = e => e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden';
                const capture = selector => [...document.querySelectorAll(selector)].filter(visible).slice(0,20)
                    .map(e => ({text:e.innerText, dom:e.outerHTML.slice(0,4000)}));
                return {url:location.href, title:document.title, ready_state:document.readyState,
                    headlines:capture('h1'), authors:capture('span.name > a, a.user-name')};
            }"""), timeout=min(2, remaining))
            if _http_navigation_url(document.get('url')):
                method = _citation_title_match(source_title, document) if source_title else 'direct_marker_navigation'
                if method and document.get('ready_state') in {'interactive', 'complete'}:
                    return document, method
        except Exception:
            # Page startup and redirects can briefly destroy the read context.
            # Reads are bounded and never navigate or click the source again.
            pass
        await asyncio.sleep(.1)
    return document, None


async def _resolve_citation_navigation(page: Any, control: Any, run_dir: Path,
                                        index: int, *, selectors: DoubaoSelectors = DoubaoSelectors(), client_mode: str = "consumer") -> tuple[dict[str, Any] | None, str | None]:
    """Observe a normal click's new-tab navigation when a UI citation has no href."""
    filename = f"citation-{index + 1}.png"
    evidence: dict[str, Any] = {"captured_at": datetime.now(HK_TIME).isoformat(), "navigation_urls": []}
    popup = None
    previous_pages = set(page.context.pages)
    created_pages, navigations = [], []
    listening = False
    stage = 'hover_card'

    def observe_page(candidate):
        if candidate not in previous_pages and candidate not in created_pages:
            created_pages.append(candidate)

    def observe(request: Any) -> None:
        try:
            if request.is_navigation_request() and request.frame.page not in previous_pages and request.frame == request.frame.page.main_frame:
                navigations.append((request.frame.page, request.url))
        except Exception:
            pass

    try:
        await control.hover(timeout=3000)
        # The UI can keep the previous citation's card visible while lazily
        # replacing it. Wait a finite time for this marker's publisher instead
        # of treating a fixed delay as proof that the card has switched.
        evidence['hover_card_wait_budget_seconds'] = 2
        try:
            await page.wait_for_function("""({s, publisher}) => {
                const visible = e => e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden';
                const fold = value => (value || '').replace(/\\s+/g, '');
                const cards = [...document.querySelectorAll(s.citation_popup)]
                    .filter(e => visible(e) && e.querySelector(s.citation_popup_title));
                if (cards.length !== 1) return false;
                const labels = cards[0].querySelectorAll(s.citation_popup_label);
                const titles = cards[0].querySelectorAll(s.citation_popup_title);
                return labels.length === 1 && titles.length === 1 && visible(titles[0])
                    && fold(labels[0].innerText) === fold(publisher);
            }""", arg={'s': asdict(selectors), 'publisher': await control.inner_text()}, timeout=2000)
            evidence['hover_card_wait_status'] = 'matched'
        except Exception as exc:
            if type(exc).__name__ != 'TimeoutError':
                raise
            evidence['hover_card_wait_status'] = 'publisher_match_timeout'
        evidence["marker_dom"] = await control.evaluate("e => e.outerHTML")
        evidence["tooltip_dom"] = await page.locator('[role="tooltip"]:visible').evaluate_all("nodes => nodes.map(e => e.outerHTML)")
        click_target = control
        source_title = None
        # Work UI labels open a hover card; the source title inside that card is
        # the actual link control. Match the visible publisher to this marker so
        # stale/foreign popovers cannot be attributed to the current citation.
        active = page.locator(f':is({selectors.citation_popup}):visible').filter(has=page.locator(selectors.citation_popup_title))
        if await active.count() == 1:
            labels = active.locator(selectors.citation_popup_label)
            if await labels.count() == 1 and re.sub(r"\s+", "", await labels.inner_text()) == re.sub(r"\s+", "", await control.inner_text()):
                title = active.locator(selectors.citation_popup_title)
                if await title.count() == 1 and await title.is_visible():
                    # Bind the concrete element: a live locator may switch to a
                    # different hover card while the mouse travels to the title.
                    click_target = await title.element_handle()
                    if click_target is None:
                        return None, None
                    stage = 'bind_title'
                    binding = await click_target.evaluate('(e) => ({title:e.innerText, dom:e.outerHTML, connected:e.isConnected})')
                    if not binding['connected']:
                        raise ValueError('citation_title_detached')
                    source_title = binding['title']
                    evidence["source_title"] = source_title
                    evidence["click_target_dom"] = binding['dom']
                    await click_target.evaluate("""(e, s) => {
                        e.addEventListener('click', () => {
                            e.setAttribute('data-geo-click-title', e.innerText);
                            const card = e.closest(s.citation_popup);
                            e.setAttribute('data-geo-click-publisher', card?.querySelector(s.citation_popup_label)?.innerText || '');
                        }, {once: true, capture: true});
                    }""", asdict(selectors))
        stage = 'citation_screenshot'
        await page.screenshot(path=str(run_dir / filename), full_page=True)
        if client_mode == "work" and source_title is None:
            evidence["resolution_status"] = "work_citation_card_not_matched"
            return None, filename
        page.context.on('page', observe_page)
        page.context.on('request', observe)
        listening = True
        deadline = time.monotonic() + 12
        # noopener links can emit only context.page, initially with url=''.
        # One click is the only submission; URL/title reads may wait or repeat.
        stage = 'source_click'
        await click_target.click(timeout=3000, no_wait_after=True)
        stage = 'new_page_wait'
        while not created_pages and time.monotonic() < deadline:
            await asyncio.sleep(.1)
        evidence['new_page_count'] = len(created_pages)
        document, match_method = {}, None
        if len(created_pages) == 1:
            popup = created_pages[0]
            stage = 'destination_read'
            document, match_method = await _inspect_citation_destination(popup, source_title, deadline)
        evidence['new_page_count'] = len(created_pages)
        evidence['navigation_urls'] = [url for candidate, url in navigations if candidate is popup]
        evidence['popup_url'] = popup.url if popup is not None else None
        evidence['destination_title'] = document.get('title')
        evidence['destination_headlines'] = document.get('headlines', [])
        evidence['destination_authors'] = document.get('authors', [])
        evidence['destination_match_method'] = match_method
        observed_navigation = next((url for url in evidence['navigation_urls'] if _http_navigation_url(url)), None)
        evidence['unconfirmed_navigation_url'] = observed_navigation or _http_navigation_url(evidence['popup_url'])
        target = _http_navigation_url(document.get('url')) if match_method and len(created_pages) == 1 else None
        if len(created_pages) > 1:
            evidence['resolution_status'] = 'multiple_new_pages_not_attributed'
        elif not created_pages:
            evidence['resolution_status'] = 'new_page_not_observed'
        if source_title:
            stage = 'click_verification'
            clicked_title = await click_target.get_attribute('data-geo-click-title')
            clicked_publisher = await click_target.get_attribute('data-geo-click-publisher')
            evidence.update(clicked_title=clicked_title, clicked_publisher=clicked_publisher)
            if (clicked_title != source_title or
                    re.sub(r"\s+", "", clicked_publisher or "") != re.sub(r"\s+", "", await control.inner_text())):
                evidence["resolution_status"] = "citation_card_changed_during_click"
                target = None
                source_title = None
            elif not target and len(created_pages) == 1:
                # Work popups can expose the previous card's target even when
                # the current click text is correct. Only attribute a target
                # corroborated by the destination's public document title.
                evidence['resolution_status'] = 'destination_title_not_confirmed'
        evidence["observed_url"] = target
        observed = {"observed_url": target, "url_observation_method": "ui_click_navigation"} if target else {}
        if source_title:
            observed.update(title=source_title, title_kind="ui_title")
        return observed or None, filename
    except Exception as exc:
        evidence.update(resolution_status='citation_navigation_error', failure_stage=stage,
                        error_type=type(exc).__name__)
        return None, filename if (run_dir / filename).is_file() else None
    finally:
        if listening:
            page.context.remove_listener('page', observe_page)
            page.context.remove_listener('request', observe)
        (run_dir / f"citation-{index + 1}.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
        if popup is not None and len(created_pages) == 1 and not popup.is_closed():
            await popup.close()


def write_evidence_result(run_dir: Path, result: dict[str, Any]) -> Path:
    result["evidence_manifest"] = [
        {"path": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "bytes": path.stat().st_size}
        for path in sorted(run_dir.iterdir()) if path.is_file() and path.name != "result.json"
    ]
    result_path = run_dir / "result.json"
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result_path


async def reinspect_existing_sources(config: CollectorConfig, original_result_path: Path, *, page: Any,
                                     progress: Callable | None = None) -> tuple[dict[str, Any], Path]:
    """Inspect sources of an unchanged answer in a caller-owned existing Page.

    Never navigate, create a chat, select a model, fill an editor or send a
    question. A later source inspection is not a new answer or a new test run.
    The caller retains browser/profile ownership and any cross-task lock.
    """
    original_path = Path(original_result_path).resolve()
    if original_path.stat().st_size > 5 * 1024 * 1024:
        raise ValueError('原始采集结果过大')
    original_bytes = original_path.read_bytes()
    original = json.loads(original_bytes.decode('utf-8'))
    if (not isinstance(original, dict) or original.get('question') != config.question
            or original.get('question_submitted') is not True or original.get('answer_completed') is not True
            or not isinstance(original.get('response'), str) or not original['response'].strip()
            or is_deferred_answer(original['response'])
            or original['response'].strip().startswith('算力紧张，办公能力得晚点才能用了')):
        raise ValueError('只可复查原问题已经确认提交且回答完整的采集结果')
    expected_route = SubmissionReceipt._route(original.get('conversation_url'))
    if not expected_route or expected_route[0] != 'canonical':
        raise ValueError('原始结果缺少可绑定的已完成会话网址')
    try:
        config.output_root.resolve().relative_to(original_path.parent)
    except ValueError:
        pass
    else:
        raise ValueError('复查证据必须保存到原始证据目录之外')
    if not hasattr(page, 'main_frame') or not hasattr(page, 'on') or not hasattr(page, 'remove_listener'):
        raise ValueError('复查需要支持主页面导航观察的现有 Page')

    started = datetime.now(HK_TIME)
    run_id = f'inspect-{started:%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:8]}'
    run_dir = config.output_root.resolve() / run_id
    run_dir.mkdir(parents=True)
    original_sha = hashlib.sha256(original_bytes).hexdigest()
    answer_sha = hashlib.sha256(original['response'].encode('utf-8')).hexdigest()
    result = {**copy.deepcopy(original), 'run_id': run_id, 'mode': 'inspect_existing_answer',
        'timestamp': started.isoformat(), 'original_answer_timestamp': original.get('original_answer_timestamp') or original.get('timestamp'),
        'original_result_path': str(original_path), 'original_result_sha256': original_sha,
        'original_response_sha256': answer_sha, 'question_submission_attempted': False,
        'question_submitted': False, 'answer_completed': False, 'collection_status': 'need_action',
        'failure_reason': None, 'screenshots': [], 'citations': [], 'displayed_sources': [],
        'visible_source_count': None, 'visible_citation_count': None, 'ui_citation_count': None,
        'source_extraction_status': 'not_attempted', 'source_absence_verified': False,
        'inspection_provenance': {'kind': 'later_read_only_sources_not_new_answer', 'question_sent': False,
            'question_sha256': hashlib.sha256(config.question.encode('utf-8')).hexdigest(),
            'navigation_urls': [original['conversation_url']], 'original_result_unchanged': None}}
    result.pop('failure_stage', None)
    result.pop('finished_at', None)
    (run_dir / 'selectors.json').write_text(json.dumps(asdict(config.selectors), ensure_ascii=False, indent=2), encoding='utf-8')
    invalid_navigation = False
    original_answer_snapshot, original_snapshot_sha = None, None
    snapshot_evidence = next((item for item in original.get('evidence_manifest', [])
                              if isinstance(item, dict) and item.get('path') == 'answer.json'), None)
    if snapshot_evidence:
        try:
            snapshot_bytes = (original_path.parent / 'answer.json').read_bytes()
            original_snapshot_sha = hashlib.sha256(snapshot_bytes).hexdigest()
            if original_snapshot_sha == snapshot_evidence.get('sha256'):
                original_answer_snapshot = json.loads(snapshot_bytes.decode('utf-8'))
        except (OSError, ValueError, UnicodeError):
            pass

    def navigated(frame):
        nonlocal invalid_navigation
        if frame == page.main_frame:
            result['inspection_provenance']['navigation_urls'].append(frame.url)
            if SubmissionReceipt._route(frame.url) != expected_route:
                invalid_navigation = True

    def verify(snapshot):
        if invalid_navigation or SubmissionReceipt._route(snapshot.get('page_url')) != expected_route:
            raise ValueError('inspection_conversation_changed')
        if (_blocked_reason(snapshot) or snapshot.get('question_verified') is not True
                or snapshot.get('user_message_count') != 1 or snapshot.get('assistant_count', 0) < 1
                or snapshot.get('completion_marker') is not True or snapshot.get('busy')):
            raise ValueError('inspection_question_or_completion_not_verified')
        current_answer_sha = hashlib.sha256((snapshot.get('answer') or '').encode('utf-8')).hexdigest()
        if current_answer_sha == answer_sha:
            identity = {'answer_identity_method': 'exact_complete_response',
                        'current_answer_sha256': current_answer_sha, 'omitted_progress_body_count': 0}
        else:
            identity = (prove_progress_body_elision(original['response'], original_answer_snapshot, snapshot)
                        if original_answer_snapshot else None)
            if not identity:
                raise ValueError('inspection_answer_changed')
            identity['original_answer_snapshot_sha256'] = original_snapshot_sha
        result['inspection_provenance'].update(identity)
        expected_mode = (original.get('environment') or {}).get('client_mode') or config.client_mode
        if snapshot.get('client_mode') != expected_mode or config.client_mode != expected_mode:
            raise ValueError('inspection_client_mode_changed')

    page.on('framenavigated', navigated)
    stage = 'inspection_identity'
    try:
        verify(await read_snapshot(page, config))
        await _save_checkpoint(page, config, run_dir, result, 'inspection-before')
        verify(await read_snapshot(page, config))
        if progress:
            progress({'event': 'answer_completed', 'state': 'extracting_sources', 'question_submitted': True})
        stage = 'inspection_sources'
        items, screenshots, incomplete = await _expand_sources(page, config, run_dir, progress=progress)
        result['screenshots'].extend(screenshots)
        verify(await read_snapshot(page, config))
        await _save_checkpoint(page, config, run_dir, result, 'inspection-after')
        verify(await read_snapshot(page, config))
        if hashlib.sha256(original_path.read_bytes()).hexdigest() != original_sha:
            raise ValueError('inspection_original_result_changed')
        result['inspection_provenance']['original_result_unchanged'] = True
        result.update(question_submitted=True, answer_completed=True,
            citations=normalize_ui_sources([item for item in items if item['scope'] == 'inline_citation']),
            displayed_sources=normalize_ui_sources([item for item in items if item['scope'] == 'source_panel']),
            source_extraction_status='incomplete' if incomplete else ('captured' if items else 'none_detected'),
            collection_status='partial' if incomplete else 'success',
            failure_reason='source_ui_not_resolved' if incomplete else None)
        result['visible_source_count'] = len(result['displayed_sources'])
        result['visible_citation_count'] = len(result['citations'])
        result['ui_citation_count'] = sum(item['evidence_type'] == 'ui_citation' for item in result['citations'])
    except asyncio.CancelledError:
        result.update(failure_reason='source_reinspection_cancelled', failure_stage=stage,
                      finished_at=datetime.now(HK_TIME).isoformat())
        write_evidence_result(run_dir, result)
        raise
    except Exception as exc:
        reason = str(exc) if isinstance(exc, ValueError) and str(exc).startswith('inspection_') else _browser_error_reason(exc)
        result.update(failure_reason=reason, failure_stage=stage)
    finally:
        page.remove_listener('framenavigated', navigated)
    result['finished_at'] = datetime.now(HK_TIME).isoformat()
    return result, write_evidence_result(run_dir, result)


async def collect_doubao_sources(config: CollectorConfig, *, playwright_factory: Callable | None = None,
                                progress: Callable | None = None) -> tuple[dict[str, Any], Path]:
    started = datetime.now(HK_TIME)
    run_id = f"{started:%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:8]}"
    run_dir = config.output_root.resolve() / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "selectors.json").write_text(json.dumps(asdict(config.selectors), ensure_ascii=False, indent=2), encoding="utf-8")
    result: dict[str, Any] = {
        "schema_version": "1.1", "run_id": run_id, "platform": "doubao", "client": "web",
        "question": config.question, "timestamp": started.isoformat(), "response": "",
        "question_submission_attempted": False, "question_submitted": False,
        "answer_completed": False, "citations": [], "visible_citation_count": None,
        "ui_citation_count": None,
        "displayed_sources": [], "visible_source_count": None,
        "source_scope": "client_displayed", "retrieval_results": None, "api_citations": None,
        "source_extraction_status": "not_attempted", "screenshots": [],
        "source_absence_verified": False,
        "collection_status": "failed", "failure_reason": None,
        "manual_verification": {"wait_budget_seconds": config.manual_wait_seconds, "waited_seconds": 0, "events": []},
        "environment": {"locale": "zh-CN", "timezone": "Asia/Hong_Kong", "model_version": None,
                        "session": "dedicated_persistent_profile", "new_conversation": False},
    }
    page = None
    context = None
    stage = "browser_start"
    try:
        from playwright.async_api import async_playwright

        async with (playwright_factory or async_playwright)() as playwright:
            config.profile_dir.resolve().mkdir(parents=True, exist_ok=True)
            options: dict[str, Any] = {"headless": not config.headed, "viewport": {"width": 1440, "height": 1000},
                                      "locale": "zh-CN", "timezone_id": "Asia/Hong_Kong"}
            if config.executable_path:
                options["executable_path"] = config.executable_path
            context = await launch_context(playwright, config.profile_dir.resolve(), options, config.fallback_executable_path)
            try:
                page = await context.new_page()
                stage = "navigation"
                await _navigate_to_doubao(page, config.navigation_timeout_seconds)
                initial = await read_snapshot(page, config)
                initial = await _wait_for_manual_verification(page, config, run_dir, result, initial, progress=progress)
                if _blocked_reason(initial):
                    result.update(collection_status='need_action', failure_reason=_blocked_reason(initial),
                                  finished_at=datetime.now(HK_TIME).isoformat())
                    await _save_checkpoint(page, config, run_dir, result, 'before')
                    return result, write_evidence_result(run_dir, result)
                await page.locator(config.selectors.editor).first.wait_for(state="visible", timeout=15000)
                stage = "new_conversation"
                new_label = r"^新工作任务(?:\s|$)" if config.client_mode == "work" else r"^新对话(?:\s|$)"
                new_chat = await _first_visible(page.get_by_role("button", name=re.compile(new_label)))
                if new_chat is not None:
                    await new_chat.click()
                    result["environment"]["new_conversation"] = True
                await page.locator(config.selectors.editor).first.wait_for(state="visible", timeout=15000)
                baseline = await read_snapshot(page, config)
                baseline = await _wait_for_manual_verification(page, config, run_dir, result, baseline, progress=progress)
                if baseline["assistant_count"]:
                    result["failure_reason"] = "fresh_conversation_not_available"
                    await _save_checkpoint(page, config, run_dir, result, "before")
                    result["finished_at"] = datetime.now(HK_TIME).isoformat()
                    return result, write_evidence_result(run_dir, result)
                result["environment"].update({"model_label": baseline["model_label"], "login_state": baseline["login_state"],
                                              "client_mode": baseline["client_mode"], "requested_client_mode": config.client_mode,
                                              "browser_version": context.browser.version if context.browser else None,
                                              "browser_user_agent": await page.evaluate("navigator.userAgent")})
                await _save_checkpoint(page, config, run_dir, result, "before")
                if _blocked_reason(baseline):
                    result.update(collection_status="need_action", failure_reason=_blocked_reason(baseline))
                    result["finished_at"] = datetime.now(HK_TIME).isoformat()
                    return result, write_evidence_result(run_dir, result)
                if baseline["client_mode"] != config.client_mode:
                    result.update(collection_status="need_action", failure_reason="client_mode_mismatch")
                    result["finished_at"] = datetime.now(HK_TIME).isoformat()
                    return result, write_evidence_result(run_dir, result)
                if config.requested_model_label:
                    stage = "model_selection"
                    requested_label = re.sub(r"\s+", "", config.requested_model_label)
                    if requested_label not in re.sub(r"\s+", "", baseline.get("model_label") or ""):
                        await page.locator(config.selectors.model_label).first.click(timeout=5000)
                        choices = page.get_by_role("menuitem").filter(has=page.get_by_text(config.requested_model_label, exact=True))
                        if await choices.count() != 1:
                            raise ValueError("model_option_not_uniquely_available")
                        choice = choices.first
                        if await choice.get_attribute("data-disabled") is not None or await choice.get_attribute("aria-disabled") == "true" or "升级" in await choice.inner_text():
                            raise ValueError("model_option_requires_upgrade_or_disabled")
                        await choice.click(timeout=5000)
                        await page.wait_for_timeout(300)
                    # Re-read even when the already-visible model matched;
                    # never infer an unknown label or trust a stale header.
                    baseline = await read_snapshot(page, config)
                    if requested_label not in re.sub(r"\s+", "", baseline.get("model_label") or ""):
                        raise ValueError("model_selection_not_confirmed")
                    result["environment"].update(model_label=baseline["model_label"], requested_model_label=config.requested_model_label)
                    await _save_checkpoint(page, config, run_dir, result, "model-selected")
                editor = await _first_visible(page.locator(config.selectors.editor))
                if editor is None:
                    raise ValueError("editor_not_found")
                sent_text = submission_text(config.question, config.submission_format)
                result['submission_transport'] = {'format': config.submission_format,
                    'logical_question_sha256': hashlib.sha256(config.question.encode('utf-8')).hexdigest(),
                    'submitted_text_sha256': hashlib.sha256(sent_text.encode('utf-8')).hexdigest()}
                await editor.fill(sent_text)
                stage = "question_evidence"
                await _save_checkpoint(page, config, run_dir, result, "question")
                stage = 'editor_readback'
                editor_proof = await editor.evaluate(EDITOR_READBACK_JS, sent_text)
                readback = editor_proof.get('readback')
                (run_dir / 'editor-readback.json').write_text(json.dumps({
                    'submission_format': config.submission_format, 'expected_question_sha256': hashlib.sha256(config.question.encode('utf-8')).hexdigest(),
                    'expected_submission_sha256': hashlib.sha256(sent_text.encode('utf-8')).hexdigest(),
                    'editor_readback': readback,
                    'editor_readback_sha256': hashlib.sha256(readback.encode('utf-8')).hexdigest() if isinstance(readback, str) else None,
                    'editor_readback_method': editor_proof['method'],
                    'editor_dom': editor_proof['editor_dom'],
                    'editor_inner_text': editor_proof['inner_text'], 'editor_text_content': editor_proof['text_content'],
                    'verified': readback == sent_text}, ensure_ascii=False, indent=2), encoding='utf-8')
                if readback != sent_text:
                    raise ValueError('editor_readback_mismatch_not_submitted')
                if config.submission_format == 'verbatim_fenced' and baseline.get('user_message_count') != 0:
                    raise ValueError('submission_fresh_user_context_not_confirmed')
                result["question_submission_attempted"] = True
                submission = SubmissionReceipt(page, config.question, baseline['assistant_count'],
                    submitted_text=sent_text, editor_readback=readback, submission_format=config.submission_format,
                    baseline_user_count=baseline.get('user_message_count'))
                submission.attempted()
                stage = "submit_question"
                await editor.press("Enter")  # exactly one submission, never an automatic retry
                if progress:
                    progress({'event': 'question_sent', 'state': 'waiting_for_answer',
                              'question_submission_attempted': True, 'question_submitted': False})
                tracker = CompletionTracker(baseline["assistant_count"], config.stable_seconds)
                deadline = time.monotonic() + config.timeout_seconds
                last = baseline
                stage = "wait_for_answer"
                while time.monotonic() < deadline:
                    last = await read_snapshot(page, config)
                    # Save literal DOM confirmation before a human verification
                    # can replace the local conversation and hide its user bubble.
                    verified = _capture_submission(page, config, run_dir, result, last, submission)
                    reason = _blocked_reason(last)
                    if reason:
                        paused_at = time.monotonic()
                        last = await _wait_for_manual_verification(page, config, run_dir, result, last,
                                                                  progress=progress, submission=submission)
                        deadline += time.monotonic() - paused_at
                        reason = _blocked_reason(last)
                        verified = _capture_submission(page, config, run_dir, result, last, submission)
                    if last["assistant_count"] > baseline["assistant_count"]:
                        result["response"] = last["answer"]
                    if reason:
                        result.update(collection_status="failed" if reason == "service_capacity_unavailable" else "need_action", failure_reason=reason)
                        break
                    if verified and is_deferred_answer(last.get('answer')):
                        submission.observed_deferred(last, verified=verified)
                        result['submission_receipt'] = submission.evidence()
                        if 'deferred_answer' not in result:
                            result['deferred_answer'] = {'observed': True, 'pending': True,
                                'observed_at': datetime.now(HK_TIME).isoformat(),
                                'notice_sha256': hashlib.sha256(last['answer'].encode('utf-8')).hexdigest()}
                            await _save_checkpoint(page, config, run_dir, result, 'deferred-notice')
                            # Allow a full bounded answer budget from the notice;
                            # repeated placeholders never keep extending it.
                            deadline = max(deadline, time.monotonic() + config.timeout_seconds)
                            if progress:
                                progress({'event': 'background_task_waiting', 'state': 'waiting_for_final_answer',
                                          'question_submitted': result['question_submitted']})
                    if tracker.observe(last, time.monotonic(), submission_verified=verified):
                        result["answer_completed"] = True
                        if result.get('deferred_answer'):
                            result['deferred_answer'].update(pending=False, resolved_at=datetime.now(HK_TIME).isoformat())
                            if progress:
                                progress({'event': 'background_task_cleared', 'state': 'final_answer_received',
                                          'question_submitted': result['question_submitted']})
                        break
                    await asyncio.sleep(0.5)
                await _save_checkpoint(page, config, run_dir, result, "answer")
                result["conversation_url"] = last.get("page_url")
                result["source_ui_summaries"] = last.get("source_ui_summaries", [])
                result["environment"]["model_label"] = last.get("model_label") or result["environment"].get("model_label")
                if result["answer_completed"]:
                    stage = "extract_sources"
                    if progress:
                        progress({'event': 'answer_completed', 'state': 'extracting_sources',
                                  'question_submitted': result['question_submitted']})
                    items, screenshots, incomplete = await _expand_sources(page, config, run_dir, progress=progress)
                    result["screenshots"].extend(screenshots)
                    result["citations"] = normalize_ui_sources([item for item in items if item["scope"] == "inline_citation"])
                    result["displayed_sources"] = normalize_ui_sources([item for item in items if item["scope"] == "source_panel"])
                    result["visible_source_count"] = len(result["displayed_sources"])
                    result["visible_citation_count"] = len(result["citations"])
                    result["ui_citation_count"] = sum(item["evidence_type"] == "ui_citation" for item in result["citations"])
                    result["source_extraction_status"] = "incomplete" if incomplete else ("captured" if items else "none_detected")
                    result["collection_status"] = "partial" if incomplete else "success"
                    result["failure_reason"] = "source_ui_not_resolved" if incomplete else None
                elif not result["failure_reason"]:
                    if result.get('deferred_answer', {}).get('pending'):
                        result.update(collection_status='need_action', failure_reason='background_task_timeout')
                    else:
                        result["failure_reason"] = "answer_timeout_or_selectors_changed"
            except Exception as exc:
                # Browser errors can contain profile paths and other session data.
                result["failure_reason"] = _browser_error_reason(exc)
                result["failure_stage"] = stage
                if isinstance(exc, ManualWindowClosedError) or (isinstance(exc, ValueError) and str(exc).startswith(('submission_', 'editor_'))):
                    result['collection_status'] = 'need_action'
                if page is not None and not page.is_closed():
                    try:
                        await _save_checkpoint(page, config, run_dir, result, "failure")
                        reason = _blocked_reason(await read_snapshot(page, config))
                        if reason:
                            result.update(collection_status="need_action", failure_reason=reason)
                    except Exception:
                        pass
            finally:
                try:
                    await context.close()
                except Exception as exc:
                    if 'Target page, context or browser has been closed' not in str(exc):
                        raise
    except asyncio.CancelledError:
        result.update(collection_status='need_action', failure_reason='user_cancelled_submission_unknown',
                      finished_at=datetime.now(HK_TIME).isoformat())
        write_evidence_result(run_dir, result)
        raise
    except ImportError as exc:
        missing = getattr(exc, "name", None)
        result["failure_reason"] = ("playwright_not_installed" if missing in {"playwright", "playwright.async_api"}
                                    else f"browser_runtime_dependency_missing:{missing or 'unknown'}")
    except Exception as exc:
        result["failure_reason"] = f"browser_start_or_storage_error:{type(exc).__name__}"
    result["finished_at"] = datetime.now(HK_TIME).isoformat()
    return result, write_evidence_result(run_dir, result)


def _blocked_reason(snapshot: dict[str, Any]) -> str | None:
    if snapshot.get("challenge"):
        return "human_verification_required"
    if snapshot.get("login_required"):
        return "login_required"
    if snapshot.get("session_reset"):
        return "session_redirected_to_logout"
    if snapshot.get("rate_limited"):
        return "rate_limited"
    if snapshot.get("service_unavailable"):
        return "service_capacity_unavailable"
    return None


async def _save_checkpoint(page: Any, config: CollectorConfig, run_dir: Path, result: dict[str, Any], name: str) -> None:
    filename = f"{name}.png"
    await page.screenshot(path=str(run_dir / filename), full_page=True)
    result["screenshots"].append(filename)
    snapshot = await read_snapshot(page, config)
    (run_dir / f"{name}.json").write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")


async def _wait_for_manual_verification(page: Any, config: CollectorConfig, run_dir: Path,
                                        result: dict[str, Any], snapshot: dict[str, Any], *, progress=None,
                                        submission=None) -> dict[str, Any]:
    """Keep the current page open for the user; never solve or resubmit anything."""
    reason = _blocked_reason(snapshot)
    record = result["manual_verification"]
    remaining = None if config.manual_wait_seconds is None else config.manual_wait_seconds - record["waited_seconds"]
    if reason not in {"human_verification_required", "login_required"} or (remaining is not None and remaining <= 0):
        return snapshot
    event = {"reason": reason, "started_at": datetime.now(HK_TIME).isoformat(), "cleared": False}
    record["events"].append(event)
    if submission:
        submission.manual_action_seen = True
    if progress:
        progress({'event': 'manual_action_required', 'state': 'waiting_for_user', 'reason': reason,
                  'question_submission_attempted': result['question_submission_attempted'],
                  'question_submitted': result['question_submitted']})
    started = time.monotonic()
    try:
        await _save_checkpoint(page, config, run_dir, result, f"manual-action-{len(record['events'])}")
        print(f"等待人工处理（{reason}），请在当前浏览器完成正常验证/登录；保持当前提问，不需要重新发送。", flush=True)
        while remaining is None or time.monotonic() - started < remaining:
            if page.is_closed():
                raise ManualWindowClosedError('正常验证窗口已关闭，提交状态仍需检查；不会自动重发')
            await asyncio.sleep(0.5)
            if page.is_closed():
                raise ManualWindowClosedError('正常验证窗口已关闭，提交状态仍需检查；不会自动重发')
            snapshot = await read_snapshot(page, config)
            if submission:
                _capture_submission(page, config, run_dir, result, snapshot, submission)
            if _blocked_reason(snapshot) not in {"human_verification_required", "login_required"}:
                break
        event["cleared"] = _blocked_reason(snapshot) is None
        event['exit_reason'] = 'cleared' if event['cleared'] else 'wait_budget_exhausted' if _blocked_reason(snapshot) in {'human_verification_required', 'login_required'} else 'different_blocker'
        if progress:
            progress({'event': 'manual_action_cleared' if event['cleared'] else 'manual_action_ended',
                      'state': 'waiting_for_answer' if event['cleared'] else 'need_action', 'reason': reason,
                      'question_submission_attempted': result['question_submission_attempted'],
                      'question_submitted': result['question_submitted']})
        return snapshot
    except asyncio.CancelledError:
        event['exit_reason'] = 'cancelled'
        raise
    except Exception:
        event['exit_reason'] = 'window_closed' if page.is_closed() else 'page_unavailable'
        if progress:
            progress({'event': 'manual_action_ended', 'state': 'need_action', 'reason': reason,
                      'question_submission_attempted': result['question_submission_attempted'],
                      'question_submitted': result['question_submitted']})
        if page.is_closed():
            raise ManualWindowClosedError('正常验证窗口已关闭，提交状态仍需检查；不会自动重发') from None
        raise
    finally:
        elapsed = time.monotonic() - started
        record["waited_seconds"] += elapsed
        event["waited_seconds"] = elapsed
        event["finished_at"] = datetime.now(HK_TIME).isoformat()


async def open_doubao_session(config: CollectorConfig, wait_seconds: float | None = 300, *, playwright_factory: Callable | None = None) -> None:
    """Open a dedicated normal browser for the user to log in/verify manually."""
    from playwright.async_api import async_playwright

    async with (playwright_factory or async_playwright)() as playwright:
        config.profile_dir.resolve().mkdir(parents=True, exist_ok=True)
        options: dict[str, Any] = {"headless": False, "locale": "zh-CN", "timezone_id": "Asia/Hong_Kong"}
        if config.executable_path:
            options["executable_path"] = config.executable_path
        context = await launch_context(playwright, config.profile_dir.resolve(), options, config.fallback_executable_path)
        try:
            page = context.pages[0] if context.pages else await context.new_page()
            try:
                await page.goto(DOUBAO_URL, wait_until="domcontentloaded", timeout=config.navigation_timeout_seconds * 1000)
            except Exception as exc:
                if page.is_closed():
                    return
                # The user may navigate during startup; a cancelled navigation
                # must not close their login window or discard the session.
                error_match = re.search(r"net::[A-Z_]+", str(exc))
                reason = error_match.group(0) if error_match else type(exc).__name__
                print(f"页面跳转未完成（{reason}）；浏览器保持打开，可正常刷新或登录。", flush=True)
            deadline = time.monotonic() + wait_seconds if wait_seconds is not None else None
            while (deadline is None or time.monotonic() < deadline) and not page.is_closed():
                await asyncio.sleep(0.5)
        finally:
            try:
                await context.close()
            except Exception as exc:
                if "Target page, context or browser has been closed" not in str(exc):
                    raise
