"""Bounded checks of already-configured official pages, separate from GEO results.

State lives under geo_webmaster_records.record.website_audit; existing sites,
baseline, facts and probes are never rewritten. Network runs outside SQL locks.
"""
from __future__ import annotations

import copy
import hashlib
import http.client
import json
import re
import time
from contextlib import closing
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urljoin, urlsplit

from services import webmaster_service as public_http
from services.geo_test_service import canonical_entity_url
from services.geo_website_html import inspect_html


MAX_PAGES = 5
MAX_BYTES = 512 * 1024
MAX_REDIRECTS = 2
TOTAL_SECONDS = 45
PENDING_SECONDS = 75
HISTORY_LIMIT = 5
LIMITATIONS = [
    '只检查登记官网的服务端HTTP与HTML，不执行JavaScript或自动抓取发现的新链接。',
    'HTTP200、名称字面出现、canonical或JSON-LD均不证明主体业务真实性、索引、AI采用或GEO成功。',
    'robots规则与页面元数据只是本次观察；真实爬虫访问、适用规则和搜索平台状态仍需分别核对。',
]


def _now():
    return datetime.now(timezone.utc).isoformat()


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _official_url(value):
    identity = canonical_entity_url(value)
    if identity is None or identity[0] != 'https' or identity[2] != 443:
        raise ValueError('仅检测已登记且使用默认端口的HTTPS官方地址')
    origin = public_http._origin(value)
    path = quote(urlsplit(value).path or '/', safe="/%:@!$&'()*+,;=-._~")
    result = origin + path
    if len(result) > 2000 or path.startswith('//'):
        raise ValueError('官方页面地址不满足当前检查范围')
    return result


def _pages(cycle):
    profile = cycle['profile']
    configured = [profile.get('website'), *(profile.get('official_urls') or [])]
    pages, seen, unsupported = [], set(), 0
    for value in configured:
        if not value:
            continue
        try:
            url = _official_url(value)
        except (ValueError, TypeError):
            unsupported += 1
            continue
        identity = canonical_entity_url(url)
        if identity in seen:
            continue
        seen.add(identity)
        pages.append({'id': _digest({'url': url})[:24], 'url': url, 'basis': 'configured_official_url'})
    return pages[:11], unsupported


def _context(cycle, pages):
    return _digest({'cycle_id': cycle['id'], 'project_id': cycle['project_id'], 'revision': cycle['revision'],
        'plan_sha256': cycle['test_plan']['plan_sha256'], 'entity_type': cycle['profile'].get('entity_type'),
        'name': cycle['profile'].get('name'), 'website': cycle['profile'].get('website'),
        'official_urls': cycle['profile'].get('official_urls') or [], 'pages': pages})


def _state(stored):
    value = stored.get('website_audit', {'revision': 0, 'history': []})
    if (not isinstance(value, dict) or type(value.get('revision')) is not int or value['revision'] < 0
            or not isinstance(value.get('history'), list) or len(value['history']) > HISTORY_LIMIT
            or any(not isinstance(item, dict) for item in value['history'])):
        raise ValueError('官网检测历史格式异常，已保留原数据，未覆盖')
    return copy.deepcopy(value)


def _expired(record):
    try:
        expires = datetime.fromisoformat(record['expires_at'])
        return expires.tzinfo is None or datetime.fromisoformat(_now()) >= expires
    except (KeyError, TypeError, ValueError):
        return True


def overview(connection, cycle):
    pages, unsupported = _pages(cycle)
    context_sha = _context(cycle, pages)
    state = _state(public_http._stored(connection, cycle['id']))
    history = copy.deepcopy(state['history'])
    for record in history:
        record['current_scope'] = record.get('context_sha256') == context_sha and record.get('status') != 'stale_context'
        if record.get('status') == 'running' and _expired(record):
            record.update(status='interrupted', message='原检测未在限定时间内返回；结果未知，明确再次检测才会新建检查。')
    return {'supported': bool(pages), 'reason': '' if pages else '本轮没有满足公网HTTPS登记范围的官方页面；HTTP地址不会自动升级或请求。',
        'context_sha256': context_sha, 'audit_revision': state['revision'], 'allowed_pages': pages,
        'unsupported_configured_url_count': unsupported,
        'latest': history[-1] if history and history[-1]['current_scope'] else None,
        'history': list(reversed(history)),
        'limits': {'pages': MAX_PAGES, 'bytes_per_page': MAX_BYTES, 'redirects_per_page': MAX_REDIRECTS,
                   'total_seconds': TOTAL_SECONDS, 'pending_seconds': PENDING_SECONDS, 'history': HISTORY_LIMIT},
        'limitations': list(LIMITATIONS)}


def get_overview(database, cycle_id):
    from services.geo_content_store import _connect, _cycle
    with closing(_connect(database)) as connection:
        return overview(connection, _cycle(connection, cycle_id))


