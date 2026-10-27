"""Persistent Bing onboarding: public-file observations and owner reports stay separate.

This service never signs in to Bing, submits a sitemap, or infers index/GEO
success. Public checks only fetch two fixed paths on the cycle's HTTPS site.
"""
from __future__ import annotations

import base64
import binascii
import copy
import hashlib
import http.client
import ipaddress
import json
import re
import socket
import ssl
import threading
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from contextlib import closing
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit

ENTRY_URL = 'https://www.bing.com/webmasters/'
CREATE_TABLE = '''CREATE TABLE IF NOT EXISTS geo_webmaster_records (
    cycle_id TEXT PRIMARY KEY, record TEXT NOT NULL, updated_at TEXT NOT NULL,
    FOREIGN KEY(cycle_id) REFERENCES geo_content_cycles(id) ON DELETE CASCADE)'''
MAX_XML_BYTES = 16 * 1024
MAX_SITEMAP_BYTES = 2 * 1024 * 1024
REQUEST_SECONDS = 8
DNS_SECONDS = 3
_DNS_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix='webmaster-dns')
_DNS_SLOTS = threading.BoundedSemaphore(4)


def _now():
    return datetime.now(timezone.utc).isoformat()


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':')).encode()).hexdigest()


def _global_ip(value):
    address = ipaddress.ip_address(value)
    if (not address.is_global or address.is_reserved or address.is_multicast or address.is_unspecified
            or getattr(address, 'ipv4_mapped', None) is not None):
        raise ValueError('官网检查只允许公网地址')
    return address


def _origin(website):
    if not isinstance(website, str) or len(website) > 2000 or any(ord(c) <= 32 for c in website):
        raise ValueError('请先填写有效的 HTTPS 官网地址')
    try:
        parsed = urlsplit(website)
        host = parsed.hostname
        if (parsed.scheme != 'https' or not host or parsed.username is not None or parsed.password is not None
                or parsed.port not in (None, 443) or '\\' in website or '%' in host):
            raise ValueError()
        host = host.encode('idna').decode('ascii').lower()
    except (ValueError, UnicodeError):
        raise ValueError('请先填写不含账号信息、使用默认端口的 HTTPS 官网地址') from None
    try:
        address = _global_ip(host)
        authority = '[' + host + ']' if address.version == 6 else host
    except ValueError:
        try:
            ipaddress.ip_address(host)
        except ValueError:
            labels = host.split('.')
            if (len(labels) < 2 or len(host) > 253 or host.endswith(('.localhost', '.local', '.internal', '.invalid'))
                    or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label) for label in labels)):
                raise ValueError('官网检查只支持有效的公网域名') from None
            authority = host
        else:
            raise ValueError('官网检查只允许公网地址') from None
    return 'https://' + authority


def _targets(cycle, origin):
    values = [origin + '/', cycle['profile']['website']]
    values += [item.get('source_url') for item in cycle.get('facts', [])
               if isinstance(item, dict) and item.get('confirmed') is True]
    result = []
    for value in values:
        try:
            if _origin(value) != origin:
                continue
            parsed = urlsplit(value)
            normalized = origin + (parsed.path or '/') + ('?' + parsed.query if parsed.query else '')
        except (ValueError, TypeError):
            continue
        if normalized not in result:
            result.append(normalized)
    return result[:30]


def _stored(connection, cycle_id):
    exists = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='geo_webmaster_records'").fetchone()
    if not exists:
        return {'sites': {}}
    row = connection.execute('SELECT record FROM geo_webmaster_records WHERE cycle_id=?', (cycle_id,)).fetchone()
    return json.loads(row[0]) if row else {'sites': {}}


