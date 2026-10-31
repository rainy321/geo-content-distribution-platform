"""A cycle-scoped single-question observation, separate from full GEO reports."""
from __future__ import annotations

import copy
import hashlib
import ipaddress
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
    if cycle.get('baseline_source') == 'user_reported_external':
        raise ValueError('本轮是用户提供的外部基线，不能当作原生采集复测；请继续登记外部观察或另建原生基线')
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
    session = {key: execution[key] for key in ('profile_mode', 'mode_policy') if key in execution}
    if session.get('profile_mode') not in (None, 'consumer', 'work') or session.get('mode_policy') not in (None, 'available_web'):
        raise ValueError('原网页会话配置无法确认，未发送')
    if session.get('mode_policy') == 'available_web' and session.get('profile_mode') is None:
        raise ValueError('原专用网页会话类型缺失，未发送')
    return {'case': copy.deepcopy(selected[0]), 'before': copy.deepcopy(previous[0]),
            'execution': {'client_mode': mode, 'requested_model_label': model, 'timeout_seconds': timeout, **session}}


def _observation_context(cycle, case_id):
    if cycle.get('baseline_source') == 'user_reported_external':
        from services.geo_external_baseline import external_context
        return external_context(cycle, case_id)
    return context(cycle, case_id)


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
        cycle = _cycle(connection, cycle_id, for_update=True)
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


def _check_external_scope(cycle, expected_scope, *, allow_unassigned_project=False):
    if expected_scope is None:
        return
    if (not isinstance(expected_scope, dict)
            or set(expected_scope) != {'project_id', 'expected_plan_sha256', 'expected_revision'}
            or not ((type(expected_scope['project_id']) is int and expected_scope['project_id'] > 0)
                    or (allow_unassigned_project and expected_scope['project_id'] is None and cycle['project_id'] is None))
            or type(expected_scope['expected_revision']) is not int
            or not isinstance(expected_scope['expected_plan_sha256'], str)
            or not re.fullmatch('[0-9a-f]{64}', expected_scope['expected_plan_sha256'])):
        raise ValueError('请提供当前品牌项目、计划摘要与资料版本')
    if (cycle['project_id'] != expected_scope['project_id']
            or cycle['revision'] != expected_scope['expected_revision']
            or cycle['test_plan']['plan_sha256'] != expected_scope['expected_plan_sha256']):
        raise ValueError('品牌项目、原计划或资料版本已变化，请刷新后重新核对')


