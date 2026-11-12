"""Observe an official, visible account control without sending a question.

Only native callers with a freshly verified consumer mode may use this observer.
Raw account labels stay in memory and are immediately passed to the pure hash
helper. Nothing here writes logs, screenshots, browser storage, or API requests.
"""
from __future__ import annotations

import asyncio
import re
import time
from typing import Any

from services import doubao_account_identity as identity


_SETTINGS_GROUP = '设置分类'
# The live UIA Group is an HTML NAV with implicit navigation semantics. Keep
# the previously verified explicit group variant without assuming UIA roles
# translate directly to DOM ARIA roles.
_SETTINGS_CONTAINER_SELECTOR = 'nav[aria-label="设置分类"], [role="group"][aria-label="设置分类"]'
_ACCOUNT_LABEL = re.compile(r'豆包号\s*[:：]')
_CLOSE_LABEL = re.compile(r'^(?:关闭|关闭设置|Close|Close settings)$')
_SAFETY_MESSAGE = 'account_observation_settings_close_unverified'


class AccountObservationSafetyError(RuntimeError):
    """Stop this context without a screenshot or another page read.

    A settings overlay may still contain the original account label. Callers
    must close the browser context normally, rather than capturing diagnostics.
    """

    def __init__(self):
        super().__init__(_SAFETY_MESSAGE)


async def _visible(locator: Any) -> list[Any]:
    # Bound discovery instead of walking an arbitrary document or its text.
    count = await locator.count()
    if count > 32:
        raise ValueError('account_observation_controls_ambiguous')
    controls = []
    for index in range(count):
        control = locator.nth(index)
        if await control.is_visible() and await control.evaluate(
                "e => getComputedStyle(e).opacity !== '0'"):
            controls.append(control)
    return controls


async def _settings_group(page: Any, timeout_ms: int) -> Any:
    deadline = time.monotonic() + timeout_ms / 1000
    while True:
        groups = await _visible(page.locator(_SETTINGS_CONTAINER_SELECTOR))
        if len(groups) == 1:
            return groups[0]
        if len(groups) > 1 or time.monotonic() >= deadline:
            raise ValueError('account_observation_settings_unverified')
        await asyncio.sleep(min(0.05, max(0, deadline - time.monotonic())))


async def _close_settings(page: Any, dialog: Any, dialog_element: Any, timeout_ms: int) -> None:
    if dialog is None or dialog_element is None:
        raise AccountObservationSafetyError()
    # Observed official settings have no named close control. A single Escape
    # goes to the verified containing dialog, never to page.keyboard globally.
    # Prefer a future unambiguous, explicitly named close button in that dialog.
    buttons = await _visible(dialog.get_by_role('button', name=_CLOSE_LABEL))
    if len(buttons) > 1:
        raise AccountObservationSafetyError()
    if buttons:
        if not await buttons[0].is_enabled():
            raise AccountObservationSafetyError()
        await buttons[0].click(timeout=timeout_ms)
    else:
        await dialog_element.press('Escape', timeout=timeout_ms)
    deadline = time.monotonic() + timeout_ms / 1000
    while True:
        groups = await _visible(page.locator(_SETTINGS_CONTAINER_SELECTOR))
        # The containing-dialog locator uses has(group). If only the group is
        # hidden/removed, that dynamic locator no longer matches even though
        # the dialog remains open. Check the original element independently.
        if not groups and not await dialog_element.is_visible():
            return
        if time.monotonic() >= deadline:
            raise AccountObservationSafetyError()
        await asyncio.sleep(min(0.05, max(0, deadline - time.monotonic())))


async def observe_official_visible_handle(
        page: Any, *, client_mode: str, session_slot: str, context_id: str,
        profile_scope_sha256: str, stage: str = 'login_observation',
        probe_id: str | None = None, timeout_ms: int = 1500,
) -> identity._VisibleHandleObservation | None:
    """Return an opaque hash observation only after settings are safely closed.

    ``client_mode`` must come from the caller's current native mode observation,
    not frontend/imported assertions. Missing/ambiguous controls are unverified,
    not an account failure. Once settings are clicked, failure to prove normal
    closure raises ``AccountObservationSafetyError`` with no original exception
    chain. Cancellation also attempts closure before it can propagate.
    """
    identity._scope(session_slot, context_id, profile_scope_sha256, probe_id, stage)
    if not isinstance(timeout_ms, int) or isinstance(timeout_ms, bool) or not 1 <= timeout_ms <= 10000:
        raise ValueError('account_observation_timeout_invalid')
    original_url = page.url
    if client_mode != 'consumer' or identity._official_chat_origin(original_url) is None:
        return None
    try:
        # A pre-existing settings overlay cannot safely pass through to a later
        # answer screenshot without a close proof owned by this operation.
        if await _visible(page.locator(_SETTINGS_CONTAINER_SELECTOR)):
            raise AccountObservationSafetyError()
        settings = await _visible(page.get_by_role('button', name='设置', exact=True))
        if len(settings) != 1 or not await settings[0].is_enabled():
            return None
    except AccountObservationSafetyError:
        raise
    except Exception:
        return None

    dialog = None
    dialog_element = None
    label_text = None
    clicked = False
    observation = None
    try:
        clicked = True  # A click timeout may still have opened the overlay.
        await settings[0].click(timeout=timeout_ms)
        group = await _settings_group(page, timeout_ms)
        dialogs = await _visible(page.get_by_role('dialog').filter(
            has=page.locator(_SETTINGS_CONTAINER_SELECTOR)))
        if len(dialogs) != 1:
            return None
        dialog = dialogs[0]
        dialog_element = await dialog.element_handle(timeout=timeout_ms)
        if page.url != original_url or identity._official_chat_origin(page.url) is None:
            return None
        accounts = await _visible(group.get_by_role('button', name=_ACCOUNT_LABEL))
        if len(accounts) != 1:
            return None
        label_text = await accounts[0].get_attribute('aria-label')
        if label_text is None:
            label_text = await accounts[0].inner_text(timeout=timeout_ms)
        observation = identity.observe_visible_handle(
            label_text, page_url=page.url, settings_group_name=_SETTINGS_GROUP,
            control_role='button', visible=True, session_slot=session_slot,
            context_id=context_id, profile_scope_sha256=profile_scope_sha256,
            stage=stage, probe_id=probe_id,
        )
        label_text = None
    except asyncio.CancelledError:
        raise
    except Exception:
        return None
    finally:
        label_text = None
        if clicked:
            try:
                # Cancellation may arrive immediately after the normal click,
                # before the main observation has resolved its dialog. Recover
                # only the same unique, visible settings/group scope for close.
                if dialog_element is None:
                    groups = await _visible(page.locator(_SETTINGS_CONTAINER_SELECTOR))
                    dialogs = await _visible(page.get_by_role('dialog').filter(
                        has=page.locator(_SETTINGS_CONTAINER_SELECTOR)))
                    if len(groups) != 1 or len(dialogs) != 1:
                        raise AccountObservationSafetyError()
                    dialog = dialogs[0]
                    dialog_element = await dialog.element_handle(timeout=timeout_ms)
                await _close_settings(page, dialog, dialog_element, timeout_ms)
            except BaseException:
                # Never retain a Playwright call log, raw account label, or
                # original exception chain in this privacy boundary exception.
                raise AccountObservationSafetyError() from None
            finally:
                if dialog_element is not None:
                    try:
                        await dialog_element.dispose()
                    except Exception:
                        pass
    if page.url != original_url or identity._official_chat_origin(page.url) is None:
        return None
    return observation
