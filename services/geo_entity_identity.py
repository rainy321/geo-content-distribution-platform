"""Bind editorial work to an entity and its actual page, not a shared domain."""
from __future__ import annotations

from collections.abc import Mapping

from services.geo_test_service import canonical_entity_url


def _name(value):
    return str(value or '').strip().casefold()


def same_entity_target(left, right):
    if not isinstance(left, Mapping) or not isinstance(right, Mapping):
        return False
    if not _name(left.get('name')) or _name(left.get('name')) != _name(right.get('name')):
        return False
    if left.get('entity_type', 'product') != right.get('entity_type', 'product'):
        return False
    left_url, right_url = left.get('website') or '', right.get('website') or ''
    if not left_url and not right_url:
        return True
    first, second = canonical_entity_url(left_url), canonical_entity_url(right_url)
    return first is not None and first == second


def entity_measurement_signature(profile):
    """Include the measurement boundary, keeping legacy scope explicitly distinct."""
    strict = 'entity_type' in profile
    scopes = [profile.get('website') or '']
    if strict:
        scopes.extend(profile.get('official_urls') or [])
    normalized = []
    for url in scopes:
        if not url:
            continue
        canonical = canonical_entity_url(url)
        if canonical is None:
            raise ValueError('测试对象的官网范围无效，请重新生成计划')
        normalized.append(canonical)
    return {'semantics': 'target_entity_scoped_urls' if strict else 'legacy_mixed_entity_hostwide',
            'entity_type': profile.get('entity_type', 'product'), 'name': _name(profile.get('name')),
            'aliases': sorted({_name(value) for value in profile.get('aliases') or []}),
            'official_scopes': sorted(set(normalized)),
            'legacy_product': _name(profile.get('product')) if not strict else '',
            'legacy_company': _name(profile.get('company')) if not strict else ''}