def _external_source_links(value):
    from services.geo_content_research import public_url
    if not isinstance(value, list) or len(value) > 20:
        raise ValueError('外部来源链接最多20项，只接受用户提供的公开网址')
    result = []
    for item in value:
        if not isinstance(item, dict) or set(item) - {'title', 'url'} or 'url' not in item:
            raise ValueError('外部来源只接受公开网址和可选标题，不接受验证或采集字段')
        url, title = item['url'], item.get('title')
        if (not isinstance(url, str) or not 1 <= len(url) <= 2000 or not public_url(url)
                or re.search(r'[\s\\\x00-\x1f\x7f]', url)
                or ('title' in item and (not isinstance(title, str) or not title.strip() or len(title) > 300))):
            raise ValueError('外部来源应为公开HTTP/HTTPS网址，标题最多300字')
        parsed = urlsplit(url)
        try:
            port = parsed.port
        except ValueError:
            raise ValueError('外部来源网址端口无效') from None
        host = parsed.hostname.casefold().rstrip('.')
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
            try:
                host = host.encode('idna').decode('ascii')
            except UnicodeError:
                raise ValueError('外部来源网址域名无效') from None
        valid_domain = (len(host) <= 253 and '.' in host and not host.rsplit('.', 1)[-1].isdigit()
            and all(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', part) for part in host.split('.'))
            and not host.endswith(('.localhost', '.local', '.internal')))
        if ((address is not None and not address.is_global)
                or (address is None and not valid_domain)
                or (port is not None and not 1 <= port <= 65535)):
            raise ValueError('外部来源不能使用本机或内部网络地址')
        result.append({'url': url, 'title': title, 'basis': 'user_reported',
                       'verified': False, 'native_verified': False})
    return result


def submit_external_observation(database, cycle_id, payload):
    """Finite browser JSON contract; no native imports or business judgments."""
    required = {'project_id', 'case_id', 'expected_plan_sha256', 'expected_revision', 'raw_response_text'}
    if (not isinstance(payload, dict) or not required <= set(payload)
            or set(payload) - required - {'metadata'}):
        raise ValueError('请仅提交关联原题、当前项目版本与外部回答原文')
    metadata = payload.get('metadata', {})
    allowed = {'question', 'model_label', 'client_mode', 'collected_at', 'answer_complete', 'note', 'source_links'}
    if not isinstance(metadata, dict) or set(metadata) - allowed:
        raise ValueError('外部反馈只接受用户提供的条件和公开链接，不能提供业务判定或原生验证')
    text = payload['raw_response_text']
    if isinstance(text, str) and '\n' not in text and '\r' not in text and re.match(
            r'^(?:[a-zA-Z]:[\\/]|\\\\|file:|/|\.{1,2}[\\/]|~[\\/])', text.strip(), re.I):
        raise ValueError('请粘贴回答原文，不接受本地文件路径')
    return attach_external_observation(database, cycle_id, payload['case_id'], text, metadata,
        expected_scope={key: payload[key] for key in ('project_id', 'expected_plan_sha256', 'expected_revision')})


def submit_external_observation_context(database, cycle_id, probe_id, payload):
    required = {'project_id', 'case_id', 'expected_plan_sha256', 'expected_revision',
                'expected_response_sha256', 'expected_context_sequence', 'attestation'}
    if not isinstance(payload, dict) or set(payload) != required:
        raise ValueError('请仅提交当前外部记录身份、原文摘要与明确的用户条件声明')
    sequence = payload['expected_context_sequence']
    if type(sequence) is not int or not 0 <= sequence < 1000:
        raise ValueError('条件声明版本无效，请刷新后重新核对')
    return attest_external_observation_context(database, cycle_id, probe_id, payload['expected_response_sha256'],
        payload['attestation'], expected_case_id=payload['case_id'], expected_context_sequence=sequence,
        expected_scope={key: payload[key] for key in ('project_id', 'expected_plan_sha256', 'expected_revision')})


def attach_external_observation(database, cycle_id, expected_case_id, raw_response_text, metadata, *, expected_scope=None,
                                _connection=None, observation_role=None):
    """Save owner-provided external answer text; never pretend a native collection.

    The finite browser facade requires current project/plan/revision identity.
    Trusted domain callers can still add attributed business material reviews.
    The expected case is an association, not proof of the external question.
    """
    from contextlib import closing
    from services.geo_content_store import _connect, _cycle
    if (not isinstance(raw_response_text, str) or not raw_response_text.strip()
            or len(raw_response_text.encode('utf-8')) > 1024 * 1024 or not isinstance(metadata, dict)):
        raise ValueError('请提供外部回答原文与元数据，不接受文件路径读取')
    allowed = {'question', 'model_label', 'client_mode', 'collected_at', 'answer_complete', 'note',
               'business_relationship_review', 'source_links'}
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
    source_links = _external_source_links(metadata['source_links']) if 'source_links' in metadata else []
    from contextlib import contextmanager
    @contextmanager
    def transaction():
        if _connection is not None:
            yield _connection
        else:
            with closing(_connect(database)) as own, own:
                own.execute('BEGIN IMMEDIATE')
                yield own
    with transaction() as connection:
        cycle = _cycle(connection, cycle_id, for_update=True)
        _check_external_scope(cycle, expected_scope)
        if cycle.get('baseline_source') == 'user_reported_external':
            from services.geo_external_baseline import external_context, check_profile_current
            check_profile_current(connection, cycle)
            details = external_context(cycle, expected_case_id)
        else:
            details = context(cycle, expected_case_id)
        observed_question = metadata.get('question')
        raw_sha = _sha(raw_response_text.encode('utf-8'))
        identity = {'cycle_id': cycle_id, 'case_id': expected_case_id, 'response_sha256': raw_sha,
                    'metadata': {key: value for key, value in metadata.items() if key != 'business_relationship_review'}}
        identifier = _sha(json.dumps(identity, ensure_ascii=False, sort_keys=True).encode('utf-8'))[:32]
        before = details['external_before'] if 'external_before' in details else _side(cycle['profile'], details['before'])
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
            'external_source_links': source_links,
            'limitations': list(LIMITATIONS) + ['这是用户提供的外部回答原文；关联题不证明实际同题，未独立核验采集过程、完整性或条件。']}
        if expected_scope is not None:
            record['external_submission'] = {'channel': 'software_json_text_entry', 'basis': 'user_reported',
                'project_id': cycle['project_id'], 'cycle_revision': cycle['revision'],
                'plan_sha256': cycle['test_plan']['plan_sha256'], 'independent_verification': False}
        if observation_role is not None:
            if observation_role != 'baseline' or cycle.get('baseline_source') != 'user_reported_external':
                raise ValueError('外部观察角色无效')
            record['observation_role'] = observation_role
        if cycle.get('baseline_source') == 'user_reported_external':
            from services.geo_external_baseline import question_has_url
            record['question_with_url'] = question_has_url(details['case']['question'], cycle['profile'])
            record['question_marker_basis'] = 'frozen_plan_text'
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


