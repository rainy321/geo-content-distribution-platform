"""Observed source surfaces -> project-scoped, declarative distribution targets.

No network discovery and no executable recipes. A citation is evidence of a URL,
not proof that the customer can publish there or that a browser session is logged in.
"""
from __future__ import annotations

import ipaddress
import json
import re
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit, urlunsplit

from services.geo_publication_suitability import (
    normalize_publication_intent, publication_gate, publication_policy, assert_publication_allowed,
)
from services.geo_source_url import trusted_source_url


CATALOG_VERSION = '2026-10-21.3'


def _candidate(name, home, login, editor, style, channel='', rules_url='', **limits):
    return dict(name=name, home_url=home, login_url=login, editor_url=editor,
                source_scope=home, style=style, channel=channel, rules_url=rules_url, **limits)


CATALOG = [
    ('zhihu', ('zhihu.com', 'www.zhihu.com', 'zhuanlan.zhihu.com'), '/', _candidate(
        '知乎', 'https://www.zhihu.com/', 'https://www.zhihu.com/signin', 'https://zhuanlan.zhihu.com/write',
        '直接回答具体问题，提供推理、适用边界和可核验依据；避免堆品牌和伪装亲身体验。', 'zhihu')),
    ('toutiao', ('toutiao.com', 'www.toutiao.com'), '/', _candidate(
        '今日头条', 'https://www.toutiao.com/', 'https://mp.toutiao.com/', 'https://mp.toutiao.com/',
        '标题准确，先给结论再解释；技术说明需要有可核验的信息增量，不写纯广告、导购或私信引流。'
        '避免夸张标题、重复铺稿和无法证实的效果承诺。AI 创作内容不得声明原创，并按平台要求标注 AI 使用。'
        '这些提示不代表自动合规，发布前仍需人工核对平台当前规则。', 'toutiao',
        rules_url='https://baike.toutiao.com/detail/236/238/0', title_limit=30)),
    ('baijiahao', ('baijiahao.baidu.com',), '/', _candidate(
        '百家号', 'https://baijiahao.baidu.com/', 'https://baijiahao.baidu.com/', 'https://baijiahao.baidu.com/',
        '清晰说明问题和依据，保留事实出处；配图与账号权限需要单独检查。', 'baijiahao')),
    ('sohu', ('sohu.com', 'www.sohu.com'), '/a/', _candidate(
        '搜狐号', 'https://www.sohu.com/', 'https://mp.sohu.com/', 'https://mp.sohu.com/',
        '分段清晰，事实与观点分开，说明产品适用范围和公开资料出处。', 'sohu')),
    ('xiaohongshu', ('xiaohongshu.com', 'www.xiaohongshu.com'), '/', _candidate(
        '小红书', 'https://www.xiaohongshu.com/', 'https://creator.xiaohongshu.com/', 'https://creator.xiaohongshu.com/',
        '短段落和实用清单，不编造用户体验；商业关系如实说明，图片与平台规则另行检查。', 'xiaohongshu')),
    ('tencent_community', ('cloud.tencent.com', 'developer.cloud.tencent.com'), '/developer/', _candidate(
        '腾讯云开发者社区', 'https://cloud.tencent.com/developer/', 'https://cloud.tencent.com/developer/',
        'https://cloud.tencent.com/developer/article/write',
        '面向开发者提供原创技术信息与实际技术贡献，明确区分设计建议、假设示例和实测结果，使用 Markdown。'
        '当前社区规范不鼓励较多段落为 AI 生成的内容，并禁止广告推广和站外引流；'
        'AI 草稿或 AI 审校不能直接等同于实质人工贡献或获准自动投稿。'
        '发文可能要求腾讯云账号实名、手机验证及开通专栏，后续需核对封面等发布设置。'
        '编辑器可能自动保存草稿；权限、具体要求和内容是否可投须在当前账号页面核对。',
        rules_url='https://cloud.tencent.com/developer/article/1006434',
        publication_scope='https://cloud.tencent.com/developer/article/')),
    ('aliyun_community', ('developer.aliyun.com',), '/article/', _candidate(
        '阿里云开发者社区', 'https://developer.aliyun.com/', 'https://developer.aliyun.com/article/new',
        'https://developer.aliyun.com/article/new',
        '围绕开发者技术问题提供有依据、原创且有信息增量的内容，不虚构实测或案例。'
        '官方博文规范将机器人生成、软文推广和单纯站外引流列为低质或受限内容；'
        'AI 草稿须经人工实质审稿，不能据此直接自动发布。'
        '发文要求实名认证，当前账号的身份、投稿权限和编辑器能力仍需正常登录后核验。',
        rules_url='https://developer.aliyun.com/article/1639804')),
    ('csdn', ('blog.csdn.net',), '/', _candidate(
        'CSDN 博客', 'https://blog.csdn.net/', 'https://passport.csdn.net/login', 'https://editor.csdn.net/md/',
        '围绕技术问题提供步骤、设计检查和参考资料；未实测不能写成实测结果，不虚构人工审稿。'
        '如实说明商业关系，避免硬广告与站外引流；使用平台可见创作声明标注 AI 辅助。'
        '可能需要正常登录、安全验证及微信或手机号绑定，账号权限和额度以实际页面为准。',
        rules_url='https://blog.csdn.net/blogdevteam/article/details/113122012')),
    ('cnblogs', ('cnblogs.com', 'www.cnblogs.com'), '/', _candidate(
        '博客园', 'https://www.cnblogs.com/', 'https://account.cnblogs.com/', 'https://i.cnblogs.com/',
        '工程实践与原理为主，说明复现条件和参考资料，避免广告堆砌。')),
]


