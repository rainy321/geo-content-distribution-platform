"""Persistent research-to-article lineage, sharing the normal publishing DB."""
from __future__ import annotations

import json
import re
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

from services.geo_content_research import (
    CHANNELS, compare_reports, draft_input, fingerprint, normalize_facts,
    parse_draft, parse_research, plan_from_report, public_url, publication_feedback, research_observations, public_research_observations,
)
from services.geo_score_service import score_geo_content
from services import geo_destination_service as destinations
from services.geo_entity_identity import same_entity_target, entity_measurement_signature
from services.geo_test_reports import project_report_evaluation
from services.geo_feedback_summary import collection_summary


class ResearchNotFoundError(LookupError):
    pass


MODERATION_STATES = frozenset(('pending', 'rejected', 'publicly_visible', 'unknown'))


def _now():
    return datetime.now(timezone.utc).isoformat()


def _json(value):
    return json.dumps(value, ensure_ascii=False)


def _connect(database):
    connection = sqlite3.connect(Path(database), timeout=15)
    connection.row_factory = sqlite3.Row
    connection.execute('PRAGMA foreign_keys=ON')
    return connection


def _cycle(connection, identifier):
    row = connection.execute('SELECT * FROM geo_content_cycles WHERE id=?', (identifier,)).fetchone()
    if not row:
        raise ResearchNotFoundError('内容迭代记录不存在')
    result = dict(row)
    for field in ('baseline', 'observations', 'analysis', 'facts'):
        result[field] = json.loads(result[field])
    # Re-evaluate the view, preserving the stored cycle identity, digest, plan,
    # timestamps and raw answer evidence. Existing cycles are never recreated.
    result['baseline'] = project_report_evaluation(result['baseline'])
    result['profile'] = result['baseline']['profile']
    result['test_plan'] = plan_from_report(result['baseline'])
    result['observations'] = public_research_observations(result['baseline'], result['observations'])
    result['destinations'] = destinations.targets_for_connection(connection, result['project_id'])
    result['channels'] = {**CHANNELS, **{target['key']: target['config']['name'] for target in result['destinations']}}
    result['opportunities'] = destinations.opportunities(result['observations'], result['destinations'], result['profile'].get('website', ''))
    result['fact_candidates'] = _fact_candidates(connection, result)
    return result


def _fact_candidates(connection, cycle):
    """Offer previous owner statements without silently confirming them again."""
    rows = connection.execute('''SELECT id,created_at,updated_at,facts,baseline FROM geo_content_cycles
        WHERE project_id=? AND id!=? AND created_at<=? AND facts!='[]' ORDER BY updated_at DESC,id DESC''',
        (cycle['project_id'], cycle['id'], cycle['created_at']))
    for row in rows:
        try:
            if not same_entity_target(json.loads(row['baseline'])['profile'], cycle['profile']):
                continue
            facts = normalize_facts(json.loads(row['facts']))
        except (ValueError, TypeError, KeyError):
            continue
        return {'source_cycle_id': row['id'], 'source_created_at': row['created_at'],
                'source_updated_at': row['updated_at'],
                'facts': [{**fact, 'confirmed': False, 'provenance': 'previous_cycle_requires_confirmation'} for fact in facts],
                'note': '同一品牌上轮已确认的资料，仅供复用；请检查是否仍准确并重新确认，保存后才能用于本轮写作。'}
    return None


