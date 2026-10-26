"""A cycle-scoped single-question observation, separate from full GEO reports."""
from __future__ import annotations

import copy
import hashlib
import json
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit

from services.geo_test_service import assess_answer, answer_evaluation_error


PROBE_SCHEMA = 'geo_research_single_probe_v1'
SOURCES = ('native_collector', 'research_prototype_import')
LIMITATIONS = [
    '本次只测一道原问题，未测问题不进入新回答或统计分母。',
    '官网地址出现仅指回答包含已配置官网域名文字，不证明官网被引用、业务正确或推荐。',
    '单次前后观察受时间、账号、模型与搜索结果影响，不能证明发文或网站修改导致改善。',
    '业务关系与来源可靠性仍需人工核对；未完成或包含账户内部资料的回答不作零值。',
]


def _sha(payload):
    return hashlib.sha256(payload).hexdigest()


def _now():
    return datetime.now(timezone.utc).isoformat()


def context(cycle, case_id):
    if not isinstance(case_id, str):
        raise ValueError('请选择原计划中的一道问题')
    selected = [case for case in cycle['test_plan']['cases'] if case['id'] == case_id]
    previous = [case for case in cycle['baseline']['cases'] if case['id'] == case_id]
    if len(selected) != 1 or len(previous) != 1 or selected[0]['question'] != previous[0]['question']:
        raise ValueError('原问题不存在或与本轮基线不一致，未发送')
    execution = cycle['baseline'].get('execution') or {}
    mode = execution.get('client_mode') or previous[0].get('client_mode') or 'consumer'
    if mode not in ('work', 'consumer'):
        raise ValueError('原采集界面无法确认，未发送')
    model = execution.get('requested_model_label')
    if model is not None and (not isinstance(model, str) or not model.strip()):
        raise ValueError('原模型选项无法确认，未发送')
    timeout = execution.get('timeout_seconds', 180)
    if type(timeout) not in (int, float) or not 0 < timeout <= 600:
        raise ValueError('原回答等待时间无法确认，未发送')
    return {'case': copy.deepcopy(selected[0]), 'before': copy.deepcopy(previous[0]),
            'execution': {'client_mode': mode, 'requested_model_label': model, 'timeout_seconds': timeout}}


def _domains(profile):
    result = []
    for value in [profile.get('website'), *(profile.get('official_urls') or [])]:
        if not isinstance(value, str) or not value:
            continue
        try:
            host = urlsplit(value if '://' in value else 'https://' + value).hostname
        except ValueError:
            continue
        if host:
            host = host.casefold().removeprefix('www.')
            if host not in result:
                result.append(host)
    return result


def _address_present(text, profile):
    hosts = _domains(profile)
    if not hosts:
        return None
    return any(re.search(r'(?<![a-z0-9.\-])(?:www\.)?' + re.escape(host) + r'(?![a-z0-9.\-])',
                         text or '', re.I) is not None for host in hosts)


def _side(profile, row, *, result=False):
    if result:
        assessment = assess_answer(profile, row, row['question'])
        if row.get('failure_reason') in ('service_capacity_unavailable', 'collector_exception_submission_unknown'):
            assessment = {**assessment, 'answer_evaluable': False,
                          'answer_error': row['failure_reason']}
        environment = row.get('environment') or {}
    else:
        assessment = row.get('assessment') or {}
        environment = {'client_mode': row.get('client_mode'), 'model_label': row.get('model_label')}
    valid = assessment.get('answer_evaluable') is True
    response = row.get('response') if isinstance(row.get('response'), str) else ''
    return {'response': response,
        'collected_at': (row.get('original_answer_timestamp') or row.get('timestamp')) if result else row.get('collected_at'),
        'client_mode': environment.get('client_mode'), 'model_label': environment.get('model_label'),
        'answer_evaluable': valid, 'answer_error': assessment.get('answer_error'),
        'website_presence': _address_present(response, profile) if valid else None,
        'official_source_present': assessment.get('official_source_present') if valid else None,
        'official_citation_present': assessment.get('official_citation_present') if valid else None,
        'citations': copy.deepcopy(row.get('citations')), 'displayed_sources': copy.deepcopy(row.get('displayed_sources')),
        'collection_status': row.get('collection_status'), 'failure_reason': row.get('failure_reason')}