def _now():
    return datetime.now(timezone.utc).isoformat()


def _connect(database):
    connection = sqlite3.connect(database, timeout=15)
    connection.row_factory = sqlite3.Row
    connection.execute('PRAGMA foreign_keys=ON')
    return connection


def public_https(value):
    if not isinstance(value, str) or not 8 <= len(value) <= 1800 or any(c.isspace() or ord(c) < 32 for c in value):
        raise ValueError('平台入口应为完整的公开 HTTPS 网址')
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or '').lower().rstrip('.')
        if parsed.scheme != 'https' or not host or parsed.username or parsed.password or parsed.port not in (None, 443):
            raise ValueError()
        try:
            ipaddress.ip_address(host)
        except ValueError:
            if '.' not in host or host.endswith(('.local', '.localhost', '.internal', '.test', '.invalid')) or not re.fullmatch(r'[a-z0-9.-]+', host):
                raise ValueError()
        else:
            raise ValueError()
        if host == 'localhost' or '..' in host:
            raise ValueError()
        return urlunsplit(('https', host, parsed.path or '/', parsed.query, ''))
    except (ValueError, TypeError) as error:
        raise ValueError('平台入口应为不含账号密码的公开 HTTPS 域名网址') from error


def scope_matches(url, scope):
    if not url or not scope:
        return False
    try:
        left, right = urlsplit(url), urlsplit(scope)
        path = right.path.rstrip('/')
        return (left.scheme in ('http', 'https') and left.hostname == right.hostname
                and left.port in (None, 80, 443) and not left.username and not left.password
                and (not path or left.path == path or left.path.startswith(path + '/')))
    except ValueError:
        return False