def create_cycle(database, report, report_ref, project_id=None):
    plan = plan_from_report(report)
    observations = research_observations(report)
    profile = plan['profile']
    if project_id is not None and (type(project_id) is not int or project_id <= 0):
        raise ValueError('品牌项目编号无效')
    with closing(_connect(database)) as connection, connection:
        connection.execute('BEGIN IMMEDIATE')
        if project_id:
            project = connection.execute('SELECT * FROM projects WHERE id=?', (project_id,)).fetchone()
            if not project:
                raise ValueError('品牌项目不存在')
        else:
            project = connection.execute('SELECT * FROM projects WHERE name=? COLLATE NOCASE ORDER BY id LIMIT 1', (profile['name'],)).fetchone()
        if project:
            if project['name'].strip().casefold() != profile['name'].strip().casefold():
                raise ValueError('采集客户与所选品牌项目不一致，不能混入其他客户的内容')
            if not same_entity_target(dict(project), profile):
                raise ValueError('同名对象的公司/产品类型或官网路径不同，请核对品牌项目与采集客户')
            project_id = project['id']
        else:
            project_id = connection.execute('''INSERT INTO projects
                (name,website,product,industry,description,keywords,competitors,entity_type,company,audience,aliases,official_urls)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''', (profile['name'], profile['website'], profile['product'], profile['industry'],
                    profile['description'], _json(profile['keywords']), _json(profile['competitors']),
                    profile.get('entity_type', 'product'), profile.get('company', ''), profile.get('audience', ''),
                    _json(profile.get('aliases', [])), _json(profile.get('official_urls', [])))).lastrowid
        report_hash = fingerprint(report)
        prior = connection.execute('SELECT id FROM geo_content_cycles WHERE project_id=? AND report_digest=?', (project_id, report_hash)).fetchone()
        if not prior:
            # Assessment upgrades change a report view, not its original run.
            # Keep the stored digest and lineage; reuse only the same source
            # whose complete evidence still matches under the current rules.
            current_view = project_report_evaluation(report)
            current_view.pop('evaluation_projection', None)
            current_hash = fingerprint(current_view)
            for candidate in connection.execute('''SELECT id,baseline FROM geo_content_cycles
                    WHERE project_id=? AND report_ref=? ORDER BY created_at,id''', (project_id, report_ref)):
                try:
                    stored = json.loads(candidate['baseline'])
                    if (stored.get('run_id') != report['run_id']
                            or stored.get('plan_sha256') != report['plan_sha256']):
                        continue
                    stored_view = project_report_evaluation(stored)
                    stored_view.pop('evaluation_projection', None)
                    if fingerprint(stored_view) == current_hash:
                        prior = candidate
                        break
                except (ValueError, TypeError, KeyError, AttributeError):
                    continue
        if prior:
            identifier = prior['id']
        else:
            identifier = uuid.uuid4().hex
            stamp = _now()
            analysis = {key: observations[key] for key in ('method', 'diagnosis', 'briefs')}
            connection.execute('''INSERT INTO geo_content_cycles
                (id,project_id,report_ref,report_digest,baseline,observations,analysis,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?)''', (identifier, project_id, report_ref, report_hash, _json(report), _json(observations), _json(analysis), stamp, stamp))
    return get_cycle(database, identifier)


def list_cycles(database):
    with closing(_connect(database)) as connection:
        result = []
        for row in connection.execute('''
            SELECT c.id,c.project_id,c.report_ref,c.created_at,c.updated_at,p.name,c.baseline,
                (SELECT COUNT(*) FROM geo_content_drafts d WHERE d.cycle_id=c.id) AS draft_count
            FROM geo_content_cycles c JOIN projects p ON p.id=c.project_id ORDER BY c.updated_at DESC
        '''):
            item = dict(row)
            try:
                baseline = json.loads(item.pop('baseline'))
                profile = baseline.get('profile', {}) if isinstance(baseline, dict) else {}
                if not isinstance(profile, dict):
                    profile = {}
            except (TypeError, ValueError):
                profile = {}
            item['website'] = profile.get('website') if isinstance(profile.get('website'), str) else ''
            item['entity_type'] = profile.get('entity_type') if profile.get('entity_type') in ('company', 'product') else ''
            result.append(item)
        return result


def _drafts(connection, identifier):
    from services.publish_messages import public_publish_message
    result = []
    for row in connection.execute('''SELECT d.*,a.title,a.summary,a.content,a.tags,a.status FROM geo_content_drafts d
            JOIN articles a ON a.id=d.article_id WHERE d.cycle_id=? ORDER BY d.created_at DESC''', (identifier,)):
        item = dict(row)
        item['tags'], item['provenance'] = json.loads(item['tags']), json.loads(item['provenance'])
        item['article_sha256'] = fingerprint({'title': item['title'], 'content': item['content']})
        item['jobs'] = [dict(job) for job in connection.execute('''SELECT id,platform,status,result_url,message,demo,finished_at,
            platform_published_at,platform_published_precision,result_checked_at,result_check_evidence
            FROM publish_jobs WHERE article_id=? ORDER BY id DESC''', (item['article_id'],))]
        for job in item['jobs']:
            job['message'] = public_publish_message(job['message'], job['platform'])
        result.append(item)
    return result


