"""Explicit, bounded IndexNow notifications for the current owned website.

Protocol: https://www.indexnow.org/documentation . A receipt is transport
evidence only. Neither this module nor its GET view writes GEO measurements.
Public reads reuse webmaster_service's pinned, anonymous HTTPS transport.
"""
from __future__ import annotations

import copy
import hashlib
import http.client
import json
import re
import socket
import threading
import time
import uuid
from contextlib import closing
from datetime import datetime, timedelta, timezone
from email.message import Message
from html.parser import HTMLParser
from urllib.parse import quote, unquote, urlsplit

from services import webmaster_service as public_http
from services.geo_entity_identity import same_entity_target
from services.geo_external_baseline_contract import profile_for_project, profile_sha256, validate_request_id
from services.geo_source_url import trusted_source_url
from services.geo_website_html import inspect_html


ENDPOINT = 'https://api.indexnow.org/indexnow'
MAX_URLS = 3
MAX_PAGE_BYTES = 512 * 1024
PREVIEW_SECONDS = 600
TOTAL_SECONDS = 42
HISTORY_LIMIT = 15
_BLOCKING = frozenset(('reserved', 'received', 'key_pending', 'delivery_unknown'))
_PRIVATE_SEGMENTS = frozenset(('login', 'signin', 'sign-in', 'sign_in', 'signup', 'sign-up',
    'register', 'logout', 'admin', 'account', 'accounts', 'settings', 'console', 'dashboard',
    'editor', 'edit', 'write', 'oauth', 'authorize', 'callback', 'session'))
LIMITATIONS = [
    '只在本次点击后通知已登记官网范围内的公开HTML页面；不自动后台提交，不操作搜索平台登录。',
    'HTTP 200只代表通知已收到；202代表密钥仍待核验，不表示已经抓取、更新索引或豆包采用。',
    '本次记录不计入GEO效果；索引版本、自然搜索及普通豆包同题结果仍需各自取得证据。',
    '没有公开密钥文件时需网站维护者先部署；不读取Cookie、API Key、密码或客户内部文件。',
    '相同页面内容已收到、待核验或发送结果未知时不重复通知；明确拒绝后只能显式重试。',
]

_PREVIEWS = '''CREATE TABLE IF NOT EXISTS geo_indexnow_previews (
    id TEXT PRIMARY KEY, cycle_id TEXT NOT NULL, project_id INTEGER NOT NULL,
    context_sha256 TEXT NOT NULL, created_at TEXT NOT NULL, expires_at TEXT NOT NULL,
    record TEXT NOT NULL)'''
_NOTIFICATIONS = '''CREATE TABLE IF NOT EXISTS geo_indexnow_notifications (
    id TEXT PRIMARY KEY, cycle_id TEXT NOT NULL, project_id INTEGER NOT NULL,
    request_id TEXT NOT NULL UNIQUE, request_sha256 TEXT NOT NULL,
    dedup_scope TEXT NOT NULL, body_sha256 TEXT NOT NULL, content_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL, status TEXT NOT NULL, record TEXT NOT NULL)'''


def _now():
    return datetime.now(timezone.utc)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _digest(value):
    return hashlib.sha256(_json(value).encode('utf-8')).hexdigest()


