"""Append-only project progress from server evidence; never a GEO success metric."""
from __future__ import annotations

import copy
import hashlib
import json
import re
import uuid
from contextlib import closing
from datetime import datetime, timezone

from services.geo_content_research import fingerprint
from services.geo_content_store import _connect, _cycle, _now, ResearchNotFoundError
from services.geo_entity_identity import same_entity_target, entity_measurement_signature

STATUSES = {'observed_improvement': '观察到原文变化，尚未独立核验',
            'not_observed': '尚未形成改善观察', 'preparation': '基础准备，不计为GEO效果'}
SOURCES = {'native_collector', 'research_prototype_import', 'user_reported_external'}
FIELDS = {'request_id', 'direction', 'action_summary', 'action_at', 'status', 'before_probe_id',
          'after_probe_id', 'before_quote', 'after_quote', 'review_note', 'expected_project_identity_sha256'}
REQUIRED = {'request_id', 'direction', 'action_summary', 'status'}
LIMITATIONS = ['这是运营方对服务器原文摘录的人工记录，不证明事实正确、独立GEO成功或因果关系。',
               '基础准备与原文观察分别计数；用户提供的记录不进入原生采集指标。',
               '采集时间与条件缺失仍为未知，录入时间不代替实际采集时间。']


class OptimizationProgressConflict(ValueError):
    pass


def _sha(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _digest(value):
    return isinstance(value, str) and re.fullmatch(r'[0-9a-f]{64}', value) is not None


def _identifier(value):
    return isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,100}', value) is not None


def _time(value, *, required=False):
    if value is None:
        return None
    try:
        if not isinstance(value, str) or not 0 < len(value) <= 100:
            raise ValueError()
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError()
        return parsed.astimezone(timezone.utc).isoformat()
    except (ValueError, OverflowError):
        if required:
            raise ValueError('行动时间须为包含时区的ISO时间；不知道时请留空') from None
        return None


def _text(value, name, maximum, *, optional=False):
    if optional and value is None:
        return ''
    if not isinstance(value, str) or len(value) > maximum or (not optional and not value.strip()):
        raise ValueError(f'{name}须为{maximum}字以内的文本' + ('，且不能为空' if not optional else ''))
    return value


def _project(connection, project_id):
    if type(project_id) is not int or project_id <= 0:
        raise ValueError('项目编号无效')
    row = connection.execute('SELECT * FROM projects WHERE id=?', (project_id,)).fetchone()
    if row is None:
        raise ResearchNotFoundError('项目不存在')
    profile = {key: row[key] for key in ('name', 'entity_type', 'website')}
    for key in ('aliases', 'official_urls'):
        value = row[key]
        profile[key] = json.loads(value) if isinstance(value, str) else value
        if not isinstance(profile[key], list) or not all(isinstance(item, str) for item in profile[key]):
            raise ValueError('项目别名或官网范围格式无效，请先核对项目资料')
    identity = _identity(profile)
    return {'identity': identity, 'sha256': fingerprint(identity), 'updated_at': row['updated_at']}


def _signature(profile):
    # Canonical URL tuples must survive the persisted JSON roundtrip as arrays.
    return json.loads(json.dumps(entity_measurement_signature(profile), ensure_ascii=False))


def _identity(profile):
    return {'name': profile['name'], 'entity_type': profile.get('entity_type', 'product'),
            'website': profile.get('website') or '', 'aliases': copy.deepcopy(profile.get('aliases') or []),
            'official_urls': copy.deepcopy(profile.get('official_urls') or []),
            'measurement_signature': _signature(profile)}


def _same_identity(left, right):
    return (same_entity_target(left, right)
            and left.get('measurement_signature') == right.get('measurement_signature'))