def opportunities(observations, targets, website=''):
    """Group unique observed URL/question pairs; failed answers never rank targets."""
    usable = {case['id'] for case in observations['cases'] if case['usable']}
    grouped = {}
    for source in observations['sources']:
        if source['case_id'] not in usable:
            continue
        url = source.get('url')
        identity = trusted_source_url(url)
        host = (urlsplit(identity).hostname or '') if identity else ''
        candidate, surface, note = None, 'unknown', '还需核对作者入口、准入条件和平台规则。'
        key = host or ('url_untrusted' if url else 'url_unknown')
        if not url:
            surface, note = 'url_unknown', '只有标题，不能据此认定平台；需要补采来源网址。'
        elif not identity:
            note = '原始来源网址未通过平台身份检查，仅保留研究证据，不能据此推断接入平台。'
        elif scope_matches(identity, trusted_source_url(website)):
            surface, note = 'owned_site', '客户官网，可通过网站 CMS 或交给网站编辑更新。'
            key += ':owned'
        else:
            for catalog_key, hosts, path, entry in CATALOG:
                if host in hosts and (path == '/' or urlsplit(identity).path.startswith(path)):
                    key, surface, candidate = catalog_key, 'community_candidate', dict(entry)
                    # Bind only the observed surface, never all pages of a vendor domain.
                    candidate['source_scope'] = f'https://{host}{path}'
                    candidate['publication_policy'] = publication_policy(candidate)
                    note = '已匹配公开内容平台；账号是否获准投稿、当前入口和自动发布能力仍需验证。'
                    break
            if surface == 'unknown' and (host in ('cloud.tencent.com', 'help.aliyun.com', 'support.huaweicloud.com', 'support.huawei.com', 'www.huaweicloud.com', 'docs.newapi.pro')
                                         or host.startswith(('docs.', 'support.')) or '/document/' in urlsplit(identity).path):
                surface, note = 'vendor_documentation', '厂商文档或自有资料，不能当作可注册发文的平台；优先做引用关系或合作调研。'
                key += ':docs'
        item = grouped.setdefault(key, dict(id=key, domain=host or None, surface=surface, note=note,
            candidate=candidate, source_ids=[], case_ids=[], examples=[], matches=[], _pairs=set()))
        item['source_ids'].append(source['id'])
        if source['case_id'] not in item['case_ids']:
            item['case_ids'].append(source['case_id'])
        pair = (source['case_id'], url.split('#')[0] if isinstance(url, str) and url else source.get('title'))
        if pair not in item['_pairs']:
            item['_pairs'].add(pair)
            item['examples'].append({'title': source.get('title'), 'url': url})
        for target in targets:
            if (identity and scope_matches(identity, trusted_source_url(target['config']['source_scope']))
                    and target['id'] not in [match['id'] for match in item['matches']]):
                item['matches'].append({'id': target['id'], 'key': target['key'], 'name': target['config']['name'], 'basis': 'user_configured_scope'})
    result = []
    for item in grouped.values():
        item['observed_pairs'] = len(item.pop('_pairs'))
        item['priority_note'] = '按有效问题数排序，仅为接入研究优先级，不是平台收录率或效果预测。'
        item['title_leads'] = []
        if item['surface'] == 'url_unknown':
            for words, catalog_key in ((('腾讯云开发者社区',), 'tencent_community'), (('知乎',), 'zhihu'), (('博客园',), 'cnblogs'), (('CSDN', 'csdn'), 'csdn')):
                references = [source['id'] for source in observations['sources'] if source['id'] in item['source_ids'] and any(word in source['title'] for word in words)]
                if references:
                    candidate = next(dict(entry) for key, _, _, entry in CATALOG if key == catalog_key)
                    candidate['publication_policy'] = publication_policy(candidate)
                    item['title_leads'].append({'candidate': candidate, 'source_ids': references, 'basis': 'title_only_unverified',
                                                'note': '仅标题包含平台名称，未确认原网址；不计为已核实平台命中'})
        result.append(item)
    return sorted(result, key=lambda item: (item['surface'] == 'url_unknown', -len(item['case_ids']), -item['observed_pairs'], item['id']))


def _text(value, name, maximum, required=True):
    if not isinstance(value, str) or len(value.strip()) > maximum or (required and not value.strip()):
        raise ValueError(f'{name}不能为空且不能超过 {maximum} 字')
    return value.strip()