def build_probe(cycle, case_id, collector_result, *, run_id, source, created_at=None,
                source_probe_sha256=None, native_task_id=None, evidence=None):
    details = context(cycle, case_id)
    if (source not in SOURCES or not isinstance(run_id, str) or not re.fullmatch('[0-9a-f]{32}', run_id)
            or not isinstance(collector_result, dict)
            or collector_result.get('question') != details['case']['question']):
        raise ValueError('单题采集身份不一致，原问题与记录保持不变')
    before = _side(cycle['profile'], details['before'])
    after = _side(cycle['profile'], collector_result, result=True)
    website_in_question = _address_present(details['case']['question'], cycle['profile']) is True
    same = lambda field: (before[field] == after[field]) if before[field] and after[field] else None
    return {'schema_version': PROBE_SCHEMA, 'kind': 'geo_research_single_probe',
        'id': run_id, 'run_id': run_id, 'cycle_id': cycle['id'], 'case_id': case_id,
        'category': details['case']['category'], 'question': details['case']['question'],
        'question_sha256': _sha(details['case']['question'].encode('utf-8')),
        'plan_sha256': cycle['test_plan']['plan_sha256'],
        'source': source, 'native_verified': source == 'native_collector',
        'source_probe_sha256': source_probe_sha256, 'native_task_id': native_task_id,
        'selected_count': 1, 'not_selected_case_ids': [case['id'] for case in cycle['test_plan']['cases'] if case['id'] != case_id],
        'created_at': created_at or _now(), 'collected_at': after['collected_at'],
        'status': ('needs_review' if collector_result.get('failure_reason') == 'collector_exception_submission_unknown'
                   else 'observed' if after['answer_evaluable'] else 'incomplete'),
        'website_in_question': website_in_question,
        'website_test_without_url': not website_in_question and not bool(re.search(r'https?://\S+', details['case']['question'], re.I)),
        'before': before, 'after': after,
        'execution': {**details['execution'], 'manual_wait_seconds': None, 'collector_invocation_limit': 1},
        'conditions': {'same_question': True, 'client_mode_match': same('client_mode'), 'model_label_match': same('model_label')},
        'evidence': evidence, 'limitations': list(LIMITATIONS),
        'business_relationship_review': None, 'causal_effect': None}


def normalize_import(cycle, raw):
    if not isinstance(raw, bytes) or not 0 < len(raw) <= 5 * 1024 * 1024:
        raise ValueError('研究导入应为原始单题结果的完整UTF8字节，不接受文件路径')
    payload = json.loads(raw.decode('utf-8'))
    selected = payload.get('selection') or {}
    case = payload.get('case') or {}
    if (payload.get('kind') != 'private_same_question_probe'
            or payload.get('source_cycle_id') != cycle['id']
            or payload.get('source_plan_sha256') != cycle['test_plan']['plan_sha256']
            or payload.get('profile') != cycle['profile']
            or selected.get('selected_count') != 1
            or selected.get('selected_case_ids') != [case.get('id')]
            or payload.get('source_files_unchanged') is not True
            or payload.get('source_baseline_unchanged') is not True):
        raise ValueError('研究结果不是本轮独立单题原始记录，未导入')
    details = context(cycle, case.get('id'))
    execution = payload.get('execution') or {}
    if (case.get('question') != details['case']['question']
            or any(execution.get(key) != value for key, value in details['execution'].items())
            or execution.get('manual_wait_seconds') is not None
            or execution.get('normal_profile_lock') is not True
            or execution.get('collector_invocation_limit') != 1):
        raise ValueError('研究导入问题与原题不一致，未导入')
    return build_probe(cycle, case['id'], payload.get('collector_result'), run_id=payload.get('run_id'),
        source='research_prototype_import', created_at=payload.get('created_at'), source_probe_sha256=_sha(raw))