def _validate(cycle, view, payload):
    required = {'project_id', 'expected_plan_sha256', 'expected_revision',
                'expected_context_sha256', 'expected_audit_revision', 'page_ids'}
    if not isinstance(payload, dict) or set(payload) != required:
        raise ValueError('只接受当前官网范围和登记页面编号，不接受任意URL、路径或请求配置')
    project = payload['project_id']
    if (not ((type(project) is int and project > 0) or (project is None and cycle['project_id'] is None))
            or project != cycle['project_id'] or type(payload['expected_revision']) is not int
            or payload['expected_revision'] != cycle['revision']
            or payload['expected_plan_sha256'] != cycle['test_plan']['plan_sha256']
            or payload['expected_context_sha256'] != view['context_sha256']):
        raise ValueError('项目、原计划、资料版本或官网范围已变化，请刷新后核对')
    identifiers = payload['page_ids']
    allowed = {page['id'] for page in view['allowed_pages']}
    if (type(payload['expected_audit_revision']) is not int or payload['expected_audit_revision'] < 0
            or not isinstance(identifiers, list) or not 1 <= len(identifiers) <= MAX_PAGES
            or any(not isinstance(identifier, str) or identifier not in allowed for identifier in identifiers)
            or len(set(identifiers)) != len(identifiers)):
        raise ValueError('请选择本轮已登记的1到5个不同官方页面，并提供当前检测版本')


def _save(connection, cycle_id, stored, state):
    stored['website_audit'] = state
    connection.execute(public_http.CREATE_TABLE)
    connection.execute('INSERT OR REPLACE INTO geo_webmaster_records VALUES(?,?,?)',
        (cycle_id, json.dumps(stored, ensure_ascii=False), _now()))


def _reserve(database, cycle_id, payload):
    from services.geo_content_store import _connect, _cycle
    with closing(_connect(database)) as connection, connection:
        connection.execute('BEGIN IMMEDIATE')
        cycle = _cycle(connection, cycle_id, for_update=True)
        view = overview(connection, cycle)
        _validate(cycle, view, payload)
        stored = public_http._stored(connection, cycle_id)
        state = _state(stored)
        input_sha = _digest(payload)
        latest = state['history'][-1] if state['history'] else None
        expected = payload['expected_audit_revision']
        if latest and expected == state['revision'] - 1 and latest.get('input_sha256') == input_sha:
            return cycle, view, copy.deepcopy(latest), False
        if expected != state['revision']:
            raise ValueError('官网检测已更新，请检查同一次记录，未再次请求网站')
        if latest and latest.get('status') == 'running':
            if not _expired(latest):
                raise ValueError('上一次官网检测仍未返回，请检查原记录，未重复请求')
            latest.update(status='interrupted', message='原检测未在限定时间内返回，结果保持未知。')
        pages = {page['id']: page for page in view['allowed_pages']}
        started = _now()
        record = {'id': _digest({'cycle_id': cycle_id, 'input': input_sha})[:32], 'status': 'running',
            'cycle_id': cycle_id, 'project_id': cycle['project_id'], 'revision': state['revision'] + 1,
            'plan_sha256': cycle['test_plan']['plan_sha256'], 'cycle_revision': cycle['revision'],
            'context_sha256': view['context_sha256'], 'input_sha256': input_sha,
            'started_at': started, 'expires_at': (datetime.fromisoformat(started) + timedelta(seconds=PENDING_SECONDS)).isoformat(),
            'finished_at': None, 'pages': [], 'selected_page_ids': list(payload['page_ids']),
            'requested_urls': [pages[identifier]['url'] for identifier in payload['page_ids']],
            'evidence_source': 'software_public_http', 'index_status': 'unknown', 'ai_adoption': 'unknown',
            'message': '官网只读检测已预约；未提交搜索或修改网站。'}
        state['revision'] += 1
        state['history'] = [*state['history'], record][-HISTORY_LIMIT:]
        _save(connection, cycle_id, stored, state)
        return cycle, view, copy.deepcopy(record), True


def _page_result(page):
    return {'page_id': page['id'], 'requested_url': page['url'], 'final_url': None, 'status': 'not_checked',
        'http_status': None, 'redirects': [], 'bytes': None, 'sha256': None, 'title': None,
        'meta_description': None, 'canonical_urls': [], 'robots_noindex_observed': None,
        'name_text_match': None, 'json_ld': None, 'page_links': [], 'findings': []}


def _failure(result, error):
    if isinstance(error, TimeoutError):
        status = 'request_timeout'
    elif isinstance(error, ValueError):
        status = str(error) if str(error) in ('response_too_large', 'unsupported_content_encoding', 'html_structure_limit') else 'address_or_html_invalid'
    else:
        status = 'network_error'
    result['status'] = status
    result['findings'].append({'code': status, 'message': '本页未在安全限额内完成检查，未推断为部署、索引或采用成功。'})
    return result


