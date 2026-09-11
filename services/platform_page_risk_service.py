from __future__ import annotations

from typing import Any

from services.media_account_risk_service import (
    classify_media_account_risk,
    sanitize_risk_evidence,
)


_RISK_SELECTORS = (
    '[role="alert"]',
    '[role="dialog"]',
    '[class*="ban"]',
    '[class*="forbid"]',
    '[class*="restrict"]',
    '[class*="exception"]',
    '[class*="error-page"]',
)
_EDITOR_SELECTOR = 'textarea, [contenteditable="true"], input[type="text"]'


class PlatformPageAccountRiskError(RuntimeError):
    """Raised when the current platform page exposes an account block."""

    def __init__(self, message: str, *, risk: dict[str, Any]):
        super().__init__(message)
        self.risk = dict(risk)


async def detect_visible_platform_account_risk(
    page: Any,
    *,
    platform_name: str,
) -> dict[str, Any] | None:
    """Read bounded operator-visible UI only; never inspect requests or cookies."""

    candidates: list[tuple[str, str]] = []
    try:
        title = sanitize_risk_evidence(await page.title(), limit=240)
    except Exception:
        title = ""
    if title:
        candidates.append(("page_title", title))

    for selector in _RISK_SELECTORS:
        try:
            locator = page.locator(selector)
            count = min(await locator.count(), 6)
        except Exception:
            continue
        for index in range(count):
            try:
                item = locator.nth(index)
                if not await item.is_visible():
                    continue
                text = sanitize_risk_evidence(await item.inner_text(), limit=600)
            except Exception:
                continue
            if text:
                candidates.append((selector, text))
            if len(candidates) >= 18:
                break
        if len(candidates) >= 18:
            break

    for source, text in candidates:
        risk = classify_media_account_risk(text)
        if risk and risk["category"] != "network_error":
            return {**risk, "source": source, "platform_name": platform_name}

    # Full-page bans often lack semantic alert markup. Read the bounded body
    # only if there is no visible editor, so article copy cannot look like an
    # account warning merely because it discusses platform risk.
    try:
        editors = page.locator(_EDITOR_SELECTOR)
        visible_editor = False
        for index in range(min(await editors.count(), 12)):
            if await editors.nth(index).is_visible():
                visible_editor = True
                break
    except Exception:
        visible_editor = True
    if not visible_editor:
        try:
            body = sanitize_risk_evidence(await page.inner_text("body"), limit=4000)
        except Exception:
            body = ""
        risk = classify_media_account_risk(body)
        if risk and risk["category"] != "network_error":
            return {
                **risk,
                "source": "full_page_body",
                "platform_name": platform_name,
            }
    return None


async def raise_if_visible_platform_account_risk(
    page: Any,
    *,
    platform_name: str,
) -> None:
    risk = await detect_visible_platform_account_risk(
        page,
        platform_name=platform_name,
    )
    if not risk:
        return
    evidence = sanitize_risk_evidence(
        risk.get("evidence") or risk.get("reason"),
        limit=300,
    )
    message = (
        f"{platform_name}{risk['label']}[{risk['category']}]：{evidence}；"
        f"{risk['recommended_action']}"
    )
    raise PlatformPageAccountRiskError(message, risk=risk)


__all__ = [
    "PlatformPageAccountRiskError",
    "detect_visible_platform_account_risk",
    "raise_if_visible_platform_account_risk",
]