CREATE_PROBES = '''CREATE TABLE IF NOT EXISTS geo_research_probes (
    id TEXT PRIMARY KEY, cycle_id TEXT NOT NULL, case_id TEXT NOT NULL,
    source TEXT NOT NULL, record TEXT NOT NULL, created_at TEXT NOT NULL,
    FOREIGN KEY(cycle_id) REFERENCES geo_content_cycles(id))'''


def list_probes(connection, cycle_id):
    if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='geo_research_probes'").fetchone():
        return []
    return [json.loads(row[0]) for row in connection.execute(
        'SELECT record FROM geo_research_probes WHERE cycle_id=? ORDER BY created_at DESC,id DESC', (cycle_id,))]


def attach_probe(database, cycle_id, value, *, source, native_task_id=None, evidence=None):
    """Domain-only entry: prototype bytes or native collector payload, never a path.

    The browser bridge exposes only cycle/case selection, not this import method.
    """
    from contextlib import closing
    from services.geo_content_store import _connect, _cycle
    with closing(_connect(database)) as connection, connection:
        connection.execute('BEGIN IMMEDIATE')
        cycle = _cycle(connection, cycle_id)
        if source == 'research_prototype_import':
            record = normalize_import(cycle, value)
            raw_baseline = connection.execute('SELECT baseline FROM geo_content_cycles WHERE id=?', (cycle_id,)).fetchone()[0]
            if json.loads(value.decode('utf-8')).get('source_baseline_sha256') != _sha(raw_baseline.encode('utf-8')):
                raise ValueError('研究导入的原基线已变化，未保存')
        elif source == 'native_collector' and isinstance(value, dict):
            record = build_probe(cycle, value['case_id'], value['collector_result'], run_id=value['run_id'],
                source=source, created_at=value.get('created_at'), native_task_id=native_task_id, evidence=evidence)
        else:
            raise ValueError('单题来源无效，不接受任意文件路径')
        connection.execute(CREATE_PROBES)
        prior = connection.execute('SELECT record FROM geo_research_probes WHERE id=?', (record['id'],)).fetchone()
        if prior:
            saved = json.loads(prior[0])
            if (saved['cycle_id'] != cycle_id or saved['source'] != source
                    or saved.get('source_probe_sha256') != record.get('source_probe_sha256')
                    or saved['question_sha256'] != record['question_sha256'] or saved['after'] != record['after']):
                raise ValueError('已有同编号的其他记录，未覆盖')
            return saved
        connection.execute('INSERT INTO geo_research_probes VALUES(?,?,?,?,?,?)',
            (record['id'], cycle_id, record['case_id'], source, json.dumps(record, ensure_ascii=False), record['created_at']))
    return record