def overview(connection, cycle):
    result = {'supported': False, 'reason': '', 'site_origin': None, 'site_key': None,
              'entry_url': ENTRY_URL, 'verification_url': None, 'sitemap_url': None,
              'target_urls': [], 'record': None, 'context_sha256': None}
    try:
        origin = _origin(cycle['profile'].get('website'))
    except ValueError as error:
        result['reason'] = str(error)
        return result
    key = _digest({'site_origin': origin})
    record = _stored(connection, cycle['id'])['sites'].get(key)
    targets = _targets(cycle, origin)
    result.update(supported=True, site_origin=origin, site_key=key,
                  verification_url=origin + '/BingSiteAuth.xml', sitemap_url=origin + '/sitemap.xml',
                  target_urls=targets, record=record,
                  context_sha256=_digest({'cycle_id': cycle['id'], 'site_origin': origin,
                      'website': cycle['profile']['website'], 'target_urls': targets,
                      'revision': record['revision'] if record else 0}))
    return result


def get_overview(database, cycle_id):
    from services.geo_content_store import _connect, _cycle
    with closing(_connect(database)) as connection:
        return overview(connection, _cycle(connection, cycle_id))


def _default_record(view):
    return {'site_origin': view['site_origin'], 'site_key': view['site_key'], 'revision': 0,
            'verification_file': None,
            'public_check': {'status': 'not_checked', 'checked_at': None, 'http_status': None,
                             'exact_source_bytes': None, 'matches_key': None},
            'sitemap_check': {'status': 'not_checked', 'checked_at': None, 'entries': None},
            'ownership': {'status': 'not_confirmed', 'evidence_source': 'user_reported_platform_result', 'observed_at': None},
            'sitemap_submission': {'status': 'not_reported', 'evidence_source': 'user_reported_platform_result', 'observed_at': None},
            'inspections': [], 'created_at': _now(), 'updated_at': None}


def _xml_root(data):
    if b'\x00' in data or re.search(br'<!\s*(?:DOCTYPE|ENTITY)\b', data, re.I):
        raise ValueError('XML 不允许包含 DTD、实体或非 UTF-8 文本')
    try:
        return ET.fromstring(data)
    except (ET.ParseError, ValueError):
        raise ValueError('请使用真实下载的 XML 文件') from None


def _ownership_key(data):
    root = _xml_root(data)
    if (root.tag != 'users' or root.attrib or (root.text or '').strip() or len(root) != 1
            or root[0].tag != 'user' or root[0].attrib or len(root[0])
            or (root[0].tail or '').strip()
            or not re.fullmatch(r'[0-9A-Fa-f]{32}', root[0].text or '')):
        raise ValueError('请选择 Bing 下载的 BingSiteAuth.xml：应包含唯一的 32 位验证密钥')
    return root[0].text


def _decode_file(value):
    if not isinstance(value, str) or not value or len(value) > ((MAX_XML_BYTES + 2) // 3) * 4:
        raise ValueError('验证文件最大 16KB，请上传原始 XML 文件')
    try:
        data = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error):
        raise ValueError('验证文件编码无效') from None
    if not data or len(data) > MAX_XML_BYTES:
        raise ValueError('验证文件最大 16KB')
    _ownership_key(data)
    return data


def _resolve_public(host):
    try:
        literal = _global_ip(host)
    except ValueError:
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            raise
    else:
        return socket.AF_INET6 if literal.version == 6 else socket.AF_INET, host
    if not _DNS_SLOTS.acquire(blocking=False):
        raise OSError('dns_busy')
    try:
        future = _DNS_POOL.submit(socket.getaddrinfo, host, 443, socket.AF_UNSPEC, socket.SOCK_STREAM)
    except BaseException:
        _DNS_SLOTS.release()
        raise
    future.add_done_callback(lambda unused: _DNS_SLOTS.release())
    try:
        answers = future.result(timeout=DNS_SECONDS)
    except FutureTimeout:
        raise TimeoutError('dns_timeout') from None
    candidates = []
    for family, socktype, protocol, canonical, address in answers:
        if family not in (socket.AF_INET, socket.AF_INET6):
            continue
        _global_ip(address[0])  # Reject mixed public/private DNS responses too.
        candidates.append((family, address[0]))
    if not candidates:
        raise OSError('dns_empty')
    return sorted(set(candidates), key=lambda item: item[0] != socket.AF_INET)[0]


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host, endpoint, *, timeout_seconds=REQUEST_SECONDS):
        super().__init__(host, port=443, timeout=timeout_seconds, context=ssl.create_default_context())
        self.endpoint = endpoint
        self._request_seconds = timeout_seconds

    def connect(self):
        family, address = self.endpoint
        raw = socket.socket(family, socket.SOCK_STREAM)
        self.sock = raw
        self._active_socket = raw
        raw.settimeout(self._request_seconds)
        raw.connect((address, 443))
        self.sock = self._context.wrap_socket(raw, server_hostname=self.host, do_handshake_on_connect=False)
        self._active_socket = self.sock
        self.sock.do_handshake()