def normalize_config(value):
    if not isinstance(value, dict):
        raise ValueError('请填写平台配置')
    config = {field: public_https(value.get(field)) for field in ('home_url', 'login_url', 'editor_url', 'source_scope')}
    config['publication_scope'] = public_https(value.get('publication_scope') or config['source_scope'])
    config['name'] = _text(value.get('name'), '平台名称', 80)
    config['style'] = _text(value.get('style'), '平台内容规范', 3000)
    config['rules_url'] = public_https(value['rules_url']) if value.get('rules_url') else ''
    config['channel'] = value.get('channel') or ''
    config['enabled'] = value.get('enabled', True)
    if type(config['enabled']) is not bool:
        raise ValueError('目标启用状态必须为布尔值')
    if config['channel'] not in ('', 'zhihu', 'toutiao', 'baijiahao', 'sohu', 'xiaohongshu', 'owned_web'):
        raise ValueError('已有发布渠道无效')
    for field, default, low, high in (('daily_limit', 1, 1, 20), ('interval_hours', 6, 1, 168),
                                     ('title_limit', 100, 10, 180), ('body_limit', 15000, 100, 30000)):
        config[field] = value.get(field, default)
        if type(config[field]) is not int or not low <= config[field] <= high:
            raise ValueError(f'{field} 应为 {low} 到 {high} 的整数；这是软件内部阈值，不是平台许可额度')
    for field in ('title_selector', 'body_selector'):
        config[field] = _text(value.get(field, ''), '编辑框 CSS 选择器', 300, False)
        if config[field] and any(token in config[field].lower() for token in ('>>', 'javascript:', 'xpath=', 'text=', 'internal:', 'css=', '::-p-')):
            raise ValueError('只允许普通 CSS 选择器，不执行脚本或点击发布按钮')
    if bool(config['title_selector']) != bool(config['body_selector']):
        raise ValueError('标题和正文选择器需要同时配置，或同时留空')
    config.update(normalize_publication_intent(value))
    return config


INACTIVE_TARGET_STATES = frozenset(('disabled', 'archived', 'deleted'))


def assert_target_enabled(target):
    if target.get('config', {}).get('enabled', True) is not True or target.get('state') in INACTIVE_TARGET_STATES:
        raise ValueError('平台已停用，记录仍保留；请在编辑接入配置中明确启用后再操作')


def _target(row):
    from services.geo_target_publish_guard import builtin_publisher, configuration_digest
    from services.csdn_editor import supports as csdn_editor_supported
    value = dict(row)
    value['config'] = json.loads(value['config'])
    value['config_sha256'] = configuration_digest(value['config'])
    value['publication_gate'] = publication_gate(value['config'], value['state'])
    value['publication_policy'] = publication_policy(value['config'])
    value['key'] = 'target_' + value['id']
    value['capability'] = ('platform_assisted_fill' if csdn_editor_supported(value['config']) else
                           'configured_fill' if value['config']['title_selector'] else 'login_and_export')
    value['auto_publish_verified'] = False
    value['builtin_publisher'] = builtin_publisher(value['config'])
    if not value['publication_gate']['allowed']:
        value['builtin_publisher'] = {**value['builtin_publisher'], 'available': False, 'reason': value['publication_gate']['reason']}
    return value


def list_targets(database, project_id):
    with closing(_connect(database)) as connection:
        return targets_for_connection(connection, project_id)


def targets_for_connection(connection, project_id):
    return [_target(row) for row in connection.execute('SELECT * FROM geo_distribution_targets WHERE project_id=? ORDER BY created_at', (project_id,))]


def get_target(database, project_id, identifier):
    with closing(_connect(database)) as connection:
        row = connection.execute('SELECT * FROM geo_distribution_targets WHERE id=? AND project_id=?', (identifier, project_id)).fetchone()
        if not row:
            raise ValueError('当前客户的平台接入记录不存在')
        result = _target(row)
        result['events'] = [dict(event) for event in connection.execute('SELECT * FROM geo_distribution_events WHERE target_id=? ORDER BY created_at DESC LIMIT 30', (identifier,))]
        return result


