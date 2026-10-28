"""Typed, owner-provided baseline; never a native GEO search report."""
from __future__ import annotations
import copy
import hashlib
import json
import re
import uuid
from contextlib import closing

from services.geo_content_research import fingerprint
from services.geo_external_baseline_contract import (
    profile_for_project, profile_sha256, prepare_plan, question_sha256, validate_request_id,
)

KIND = 'geo_external_baseline_v1'
SOURCE = 'user_reported_external'
CAPABILITIES = {'native_probe': False, 'native_retest': False, 'native_metrics': False,
                'editorial_research': True, 'work_drafting': True, 'external_observations': True}


class ExternalBaselineConflict(Exception):
    pass


def _json(value):
    return json.dumps(value, ensure_ascii=False)


def _profile(connection, project_id):
    from services.geo_content_store import ResearchNotFoundError
    if type(project_id) is not int or project_id <= 0:
        raise ValueError('请选择已保存的品牌项目')
    row = connection.execute('SELECT * FROM projects WHERE id=?', (project_id,)).fetchone()
    if row is None:
        raise ResearchNotFoundError('品牌项目不存在')
    return profile_for_project(dict(row))


def _digest(value, field):
    if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{64}', value):
        raise ValueError(field + '摘要无效')


def _required(payload, fields):
    if not isinstance(payload, dict) or set(payload) != set(fields):
        raise ValueError('请仅提交本次操作要求的字段')


def _request(connection, project_id, operation, request_id, request_sha):
    validate_request_id(request_id)
    prior = connection.execute('''SELECT * FROM geo_external_baseline_requests
        WHERE project_id=? AND operation=? AND request_id=?''', (project_id, operation, request_id)).fetchone()
    if prior is not None and prior['request_sha256'] != request_sha:
        raise ExternalBaselineConflict('同一次请求编号已对应其他内容，请保留原记录并使用新的请求编号')
    return prior


def _frozen(connection, identifier):
    from services.geo_content_store import ResearchNotFoundError
    if not isinstance(identifier, str) or not re.fullmatch('[0-9a-f]{32}', identifier):
        raise ValueError('冻结计划编号无效')
    row = connection.execute('SELECT * FROM geo_external_baseline_plans WHERE id=?', (identifier,)).fetchone()
    if row is None:
        raise ResearchNotFoundError('冻结计划不存在')
    result = {'id': row['id'], 'project_id': row['project_id'], 'profile': json.loads(row['profile']),
            'profile_sha256': row['profile_sha256'], 'plan': json.loads(row['plan']),
            'plan_sha256': row['plan_sha256'], 'revision': 1}
    result['case_markers'] = question_markers(result['plan'])
    return result


def question_markers(plan):
    return {case['id']: {'question_with_url': question_has_url(case['question'], plan['profile']),
                        'category': case['category'], 'question_sha256': question_sha256(case['question']),
                        'basis': 'frozen_plan_text'} for case in plan['cases']}


def question_has_url(question, profile):
    from services.geo_research_probe_service import _address_present
    return _address_present(question, profile) is True or bool(re.search(r'https?://\S+', question, re.I))


def _receipt(payload, digest, reused):
    return {'request_id': payload['request_id'], 'request_sha256': digest, 'idempotent': reused}


def prepare(database, payload):
    from services.geo_content_store import _connect
    if not isinstance(payload, dict) or set(payload) - {'project_id', 'existing_plan'} or 'project_id' not in payload:
        raise ValueError('请提交品牌项目及可选的已有问题计划')
    with closing(_connect(database)) as connection:
        profile = _profile(connection, payload['project_id'])
    return prepare_plan(profile, payload.get('existing_plan'))