def _moderation_rows(connection, article_id, target_id):
    if not target_id:
        return []
    return list(connection.execute('''SELECT rowid AS sequence,* FROM geo_distribution_events
        WHERE article_id=? AND target_id=? AND kind='moderation'
        ORDER BY created_at DESC,rowid DESC''', (article_id, target_id)))


def _moderation_view(row):
    value = {key: row[key] for key in ('id', 'article_id', 'target_id', 'state', 'created_at')}
    try:
        metadata = json.loads(row['message'])
    except (ValueError, TypeError):
        metadata = None
    if (isinstance(metadata, dict) and metadata.get('schema_version') == 1 and metadata.get('source') == 'operator_reported'
            and isinstance(metadata.get('note'), str) and isinstance(metadata.get('url'), str)
            and isinstance(metadata.get('article_sha256'), str)
            and re.fullmatch('[0-9a-f]{64}', metadata['article_sha256'])
            and type(metadata.get('content_confirmed')) is bool):
        value.update({key: metadata.get(key) for key in ('source', 'note', 'url', 'article_sha256', 'content_confirmed')})
    else:
        # Only fixed kind/state carry machine meaning. Never infer rejection
        # or approval from a login failure, free-text note or malformed JSON.
        value.update(source='unknown', note=row['message'], url='', article_sha256=None, content_confirmed=False)
    return value


def _publication_moderation(connection, article_id, target_id, article_sha256, url=None):
    """Unknown observations do not erase a prior pending/rejected decision.

    A public-content observation only supersedes a hold for the exact reviewed
    article version and URL. It remains an operator statement, never a network
    verifier or a synthetic successful publishing job.
    """
    for row in _moderation_rows(connection, article_id, target_id):
        state = row['state']
        if state in ('pending', 'rejected'):
            return _moderation_view(row)
        if state != 'publicly_visible':
            continue
        view = _moderation_view(row)
        if (view['source'] == 'operator_reported' and view['content_confirmed'] is True
                and view['article_sha256'] == article_sha256 and view['url']
                and (url is None or view['url'] == url)):
            return view
    return None


def _filter_publication_records(connection, records, drafts):
    contexts = {draft['article_id']: draft for draft in drafts}
    eligible, blocked = [], []
    for item in records:
        draft = contexts[item['article_id']]
        target_id = item.get('target_id') or (draft['platform'][7:] if draft['platform'].startswith('target_') else None)
        observation = _publication_moderation(connection, item['article_id'], target_id,
                                               draft['article_sha256'], item['url'])
        if observation and observation['state'] in ('pending', 'rejected'):
            blocked.append({**item, 'moderation_state': observation['state'],
                            'moderation_observation_id': observation['id']})
        else:
            eligible.append(item)
    return eligible, blocked