def save_target(database, project_id, value, identifier=None, revision=None):
    config = normalize_config(value)
    updating = identifier is not None
    identifier = identifier or uuid.uuid4().hex
    stamp = _now()
    with closing(_connect(database)) as connection, connection:
        connection.execute('BEGIN IMMEDIATE')
        prior = connection.execute('SELECT * FROM geo_distribution_targets WHERE id=? AND project_id=?', (identifier, project_id)).fetchone()
        if prior:
            if connection.execute('''SELECT 1 FROM publish_jobs j JOIN geo_content_drafts d ON d.article_id=j.article_id
                WHERE d.platform=? AND j.demo=0 AND j.status='processing' LIMIT 1''', ('target_' + identifier,)).fetchone():
                raise ValueError('目标存在正在发布或最终状态未知的任务，请先核对结果；配置暂不能修改')
            if prior['revision'] != revision or prior['state'] == 'preparing':
                raise ValueError('平台配置已变化或正在填写，请刷新后再修改')
            # A normal edit must not revive an inactive record through the
            # legacy default. Reactivation requires an explicit enabled flag.
            state = prior['state']
            if 'enabled' not in value and (state in INACTIVE_TARGET_STATES or json.loads(prior['config']).get('enabled', True) is not True):
                config['enabled'] = False
            if state in INACTIVE_TARGET_STATES and value.get('enabled') is True:
                state = 'need_action' if prior['blocked_reason'] else 'configured'
            connection.execute('UPDATE geo_distribution_targets SET config=?,state=?,revision=revision+1,updated_at=? WHERE id=?',
                               (json.dumps(config, ensure_ascii=False), state, stamp, identifier))
        else:
            if updating or revision is not None:
                raise ValueError('平台接入记录不存在，不能跨客户修改')
            if connection.execute('SELECT COUNT(*) FROM geo_distribution_targets WHERE project_id=?', (project_id,)).fetchone()[0] >= 50:
                raise ValueError('每个客户当前最多配置 50 个平台')
            connection.execute('INSERT INTO geo_distribution_targets(id,project_id,config,created_at,updated_at) VALUES(?,?,?,?,?)',
                               (identifier, project_id, json.dumps(config, ensure_ascii=False), stamp, stamp))
        # In-flight AI drafts must not silently use a changed destination profile.
        connection.execute('UPDATE geo_content_cycles SET revision=revision+1 WHERE project_id=?', (project_id,))
    return get_target(database, project_id, identifier)


def record_event(database, identifier, kind, state, message, article_id=None, event_id=None):
    with closing(_connect(database)) as connection, connection:
        stamp = _now()
        if event_id:
            connection.execute('UPDATE geo_distribution_events SET state=?,message=? WHERE id=? AND target_id=?', (state, message[:800], event_id, identifier))
        else:
            event_id = uuid.uuid4().hex
            connection.execute('INSERT INTO geo_distribution_events VALUES(?,?,?,?,?,?,?)', (event_id, identifier, article_id, kind, state, message[:800], stamp))
        current = connection.execute('SELECT state,blocked_reason,config FROM geo_distribution_targets WHERE id=?', (identifier,)).fetchone()
        # A late browser result is history, never authorization to reactivate.
        if current['state'] in INACTIVE_TARGET_STATES or json.loads(current['config']).get('enabled', True) is not True:
            return event_id
        # A verified editor can supersede only a recorded login-only failure.
        # Saved cookies/closed windows are not proof, and an empty editor says
        # nothing about a previous fill or an unresolved publication.
        editor_observed = kind == 'login' and state == 'editor_ready'
        unresolved = False
        if editor_observed:
            unresolved = bool(connection.execute('''SELECT 1 FROM geo_distribution_events
                WHERE target_id=? AND kind='fill' AND state IN ('preparing','filled','need_action') LIMIT 1''',
                (identifier,)).fetchone() or connection.execute('''SELECT 1 FROM publish_jobs j
                JOIN geo_content_drafts d ON d.article_id=j.article_id
                WHERE d.platform=? AND j.demo=0
                  AND (j.status!='success' OR j.result_url IS NULL OR TRIM(j.result_url)='') LIMIT 1''',
                ('target_' + identifier,)).fetchone())
        clear_login_failure = False
        if editor_observed and not unresolved and current['state'] == 'need_action' and current['blocked_reason']:
            clear_login_failure = bool(
                connection.execute('''SELECT 1 FROM geo_distribution_events
                    WHERE target_id=? AND kind='login' AND state='need_action' AND message=? LIMIT 1''',
                    (identifier, current['blocked_reason'])).fetchone())
        if kind != 'login' or (current['blocked_reason'] == '' and not (editor_observed and unresolved)) or clear_login_failure:
            connection.execute('UPDATE geo_distribution_targets SET state=?,blocked_reason=?,updated_at=? WHERE id=?',
                               (state, message[:800] if state == 'need_action' else '', stamp, identifier))
    return event_id