def _business_review(cycle, record, review):
    if not isinstance(review, dict):
        raise ValueError('业务核对应为原文摘录与已确认资料的人工或助手核对')
    status, by = review.get('status'), review.get('by')
    quote, references = review.get('quote'), review.get('reference_fact_ids')
    response = record['after']['response']
    approved = {fact['id']: fact for fact in cycle.get('facts', []) if fact.get('confirmed') is True}
    if (status not in ('not_acknowledged', 'acknowledged', 'unknown') or by not in ('assistant', 'operator')
            or not isinstance(quote, str) or not 1 <= len(quote) <= 3000 or quote not in response
            or not isinstance(references, list) or not 1 <= len(references) <= 30
            or any(not isinstance(value, str) or value not in approved for value in references)
            or len(set(references)) != len(references)):
        raise ValueError('业务核对须使用回答原文精确摘录，并引用本轮已确认事实编号')
    if (record['after'].get('answer_evaluable') is False or record['after'].get('answer_error')) and status != 'unknown':
        raise ValueError('未完成或排除的回答只能保留业务核对未知，不能判定已识别或未识别')
    note = review.get('note', '')
    if not isinstance(note, str) or len(note) > 1000 or review.get('delivery_capability', 'unknown') != 'unknown':
        raise ValueError('业务核对不能推断实际交付能力，备注应为1000字以内')
    return {'status': status, 'by': by, 'quote': quote, 'reference_fact_ids': list(references),
        'reference_facts': [{key: approved[identifier].get(key) for key in ('id', 'text', 'source_url')}
                            for identifier in references],
        'response_sha256': _sha(response.encode('utf-8')), 'reviewed_at': _now(),
        'delivery_capability': 'unknown', 'note': note,
        'scope': 'manual_or_assistant_business_material_acknowledgment_not_automated_fact_accuracy'}