def list_pending_plans(database, project_id, *, offset=0):
    """Restore only the explicitly selected project's immutable, unused plans."""
    from services.geo_content_store import _connect
    if type(offset) is not int or not 0 <= offset <= 1000000:
        raise ValueError('冻结计划页码无效')
    with closing(_connect(database)) as connection:
        connection.execute('BEGIN')
        current_sha = profile_sha256(_profile(connection, project_id))
        rows = connection.execute('''SELECT p.id,p.created_at FROM geo_external_baseline_plans p
            WHERE p.project_id=? AND NOT EXISTS (
                SELECT 1 FROM geo_external_baseline_requests used
                WHERE used.project_id=p.project_id AND used.operation='create'
                  AND used.frozen_plan_id=p.id AND used.cycle_id IS NOT NULL)
            ORDER BY p.created_at DESC,p.id DESC LIMIT 21 OFFSET ?''', (project_id, offset)).fetchall()
        plans = []
        for row in rows[:20]:
            frozen = _frozen(connection, row['id'])
            plans.append({**frozen, 'created_at': row['created_at'],
                          'current_profile_matches': frozen['profile_sha256'] == current_sha})
    has_more = len(rows) > 20
    return {'plans': plans, 'has_more': has_more, 'next_offset': offset + 20 if has_more else None}


def freeze(database, payload):
    from services.geo_content_store import _connect, _now
    _required(payload, {'request_id', 'project_id', 'expected_profile_sha256', 'expected_plan_sha256', 'plan', 'confirmed'})
    validate_request_id(payload['request_id'])
    if type(payload['project_id']) is not int or payload['project_id'] <= 0 or not isinstance(payload['plan'], dict):
        raise ValueError('请先核对已保存品牌和原题，再明确确认冻结')
    _digest(payload['expected_profile_sha256'], '品牌资料'); _digest(payload['expected_plan_sha256'], '问题计划')
    request_sha = fingerprint({'operation': 'freeze_external_baseline_plan', 'payload': payload})
    with closing(_connect(database)) as connection, connection:
        connection.execute('BEGIN IMMEDIATE')
        prior = _request(connection, payload['project_id'], 'freeze', payload['request_id'], request_sha)
        if prior is not None:
            return {'frozen_plan': _frozen(connection, prior['frozen_plan_id']), 'receipt': _receipt(payload, request_sha, True)}
        if payload['confirmed'] is not True:
            raise ValueError('请先核对原题，再明确确认冻结')
        profile = _profile(connection, payload['project_id'])
        if profile_sha256(profile) != payload['expected_profile_sha256']:
            raise ExternalBaselineConflict('品牌资料已变化，请重新准备和核对原题')
        planned = prepare_plan(profile, payload['plan'])
        planner = payload['plan'].get('planner')
        if isinstance(planner, dict) and planner.get('method') == 'template_not_ai':
            template = prepare_plan(profile)
            if planned['plan_sha256'] == template['plan_sha256']:
                planned = template
        if planned['plan_sha256'] != payload['expected_plan_sha256']:
            raise ExternalBaselineConflict('原题计划已变化，请重新核对后冻结')
        identifier, stamp = uuid.uuid4().hex, _now()
        connection.execute('INSERT INTO geo_external_baseline_plans VALUES(?,?,?,?,?,?,?)',
            (identifier, payload['project_id'], planned['profile_sha256'], planned['plan_sha256'],
             _json(profile), _json(planned['plan']), stamp))
        connection.execute('INSERT INTO geo_external_baseline_requests VALUES(?,?,?,?,?,?,?,?)',
            (payload['project_id'], 'freeze', payload['request_id'], request_sha, identifier, None, None, stamp))
        return {'frozen_plan': _frozen(connection, identifier), 'receipt': _receipt(payload, request_sha, False)}


def check_profile_current(connection, cycle):
    if cycle.get('baseline_source') != SOURCE:
        return
    expected = cycle['external_baseline']['profile_sha256']
    if profile_sha256(_profile(connection, cycle['project_id'])) != expected:
        raise ExternalBaselineConflict('品牌资料已变化，旧外部轮保留；请切回原资料或为新对象准备独立一轮')


def external_context(cycle, case_id):
    if not isinstance(case_id, str):
        raise ValueError('请选择冻结计划中的一道原题')
    rows = [item for item in cycle['test_plan']['cases'] if item['id'] == case_id]
    if len(rows) != 1:
        raise ValueError('所选问题不属于本轮冻结计划')
    records = cycle['external_baseline'].get('observations', [])
    before = next((copy.deepcopy(item['after']) for item in records if item['case_id'] == case_id), None)
    if before is None:
        before = {key: None for key in ('collected_at', 'client_mode', 'model_label', 'answer_evaluable',
                  'answer_error', 'website_presence', 'official_source_present', 'official_citation_present',
                  'citations', 'displayed_sources', 'collection_status', 'failure_reason')}
        before['response'] = ''
    before.update(source=SOURCE, native_verified=False)
    return {'case': copy.deepcopy(rows[0]), 'external_before': before}


