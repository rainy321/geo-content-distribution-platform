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
import unicodedata
import uuid
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from services.browser_runtime import launch_context, context_profile_scope
from services.doubao_account_identity import AccountObservedResult, bind_probe_handle
from services.doubao_account_observer import observe_official_visible_handle, AccountObservationSafetyError
from services.doubao_answer_state import is_deferred_answer
from services.doubao_answer_identity import prove_progress_body_elision
from services.doubao_submission_transport import EDITOR_READBACK_JS, KNOWN_DOWNLOAD_PROMOTION_JS, LITERAL_PREAMBLE, submission_fence, submission_text


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
    # Observed on the live work composer 2026-10-25; icon/aria text is absent.
    send_button: str = 'button[data-testid="chat_input_send_button"]'


def validate_session_slot(slot, client_mode, profile_mode=None):
    """Closed local session scope; a separate directory is not account identity proof."""
    if not isinstance(slot, str) or slot not in ('primary', 'secondary'):
        raise ValueError('豆包会话只能选择 primary 或 secondary')
    if slot == 'secondary' and (client_mode != 'consumer' or profile_mode not in (None, 'consumer')):
        raise ValueError('会话 2 只用于普通豆包对话，工作会话只支持会话 1')
    return slot


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
    strict_client_mode: bool = False
    submission_format: str = 'plain'
    selectors: DoubaoSelectors = DoubaoSelectors()
    session_slot: str = 'primary'
    # Set by the native single-probe job, never by frontend/import options.
    account_probe_id: str | None = None

    def __post_init__(self) -> None:
        if type(self.strict_client_mode) is not bool:
            raise ValueError('strict_client_mode 必须是布尔值')
        if self.client_mode not in {"consumer", "work"}:
            raise ValueError("client_mode 必须是 consumer 或 work")
        validate_session_slot(self.session_slot, self.client_mode)
        if self.account_probe_id is not None and (not isinstance(self.account_probe_id, str)
                or not re.fullmatch('[a-f0-9]{32}', self.account_probe_id) or self.client_mode != 'consumer'):
            raise ValueError('账号观察只能绑定普通豆包的本次原生单题')
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


MANUAL_AUTH_REASONS = frozenset(('human_verification_required', 'login_required', 'session_redirected_to_logout'))


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
        self.stale_local_snapshot_count = 0
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

    def _stale_local_snapshot(self, url):
        if not self.navigation_tracking or self.invalid_reason or len(self.navigation_urls) != 2:
            return False
        local_url, canonical_url = self.navigation_urls
        if url != local_url or self.page.main_frame.url != canonical_url:
            return False
        local, canonical = self._route(local_url), self._route(canonical_url)
        return bool(local and canonical and local[0] == 'local' and canonical[0] == 'canonical')

    def observe(self, page, question, snapshot):
        if page is not self.page or question != self.question or self.attempts != 1:
            self.invalid_reason = 'request_identity_changed'
            return False, None
        url = snapshot.get('page_url')
        user_count = snapshot.get('user_message_count')
        multiple_users = isinstance(user_count, int) and user_count > 1
        literal = snapshot.get('question_verified') is True and self.submission_format == 'plain'
        fenced = (snapshot.get('verbatim_question_verified') is True and self.submission_format == 'verbatim_fenced'
                  and self.editor_readback_verified and self.baseline_count == 0 and self.baseline_user_count == 0
                  and user_count == 1)
        if self.observed_url:
            # The awaited DOM result can arrive after the main frame has moved
            # to its canonical URL. Ignore only an empty or still-exact old DOM.
            if (user_count == 0 or user_count == 1 and (literal or fenced)) and self._stale_local_snapshot(url):
                self.stale_local_snapshot_count += 1
                return False, None
            self._navigate(url)
        if self.invalid_reason:
            return False, None
        if multiple_users:
            self.invalid_reason = 'multiple_user_messages'
            return False, None
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
                'stale_local_snapshot_count': self.stale_local_snapshot_count,
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


_VISIBLE_CLIENT_MODE_JS = r"""() => {
    const visible = e => !!e && e.getClientRects().length > 0
        && getComputedStyle(e).visibility !== 'hidden' && getComputedStyle(e).display !== 'none';
    const controls = selector => [...document.querySelectorAll(selector)].filter(visible);
    const label = e => (e.textContent || '').replace(/\s+/g, '');
    const chipModes = controls('[data-testid="workspace-switch-chip"]').map(chip => {
        const value = label(chip);
        if (/^(?:豆包)?工作$/.test(value)) return 'work';
        if (value === '豆包对话') return 'consumer';
        const disabled = chip.disabled === true
            || (chip.getAttribute('aria-disabled') || '').trim().toLowerCase() === 'true';
        // The disabled plain brand appears in both new chat and work shells.
        return value === '豆包' && !disabled ? 'consumer' : null;
    }).filter(Boolean);
    const knownChipModes = [...new Set(chipModes)];
    if (knownChipModes.length > 1) return null;
    const chipMode = knownChipModes[0] || null;
    const composer = controls('[data-testid="chat_input_action_mode"]');
    if (composer.length) {
        const labels = composer.map(label);
        // Observed work UI uses this same testid for its cloud-computer tool.
        // That tool is not mode evidence without an explicit work chip.
        if (labels.every(value => value === '云电脑')) return chipMode === 'work' ? 'work' : null;
        const modes = labels.map(value => value === '对话' ? 'consumer' : value === '工作' ? 'work' : null);
        // A visible unknown/empty composer label cannot fall back to a chip.
        if (modes.some(mode => !mode) || new Set(modes).size !== 1) return null;
        return chipMode && chipMode !== modes[0] ? null : modes[0];
    }
    // Legacy explicit chips remain compatible; titles/body/sidebar styles do not identify a mode.
    return chipMode;
}"""


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
    // from_logout is historical URL evidence, not an authentication verdict.
    // A visible input alone is insufficient: require the normal chat composer
    // and visible model control on the expected chat origin and route.
    const modelControl = all(document, s.model_label)[0];
    const chatInterfaceReady = location.protocol === 'https:' && location.port === ''
        && ['doubao.com', 'www.doubao.com'].includes(location.hostname)
        && /^\/chat(?:\/|$)/.test(location.pathname)
        && !!modelControl && !!text(modelControl)
        && all(document, s.editor).some(e => e.isContentEditable || (e.tagName === 'TEXTAREA' && !e.disabled && !e.readOnly));
    // This is the site's whole-page technical-error screen, not a quoted
    // phrase in a user question or an assistant answer. Keep it separate from
    // the work client's capacity notice so recovery can explain what failed.
    const websiteUnavailable = !chatInterfaceReady
        && all(document, 'h1,h2,h3,p,div,span,[role="alert"]').some(e =>
            !e.closest(s.assistant_message) && !e.closest(s.user_message)
            && /^该页面暂时不可用[。！!]?\s*$/.test(text(e)))
        && /这可能是一个技术错误|请尝试刷新此页面/.test(document.body.innerText);
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
        chat_interface_ready: chatInterfaceReady,
        rate_limited: /请求过于频繁|操作频繁，请|访问过于频繁/.test(document.body.innerText),
        service_unavailable: /^算力紧张，办公能力得晚点才能用了/.test(answer.trim()),
        website_temporarily_unavailable: websiteUnavailable,
        login_state: all(document, '[data-testid="to_login_button"]').length ? 'guest' : 'unknown',
        client_mode: (__VISIBLE_CLIENT_MODE__)(),
        model_label: text(all(document, s.model_label)[0]) || null,
        source_trigger_count: triggerCount, source_panel_count: panels.length,
        source_ui_summaries: answerRoot ? all(answerRoot, '[data-plugin-identifier*="search_query_result_block"] [data-copy-ignore]').map(text) : [],
        sources, answer_dom: bodies.length ? bodies.map(e => e.outerHTML).join('\n') : null,
        source_panel_dom: panels.map(e => e.outerHTML)
    };
}""".replace('__VISIBLE_CLIENT_MODE__', _VISIBLE_CLIENT_MODE_JS)


async def read_snapshot(page: Any, config: CollectorConfig, *, allow_external_panels: bool = False) -> dict[str, Any]:
    payload = {"s": asdict(config.selectors), "question": config.question, "allowExternalPanels": allow_external_panels,
               'submissionFormat': config.submission_format, 'literalPreamble': LITERAL_PREAMBLE,
               'literalFence': submission_fence(config.question) if config.submission_format == 'verbatim_fenced' else None}
    for attempt in range(3):
        try:
            value = await page.evaluate(SNAPSHOT_JS, payload)
            if config.strict_client_mode:
                value['client_mode'] = (await _read_login_observation(page, config))['client_mode']
            return value
        except Exception as exc:
            if attempt == 2 or not any(reason in str(exc) for reason in ("Execution context was destroyed", "Cannot find context")):
                raise
            await asyncio.sleep(0.25)  # navigation recovery only; never re-submit
    raise RuntimeError("snapshot_unavailable")


async def _settle_pre_submission_mode(page: Any, config: CollectorConfig, initial: dict[str, Any]) -> dict[str, Any]:
    """Wait briefly for existing mode controls to hydrate, without clicking.

    The current homepage renders its editor before its composer mode control.
    A missing control is unknown, not the wrong account. Require two consecutive
    explicit observations of the requested mode; a known other mode or an auth
    barrier stops immediately. This helper runs only before filling/sending.
    """
    deadline = asyncio.get_running_loop().time() + 3
    snapshot, consecutive = initial, 0
    for attempt in range(13):
        observed = snapshot.get('client_mode')
        if _blocked_reason(snapshot) or observed in ('consumer', 'work') and observed != config.client_mode:
            return snapshot
        consecutive = consecutive + 1 if observed == config.client_mode else 0
        if consecutive >= 2:
            return snapshot
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0 or attempt == 12:
            break
        await asyncio.sleep(min(.25, remaining))
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            break
        try:
            snapshot = await asyncio.wait_for(read_snapshot(page, config), timeout=remaining)
        except asyncio.TimeoutError:
            break
    # A single late observation or continuously absent/conflicting controls do
    # not establish readiness. Preserve the latest diagnostics, but never send.
    return {**snapshot, 'client_mode': None}


async def _first_visible(locator: Any) -> Any:
    for index in range(await locator.count()):
        item = locator.nth(index)
        if await item.is_visible():
            return item
    return None


def _submission_context_view(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Keep only the controlled composer context, never account text or page DOM."""
    label = snapshot.get('model_label')
    url = snapshot.get('page_url')
    return {'page_url_sha256': hashlib.sha256(url.encode('utf-8')).hexdigest() if isinstance(url, str) else None,
            'client_mode': snapshot.get('client_mode') if snapshot.get('client_mode') in ('consumer', 'work') else None,
            'model_label': label if isinstance(label, str) and 0 < len(label.strip()) <= 100 else None,
            'chat_interface_ready': snapshot.get('chat_interface_ready') if type(snapshot.get('chat_interface_ready')) is bool else None,
            'user_message_count': snapshot.get('user_message_count'), 'assistant_count': snapshot.get('assistant_count'),
            'blocked_reason': _blocked_reason(snapshot)}