def attach_external_observation(database, cycle_id, expected_case_id, raw_response_text, metadata):
    """Save owner-provided external answer text; never pretend a native collection.

    Domain-only: the public browser bridge accepts no imported text or file path.
    The expected case is an association, not proof of the external question.
    """
    from contextlib import closing
    from services.geo_content_store import _connect, _cycle
    if (not isinstance(raw_response_text, str) or not raw_response_text.strip()
            or len(raw_response_text.encode('utf-8')) > 1024 * 1024 or not isinstance(metadata, dict)):
        raise ValueError('请提供外部回答原文与元数据，不接受文件路径读取')
    allowed = {'question', 'model_label', 'client_mode', 'collected_at', 'answer_complete', 'note',
               'business_relationship_review'}
    if set(metadata) - allowed:
        raise ValueError('外部反馈不能提供或伪造原生采集验证字段')
    for key, maximum in (('question', 4000), ('model_label', 100), ('collected_at', 100), ('note', 1000)):
        value = metadata.get(key)
        if value is not None and (not isinstance(value, str) or not value.strip() or len(value) > maximum):
            raise ValueError('外部反馈的题文、模型、时间或备注格式无效')
    mode = metadata.get('client_mode')
    complete = metadata.get('answer_complete')
    if mode not in (None, 'work', 'consumer') or (complete is not None and type(complete) is not bool):
        raise ValueError('外部界面或完整性只能使用明确提供的信息，缺失请保持未知')
    with closing(_connect(database)) as connection, connection:
        connection.execute('BEGIN IMMEDIATE')
        cycle = _cycle(connection, cycle_id)
        details = context(cycle, expected_case_id)
        observed_question = metadata.get('question')
        raw_sha = _sha(raw_response_text.encode('utf-8'))
        identity = {'cycle_id': cycle_id, 'case_id': expected_case_id, 'response_sha256': raw_sha,
                    'metadata': {key: value for key, value in metadata.items() if key != 'business_relationship_review'}}
        identifier = _sha(json.dumps(identity, ensure_ascii=False, sort_keys=True).encode('utf-8'))[:32]
        before = _side(cycle['profile'], details['before'])
        # Existing explicit private/deferred/capacity exclusions also protect
        # pasted text. No submission or completion receipt is manufactured.
        error = answer_evaluation_error(raw_response_text, client_mode=mode or 'work')
        answer_evaluable = False if error or complete is False else True if complete is True else None
        after = {'response': raw_response_text, 'response_sha256': raw_sha,
            'collected_at': metadata.get('collected_at'), 'client_mode': mode, 'model_label': metadata.get('model_label'),
            'answer_evaluable': answer_evaluable, 'answer_error': error,
            'website_presence': _address_present(raw_response_text, cycle['profile']) if not error and complete is not False else None,
            'official_source_present': None, 'official_citation_present': None, 'citations': None, 'displayed_sources': None,
            'collection_status': None, 'failure_reason': None,
            'completeness_basis': 'user_statement' if complete is not None else 'not_provided'}
        website_in_question = _address_present(observed_question, cycle['profile']) if observed_question else None
        no_url = (not website_in_question and not bool(re.search(r'https?://\S+', observed_question, re.I))) if observed_question else None
        record = {'schema_version': PROBE_SCHEMA, 'kind': 'geo_research_single_probe',
            'id': identifier, 'run_id': identifier, 'cycle_id': cycle_id, 'case_id': expected_case_id,
            'category': details['case']['category'], 'question': details['case']['question'],
            'question_sha256': _sha(details['case']['question'].encode('utf-8')),
            'question_role': 'cycle_anchor_only', 'observed_question': observed_question,
            'observed_question_sha256': _sha(observed_question.encode('utf-8')) if observed_question else None,
            'plan_sha256': cycle['test_plan']['plan_sha256'], 'source': 'user_reported_external',
            'native_verified': False, 'native_task_id': None, 'source_probe_sha256': None,
            'response_sha256': raw_sha, 'selected_count': 1,
            'not_selected_case_ids': [case['id'] for case in cycle['test_plan']['cases'] if case['id'] != expected_case_id],
            'created_at': _now(), 'collected_at': metadata.get('collected_at'),
            'status': 'incomplete' if error or complete is False else 'observed',
            'website_in_question': website_in_question, 'website_test_without_url': no_url,
            'before': before, 'after': after, 'execution': None, 'evidence': None,
            'conditions': {'same_question': observed_question == details['case']['question'] if observed_question else None,
                'client_mode_match': mode == before['client_mode'] if mode and before['client_mode'] else None,
                'model_label_match': metadata.get('model_label') == before['model_label'] if metadata.get('model_label') and before['model_label'] else None},
            'business_relationship_review': None, 'causal_effect': None, 'external_note': metadata.get('note'),
            'limitations': list(LIMITATIONS) + ['这是用户提供的外部回答原文；关联题不证明实际同题，未独立核验采集过程、完整性或条件。']}
        if metadata.get('business_relationship_review') is not None:
            record['business_relationship_review'] = _business_review(cycle, record, metadata['business_relationship_review'])
        connection.execute(CREATE_PROBES)
        prior = connection.execute('SELECT record FROM geo_research_probes WHERE id=?', (identifier,)).fetchone()
        if prior:
            saved = json.loads(prior[0])
            if saved.get('source') != 'user_reported_external' or saved['after']['response'] != raw_response_text:
                raise ValueError('已有同编号的其他记录，未覆盖')
            # Re-importing the same original must not silently replace a review.
            return saved
        connection.execute('INSERT INTO geo_research_probes VALUES(?,?,?,?,?,?)',
            (identifier, cycle_id, expected_case_id, 'user_reported_external', json.dumps(record, ensure_ascii=False), record['created_at']))
    return record


def review_probe_business(database, cycle_id, probe_id, review):
    """Attach an explicitly attributed local review to this unchanged answer."""
    from contextlib import closing
    from services.geo_content_store import _connect, _cycle
    with closing(_connect(database)) as connection, connection:
        connection.execute('BEGIN IMMEDIATE')
        cycle = _cycle(connection, cycle_id)
        if not isinstance(probe_id, str):
            raise ValueError('请选择本轮单题记录')
        if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='geo_research_probes'").fetchone():
            raise ValueError('单题记录不存在')
        row = connection.execute('SELECT record FROM geo_research_probes WHERE id=? AND cycle_id=?', (probe_id, cycle_id)).fetchone()
        if not row:
            raise ValueError('单题记录不属于本轮实验')
        record = json.loads(row[0])
        record['business_relationship_review'] = _business_review(cycle, record, review)
        connection.execute('UPDATE geo_research_probes SET record=? WHERE id=? AND cycle_id=?',
            (json.dumps(record, ensure_ascii=False), probe_id, cycle_id))
    return record