def get_cycle(database, identifier):
    with closing(_connect(database)) as connection:
        result = _cycle(connection, identifier)
        result['drafts'] = _drafts(connection, identifier)
        reported = [dict(row) for row in connection.execute('''
            SELECT id,article_id,target_id,target_revision,url,published_at,evidence_level,note,
                   baseline_run_id,report_digest,plan_sha256,article_sha256,created_at
            FROM geo_content_publications WHERE cycle_id=? ORDER BY created_at DESC,id DESC''', (identifier,))]
        publication_records = []
        for draft in result['drafts']:
            for job in draft['jobs']:
                if job['status'] != 'success' or job['demo'] or not public_url(job['result_url']):
                    continue
                # SQLite CURRENT_TIMESTAMP is UTC but has no offset. This is the
                # adapter completion time, not proof of the platform's index time.
                try:
                    finished = datetime.fromisoformat((job['platform_published_at'] or job['finished_at']).replace('Z', '+00:00'))
                    if finished.tzinfo is None:
                        finished = finished.replace(tzinfo=timezone.utc)
                    stamp = finished.isoformat()
                except (ValueError, TypeError, AttributeError):
                    stamp = None
                publication_records.append({'url': job['result_url'], 'article_id': draft['article_id'],
                    'job_id': job['id'], 'platform': job['platform'], 'published_at': stamp,
                    'published_at_basis': 'platform_visible_public_time' if job['platform_published_at'] else 'publisher_completion_time',
                    'published_at_precision': job['platform_published_precision'] or 'second',
                    'platform_published_at': job['platform_published_at'], 'result_checked_at': job['result_checked_at'],
                    'evidence_level': 'publisher_reported_success'})
        publication_records += reported
        publication_records, result['blocked_publications'] = _filter_publication_records(connection, publication_records, result['drafts'])
        result['publications'] = list(dict.fromkeys(item['url'] for item in publication_records
                                                   if item['evidence_level'] == 'publisher_reported_success'))
        result['reported_publications'] = [item for item in publication_records if item['evidence_level'] == 'owner_reported']
        result['moderation_observations'] = [_moderation_view(row) for row in connection.execute('''
            SELECT e.* FROM geo_distribution_events e
            JOIN geo_content_drafts d ON d.article_id=e.article_id
            JOIN articles a ON a.id=d.article_id
            JOIN geo_distribution_targets t ON t.id=e.target_id
            WHERE d.cycle_id=? AND a.project_id=? AND t.project_id=?
              AND d.platform='target_' || e.target_id AND e.kind='moderation'
            ORDER BY e.created_at DESC,e.rowid DESC''', (identifier, result['project_id'], result['project_id']))
            if row['state'] in MODERATION_STATES]
        publication_urls = list(dict.fromkeys(item['url'] for item in publication_records))
        result['retests'] = []
        for row in connection.execute('SELECT report FROM geo_content_retests WHERE cycle_id=? ORDER BY created_at DESC', (identifier,)):
            report = json.loads(row['report'])
            comparison = compare_reports(result['baseline'], report, publication_urls)
            comparison.update(publication_feedback(comparison, report, publication_records))
            result['retests'].append({'run_id': report['run_id'], 'updated_at': report.get('updated_at'),
                                      'comparison': comparison, 'collection': collection_summary(report)})
        from services.geo_research_probe_service import list_probes
        result['probes'] = list_probes(connection, identifier)
    return result


def _check_revision(cycle, revision):
    if type(revision) is not int or cycle['revision'] != revision:
        raise ValueError('采集任务单或事实资料已变更，本次 AI 结果未覆盖新资料，请重新核对')


def save_facts(database, identifier, facts, revision):
    facts = normalize_facts(facts)
    with closing(_connect(database)) as connection, connection:
        connection.execute('BEGIN IMMEDIATE')
        cycle = _cycle(connection, identifier)
        _check_revision(cycle, revision)
        if facts != cycle['facts']:
            connection.execute('UPDATE geo_content_cycles SET facts=?,revision=revision+1,updated_at=? WHERE id=?', (_json(facts), _now(), identifier))
    return get_cycle(database, identifier)


def save_analysis(database, identifier, raw, provenance, revision):
    with closing(_connect(database)) as connection, connection:
        connection.execute('BEGIN IMMEDIATE')
        cycle = _cycle(connection, identifier)
        _check_revision(cycle, revision)
        analysis = parse_research(raw, cycle, provenance)
        connection.execute('UPDATE geo_content_cycles SET analysis=?,revision=revision+1,updated_at=? WHERE id=?', (_json(analysis), _now(), identifier))
    return get_cycle(database, identifier)


def existing_draft(database, cycle, brief_id, platform):
    key = fingerprint(draft_input(cycle, brief_id, platform))
    with closing(_connect(database)) as connection:
        row = connection.execute('SELECT article_id FROM geo_content_drafts WHERE cycle_id=? AND input_sha256=?', (cycle['id'], key)).fetchone()
    return dict(row) if row else None