def _fetch_public(origin, path, maximum):
    """Pinned DNS/TLS; no proxies, redirects, cookies, or caller-controlled paths."""
    if path not in ('/BingSiteAuth.xml', '/sitemap.xml') or _origin(origin) != origin:
        raise ValueError('只能检查当前官网固定的验证文件与站点地图')
    return _fetch_bounded(origin, path, maximum)


def _fetch_bounded(origin, path, maximum, *, accept='application/xml,text/xml', deadline=None, include_headers=False):
    """Shared bounded transport; URL scope/redirect policy belongs to its caller.

    Both callers use the same public DNS validation, pinned socket and TLS
    hostname. This transport never redirects, sends cookies, or uses a proxy.
    """
    if (_origin(origin) != origin or not isinstance(path, str) or not path.startswith('/') or path.startswith('//')
            or len(path) > 2000 or '\\' in path or any(ord(char) <= 32 or ord(char) == 127 for char in path)
            or type(maximum) is not int or not 1 <= maximum <= MAX_SITEMAP_BYTES
            or accept not in ('application/xml,text/xml', 'text/html,application/xhtml+xml')):
        raise ValueError('invalid_public_request')
    host = urlsplit(origin).hostname
    endpoint = _resolve_public(host)
    budget = REQUEST_SECONDS if deadline is None else min(REQUEST_SECONDS, deadline - time.monotonic())
    if budget <= 0:
        raise TimeoutError('response_timeout')
    connection = (_PinnedHTTPSConnection(host, endpoint) if budget == REQUEST_SECONDS else
                  _PinnedHTTPSConnection(host, endpoint, timeout_seconds=budget))
    if deadline is not None:
        budget = min(budget, deadline - time.monotonic())
        if budget <= 0:
            connection.close()
            raise TimeoutError('response_timeout')
        connection.timeout = budget
        connection._request_seconds = budget

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
    started = time.monotonic()
    timer.start()
    try:
        connection.request('GET', path, headers={'User-Agent': 'GEO-Website-Verification/1.0',
                           'Accept': accept, 'Accept-Encoding': 'identity', 'Connection': 'close'})
        response = connection.getresponse()
        metadata = {'http_status': response.status, 'content_type': response.getheader('Content-Type', '')}
        if include_headers:
            content_type = metadata['content_type'] or ''
            location = response.getheader('Location', '') or ''
            robots = response.getheader('X-Robots-Tag', '') or ''
            metadata.update(content_type=content_type[:512], content_type_truncated=len(content_type) > 512,
                location=location if len(location) <= 2000 else '', location_truncated=len(location) > 2000,
                x_robots_tag=robots[:1000], x_robots_tag_truncated=len(robots) > 1000)
        if response.status != 200:
            return metadata, b''
        encoding = response.getheader('Content-Encoding', 'identity').lower()
        if encoding not in ('', 'identity'):
            raise ValueError('unsupported_content_encoding')
        length = response.getheader('Content-Length')
        if length and (not length.isdigit() or int(length) > maximum):
            raise ValueError('response_too_large')
        chunks, size = [], 0
        while True:
            if time.monotonic() - started >= budget:
                raise TimeoutError('response_timeout')
            chunk = response.read(min(16384, maximum + 1 - size))
            if not chunk:
                break
            size += len(chunk)
            if size > maximum:
                raise ValueError('response_too_large')
            chunks.append(chunk)
        return metadata, b''.join(chunks)
    finally:
        timer.cancel()
        connection.close()