def _legacy_identity(connection, project_id, record, current, cycles):
    snapshots = [record.get(side) for side in ('before', 'after') if record.get(side) is not None]
    if snapshots:
        identities = []
        for snapshot in snapshots:
            identifier = snapshot.get('cycle_id') if isinstance(snapshot, dict) else None
            try:
                cycle = cycles.get(identifier)
                if cycle is None:
                    cycle = cycles[identifier] = _cycle(connection, identifier)
                if cycle['project_id'] != project_id or cycle['test_plan']['plan_sha256'] != snapshot.get('plan_sha256'):
                    return None, 'unknown'
                identities.append(_identity(cycle['profile']))
            except (ValueError, ResearchNotFoundError, KeyError, TypeError, AttributeError):
                return None, 'unknown'
        if all(_same_identity(identities[0], item) for item in identities[1:]):
            return identities[0], 'derived_frozen_profile'
        return None, 'unknown'
    # SQLite CRUD timestamps are UTC whole seconds. The strict inequality also
    # excludes an update later within the history record's own second.
    updated = current['updated_at']
    recorded = _time(record.get('recorded_at'))
    if (record.get('status') == 'preparation' and isinstance(updated, str)
            and re.fullmatch(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}', updated) and recorded):
        try:
            watermark = datetime.strptime(updated, '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)
            floor = datetime.fromisoformat(recorded).replace(microsecond=0)
            if watermark < floor:
                return current['identity'], 'derived_unchanged_project'
        except ValueError:
            pass
    return None, 'unknown'


def _record_identity(connection, project_id, record, current, cycles):
    identity, basis = record.get('project_identity'), record.get('project_identity_basis')
    if identity is not None:
        try:
            if (not isinstance(identity, dict) or basis != 'stored_at_record'
                    or record.get('project_identity_sha256') != fingerprint(identity)
                    or identity.get('measurement_signature') != _signature(identity)):
                return None, 'unknown', None
        except (TypeError, ValueError, KeyError):
            return None, 'unknown', None
    else:
        identity, basis = _legacy_identity(connection, project_id, record, current, cycles)
    matches = _same_identity(identity, current['identity']) if identity is not None else None
    return identity, basis, matches


def _project_record(connection, project_id, record, current, cycles):
    identity, basis, matches = _record_identity(connection, project_id, record, current, cycles)
    result = copy.deepcopy(record)
    result.update(project_identity=copy.deepcopy(identity), project_identity_sha256=fingerprint(identity) if identity else None,
                  project_identity_basis=basis, current_entity_match=matches)
    notes = {'derived_frozen_profile': '身份只读派生自原轮冻结资料，不是当时已保存的身份快照。',
             'derived_unchanged_project': '身份只读派生自严格早于该记录且之后未更新的项目资料，不是原始身份快照。',
             'unknown': '历史对象身份未记录或无法核对，不计入当前对象统计。'}
    if basis in notes:
        result['limitations'] = [*result.get('limitations', []), notes[basis]]
    return result


def _payload(payload):
    if not isinstance(payload, dict) or not REQUIRED <= payload.keys() or set(payload) - FIELDS:
        raise ValueError('请只提交进展记录要求的字段，不接受快照、验证结论或指标')
    request_id = payload['request_id']
    if not isinstance(request_id, str) or not re.fullmatch(r'(?:[a-fA-F0-9]{32}|[a-fA-F0-9]{8}(?:-[a-fA-F0-9]{4}){3}-[a-fA-F0-9]{12})', request_id):
        raise ValueError('请求编号须为UUID或32位十六进制编号')
    if not isinstance(payload['status'], str) or payload['status'] not in STATUSES:
        raise ValueError('进展阶段无效')
    if 'expected_project_identity_sha256' in payload and not _digest(payload['expected_project_identity_sha256']):
        raise ValueError('当前项目身份摘要无效，请刷新项目后再保存')
    value = {'request_id': uuid.UUID(request_id).hex, 'status': payload['status'],
             'direction': _text(payload['direction'], '优化方向', 200),
             'action_summary': _text(payload['action_summary'], '操作摘要', 3000),
             'review_note': _text(payload.get('review_note'), '核对说明', 3000, optional=True),
             'action_at': _time(payload.get('action_at'), required=True)}
    for side in ('before', 'after'):
        probe_id = payload.get(side + '_probe_id')
        quote = _text(payload.get(side + '_quote'), '原文摘录', 2000, optional=True)
        if probe_id is not None and not _identifier(probe_id):
            raise ValueError('请选择本项目的原始观察记录')
        if (probe_id is None and quote) or (probe_id is not None and not quote.strip()):
            raise ValueError('关联记录与非空精确摘录须同时提供')
        value[side + '_probe_id'], value[side + '_quote'] = probe_id, quote
    if value['before_probe_id'] is not None and value['before_probe_id'] == value['after_probe_id']:
        raise ValueError('前后须选择不同的原始观察记录')
    if value['status'] != 'preparation' and not all(value[side + '_probe_id'] for side in ('before', 'after')):
        raise ValueError('前后观察须各选择一条记录并提供精确摘录')
    return value


def _evidence(connection, project_id, probe_id, quote, cycles, current_identity):
    row = connection.execute('''SELECT p.*,c.project_id FROM geo_research_probes p
        JOIN geo_content_cycles c ON c.id=p.cycle_id WHERE p.id=? AND c.project_id=?''', (probe_id, project_id)).fetchone()
    if row is None:
        raise ResearchNotFoundError('本项目的原始观察记录不存在')
    try:
        record = json.loads(row['record'])
        cycle = cycles.setdefault(row['cycle_id'], None)
        if cycle is None:
            cycle = cycles[row['cycle_id']] = _cycle(connection, row['cycle_id'])
        if not _same_identity(_identity(cycle['profile']), current_identity):
            raise OptimizationProgressConflict('原始观察属于历史对象或官网范围，不能登记为当前对象进展')
        cases = [item for item in cycle['test_plan']['cases'] if item['id'] == row['case_id']]
        after = record['after']
        raw = after['response']
        if (not isinstance(record, dict) or not isinstance(after, dict) or not isinstance(raw, str) or not raw.strip()
                or len(cases) != 1 or record.get('id') != row['id'] or record.get('cycle_id') != row['cycle_id']
                or record.get('case_id') != row['case_id'] or record.get('source') != row['source'] or row['source'] not in SOURCES
                or record.get('created_at') != row['created_at']
                or record.get('native_verified') is not (row['source'] == 'native_collector')
                or record.get('question') != cases[0]['question'] or record.get('question_sha256') != _sha(cases[0]['question'])
                or record.get('plan_sha256') != cycle['test_plan']['plan_sha256']):
            raise ValueError()
        response_sha = _sha(raw)
        stored = [record.get('response_sha256'), after.get('response_sha256')]
        if row['source'] == 'user_reported_external' and not all(_digest(item) for item in stored):
            raise ValueError()
        if any(item is not None and (not _digest(item) or item != response_sha) for item in stored):
            raise ValueError()
        observed = record.get('observed_question')
        observed_sha = record.get('observed_question_sha256')
        if observed is not None:
            if not isinstance(observed, str) or not observed.strip() or observed_sha != _sha(observed):
                raise ValueError()
        elif observed_sha is not None:
            raise ValueError()
        if quote is not None and quote not in raw:
            raise OptimizationProgressConflict('摘录与服务器原文不一致，请重新核对；未保存进展')
        original_conditions = record.get('conditions', {})
        original_statements = record.get('context_attestations', [])
        if (not isinstance(original_conditions, dict) or not isinstance(original_statements, list)
                or after.get('answer_evaluable') is not None and type(after['answer_evaluable']) is not bool):
            raise ValueError()
        conditions = {key: original_conditions.get(key) if type(original_conditions.get(key)) is bool else None
                      for key in ('same_question', 'client_mode_match', 'model_label_match')}
        statements = []
        statement_keys = {'id', 'sequence', 'recorded_at', 'basis', 'native_verified', 'independent_verification',
                          'response_sha256', 'statement_sha256', 'question', 'new_conversation', 'first_message',
                          'confirmation_text', 'question_sha256', 'submission_channel'}
        for statement in original_statements:
            if (not isinstance(statement, dict) or statement.get('basis') != 'user_statement'
                    or statement.get('native_verified') is not False or statement.get('independent_verification') is not False
                    or statement.get('response_sha256') != response_sha
                    or not isinstance(statement.get('question'), str)
                    or statement.get('question_sha256') != _sha(statement['question'])
                    or type(statement.get('new_conversation')) is not bool or type(statement.get('first_message')) is not bool):
                raise ValueError()
            projected = {key: copy.deepcopy(statement[key]) for key in statement_keys if key in statement}
            prior = statement.get('prior_context')
            if isinstance(prior, dict):
                projected['prior_context'] = {key: copy.deepcopy(prior[key]) for key in
                    ('observed_question', 'observed_question_sha256', 'website_in_question', 'website_test_without_url', 'same_question') if key in prior}
            statements.append(projected)
        if statements and (observed != statements[-1]['question'] or observed_sha != statements[-1]['question_sha256']):
            raise ValueError()
        excluded = (after.get('answer_evaluable') is False or bool(after.get('answer_error'))
                    or record.get('excluded') is True or after.get('excluded') is True
                    or record.get('status') in {'excluded', 'incomplete', 'failed', 'needs_review'})
        result = {'probe_id': row['id'], 'cycle_id': row['cycle_id'], 'case_id': row['case_id'],
            'plan_sha256': record['plan_sha256'], 'question': record['question'], 'question_sha256': record['question_sha256'],
            'observed_question': observed, 'effective_question_sha256': observed_sha if observed is not None else record['question_sha256'],
            'question_basis': 'observed_question' if observed is not None else 'frozen_question',
            'source': row['source'], 'native_verified': record['native_verified'], 'independent_verification': False,
            'recorded_at': _time(row['created_at']), 'collected_at': _time(after.get('collected_at')),
            'collection_time_known': _time(after.get('collected_at')) is not None,
            'collection_time_note': '来自原记录，未独立核验' if _time(after.get('collected_at')) else '未知：原记录未提供有效含时区采集时间',
            'client_mode': after.get('client_mode'), 'model_label': after.get('model_label'),
            'answer_evaluable': after.get('answer_evaluable'), 'answer_error': after.get('answer_error'),
            'excluded_from_improvement': excluded,
            'eligibility_note': '该原文被排除或明确不完整，不能登记为观察到改善' if excluded else '仅供人工摘录核对，不证明独立效果',
            'response_sha256': response_sha,
            'response_sha256_basis': 'stored_and_verified' if all(stored) else 'derived_from_server_record',
            'conditions': copy.deepcopy(conditions), 'context_attestations': copy.deepcopy(statements)}
        if quote is None:
            result.update(response_excerpt=raw[:1000], response_truncated=len(raw) > 1000, response_chars=len(raw))
        else:
            result['quote'] = quote
        if len(json.dumps(result, ensure_ascii=False).encode('utf-8')) > 128 * 1024:
            raise ValueError()
        return result
    except OptimizationProgressConflict:
        raise
    except (ValueError, KeyError, TypeError, IndexError, AttributeError):
        raise OptimizationProgressConflict('原始观察的身份、摘要或条件记录未通过校验，请先核对原记录') from None


def _comparison(before, after, status):
    if (before['case_id'] != after['case_id'] or before['plan_sha256'] != after['plan_sha256']
            or before['effective_question_sha256'] != after['effective_question_sha256']):
        raise OptimizationProgressConflict('前后记录须属于同一问题计划和同一道实际原题')
    if status == 'observed_improvement':
        if before['excluded_from_improvement'] or after['excluded_from_improvement']:
            raise OptimizationProgressConflict('被排除或明确不完整的回答不能登记为观察到改善；可保留为待核对记录')
        for field in ('recorded_at', 'collected_at'):
            if before[field] is not None and after[field] is not None and after[field] < before[field]:
                raise OptimizationProgressConflict('后一次观察的时间早于前一次，不能登记为改善')


def _kind(before, after):
    sources = {item['source'] for item in (before, after) if item}
    return ('user_reported_included' if 'user_reported_external' in sources else
            'research_import_included' if 'research_prototype_import' in sources else 'native_only' if sources else 'no_evidence')


def append_progress(database, project_id, payload):
    value = _payload(payload)
    request_sha = fingerprint({'operation': 'append_optimization_progress', 'project_id': project_id, 'payload': payload})
    with closing(_connect(database)) as connection, connection:
        connection.execute('BEGIN IMMEDIATE')
        current = _project(connection, project_id)
        prior = connection.execute('SELECT record,request_sha256 FROM geo_optimization_progress WHERE project_id=? AND request_id=?',
                                   (project_id, value['request_id'])).fetchone()
        if prior is not None:
            if prior['request_sha256'] != request_sha:
                raise OptimizationProgressConflict('同一请求编号已对应其他进展内容，请保留原记录并核对')
            return {'record': _project_record(connection, project_id, json.loads(prior['record']), current, {}),
                    'receipt': {'request_id': payload['request_id'], 'request_sha256': request_sha, 'idempotent': True}}
        if 'expected_project_identity_sha256' not in payload:
            raise ValueError('请先读取当前项目身份，再保存新的进展记录')
        if payload['expected_project_identity_sha256'] != current['sha256']:
            raise OptimizationProgressConflict('项目名称、官网或测量范围已变化，原填写保留，请核对当前对象')
        cycles = {}
        before, after = [(_evidence(connection, project_id, value[side + '_probe_id'], value[side + '_quote'], cycles, current['identity'])
                          if value[side + '_probe_id'] is not None else None) for side in ('before', 'after')]
        if value['status'] != 'preparation':
            _comparison(before, after, value['status'])
        stamp = _now()
        record = {'id': uuid.uuid4().hex, 'project_id': project_id, 'request_id': payload['request_id'], 'request_sha256': request_sha,
            **{key: value[key] for key in ('direction', 'action_summary', 'status', 'action_at', 'review_note')},
            'status_label': STATUSES[value['status']], 'recorded_at': stamp,
            'timeline_at': value['action_at'] or stamp, 'timeline_basis': 'action_at' if value['action_at'] else 'recorded_at',
            'before': before, 'after': after, 'evidence_kind': _kind(before, after),
            'assessment_basis': 'operator_review_of_server_quotes', 'independent_verification': False,
            'native_metrics_available': False, 'causal_effect': None, 'limitations': list(LIMITATIONS),
            'project_identity': copy.deepcopy(current['identity']), 'project_identity_sha256': current['sha256'],
            'project_identity_basis': 'stored_at_record'}
        if any(item and (item['question_basis'] == 'frozen_question' and item['source'] == 'user_reported_external'
                         or item['answer_evaluable'] is None) for item in (before, after)):
            record['limitations'].append('部分实际题文或完整性未明确声明，仅可人工观察原文变化。')
        connection.execute('''INSERT INTO geo_optimization_progress
            (id,project_id,request_id,request_sha256,status,evidence_kind,action_at,recorded_at,record)
            VALUES(?,?,?,?,?,?,?,?,?)''', (record['id'], project_id, value['request_id'], request_sha, value['status'],
             record['evidence_kind'], value['action_at'], stamp, json.dumps(record, ensure_ascii=False)))
        return {'record': _project_record(connection, project_id, record, current, cycles),
                'receipt': {'request_id': payload['request_id'], 'request_sha256': request_sha, 'idempotent': False}}


def list_progress(database, project_id, *, offset=0, probe_offset=0, snapshot_sequence=None, include_probes=True,
                  expected_project_identity_sha256=None):
    for value in (offset, probe_offset):
        if type(value) is not int or not 0 <= value <= 1000000:
            raise ValueError('进展页码无效')
    if snapshot_sequence is not None and (type(snapshot_sequence) is not int or not 0 <= snapshot_sequence <= 2**63-1):
        raise ValueError('进展快照编号无效')
    if type(include_probes) is not bool:
        raise ValueError('证据列表选项无效')
    with closing(_connect(database)) as connection:
        connection.execute('BEGIN')
        current = _project(connection, project_id)
        if expected_project_identity_sha256 is not None:
            if not _digest(expected_project_identity_sha256):
                raise ValueError('当前项目身份摘要无效')
            if expected_project_identity_sha256 != current['sha256']:
                raise OptimizationProgressConflict('导出期间项目身份已变化，未混入另一对象，请重新读取')
        maximum = connection.execute('SELECT COALESCE(MAX(sequence),0) FROM geo_optimization_progress WHERE project_id=?', (project_id,)).fetchone()[0]
        snapshot = maximum if snapshot_sequence is None else snapshot_sequence
        if snapshot > maximum:
            raise ValueError('进展快照编号超出本项目范围')
        scope = (project_id, snapshot)
        empty_counts = lambda: {key: 0 for key in ('total', *STATUSES, 'native_only', 'user_reported_included', 'research_import_included', 'no_evidence', 'independently_verified_effects')}
        counts, counts_current, counts_history, counts_unknown = [empty_counts() for _ in range(4)]
        rows, cycles = [], {}
        for index, row in enumerate(connection.execute('''SELECT record,status,evidence_kind FROM geo_optimization_progress
                WHERE project_id=? AND sequence<=? ORDER BY COALESCE(action_at,recorded_at) DESC,recorded_at DESC,id DESC''', scope)):
            stored = json.loads(row['record'])
            _, _, matches = _record_identity(connection, project_id, stored, current, cycles)
            selected_counts = counts_current if matches is True else counts_history if matches is False else counts_unknown
            for target in (counts, selected_counts):
                target['total'] += 1; target[row['status']] += 1; target[row['evidence_kind']] += 1
            if offset <= index < offset + 20:
                rows.append(_project_record(connection, project_id, stored, current, cycles))
        options, invalid = [], 0
        if include_probes:
            probes = connection.execute('''SELECT p.id FROM geo_research_probes p JOIN geo_content_cycles c ON c.id=p.cycle_id
                WHERE c.project_id=? ORDER BY p.created_at DESC,p.id DESC LIMIT 101 OFFSET ?''', (project_id, probe_offset)).fetchall()
            for probe in probes[:100]:
                try:
                    options.append(_evidence(connection, project_id, probe['id'], None, cycles, current['identity']))
                except (OptimizationProgressConflict, ResearchNotFoundError):
                    invalid += 1
        else:
            probes = []
        has_more = counts['total'] > offset + 20
        return {'project_id': project_id, 'records': rows,
            'project_identity': copy.deepcopy(current['identity']), 'project_identity_sha256': current['sha256'],
            'counts': counts, 'counts_current': counts_current, 'counts_history': counts_history, 'counts_unknown': counts_unknown,
            'has_more': has_more, 'next_offset': offset + 20 if has_more else None,
            'snapshot_sequence': snapshot, 'probe_options': options, 'probe_options_has_more': len(probes) > 100,
            'next_probe_offset': probe_offset + 100 if len(probes) > 100 else None,
            'unavailable_probe_count': invalid, 'count_semantics': '阶段与证据来源计数，不是成功率；基础准备不计为GEO效果。'}