def save_draft(database, identifier, brief_id, platform, raw, provenance, revision):
    with closing(_connect(database)) as connection, connection:
        connection.execute('BEGIN IMMEDIATE')
        cycle = _cycle(connection, identifier)
        _check_revision(cycle, revision)
        inputs = draft_input(cycle, brief_id, platform)
        key = fingerprint(inputs)
        if provenance.get('input_sha256') and provenance['input_sha256'] != key:
            raise ValueError('生成期间目标平台或写作资料变化，请刷新；未保存过期稿件')
        prior = connection.execute('SELECT article_id FROM geo_content_drafts WHERE cycle_id=? AND input_sha256=?', (identifier, key)).fetchone()
        if prior:
            return {'article_id': prior['article_id'], 'cycle_id': identifier, 'already_saved': True}
        draft = parse_draft(raw, inputs)
        project = connection.execute('SELECT * FROM projects WHERE id=?', (cycle['project_id'],)).fetchone()
        score = score_geo_content(title=draft['title'], content=draft['content'], brand=project['name'], keywords=json.loads(project['keywords']))
        # Article and research lineage commit together; a crash cannot leave an
        # untraceable saved article or cause a second draft on retry.
        article_id = connection.execute('''INSERT INTO articles
            (project_id,title,summary,content,tags,geo_score,geo_analysis,status) VALUES(?,?,?,?,?,?,?,'draft')''',
            (cycle['project_id'], draft['title'], draft['summary'], draft['content'], _json(draft['tags']), score['score'],
             _json({'dimensions': score['dimensions'], 'suggestions': score['suggestions']}))).lastrowid
        context = {**provenance, 'report_digest': cycle['report_digest'], 'baseline_run_id': cycle['baseline']['run_id'],
                   'plan_sha256': cycle['test_plan']['plan_sha256'], 'brief': inputs['brief'], 'facts': inputs['facts'],
                   'used_fact_ids': draft['used_fact_ids'], 'review_state': 'draft_requires_editorial_review'}
        if inputs.get('destination'):
            context['destination'] = inputs['destination']
            context['destination_config_sha256'] = inputs['destination_config_sha256']
            context['distribution_basis'] = inputs['distribution_basis']
        stamp = _now()
        connection.execute('INSERT INTO geo_content_drafts VALUES(?,?,?,?,?,?,?)',
                           (article_id, identifier, brief_id, platform, key, _json(context), stamp))
        connection.execute('UPDATE geo_content_cycles SET updated_at=? WHERE id=?', (stamp, identifier))
    return {'article_id': article_id, 'cycle_id': identifier, 'already_saved': False}


def article_research(database, article_id):
    with closing(_connect(database)) as connection:
        row = connection.execute('SELECT * FROM geo_content_drafts WHERE article_id=?', (article_id,)).fetchone()
        if not row:
            return None
        result = dict(row)
        result['provenance'] = json.loads(result['provenance'])
        return result