async def _settle_consumer_composer(page: Any, config: CollectorConfig,
                                     initial: dict[str, Any], run_dir: Path) -> dict[str, Any]:
    """Wait for a strict ordinary-web composer before filling, never after input.

    The editor/mode can hydrate before the model selector. The ordinary editor
    has already been observed visible by the caller. This gate belongs only to
    strict official web sessions; legacy custom collectors retain their unknown
    model contract. Known mode, conversation or model changes fail closed.
    """
    if config.client_mode != 'consumer' or config.strict_client_mode is not True:
        raise ValueError('submission_composer_scope_invalid_not_submitted')
    proof = {'observations': [], 'stable_complete_observations': 0, 'ready': False, 'stop_reason': None}
    path = run_dir / 'composer-readiness.json'
    def persist():
        path.write_text(json.dumps(proof, ensure_ascii=False, indent=2), encoding='utf-8')
    snapshot, stable, known_model = initial, 0, None
    deadline = asyncio.get_running_loop().time() + 3
    for attempt in range(13):
        view = _submission_context_view(snapshot)
        proof['observations'].append(view)
        if view['blocked_reason'] or snapshot.get('client_mode') == 'work':
            proof['stop_reason'] = view['blocked_reason'] or 'client_mode_mismatch'
            persist()
            return snapshot
        if any(snapshot.get(key) != initial.get(key) for key in ('page_url', 'user_message_count', 'assistant_count')):
            proof['stop_reason'] = 'conversation_changed'
            persist()
            raise ValueError('submission_context_changed_not_submitted')
        label = view['model_label']
        if label is not None:
            if known_model is not None and label != known_model:
                proof['stop_reason'] = 'known_model_changed'
                persist()
                raise ValueError('submission_context_changed_not_submitted')
            known_model = label
        complete = (snapshot.get('client_mode') == 'consumer' and label is not None
                    and snapshot.get('chat_interface_ready') is True)
        stable = stable + 1 if complete else 0
        proof['stable_complete_observations'] = stable
        if stable >= 2:
            proof['ready'] = True
            persist()
            return snapshot
        persist()
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0 or attempt == 12:
            break
        await asyncio.sleep(min(.25, remaining))
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            break
        try:
            snapshot = await asyncio.wait_for(read_snapshot(page, config), timeout=remaining)
        except asyncio.TimeoutError:
            break
    proof['stop_reason'] = 'readiness_timeout'
    persist()
    raise ValueError('submission_composer_not_ready_not_submitted')


async def _fenced_editor_settle(editor: Any, sent_text: str, run_dir: Path) -> dict[str, Any]:
    """Allow the observed work editor to settle without editing or submitting again.

    A correct DOM readback can precede the composer's own input processing.
    Two exact observations a second apart are a bounded precaution, not proof
    of app readiness or delivery; the normal user-message receipt is still required.
    """
    started = time.monotonic()
    before = await editor.evaluate(EDITOR_READBACK_JS, sent_text)
    first = before.get('readback')
    proof = {'minimum_wait_seconds': 1.0, 'observations': 1,
             'expected_submission_sha256': hashlib.sha256(sent_text.encode('utf-8')).hexdigest(),
             'before_sha256': hashlib.sha256(first.encode('utf-8')).hexdigest() if isinstance(first, str) else None,
             'matched_before': first == sent_text, 'matched_after': None,
             'unchanged_between_observations': None, 'waited_seconds': 0.0}
    if first == sent_text:
        await asyncio.sleep(1.0)
        after = await editor.evaluate(EDITOR_READBACK_JS, sent_text)
        second = after.get('readback')
        proof.update(observations=2,
            after_sha256=hashlib.sha256(second.encode('utf-8')).hexdigest() if isinstance(second, str) else None,
            matched_after=second == sent_text, unchanged_between_observations=second == first,
            waited_seconds=time.monotonic() - started)
    (run_dir / 'editor-settle.json').write_text(json.dumps(proof, ensure_ascii=False, indent=2), encoding='utf-8')
    return proof


async def _fenced_send_control(page: Any, config: CollectorConfig, run_dir: Path, *, allow_missing: bool = False) -> Any:
    """Bind one visible, ready send button before recording any send attempt."""
    locator = page.locator(config.selectors.send_button)
    matched = await locator.count()
    handles = []
    observations = []
    for index in range(matched):
        handle = await locator.nth(index).element_handle()
        if handle is None or not await handle.is_visible():
            continue
        evidence = await handle.evaluate('''e => ({tag_name:e.tagName, test_id:e.getAttribute('data-testid'),
            id:e.id || null, disabled:e.disabled === true, aria_disabled:e.getAttribute('aria-disabled'),
            data_disabled:e.getAttribute('data-disabled'), data_loading:e.getAttribute('data-loading')})''')
        evidence['enabled'] = await handle.is_enabled()
        handles.append(handle)
        observations.append(evidence)
    proof = {'selector': config.selectors.send_button, 'matched_count': matched,
             'visible_count': len(handles), 'controls': observations}
    (run_dir / 'submission-control.json').write_text(json.dumps(proof, ensure_ascii=False, indent=2), encoding='utf-8')
    if allow_missing and matched == 0:
        return None
    if len(handles) != 1 or (allow_missing and matched != 1):
        raise ValueError('submission_send_button_not_unique_not_submitted')
    control = observations[0]
    if (control.get('disabled') is True or control.get('enabled') is not True
            or any(str(control.get(name) or '').strip().casefold() == 'true'
                   for name in ('aria_disabled', 'data_disabled', 'data_loading'))):
        raise ValueError('submission_send_button_not_ready_not_submitted')
    # ElementHandle keeps this exact node bound. Replacement or detachment
    # after checkpoint must fail conservatively, never retarget another button.
    return handles[0]