def _check_status(http_status):
    if 300 <= http_status < 400:
        return 'redirect'
    return 'not_found' if http_status == 404 else 'unreachable'


def _public_checks(view):
    record = view['record'] or _default_record(view)
    original = _decode_file(record['verification_file']['xml_base64']) if record['verification_file'] else None
    public = {'status': 'unreachable', 'checked_at': _now(), 'http_status': None,
              'exact_source_bytes': None, 'matches_key': None, 'evidence_source': 'software_public_http'}
    sitemap = {'status': 'unreachable', 'checked_at': _now(), 'entries': None,
               'evidence_source': 'software_public_http', 'target_urls_present': {url: None for url in view['target_urls']}}
    for path, target, maximum in (('/BingSiteAuth.xml', public, MAX_XML_BYTES), ('/sitemap.xml', sitemap, MAX_SITEMAP_BYTES)):
        try:
            metadata, data = _fetch_public(view['site_origin'], path, maximum)
            target.update(metadata)
            if metadata['http_status'] != 200:
                target['status'] = _check_status(metadata['http_status'])
                continue
            target.update(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
            if path == '/BingSiteAuth.xml':
                key = _ownership_key(data)
                target['status'] = 'xml_present'
                if original is not None:
                    target.update(exact_source_bytes=data == original, matches_key=key == _ownership_key(original))
                    target['status'] = 'matches' if target['exact_source_bytes'] and target['matches_key'] else 'mismatch'
            else:
                root = _xml_root(data)
                kind = root.tag.rsplit('}', 1)[-1]
                if kind not in ('urlset', 'sitemapindex'):
                    raise ValueError('invalid_sitemap_root')
                item_type = 'url' if kind == 'urlset' else 'sitemap'
                locations = [(child.text or '').strip() for item in root if item.tag.rsplit('}', 1)[-1] == item_type
                             for child in item if child.tag.rsplit('}', 1)[-1] == 'loc']
                target.update(status='ok', kind=kind, entries=len(locations),
                              target_urls_present={url: url in locations if kind == 'urlset' else None for url in view['target_urls']})
        except ValueError:
            target.update(status='invalid_xml' if target.get('http_status') == 200 else 'unreachable', error='文件或公网地址校验未通过')
        except (OSError, ssl.SSLError, http.client.HTTPException):
            target.update(status='unreachable', error='当前无法完成公网检查，请稍后检查；未推断为已部署或已索引')
    return public, sitemap


_FIELDS = {
    'upload_xml': {'xml_base64'}, 'check_public': set(), 'confirm_ownership': {'confirmed'},
    'record_sitemap': {'status', 'note', 'discovered_urls'}, 'record_inspection': {'url', 'status', 'note'},
}


def _validate_payload(payload):
    if not isinstance(payload, dict) or not isinstance(payload.get('action'), str) or payload['action'] not in _FIELDS:
        raise ValueError('请选择有效的站长操作')
    if set(payload) - ({'action', 'context_sha256'} | _FIELDS[payload['action']]):
        raise ValueError('站长操作不接受任意地址、文件路径或登录信息')
    if not isinstance(payload.get('context_sha256'), str):
        raise ValueError('请刷新当前官网记录后重试')
    if payload['action'] == 'upload_xml':
        _decode_file(payload.get('xml_base64'))
    elif payload['action'] == 'confirm_ownership' and payload.get('confirmed') is not True:
        raise ValueError('请先在 Bing 确认验证成功，再登记结果')
    elif payload['action'] == 'record_sitemap':
        if payload.get('status') not in ('success', 'pending', 'failed'):
            raise ValueError('请记录 Bing 实际显示的站点地图状态')
        discovered = payload.get('discovered_urls')
        if discovered is not None and (type(discovered) is not int or not 0 <= discovered <= 100000000):
            raise ValueError('发现的网址数量必须为非负整数或未知')
    elif payload['action'] == 'record_inspection' and payload.get('status') not in ('indexed', 'not_indexed', 'unknown'):
        raise ValueError('请记录 Bing 实际显示的索引状态')
    if 'note' in payload and (not isinstance(payload['note'], str) or len(payload['note']) > 500):
        raise ValueError('补充说明最多 500 字')


def _require_context(view, expected):
    if not view['supported']:
        raise ValueError(view['reason'])
    if view['context_sha256'] != expected:
        raise ValueError('官网或站长记录已变更，请刷新后核对，原记录未被覆盖')


def perform_action(database, cycle_id, payload):
    from services.geo_content_store import _connect, _cycle
    _validate_payload(payload)
    view = get_overview(database, cycle_id)
    _require_context(view, payload['context_sha256'])
    checks = _public_checks(view) if payload['action'] == 'check_public' else None
    with closing(_connect(database)) as connection, connection:
        connection.execute('BEGIN IMMEDIATE')
        cycle = _cycle(connection, cycle_id)
        fresh = overview(connection, cycle)
        _require_context(fresh, payload['context_sha256'])
        record = copy.deepcopy(fresh['record'] or _default_record(fresh))
        action, stamp = payload['action'], _now()
        if action == 'upload_xml':
            data = _decode_file(payload['xml_base64'])
            previous = record['verification_file']
            changed = previous is None or previous['sha256'] != hashlib.sha256(data).hexdigest()
            if changed:
                record['ownership'] = {'status': 'not_confirmed', 'evidence_source': 'user_reported_platform_result', 'observed_at': None}
            record['verification_file'] = {'name': 'BingSiteAuth.xml', 'sha256': hashlib.sha256(data).hexdigest(),
                                           'bytes': len(data), 'xml_base64': payload['xml_base64']}
            if changed:
                record['public_check'] = _default_record(fresh)['public_check']
        elif action == 'check_public':
            record['public_check'], record['sitemap_check'] = checks
        elif action == 'confirm_ownership':
            record['ownership'] = {'status': 'confirmed', 'evidence_source': 'user_reported_platform_result', 'observed_at': stamp}
        elif action == 'record_sitemap':
            record['sitemap_submission'] = {'status': payload['status'], 'note': payload.get('note', ''),
                'discovered_urls': payload.get('discovered_urls'), 'evidence_source': 'user_reported_platform_result', 'observed_at': stamp}
        else:
            url = payload.get('url')
            if not isinstance(url, str) or len(url) > 2000 or _origin(url) != fresh['site_origin'] or urlsplit(url).fragment:
                raise ValueError('只能记录当前官网内具体页面的索引状态')
            parsed = urlsplit(url)
            url = urlunsplit(('https', urlsplit(fresh['site_origin']).netloc, parsed.path or '/', parsed.query, ''))
            record['inspections'] = [item for item in record['inspections'] if item['url'] != url]
            if len(record['inspections']) >= 50:
                raise ValueError('每个官网最多保留 50 个页面的当前检查结果')
            record['inspections'].append({'url': url, 'status': payload['status'], 'note': payload.get('note', ''),
                                         'evidence_source': 'user_reported_platform_result', 'observed_at': stamp})
        record['revision'] += 1
        record['updated_at'] = stamp
        stored = _stored(connection, cycle_id)
        stored['sites'][fresh['site_key']] = record
        connection.execute(CREATE_TABLE)
        connection.execute('INSERT INTO geo_webmaster_records(cycle_id,record,updated_at) VALUES(?,?,?) '
                           'ON CONFLICT(cycle_id) DO UPDATE SET record=excluded.record,updated_at=excluded.updated_at',
                           (cycle_id, json.dumps(stored, ensure_ascii=False), stamp))
        return overview(connection, cycle)