def attest_external_observation_context(database, cycle_id, probe_id, expected_response_sha256, attestation, *,
                                        expected_scope=None, expected_case_id=None, expected_context_sequence=None):
    """Append an owner's context statement to the same unchanged external answer.

    Domain-only; no collector receipt or independent sampling is manufactured.
    The statement can clarify the actual question and session conditions, but
    never changes answer completeness, mode, model, collection time or metrics.
    """
    from contextlib import closing
    from services.geo_content_store import _connect, _cycle
    fields = {'question', 'new_conversation', 'first_message', 'confirmation_text'}
    if (not isinstance(cycle_id, str) or not cycle_id
            or not isinstance(probe_id, str) or not re.fullmatch('[0-9a-f]{32}', probe_id)
            or not isinstance(expected_response_sha256, str)
            or not re.fullmatch('[0-9a-f]{64}', expected_response_sha256)
            or not isinstance(attestation, dict) or set(attestation) != fields):
        raise ValueError('请提供本轮外部记录、原文 SHA256 与明确的用户条件声明')
    for key, maximum in (('question', 4000), ('confirmation_text', 3000)):
        value = attestation[key]
        if not isinstance(value, str) or not value.strip() or len(value) > maximum:
            raise ValueError('实际题文与用户确认原文不能为空，且不能超过长度限制')
    if any(type(attestation[key]) is not bool for key in ('new_conversation', 'first_message')):
        raise ValueError('是否新会话及首条提问必须由用户明确声明为是或否')
    # Detach the caller's mapping before hashing or persisting its exact text.
    statement = {key: copy.deepcopy(attestation[key]) for key in sorted(fields)}
    statement_sha = _sha(json.dumps(statement, ensure_ascii=False, sort_keys=True).encode('utf-8'))
    with closing(_connect(database)) as connection, connection:
        connection.execute('BEGIN IMMEDIATE')
        cycle = _cycle(connection, cycle_id, for_update=True)
        _check_external_scope(cycle, expected_scope)
        from services.geo_external_baseline import check_profile_current
        check_profile_current(connection, cycle)
        if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='geo_research_probes'").fetchone():
            raise ValueError('外部反馈记录不存在，未新建记录')
        row = connection.execute('SELECT source,record FROM geo_research_probes WHERE id=? AND cycle_id=?',
                                 (probe_id, cycle_id)).fetchone()
        if not row:
            raise ValueError('外部反馈记录不属于本轮实验，未修改')
        record = json.loads(row['record'])
        if expected_scope is not None and record.get('case_id') != expected_case_id:
            raise ValueError('外部反馈不属于当前关联原题，未修改')
        if (row['source'] != 'user_reported_external' or record.get('source') != 'user_reported_external'
                or record.get('id') != probe_id or record.get('cycle_id') != cycle_id
                or record.get('native_verified') is not False or record.get('native_task_id') is not None):
            raise ValueError('仅能补充本轮未原生验证的外部反馈条件')
        after = record.get('after') or {}
        response = after.get('response')
        if (not isinstance(response, str) or _sha(response.encode('utf-8')) != expected_response_sha256
                or record.get('response_sha256') != expected_response_sha256
                or after.get('response_sha256') != expected_response_sha256):
            raise ValueError('外部回答原文与预期 SHA256 不一致，未修改')
        details = _observation_context(cycle, record.get('case_id'))
        anchor_question = details['case']['question']
        if (record.get('question') != anchor_question
                or record.get('question_sha256') != _sha(anchor_question.encode('utf-8'))
                or record.get('plan_sha256') != cycle['test_plan']['plan_sha256']):
            raise ValueError('外部反馈的关联原题已变化，未修改')
        history = record.get('context_attestations', [])
        if not isinstance(history, list) or any(not isinstance(item, dict) for item in history):
            raise ValueError('已有条件声明历史格式异常，未覆盖')
        if expected_context_sequence is not None:
            if (type(expected_context_sequence) is not int
                    or expected_context_sequence not in (len(history), len(history) - 1)
                    or (expected_context_sequence == len(history) - 1
                        and (not history or history[-1].get('statement_sha256') != statement_sha))):
                raise ValueError('条件声明已更新，请刷新后核对，未覆盖后续声明')
        if history and history[-1].get('statement_sha256') == statement_sha:
            return record
        if len(history) >= 1000:
            raise ValueError('条件声明历史已达到上限，未覆盖已有记录')
        prior_context = {key: copy.deepcopy(record.get(key)) for key in
            ('observed_question', 'observed_question_sha256', 'website_in_question', 'website_test_without_url')}
        prior_context['same_question'] = (record.get('conditions') or {}).get('same_question')
        question = statement['question']
        website_in_question = _address_present(question, cycle['profile'])
        without_url = not website_in_question and not bool(re.search(r'https?://\S+', question, re.I))
        sequence = len(history) + 1
        event_identity = {'probe_id': probe_id, 'response_sha256': expected_response_sha256,
                          'sequence': sequence, 'statement_sha256': statement_sha}
        entry = {'id': _sha(json.dumps(event_identity, sort_keys=True).encode('utf-8'))[:32],
            'sequence': sequence, 'recorded_at': _now(), 'basis': 'user_statement',
            'native_verified': False, 'independent_verification': False,
            'response_sha256': expected_response_sha256, 'statement_sha256': statement_sha,
            **statement, 'question_sha256': _sha(question.encode('utf-8')),
            'prior_context': prior_context}
        if expected_scope is not None:
            entry['submission_channel'] = 'software_json_text_entry'
        record['context_attestations'] = [*history, entry]
        record['observed_question'] = question
        record['observed_question_sha256'] = entry['question_sha256']
        record['website_in_question'] = website_in_question
        record['website_test_without_url'] = without_url
        record['conditions'] = {**(record.get('conditions') or {}), 'same_question': question == anchor_question}
        connection.execute('UPDATE geo_research_probes SET record=? WHERE id=? AND cycle_id=?',
            (json.dumps(record, ensure_ascii=False), probe_id, cycle_id))
    return record