async def _fenced_send_ready(page: Any, config: CollectorConfig, run_dir: Path, control: Any,
                             sent_text: str, baseline: dict[str, Any]) -> Any:
    """Prove actionability before the send checkpoint; dismiss at most one known ad.

    Trial clicks do not send. The eventual real click remains single-attempt,
    including when a new overlay appears after this check. Unknown overlays,
    authentication and changed input never cause a force click or Enter fallback.
    """
    proof = {'trial_checks': [], 'promotion_close_attempted': False, 'promotion_close_clicked': False, 'ready': False}
    path = run_dir / 'submission-actionability.json'

    def persist():
        path.write_text(json.dumps(proof, ensure_ascii=False, indent=2), encoding='utf-8')

    async def trial(handle):
        try:
            await handle.click(trial=True, timeout=1500)
        except Exception as error:
            proof['trial_checks'].append({'passed': False, 'error_type': type(error).__name__})
            persist()
            return False
        proof['trial_checks'].append({'passed': True})
        persist()
        return True

    if await trial(control):
        proof['ready'] = True
        persist()
        return control
    if proof['trial_checks'][-1]['error_type'] != 'TimeoutError':
        raise ValueError('submission_send_button_blocked_not_submitted')
    inspection = await page.evaluate_handle(KNOWN_DOWNLOAD_PROMOTION_JS, asdict(config.selectors))
    try:
        observed = await inspection.evaluate('value => ({...value, close_element:undefined})')
        proof['promotion'] = observed
        persist()
        if observed.get('can_close') is not True:
            raise ValueError('submission_send_button_blocked_not_submitted')
        close = (await inspection.get_property('close_element')).as_element()
        if close is None:
            raise ValueError('submission_send_button_blocked_not_submitted')
        proof['promotion_close_attempted'] = True
        persist()
        # The bound visible close node is clicked normally, once. Never retarget
        # another close control or retry it when its result is uncertain.
        try:
            await close.click(timeout=2000)
        except Exception as error:
            proof['promotion_close_error_type'] = type(error).__name__
            persist()
            raise ValueError('submission_promotion_close_unconfirmed_not_submitted') from error
        proof['promotion_close_clicked'] = True
        persist()
    finally:
        await inspection.dispose()
    current = await read_snapshot(page, config)
    context_guard = {'blocker_absent': _blocked_reason(current) is None,
        'client_mode_match': current.get('client_mode') == config.client_mode,
        'page_url_match': current.get('page_url') == baseline.get('page_url'),
        'user_message_count_match': current.get('user_message_count') == baseline.get('user_message_count'),
        'assistant_count_match': current.get('assistant_count') == baseline.get('assistant_count'),
        'model_label_match': current.get('model_label') == baseline.get('model_label')}
    if not all(context_guard.values()):
        proof['context_guard'] = {**context_guard, 'baseline': _submission_context_view(baseline),
                                  'current': _submission_context_view(current)}
        persist()
        raise ValueError('submission_context_changed_not_submitted')
    editor = await _first_visible(page.locator(config.selectors.editor))
    readback = await editor.evaluate(EDITOR_READBACK_JS, sent_text) if editor is not None else {}
    text = readback.get('readback')
    proof['editor_rechecked'] = text == sent_text
    proof['editor_readback_sha256'] = hashlib.sha256(text.encode('utf-8')).hexdigest() if isinstance(text, str) else None
    persist()
    if text != sent_text:
        raise ValueError('editor_readback_mismatch_not_submitted')
    if config.submission_format == 'plain':
        control = await _fenced_send_control(page, config, run_dir, allow_missing=True)
    else:
        control = await _fenced_send_control(page, config, run_dir)
    if control is None or not await trial(control):
        raise ValueError('submission_send_button_blocked_not_submitted')
    proof['ready'] = True
    persist()
    return control


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
    if isinstance(exc, _SourceContextChanged):
        return 'source_context_changed_not_captured'
    if isinstance(exc, ManualWindowClosedError):
        return 'manual_verification_window_closed'
    if isinstance(exc, ValueError) and str(exc).startswith("model_"):
        return str(exc)
    if isinstance(exc, ValueError) and str(exc).startswith(('submission_', 'editor_')):
        return str(exc)
    code = re.search(r"net::(ERR_[A-Z_]+)", str(exc))
    return f"network_error:{code.group(1)}" if code else f"browser_error:{type(exc).__name__}"


class _SourceContextChanged(ValueError):
    pass


@asynccontextmanager
async def _literal_source_readiness(page, config, expected, run_dir, submission, *, timeout_seconds=3):
    """Reobserve a missing user bubble, without promoting a delivery receipt to DOM proof."""
    route = SubmissionReceipt._route(expected.get('page_url'))
    routes = [SubmissionReceipt._route(value) for value in submission.navigation_urls]
    eligible = (submission.page is page and submission.question == config.question
        and submission.attempts == 1 and submission.invalid_reason is None
        and submission.baseline_count == 0 and submission.baseline_user_count == 0
        and submission.editor_readback_verified and submission.submission_format == 'plain'
        and submission.navigation_tracking and submission.manual_action_seen and submission.observed_url
        and len(routes) >= 2 and all(routes) and routes[0][0] == 'local'
        and route and route[0] == 'canonical' and all(value == route for value in routes[1:])
        and expected.get('question_verified') is False and expected.get('user_message_count') == 0)
    proof = {'schema_version': 1, 'kind': 'bounded_literal_source_readiness',
        'target_question_sha256': hashlib.sha256(config.question.encode('utf-8')).hexdigest(),
        'target_answer_sha256': hashlib.sha256((expected.get('answer') or '').encode('utf-8')).hexdigest(),
        'canonical_url_sha256': hashlib.sha256((expected.get('page_url') or '').encode('utf-8')).hexdigest(),
        'eligible_manual_fallback': bool(eligible), 'navigation_away_seen': False,
        'status': 'unverified', 'stop_reason': None, 'observations': []}

    def fail(reason):
        proof.update(status='unverified', stop_reason=reason)
        raise _SourceContextChanged('source_context_changed_not_captured')

    def navigated(frame):
        if frame == page.main_frame and SubmissionReceipt._route(frame.url) != route:
            proof['navigation_away_seen'] = True

    def check_navigation():
        if (proof['navigation_away_seen'] or submission.invalid_reason is not None
                or page.is_closed() or SubmissionReceipt._route(page.main_frame.url) != route):
            fail('main_page_navigation_or_closed')

    listening = False
    try:
        if (not eligible or not all(hasattr(page, name) for name in ('on', 'remove_listener', 'main_frame'))
                or not expected.get('answer') or expected.get('completion_marker') is not True
                or expected.get('busy') or _blocked_reason(expected)
                or expected.get('client_mode') != config.client_mode or not expected.get('model_label')
                or type(expected.get('assistant_count')) is not int or expected['assistant_count'] < 1):
            fail('manual_fallback_not_eligible')
        page.on('framenavigated', navigated)
        listening = True
        deadline, stable = time.monotonic() + timeout_seconds, 0
        while True:
            check_navigation()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                fail('literal_question_not_ready')
            try:
                current = await asyncio.wait_for(read_snapshot(page, config), timeout=remaining)
            except asyncio.TimeoutError:
                fail('literal_question_not_ready')
            except Exception:
                fail('source_readiness_unavailable')
            check_navigation()
            if not isinstance(current, dict) or not isinstance(current.get('answer'), str):
                fail('source_readiness_unavailable')
            answer_sha = hashlib.sha256((current.get('answer') or '').encode('utf-8')).hexdigest()
            proof['observations'].append({'question_verified': current.get('question_verified'),
                'user_message_count': current.get('user_message_count'), 'answer_sha256': answer_sha})
            if (current.get('page_url') != expected.get('page_url') or _blocked_reason(current)
                    or current.get('assistant_count') != expected.get('assistant_count')
                    or answer_sha != proof['target_answer_sha256']
                    or current.get('client_mode') != expected.get('client_mode')
                    or current.get('model_label') != expected.get('model_label')
                    or current.get('completion_marker') is not True or current.get('busy')):
                fail('completed_answer_context_changed')
            if current.get('user_message_count') == 1 and current.get('question_verified') is True:
                stable += 1
            elif current.get('user_message_count') == 0 and current.get('question_verified') is False:
                stable = 0
            else:
                fail('literal_question_mismatch')
            if stable >= 2:
                proof['status'] = 'literal_dom_verified'
                # Keep navigation observation active until the caller installs
                # the unchanged source guard; there is no unobserved handoff.
                yield current
                check_navigation()
                return
            await asyncio.sleep(min(.25, max(0, deadline - time.monotonic())))
    finally:
        try:
            if listening:
                page.remove_listener('framenavigated', navigated)
            (run_dir / 'source-readiness.json').write_text(json.dumps(proof, ensure_ascii=False, indent=2), encoding='utf-8')
        except Exception:
            # Even an audit/cleanup failure must not fall into an unguarded
            # diagnostic DOM read or screenshot before the source guard exists.
            raise _SourceContextChanged('source_context_changed_not_captured') from None


