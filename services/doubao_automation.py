"""Read-only readiness check for the ordinary Doubao web UI; never submits text."""
from __future__ import annotations

from dataclasses import asdict
import asyncio
from datetime import datetime
import uuid
import time

from services.browser_runtime import launch_context
from services.doubao_source_collector import (
    CollectorConfig, HK_TIME, _blocked_reason, _browser_error_reason,
    _navigate_to_doubao, _read_login_observation, _wait_for_manual_verification,
)
from services.geo_test_runner import write_json


_READY_JS = r"""s => {
    const visible = e => !!e && e.getClientRects().length > 0
        && getComputedStyle(e).visibility !== 'hidden' && getComputedStyle(e).display !== 'none';
    const first = selector => [...document.querySelectorAll(selector)].find(visible);
    const editor = first(s.editor), model = first(s.model_label);
    const label = model ? (model.textContent || '').replace(/\s+/g, ' ').trim() : '';
    return {chat_interface_ready: location.protocol === 'https:'
        && ['doubao.com', 'www.doubao.com'].includes(location.hostname)
        && location.port === ''
        && /^\/chat(?:\/|$)/.test(location.pathname) && !!model && !!label && !!editor
        && (editor.isContentEditable || (editor.tagName === 'TEXTAREA' && !editor.disabled && !editor.readOnly)),
        model_label: label && label.length <= 100 ? label : null};
}"""


async def inspect_available_doubao_session(config: CollectorConfig, *, progress=None, playwright_factory=None):
    """Observe an owned profile, wait for normal authentication, then close our context.

    The next collector opens the exact same controlled profile and checks the
    locked mode again before sending. No cookie copying or mode switching occurs.
    """
    from playwright.async_api import async_playwright

    stamp = datetime.now(HK_TIME)
    directory = config.output_root.resolve() / (f'{stamp:%Y%m%d-%H%M%S}-preflight-{uuid.uuid4().hex[:8]}')
    directory.mkdir(parents=True, exist_ok=False)
    target = directory / 'result.json'
    result = {
        'kind': 'doubao_web_preflight', 'automation_scope': 'doubao_web', 'read_only': True,
        'timestamp': stamp.isoformat(), 'collection_status': 'need_action',
        'failure_stage': 'web_preflight', 'failure_reason': None,
        'question_submission_attempted': False, 'question_submitted': False,
        'answer_completed': False, 'actual_client_mode': None, 'model_label': None,
        'environment': {'session': 'dedicated_persistent_profile', 'requested_client_mode': None},
        'screenshots': [],
        'manual_verification': {'wait_budget_seconds': config.manual_wait_seconds, 'waited_seconds': 0, 'events': []},
    }
    write_json(target, result)
    context = page = None
    try:
        async with (playwright_factory or async_playwright)() as playwright:
            options = {'headless': False, 'locale': 'zh-CN', 'timezone_id': 'Asia/Hong_Kong'}
            if config.executable_path:
                options['executable_path'] = config.executable_path
            context = await launch_context(playwright, config.profile_dir.resolve(), options, config.fallback_executable_path)
            try:
                page = context.pages[0] if context.pages else await context.new_page()
                await _navigate_to_doubao(page, config.navigation_timeout_seconds)
                # domcontentloaded precedes SPA hydration. Wait for observable
                # controls, while keeping this finite startup budget separate
                # from the existing user-controlled authentication wait.
                startup_budget = min(config.navigation_timeout_seconds, 15)
                waited = 0
                while True:
                    if page.is_closed():
                        raise ValueError('manual_verification_window_closed')
                    started_read = time.monotonic()
                    auth_before = result['manual_verification']['waited_seconds']
                    navigation_interrupted = False
                    observation = {'client_mode': None, 'login_required': None, 'challenge': None}
                    try:
                        observation = await _read_login_observation(page, config)
                        snapshot = {**observation, 'page_url': page.url}
                        snapshot = await _wait_for_manual_verification(page, config, directory, result, snapshot, progress=progress)
                        if page.is_closed():
                            raise ValueError('manual_verification_window_closed')
                        # Never infer consumer from absence of a work marker.
                        observation = await _read_login_observation(page, config)
                        ready = await page.evaluate(_READY_JS, asdict(config.selectors))
                        ready = ready if isinstance(ready, dict) else {}
                        blocker = _blocked_reason({**snapshot, **observation, 'page_url': page.url})
                        if blocker:
                            result['failure_reason'] = blocker
                            break
                        if (observation['client_mode'] in ('consumer', 'work')
                                and observation['login_required'] is False and observation['challenge'] is False
                                and ready.get('chat_interface_ready') is True):
                            result.update(collection_status='preflight_ready', actual_client_mode=observation['client_mode'],
                                model_label=ready.get('model_label') if isinstance(ready.get('model_label'), str) else None)
                            break
                    except Exception as error:
                        if page.is_closed() or not any(text in str(error) for text in (
                                'Execution context was destroyed', 'Cannot find context')):
                            raise
                        # A normal login/SPA redirect destroys its old JS
                        # context. Read the new page; do not close or resubmit.
                        navigation_interrupted = True
                    finally:
                        auth_elapsed = result['manual_verification']['waited_seconds'] - auth_before
                        waited += max(0, time.monotonic() - started_read - auth_elapsed)
                    if waited >= startup_budget:
                        result['failure_reason'] = ('web_navigation_not_settled_not_submitted' if navigation_interrupted
                                                    else 'web_mode_unknown_not_submitted' if observation['client_mode'] is None
                                                    else 'web_composer_not_ready_not_submitted')
                        break
                    # The manual wait above is excluded: only actual startup
                    # polling consumes this deadline, never time spent logging in.
                    started_poll = time.monotonic()
                    await asyncio.sleep(0.25)
                    waited += time.monotonic() - started_poll
            finally:
                if context is not None:
                    await context.close()
    except asyncio.CancelledError:
        result.update(collection_status='need_action', failure_reason='preflight_cancelled_not_submitted', actual_client_mode=None)
        raise
    except Exception as error:
        result['collection_status'] = 'need_action'
        result['actual_client_mode'] = None
        if page is not None and page.is_closed():
            result['failure_reason'] = 'manual_verification_window_closed'
        else:
            result['failure_reason'] = _browser_error_reason(error)
    finally:
        result['finished_at'] = datetime.now(HK_TIME).isoformat()
        write_json(target, result)
        if progress and result['collection_status'] != 'preflight_ready':
            progress({'event': 'collector_finished', 'collection_status': 'need_action',
                'failure_reason': result['failure_reason'], 'failure_stage': 'web_preflight',
                'evidence_path': str(target), 'question_submission_attempted': False, 'question_submitted': False})
    return result, target