def _reviewed_probe(connection, cycle, probe_id, *, expected_case_id=None, expected_response_sha256=None):
    if not isinstance(probe_id, str) or not re.fullmatch('[0-9a-f]{32}', probe_id):
        raise ValueError('请选择本轮单题记录')
    if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='geo_research_probes'").fetchone():
        raise ValueError('单题记录不存在')
    row = connection.execute('SELECT source,record FROM geo_research_probes WHERE id=? AND cycle_id=?',
                             (probe_id, cycle['id'])).fetchone()
    if not row:
        raise ValueError('单题记录不属于本轮实验')
    record = json.loads(row['record'])
    if (record.get('id') != probe_id or record.get('cycle_id') != cycle['id']
            or row['source'] not in (*SOURCES, 'user_reported_external') or record.get('source') != row['source']
            or record.get('native_verified') is not (row['source'] == 'native_collector')
            or (expected_case_id is not None and record.get('case_id') != expected_case_id)):
        raise ValueError('单题记录的来源或关联原题不一致，未修改')
    if cycle.get('baseline_source') == 'user_reported_external' and record.get('source') != 'user_reported_external':
        raise ValueError('外部起始轮不能包含伪造的原生采集身份')
    details = _observation_context(cycle, record.get('case_id'))
    question = details['case']['question']
    if (record.get('question') != question or record.get('question_sha256') != _sha(question.encode('utf-8'))
            or record.get('plan_sha256') != cycle['test_plan']['plan_sha256']):
        raise ValueError('单题记录的关联原题或计划已变化，未修改')
    after = record.get('after') or {}
    response = after.get('response')
    if not isinstance(response, str):
        raise ValueError('原始回答文本不存在，未修改')
    response_sha = _sha(response.encode('utf-8'))
    if (('response_sha256' in record and record['response_sha256'] != response_sha)
            or ('response_sha256' in after and after['response_sha256'] != response_sha)
            or (expected_response_sha256 is not None and expected_response_sha256 != response_sha)):
        raise ValueError('原始回答与预期SHA256不一致，未修改')
    return record, response_sha