class _SourceContextGuard:
    """Bind source evidence to the completed answer, not a live last-answer locator."""
    def __init__(self, page, config, snapshot, run_dir, *, original_answer_sha256=None, canonical_url=None):
        self.page, self.config, self.expected, self.run_dir = page, config, snapshot, run_dir
        self.route = SubmissionReceipt._route(snapshot.get('page_url'))
        self.answer_sha = hashlib.sha256((snapshot.get('answer') or '').encode('utf-8')).hexdigest()
        self.binding = {'schema_version': 1, 'evidence_file': 'source-context.json',
                        'target_question_sha256': hashlib.sha256(config.question.encode('utf-8')).hexdigest(),
                        'target_answer_sha256': original_answer_sha256 or self.answer_sha,
                        'canonical_url_sha256': hashlib.sha256((canonical_url or snapshot.get('page_url') or '').encode('utf-8')).hexdigest()}
        self.proof = {**self.binding, 'blocked': False, 'stop_reason': None, 'navigation_away_seen': False,
                      'source_phase_completed': False,
                      'observations': [], 'observations_dropped': 0}
        self.listening = False
        self.expected_verified = False

    def fail(self, reason):
        self.proof.update(blocked=True, stop_reason=reason)
        raise _SourceContextChanged('source_context_changed_not_captured')

    def navigated(self, frame):
        if frame == self.page.main_frame and SubmissionReceipt._route(frame.url) != self.route:
            self.proof.update(blocked=True, stop_reason='main_page_navigation', navigation_away_seen=True)

    def start(self):
        if not all(hasattr(self.page, name) for name in ('on', 'remove_listener', 'main_frame')):
            self.fail('navigation_observation_unavailable')
        self.page.on('framenavigated', self.navigated)
        self.listening = True
        self.verify(self.expected, 'expected_answer')
        self.expected_verified = True

    def verify(self, snapshot, phase):
        record = {**_submission_context_view(snapshot), 'phase': phase,
                  'question_verified': snapshot.get('question_verified'),
                  'completion_marker': snapshot.get('completion_marker'), 'busy': snapshot.get('busy'),
                  'answer_sha256': hashlib.sha256((snapshot.get('answer') or '').encode('utf-8')).hexdigest()}
        if len(self.proof['observations']) < 128:
            self.proof['observations'].append(record)
        else:
            self.proof['observations_dropped'] += 1
        if self.proof['blocked']:
            self.fail(self.proof['stop_reason'])
        if not self.route or self.route[0] != 'canonical' or SubmissionReceipt._route(snapshot.get('page_url')) != self.route:
            self.fail('canonical_conversation_mismatch')
        if (_blocked_reason(snapshot) or snapshot.get('question_verified') is not True
                or snapshot.get('user_message_count') != 1
                or snapshot.get('assistant_count') != self.expected.get('assistant_count')
                or snapshot.get('completion_marker') is not True or snapshot.get('busy')
                or snapshot.get('client_mode') != self.config.client_mode
                or snapshot.get('model_label') != self.expected.get('model_label')):
            self.fail('question_completion_or_mode_mismatch')
        if record['answer_sha256'] != self.answer_sha:
            if not prove_progress_body_elision(self.expected.get('answer') or '', self.expected, snapshot):
                self.fail('complete_answer_mismatch')

    async def read(self, phase, *, allow_external_panels=False):
        # Refuse before reading body/DOM as well as after the asynchronous read.
        if self.proof['blocked']:
            self.fail(self.proof['stop_reason'])
        if SubmissionReceipt._route(self.page.main_frame.url) != self.route:
            self.fail('canonical_conversation_mismatch')
        snapshot = await read_snapshot(self.page, self.config, allow_external_panels=allow_external_panels)
        self.verify(snapshot, phase)
        return snapshot

    async def capture(self, phase, *, allow_external_panels=False, optional_screenshot=False):
        await self.read(phase + '_before_image', allow_external_panels=allow_external_panels)
        # Keep the image in memory until the page is still bound after capture.
        try:
            options = {'full_page': True, **({'timeout': 5000} if optional_screenshot else {})}
            image = await self.page.screenshot(**options)
        except Exception as exc:
            if not optional_screenshot or type(exc).__name__ != 'TimeoutError' or self.page.is_closed():
                raise
            image = None
        snapshot = await self.read(phase + '_after_image', allow_external_panels=allow_external_panels)
        return image, snapshot

    def finish(self):
        if self.listening:
            self.page.remove_listener('framenavigated', self.navigated)
        (self.run_dir / 'source-context.json').write_text(
            json.dumps(self.proof, ensure_ascii=False, indent=2), encoding='utf-8')
        return {**self.binding, 'status': ('verified' if self.proof['source_phase_completed']
                                          and not self.proof['blocked'] else 'unverified')}


async def _expand_sources(page: Any, config: CollectorConfig, run_dir: Path, *, progress: Callable | None = None,
                          expected_snapshot: dict[str, Any] | None = None,
                          source_guard=None, initial_evidence_file: str = 'answer.png') -> tuple[list[dict[str, Any]], list[str], bool]:
    if initial_evidence_file not in ('answer.png', 'answer.json', 'inspection-before.png'):
        raise ValueError('initial_source_evidence_file_invalid')
    # Legacy custom collectors keep their original unverified contract. Native
    # strict sessions and explicit read-only inspections require canonical proof.
    if source_guard is not None:
        expected_snapshot = source_guard.expected
    if expected_snapshot is None and config.strict_client_mode and config.client_mode == 'consumer':
        expected_snapshot = await read_snapshot(page, config)
    if expected_snapshot is None:
        return await _expand_sources_bound(page, config, run_dir, progress=progress,
                                           initial_evidence_file=initial_evidence_file)
    owned_guard = source_guard is None
    guard = source_guard or _SourceContextGuard(page, config, expected_snapshot, run_dir)
    try:
        if owned_guard:
            guard.start()
        result = await _expand_sources_bound(page, config, run_dir, progress=progress, source_guard=guard,
                                             initial_evidence_file=initial_evidence_file)
        await guard.read('finished')
        guard.proof['source_phase_completed'] = True
        return result
    except _SourceContextChanged:
        # Preserve only markers already observed in the completed target answer.
        # No body, DOM or screenshot from another conversation is persisted.
        items = ([{**item, 'evidence_file': initial_evidence_file} for item in expected_snapshot.get('sources', [])]
                 if guard.expected_verified and guard.route and guard.route[0] == 'canonical' else [])
        return items, [], True
    finally:
        if owned_guard:
            guard.finish()


async def _expand_sources_bound(page: Any, config: CollectorConfig, run_dir: Path, *, progress: Callable | None = None,
                                source_guard=None, initial_evidence_file: str = 'answer.png') -> tuple[list[dict[str, Any]], list[str], bool]:
    if initial_evidence_file not in ('answer.png', 'answer.json', 'inspection-before.png'):
        raise ValueError('initial_source_evidence_file_invalid')
    # Controls belong to the latest answer, so sidebar/history links cannot leak
    # into the citation list. Each source control is clicked at most once.
    roots = page.locator(config.selectors.assistant_message)
    root = await _first_visible(roots.last)
    if root is None:
        return [], [], source_guard is not None
    snapshot = (await source_guard.read('initial') if source_guard else await read_snapshot(page, config))
    bound_root = await root.element_handle() if source_guard else None
    items = [{**item, "evidence_file": initial_evidence_file} for item in snapshot["sources"]]
    screenshots: list[str] = []
    controls = root.locator(config.selectors.source_trigger)
    if not await controls.count():
        controls = root.get_by_role("button", name=re.compile(r"^(查看)?(全部)?(\d+\s*(个|篇|条))?\s*(参考资料|搜索来源|来源|引用)(\s*\d+\s*(个|篇|条)?)?$"))
    control_count = await controls.count()
    bound_controls = await controls.element_handles() if source_guard else None
    if bound_controls is not None:
        control_count = len(bound_controls)
        await source_guard.read('bound_source_controls')
    incomplete = control_count > 30 or bool(snapshot["source_trigger_count"] and not control_count)
    def report(phase, processed, total):
        if progress:
            try:
                progress({'event': 'source_progress', 'phase': phase, 'processed': processed, 'total': max(processed, total)})
            except Exception:
                # UI telemetry must not alter evidence or replay a browser action.
                pass
    panel_total = sum([await (bound_controls[index] if bound_controls is not None else controls.nth(index)).is_visible()
                       for index in range(min(control_count, 30))])
    panel_processed = 0
    report('source_panels', panel_processed, panel_total)
    for index in range(min(control_count, 30)):
        control = bound_controls[index] if bound_controls is not None else controls.nth(index)
        if not await control.is_visible():
            incomplete = incomplete or source_guard is not None
            continue
        try:
            if source_guard:
                await source_guard.read('panel_before_click')
                if not await control.evaluate('(e, root) => e.isConnected && root.isConnected && root.contains(e)', bound_root):
                    source_guard.fail('source_control_detached_or_foreign')
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
            if source_guard:
                # The observed search-result block is inside this assistant.
                # A global history/search block or an unbound portal cannot
                # authorize a source capture for the target answer.
                await bound_root.wait_for_selector(config.selectors.source_panel, state='visible', timeout=5000)
            else:
                panel = page.locator(config.selectors.source_panel)
                await panel.first.wait_for(state="visible", timeout=5000)
            # Allow lazy source cards to appear; a recognized empty panel is not
            # enough to claim extraction succeeded.
            await page.wait_for_timeout(750)
            filename = f"sources-{index + 1}.png"
            evidence_file = filename
            if source_guard:
                image, expanded = await source_guard.capture('source_panel', optional_screenshot=True)
                if image is not None:
                    (run_dir / filename).write_bytes(image)
                else:
                    evidence_file = f"sources-{index + 1}.json"
                    expanded['source_capture'] = {'screenshot_status': 'image_timeout',
                        'screenshot_error_type': 'TimeoutError', 'guarded_DOM_verified': True}
            else:
                await page.screenshot(path=str(run_dir / filename), full_page=True)
                expanded = await read_snapshot(page, config, allow_external_panels=True)
            if evidence_file.endswith('.png'):
                screenshots.append(filename)
            if not any(item['scope'] == 'source_panel' for item in expanded["sources"]):
                incomplete = True
            items.extend({**item, "evidence_file": evidence_file} for item in expanded["sources"])
            (run_dir / f"sources-{index + 1}.json").write_text(json.dumps(expanded, ensure_ascii=False, indent=2), encoding="utf-8")
            await page.keyboard.press("Escape")
        except _SourceContextChanged:
            raise
        except Exception:
            incomplete = True
        finally:
            panel_processed += 1
            report('source_panels', panel_processed, panel_total)
    if snapshot["source_trigger_count"] and not any(item['scope'] == 'source_panel' for item in items):
        incomplete = True
    citations = root.locator(f':is({config.selectors.answer_text}) :is({config.selectors.inline_citation})')
    bound_citations = (await bound_root.query_selector_all(f':is({config.selectors.answer_text}) :is({config.selectors.inline_citation})')
                       if source_guard else None)
    if await citations.count() > 30:
        incomplete = True
    citation_count = len(bound_citations) if bound_citations is not None else await citations.count()
    citation_total = sum([await (bound_citations[index] if bound_citations is not None else citations.nth(index)).is_visible()
                          for index in range(min(citation_count, 30))])
    citation_processed = 0
    report('inline_citations', citation_processed, citation_total)
    for index in range(min(citation_count, 30)):
        control = bound_citations[index] if bound_citations is not None else citations.nth(index)
        if source_guard:
            await source_guard.read('citation_before_resolution')
            if not await control.evaluate('(e, root) => e.isConnected && root.isConnected && root.contains(e)', bound_root):
                source_guard.fail('citation_control_detached_or_foreign')
        if await control.evaluate("e => e.matches('a[href]')"):
            if await control.is_visible():
                citation_processed += 1
                report('inline_citations', citation_processed, citation_total)
            continue
        if await control.is_visible():
            try:
                scope = {'source_guard': source_guard} if source_guard else {}
                observed, filename = await _resolve_citation_navigation(page, control, run_dir, index, selectors=config.selectors, client_mode=config.client_mode, **scope)
                if source_guard:
                    await source_guard.read('citation_after_resolution')
                if observed:
                    # Upgrade the same visible marker with its UI-observed target.
                    for item in items:
                        if item["scope"] == "inline_citation" and item.get("ui_order") == index + 1 and not item.get("href"):
                            item.update(observed, evidence_file=filename)
                if filename and filename.endswith('.png'):
                    screenshots.append(filename)
            finally:
                citation_processed += 1
                report('inline_citations', citation_processed, citation_total)
    return items, screenshots, incomplete


