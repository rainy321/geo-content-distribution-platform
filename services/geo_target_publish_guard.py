"""Validate source-driven article lineage before filling or built-in delivery."""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from urllib.parse import urlsplit

from services.platform_capability_service import get_platform_capability
from services.publish_policy_service import (
    COUNTED_JOB_STATUSES, PublishPolicyViolation, _job_effective_time,
    _max_existing_in_rolling_window, _normalize_reference_time,
)


PUBLIC_HOSTS = {
    'zhihu': {'zhihu.com', 'www.zhihu.com', 'zhuanlan.zhihu.com'},
    'toutiao': {'toutiao.com', 'www.toutiao.com'},
    'baijiahao': {'baijiahao.baidu.com'},
    'sohu': {'sohu.com', 'www.sohu.com'},
    'xiaohongshu': {'xiaohongshu.com', 'www.xiaohongshu.com'},
}


def configuration_digest(config):
    raw = json.dumps(config, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()


def builtin_publisher(config):
    channel = config.get('channel') or ''
    result = {'available': False, 'platform': channel, 'reason': ''}
    capability = get_platform_capability(channel)
    if config.get('enabled', True) is not True:
        return {**result, 'reason': '目标已停用'}
    if not capability or channel not in PUBLIC_HOSTS:
        return {**result, 'reason': '此渠道没有已核验的内置文章发布器，可使用辅助填写或导出'}
    if capability.get('delivery_state') != 'verified' or capability.get('requires_video') or capability.get('content_mode') not in ('article', 'image_article'):
        return {**result, 'reason': '此渠道尚需人工辅助，不能接入内置自动文章发布'}
    for field in ('source_scope', 'publication_scope'):
        try:
            url = urlsplit(config.get(field) or config.get('source_scope') or '')
            valid = (url.scheme == 'https' and url.hostname in PUBLIC_HOSTS[channel]
                     and not url.username and not url.password and url.port in (None, 443))
        except ValueError:
            valid = False
        if not valid:
            return {**result, 'reason': f'{field} 不属于 {capability["name"]} 的已知公开域名，请核对目标配置；不会猜测路由'}
    return {**result, 'available': True, 'reason': '可使用内置发布器；仍需账号、审稿、频率检查和最终确认'}


def article_target_context(connection, article_id, *, platform=None, require_builtin=False):
    """Return current target provenance, or None for an ordinary article."""
    if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='geo_content_drafts'").fetchone():
        return None
    row = connection.execute('''SELECT d.*,a.project_id,a.title,a.content,c.project_id AS cycle_project
        FROM geo_content_drafts d JOIN articles a ON a.id=d.article_id
        LEFT JOIN geo_content_cycles c ON c.id=d.cycle_id WHERE d.article_id=?''', (article_id,)).fetchone()
    if not row:
        return None
    try:
        provenance = json.loads(row['provenance'])
        if not isinstance(provenance, dict):
            raise ValueError('目标稿来源证据格式无效')
        key = row['platform']
        destination = provenance.get('destination')
        if not str(key).startswith('target_'):
            if destination:
                raise ValueError('目标稿关联格式不一致')
            return None
        identifier = key[len('target_'):]
        if not identifier or row['cycle_project'] != row['project_id'] or not isinstance(destination, dict) or destination.get('id') != identifier:
            raise ValueError('目标稿缺少同客户采集周期或目标关联证据')
        target = connection.execute('SELECT * FROM geo_distribution_targets WHERE id=? AND project_id=?',
                                    (identifier, row['project_id'])).fetchone()
        if not target:
            raise ValueError('目标已不存在或不属于当前客户')
        config = json.loads(target['config'])
        if not isinstance(config, dict):
            raise ValueError('目标配置证据格式无效')
        if config.get('enabled', True) is not True or target['state'] in ('disabled', 'archived', 'deleted'):
            raise ValueError('目标已停用')
        digest = configuration_digest(config)
        if (type(destination.get('revision')) is not int or destination['revision'] != target['revision']
                or provenance.get('destination_config_sha256') != digest):
            raise ValueError('目标配置与生成稿件时不一致，请按当前配置重新生成并审稿')
        if destination.get('channel') != config.get('channel', ''):
            raise ValueError('目标稿渠道证据与当前配置不一致')
        if platform is not None and platform != config.get('channel'):
            raise ValueError('发布任务渠道与目标配置不一致')
        if not str(row['title']).strip() or not str(row['content']).strip() or len(row['title']) > config['title_limit'] or len(row['content']) > config['body_limit']:
            raise ValueError('文章超过当前目标长度限制或内容为空，请重新编辑并审稿')
        if require_builtin:
            delivery = builtin_publisher(config)
            if not delivery['available']:
                raise ValueError(delivery['reason'])
            if target['blocked_reason'] or target['state'] == 'preparing':
                raise ValueError('目标存在待检查或正在填写的尝试，请先人工核对')
            if config.get('channel') == 'toutiao' and len(row['title']) > 30:
                raise ValueError('内置头条文字发布器要求标题不超过 30 字，请修改后重新审稿')
        authorization = {'id': identifier, 'revision': target['revision'], 'config_sha256': digest,
                         'cycle_id': row['cycle_id'], 'channel': config.get('channel', '')}
        return {'id': identifier, 'project_id': row['project_id'], 'cycle_id': row['cycle_id'],
                'config': config, 'authorization': authorization}
    except (KeyError, TypeError, json.JSONDecodeError) as error:
        raise ValueError('目标稿关联或配置证据不完整，请重新生成并审稿') from error


def assert_target_frequency(connection, target, *, exclude_job_id=None, exclude_attempt_job_id=None, publish_at=None, now=None):
    """Count assisted fills, real attempts and still-unattempted reservations."""
    identifier, config = target['id'], target['config']
    reference = _normalize_reference_time(publish_at, now=now)
    times = []
    from services.publish_retry_evidence import claim_job_id, latest_claim, reviewed_zero_write_claims
    events = connection.execute("SELECT * FROM geo_distribution_events WHERE target_id=? AND kind IN ('fill','builtin_publish')", (identifier,)).fetchall()
    reviewed = reviewed_zero_write_claims(connection, identifier, exclude_job_id) if exclude_job_id is not None else set()
    active = latest_claim(connection, identifier, exclude_attempt_job_id) if exclude_attempt_job_id is not None else None
    try:
        for event in events:
            if event['id'] in reviewed or active and event['id'] == active['id']:
                continue
            stamp = datetime.fromisoformat(event['created_at'].replace('Z', '+00:00'))
            times.append(stamp.replace(tzinfo=timezone.utc) if stamp.tzinfo is None else stamp.astimezone(timezone.utc))
    except (ValueError, TypeError):
        raise ValueError('目标频率记录无法验证，请先检查历史尝试')
    rows = connection.execute('''SELECT j.* FROM publish_jobs j JOIN geo_content_drafts d ON d.article_id=j.article_id
        WHERE d.platform=? AND j.demo=0''', ('target_' + identifier,)).fetchall()
    for row in rows:
        if row['id'] == exclude_job_id:
            continue
        if row['status'] == 'processing':
            raise ValueError('同一目标已有发布任务处理中或最终状态未知，请先人工核对；不会另发')
        if row['status'] in COUNTED_JOB_STATUSES and not any(claim_job_id(event) == row['id'] for event in events):
            stamp = _job_effective_time(row)
            if stamp:
                times.append(stamp)
    decision = {'allowed': False, 'target_id': identifier, 'reference_at': reference.isoformat(),
                'daily_limit': config['daily_limit'], 'interval_hours': config['interval_hours']}
    if _max_existing_in_rolling_window(times, reference) >= config['daily_limit']:
        raise PublishPolicyViolation('达到目标平台设置的滚动 24 小时操作阈值；这不是平台官方额度', decision={**decision, 'reason_code': 'target_rolling_limit'})
    if any(abs((reference - stamp).total_seconds()) < config['interval_hours'] * 3600 for stamp in times):
        raise PublishPolicyViolation('尚未达到目标平台设置的两次操作间隔；填写与内置发布共享更严格的阈值', decision={**decision, 'reason_code': 'target_minimum_interval'})


def record_builtin_attempt(connection, target, job_id, article_id):
    stamp = datetime.now(timezone.utc).isoformat()
    from services.publish_retry_evidence import latest_claim
    identifier = f'publish-job:{job_id}'
    if latest_claim(connection, target['id'], job_id):
        identifier += ':attempt:' + uuid.uuid4().hex
    connection.execute('''INSERT INTO geo_distribution_events(id,target_id,article_id,kind,state,message,created_at)
        VALUES(?,?,?,'builtin_publish','attempted','已领取内置发布任务；以发布任务最终结果为准',?)''',
        (identifier, target['id'], article_id, stamp))