def _business_review_history(record):
    current = record.get('business_relationship_review')
    history = record.get('business_relationship_reviews')
    if history is None:
        if current is not None and not isinstance(current, dict):
            raise ValueError('已有业务核对格式异常，未覆盖')
        history = [current] if current is not None else []
    elif (not isinstance(history, list) or len(history) > 1000
            or any(not isinstance(entry, dict) for entry in history)
            or (history[-1] if history else None) != current):
        raise ValueError('业务核对最新项与历史不一致，未覆盖')
    identifiers = set()
    for sequence, entry in enumerate(history, 1):
        if ('sequence' in entry and (type(entry['sequence']) is not int or entry['sequence'] != sequence)
                or ('review_request_sha256' in entry and (not isinstance(entry['review_request_sha256'], str)
                    or not re.fullmatch('[0-9a-f]{64}', entry['review_request_sha256'])))
                or ('id' in entry and (not isinstance(entry['id'], str) or not re.fullmatch('[0-9a-f]{32}', entry['id'])
                    or entry['id'] in identifiers))):
            raise ValueError('业务核对历史顺序或身份异常，未覆盖')
        if 'id' in entry:
            identifiers.add(entry['id'])
    if ('business_review_sequence' in record
            and (type(record['business_review_sequence']) is not int or record['business_review_sequence'] != len(history))):
        raise ValueError('业务核对版本与历史不一致，未覆盖')
    return copy.deepcopy(history)


def business_review_context(database, cycle_id, probe_id):
    """Read exact source identity, current facts and retained attributed reviews."""
    from contextlib import closing
    from services.geo_content_store import _connect, _cycle
    with closing(_connect(database)) as connection:
        cycle = _cycle(connection, cycle_id)
        record, response_sha = _reviewed_probe(connection, cycle, probe_id)
        history = _business_review_history(record)
        return {'project_id': cycle['project_id'], 'cycle_id': cycle_id, 'case_id': record['case_id'],
            'probe_id': probe_id, 'plan_sha256': cycle['test_plan']['plan_sha256'], 'revision': cycle['revision'],
            'response_sha256': response_sha, 'review_sequence': len(history), 'reviews': history,
            'confirmed_facts': [{key: fact.get(key) for key in ('id', 'text', 'source_url')}
                                for fact in cycle.get('facts', []) if fact.get('confirmed') is True],
            'scope': 'business_material_acknowledgment_only_not_fact_or_effect_verification'}


