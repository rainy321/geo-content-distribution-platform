"""Account-owner statements for builtin tasks, without adapter reconciliation.

This module performs no platform access and never updates a job or article.
Platform identifiers stay inside strictly matched URL strings throughout.
"""
from __future__ import annotations

import hmac
import json
import re
import uuid
from contextlib import closing
from datetime import datetime, timedelta, timezone

from services.publish_confirmation import article_review_fingerprint, publish_confirmation_snapshot
from services.publish_job_service import publish_authorization_matches


def _identifier(value):
    return type(value) is int and 0 < value <= 9007199254740991


def _snapshot(value):
    return isinstance(value, str) and re.fullmatch(r'[0-9a-f]{64}', value) is not None


def _public_article_url(value):
    # Accept the canonical public article route only. In particular, no editor,
    # sign-in, profile, query/fragment, credentials, escaping or numeric coercion.
    if not isinstance(value, str) or not re.fullmatch(
            r'https://zhuanlan\.zhihu\.com/p/[1-9][0-9]{0,31}', value):
        raise ValueError('请填写知乎规范公开文章链接：https://zhuanlan.zhihu.com/p/文章编号')
    return value


def moderation_hold(connection, article_id):
    # Builtin articles cannot normally have target moderation events. A legacy
    # hold still must not disappear merely because the draft now has no target.
    return connection.execute('''SELECT id,state FROM geo_distribution_events
        WHERE article_id=? AND kind='moderation' AND state IN ('pending','rejected')
        ORDER BY created_at DESC,rowid DESC LIMIT 1''', (article_id,)).fetchone()


def records(connection, identifier):
    return [{**dict(row), 'kind': 'builtin_job', 'target_id': None,
             'published_at_basis': 'operator_reported_platform_time'}
            for row in connection.execute('''SELECT * FROM geo_builtin_publications
                WHERE cycle_id=? ORDER BY created_at DESC,id DESC''', (identifier,))]