def _budget(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError('operation_deadline')
    return remaining


def _connect_before(database, deadline):
    from services.geo_content_store import _connect
    remaining = _budget(deadline)
    connection = _connect(database)
    # SQL lock waits share the same budget as public checks and the POST.
    connection.execute('PRAGMA busy_timeout=' + str(max(1, int(remaining * 1000))))
    return connection


def _public_url(value):
    if not isinstance(value, str) or len(value) > 2000 or '?' in value or '#' in value:
        raise ValueError('请使用不含查询参数或锚点的HTTPS公开规范网址')
    canonical = trusted_source_url(value)
    if canonical is None or canonical != value or not value.isascii():
        raise ValueError('请使用公开域名的HTTPS规范网址，不接受IP、凭据、内部地址或编码穿越')
    parsed = urlsplit(canonical)
    if parsed.path.startswith('//') or '//' in parsed.path or ';' in unquote(parsed.path):
        raise ValueError('页面路径不接受双斜线或歧义编码')
    # Equivalent percent encodings must not create fresh notification identities.
    # Reject aliases rather than silently converting an operator's selected URL.
    # UTF-8 escapes use uppercase hex; unreserved characters stay literal.
    if quote(unquote(parsed.path, encoding='utf-8', errors='strict'), safe='/-._~') != parsed.path:
        raise ValueError('页面路径需使用规范UTF-8编码和大写百分号转义，不能用编码别名重复通知')
    return canonical


def _profile_url(value):
    # Host-only project inputs are common; scope canonicalization may add the
    # slash, while caller-selected notification URLs must already be canonical.
    if not isinstance(value, str) or '?' in value or '#' in value:
        raise ValueError('品牌官网需为不含参数的公开HTTPS地址')
    return _public_url(trusted_source_url(value))


def _profiles(connection, cycle):
    website = _profile_url(cycle['profile'].get('website'))
    project_id = cycle['project_id']
    if type(project_id) is not int or project_id <= 0:
        raise ValueError('请先保存品牌项目，再通知该项目的官网更新')
    row = connection.execute('SELECT * FROM projects WHERE id=?', (project_id,)).fetchone()
    if row is None:
        raise ValueError('当前品牌项目已不存在，旧记录仍保留')
    current = profile_for_project(dict(row))
    if not same_entity_target(current, cycle['profile']):
        raise ValueError('当前品牌的名称、对象类型或官网范围已变化，请切换到对应项目轮次')
    # Revalidate the currently saved website too; normalization alone must not
    # make a malformed current project eligible for a frozen old website.
    _profile_url(current.get('website'))
    return website, current


def _asset(value, project_id, *, primary=False):
    parsed = urlsplit(_profile_url(value))
    origin, path = 'https://' + parsed.netloc, parsed.path.rstrip('/') or '/'
    return {'id': _digest({'project_id': project_id, 'site_origin': origin, 'path': path}),
            'url': origin + path, 'site_origin': origin,
            'page_scope': origin + (path.rstrip('/') + '/' if path != '/' else '/'),
            'primary': primary, 'basis': 'frozen_website' if primary else 'configured_official_url',
            'control_status': 'not_checked'}


def _validate_asset_id(asset_id):
    if asset_id is not None and (not isinstance(asset_id, str) or not re.fullmatch('[0-9a-f]{64}', asset_id)):
        raise ValueError('官网资产编号无效，请从本轮已登记地址重新选择')


def _assets(cycle, current, website):
    """Configured addresses are candidates, never ownership evidence.

    A secondary scope must still exist at exactly the same canonical host/path
    in both profiles. A removed root cannot be retained through a narrower URL.
    """
    project_id = cycle['project_id']
    primary = _asset(website, project_id, primary=True)
    result, seen = [primary], {primary['id']}
    active = set()
    for value in current.get('official_urls') or []:
        try:
            active.add(_asset(value, project_id)['id'])
        except (ValueError, TypeError, UnicodeError):
            continue
    for value in cycle['profile'].get('official_urls') or []:
        try:
            item = _asset(value, project_id)
        except (ValueError, TypeError, UnicodeError):
            continue
        if item['id'] in active and item['id'] not in seen:
            result.append(item)
            seen.add(item['id'])
    return result


def _scope(connection, cycle, asset_id=None):
    website, current = _profiles(connection, cycle)
    assets = _assets(cycle, current, website)
    selected = assets[0]
    if asset_id is not None:
        _validate_asset_id(asset_id)
        selected = next((item for item in assets if item['id'] == asset_id), None)
        if selected is None:
            raise ValueError('该官网地址不在冻结资料与当前项目的共同登记范围内，旧记录保留')
        website = selected['url']
    project_id = cycle['project_id']
    parsed = urlsplit(website)
    base = parsed.path.rstrip('/')
    identity = {'cycle_id': cycle['id'], 'project_id': project_id,
        'plan_sha256': cycle['test_plan']['plan_sha256'],
        'frozen_profile_sha256': profile_sha256(cycle['profile']),
        'current_profile_sha256': profile_sha256(current),
        'site_origin': 'https://' + parsed.netloc, 'path': base or '/'}
    # The original primary identity and dedup remain byte-for-byte compatible.
    if not selected['primary']:
        identity.update(asset_id=selected['id'], registered_url=selected['url'])
    # Stable across cycles and nonidentity edits: changing a description, key or
    # batch grouping must not reset delivery-unknown or accepted content history.
    dedup = {'project_id': project_id, 'site_origin': identity['site_origin'],
             'path': identity['path'], 'name': current['name'].strip().casefold(),
             'entity_type': current.get('entity_type', 'product')}
    return {**identity, 'context_sha256': _digest(identity), 'dedup_scope': _digest(dedup),
            'content_entity_sha256': _digest({key: dedup[key] for key in ('project_id', 'name', 'entity_type')}),
            'page_scope': identity['site_origin'] + (base + '/' if base else '/')}


def _scope_metadata(scope):
    return {'asset_id': _digest({'project_id': scope['project_id'],
                                'site_origin': scope['site_origin'], 'path': scope['path']}),
            'site_origin': scope['site_origin'], 'page_scope': scope['page_scope']}


def _inside(url, scope):
    parsed = urlsplit(_public_url(url))
    if 'https://' + parsed.netloc != scope['site_origin']:
        raise ValueError('只接受本轮官网同域页面，不能通知其他网站或子域')
    base = scope['path'].rstrip('/')
    if base and not (parsed.path == base or parsed.path.startswith(base + '/')):
        raise ValueError('页面必须在本轮产品官网路径范围内，不能扩展到兄弟产品或整站')
    return parsed.path


def _selection(scope, key_location, urls):
    key_path = _inside(key_location, scope)
    match = re.fullmatch(r'([A-Za-z0-9-]{8,128})\.txt', key_path.rsplit('/', 1)[-1])
    if match is None:
        raise ValueError('密钥文件名需为8到128位字母数字或连字符组成的密钥加.txt')
    if not isinstance(urls, list) or not 1 <= len(urls) <= MAX_URLS or len(set(map(str, urls))) != len(urls):
        raise ValueError('每次请选择1到3个互不重复的公开HTML页面')
    directory = key_path.rsplit('/', 1)[0] + '/'
    for url in urls:
        path = _inside(url, scope)
        if not path.startswith(directory):
            raise ValueError('密钥文件目录不能覆盖所选页面，请使用覆盖这些页面的同范围密钥')
        segments = unquote(path).casefold().split('/')
        if any(segment in _PRIVATE_SEGMENTS for segment in segments) or path == key_path:
            raise ValueError('不能通知登录、账户、后台、编辑页或密钥文件')
    return match.group(1), sorted(urls)


class _LoginPage(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.password = False
        self.title = []
        self.in_title = False

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        if tag == 'title':
            self.in_title = True
        if tag == 'input' and (attrs.get('type') or '').casefold() == 'password':
            self.password = True

    def handle_endtag(self, tag):
        if tag == 'title':
            self.in_title = False

    def handle_data(self, data):
        if self.in_title:
            self.title.append(data)


def _fetch(url, maximum, deadline, *, html=False):
    if _budget(deadline) < public_http.DNS_SECONDS:
        raise TimeoutError('operation_deadline')
    parsed = urlsplit(url)
    metadata, raw = public_http._fetch_bounded('https://' + parsed.netloc, parsed.path, maximum,
        accept='text/html,application/xhtml+xml' if html else 'application/xml,text/xml',
        include_headers=True, deadline=deadline)
    _budget(deadline)
    if metadata.get('http_status') != 200 or not raw:
        raise ValueError('匿名预检未取得HTTP 200正文（状态：' + str(metadata.get('http_status')) + '），未发送通知')
    if metadata.get('location') or metadata.get('location_truncated'):
        raise ValueError('公开预检遇到跳转指示，未发送通知')
    if metadata.get('content_type_truncated'):
        raise ValueError('公开响应类型无法完整核对，未发送通知')
    return metadata, raw


def _check_public(scope, key_location, urls, deadline):
    key, urls = _selection(scope, key_location, urls)
    meta, raw = _fetch(key_location, 512, deadline)
    if meta.get('content_type', '').split(';', 1)[0].strip().lower() != 'text/plain' or raw != key.encode('ascii'):
        raise ValueError('密钥文件需匿名返回text/plain且正文仅包含文件名中的密钥，未发送通知')
    pages = []
    for url in urls:
        metadata, data = _fetch(url, MAX_PAGE_BYTES, deadline, html=True)
        if metadata.get('content_type', '').split(';', 1)[0].strip().lower() not in ('text/html', 'application/xhtml+xml'):
            raise ValueError('所选地址不是公开HTML页面，未发送通知')
        observed = inspect_html(data, metadata, url, '', lambda candidate: candidate == url)
        if observed['robots_noindex_observed'] is not False or observed['decoding_warning']:
            raise ValueError('页面有noindex或编码/索引指令不确定，未发送通知')
        message = Message()
        message['content-type'] = metadata.get('content_type', '')
        parser = _LoginPage()
        parser.feed(data.decode(message.get_content_charset() or 'utf-8'))
        parser.close()
        title = ''.join(parser.title).strip()
        if (not observed['body_text_characters'] or parser.password
                or re.search(r'(?:^|[\s|｜-])(?:log[ -]?in|sign[ -]?in|登录|登陆|身份验证)(?:$|[\s|｜-])', title, re.I)):
            raise ValueError('页面为空或可能是登录/验证页面，未发送通知')
        pages.append({'url': url, 'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)})
    return key, pages


def _has_table(connection, table):
    return connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def _records(connection, table, *, scope=None, cycle_id=None, limit=None):
    if not _has_table(connection, table):
        return []
    where, args = ('dedup_scope=?', (scope,)) if scope is not None else ('cycle_id=?', (cycle_id,))
    sql = 'SELECT record FROM ' + table + ' WHERE ' + where + ' ORDER BY created_at DESC,id DESC'
    if limit is not None:
        sql += ' LIMIT ?'
        args += (limit,)
    return [json.loads(row['record']) for row in connection.execute(sql, args)]


def _content_records(connection, scope):
    table = 'geo_indexnow_notifications'
    if not _has_table(connection, table):
        return []
    # Existing dedup hashes stay intact. Reconcile URL/content across registered
    # paths using each original cycle's frozen entity, including legacy receipts
    # and roots removed in a later cycle. This never grants a send scope.
    rows = connection.execute('SELECT cycle_id,record FROM ' + table +
        ' WHERE project_id=? ORDER BY created_at DESC,id DESC', (scope['project_id'],))
    candidates = []
    for row in rows:
        record = json.loads(row['record'])
        parsed = urlsplit(_public_url(record['key_location']))
        if 'https://' + parsed.netloc == scope['site_origin']:
            candidates.append((row['cycle_id'], record))
    identities = {}
    cycle_ids = sorted({identifier for identifier, _ in candidates})
    # One profile per distinct historical cycle, in bounded SQL batches; never
    # one SQL lookup or report projection per notification row.
    for start in range(0, len(cycle_ids), 100):
        batch = cycle_ids[start:start + 100]
        sql = 'SELECT id,baseline FROM geo_content_cycles WHERE project_id=? AND id IN (' + ','.join('?' for _ in batch) + ')'
        for row in connection.execute(sql, [scope['project_id'], *batch]):
            profile = json.loads(row['baseline'])['profile']
            if not isinstance(profile.get('name'), str) or not profile['name'].strip():
                raise ValueError('历史通知的冻结对象范围无法核对，未发送通知')
            identities[row['id']] = _digest({'project_id': scope['project_id'],
                'name': profile['name'].strip().casefold(), 'entity_type': profile.get('entity_type', 'product')})
    if len(identities) != len(cycle_ids):
        raise ValueError('历史通知的冻结对象记录缺失，未发送通知')
    return [record for identifier, record in candidates if identities[identifier] == scope['content_entity_sha256']]


def _blocked(connection, scope, pages):
    prior = _content_records(connection, scope)
    blocked, changed = [], []
    for page in pages:
        match = next((record for record in prior if record['status'] in _BLOCKING
            and any(old['url'] == page['url'] and old['sha256'] == page['sha256'] for old in record['pages'])), None)
        if match:
            blocked.append({'url': page['url'], 'status': match['status'], 'receipt_id': match['id'],
                            'reason': '相同页面内容已有通知或结果未知，未重复发送'})
        else:
            changed.append(page['url'])
    return changed, blocked


def _retry_record(connection, scope, pages):
    """A rejected receipt cannot be bypassed by reordering/regrouping its pages."""
    selected = {(item['url'], item['sha256']) for item in pages}
    matches = {}
    for record in _content_records(connection, scope):
        if record['status'] != 'rejected':
            continue
        for page in record['pages']:
            key = (page['url'], page['sha256'])
            if key in selected and key not in matches:
                matches[key] = record
    if len({item['id'] for item in matches.values()}) > 1:
        raise ValueError('所选内容对应多条不同的明确拒绝通知，请分别选择原通知页面后显式重试')
    return next(iter(matches.values()), None)


def _preview_public(record):
    result = {key: copy.deepcopy(value) for key, value in record.items() if key not in ('key', 'scope')}
    result.update(_scope_metadata(record['scope']))
    return result


def _receipt_public(record, *, idempotent=False):
    visible = {key: copy.deepcopy(value) for key, value in record.items()
               if key not in ('body', 'key', 'scope', 'request_sha256', 'dedup_scope')}
    visible.update(idempotent=idempotent, received=record['status'] == 'received',
        key_validation_pending=record['status'] == 'key_pending',
        delivery_unknown=record['status'] in ('delivery_unknown', 'reserved'),
        indexing_verified=False, geo_effect_verified=False)
    # Historical receipts have no asset metadata. Their checked key location
    # still identifies the exact host; do not infer control or a wider scope.
    if 'site_origin' not in visible and isinstance(record.get('key_location'), str):
        parsed = urlsplit(record['key_location'])
        visible['site_origin'] = 'https://' + parsed.netloc
    return visible


def overview(connection, cycle, asset_id=None):
    result = {'supported': False, 'reason': '', 'cycle_id': cycle['id'], 'project_id': cycle['project_id'],
        'site_origin': None, 'page_scope': None, 'context_sha256': None, 'endpoint': ENDPOINT,
        'assets': [], 'selected_asset_id': None,
        'limits': {'max_urls': MAX_URLS, 'preview_seconds': PREVIEW_SECONDS, 'max_page_bytes': MAX_PAGE_BYTES,
                   'total_seconds': TOTAL_SECONDS}, 'previews': [], 'notifications': [], 'limitations': list(LIMITATIONS)}
    try:
        website, current = _profiles(connection, cycle)
        result['assets'] = _assets(cycle, current, website)
        scope = _scope(connection, cycle, asset_id)
    except (ValueError, TypeError, KeyError) as error:
        result['reason'] = str(error)
    else:
        result.update(supported=True, site_origin=scope['site_origin'], page_scope=scope['page_scope'],
                      context_sha256=scope['context_sha256'], selected_asset_id=_scope_metadata(scope)['asset_id'])
    result['previews'] = [_preview_public(item) for item in _records(connection, 'geo_indexnow_previews',
        cycle_id=cycle['id'], limit=HISTORY_LIMIT)]
    result['notifications'] = [_receipt_public(item) for item in _records(connection, 'geo_indexnow_notifications',
        cycle_id=cycle['id'], limit=HISTORY_LIMIT)]
    return result


def get_overview(database, cycle_id, asset_id=None):
    _validate_asset_id(asset_id)
    from services.geo_content_store import _connect, _cycle
    with closing(_connect(database)) as connection:
        return overview(connection, _cycle(connection, cycle_id), asset_id)


def _context(connection, cycle_id, expected, asset_id=None):
    from services.geo_content_store import _cycle
    if not isinstance(expected, str) or not re.fullmatch('[0-9a-f]{64}', expected):
        raise ValueError('请先刷新官网范围，再预检或通知')
    cycle = _cycle(connection, cycle_id)
    scope = _scope(connection, cycle, asset_id)
    if scope['context_sha256'] != expected:
        raise ValueError('官网资料或项目身份已变化，请重新预检；旧通知记录保留')
    return cycle, scope


def _prepare(database, cycle_id, payload, asset_id=None):
    deadline = time.monotonic() + TOTAL_SECONDS
    with closing(_connect_before(database, deadline)) as connection:
        _, scope = _context(connection, cycle_id, payload['context_sha256'], asset_id)
    try:
        key, pages = _check_public(scope, payload['key_location'], payload['urls'], deadline)
    except (OSError, TimeoutError, http.client.HTTPException, UnicodeError):
        raise ValueError('公开页面预检未完整完成，未发送通知；请核对地址和网络后再预检') from None
    with closing(_connect_before(database, deadline)) as connection, connection:
        connection.execute('BEGIN IMMEDIATE')
        cycle, latest = _context(connection, cycle_id, payload['context_sha256'], asset_id)
        _budget(deadline)
        changed, blocked = _blocked(connection, latest, pages)
        retry = _retry_record(connection, latest, [page for page in pages if page['url'] in changed])
        created = _now()
        record = {'id': uuid.uuid4().hex, 'cycle_id': cycle_id, 'project_id': cycle['project_id'],
            'context_sha256': scope['context_sha256'], 'scope': scope, 'created_at': created.isoformat(),
            'expires_at': (created + timedelta(seconds=PREVIEW_SECONDS)).isoformat(),
            'key_location': payload['key_location'], 'key': key, 'urls': sorted(payload['urls']), 'pages': pages,
            'changed_urls': changed, 'blocked_urls': blocked, 'ready': bool(changed),
            'retry_receipt_id': retry['id'] if retry else None,
            'indexing_verified': False, 'geo_effect_verified': False}
        connection.execute(_PREVIEWS)
        connection.execute('INSERT INTO geo_indexnow_previews VALUES(?,?,?,?,?,?,?)',
            (record['id'], cycle_id, cycle['project_id'], scope['context_sha256'], record['created_at'],
             record['expires_at'], _json(record)))
        return {'overview': overview(connection, cycle, asset_id), 'preview': _preview_public(record)}


def _prior_request(connection, request_id, request_sha, cycle_id):
    if not _has_table(connection, 'geo_indexnow_notifications'):
        return None
    row = connection.execute('SELECT cycle_id,request_sha256,record FROM geo_indexnow_notifications WHERE request_id=?',
                             (request_id,)).fetchone()
    if row is not None:
        if row['cycle_id'] != cycle_id or row['request_sha256'] != request_sha:
            raise ValueError('同一请求编号已绑定其他操作，请保留原编号和原记录，不要覆盖')
        return json.loads(row['record'])
    return None


def _preview(connection, cycle_id, preview_id, expected):
    if not isinstance(preview_id, str) or not re.fullmatch('[0-9a-f]{32}', preview_id):
        raise ValueError('请先完成本轮公开页面预检')
    if not _has_table(connection, 'geo_indexnow_previews'):
        raise ValueError('没有本轮预检记录，请先预检')
    row = connection.execute('SELECT record FROM geo_indexnow_previews WHERE id=? AND cycle_id=?',
                             (preview_id, cycle_id)).fetchone()
    if row is None:
        raise ValueError('预检记录不属于当前项目轮次')
    record = json.loads(row['record'])
    if record['context_sha256'] != expected or datetime.fromisoformat(record['expires_at']) <= _now():
        raise ValueError('预检已过期或官网资料已变化，请重新预检')
    if not record['ready']:
        raise ValueError('所选页面的相同内容已有通知或结果未知，无需再次通知')
    return record


def _post_indexnow(body, deadline):
    """One POST to the fixed endpoint, with pinned DNS/TLS and no redirects.

    No supplied URL, proxy, Cookie, authentication header, retry or body logging.
    A response body is deliberately never interpreted as caller-visible evidence.
    """
    if _budget(deadline) < public_http.DNS_SECONDS:
        raise TimeoutError('operation_deadline')
    host = 'api.indexnow.org'
    endpoint = public_http._resolve_public(host)
    budget = min(public_http.REQUEST_SECONDS, _budget(deadline))
    connection = public_http._PinnedHTTPSConnection(host, endpoint, timeout_seconds=budget)

    def expire():
        sock = getattr(connection, '_active_socket', None) or connection.sock
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()

    timer = threading.Timer(budget, expire)
    timer.daemon = True
    timer.start()
    try:
        connection.request('POST', '/indexnow', body=body,
            headers={'Content-Type': 'application/json; charset=utf-8', 'Accept': 'application/json',
                     'Accept-Encoding': 'identity', 'Connection': 'close', 'User-Agent': 'GEO-IndexNow/1.0'})
        response = connection.getresponse()
        _budget(deadline)
        return response.status
    finally:
        timer.cancel()
        connection.close()


def _submit(database, cycle_id, payload, asset_id=None):
    from services.geo_content_store import _cycle
    validate_request_id(payload['request_id'])
    retry_id = payload.get('retry_receipt_id')
    if retry_id is not None and (not isinstance(retry_id, str) or not re.fullmatch('[0-9a-f]{32}', retry_id)):
        raise ValueError('只能显式重试一条明确拒绝的历史通知')
    request_sha = _digest(payload if asset_id is None else {'asset_id': asset_id, 'payload': payload})
    deadline = time.monotonic() + TOTAL_SECONDS
    with closing(_connect_before(database, deadline)) as connection:
        # A repeated exact intent returns its receipt before network or expiry
        # validation. An accepted receipt cannot turn into an expired failure.
        prior = _prior_request(connection, payload['request_id'], request_sha, cycle_id)
        if prior is not None:
            return {'overview': overview(connection, _cycle(connection, cycle_id), asset_id),
                    'receipt': _receipt_public(prior, idempotent=True)}
        _, scope = _context(connection, cycle_id, payload['context_sha256'], asset_id)
        preview = _preview(connection, cycle_id, payload['preview_id'], payload['context_sha256'])
    try:
        key, pages = _check_public(scope, preview['key_location'], preview['urls'], deadline)
    except (OSError, TimeoutError, http.client.HTTPException, UnicodeError):
        raise ValueError('发送前公开页面复核未完整完成，未发送通知；请重新预检') from None
    if key != preview['key'] or pages != preview['pages']:
        raise ValueError('公开页面内容或密钥已变化，请重新预检后再通知，旧预检未发送')
    with closing(_connect_before(database, deadline)) as connection, connection:
        connection.execute('BEGIN IMMEDIATE')
        prior = _prior_request(connection, payload['request_id'], request_sha, cycle_id)
        if prior is not None:
            return {'overview': overview(connection, _cycle(connection, cycle_id), asset_id),
                    'receipt': _receipt_public(prior, idempotent=True)}
        cycle, scope = _context(connection, cycle_id, payload['context_sha256'], asset_id)
        preview = _preview(connection, cycle_id, payload['preview_id'], payload['context_sha256'])
        _budget(deadline)
        changed, blocked = _blocked(connection, scope, pages)
        if changed != preview['changed_urls'] or blocked != preview['blocked_urls']:
            raise ValueError('通知记录已变化，可能已有同一内容正在发送；请刷新，不要重复提交')
        selected = [page for page in pages if page['url'] in changed]
        body_object = {'host': urlsplit(scope['site_origin']).hostname, 'key': key,
                       'keyLocation': preview['key_location'], 'urlList': changed}
        body = _json(body_object).encode('utf-8')
        body_sha = hashlib.sha256(body).hexdigest()
        content_sha = _digest(selected)
        rejected = _retry_record(connection, scope, selected)
        if retry_id:
            if rejected is None or retry_id != rejected['id']:
                raise ValueError('重试必须绑定相同页面内容的最近一条明确拒绝通知')
        elif rejected is not None:
            raise ValueError('相同内容曾被明确拒绝；请核对历史原因后使用显式重试，不能自动重新发送')
        receipt = {'id': uuid.uuid4().hex, 'cycle_id': cycle_id, 'project_id': cycle['project_id'],
            'preview_id': preview['id'], 'request_id': payload['request_id'], 'request_sha256': request_sha,
            'context_sha256': scope['context_sha256'], 'dedup_scope': scope['dedup_scope'],
            'key_location': preview['key_location'], 'urls': changed, 'pages': selected,
            'body_sha256': body_sha, 'content_sha256': content_sha, 'endpoint': ENDPOINT,
            'created_at': _now().isoformat(), 'completed_at': None, 'status': 'reserved',
            'http_status': None, 'reason': '通知意图已保存，正在发送；结果未确认时不得重复发送',
            'retry_receipt_id': retry_id, 'indexing_verified': False, 'geo_effect_verified': False}
        receipt.update(_scope_metadata(scope))
        connection.execute(_NOTIFICATIONS)
        connection.execute('INSERT INTO geo_indexnow_notifications VALUES(?,?,?,?,?,?,?,?,?,?,?)',
            (receipt['id'], cycle_id, cycle['project_id'], payload['request_id'], request_sha,
             scope['dedup_scope'], body_sha, content_sha, receipt['created_at'], 'reserved', _json(receipt)))
    # Reservation is committed first. Even a process exit after this point leaves
    # a durable uncertain receipt which suppresses duplicate sends.
    try:
        status = _post_indexnow(body, deadline)
        if type(status) is not int:
            raise ValueError('invalid_response_status')
        receipt['http_status'] = status
        if status == 200:
            receipt.update(status='received', reason='IndexNow已收到通知；抓取、索引更新和GEO效果均未知')
        elif status == 202:
            receipt.update(status='key_pending', reason='IndexNow已接收，密钥验证仍待处理；索引和GEO效果均未知')
        elif 300 <= status < 500:
            receipt.update(status='rejected', reason='IndexNow返回HTTP ' + str(status) + '，明确拒绝或跳转，未自动重试')
        else:
            receipt.update(status='delivery_unknown', reason='平台响应未确认收到通知；结果未知，未自动重试')
    except Exception:
        # Never downgrade a possibly delivered notification to a retryable failure.
        receipt.update(status='delivery_unknown', reason='发送后未取得确定回执，可能已送达；结果未知，未自动重试')
    receipt['completed_at'] = _now().isoformat()
    # Expiry cannot turn a confirmed receipt into a resend. If disk persistence
    # cannot complete in time, the earlier reserved intent remains uncertain.
    with closing(_connect_before(database, deadline)) as connection, connection:
        connection.execute('BEGIN IMMEDIATE')
        updated = connection.execute('UPDATE geo_indexnow_notifications SET status=?,record=? WHERE id=? AND status=?',
            (receipt['status'], _json(receipt), receipt['id'], 'reserved'))
        if updated.rowcount != 1:
            raise ValueError('通知回执状态已变化，请查看原记录，不能再次发送')
        return {'overview': overview(connection, _cycle(connection, cycle_id), asset_id), 'receipt': _receipt_public(receipt)}


def perform_action(database, cycle_id, payload, asset_id=None):
    _validate_asset_id(asset_id)
    common = {'action', 'context_sha256'}
    if not isinstance(payload, dict) or payload.get('action') not in ('preview', 'submit'):
        raise ValueError('只接受公开页面预检或按预检通知，不接受自定义端点或提交结果')
    if payload['action'] == 'preview':
        if set(payload) != common | {'key_location', 'urls'}:
            raise ValueError('预检只接受当前官网范围、公开密钥文件地址和所选页面')
        try:
            return _prepare(database, cycle_id, payload, asset_id)
        except TimeoutError:
            raise ValueError('公开预检超过本次时间限制，未发送通知') from None
    if set(payload) not in (common | {'preview_id', 'request_id'},
                           common | {'preview_id', 'request_id', 'retry_receipt_id'}):
        raise ValueError('通知只接受当前预检编号、请求编号及可选的明确拒绝重试编号')
    try:
        return _submit(database, cycle_id, payload, asset_id)
    except TimeoutError:
        raise ValueError('官网通知操作超过本次时间限制，请先查看原回执，不要自动重发') from None