def _shuidi_company_separator_match(source_title: str, document: dict) -> str | None:
    """Corroborate one observed Shuidi title variant, without fuzzy matching.

    The citation uses an em dash; the public company page uses a hyphen.
    Company spelling, publisher suffix, unique H1 and company-page route
    must all agree. This does not authorize login, claim or search pages.
    """
    try:
        parsed = urlsplit(document.get('url') or '')
        if (parsed.scheme != 'https' or parsed.hostname != 'shuidi.cn'
                or parsed.username or parsed.password or parsed.port not in (None, 443)
                or not re.fullmatch(r'/company-[0-9a-fA-F]{32}\.html', parsed.path)):
            return None
    except ValueError:
        return None
    if not isinstance(source_title, str) or not isinstance(document.get('title'), str):
        return None
    source = re.fullmatch(r'(?P<company>.+(?:有限责任公司|股份有限公司|有限公司|股份公司))\s*—\s*水滴信用', source_title.strip())
    if not source:
        return None
    company = source['company']
    headlines = document.get('headlines')
    if (not isinstance(headlines, list) or len(headlines) != 1
            or not isinstance(headlines[0], dict) or not isinstance(headlines[0].get('text'), str)
            or headlines[0]['text'].strip() != company):
        return None
    if not re.fullmatch(re.escape(company) + r'\s*-\s*水滴信用', document['title'].strip()):
        return None
    return 'shuidi_company_h1_and_publisher_separator_equivalent'


def _citation_title_match(source_title: str, document: dict) -> str | None:
    """Require complete title equality, recording compatibility normalization."""
    # The observed Toutiao layout appends the public author to a source-card
    # title. Strip it only when both the unique headline and that same public
    # author corroborate the exact parts; never infer a URL or match a domain.
    try:
        parsed = urlsplit(document.get('url') or '')
        toutiao_article = (parsed.scheme in {'http', 'https'} and not parsed.username and not parsed.password
            and parsed.hostname in {'www.toutiao.com', 'm.toutiao.com'}
            and parsed.port in (None, 443 if parsed.scheme == 'https' else 80)
            and re.fullmatch(r'/article/\d+/?', parsed.path))
    except ValueError:
        toutiao_article = False
    base, separator, author = source_title.rpartition('_')

    def match(fold, suffix):
        headlines = list(dict.fromkeys(fold(item.get('text')) for item in document.get('headlines', []) if item.get('text')))
        expected = fold(source_title)
        if not expected:
            return None
        if len(headlines) == 1 and headlines[0] == expected:
            return 'visible_h1_' + suffix
        if (toutiao_article and separator and base and author and len(headlines) == 1
                and headlines[0] == fold(base)
                and any(fold(item.get('text')) == fold(author) for item in document.get('authors', []))):
            return 'visible_h1_and_public_author_' + suffix
        if fold(document.get('title')) == expected:
            return 'document_title_' + suffix
        return None

    fold = lambda value: re.sub(r'\s+', '', value or '').casefold()
    exact = match(fold, 'exact')
    if exact:
        return exact
    # Full/half-width punctuation and compatibility glyphs can differ between
    # the visible source card and its observed destination. Keep both originals
    # in evidence; only complete NFKC-equivalent strings may authorize a match.
    compatible = match(lambda value: fold(unicodedata.normalize('NFKC', value or '')), 'nfkc_equivalent')
    if compatible:
        return compatible
    return _shuidi_company_separator_match(source_title, document)


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


def _publisher_marker(marker, *, allow_truncated=False):
    """A visible ellipsis may authorize only a long, literal publisher prefix."""
    folded = re.sub(r'\s+', '', marker or '')
    prefix = None
    if allow_truncated:
        match = re.fullmatch(r'(.+?)(?:\.{3}|…)', folded)
        if match and sum(character.isalnum() for character in match[1]) >= 5:
            prefix = match[1]
    return folded, prefix


def _publisher_label_match(marker, publisher, *, allow_truncated=False):
    folded, prefix = _publisher_marker(marker, allow_truncated=allow_truncated)
    label = re.sub(r'\s+', '', publisher or '')
    if folded and label == folded:
        return 'exact'
    if prefix and label.startswith(prefix) and len(label) > len(prefix):
        return 'truncated_prefix_guarded'
    return None