def submit_operator_business_review(database, cycle_id, probe_id, payload):
    required = {'project_id', 'case_id', 'expected_plan_sha256', 'expected_revision',
                'expected_response_sha256', 'expected_review_sequence', 'review'}
    if not isinstance(payload, dict) or set(payload) != required:
        raise ValueError('请仅提交当前单题身份、原文摘要与用户业务材料核对')
    review = payload['review']
    if (not isinstance(review, dict) or not {'status', 'quote', 'reference_fact_ids'} <= set(review)
            or set(review) - {'status', 'quote', 'reference_fact_ids', 'note'}
            or not isinstance(review.get('quote'), str) or not review['quote'].strip()):
        raise ValueError('用户核对只接受状态、原文精确摘录、已确认事实编号和备注，不接受身份或效果字段')
    if (not isinstance(payload['expected_response_sha256'], str)
            or not re.fullmatch('[0-9a-f]{64}', payload['expected_response_sha256'])
            or type(payload['expected_review_sequence']) is not int
            or not 0 <= payload['expected_review_sequence'] <= 1000
            or not isinstance(payload['case_id'], str)):
        raise ValueError('请提供原文SHA256、关联题和当前核对版本')
    return review_probe_business(database, cycle_id, probe_id, {**review, 'by': 'operator'},
        expected_scope={key: payload[key] for key in ('project_id', 'expected_plan_sha256', 'expected_revision')},
        expected_case_id=payload['case_id'], expected_response_sha256=payload['expected_response_sha256'],
        expected_review_sequence=payload['expected_review_sequence'])


def review_probe_business(database, cycle_id, probe_id, review, *, expected_scope=None, expected_case_id=None,
                          expected_response_sha256=None, expected_review_sequence=None):
    """Append an attributed review; preserve raw text and all earlier opinions."""
    from contextlib import closing
    from services.geo_content_store import _connect, _cycle
    with closing(_connect(database)) as connection, connection:
        connection.execute('BEGIN IMMEDIATE')
        cycle = _cycle(connection, cycle_id, for_update=True)
        _check_external_scope(cycle, expected_scope, allow_unassigned_project=True)
        from services.geo_external_baseline import check_profile_current
        check_profile_current(connection, cycle)
        record, response_sha = _reviewed_probe(connection, cycle, probe_id,
            expected_case_id=expected_case_id, expected_response_sha256=expected_response_sha256)
        history = _business_review_history(record)
        entry = _business_review(cycle, record, review)
        identity = {key: value for key, value in entry.items() if key != 'reviewed_at'}
        identity.update(cycle_revision=cycle['revision'], plan_sha256=cycle['test_plan']['plan_sha256'])
        request_sha = _sha(json.dumps(identity, ensure_ascii=False, sort_keys=True).encode('utf-8'))
        identical = bool(history and history[-1].get('review_request_sha256') == request_sha)
        if (expected_review_sequence is not None
                and (type(expected_review_sequence) is not int
                     or expected_review_sequence not in (len(history), len(history) - 1)
                     or (expected_review_sequence == len(history) - 1 and not identical))):
            raise ValueError('业务核对已更新，请刷新后核对，未覆盖后续意见')
        if identical:
            return record
        if len(history) >= 1000:
            raise ValueError('业务核对历史已达到上限，未覆盖已有意见')
        sequence = len(history) + 1
        event = {'probe_id': probe_id, 'response_sha256': response_sha, 'sequence': sequence, 'request_sha256': request_sha}
        entry.update(id=_sha(json.dumps(event, sort_keys=True).encode())[:32], sequence=sequence,
                     review_request_sha256=request_sha, cycle_revision=cycle['revision'],
                     plan_sha256=cycle['test_plan']['plan_sha256'], basis='operator_statement' if entry['by'] == 'operator' else 'attributed_review',
                     submission_channel='software_json_text_entry' if expected_scope is not None else 'trusted_domain',
                     independent_verification=False)
        record['business_relationship_reviews'] = [*history, entry]
        record['business_review_sequence'] = sequence
        record['business_relationship_review'] = entry
        connection.execute('UPDATE geo_research_probes SET record=? WHERE id=? AND cycle_id=?',
            (json.dumps(record, ensure_ascii=False), probe_id, cycle_id))
    return record