def create(database, payload):
    from services.geo_content_store import _connect, _now, get_cycle
    from services.geo_research_probe_service import attach_external_observation
    _required(payload, {'request_id', 'project_id', 'frozen_plan_id', 'expected_profile_sha256',
                        'expected_plan_sha256', 'first_observation'})
    validate_request_id(payload['request_id'])
    if type(payload['project_id']) is not int or payload['project_id'] <= 0:
        raise ValueError('品牌项目编号无效')
    _digest(payload['expected_profile_sha256'], '品牌资料'); _digest(payload['expected_plan_sha256'], '问题计划')
    observation = payload['first_observation']
    fields = {'case_id', 'expected_question_sha256', 'raw_response_text'}
    if not isinstance(observation, dict) or not fields <= set(observation) or set(observation) - fields - {'metadata'}:
        raise ValueError('首条外部观察须明确关联冻结原题并提供回答原文')
    metadata = observation.get('metadata', {})
    if not isinstance(metadata, dict) or set(metadata) - {'question', 'model_label', 'client_mode', 'collected_at', 'answer_complete', 'note', 'source_links'}:
        raise ValueError('首条记录只能包含用户提供的条件与来源，不接受原生验证或业务判定')
    raw = observation['raw_response_text']
    if not isinstance(observation['case_id'], str):
        raise ValueError('关联原题编号无效')
    _digest(observation['expected_question_sha256'], '关联原题')
    request_sha = fingerprint({'operation': 'create_external_baseline_cycle', 'payload': payload})
    reused = False
    with closing(_connect(database)) as connection, connection:
        connection.execute('BEGIN IMMEDIATE')
        prior = _request(connection, payload['project_id'], 'create', payload['request_id'], request_sha)
        if prior is not None:
            cycle_id, probe_id, reused = prior['cycle_id'], prior['probe_id'], True
        else:
            if not isinstance(raw, str) or not raw.strip() or len(raw.encode('utf-8')) > 1024*1024:
                raise ValueError('请粘贴非空的外部回答原文，UTF8内容不能超过1MiB')
            if '\n' not in raw and '\r' not in raw and re.match(
                    r'^(?:[a-zA-Z]:[\\/]|\\\\|file:|/|\.{1,2}[\\/]|~[\\/])', raw.strip(), re.I):
                raise ValueError('请粘贴回答原文，不接受本地文件路径')
            frozen = _frozen(connection, payload['frozen_plan_id'])
            profile = _profile(connection, payload['project_id'])
            if (frozen['project_id'] != payload['project_id'] or frozen['profile_sha256'] != payload['expected_profile_sha256']
                    or frozen['plan_sha256'] != payload['expected_plan_sha256']
                    or profile_sha256(profile) != frozen['profile_sha256']):
                raise ExternalBaselineConflict('品牌、官网范围或冻结计划已变化，首条记录未混入其他轮次')
            cases = [item for item in frozen['plan']['cases'] if item['id'] == observation['case_id']]
            if len(cases) != 1 or observation['expected_question_sha256'] != question_sha256(cases[0]['question']):
                raise ExternalBaselineConflict('关联原题已变化，请按服务端冻结题文重新核对')
            cycle_id, stamp = uuid.uuid4().hex, _now()
            stored = {'kind': KIND, 'baseline_id': uuid.uuid4().hex, 'profile': profile,
                      'profile_sha256': frozen['profile_sha256'], 'frozen_plan_id': frozen['id'],
                      'test_plan': frozen['plan'], 'source': SOURCE, 'native_verified': False,
                      'independent_verification': False, 'baseline_observation_ids': [], 'created_at': stamp}
            empty = {'method': 'user_reported_external_editorial_clues', 'diagnosis': '外部原文仅作编辑线索，尚未独立核验。',
                     'cases': [], 'sources': [], 'briefs': []}
            connection.execute('''INSERT INTO geo_content_cycles
                (id,project_id,report_ref,report_digest,baseline,observations,analysis,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?)''', (cycle_id, payload['project_id'], 'external-baseline:' + frozen['id'],
                 fingerprint({'kind': KIND, 'request_sha256': request_sha}), _json(stored), _json(empty),
                 _json({key: empty[key] for key in ('method', 'diagnosis', 'briefs')}), stamp, stamp))
            probe = attach_external_observation(database, cycle_id, observation['case_id'], raw, metadata,
                expected_scope={'project_id': payload['project_id'], 'expected_plan_sha256': frozen['plan_sha256'], 'expected_revision': 1},
                _connection=connection, observation_role='baseline')
            probe_id = probe['id']; stored['baseline_observation_ids'] = [probe_id]
            records = [probe]
            observed = observations(stored, records)
            analysis = {key: observed[key] for key in ('method', 'diagnosis', 'briefs')}
            connection.execute('UPDATE geo_content_cycles SET baseline=?,observations=?,analysis=? WHERE id=?',
                               (_json(stored), _json(observed), _json(analysis), cycle_id))
            connection.execute('INSERT INTO geo_external_baseline_requests VALUES(?,?,?,?,?,?,?,?)',
                (payload['project_id'], 'create', payload['request_id'], request_sha, frozen['id'], cycle_id, probe_id, stamp))
        row = connection.execute('SELECT record FROM geo_research_probes WHERE id=? AND cycle_id=?', (probe_id, cycle_id)).fetchone()
        probe = json.loads(row['record'])
    return {'cycle': get_cycle(database, cycle_id), 'probe': probe, 'receipt': _receipt(payload, request_sha, reused)}