async def _resolve_citation_navigation(page: Any, control: Any, run_dir: Path,
                                        index: int, *, selectors: DoubaoSelectors = DoubaoSelectors(), client_mode: str = "consumer",
                                        source_guard=None) -> tuple[dict[str, Any] | None, str | None]:
    """Observe a normal click's new-tab navigation when a UI citation has no href."""
    filename = f"citation-{index + 1}.png"
    evidence_file = filename
    evidence: dict[str, Any] = {"captured_at": datetime.now(HK_TIME).isoformat(), "navigation_urls": []}
    popup = None
    previous_pages = set(page.context.pages)
    created_pages, navigations = [], []
    listening = False
    stage = 'hover_card'
    pending_image = None

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
        if source_guard:
            await source_guard.read('citation_before_hover')
        await control.hover(timeout=3000)
        marker_label = await control.inner_text()
        marker_folded, publisher_prefix = _publisher_marker(marker_label, allow_truncated=source_guard is not None)
        # The UI can keep the previous citation's card visible while lazily
        # replacing it. Wait a finite time for this marker's publisher instead
        # of treating a fixed delay as proof that the card has switched.
        evidence['hover_card_wait_budget_seconds'] = 2
        try:
            await page.wait_for_function("""({s, publisher, prefix}) => {
                const visible = e => e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden';
                const fold = value => (value || '').replace(/\\s+/g, '');
                const cards = [...document.querySelectorAll(s.citation_popup)]
                    .filter(e => visible(e) && e.querySelector(s.citation_popup_title));
                if (cards.length !== 1) return false;
                const labels = cards[0].querySelectorAll(s.citation_popup_label);
                const titles = cards[0].querySelectorAll(s.citation_popup_title);
                const label = labels.length === 1 ? fold(labels[0].innerText) : '';
                return labels.length === 1 && titles.length === 1 && visible(titles[0])
                    && (label === publisher || (prefix && label.startsWith(prefix) && label.length > prefix.length));
            }""", arg={'s': asdict(selectors), 'publisher': marker_folded, 'prefix': publisher_prefix}, timeout=2000)
            evidence['hover_card_wait_status'] = 'matched'
        except Exception as exc:
            if type(exc).__name__ != 'TimeoutError':
                raise
            evidence['hover_card_wait_status'] = 'publisher_match_timeout'
        if source_guard:
            await source_guard.read('citation_before_dom')
        evidence["marker_dom"] = await control.evaluate("e => e.outerHTML")
        evidence["tooltip_dom"] = await page.locator('[role="tooltip"]:visible').evaluate_all("nodes => nodes.map(e => e.outerHTML)")
        click_target = control
        source_title = None
        bound_publisher = None
        # Work UI labels open a hover card; the source title inside that card is
        # the actual link control. Match the visible publisher to this marker so
        # stale/foreign popovers cannot be attributed to the current citation.
        active = page.locator(f':is({selectors.citation_popup}):visible').filter(has=page.locator(selectors.citation_popup_title))
        if await active.count() == 1:
            labels = active.locator(selectors.citation_popup_label)
            publisher = await labels.inner_text() if await labels.count() == 1 else None
            publisher_match = _publisher_label_match(marker_label, publisher, allow_truncated=source_guard is not None)
            if publisher_match:
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
                    bound_publisher = publisher
                    evidence["source_title"] = source_title
                    evidence['bound_publisher'] = bound_publisher
                    evidence['publisher_match_method'] = publisher_match
                    evidence["click_target_dom"] = binding['dom']
                    await click_target.evaluate("""(e, s) => {
                        e.addEventListener('click', () => {
                            e.setAttribute('data-geo-click-title', e.innerText);
                            const card = e.closest(s.citation_popup);
                            e.setAttribute('data-geo-click-publisher', card?.querySelector(s.citation_popup_label)?.innerText || '');
                        }, {once: true, capture: true});
                    }""", asdict(selectors))
        stage = 'citation_screenshot'
        if source_guard:
            # Only an already-bound unique card/title/publisher may continue
            # without a PNG. capture still verifies this exact completed answer
            # before AND after a bounded image timeout; all other errors raise.
            pending_image, _ = await source_guard.capture('citation_card', optional_screenshot=source_title is not None)
            if pending_image is None:
                evidence_file = f"citation-{index + 1}.json"
                evidence['screenshot_status'] = 'image_timeout'
                evidence['screenshot_error_type'] = 'TimeoutError'
                evidence['guarded_DOM_verified_after_image_timeout'] = True
            else:
                evidence['screenshot_status'] = 'captured'
        else:
            await page.screenshot(path=str(run_dir / filename), full_page=True)
        if client_mode == "work" and source_title is None:
            evidence["resolution_status"] = "work_citation_card_not_matched"
            return None, evidence_file
        if source_title:
            stage = 'bound_card_before_click'
            still_bound = await click_target.evaluate("""(e, value) => {
                const {s, title, publisher} = value;
                const visible = node => node.getClientRects().length && getComputedStyle(node).visibility !== 'hidden';
                const cards = [...document.querySelectorAll(s.citation_popup)]
                    .filter(node => visible(node) && node.querySelector(s.citation_popup_title));
                const card = e.closest(s.citation_popup);
                const labels = card?.querySelectorAll(s.citation_popup_label);
                const titles = card?.querySelectorAll(s.citation_popup_title);
                return e.isConnected && visible(e) && cards.length === 1 && cards[0] === card
                    && labels?.length === 1 && titles?.length === 1 && titles[0] === e
                    && e.innerText === title && labels[0].innerText === publisher;
            }""", {'s': asdict(selectors), 'title': source_title, 'publisher': bound_publisher})
            if not still_bound or re.sub(r'\s+', '', await control.inner_text()) != marker_folded:
                evidence['resolution_status'] = 'citation_card_changed_before_click'
                return None, evidence_file
        page.context.on('page', observe_page)
        page.context.on('request', observe)
        listening = True
        deadline = time.monotonic() + 12
        # noopener links can emit only context.page, initially with url=''.
        # One click is the only submission; URL/title reads may wait or repeat.
        stage = 'source_click'
        if source_guard:
            await source_guard.read('citation_before_click')
        await click_target.click(timeout=3000, no_wait_after=True)
        if source_guard:
            await source_guard.read('citation_after_click')
        stage = 'new_page_wait'
        while not created_pages and time.monotonic() < deadline:
            if source_guard and source_guard.proof['blocked']:
                source_guard.fail(source_guard.proof['stop_reason'])
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
                    re.sub(r"\s+", "", clicked_publisher or "") != re.sub(r"\s+", "", bound_publisher or "")
                    or re.sub(r"\s+", "", await control.inner_text()) != marker_folded):
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
        return observed or None, evidence_file
    except _SourceContextChanged:
        raise
    except Exception as exc:
        evidence.update(resolution_status='citation_navigation_error', failure_stage=stage,
                        error_type=type(exc).__name__)
        return None, evidence_file if evidence_file.endswith('.json') or pending_image is not None or (run_dir / filename).is_file() else None
    finally:
        if listening:
            page.context.remove_listener('page', observe_page)
            page.context.remove_listener('request', observe)
        try:
            if source_guard:
                await source_guard.read('citation_before_persist')
                if pending_image is not None:
                    (run_dir / filename).write_bytes(pending_image)
            (run_dir / f"citation-{index + 1}.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
        finally:
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
    if (original.get('environment') or {}).get('session_slot', 'primary') != config.session_slot:
        raise ValueError('复查必须使用原豆包会话，不可切换到其他会话')
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
    source_guard = None
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
        if (invalid_navigation or (source_guard and source_guard.proof['navigation_away_seen'])
                or SubmissionReceipt._route(snapshot.get('page_url')) != expected_route):
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
        expected_model = (original.get('environment') or {}).get('model_label')
        if config.strict_client_mode and expected_model and snapshot.get('model_label') != expected_model:
            raise ValueError('inspection_model_changed')

    page.on('framenavigated', navigated)
    stage = 'inspection_identity'
    try:
        current = await read_snapshot(page, config)
        verify(current)
        # verify(current) above has authenticated any omitted progress prefix
        # against the original answer.json manifest. Public binding stays to
        # the immutable original response; internal comparisons freeze current.
        source_guard = _SourceContextGuard(page, config, current, run_dir,
            original_answer_sha256=answer_sha, canonical_url=original['conversation_url'])
        source_guard.start()
        await _save_checkpoint(page, config, run_dir, result, 'inspection-before', source_guard=source_guard)
        verify(await source_guard.read('inspection_before_sources'))
        if progress:
            progress({'event': 'answer_completed', 'state': 'extracting_sources', 'question_submitted': True})
        stage = 'inspection_sources'
        items, screenshots, incomplete = await _expand_sources(page, config, run_dir, progress=progress,
            source_guard=source_guard, initial_evidence_file='inspection-before.png')
        result['screenshots'].extend(screenshots)
        verify(await source_guard.read('inspection_after_sources'))
        await _save_checkpoint(page, config, run_dir, result, 'inspection-after', source_guard=source_guard)
        verify(await source_guard.read('inspection_complete'))
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
        if source_guard:
            result['source_context_binding'] = source_guard.finish()
            source_guard = None
        write_evidence_result(run_dir, result)
        raise
    except Exception as exc:
        if isinstance(exc, _SourceContextChanged):
            reason = ('inspection_conversation_changed' if source_guard.proof['stop_reason'] in
                      {'main_page_navigation', 'canonical_conversation_mismatch'} else 'inspection_source_context_changed')
        else:
            reason = str(exc) if isinstance(exc, ValueError) and str(exc).startswith('inspection_') else _browser_error_reason(exc)
        result.update(failure_reason=reason, failure_stage=stage)
    finally:
        if source_guard:
            result['source_context_binding'] = source_guard.finish()
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
    result: dict[str, Any] = AccountObservedResult({
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
                        "session": "dedicated_persistent_profile", "new_conversation": False,
                        "session_slot": config.session_slot, "account_identity_status": "unverified",
                        "requested_client_mode": config.client_mode, "requested_model_label": config.requested_model_label},
    })
    page = None
    context = None
    source_guard = None
    account_before = None
    account_context_id = uuid.uuid4().hex
    account_profile_scope = None
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
            account_profile_scope = context_profile_scope(context)
            try:
                page = await context.new_page()
                stage = "navigation"
                await _navigate_to_doubao(page, config.navigation_timeout_seconds)
                initial = await read_snapshot(page, config)
                initial = await _wait_for_manual_verification(page, config, run_dir, result, initial, progress=progress)
                if _blocked_reason(initial):
                    result.update(collection_status='need_action', failure_reason=_blocked_reason(initial),
                                  failure_stage=stage,
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
                baseline = await _settle_pre_submission_mode(page, config, baseline)
                if config.client_mode == 'consumer' and config.strict_client_mode:
                    stage = 'composer_readiness'
                    baseline = await _settle_consumer_composer(page, config, baseline, run_dir)
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
                    result.update(collection_status="need_action", failure_reason=_blocked_reason(baseline), failure_stage=stage)
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
                    if config.strict_client_mode and baseline.get('client_mode') != config.client_mode:
                        result.update(collection_status='need_action', failure_reason='client_mode_mismatch',
                                      finished_at=datetime.now(HK_TIME).isoformat())
                        return result, write_evidence_result(run_dir, result)
                    if requested_label not in re.sub(r"\s+", "", baseline.get("model_label") or ""):
                        raise ValueError("model_selection_not_confirmed")
                    result["environment"].update(model_label=baseline["model_label"], requested_model_label=config.requested_model_label)
                    await _save_checkpoint(page, config, run_dir, result, "model-selected")
                if config.account_probe_id and account_profile_scope:
                    stage = 'account_before_submission'
                    account_before = await observe_official_visible_handle(page,
                        client_mode=baseline['client_mode'], session_slot=config.session_slot,
                        context_id=account_context_id, profile_scope_sha256=account_profile_scope,
                        stage='before_submission', probe_id=config.account_probe_id)
                    # The settings surface must be closed before any full-page
                    # evidence capture or submission. Recheck the composer too.
                    baseline = await read_snapshot(page, config)
                    if (_blocked_reason(baseline) or baseline.get('client_mode') != config.client_mode
                            or baseline.get('assistant_count') != 0 or baseline.get('user_message_count') != 0):
                        raise ValueError('account_observation_composer_changed_not_submitted')
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
                editor_settle = None
                if config.submission_format == 'verbatim_fenced':
                    editor_settle = await _fenced_editor_settle(editor, sent_text, run_dir)
                editor_proof = await editor.evaluate(EDITOR_READBACK_JS, sent_text)
                readback = editor_proof.get('readback')
                (run_dir / 'editor-readback.json').write_text(json.dumps({
                    'submission_format': config.submission_format, 'expected_question_sha256': hashlib.sha256(config.question.encode('utf-8')).hexdigest(),
                    'expected_submission_sha256': hashlib.sha256(sent_text.encode('utf-8')).hexdigest(),
                    'editor_readback': readback,
                    'editor_readback_sha256': hashlib.sha256(readback.encode('utf-8')).hexdigest() if isinstance(readback, str) else None,
                    'editor_readback_method': editor_proof['method'],
                    'editor_settle': editor_settle,
                    'editor_dom': editor_proof['editor_dom'],
                    'editor_inner_text': editor_proof['inner_text'], 'editor_text_content': editor_proof['text_content'],
                    'verified': readback == sent_text}, ensure_ascii=False, indent=2), encoding='utf-8')
                if (readback != sent_text or editor_settle is not None
                        and (editor_settle['matched_before'] is not True or editor_settle['matched_after'] is not True)):
                    raise ValueError('editor_readback_mismatch_not_submitted')
                if config.submission_format == 'verbatim_fenced' and baseline.get('user_message_count') != 0:
                    raise ValueError('submission_fresh_user_context_not_confirmed')
                send_control = None
                if config.submission_format == 'verbatim_fenced':
                    stage = 'submission_control'
                    send_control = await _fenced_send_control(page, config, run_dir)
                    send_control = await _fenced_send_ready(page, config, run_dir, send_control, sent_text, baseline)
                elif baseline.get('client_mode') == 'consumer':
                    stage = 'submission_control'
                    send_control = await _fenced_send_control(page, config, run_dir, allow_missing=True)
                    if send_control is not None:
                        send_control = await _fenced_send_ready(page, config, run_dir, send_control, sent_text, baseline)
                result['submission_transport']['method'] = 'visible_send_button' if send_control is not None else 'editor_enter'
                result["question_submission_attempted"] = True
                submission = SubmissionReceipt(page, config.question, baseline['assistant_count'],
                    submitted_text=sent_text, editor_readback=readback, submission_format=config.submission_format,
                    baseline_user_count=baseline.get('user_message_count'))
                submission.attempted()
                # Persist conservative attempt state before the first operation
                # that can submit. If either evidence or the native task store
                # cannot persist it, neither a click nor an Enter may occur.
                stage = 'submission_checkpoint'
                result['submission_receipt'] = submission.evidence()
                checkpoint_path = write_evidence_result(run_dir, result)
                if progress:
                    progress({'event': 'question_submission_started', 'state': 'submitting_question',
                              'question_submission_attempted': True, 'question_submitted': False,
                              'evidence_path': str(checkpoint_path)})
                stage = "submit_question"
                if send_control is not None:
                    await send_control.click(timeout=5000)  # one operation; no Enter fallback or retry
                else:
                    await editor.press("Enter")  # legacy transport when no consumer send button was found
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
                        result.update(collection_status="failed" if reason == "service_capacity_unavailable" else "need_action",
                                      failure_reason=reason, failure_stage=stage)
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
                stage = 'answer_checkpoint'
                if result['answer_completed'] and config.strict_client_mode and config.client_mode == 'consumer':
                    if (result.get('question_verification_method') == 'bound_local_to_canonical_after_manual'
                            and last.get('question_verified') is False and last.get('user_message_count') == 0):
                        async with _literal_source_readiness(page, config, last, run_dir, submission) as ready:
                            last = ready
                            source_guard = _SourceContextGuard(page, config, last, run_dir)
                            source_guard.start()
                    else:
                        source_guard = _SourceContextGuard(page, config, last, run_dir)
                        source_guard.start()
                checkpoint_scope = {'source_guard': source_guard} if source_guard else {}
                await _save_checkpoint(page, config, run_dir, result, "answer", **checkpoint_scope)
                result["conversation_url"] = last.get("page_url")
                result["source_ui_summaries"] = last.get("source_ui_summaries", [])
                result["environment"]["model_label"] = last.get("model_label") or result["environment"].get("model_label")
                if result["answer_completed"]:
                    stage = "extract_sources"
                    if progress:
                        progress({'event': 'answer_completed', 'state': 'extracting_sources',
                                  'question_submitted': result['question_submitted']})
                    source_scope = {'source_guard': source_guard} if source_guard else {}
                    initial_evidence = 'answer.png' if (run_dir / 'answer.png').is_file() else 'answer.json'
                    items, screenshots, incomplete = await _expand_sources(page, config, run_dir, progress=progress,
                        initial_evidence_file=initial_evidence, **source_scope)
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
                    elif result['question_submission_attempted'] is True and result['question_submitted'] is not True:
                        # A click/Enter is only an attempt. A missing receipt
                        # neither proves rejection nor authorizes another send.
                        result.update(collection_status='need_action', failure_reason='submission_receipt_timeout')
                    else:
                        result["failure_reason"] = "answer_timeout_or_selectors_changed"
                if config.account_probe_id and account_profile_scope and result['answer_completed']:
                    # Account settings are outside source inspection. Complete
                    # that guard first so the two evidence scopes stay separate.
                    if source_guard:
                        result['source_context_binding'] = source_guard.finish()
                        source_guard = None
                    stage = 'account_after_answer'
                    account_after = await observe_official_visible_handle(page,
                        client_mode=last.get('client_mode'), session_slot=config.session_slot,
                        context_id=account_context_id, profile_scope_sha256=account_profile_scope,
                        stage='after_answer', probe_id=config.account_probe_id)
                    result.account_observations = (account_before, account_after)
                    try:
                        binding = bind_probe_handle(account_before, account_after,
                            probe_id=config.account_probe_id, session_slot=config.session_slot,
                            context_id=account_context_id, profile_scope_sha256=account_profile_scope,
                            source='native_collector')
                    except ValueError:
                        binding = None
                    if binding:
                        result['account_identity_evidence'] = binding
                        result['environment']['account_identity_status'] = binding['account_identity_status']
            except Exception as exc:
                # Browser errors can contain profile paths and other session data.
                result["failure_reason"] = _browser_error_reason(exc)
                result["failure_stage"] = stage
                if isinstance(exc, (ManualWindowClosedError, _SourceContextChanged)) or (isinstance(exc, ValueError) and str(exc).startswith(('submission_', 'editor_'))):
                    result['collection_status'] = 'need_action'
                if isinstance(exc, AccountObservationSafetyError):
                    # A visible account menu may contain personal identifiers.
                    # Preserve earlier evidence; never screenshot this surface.
                    result.update(collection_status='need_action', failure_reason='account_menu_not_safely_closed',
                                  failure_stage=stage)
                elif page is not None and not page.is_closed() and not isinstance(exc, _SourceContextChanged):
                    try:
                        scope = {'source_guard': source_guard} if source_guard else {}
                        await _save_checkpoint(page, config, run_dir, result, "failure", **scope)
                        failure_snapshot = (await source_guard.read('failure_reason') if source_guard
                                            else await read_snapshot(page, config))
                        reason = _blocked_reason(failure_snapshot)
                        if reason:
                            result.update(collection_status="need_action", failure_reason=reason)
                    except Exception:
                        pass
            finally:
                if source_guard:
                    result['source_context_binding'] = source_guard.finish()
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
    if snapshot.get("website_temporarily_unavailable"):
        return "website_temporarily_unavailable"
    try:
        logout_path = urlsplit(snapshot.get('page_url') or '').path.rstrip('/') in ('/logout', '/chat/logout')
    except (ValueError, TypeError):
        logout_path = False
    if logout_path or (snapshot.get("session_reset") and snapshot.get('chat_interface_ready') is not True):
        return "session_redirected_to_logout"
    if snapshot.get("rate_limited"):
        return "rate_limited"
    if snapshot.get("service_unavailable"):
        return "service_capacity_unavailable"
    return None


async def _save_checkpoint(page: Any, config: CollectorConfig, run_dir: Path, result: dict[str, Any], name: str,
                           *, source_guard=None) -> None:
    if config.account_probe_id:
        # A user may open settings while this headed task is running. Never
        # record the visible account menu as a diagnostic checkpoint either.
        groups = page.locator('nav[aria-label="设置分类"], [role="group"][aria-label="设置分类"]')
        for index in range(await groups.count()):
            if await groups.nth(index).is_visible():
                raise AccountObservationSafetyError()
    filename = f"{name}.png"
    if source_guard:
        image, snapshot = await source_guard.capture(name, optional_screenshot=name in {'answer', 'failure'})
        if image is not None:
            (run_dir / filename).write_bytes(image)
            result['screenshots'].append(filename)
        else:
            diagnostic = {'checkpoint': name, 'operation': 'screenshot', 'error_type': 'TimeoutError',
                          'recorded_at': datetime.now(HK_TIME).isoformat()}
            result.setdefault('diagnostic_errors', []).append(diagnostic)
            (run_dir / f'{name}-diagnostic.json').write_text(json.dumps(diagnostic, ensure_ascii=False, indent=2), encoding='utf-8')
        (run_dir / f'{name}.json').write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding='utf-8')
        return
    optional_screenshot = (result.get('question_submission_attempted') is True
        and (name in {'answer', 'failure', 'deferred-notice'} or name.startswith('manual-action-')))
    if optional_screenshot:
        # Preserve readable DOM before a diagnostic screenshot can time out.
        # DOM/storage errors still propagate; they cannot prove completion.
        try:
            snapshot = await read_snapshot(page, config)
            (run_dir / f"{name}.json").write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            if name == 'answer':
                result['answer_completed'] = False
            raise
    try:
        options = {'path': str(run_dir / filename), 'full_page': True}
        if optional_screenshot:
            options['timeout'] = 5000
        await page.screenshot(**options)
        result["screenshots"].append(filename)
    except Exception as exc:
        if not optional_screenshot or type(exc).__name__ != 'TimeoutError' or page.is_closed():
            raise
        diagnostic = {'checkpoint': name, 'operation': 'screenshot', 'error_type': type(exc).__name__,
                      'recorded_at': datetime.now(HK_TIME).isoformat()}
        result.setdefault('diagnostic_errors', []).append(diagnostic)
        (run_dir / f'{name}-diagnostic.json').write_text(
            json.dumps(diagnostic, ensure_ascii=False, indent=2), encoding='utf-8')
    if not optional_screenshot:
        snapshot = await read_snapshot(page, config)
        (run_dir / f"{name}.json").write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")


