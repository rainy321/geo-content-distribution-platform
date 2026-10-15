"""Collect one consumer-web answer and its displayed sources, with local evidence.

This module never calls private Doubao endpoints, solves challenges, or invents
URLs. Selectors for answer/source UI must be checked against the current client.
"""

from __future__ import annotations

import asyncio
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
    manual_wait_seconds: float = 0
    executable_path: str | None = None
    client_mode: str = "consumer"
    requested_model_label: str | None = None
    selectors: DoubaoSelectors = DoubaoSelectors()

    def __post_init__(self) -> None:
        if self.client_mode not in {"consumer", "work"}:
            raise ValueError("client_mode 必须是 consumer 或 work")
        if self.requested_model_label is not None and (not self.requested_model_label.strip() or len(self.requested_model_label) > 100):
            raise ValueError("模型标签必须是 1 到 100 字的界面文本")
        if not self.question.strip():
            raise ValueError("问题不能为空")
        for name in ("timeout_seconds", "navigation_timeout_seconds", "stable_seconds"):
            value = getattr(self, name)
            if not 0 < value <= 600:
                raise ValueError(f"{name} 必须在 0 到 600 秒之间")
        if not 0 <= self.manual_wait_seconds <= 600:
            raise ValueError("人工验证等待时间必须在 0 到 600 秒之间")
        if self.manual_wait_seconds and not self.headed:
            raise ValueError("人工验证等待需要 --headed 可见浏览器")