def save_reported_publication(database, identifier, value):
    from services import geo_content_store as store
    from services.geo_external_baseline import baseline_id

    if not isinstance(value, dict) or value.get('kind') != 'builtin_job' or value.get('confirmed') is not True:
        raise ValueError('请确认这是已在平台提交的本篇文章；该登记尚未独立核验')
    if any(key in value for key in ('target_id', 'platform', 'evidence_level')):
        raise ValueError('内置渠道登记必须从真实任务确定平台和账号，不能指定目标或成功证据等级')
    article_id, job_id, account_id = (value.get(key) for key in ('article_id', 'job_id', 'account_id'))
    if not all(_identifier(item) for item in (article_id, job_id, account_id)):
        raise ValueError('请选择本轮文章、已尝试的真实任务和该任务绑定账号')
    review, snapshot = value.get('expected_review_fingerprint'), value.get('expected_confirmation_snapshot')
    if not _snapshot(review) or not _snapshot(snapshot):
        raise ValueError('缺少当前审稿或任务检查点，请刷新后核对')
    url = _public_article_url(value.get('url'))
    if value.get('published_at_precision') != 'minute':
        raise ValueError('请按平台显示的分钟精度登记发布时间，秒数未知')
    stamp = value.get('published_at')
    try:
        if not isinstance(stamp, str) or len(stamp) > 80:
            raise ValueError()
        published = datetime.fromisoformat(stamp.replace('Z', '+00:00'))
        if (published.tzinfo is None or published.second or published.microsecond
                or published.utcoffset().total_seconds() % 60):
            raise ValueError()
        published = published.astimezone(timezone.utc)
        if published > datetime.now(timezone.utc) + timedelta(minutes=5):
            raise ValueError()
    except (ValueError, OverflowError) as error:
        raise ValueError('发布时间需要含时区、精确到分钟，且不能是未来时间') from error
    note = value.get('note', '')
    if not isinstance(note, str) or len(note) > 1000:
        raise ValueError('发布备注不能超过 1000 字')
    with closing(store._connect(database)) as connection, connection:
        connection.execute('BEGIN IMMEDIATE')
        cycle = store._cycle(connection, identifier, for_update=True)
        article = connection.execute('''SELECT a.*,d.platform,d.brief_id,d.input_sha256
            FROM articles a JOIN geo_content_drafts d ON d.article_id=a.id
            WHERE a.id=? AND d.cycle_id=? AND a.project_id=?''',
            (article_id, identifier, cycle['project_id'])).fetchone()
        if not article or article['platform'] != 'zhihu':
            raise ValueError('文章必须属于本轮客户和已支持的内置知乎渠道，不能跨客户或平台登记')
        if article['status'] not in ('ready', 'published'):
            raise ValueError('请先审稿并把文章标记就绪，再登记平台提交结果')
        if not hmac.compare_digest(review, article_review_fingerprint(article)):
            raise ValueError('文章内容或审稿状态已变更，请刷新后重新核对')
        if store.fingerprint(store.draft_input(cycle, article['brief_id'], article['platform'])) != article['input_sha256']:
            raise ValueError('本轮写作来源或事实资料已变更，请先核对稿件来源，不能沿用旧任务登记')
        job_row = connection.execute('''SELECT j.*,a.title AS article_title,u.userName AS account_name,
                u.type AS account_type FROM publish_jobs j
            JOIN articles a ON a.id=j.article_id LEFT JOIN user_info u ON u.id=j.account_id
            WHERE j.id=?''', (job_id,)).fetchone()
        if (not job_row or job_row['demo'] or job_row['article_id'] != article_id
                or job_row['platform'] != article['platform'] or job_row['account_id'] != account_id
                or job_row['account_type'] != 9):
            raise ValueError('真实任务、稿件、知乎渠道或绑定账号不匹配，不能登记')
        if job_row['status'] not in ('failed', 'need_action') or not job_row['started_at']:
            raise ValueError('仅可登记已尝试且已停止的失败或待处理真实任务，不能登记排队、定时或处理中任务')
        try:
            attempted = datetime.fromisoformat(job_row['started_at'].replace('Z', '+00:00'))
            if attempted.tzinfo is None:
                attempted = attempted.replace(tzinfo=timezone.utc)
            if attempted > datetime.now(timezone.utc) + timedelta(minutes=5):
                raise ValueError()
        except (ValueError, TypeError, OverflowError) as error:
            raise ValueError('任务缺少有效的真实尝试时间，请先核对原任务') from error
        if published + timedelta(minutes=1) <= attempted:
            raise ValueError('平台显示的发布时间早于原任务尝试，不能绑定为本次人工登记')
        if not hmac.compare_digest(snapshot, publish_confirmation_snapshot(job_row)):
            raise ValueError('任务或绑定账号已变更，请刷新任务后重新核对')
        job = dict(job_row)
        try:
            job['images'] = json.loads(job['images'] or '[]')
            authorized = publish_authorization_matches(job, dict(article), connection=connection)
        except (ValueError, TypeError):
            authorized = False
        if not authorized:
            raise ValueError('原任务授权的内容、平台或账号已变更，不能用旧任务登记当前稿件')
        if connection.execute('''SELECT 1 FROM publish_jobs WHERE article_id=? AND platform=?
                AND demo=0 AND status IN ('queued','processing','scheduled') LIMIT 1''',
                (article_id, article['platform'])).fetchone():
            raise ValueError('这篇稿件还有排队、定时或处理中任务，请先核对，避免重复提交')
        if moderation_hold(connection, article_id):
            raise ValueError('本篇文章已记录待审核或审核未通过，不能用人工登记跳过原审核记录')
        for case in (cycle['baseline']['cases'] if cycle['baseline'] else []):
            if not case['assessment']['answer_evaluable']:
                continue
            try:
                baseline_time = datetime.fromisoformat(case['collected_at'].replace('Z', '+00:00'))
            except (ValueError, KeyError, TypeError, AttributeError):
                continue
            if baseline_time.tzinfo is not None and published <= baseline_time:
                raise ValueError('本轮内容的发布时间必须晚于本轮基线采集时间')
        # An URL cannot silently move across owner records or adapter receipts.
        if (connection.execute('SELECT 1 FROM geo_content_publications WHERE url=? LIMIT 1', (url,)).fetchone()
                or connection.execute('''SELECT 1 FROM publish_jobs WHERE result_url=?
                    AND status='success' AND demo=0 LIMIT 1''', (url,)).fetchone()):
            raise ValueError('该公开链接已有关联记录，请保留原记录，不能重复登记')
        article_sha = store.fingerprint({'title': article['title'], 'content': article['content']})
        immutable = {'cycle_id': identifier, 'article_id': article_id, 'job_id': job_id,
                     'platform': article['platform'], 'account_id': account_id,
                     'review_fingerprint': review, 'job_confirmation_snapshot': snapshot,
                     'source_input_sha256': article['input_sha256'], 'url': url,
                     'published_at': published.isoformat(timespec='minutes'), 'published_at_precision': 'minute',
                     'evidence_level': 'owner_reported', 'note': note.strip(), 'baseline_run_id': baseline_id(cycle),
                     'report_digest': cycle['report_digest'], 'plan_sha256': cycle['test_plan']['plan_sha256'],
                     'article_sha256': article_sha}
        prior = connection.execute('SELECT * FROM geo_builtin_publications WHERE job_id=? OR url=?',
                                   (job_id, url)).fetchall()
        if prior:
            if len(prior) != 1 or any(prior[0][key] != item for key, item in immutable.items()):
                raise ValueError('该任务或公开链接已有不同登记，已保留原记录，不会覆盖')
        else:
            record = {'id': uuid.uuid4().hex, **immutable, 'created_at': store._now()}
            connection.execute('INSERT INTO geo_builtin_publications (' + ','.join(record) + ') VALUES ('
                               + ','.join('?' for _ in record) + ')', tuple(record.values()))
            connection.execute('UPDATE geo_content_cycles SET updated_at=? WHERE id=?', (store._now(), identifier))
    return store.get_cycle(database, identifier)
