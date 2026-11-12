"""Hash an officially visible Doubao handle; never read browser storage.

The native observer must supply only the visible account Button in the official
Document's ``设置分类`` group. This pure helper neither discovers that control nor
authenticates arbitrary text. Do not expose it as a frontend/import assertion API.
The handle is an observed account identifier, not a proven immutable numeric UID.
"""
from __future__ import annotations

import hashlib
import copy
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urlsplit


HASH_PREFIX = 'doubao-visible-handle:v1:'
IDENTIFIER_KIND = 'official_visible_handle'
_SCHEMA = 'doubao_visible_handle_observation_v1'
_ID = re.compile(r'[a-f0-9]{32}')
_SHA = re.compile(r'[a-f0-9]{64}')
_LABEL = re.compile(r'(?:^|\s)豆包号\s*[:：]\s*(user_[A-Za-z0-9]{1,128})\s*\Z')
_STAGES = {'login_observation', 'before_submission', 'after_answer'}
_NATIVE_OBSERVATION = object()


class AccountObservedResult(dict):
    """Native collector payload whose opaque observations never enter JSON.

    Only the collector creates this in memory. A JSON round-trip deliberately
    loses its capability; frontend/import mappings must never be rewrapped.
    """
    def __init__(self, *args, account_observations=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.account_observations = account_observations


def _official_chat_origin(url):
    if not isinstance(url, str) or len(url) > 4096 or any(ord(char) < 32 for char in url):
        return None
    try:
        value = urlsplit(url)
        if (value.scheme != 'https' or value.hostname not in ('doubao.com', 'www.doubao.com')
                or value.username is not None or value.password is not None
                or value.port not in (None, 443)
                or not (value.path == '/chat' or value.path.startswith('/chat/'))):
            return None
    except ValueError:
        return None
    return 'https://' + value.hostname


def _scope(session_slot, context_id, profile_scope_sha256, probe_id, stage):
    if (not isinstance(session_slot, str) or session_slot not in ('primary', 'secondary')
            or not isinstance(stage, str) or stage not in _STAGES
            or not isinstance(context_id, str) or not _ID.fullmatch(context_id)
            or not isinstance(profile_scope_sha256, str) or not _SHA.fullmatch(profile_scope_sha256)
            or (stage == 'login_observation' and probe_id is not None)
            or (stage != 'login_observation' and (not isinstance(probe_id, str) or not _ID.fullmatch(probe_id)))):
        raise ValueError('账号观察的原生会话范围无效')


@dataclass(frozen=True)
class _VisibleHandleObservation:
    handle_sha256: str
    official_origin: str
    session_slot: str
    context_id: str
    profile_scope_sha256: str
    probe_id: str | None
    stage: str
    observed_at: str
    _native: object = field(repr=False, compare=False)

    def safe_record(self):
        """No raw label/handle, nickname, phone, chat URL, or browser paths."""
        return {
            'schema_version': _SCHEMA, 'identifier_kind': IDENTIFIER_KIND,
            'identifier_stability': 'not_established', 'hash_scheme': HASH_PREFIX,
            'handle_sha256': self.handle_sha256, 'official_origin': self.official_origin,
            'surface': 'settings_categories_account_button', 'session_slot': self.session_slot,
            'context_id': self.context_id, 'profile_scope_sha256': self.profile_scope_sha256,
            'probe_id': self.probe_id, 'stage': self.stage, 'observed_at': self.observed_at,
        }


def observe_visible_handle(label_text, *, page_url, settings_group_name, control_role,
                           visible, session_slot, context_id, profile_scope_sha256,
                           stage='login_observation', probe_id=None, observed_at=None):
    """Return an opaque native observation or None for absent/ambiguous UI proof.

    Call only after the native page observer has verified the official Document,
    visible settings group and Button. Raw text stays in this call's memory.
    The case-sensitive handle is hashed exactly, without casefolding or trimming.
    No exception message includes any supplied text or URL.
    """
    _scope(session_slot, context_id, profile_scope_sha256, probe_id, stage)
    origin = _official_chat_origin(page_url)
    if (origin is None or settings_group_name != '设置分类' or control_role != 'button'
            or visible is not True or not isinstance(label_text, str)
            or len(label_text) > 512 or label_text.count('豆包号') != 1):
        return None
    match = _LABEL.search(label_text)
    if not match:
        return None
    stamp = observed_at if observed_at is not None else datetime.now(timezone.utc)
    if not isinstance(stamp, datetime) or stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError('账号观察时间必须包含时区')
    # Preserve every character of the captured identifier. Label whitespace is
    # outside the capture group; it is not part of the official handle.
    digest = hashlib.sha256((HASH_PREFIX + match.group(1)).encode('utf-8')).hexdigest()
    return _VisibleHandleObservation(digest, origin, session_slot, context_id,
        profile_scope_sha256, probe_id, stage, stamp.astimezone(timezone.utc).isoformat(),
        _NATIVE_OBSERVATION)


def _native(value):
    return type(value) is _VisibleHandleObservation and value._native is _NATIVE_OBSERVATION


def bind_probe_handle(before, after, *, probe_id, session_slot, context_id,
                      profile_scope_sha256, source):
    """Bind two current-context observations to one new native probe.

    A deserialized dict, imported result, prior login observation or another
    context cannot establish this binding. The caller must persist this safe
    record with that probe's original collector evidence, not patch older probes.
    A missing/mismatched observation raises a safe ValueError; answering may
    continue with account_identity_status=unverified.
    """
    _scope(session_slot, context_id, profile_scope_sha256, probe_id, 'before_submission')
    if source != 'native_collector' or not _native(before) or not _native(after):
        raise ValueError('本题缺少可绑定的原生账号观察')
    expected = (probe_id, session_slot, context_id, profile_scope_sha256)
    if (any((value.probe_id, value.session_slot, value.context_id, value.profile_scope_sha256) != expected
            for value in (before, after))
            or before.stage != 'before_submission' or after.stage != 'after_answer'
            or before.handle_sha256 != after.handle_sha256
            or datetime.fromisoformat(before.observed_at) > datetime.fromisoformat(after.observed_at)):
        raise ValueError('本题账号观察发生变化或范围不一致')
    return {
        'schema_version': 'doubao_probe_visible_handle_v1',
        'account_identity_status': 'official_visible_handle_observed',
        'identifier_kind': IDENTIFIER_KIND, 'identifier_stability': 'not_established',
        'handle_sha256': before.handle_sha256, 'hash_scheme': HASH_PREFIX,
        'probe_id': probe_id, 'session_slot': session_slot, 'context_id': context_id,
        'profile_scope_sha256': profile_scope_sha256,
        'observations': [before.safe_record(), after.safe_record()],
    }


def _result_observations(result, *, probe_id, session_slot):
    if type(result) is not AccountObservedResult:
        return None
    environment = result.get('environment')
    values = result.account_observations
    if (not isinstance(environment, dict) or environment.get('client_mode') != 'consumer'
            or environment.get('session_slot') != session_slot
            or type(values) is not tuple or len(values) != 2
            or not all(_native(value) for value in values)):
        return None
    before, after = values
    try:
        _scope(session_slot, before.context_id, before.profile_scope_sha256, probe_id, 'before_submission')
        expected = (probe_id, session_slot, before.context_id, before.profile_scope_sha256)
        if (any((value.probe_id, value.session_slot, value.context_id, value.profile_scope_sha256) != expected
                for value in values) or before.stage != 'before_submission' or after.stage != 'after_answer'
                or datetime.fromisoformat(before.observed_at) > datetime.fromisoformat(after.observed_at)):
            return None
    except (TypeError, ValueError):
        return None
    return before, after


def bound_result_handle(result, *, probe_id, session_slot):
    """Accept only exact, recomputable safe JSON plus this native result capability.

    Never reconstruct observations from persisted/imported dictionaries. The
    service must additionally require source=native_collector before calling.
    """
    values = _result_observations(result, probe_id=probe_id, session_slot=session_slot)
    if values is None:
        return None
    before, after = values
    try:
        bound = bind_probe_handle(before, after, probe_id=probe_id, session_slot=session_slot,
            context_id=before.context_id, profile_scope_sha256=before.profile_scope_sha256,
            source='native_collector')
    except (TypeError, ValueError):
        return None
    if (type(result.get('account_identity_evidence')) is not dict
            or result['account_identity_evidence'] != bound
            or result['environment'].get('account_identity_status') != bound['account_identity_status']):
        return None
    return copy.deepcopy(bound)


def result_handle_changed(result, *, probe_id, session_slot):
    """Explain an observed in-context change; it remains unverified identity."""
    values = _result_observations(result, probe_id=probe_id, session_slot=session_slot)
    return (values is not None
            and result['environment'].get('account_identity_status') == 'unverified'
            and result.get('account_identity_evidence') is None
            and values[0].handle_sha256 != values[1].handle_sha256)


def compare_visible_handles(left, right):
    """Compare current official observations, without granting GEO acceptance.

    Different login observations describe now; they cannot attest which account
    produced a historical answer. This intentionally returns no independence or
    GEO-success boolean and makes no permanence claim.
    """
    if not _native(left) or not _native(right):
        return {'identifier_kind': IDENTIFIER_KIND, 'relation': 'unverified',
                'identifier_stability': 'not_established'}
    return {'identifier_kind': IDENTIFIER_KIND,
            'relation': 'same' if left.handle_sha256 == right.handle_sha256 else 'different',
            'identifier_stability': 'not_established'}
