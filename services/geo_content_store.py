"""Persistent research-to-article lineage, sharing the normal publishing DB."""
from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from services.geo_content_research import (
    CHANNELS, compare_reports, draft_input, fingerprint, normalize_facts,
    parse_draft, parse_research, plan_from_report, public_url, research_observations,
)
from services.geo_score_service import score_geo_content


class ResearchNotFoundError(LookupError):
    pass


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
    result['profile'] = result['baseline']['profile']
    result['test_plan'] = plan_from_report(result['baseline'])
    result['channels'] = CHANNELS
    return result


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
            original_host, profile_host = urlsplit(project['website']).hostname, urlsplit(profile['website']).hostname
            if original_host and profile_host and original_host.casefold().removeprefix('www.') != profile_host.casefold().removeprefix('www.'):
                raise ValueError('同名品牌的官网不同，请核对品牌项目与采集客户')
            project_id = project['id']
        else:
            project_id = connection.execute('''INSERT INTO projects(name,website,product,industry,description,keywords,competitors)
                VALUES(?,?,?,?,?,?,?)''', (profile['name'], profile['website'], profile['product'], profile['industry'],
                                          profile['description'], _json(profile['keywords']), _json(profile['competitors']))).lastrowid
        report_hash = fingerprint(report)
        prior = connection.execute('SELECT id FROM geo_content_cycles WHERE project_id=? AND report_digest=?', (project_id, report_hash)).fetchone()
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
        return [dict(row) for row in connection.execute('''
            SELECT c.id,c.project_id,c.report_ref,c.created_at,c.updated_at,p.name,
                (SELECT COUNT(*) FROM geo_content_drafts d WHERE d.cycle_id=c.id) AS draft_count
            FROM geo_content_cycles c JOIN projects p ON p.id=c.project_id ORDER BY c.updated_at DESC
        ''')]


def _drafts(connection, identifier):
    result = []
    for row in connection.execute('''SELECT d.*,a.title,a.summary,a.content,a.tags,a.status FROM geo_content_drafts d
            JOIN articles a ON a.id=d.article_id WHERE d.cycle_id=? ORDER BY d.created_at DESC''', (identifier,)):
        item = dict(row)
        item['tags'], item['provenance'] = json.loads(item['tags']), json.loads(item['provenance'])
        item['jobs'] = [dict(job) for job in connection.execute('''SELECT id,platform,status,result_url,message,demo,finished_at
            FROM publish_jobs WHERE article_id=? ORDER BY id DESC''', (item['article_id'],))]
        result.append(item)
    return result


def get_cycle(database, identifier):
    with closing(_connect(database)) as connection:
        result = _cycle(connection, identifier)
        result['drafts'] = _drafts(connection, identifier)
        publications = list(dict.fromkeys(job['result_url'] for draft in result['drafts'] for job in draft['jobs']
                                         if job['status'] == 'success' and not job['demo'] and public_url(job['result_url'])))
        result['publications'] = publications
        result['retests'] = []
        for row in connection.execute('SELECT report FROM geo_content_retests WHERE cycle_id=? ORDER BY created_at DESC', (identifier,)):
            report = json.loads(row['report'])
            comparison = compare_reports(result['baseline'], report, publications)
            comparison['publication_context'] = ('已记录真实成功发布链接；是否已被索引仍需观察。' if publications
                                                 else '尚未记录真实成功发布链接，本轮仅作为采集复测，不算发布效果验证。')
            result['retests'].append({'run_id': report['run_id'], 'updated_at': report.get('updated_at'),
                                      'comparison': comparison})
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


def attach_retest(database, identifier, report):
    plan_from_report(report)
    with closing(_connect(database)) as connection, connection:
        cycle = _cycle(connection, identifier)
        if report['run_id'] == cycle['baseline']['run_id']:
            raise ValueError('基线报告本身不能作为发布后的复测')
        connection.execute('INSERT OR REPLACE INTO geo_content_retests VALUES(?,?,?,?)',
                           (identifier, report['run_id'], _json(report), _now()))
    return get_cycle(database, identifier)
