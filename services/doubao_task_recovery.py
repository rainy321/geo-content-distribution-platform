"""Read-only, typed recovery views for native Doubao tasks and old evidence."""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path


TASK_KINDS = frozenset(('plan', 'draft', 'research', 'run'))
AUTH_REASONS = frozenset(('login_required', 'human_verification_required', 'session_redirected_to_logout',
                         'client_mode_mismatch', 'manual_verification_window_closed'))


def safe_reason(value):
    return value if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_:.-]{1,160}', value) else None


def _stamp(value):
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return parsed.timestamp() if parsed.tzinfo else None
    except (ValueError, AttributeError, TypeError):
        return None


def _read_evidence(path, root, kind):
    try:
        path = Path(path).resolve()
        path.relative_to(root.resolve())
        evidence_root = root / ('geo-content/evidence' if kind in ('draft', 'research') else 'geo-tests')
        path.relative_to(evidence_root.resolve())
        if path.name != 'result.json':
            return None
        value = json.loads(path.read_text(encoding='utf-8'))
        if isinstance(value, dict) and ('question_submission_attempted' in value or 'failure_reason' in value):
            return value, str(path)
    except (OSError, ValueError, TypeError):
        pass
    return None


def _legacy_reason(record):
    reason = safe_reason(record.get('failure_reason'))
    if reason:
        return reason
    # Legacy wrappers saved a fixed machine reason inside this exact message.
    # It supplies no submission proof; booleans must still come from evidence.
    message = record.get('message')
    if isinstance(message, str):
        match = re.search(r'(?:豆包未返回完整内容|本次未取得完整测试计划)[（(]([A-Za-z0-9_:.-]{1,160})[）)]', message)
        if match:
            return match.group(1)
    return None


def _evidence_for_task(record, root):
    stored = record.get('recovery') if isinstance(record.get('recovery'), dict) else {}
    found = []
    for value in (record.get('evidence_path'), stored.get('evidence_path')):
        if value:
            evidence = _read_evidence(value, root, record.get('kind'))
            if evidence is None:
                return None, True
            found.append(evidence)
    if not found:
        return None, False
    if len({path for _, path in found}) != 1:
        return None, True
    expected_reason = _legacy_reason(record)
    if expected_reason and found[0][0].get('failure_reason') != expected_reason:
        return None, True
    # Time proximity never establishes which customer or request owns a file.
    # Legacy files without an explicit task link remain available for manual
    # diagnosis, but cannot supply flags, session options or a no-send claim.
    return found[0], False


def recovery_view(record, root, *, live_window=False):
    if not isinstance(record.get('kind'), str) or record['kind'] not in TASK_KINDS or record.get('status') == 'completed':
        return None
    stored = record.get('recovery') if isinstance(record.get('recovery'), dict) else {}
    evidence, evidence_conflict = _evidence_for_task(record, Path(root))
    result, path = evidence if evidence else ({}, None)
    progress = record.get('progress') if isinstance(record.get('progress'), dict) else {}
    state = record.get('submission_state') if isinstance(record.get('submission_state'), dict) else {}
    reason = safe_reason(result.get('failure_reason')) or _legacy_reason(record) or safe_reason(stored.get('failure_reason'))
    if live_window:
        reason = safe_reason(record.get('wait_reason')) or reason
    if reason is None:
        reason = {'stopped': 'user_cancelled_submission_unknown', 'needs_review': 'application_closed_submission_unknown',
                  'failed': 'task_failed_submission_unknown', 'need_action': 'submission_state_unknown'}.get(record.get('status'))
    if reason is None:
        return None

    def boolean(name):
        if evidence_conflict:
            return None
        for source in (result, state, stored, progress):
            if name not in source:
                continue
            value = source[name]
            if type(value) is not bool:
                return None
            final_evidence = (_stamp(result.get('finished_at')) is not None
                              and _stamp(result.get('timestamp')) is not None
                              and _stamp(result['finished_at']) >= _stamp(result['timestamp']))
            final_state = record.get('collector_finished') is True
            terminal = record.get('status') in ('failed', 'need_action', 'needs_review', 'stopped')
            if value is False and terminal and not (final_evidence or final_state):
                return None
            return value
        return None

    attempted, submitted = boolean('question_submission_attempted'), boolean('question_submitted')
    environment = result.get('environment') if isinstance(result.get('environment'), dict) else {}
    mode = environment.get('requested_client_mode') or record.get('requested_client_mode') or stored.get('requested_client_mode')
    mode = mode if mode in ('consumer', 'work') else None
    model = environment.get('requested_model_label') or record.get('requested_model_label') or stored.get('requested_model_label')
    model = model if isinstance(model, str) and 0 < len(model) <= 100 else None
    if evidence_conflict:
        mode, model = None, None
    stage = ('before_submission' if attempted is False and submitted is False else
             'after_submission' if attempted is True or submitted is True else 'unknown')
    return {'failure_reason': reason, 'question_submission_attempted': attempted, 'question_submitted': submitted,
            'requested_client_mode': mode, 'requested_model_label': model, 'evidence_path': path,
            'stage': stage, 'continuation': 'same_window' if live_window else 'new_attempt_required'}


def action_required(reason, collection_status=None):
    return (collection_status == 'need_action' or reason in AUTH_REASONS
            or reason in ('browser_error:TargetClosedError', 'user_cancelled_submission_unknown', 'background_task_timeout',
                          'website_temporarily_unavailable'))


def recovery_message(reason, *, attempted=None, submitted=None, failure_stage=None):
    if attempted is False and submitted is False:
        if reason in ('submission_send_button_not_unique_not_submitted', 'submission_send_button_not_ready_not_submitted'):
            return '豆包发送按钮暂不可用或无法唯一确认，本次问题尚未发送。请核对当前窗口后再手动开始；证据已保留，不会自动重试。'
        if reason == 'website_temporarily_unavailable':
            return '豆包网站显示“该页面暂时不可用”，本次问题尚未发送。请核对页面恢复后再手动开始；证据已保留，不会自动重发。'
        if failure_stage == 'navigation' and isinstance(reason, str) and reason.startswith(('browser_error:', 'network_error:')):
            return '豆包页面未能正常打开，本次问题尚未发送。请核对浏览器页面后再手动开始；证据已保留，不会自动重发。'
    if reason == 'website_temporarily_unavailable':
        state = ('问题已确认发送，请先核对当前会话' if submitted is True else
                 '已尝试发送问题，请先核对当前会话与提交结果' if attempted is True else '问题提交状态尚未确认，请先核对当前会话')
        return f'豆包网站显示“该页面暂时不可用”，本次未取得完整回答。{state}；证据已保留，不会自动重发。'
    if reason in ('login_required', 'session_redirected_to_logout'):
        return '豆包登录已失效，请先正常登录对应的豆包界面，再按提交状态决定是否另开尝试；原始证据已保留，不会自动重发。'
    if reason == 'human_verification_required':
        return '豆包需要正常安全验证；本次请求和证据已保留，请核对提交状态，不会自动重发。'
    if reason == 'client_mode_mismatch':
        return '豆包当前界面与本次选择不一致，请核对豆包对话或豆包工作；不会自动切换或重发。'
    return '豆包窗口或当前请求已中断，请先核对提交状态；原始证据已保留，不会自动重发。'