def observations(stored, records):
    by_case = {item['case_id']: item for item in records}
    cases, briefs = [], []
    for item in stored['test_plan']['cases']:
        record = by_case.get(item['id']); after = record['after'] if record else {}
        different_question = record and record.get('conditions', {}).get('same_question') is False
        usable = bool(record and not different_question and not after.get('answer_error') and after.get('answer_evaluable') is not False)
        finding = ('用户声明的实际问题与冻结原题不同，原文保留，不作为该原题的基线编辑依据。' if different_question else
                   '用户提供的外部回答，仅作编辑线索；采样条件和内容未独立核验。' if record else
                   '本题尚无外部基线原文，状态未知，不能视作零曝光。')
        cases.append({**item, 'usable': usable, 'native_verified': False, 'evidence_basis': SOURCE,
                      'finding': finding, 'response_excerpt': after.get('response', '')[:2500] if usable else '',
                      'collected_at': after.get('collected_at'), 'model_label': after.get('model_label'),
                      'client_mode': after.get('client_mode'), 'source_ids': []})
        if usable:
            briefs.append({'id': f'b{len(briefs)+1:02d}', 'case_ids': [item['id']], 'topic': item['question'],
                           'why': finding, 'outline': [], 'missing_evidence': ['核对客户确认的事实与公开依据'],
                           'source_ids': [], 'priority': 'medium'})
    return {'method': 'user_reported_external_editorial_clues', 'diagnosis': '本轮基线来自用户提供的外部原文；原生采集指标不可用。',
            'cases': cases, 'sources': [], 'briefs': briefs, 'native_metrics_available': False,
            'source_note': '用户提供的链接保留在外部记录中，不作为已核实引用或客户事实。'}


def project_cycle(connection, result, stored):
    from services.geo_research_probe_service import list_probes
    ids = set(stored['baseline_observation_ids'])
    records = [item for item in list_probes(connection, result['id']) if item['id'] in ids]
    result.update(baseline=None, baseline_source=SOURCE, profile=stored['profile'], test_plan=stored['test_plan'],
                  capabilities=dict(CAPABILITIES), external_baseline={**stored, 'observations': records},
                  case_markers=question_markers(stored['test_plan']))
    result['observations'] = observations(stored, records)
    return result


def baseline_id(cycle):
    return cycle['external_baseline']['baseline_id'] if cycle.get('baseline_source') == SOURCE else cycle['baseline']['run_id']