def save_moderation_observation(database, identifier, value):
    """Save an idempotent operator observation, without claiming verification.

    The account holder checks the ordinary platform page. This function never
    accesses that page, changes article status, publishes, or clears a target's
    existing interruption/block record.
    """
    if not isinstance(value, dict) or value.get('confirmed') is not True:
        raise ValueError('请确认已在平台核对本篇文章；这条记录是运营方观察，软件未自动核验')
    observation_id = value.get('observation_id')
    if not isinstance(observation_id, str) or not re.fullmatch('[0-9a-f]{32}', observation_id):
        raise ValueError('观察编号无效，请刷新后重新记录')
    article_id, target_id, state = value.get('article_id'), value.get('target_id'), value.get('state')
    if type(article_id) is not int or article_id <= 0 or not isinstance(target_id, str) or not target_id:
        raise ValueError('请选择本轮文章和对应的目标平台')
    if not isinstance(state, str) or state not in MODERATION_STATES:
        raise ValueError('审核状态应为待审核、审核未通过、已查看公开正文或未知')
    expected = value.get('expected_article_sha256')
    if not isinstance(expected, str) or not re.fullmatch('[0-9a-f]{64}', expected):
        raise ValueError('缺少当前文章内容检查点，请刷新后核对')
    note = value.get('note', '')
    if not isinstance(note, str) or len(note) > 400:
        raise ValueError('观察备注不能超过 400 字')
    url = value.get('url', '')
    if not isinstance(url, str):
        raise ValueError('请填写具体公开文章链接，或留空')
    url = destinations.public_https(url) if url else ''
    content_confirmed = value.get('content_confirmed') is True
    if state == 'publicly_visible' and (not url or not content_confirmed):
        raise ValueError('请在公开链接核对本篇文章的标题与正文，再确认已查看公开内容；成功提示或链接本身不能证明公开可访问')
    message = _json({'schema_version': 1, 'source': 'operator_reported', 'note': note.strip(),
                     'url': url, 'article_sha256': expected, 'content_confirmed': content_confirmed})
    with closing(_connect(database)) as connection, connection:
        connection.execute('BEGIN IMMEDIATE')
        cycle = _cycle(connection, identifier)
        article = connection.execute('''SELECT a.*,d.platform FROM articles a
            JOIN geo_content_drafts d ON d.article_id=a.id
            WHERE a.id=? AND d.cycle_id=? AND a.project_id=?''',
            (article_id, identifier, cycle['project_id'])).fetchone()
        target = connection.execute('SELECT * FROM geo_distribution_targets WHERE id=? AND project_id=?',
                                    (target_id, cycle['project_id'])).fetchone()
        if not article or not target or article['platform'] != 'target_' + target_id:
            raise ValueError('文章必须属于本轮客户与所选目标平台，不能跨客户或跨平台登记')
        config = json.loads(target['config'])
        if url:
            if (not destinations.scope_matches(url, config.get('publication_scope') or config['source_scope'])
                    or url in [destinations.public_https(config[field]) for field in ('home_url', 'login_url', 'editor_url')]):
                raise ValueError('请填写该平台范围内的具体公开文章链接，不能使用首页、登录页或编辑器入口')
            prior_url = connection.execute('''SELECT article_id,target_id FROM geo_content_publications
                WHERE url=? AND (article_id!=? OR target_id!=?) LIMIT 1''', (url, article_id, target_id)).fetchone()
            if prior_url:
                raise ValueError('该链接已经关联其他文章或目标，不能用作本篇文章的公开观察')
        prior = connection.execute('SELECT * FROM geo_distribution_events WHERE id=?', (observation_id,)).fetchone()
        if prior:
            if ((prior['target_id'], prior['article_id'], prior['kind'], prior['state'], prior['message'])
                    != (target_id, article_id, 'moderation', state, message)):
                raise ValueError('这个观察编号已有不同记录，已保留原记录，不会覆盖')
        else:
            if fingerprint({'title': article['title'], 'content': article['content']}) != expected:
                raise ValueError('文章内容已变更，请刷新后重新核对审核状态与公开正文')
            stamp = _now()
            connection.execute('''INSERT INTO geo_distribution_events
                (id,target_id,article_id,kind,state,message,created_at) VALUES(?,?,?,?,?,?,?)''',
                (observation_id, target_id, article_id, 'moderation', state, message, stamp))
            connection.execute('UPDATE geo_content_cycles SET updated_at=? WHERE id=?', (stamp, identifier))
    return get_cycle(database, identifier)