async def _wait_for_manual_verification(page: Any, config: CollectorConfig, run_dir: Path,
                                        result: dict[str, Any], snapshot: dict[str, Any], *, progress=None,
                                        submission=None) -> dict[str, Any]:
    """Keep the current page open for the user; never solve or resubmit anything."""
    reason = _blocked_reason(snapshot)
    record = result["manual_verification"]
    remaining = None if config.manual_wait_seconds is None else config.manual_wait_seconds - record["waited_seconds"]
    if reason not in MANUAL_AUTH_REASONS or (remaining is not None and remaining <= 0):
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
            if _blocked_reason(snapshot) not in MANUAL_AUTH_REASONS:
                break
        event["cleared"] = _blocked_reason(snapshot) is None
        event['exit_reason'] = 'cleared' if event['cleared'] else 'wait_budget_exhausted' if _blocked_reason(snapshot) in MANUAL_AUTH_REASONS else 'different_blocker'
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


_LOGIN_OBSERVATION_JS = r"""s => {
    const unknown = {client_mode: null, login_required: null, challenge: null};
    if (!['doubao.com', 'www.doubao.com'].includes(location.hostname)) return unknown;
    if (s.require_chat_origin && (location.protocol !== 'https:' || location.port !== ''
            || !/^\/chat(?:\/|$)/.test(location.pathname))) return unknown;
    const visible = e => !!e && e.getClientRects().length > 0
        && getComputedStyle(e).visibility !== 'hidden' && getComputedStyle(e).display !== 'none';
    const any = selector => [...document.querySelectorAll(selector)].some(visible);
    // Read only this UI label. Account names, conversation text and DOM never leave the page.
    const mode = (__VISIBLE_CLIENT_MODE__)();
    const login = any(s.login_dialog) || any('[data-testid="to_login_button"]');
    const challenge = any(s.challenge);
    const recognized = !!mode || login || challenge || any(s.editor);
    return {client_mode: mode, login_required: recognized ? login : null,
        challenge: recognized ? challenge : null};
}""".replace('__VISIBLE_CLIENT_MODE__', _VISIBLE_CLIENT_MODE_JS)


