"""Frozen, project-scoped directory applications; never article publication or GEO evidence.

The only adapter is the public GrokCode API directory form. Browser callbacks
hold a private session token; HTTP clients cannot manufacture submission state.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import secrets
import uuid
from contextlib import closing
from datetime import datetime, timezone
from urllib.parse import urlsplit, parse_qsl

from services import geo_content_store as store, geo_destination_service as destinations
from services.geo_entity_identity import same_entity_target
from services.geo_external_baseline import ExternalBaselineConflict

ADAPTER = 'grokcode_api_transit_v1'
LIST_URL = 'https://grokcode.cn/api-transit'
ENTRY_URL = LIST_URL + '?modal=submit-transit'
LEAD_URL = 'https://grokcode.cn/api/lead'
FIELD_LIMITS = {'name': 120, 'url': 500, 'rate': 500, 'source': 1500, 'contact': 500, 'notes': 2000}
REQUIRED_FIELDS = ['name', 'url', 'rate', 'source', 'models']
FIELDS = frozenset((*FIELD_LIMITS, 'models'))
MODEL_OPTIONS = ['Claude', 'GPT', 'Gemini', 'Grok', 'DeepSeek', 'GLM', 'Kimi', '千问 / Qwen', '图片 / 视频模型', '不确定']
# Official public client module observed 2026-11-02. Checkbox labels themselves
# enter product_focus (join(', ')) and the description (join('、')). P1 is single-select.
MODEL_OPTIONS_SOURCE = {'url': 'https://grokcode.cn/_next/static/chunks/7850-1b6586b780812df2.js',
    'sha256': '3d6bdc382c7aefecb5c4dc9fc521a80fa56a0495c1f0c9fe23779dbb28f21d1c'}
OPEN_STAGES = frozenset(('opening', 'awaiting_human_verification', 'needs_manual_review', 'awaiting_human_confirmation'))
CREATE_TABLE = '''CREATE TABLE IF NOT EXISTS geo_directory_applications (
    id TEXT PRIMARY KEY, project_id INTEGER NOT NULL, cycle_id TEXT NOT NULL,
    target_id TEXT NOT NULL, adapter TEXT NOT NULL, record TEXT NOT NULL, intake_uid TEXT UNIQUE,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    UNIQUE(project_id,adapter),
    FOREIGN KEY(project_id) REFERENCES projects(id),
    FOREIGN KEY(cycle_id) REFERENCES geo_content_cycles(id),
    FOREIGN KEY(target_id) REFERENCES geo_distribution_targets(id))'''


def _now():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _exists(connection):
    return connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='geo_directory_applications'").fetchone()


def _public_url(value):
    if not isinstance(value, str) or len(value) > 500 or any(c.isspace() for c in value):
        raise ValueError('请填写公开的HTTPS网址')
    parsed = urlsplit(value)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
            or parsed.port not in (None, 443) or parsed.fragment):
        raise ValueError('请填写无凭证、无片段的公开HTTPS网址')
    host = parsed.hostname.rstrip('.')
    if host == 'localhost' or '.' not in host or host.endswith(('.local', '.internal')) or '\\' in host or '%' in host:
        raise ValueError('请使用可公开核对的正式域名，不提交本机或内网入口')
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise ValueError('请使用公开网址，不提交本机或内网入口')
    if any(re.search(r'key|token|secret|password|credential|auth', key, re.I) for key, _ in parse_qsl(parsed.query)):
        raise ValueError('公开网址不能带密钥、口令或授权查询参数')
    return parsed


def _known_target(target):
    config = target['config']
    destinations.assert_target_enabled(target)
    for key in ('home_url', 'source_scope', 'editor_url', 'login_url'):
        value = config.get(key, '')
        parsed = _public_url(value)
        if (parsed.netloc != 'grokcode.cn' or parsed.path.rstrip('/') != '/api-transit'
                or parsed.query not in ('', 'modal=submit-transit')):
            raise ValueError('仅支持已核对的GrokCode API目录目标，不能替换为其它平台入口')
    destinations.assert_publication_allowed(target)
    if config.get('publication_use') != 'commercial':
        raise ValueError('带价格和联系信息的目录申请属于产品展示，请先按商业用途核对当前目标规则')


def _context(connection, cycle_id, target_id):
    cycle = store._cycle(connection, cycle_id, for_update=True)
    project = connection.execute('SELECT * FROM projects WHERE id=?', (cycle['project_id'],)).fetchone()
    if not project or not same_entity_target(dict(project), cycle['profile']):
        raise ValueError('当前项目与本轮客户主体不一致，请先核对项目')
    row = connection.execute('SELECT * FROM geo_distribution_targets WHERE id=? AND project_id=?',
                             (target_id, cycle['project_id'])).fetchone()
    if not row:
        raise ValueError('目录目标不属于当前客户项目')
    target = destinations._target(row)
    _known_target(target)
    identity_keys = ('name', 'website', 'product', 'entity_type', 'company', 'aliases', 'official_urls')
    identity = {key: dict(project).get(key) for key in identity_keys}
    value = {'project_id': cycle['project_id'], 'cycle_id': cycle_id, 'profile': cycle['profile'],
             'project_identity': identity, 'target_id': target_id, 'target_revision': target['revision'],
             'target_config_sha256': target['config_sha256'], 'adapter': ADAPTER}
    return cycle, target, value


def _safe_text(value, maximum, *, required=False):
    if not isinstance(value, str) or len(value) > maximum or value != value.strip() or (required and not value):
        raise ValueError('公开字段为空、超长或含首尾空白，请核对预览')
    # These inputs are public application text, not a spreadsheet import or a
    # credential transport. Do not store rejected values or include them in errors.
    if (re.search(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\u200b\ufeff]', value)
            or re.search(r'(?:密钥|秘钥|密码|access[_ -]?token|api[_ -]?key|client[_ -]?secret|authorization|cookie)\s*[:=：]\s*\S|'
                         r'bearer\s+[A-Za-z0-9._~-]{8,}|sk-[A-Za-z0-9_-]{12,}|'
                         r'(?:成本(?:价)?|利润(?:率)?|毛利(?:率)?|进货价|结算价)\s*(?:为|是|约|[:=：])?\s*(?:CNY|人民币|[¥￥])?\s*\d|'
                         r'(?:^|\n)\s*=\s*(?:[A-Z]+\(|\$?[A-Z]{1,3}\$?\d)|'
                         r'(?<![A-Za-z])[A-Za-z]:[\\/]|\\\\|\.xlsx?\b|private-price-review', value, re.I)):
        raise ValueError('这里只接受本人确认的公开资料；不能包含实际成本利润数字、凭证值、表格公式或私密文件路径')
    return value


def validate_fields(value, profile):
    if not isinstance(value, dict) or set(value) != FIELDS:
        raise ValueError('目录申请必须恰好包含七个公开字段')
    fields = {key: _safe_text(value[key], maximum, required=key in REQUIRED_FIELDS)
              for key, maximum in FIELD_LIMITS.items()}
    if not isinstance(value['models'], list) or len(value['models']) != 1 or value['models'][0] not in MODEL_OPTIONS:
        raise ValueError('请选择一个已核对的模型族；当前版本只支持单选')
    fields['models'] = list(value['models'])
    names = [profile['name'], *(profile.get('aliases') or [])]
    if not any(name and name in fields['name'] for name in names):
        raise ValueError('申请名称须保留当前客户品牌，不能混入其它客户')
    official = [profile.get('website', ''), *(profile.get('official_urls') or [])]
    official = [item for item in official if item]
    allowed_hosts = {_public_url(item).hostname for item in official}
    if fields['url'].rstrip('/') not in {item.rstrip('/') for item in official}:
        raise ValueError('服务网址必须是本轮客户已登记的官网或官方服务入口')
    _public_url(fields['url'])
    for text in fields.values():
        if not isinstance(text, str):
            continue
        for address in re.findall(r'https?://[^\s<>"）)\]，。；;]+', text):
            parsed = _public_url(address)
            if parsed.hostname not in allowed_hosts:
                raise ValueError('公开字段中的链接只能引用当前客户已登记的官方域名')
    return fields


def expected_transport(fields):
    """The single observed official form transform; models are not guessed."""
    if (not isinstance(fields, dict) or set(fields) != FIELDS or not isinstance(fields['models'], list)
            or len(fields['models']) != 1 or fields['models'][0] not in MODEL_OPTIONS):
        raise ValueError('未核对的七字段或模型选项，不能构造提交内容')
    body = {'partner_type': 'transit', 'intent': 'api', 'org_name': fields['name'],
            'website_url': fields['url'], 'api_base_url': fields['url'], 'product_focus': fields['models'][0],
            'description': '\n'.join(item for item in ('【提交中转站】', '地址：' + fields['url'],
                '倍率/价格：' + fields['rate'], '来源：' + fields['source'], '模型：' + fields['models'][0],
                fields['notes'], '来源页：' + ENTRY_URL) if item)}
    if fields['contact']:
        body['contact'] = fields['contact']
    return body


def _get(connection, cycle_id, identifier):
    cycle = store._cycle(connection, cycle_id)
    row = connection.execute('SELECT record FROM geo_directory_applications WHERE id=? AND project_id=?',
                             (identifier, cycle['project_id'])).fetchone() if _exists(connection) else None
    if not row:
        raise ValueError('目录申请不属于当前客户项目或不存在')
    return json.loads(row['record'])


def _put(connection, record):
    record['updated_at'] = _now()
    intake = (record.get('receipt') or {}).get('intake_uid')
    if intake and connection.execute('SELECT 1 FROM geo_directory_applications WHERE intake_uid=? AND id!=?', (intake, record['id'])).fetchone():
        raise ValueError('此平台回执已登记到另一份申请，不能跨客户关联或重复登记')
    connection.execute('UPDATE geo_directory_applications SET record=?,intake_uid=?,updated_at=? WHERE id=?',
                       (json.dumps(record, ensure_ascii=False), intake, record['updated_at'], record['id']))


def _assert_current(connection, cycle_id, record):
    if cycle_id != record['cycle_id']:
        raise ValueError('此申请属于原轮次，只能查看，不能跨轮次编辑或重发')
    cycle, target, context = _context(connection, cycle_id, record['target_id'])
    if digest(context) != record['context_sha256']:
        raise ValueError('客户资料或目标版本已变化，请保留原申请，不会提交过期字段')
    if digest({'context': context, 'fields': record['fields']}) != record['review_sha256']:
        raise ValueError('申请摘要与冻结字段不一致，请保留原记录检查，未授权发送')
    return cycle, target


def _unopened_draft(record):
    return record['stage'] in ('draft', 'reviewed') and record.get('session_count') == 0


def _controlled_closed_draft(record):
    # Proof remains valid through explicit context refresh/review, until the
    # next reserve_session removes it. Session counts and fields stay frozen.
    return (record['stage'] in ('closed_not_submitted', 'draft', 'reviewed')
            and type(record.get('session_count')) is int and record['session_count'] > 0
            and record.get('submit_attempted') is False
            and isinstance(record.get('controlled_closed_at'), str)
            and _normal_no_submission(record.get('no_submission_evidence')))


def _decorate(connection, cycle_id, record):
    result = {key: item for key, item in record.items() if key not in ('session_nonce', 'no_submission_evidence')}
    try:
        _assert_current(connection, cycle_id, record)
        current = True
        context_reason = ''
    except (ValueError, store.ResearchNotFoundError, ExternalBaselineConflict) as error:
        current = False
        context_reason = str(error)
    result['current_context'] = current
    result['context_reason'] = context_reason
    result['can_edit'] = current and record['stage'] in ('draft', 'reviewed') and record.get('session_count', 0) == 0
    result['can_review'] = current and record['stage'] in ('draft', 'reviewed', 'closed_not_submitted') and not record.get('submit_intent')
    result['can_open'] = current and record['stage'] == 'reviewed' and record['reviewed'] is True and bool(record.get('open_nonce'))
    result['can_inspect'] = True
    result['can_record_external_receipt'] = current and record['stage'] in ('draft', 'reviewed', 'submission_unknown', 'closed_not_submitted') and not record.get('window_open')
    result['can_refresh_context'] = False
    if (not current and record['cycle_id'] == cycle_id and (_unopened_draft(record) or _controlled_closed_draft(record))
            and not record.get('submit_intent') and not record.get('receipt')):
        try:
            cycle, _, _ = _context(connection, cycle_id, record['target_id'])
            original = record['context_snapshot']['profile']
            result['can_refresh_context'] = same_entity_target(original, cycle['profile']) and original.get('company', '') == cycle['profile'].get('company', '')
        except (ValueError, KeyError, store.ResearchNotFoundError, ExternalBaselineConflict):
            pass
    result['read_only'] = not result['can_edit']
    if not result['can_open']:
        result['open_nonce'] = None
    result['auto_publish_verified'] = False
    result['geo_effect_evidence'] = False
    result['native_submit_attempted'] = bool(record.get('submit_attempted'))
    return result


def get_overview(database, cycle_id, target_id):
    with closing(store._connect(database)) as connection:
        cycle = store._cycle(connection, cycle_id)
        records = [json.loads(row[0]) for row in connection.execute(
            'SELECT record FROM geo_directory_applications WHERE project_id=? AND adapter=? ORDER BY created_at',
            (cycle['project_id'], ADAPTER))] if _exists(connection) else []
        result = {'supported': False, 'reason': '', 'adapter': ADAPTER, 'entry_url': ENTRY_URL,
                  'context_sha256': None, 'field_limits': FIELD_LIMITS, 'field_required': REQUIRED_FIELDS,
                  'model_options': MODEL_OPTIONS, 'models_max_selected': 1, 'model_options_source': MODEL_OPTIONS_SOURCE,
                  'applications': [_decorate(connection, cycle_id, item) for item in records]}
        try:
            _, _, context = _context(connection, cycle_id, target_id)
            result.update(supported=True, context_sha256=digest(context))
        except (ValueError, ExternalBaselineConflict) as error:
            result['reason'] = str(error)
        return result


def get_application(database, cycle_id, identifier):
    with closing(store._connect(database)) as connection:
        return _decorate(connection, cycle_id, _get(connection, cycle_id, identifier))


def save_application(database, cycle_id, data):
    names = {'target_id', 'expected_context_sha256', 'fields', 'application_id', 'revision'}
    if not isinstance(data, dict) or set(data) - names:
        raise ValueError('只接受目标版本与七字段公开资料，不接受文件或其它字段')
    with closing(store._connect(database)) as connection, connection:
        connection.execute('BEGIN IMMEDIATE')
        cycle, target, context = _context(connection, cycle_id, data.get('target_id'))
        if data.get('expected_context_sha256') != digest(context):
            raise ValueError('客户资料或目标版本已变化，请刷新公开预览')
        fields = validate_fields(data.get('fields'), cycle['profile'])
        connection.execute(CREATE_TABLE)
        row = connection.execute('SELECT record FROM geo_directory_applications WHERE project_id=? AND adapter=?',
                                 (cycle['project_id'], ADAPTER)).fetchone()
        previous = json.loads(row[0]) if row else None
        if previous:
            if previous['target_id'] != data.get('target_id'):
                raise ValueError('同一客户此目录已有绑定原目标的申请；只能查看原记录，不能改用新目标覆盖')
            _assert_current(connection, cycle_id, previous)
            if not _decorate(connection, cycle_id, previous)['can_edit']:
                raise ValueError('同一客户此目录已有申请或既有回执，请检查原记录，不能新发或绕过未知状态')
            if data.get('application_id') != previous['id'] or type(data.get('revision')) is not int or data['revision'] != previous['revision']:
                raise ValueError('申请版本已变化，请刷新后编辑')
        elif data.get('application_id') is not None or data.get('revision') is not None:
            raise ValueError('原申请不存在，不能跨客户修改或另建')
        stamp = _now()
        record = {**(previous or {}), 'id': previous['id'] if previous else uuid.uuid4().hex,
            'project_id': cycle['project_id'], 'cycle_id': cycle_id, 'target_id': target['id'],
            'target_revision': target['revision'], 'adapter': ADAPTER, 'entry_url': ENTRY_URL,
            'context_sha256': digest(context), 'context_snapshot': context, 'fields': fields, 'revision': previous['revision'] + 1 if previous else 1,
            'review_sha256': digest({'context': context, 'fields': fields}), 'reviewed': False, 'open_nonce': None,
            'stage': 'draft', 'message': '七字段公开资料已保存，请核对后确认；最终提交由本人在平台完成。',
            'receipt': None, 'evidence_level': 'prepared_only', 'source': 'software_prepared',
            'created_at': previous['created_at'] if previous else stamp, 'updated_at': stamp,
            'submit_attempted': False, 'submit_intent': None, 'window_open': False, 'session_count': 0,
            'native_task_id': None, 'history': list((previous or {}).get('history', []))}
        if previous:
            _put(connection, record)
        else:
            connection.execute('INSERT INTO geo_directory_applications VALUES(?,?,?,?,?,?,?,?,?)',
                (record['id'], record['project_id'], cycle_id, target['id'], ADAPTER, json.dumps(record, ensure_ascii=False), None, stamp, stamp))
        return _decorate(connection, cycle_id, record)


def _review_input(record, data, names):
    if not isinstance(data, dict) or set(data) - names or data.get('confirmed') is not True:
        raise ValueError('请本人确认完整公开资料预览，不接受其它字段')
    if (type(data.get('expected_revision')) is not int or data['expected_revision'] != record['revision']
            or data.get('expected_review_sha256') != record['review_sha256']):
        raise ValueError('申请资料或版本已变化，请重新核对')


def _normal_no_submission(value):
    # Counts cover directory business submissions only. Ordinary authentication,
    # CAPTCHA and browser traffic are neither inspected nor counted as a lead.
    return (isinstance(value, dict) and set(value) == {'normal_close_confirmed', 'route_guard_installed', 'allowed_posts', 'blocked_writes'}
            and value['normal_close_confirmed'] is True and value['route_guard_installed'] is True
            and type(value['allowed_posts']) is int and value['allowed_posts'] == 0
            and type(value['blocked_writes']) is int and value['blocked_writes'] == 0)


def refresh_context(database, cycle_id, identifier, data):
    """CAS refresh of a never-opened or positively unsubmitted frozen draft."""
    names = {'expected_revision', 'expected_review_sha256', 'expected_context_sha256', 'confirmed', 'fields'}
    with closing(store._connect(database)) as connection, connection:
        connection.execute('BEGIN IMMEDIATE')
        record = _get(connection, cycle_id, identifier)
        _review_input(record, data, names)
        if not _decorate(connection, cycle_id, record)['can_refresh_context']:
            raise ValueError('只有从未打开或已证明受控结束且未提交的同一主体草稿可采用当前配置；未知或已有回执保持锁定')
        cycle, target, context = _context(connection, cycle_id, record['target_id'])
        if data.get('expected_context_sha256') != digest(context):
            raise ValueError('当前目标或资料再次变化，请刷新后重新核对')
        fields = validate_fields(data.get('fields', record['fields']), cycle['profile'])
        controlled_closed = _controlled_closed_draft(record)
        if controlled_closed and fields != record['fields']:
            raise ValueError('已打开过的申请七字段保持逐字冻结；只能重新核对当前目标配置')
        history = list(record.get('history', []))
        if len(history) >= 50:
            raise ValueError('已保留50次草稿配置变更，请检查原记录，不会删除历史')
        history.append({'revision': record['revision'], 'context_snapshot': record['context_snapshot'],
            'context_sha256': record['context_sha256'], 'fields': record['fields'],
            'review_sha256': record['review_sha256'], 'reviewed': record['reviewed'],
            'stage': record['stage'], 'session_count': record['session_count'],
            'controlled_close_evidence': record.get('no_submission_evidence') if controlled_closed else None,
            'controlled_closed_at': record.get('controlled_closed_at') if controlled_closed else None,
            'replaced_at': _now(), 'reason': 'explicit_current_target_review'})
        record.update(context_sha256=digest(context), context_snapshot=context, target_revision=target['revision'],
            fields=fields, history=history, review_sha256=digest({'context': context, 'fields': fields}),
            revision=record['revision'] + 1, stage='draft', reviewed=False, open_nonce=None,
            message='已保留旧资料快照并采用当前目标配置；请重新核对七字段公开预览。')
        _put(connection, record)
        return _decorate(connection, cycle_id, record)


def review_application(database, cycle_id, identifier, data):
    with closing(store._connect(database)) as connection, connection:
        connection.execute('BEGIN IMMEDIATE')
        record = _get(connection, cycle_id, identifier)
        _assert_current(connection, cycle_id, record)
        _review_input(record, data, {'expected_revision', 'expected_review_sha256', 'confirmed'})
        if not _decorate(connection, cycle_id, record)['can_review']:
            raise ValueError('此申请已提交、结果未知或窗口未结束，不能重新授权发送')
        if record['stage'] == 'closed_not_submitted' and not _normal_no_submission(record.get('no_submission_evidence')):
            raise ValueError('缺少正常关窗且未发送的正面证据，不能解锁')
        record.update(stage='reviewed', reviewed=True, reviewed_at=_now(), open_nonce=secrets.token_urlsafe(32),
                      revision=record['revision'] + 1, message='已确认公开资料，请打开辅助窗口并在平台本人最终确认。')
        _put(connection, record)
        return _decorate(connection, cycle_id, record)


def reserve_session(database, cycle_id, identifier, review_sha256, open_nonce, *, native_task_id=None):
    """Private native bridge gate: atomically consume one UI authorization."""
    with closing(store._connect(database)) as connection, connection:
        connection.execute('BEGIN IMMEDIATE')
        record = _get(connection, cycle_id, identifier)
        _assert_current(connection, cycle_id, record)
        if (not _decorate(connection, cycle_id, record)['can_open'] or review_sha256 != record['review_sha256']
                or not isinstance(open_nonce, str) or not secrets.compare_digest(open_nonce, record['open_nonce'])):
            raise ValueError('公开资料确认已过期或此授权已使用，未重复打开申请')
        record.update(stage='opening', open_nonce=None, session_nonce=secrets.token_urlsafe(32), window_open=False,
            session_count=record['session_count'] + 1, revision=record['revision'] + 1,
            message='正在打开专用目录窗口；软件仅填写并回读，最终提交由本人操作。')
        if native_task_id is not None:
            if not isinstance(native_task_id, str) or not re.fullmatch(r'[0-9a-f]{32}', native_task_id):
                raise ValueError('原生任务编号无效')
            record['native_task_id'] = native_task_id
        record.pop('no_submission_evidence', None)
        record.pop('controlled_closed_at', None)
        _put(connection, record)
        return record


def _session(connection, cycle_id, identifier, session_nonce):
    record = _get(connection, cycle_id, identifier)
    if (record['cycle_id'] != cycle_id or not isinstance(session_nonce, str)
            or not secrets.compare_digest(session_nonce, record.get('session_nonce') or '')):
        raise ValueError('目录会话已过期，不能回放状态或提交')
    return record


def update_session(database, cycle_id, identifier, session_nonce, *, stage, window_open):
    if stage not in OPEN_STAGES or type(window_open) is not bool:
        raise ValueError('目录窗口状态无效')
    with closing(store._connect(database)) as connection, connection:
        connection.execute('BEGIN IMMEDIATE')
        record = _session(connection, cycle_id, identifier, session_nonce)
        if record['stage'] not in OPEN_STAGES or record.get('submit_intent'):
            raise ValueError('原申请状态已锁定，不接受迟到的窗口状态')
        record.update(stage=stage, window_open=window_open,
            message={'awaiting_human_confirmation': '七字段已逐字回读，请本人在平台核对并最终确认；软件不会点击提交。',
                     'awaiting_human_verification': '请本人在专用平台窗口完成正常登录或验证；软件不读取密码或验证码，也不代做验证。',
                     'needs_manual_review': '平台表单尚未核对一致，请在当前窗口检查；软件不会猜测控件或点击提交。',
                     'opening': '正在准备专用目录窗口，未点击提交。'}[stage])
        _put(connection, record)
        return _decorate(connection, cycle_id, record)


def mark_session_unknown(database, cycle_id, identifier, session_nonce, *, window_open):
    with closing(store._connect(database)) as connection, connection:
        connection.execute('BEGIN IMMEDIATE')
        record = _session(connection, cycle_id, identifier, session_nonce)
        if record['stage'] not in ('submitted_pending_review', 'accepted'):
            record.update(stage='submission_unknown', window_open=bool(window_open),
                message='检测到不匹配的申请、会话中断或提交结果未知；原记录已保留，不会自动重发。')
            _put(connection, record)
        return _decorate(connection, cycle_id, record)


def begin_submit(database, cycle_id, identifier, session_nonce, transport):
    with closing(store._connect(database)) as connection, connection:
        connection.execute('BEGIN IMMEDIATE')
        record = _session(connection, cycle_id, identifier, session_nonce)
        _assert_current(connection, cycle_id, record)
        if record['stage'] != 'awaiting_human_confirmation' or record.get('submit_intent'):
            raise ValueError('本申请已尝试提交或当前未确认，不会重复发送')
        if transport != expected_transport(record['fields']):
            raise ValueError('平台提交字段与已审阅七字段不一致，已阻止发送')
        record.update(submit_attempted=True, stage='submission_unknown', evidence_level='submission_intent',
            submit_intent={'at': _now(), 'method': 'POST', 'url': LEAD_URL, 'transport_sha256': digest(transport),
                           'review_sha256': record['review_sha256'], 'allowed_post_limit': 1},
            message='本人已触发匹配的提交；发送意图已保存，等待回执，不会再次发送。')
        _put(connection, record)
        return _decorate(connection, cycle_id, record)


def record_receipt(database, cycle_id, identifier, session_nonce, body, http_status):
    with closing(store._connect(database)) as connection, connection:
        connection.execute('BEGIN IMMEDIATE')
        record = _session(connection, cycle_id, identifier, session_nonce)
        if not record.get('submit_intent') or record['stage'] != 'submission_unknown' or record.get('receipt'):
            raise ValueError('没有对应的单次提交意图，或已有回执，不能重放')
        if (type(http_status) is not int or not 200 <= http_status < 300 or not isinstance(body, dict)
                or body.get('ok') is not True or body.get('persisted') is not True
                or not isinstance(body.get('intake_uid'), str)
                or not re.fullmatch(r'lead-[A-Za-z0-9_-]{3,100}', body['intake_uid'])):
            raise ValueError('平台尚未给出匹配的持久化回执，结果保持未知，请检查原申请')
        record.update(stage='submitted_pending_review', evidence_level='native_observed_receipt', source='software_assisted',
            receipt={'intake_uid': body['intake_uid'], 'ok': True, 'persisted': True, 'http_status': http_status,
                     'observed_at': datetime.now(timezone.utc).isoformat(timespec='seconds'), 'observed_at_precision': 'second', 'response_sha256': digest(body),
                     'review_sha256': record['review_sha256'], 'public_listing_verified': False},
            message='平台已保存线索，等待审核；不表示公开收录、AI引用或GEO效果。')
        _put(connection, record)
        return _decorate(connection, cycle_id, record)


def close_session(database, cycle_id, identifier, session_nonce, *, no_submission_evidence=None):
    with closing(store._connect(database)) as connection, connection:
        connection.execute('BEGIN IMMEDIATE')
        record = _session(connection, cycle_id, identifier, session_nonce)
        if record['stage'] in ('submitted_pending_review', 'accepted'):
            record['window_open'] = False
        elif (not record.get('submit_intent') and _normal_no_submission(no_submission_evidence)
                and record['stage'] in OPEN_STAGES):
            record.update(stage='closed_not_submitted', window_open=False, reviewed=False,
                no_submission_evidence=no_submission_evidence, controlled_closed_at=_now(),
                message='已确认本次正常关窗且没有发送申请；原七字段保留，重新核对后可再打开。')
        else:
            record.update(stage='submission_unknown', window_open=False,
                message='窗口中断、超时或回执未确认；原记录已锁定，请检查原平台，不会自动重发。')
        record['open_nonce'] = None
        record['session_nonce'] = None
        record['revision'] += 1
        _put(connection, record)
        return _decorate(connection, cycle_id, record)


def recover_interrupted(database, cycle_id, identifier):
    """Restart has no positive normal-close evidence, even with no intent."""
    with closing(store._connect(database)) as connection, connection:
        connection.execute('BEGIN IMMEDIATE')
        record = _get(connection, cycle_id, identifier)
        if record['cycle_id'] != cycle_id:
            raise ValueError('不能跨轮次恢复目录申请')
        if record['stage'] in (*OPEN_STAGES, 'submission_unknown'):
            record.update(stage='submission_unknown', window_open=False, session_nonce=None, open_nonce=None,
                          revision=record['revision'] + 1,
                          message='软件上次关闭时目录申请未结束，提交结果未知；请检查原平台，不会自动重发。')
            _put(connection, record)
        elif record['stage'] in ('submitted_pending_review', 'accepted') and record.get('window_open'):
            record.update(window_open=False, session_nonce=None, open_nonce=None, revision=record['revision'] + 1,
                message='上次软件会话已结束，已保存的平台回执保留；仅可只读核对，不会重发。')
            _put(connection, record)
        return _decorate(connection, cycle_id, record)


def _observed_time(value, precision):
    pattern = r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}' + (r':\d{2}' if precision == 'second' else '') + r'(?:Z|[+-]\d{2}:\d{2})'
    if precision not in ('minute', 'second') or not isinstance(value, str) or not re.fullmatch(pattern, value):
        raise ValueError('请按真实分钟或秒精度填写带时区的回执时间，不补造秒数')
    stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if stamp.tzinfo is None or stamp > datetime.now(timezone.utc):
        raise ValueError('回执时间须是已经发生且带时区的真实时间')
    return value


def register_external_receipt(database, cycle_id, identifier, data):
    names = {'expected_revision', 'expected_review_sha256', 'confirmed', 'status', 'intake_uid',
             'observed_at', 'observed_at_precision', 'note', 'evidence_sha256'}
    with closing(store._connect(database)) as connection, connection:
        connection.execute('BEGIN IMMEDIATE')
        record = _get(connection, cycle_id, identifier)
        _assert_current(connection, cycle_id, record)
        _review_input(record, data, names)
        if record['stage'] not in ('draft', 'reviewed', 'submission_unknown', 'closed_not_submitted') or record.get('window_open'):
            raise ValueError('已有回执或窗口仍在运行，请保留原申请，不能覆盖')
        if data.get('status') not in ('submitted_pending_review', 'accepted') or not isinstance(data.get('intake_uid'), str) or not re.fullmatch(r'lead-[A-Za-z0-9_-]{3,100}', data['intake_uid']):
            raise ValueError('请登记此目录的真实待审核或已接受线索回执编号')
        observed = _observed_time(data.get('observed_at'), data.get('observed_at_precision'))
        note = _safe_text(data.get('note'), 1000, required=True)
        evidence = data.get('evidence_sha256')
        if evidence is not None and (not isinstance(evidence, str) or not re.fullmatch(r'[0-9a-f]{64}', evidence)):
            raise ValueError('证据摘要须来自真实保存的证据；没有时请留空')
        receipt = {'intake_uid': data['intake_uid'], 'observed_at': observed,
                   'observed_at_precision': data['observed_at_precision'], 'note': note,
                   'review_sha256': record['review_sha256'], 'public_listing_verified': False,
                   'owner_reported': True}
        if evidence:
            receipt['evidence_sha256'] = evidence
        record.update(stage=data['status'], source='operator_assisted', evidence_level='owner_reported', receipt=receipt,
                      reviewed=True, open_nonce=None, session_nonce=None, revision=record['revision'] + 1,
                      message='已登记软件外的本人回执；不冒充原生提交，也不计为公开收录或GEO成效。')
        _put(connection, record)
        return _decorate(connection, cycle_id, record)