def save_reported_publication(database, identifier, value):
    """Record an account owner's statement, never forge an adapter success job."""
    if not isinstance(value, dict) or value.get('confirmed') is not True:
        raise ValueError('请确认这是你在对应平台已提交的文章链接；软件尚未独立核验')
    article_id, target_id = value.get('article_id'), value.get('target_id')
    if type(article_id) is not int or article_id <= 0 or not isinstance(target_id, str) or not target_id:
        raise ValueError('请选择本轮文章和对应的目标平台')
    url = destinations.public_https(value.get('url'))
    stamp = value.get('published_at')
    if not isinstance(stamp, str) or len(stamp) > 80:
        raise ValueError('发布时间需要含时区的 ISO 日期时间')
    try:
        published = datetime.fromisoformat(stamp.replace('Z', '+00:00'))
        if published.tzinfo is None:
            raise ValueError()
        published = published.astimezone(timezone.utc)
        if published > datetime.now(timezone.utc) + timedelta(minutes=5):
            raise ValueError()
    except (ValueError, OverflowError) as error:
        raise ValueError('发布时间需要含时区，且不能是未来时间') from error
    note = value.get('note') or ''
    if not isinstance(note, str) or len(note) > 1000:
        raise ValueError('发布备注不能超过 1000 字')
    with closing(_connect(database)) as connection, connection:
        connection.execute('BEGIN IMMEDIATE')
        cycle = _cycle(connection, identifier)
        row = connection.execute('''SELECT a.*,d.platform FROM articles a
            JOIN geo_content_drafts d ON d.article_id=a.id
            WHERE a.id=? AND d.cycle_id=? AND a.project_id=?''', (article_id, identifier, cycle['project_id'])).fetchone()
        target_row = connection.execute('SELECT * FROM geo_distribution_targets WHERE id=? AND project_id=?',
                                        (target_id, cycle['project_id'])).fetchone()
        if not row or not target_row or row['platform'] != 'target_' + target_id:
            raise ValueError('文章必须属于本轮客户与所选目标平台，不能跨客户或跨平台登记')
        if row['status'] not in ('ready', 'published'):
            raise ValueError('请先审稿并把文章标记就绪，再登记平台提交结果')
        config = json.loads(target_row['config'])
        if not destinations.scope_matches(url, config.get('publication_scope') or config['source_scope']):
            raise ValueError('发布链接不在该平台配置的公开网址范围内，请核对链接或平台配置')
        if url in [destinations.public_https(config[field]) for field in ('home_url', 'login_url', 'editor_url')]:
            raise ValueError('请填写具体的公开文章链接，不能使用平台首页、登录页或编辑器入口')
        observation = _publication_moderation(connection, article_id, target_id,
            fingerprint({'title': row['title'], 'content': row['content']}), url)
        if observation and observation['state'] in ('pending', 'rejected'):
            raise ValueError('本篇文章已记录待审核或审核未通过，不能登记为公开发布；请先核对该文章的公开正文，保留原审核记录')
        for case in cycle['baseline']['cases']:
            if not case['assessment']['answer_evaluable']:
                continue
            try:
                baseline_time = datetime.fromisoformat(case['collected_at'].replace('Z', '+00:00'))
            except (ValueError, KeyError, TypeError, AttributeError):
                continue
            if baseline_time.tzinfo is not None and published <= baseline_time:
                raise ValueError('本轮内容的发布时间必须晚于本轮基线采集时间')
        prior = connection.execute('SELECT article_id,target_id FROM geo_content_publications WHERE cycle_id=? AND url=?', (identifier, url)).fetchone()
        if prior:
            if prior['article_id'] != article_id or prior['target_id'] != target_id:
                raise ValueError('该链接已登记给另一篇文章，不能重复认作不同内容')
        else:
            connection.execute('''INSERT INTO geo_content_publications
                (id,cycle_id,article_id,target_id,target_revision,target_snapshot,url,published_at,evidence_level,note,
                 baseline_run_id,report_digest,plan_sha256,article_sha256,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (uuid.uuid4().hex, identifier, article_id, target_id, target_row['revision'], target_row['config'], url,
                 published.isoformat(), 'owner_reported', note.strip(), cycle['baseline']['run_id'], cycle['report_digest'],
                 cycle['test_plan']['plan_sha256'], fingerprint({'title': row['title'], 'content': row['content']}), _now()))
            connection.execute('UPDATE geo_content_cycles SET updated_at=? WHERE id=?', (_now(), identifier))
    return get_cycle(database, identifier)


def attach_retest(database, identifier, report):
    plan_from_report(report)
    with closing(_connect(database)) as connection, connection:
        cycle = _cycle(connection, identifier)
        if report['run_id'] == cycle['baseline']['run_id']:
            raise ValueError('基线报告本身不能作为发布后的复测')
        if report['profile']['name'].strip().casefold() != cycle['profile']['name'].strip().casefold():
            raise ValueError('复测客户与本轮品牌不同，不能关联其他客户的报告')
        if not same_entity_target(cycle['profile'], report['profile']):
            raise ValueError('复测对象的公司/产品类型或官网路径与本轮品牌不同，请核对报告')
        if entity_measurement_signature(cycle['profile']) != entity_measurement_signature(report['profile']):
            raise ValueError('复测的对象别名、官网范围或历史评估口径不同，请建立新的搜索基线')
        connection.execute('INSERT OR REPLACE INTO geo_content_retests VALUES(?,?,?,?)',
                           (identifier, report['run_id'], _json(report), _now()))
    return get_cycle(database, identifier)