async def _read_login_observation(page: Any, config: CollectorConfig) -> dict[str, Any]:
    """Read only safe UI state; absence of a login prompt is not authentication proof."""
    value = await page.evaluate(_LOGIN_OBSERVATION_JS, {
        'login_dialog': config.selectors.login_dialog, 'challenge': config.selectors.challenge,
        'editor': config.selectors.editor,
        'require_chat_origin': config.strict_client_mode,
    })
    value = value if isinstance(value, dict) else {}
    return {'client_mode': value.get('client_mode') if value.get('client_mode') in ('consumer', 'work') else None,
            'login_required': value.get('login_required') if type(value.get('login_required')) is bool else None,
            'challenge': value.get('challenge') if type(value.get('challenge')) is bool else None}


async def open_doubao_session(config: CollectorConfig, wait_seconds: float | None = 300, *, playwright_factory: Callable | None = None) -> dict[str, Any]:
    """Wait for manual login and return only last-observed UI state, not login/save proof."""
    from playwright.async_api import async_playwright

    async with (playwright_factory or async_playwright)() as playwright:
        config.profile_dir.resolve().mkdir(parents=True, exist_ok=True)
        options: dict[str, Any] = {"headless": False, "locale": "zh-CN", "timezone_id": "Asia/Hong_Kong"}
        if config.executable_path:
            options["executable_path"] = config.executable_path
        context = await launch_context(playwright, config.profile_dir.resolve(), options, config.fallback_executable_path)
        observation = {'client_mode': None, 'login_required': None, 'challenge': None}
        close_reason = 'wait_elapsed'
        page_crashed = False
        disconnected = False

        def crash(_page):
            nonlocal page_crashed
            page_crashed = True

        def disconnect(_browser):
            nonlocal disconnected
            disconnected = True

        browser = context.browser
        if browser:
            browser.on('disconnected', disconnect)
        try:
            page = context.pages[0] if context.pages else await context.new_page()
            page.on('crash', crash)
            try:
                await page.goto(DOUBAO_URL, wait_until="domcontentloaded", timeout=config.navigation_timeout_seconds * 1000)
            except Exception as exc:
                if 'Target page, context or browser has been closed' in str(exc):
                    disconnected = not page.is_closed()
                if not page.is_closed() and not page_crashed and not disconnected:
                    # A user navigation must not close their manual-login window.
                    error_match = re.search(r"net::[A-Z_]+", str(exc))
                    reason = error_match.group(0) if error_match else type(exc).__name__
                    print(f"页面跳转未完成（{reason}）；浏览器保持打开，可正常刷新或登录。", flush=True)
            deadline = time.monotonic() + wait_seconds if wait_seconds is not None else None
            while True:
                if page_crashed:
                    close_reason = 'page_crashed'
                    break
                if disconnected or (browser and not browser.is_connected()):
                    close_reason = 'browser_disconnected'
                    break
                if page.is_closed():
                    close_reason = 'window_closed'
                    break
                try:
                    observation = await _read_login_observation(page, config)
                except Exception as exc:
                    # Navigation and normal page closure can interrupt evaluate.
                    # Keep the last actual observation, never invent a new state.
                    if 'Target page, context or browser has been closed' in str(exc):
                        close_reason = 'window_closed' if page.is_closed() else 'browser_disconnected'
                        break
                if deadline is not None and time.monotonic() >= deadline:
                    break
                await asyncio.sleep(0.5)
        finally:
            try:
                await context.close()
            except Exception as exc:
                if "Target page, context or browser has been closed" not in str(exc):
                    raise
        # Even a clean close is not proof of cookie persistence or authentication.
        return {**observation, 'close_reason': close_reason, 'session_saved': None}