def review_block(database, project_id, identifier, confirmed):
    if confirmed is not True:
        raise ValueError('请先核对平台页面、是否保存或发布过内容，再确认恢复')
    target = get_target(database, project_id, identifier)
    with closing(_connect(database)) as connection, connection:
        connection.execute('BEGIN IMMEDIATE')
        current = connection.execute('SELECT * FROM geo_distribution_targets WHERE id=? AND project_id=?', (identifier, project_id)).fetchone()
        assert_target_enabled(_target(current))
        if current['state'] == 'preparing':
            raise ValueError('填写任务尚未结束，请先等待或停止；软件重启会把中断尝试标为待检查')
        connection.execute('UPDATE geo_distribution_events SET state=\'reviewed\' WHERE target_id=? AND kind=\'fill\' AND state IN (\'preparing\',\'filled\',\'need_action\')', (identifier,))
    record_event(database, identifier, 'manual_review', 'configured', '用户已检查页面状态并明确恢复；频率记录仍保留')
    return get_target(database, project_id, target['id'])


def reserve_fill(database, project_id, identifier, article_id):
    """Atomic reservation before opening an editor; no automatic replay after a crash."""
    target = get_target(database, project_id, identifier)
    with closing(_connect(database)) as connection, connection:
        from services.geo_target_publish_guard import article_target_context, assert_target_frequency
        connection.execute('BEGIN IMMEDIATE')
        current = connection.execute('SELECT * FROM geo_distribution_targets WHERE id=?', (identifier,)).fetchone()
        if current['revision'] != target['revision']:
            raise ValueError('平台配置发生变化，请刷新')
        assert_publication_allowed(_target(current))
        if current['blocked_reason'] or current['state'] == 'preparing':
            raise ValueError('该平台存在待检查的尝试，请检查后明确恢复；不会自动重试')
        row = connection.execute('''SELECT a.*,d.platform FROM articles a JOIN geo_content_drafts d ON d.article_id=a.id
            WHERE a.id=? AND a.project_id=?''', (article_id, project_id)).fetchone()
        if not row or row['platform'] != target['key'] or row['status'] != 'ready':
            raise ValueError('只能填写当前客户、当前平台已审稿并标记就绪的文章')
        lineage = article_target_context(connection, article_id)
        if not lineage or lineage['id'] != identifier:
            raise ValueError('缺少当前目标稿件的可核验关联，请重新生成并审稿')
        config = target['config']
        from services.csdn_editor import supports as csdn_editor_supported
        if not config['title_selector'] and not csdn_editor_supported(config):
            raise ValueError('尚未配置标题和正文编辑框，当前只能打开登录或导出稿件')
        if len(row['title']) > config['title_limit'] or len(row['content']) > config['body_limit']:
            raise ValueError('文章超过当前平台配置的长度限制，请先编辑')
        if connection.execute('SELECT 1 FROM geo_distribution_events WHERE target_id=? AND article_id=? AND kind=\'fill\' AND state != \'reviewed\'', (identifier, article_id)).fetchone():
            raise ValueError('这篇文章已有填写尝试，请检查页面和记录；不会重复填写')
        assert_target_frequency(connection, lineage)
        event_id = uuid.uuid4().hex
        connection.execute('INSERT INTO geo_distribution_events VALUES(?,?,?,?,?,?,?)', (event_id, identifier, article_id, 'fill', 'preparing', '打开编辑器前保留尝试，未确认已发布', _now()))
        connection.execute('UPDATE geo_distribution_targets SET state=\'preparing\' WHERE id=?', (identifier,))
    return target, dict(row), event_id