class CompletionTracker:
    """Quiet text alone is insufficient: require a fresh answer and UI end marker."""

    def __init__(self, baseline_count: int, stable_seconds: float = 3) -> None:
        self.baseline_count = baseline_count
        self.stable_seconds = stable_seconds
        self.last_text = ""
        self.changed_at = 0.0

    def observe(self, snapshot: dict[str, Any], now: float) -> bool:
        text = snapshot.get("answer", "").strip()
        if text != self.last_text:
            self.last_text = text
            self.changed_at = now
        return bool(
            text
            and snapshot.get("assistant_count", 0) > self.baseline_count
            and snapshot.get("question_verified")
            and snapshot.get("completion_marker")
            and not snapshot.get("busy")
            and not _blocked_reason(snapshot)
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


SNAPSHOT_JS = r"""({s, question, allowExternalPanels}) => {
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
        assistant_count: assistants.length, answer, question_verified: questionVerified,
        user_message_dom: user ? user.outerHTML : null,
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
    payload = {"s": asdict(config.selectors), "question": config.question, "allowExternalPanels": allow_external_panels}
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
    if isinstance(exc, ValueError) and str(exc).startswith("model_"):
        return str(exc)
    code = re.search(r"net::(ERR_[A-Z_]+)", str(exc))
    return f"network_error:{code.group(1)}" if code else f"browser_error:{type(exc).__name__}"


async def _expand_sources(page: Any, config: CollectorConfig, run_dir: Path) -> tuple[list[dict[str, Any]], list[str], bool]:
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
    if snapshot["source_trigger_count"] and not items:
        incomplete = True
    citations = root.locator(f':is({config.selectors.answer_text}) :is({config.selectors.inline_citation})')
    if await citations.count() > 30:
        incomplete = True
    for index in range(min(await citations.count(), 30)):
        control = citations.nth(index)
        if await control.evaluate("e => e.matches('a[href]')"):
            continue
        if await control.is_visible():
            observed, filename = await _resolve_citation_navigation(page, control, run_dir, index, selectors=config.selectors, client_mode=config.client_mode)
            if observed:
                # Upgrade the same visible marker with its UI-observed target.
                for item in items:
                    if item["scope"] == "inline_citation" and item.get("ui_order") == index + 1 and not item.get("href"):
                        item.update(observed, evidence_file=filename)
            if filename:
                screenshots.append(filename)
    return items, screenshots, incomplete


async def _resolve_citation_navigation(page: Any, control: Any, run_dir: Path,
                                        index: int, *, selectors: DoubaoSelectors = DoubaoSelectors(), client_mode: str = "consumer") -> tuple[dict[str, Any] | None, str | None]:
    """Observe a normal click's new-tab navigation when a UI citation has no href."""
    filename = f"citation-{index + 1}.png"
    evidence: dict[str, Any] = {"captured_at": datetime.now(HK_TIME).isoformat(), "navigation_urls": []}
    popup = None
    previous_pages = set(page.context.pages)

    def observe(request: Any) -> None:
        try:
            if request.is_navigation_request() and request.frame.page not in previous_pages and request.frame == request.frame.page.main_frame:
                evidence["navigation_urls"].append(request.url)
        except Exception:
            pass

    try:
        await control.hover(timeout=3000)
        await page.wait_for_timeout(500)
        evidence["marker_dom"] = await control.evaluate("e => e.outerHTML")
        evidence["tooltip_dom"] = await page.locator('[role="tooltip"]:visible').evaluate_all("nodes => nodes.map(e => e.outerHTML)")
        click_target = control
        source_title = None
        # Work UI labels open a hover card; the source title inside that card is
        # the actual link control. Match the visible publisher to this marker so
        # stale/foreign popovers cannot be attributed to the current citation.
        active = page.locator(selectors.citation_popup).filter(has=page.locator(selectors.citation_popup_title))
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
                    source_title = await title.inner_text()
                    evidence["source_title"] = source_title
                    evidence["click_target_dom"] = await title.evaluate("e => e.outerHTML")
                    await click_target.evaluate("""(e, s) => {
                        e.addEventListener('click', () => {
                            e.setAttribute('data-geo-click-title', e.innerText);
                            const card = e.closest(s.citation_popup);
                            e.setAttribute('data-geo-click-publisher', card?.querySelector(s.citation_popup_label)?.innerText || '');
                        }, {once: true, capture: true});
                    }""", asdict(selectors))
        await page.screenshot(path=str(run_dir / filename), full_page=True)
        if client_mode == "work" and source_title is None:
            evidence["resolution_status"] = "work_citation_card_not_matched"
            return None, filename
        page.context.on("request", observe)
        try:
            async with page.expect_popup(timeout=6000) as popup_info:
                await click_target.click(timeout=3000)
            popup = await popup_info.value
            try:
                await popup.wait_for_load_state("domcontentloaded", timeout=3000)
            except Exception:
                pass  # Navigation evidence does not certify third-party availability.
        except Exception:
            pass  # A title-only citation remains valid even without an observed URL.
        finally:
            page.context.remove_listener("request", observe)
        evidence["popup_url"] = popup.url if popup is not None else None
        target = evidence["navigation_urls"][0] if evidence["navigation_urls"] else evidence["popup_url"]
        if target and urlsplit(target).scheme not in {"http", "https"}:
            target = None
        if source_title:
            clicked_title = await click_target.get_attribute('data-geo-click-title')
            clicked_publisher = await click_target.get_attribute('data-geo-click-publisher')
            evidence.update(clicked_title=clicked_title, clicked_publisher=clicked_publisher)
            if (clicked_title != source_title or
                    re.sub(r"\s+", "", clicked_publisher or "") != re.sub(r"\s+", "", await control.inner_text())):
                evidence["resolution_status"] = "citation_card_changed_during_click"
                target = None
                source_title = None
            elif target:
                # Work popups can expose the previous card's target even when
                # the current click text is correct. Only attribute a target
                # corroborated by the destination's public document title.
                evidence["unconfirmed_navigation_url"] = target
                try:
                    destination_title = await popup.title() if popup is not None else None
                except Exception:
                    destination_title = None
                evidence["destination_title"] = destination_title
                if (not destination_title or re.sub(r"\s+", "", destination_title).casefold()
                        != re.sub(r"\s+", "", source_title).casefold()):
                    evidence["resolution_status"] = "destination_title_not_confirmed"
                    target = None
        evidence["observed_url"] = target
        observed = {"observed_url": target, "url_observation_method": "ui_click_navigation"} if target else {}
        if source_title:
            observed.update(title=source_title, title_kind="ui_title")
        return observed or None, filename
    except Exception:
        return None, filename if (run_dir / filename).is_file() else None
    finally:
        (run_dir / f"citation-{index + 1}.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
        if popup is not None and not popup.is_closed():
            await popup.close()


def write_evidence_result(run_dir: Path, result: dict[str, Any]) -> Path:
    result["evidence_manifest"] = [
        {"path": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "bytes": path.stat().st_size}
        for path in sorted(run_dir.iterdir()) if path.is_file() and path.name != "result.json"
    ]
    result_path = run_dir / "result.json"
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result_path


async def collect_doubao_sources(config: CollectorConfig, *, playwright_factory: Callable | None = None) -> tuple[dict[str, Any], Path]:
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
            context = await playwright.chromium.launch_persistent_context(str(config.profile_dir.resolve()), **options)
            try:
                page = await context.new_page()
                stage = "navigation"
                await _navigate_to_doubao(page, config.navigation_timeout_seconds)
                await page.locator(config.selectors.editor).first.wait_for(state="visible", timeout=15000)
                stage = "new_conversation"
                new_label = r"^新工作任务(?:\s|$)" if config.client_mode == "work" else r"^新对话(?:\s|$)"
                new_chat = await _first_visible(page.get_by_role("button", name=re.compile(new_label)))
                if new_chat is not None:
                    await new_chat.click()
                    result["environment"]["new_conversation"] = True
                await page.locator(config.selectors.editor).first.wait_for(state="visible", timeout=15000)
                baseline = await read_snapshot(page, config)
                baseline = await _wait_for_manual_verification(page, config, run_dir, result, baseline)
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
                    await page.locator(config.selectors.model_label).first.click(timeout=5000)
                    choices = page.get_by_role("menuitem").filter(has=page.get_by_text(config.requested_model_label, exact=True))
                    if await choices.count() != 1:
                        raise ValueError("model_option_not_uniquely_available")
                    choice = choices.first
                    if await choice.get_attribute("data-disabled") is not None or await choice.get_attribute("aria-disabled") == "true" or "升级" in await choice.inner_text():
                        raise ValueError("model_option_requires_upgrade_or_disabled")
                    await choice.click(timeout=5000)
                    await page.wait_for_timeout(300)
                    baseline = await read_snapshot(page, config)
                    if re.sub(r"\s+", "", config.requested_model_label) not in re.sub(r"\s+", "", baseline.get("model_label") or ""):
                        raise ValueError("model_selection_not_confirmed")
                    result["environment"].update(model_label=baseline["model_label"], requested_model_label=config.requested_model_label)
                    await _save_checkpoint(page, config, run_dir, result, "model-selected")
                editor = await _first_visible(page.locator(config.selectors.editor))
                if editor is None:
                    raise ValueError("editor_not_found")
                await editor.fill(config.question)
                stage = "question_evidence"
                await _save_checkpoint(page, config, run_dir, result, "question")
                result["question_submission_attempted"] = True
                stage = "submit_question"
                await editor.press("Enter")  # exactly one submission, never an automatic retry
                tracker = CompletionTracker(baseline["assistant_count"], config.stable_seconds)
                deadline = time.monotonic() + config.timeout_seconds
                last = baseline
                stage = "wait_for_answer"
                while time.monotonic() < deadline:
                    last = await read_snapshot(page, config)
                    reason = _blocked_reason(last)
                    if reason:
                        paused_at = time.monotonic()
                        last = await _wait_for_manual_verification(page, config, run_dir, result, last)
                        deadline += time.monotonic() - paused_at
                        reason = _blocked_reason(last)
                    if last["question_verified"] and not result["question_submitted"]:
                        result["question_submitted"] = True
                        (run_dir / "submission-observed.json").write_text(json.dumps(last, ensure_ascii=False, indent=2), encoding="utf-8")
                    if last["assistant_count"] > baseline["assistant_count"]:
                        result["response"] = last["answer"]
                    if reason:
                        result.update(collection_status="failed" if reason == "service_capacity_unavailable" else "need_action", failure_reason=reason)
                        break
                    if tracker.observe(last, time.monotonic()):
                        result["answer_completed"] = True
                        break
                    await asyncio.sleep(0.5)
                await _save_checkpoint(page, config, run_dir, result, "answer")
                result["conversation_url"] = last.get("page_url")
                result["source_ui_summaries"] = last.get("source_ui_summaries", [])
                result["environment"]["model_label"] = last.get("model_label") or result["environment"].get("model_label")
                if result["answer_completed"]:
                    stage = "extract_sources"
                    items, screenshots, incomplete = await _expand_sources(page, config, run_dir)
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
                    result["failure_reason"] = "answer_timeout_or_selectors_changed"
            except Exception as exc:
                # Browser errors can contain profile paths and other session data.
                result["failure_reason"] = _browser_error_reason(exc)
                result["failure_stage"] = stage
                if page is not None and not page.is_closed():
                    try:
                        await _save_checkpoint(page, config, run_dir, result, "failure")
                        reason = _blocked_reason(await read_snapshot(page, config))
                        if reason:
                            result.update(collection_status="need_action", failure_reason=reason)
                    except Exception:
                        pass
            finally:
                await context.close()
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
                                        result: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
    """Keep the current page open for the user; never solve or resubmit anything."""
    reason = _blocked_reason(snapshot)
    record = result["manual_verification"]
    remaining = config.manual_wait_seconds - record["waited_seconds"]
    if reason not in {"human_verification_required", "login_required"} or remaining <= 0:
        return snapshot
    event = {"reason": reason, "started_at": datetime.now(HK_TIME).isoformat(), "cleared": False}
    record["events"].append(event)
    await _save_checkpoint(page, config, run_dir, result, f"manual-action-{len(record['events'])}")
    print(f"等待人工处理（{reason}），请在当前浏览器完成正常验证/登录；保持当前提问，不需要重新发送。", flush=True)
    started = time.monotonic()
    try:
        while time.monotonic() - started < remaining:
            await asyncio.sleep(0.5)
            snapshot = await read_snapshot(page, config)
            if _blocked_reason(snapshot) not in {"human_verification_required", "login_required"}:
                break
        event["cleared"] = _blocked_reason(snapshot) is None
        return snapshot
    finally:
        elapsed = time.monotonic() - started
        record["waited_seconds"] += elapsed
        event["waited_seconds"] = elapsed
        event["finished_at"] = datetime.now(HK_TIME).isoformat()


async def open_doubao_session(config: CollectorConfig, wait_seconds: float = 300, *, playwright_factory: Callable | None = None) -> None:
    """Open a dedicated normal browser for the user to log in/verify manually."""
    from playwright.async_api import async_playwright

    async with (playwright_factory or async_playwright)() as playwright:
        config.profile_dir.resolve().mkdir(parents=True, exist_ok=True)
        options: dict[str, Any] = {"headless": False, "locale": "zh-CN", "timezone_id": "Asia/Hong_Kong"}
        if config.executable_path:
            options["executable_path"] = config.executable_path
        context = await playwright.chromium.launch_persistent_context(str(config.profile_dir.resolve()), **options)
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
            deadline = time.monotonic() + wait_seconds
            while time.monotonic() < deadline and not page.is_closed():
                await asyncio.sleep(0.5)
        finally:
            try:
                await context.close()
            except Exception as exc:
                if "Target page, context or browser has been closed" not in str(exc):
                    raise
