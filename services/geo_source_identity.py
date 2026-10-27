"""Conservative source-subject scope, never business or factual verification."""
from __future__ import annotations

import re
import unicodedata
from urllib.parse import urlsplit

from services.geo_test_service import canonical_entity_url


_LEGAL_NAME = re.compile(r'[\u3400-\u9fffA-Za-z0-9（）()·]{2,90}?(?:股份有限公司|有限责任公司|有限公司)')
_RELATION_WORDS = re.compile(r'母公司|子公司|控股|股东|关联公司|旗下公司|运营公司')


def _result(classification='unknown', basis='insufficient_evidence', scope='unconfirmed', name=None):
    return {'classification': classification, 'basis': basis, 'evidence_scope': scope, 'matched_name': name}


def _url(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = urlsplit(value)
        if parsed.username or parsed.password:
            return None
        return canonical_entity_url(parsed._replace(query='', fragment='').geturl())
    except (TypeError, ValueError):
        return None


def _names(title):
    return list(dict.fromkeys(match.group() for match in _LEGAL_NAME.finditer(title)))


def unconfirmed_source_url_status(source):
    """Retain explicit negative URL evidence through editorial projections."""
    if not isinstance(source, dict):
        return None
    for key in ('url_status', 'status'):
        value = source.get(key)
        if isinstance(value, str) and (value.casefold() in ('not_exposed', 'url_unknown')
                                      or value.casefold().startswith('unconfirmed')):
            return value
    return None


def _confirmed_relation(profile, name, relations):
    # This is an explicit domain input, not a claim read from an AI answer or
    # profile.company. Current stored profiles have no confirmed relation list.
    for relation in relations if isinstance(relations, (list, tuple)) else ():
        if (isinstance(relation, dict) and relation.get('confirmed') is True
                and relation.get('target_name') == profile.get('name')
                and relation.get('legal_name') == name
                and isinstance(relation.get('relationship'), str) and relation['relationship'].strip()
                and _url(relation.get('evidence_url')) is not None):
            return True
    return False


def classify_source_identity(profile, source, *, confirmed_relations=()):
    """Classify only a configured host or explicit Chinese legal-name title.

    Short aliases, homophones and profile.company do not establish identity or
    a relationship. A title-only result never verifies the linked page/body.
    Unsupported legal-name formats and ambiguous titles stay unknown.
    """
    if not isinstance(profile, dict) or not isinstance(source, dict):
        return _result()
    title = source.get('title') if isinstance(source.get('title'), str) else ''
    names = _names(title)
    if len(names) > 1:
        return _result(basis='multiple_legal_names_title', scope='title_identity_only')
    target = profile.get('name') if isinstance(profile.get('name'), str) else ''
    company_target = profile.get('entity_type') == 'company'
    legal_target = target if _LEGAL_NAME.fullmatch(target) else None
    actual = _url(source.get('url'))
    if unconfirmed_source_url_status(source) is not None:
        actual = None
    legal_name = names[0] if names else None
    if legal_name:
        if _confirmed_relation(profile, legal_name, confirmed_relations):
            return _result('related', 'confirmed_relationship', 'confirmed_relation_identity_only', legal_name)
        if not company_target:
            # A company's registry entry cannot establish a product's scope,
            # even when the unconfirmed operator happens to be named in it.
            return _result(basis='company_title_not_product', scope='title_identity_only')
        if legal_target and legal_name != legal_target:
            if unicodedata.normalize('NFKC', legal_name) == unicodedata.normalize('NFKC', legal_target):
                return _result(basis='ambiguous_legal_name_title', scope='title_identity_only')
            if _RELATION_WORDS.search(title):
                return _result(basis='unconfirmed_relationship', scope='title_identity_only')
            if legal_name.endswith(legal_target):
                return _result(basis='ambiguous_legal_name_title', scope='title_identity_only')
            return _result('other', 'different_legal_name_title', 'title_identity_only', legal_name)
        if not legal_target:
            return _result(basis='target_legal_name_unconfirmed', scope='title_identity_only')
    if actual is None:
        return _result(basis='source_url_unconfirmed')
    scopes = [_url(value) for value in [profile.get('website'), *(profile.get('official_urls') or [])]]
    for allowed in scopes:
        if allowed is None or actual[:3] != allowed[:3]:
            continue
        if company_target:
            return _result('target', 'exact_official_host', 'official_host', target or None)
        # Products sharing a company host remain separate. A configured root
        # alone establishes only that exact page, not every sibling product.
        if actual[:3] == allowed[:3] and (actual[3] == allowed[3]
                or allowed[3] != '/' and actual[3].startswith(allowed[3] + '/')):
            return _result('target', 'exact_official_host', 'official_page_scope', target or None)
    if any(allowed is not None and actual[1] == allowed[1] for allowed in scopes):
        return _result(basis='official_authority_or_scope_mismatch')
    if company_target and legal_target and legal_name == legal_target:
        return _result('target', 'exact_legal_name_title', 'title_identity_only', legal_target)
    return _result()