def _inspect_page(page, allowed, configured_name, deadline):
    result, current, visited = _page_result(page), page['url'], set()
    identities = {canonical_entity_url(item['url']) for item in allowed}

    def configured(url):
        try:
            return canonical_entity_url(_official_url(url)) in identities
        except (ValueError, TypeError):
            return False

    for hop in range(MAX_REDIRECTS + 1):
        if deadline - time.monotonic() <= public_http.DNS_SECONDS + 0.1:
            result['status'] = 'not_checked_budget'
            return result
        visited.add(current)
        origin = public_http._origin(current)
        try:
            metadata, data = public_http._fetch_bounded(origin, urlsplit(current).path or '/', MAX_BYTES,
                accept='text/html,application/xhtml+xml', deadline=deadline, include_headers=True)
        except (ValueError, OSError, http.client.HTTPException) as error:
            return _failure(result, error)
        result.update(http_status=metadata['http_status'], final_url=current)
        if metadata['http_status'] in (301, 302, 303, 307, 308):
            destination = None
            try:
                destination = _official_url(urljoin(current, metadata.get('location') or ''))
            except (ValueError, TypeError):
                pass
            reason = ('redirect_blocked' if not destination or not configured(destination) or not metadata.get('location')
                      else 'redirect_loop' if destination in visited else 'redirect_limit' if hop == MAX_REDIRECTS else None)
            result['redirects'].append({'http_status': metadata['http_status'], 'from_url': current,
                'to_url': destination, 'followed': reason is None, 'reason': reason})
            if reason:
                result['status'] = reason
                result['findings'].append({'code': reason, 'message': '跳转超限、循环或不在已登记官方页面范围；未继续访问。'})
                return result
            current = destination
            continue
        if metadata['http_status'] != 200:
            result['status'] = 'http_not_ok'
            return result
        result.update(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
        if (metadata.get('content_type') or '').split(';', 1)[0].strip().lower() not in ('text/html', 'application/xhtml+xml'):
            result['status'] = 'non_html'
            return result
        try:
            result.update(inspect_html(data, metadata, current, configured_name, configured), status='html_observed')
        except ValueError as error:
            return _failure(result, error)
        return result
    return result


def inspect_pages(cycle, view, reservation):
    selected = set(reservation['selected_page_ids'])
    pages = [page for page in view['allowed_pages'] if page['id'] in selected]
    deadline = time.monotonic() + TOTAL_SECONDS
    results = []
    for page in pages:
        try:
            results.append(_inspect_page(page, view['allowed_pages'], cycle['profile'].get('name'), deadline))
        except (ValueError, OSError, http.client.HTTPException) as error:
            results.append(_failure(_page_result(page), error))
    return results


def perform_audit(database, cycle_id, payload):
    from services.geo_content_store import _connect, _cycle
    cycle, view, reservation, created = _reserve(database, cycle_id, payload)
    if not created:
        current = get_overview(database, cycle_id)
        returned = next((item for item in current['history'] if item['id'] == reservation['id']), reservation)
        return {'audit': returned, 'overview': current}
    try:
        pages = inspect_pages(cycle, view, reservation)
        observed = sum(page['status'] == 'html_observed' for page in pages)
        status = 'completed' if observed == len(pages) and pages else 'completed_with_gaps' if observed else 'failed'
        result = {**reservation, 'pages': pages, 'status': status, 'finished_at': _now(),
                  'message': '已保存本次HTTP与HTML观察；索引和AI采用仍未知。'}
    except Exception:
        result = {**reservation, 'status': 'failed', 'finished_at': _now(),
                  'message': '本次检测未安全完成，结果保持未知；未自动重试。'}
    with closing(_connect(database)) as connection, connection:
        connection.execute('BEGIN IMMEDIATE')
        fresh_cycle = _cycle(connection, cycle_id, for_update=True)
        fresh = overview(connection, fresh_cycle)
        stored = public_http._stored(connection, cycle_id)
        state = _state(stored)
        matches = [index for index, item in enumerate(state['history']) if item.get('id') == reservation['id']]
        if len(matches) != 1:
            raise ValueError('原检测预约已不在有限历史中，未覆盖新结果，请检查当前记录')
        if (fresh['context_sha256'] != reservation['context_sha256'] or state['revision'] != reservation['revision']
                or state['history'][-1].get('id') != reservation['id']):
            result.update(status='stale_context', message='已保存原范围检测收据；资料或当前检测已变化，不标作当前结果。')
        state['history'][matches[0]] = result
        _save(connection, cycle_id, stored, state)
    current = get_overview(database, cycle_id)
    returned = next((item for item in current['history'] if item['id'] == result['id']), {**result, 'current_scope': False})
    return {'audit': returned, 'overview': current}
