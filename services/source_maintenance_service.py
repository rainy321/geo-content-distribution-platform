"""Company-source maintenance records; separate from articles and GEO metrics."""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from contextlib import closing
from datetime import datetime, timezone
from urllib.parse import urlsplit

ENTRY_URL = 'https://help.qcc.com/spt/support/handle-dissent'
MODULES = {'website': '企业官网', 'introduction': '企业简介'}
STAGES = {'draft', 'opening', 'awaiting_login', 'awaiting_human_verification', 'waiting_verification', 'needs_manual_input',
          'confirmation_ready', 'submitted_pending_review', 'submission_unknown', 'needs_manual_review',
          'fee_required_stopped', 'window_closed', 'browser_rejected'}
CREATE_TABLE = '''CREATE TABLE IF NOT EXISTS geo_source_maintenance (
    id TEXT PRIMARY KEY, cycle_id TEXT NOT NULL, company_key TEXT NOT NULL, module TEXT NOT NULL,
    record TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    FOREIGN KEY(cycle_id) REFERENCES geo_content_cycles(id) ON DELETE CASCADE)'''


def _now():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _exists(connection):
    return connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='geo_source_maintenance'").fetchone()


def _company(cycle):
    profile = cycle['profile']
    if profile.get('entity_type') != 'company':
        raise ValueError('来源资料维护仅适用于公司，请切换公司实验；不能把产品当作公司主体')
    website = profile.get('website') or ''
    parsed = urlsplit(website)
    if parsed.scheme not in ('https', 'http') or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('请先核对公司的官方网站')
    facts = [fact for fact in cycle.get('facts', []) if fact.get('confirmed') is True]
    website_facts = [fact for fact in facts if urlsplit(fact.get('source_url') or '').hostname == parsed.hostname]
    if not website_facts:
        raise ValueError('请先在事实核对中确认公司官网资料，再准备来源补正')
    return {'name': profile['name'], 'website': website, 'facts': facts, 'website_facts': website_facts,
            'company_key': digest({'company_name': profile['name'].strip().casefold()})}


def _decorate(record):
    result = dict(record)
    result['read_only'] = bool(record.get('inspection_only') or record.get('write_attempted') or record.get('confirmation_seen') or record['stage'] in
                              ('submitted_pending_review', 'submission_unknown', 'needs_manual_review'))
    result['can_edit'] = record['stage'] == 'draft' and not result['read_only']
    result['can_open'] = record['stage'] != 'opening'
    return result


def overview(connection, cycle):
    value = {'supported': False, 'reason': '', 'platform': 'qcc', 'entry_url': ENTRY_URL,
             'company_name': cycle['profile']['name'], 'context_sha256': None, 'modules': [], 'tasks': []}
    if cycle['profile'].get('entity_type') == 'company' and _exists(connection):
        key = digest({'company_name': cycle['profile']['name'].strip().casefold()})
        value['tasks'] = [_decorate(json.loads(row[0])) for row in connection.execute(
            'SELECT record FROM geo_source_maintenance WHERE company_key=? ORDER BY created_at DESC,id DESC', (key,))]
    try:
        company = _company(cycle)
    except ValueError as error:
        value['reason'] = str(error)
        for record in value['tasks']:
            record.update(can_edit=False, can_open=False)
        return value
    value.update(supported=True, context_sha256=digest(company))
    intro = '申请核对并补充' + company['name'] + '的企业简介。'
    references = []
    for fact in company['facts']:
        if len(intro + '\n' + fact['text']) <= 500:
            intro += '\n' + fact['text']
            references.append(fact['id'])
    value['modules'] = [
        {'key': 'website', 'label': MODULES['website'], 'max_length': 500,
         'proposal': f"申请核对并补充{company['name']}的企业官网：{company['website']}。\n本申请仅维护站内官网资料，不变更工商登记信息。",
         'reference_fact_ids': [fact['id'] for fact in company['website_facts']]},
        {'key': 'introduction', 'label': MODULES['introduction'], 'max_length': 500,
         'proposal': intro, 'reference_fact_ids': [fact['id'] for fact in company['facts']]}]
    if _exists(connection):
        value['tasks'] = [_decorate(json.loads(row[0])) for row in connection.execute(
            'SELECT record FROM geo_source_maintenance WHERE company_key=? ORDER BY created_at DESC,id DESC', (company['company_key'],))]
    return value


def get_overview(database, cycle_id):
    from services.geo_content_store import _connect, _cycle
    with closing(_connect(database)) as connection:
        return overview(connection, _cycle(connection, cycle_id))


def _validate_proposal(company, module, proposal, reference_fact_ids):
    if module not in MODULES or not isinstance(proposal, str) or not 5 <= len(proposal.strip()) <= 500:
        raise ValueError('请选择企业官网或企业简介，公开说明应为5到500字')
    if company['name'] not in proposal:
        raise ValueError('公开说明必须保留当前公司全称')
    facts = {fact['id']: fact for fact in company['facts']}
    if (not isinstance(reference_fact_ids, list) or not reference_fact_ids
            or any(not isinstance(item, str) or item not in facts for item in reference_fact_ids)
            or len(set(reference_fact_ids)) != len(reference_fact_ids)):
        raise ValueError('公开说明必须引用本轮已确认事实，不能新增未经确认的公司业务')
    allowed_urls = {company['website'].rstrip('/'), *[facts[item].get('source_url', '').rstrip('/') for item in reference_fact_ids]}
    for address in re.findall(r'https?://[^\s<>"）)\]，。；;]+', proposal):
        if address.rstrip('/') not in allowed_urls:
            raise ValueError('公开说明出现本轮确认资料以外的网址')
    if module == 'website' and company['website'].rstrip('/') not in proposal:
        raise ValueError('官网补正必须包含本轮已核对的公司官网')
    # Framing may be edited. Unsupported claims about clients, qualifications
    # or registered details require confirmation as facts, not a new assertion.
    fact_text = '\n'.join(facts[item]['text'] for item in reference_fact_ids)
    for clause in re.split(r'[。；;\n]', proposal):
        if re.search(r'客户案例|成功交付|服务过|中标|获奖|资质|注册资本|法定代表|信用代码', clause):
            if not re.search(r'不(?:声称|涉及|修改|变更|申请)|未(?:声称|核验)|无(?:法|从)|待核', clause) and clause.strip() not in fact_text:
                raise ValueError('客户案例、资质或工商断言须先作为事实核对，不能在申请中编造')
    without_urls = re.sub(r'https?://\S+', '', proposal)
    if re.search(r'(?<!\d)1[3-9]\d{9}(?!\d)|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}', without_urls):
        raise ValueError('联系人与联系方式请仅在官方浏览器填写，不放入公开说明')
    return proposal.strip()


def save_task(database, cycle_id, module, proposal, expected_context_sha256, reference_fact_ids, task_id=None):
    from services.geo_content_store import _connect, _cycle
    with closing(_connect(database)) as connection, connection:
        connection.execute('BEGIN IMMEDIATE')
        cycle = _cycle(connection, cycle_id)
        company = _company(cycle)
        if expected_context_sha256 != digest(company):
            raise ValueError('公司官网或已确认资料已变化，请刷新预览后再保存')
        proposal = _validate_proposal(company, module, proposal, reference_fact_ids)
        connection.execute(CREATE_TABLE)
        rows = connection.execute('SELECT record FROM geo_source_maintenance WHERE company_key=? AND module=?',
                                  (company['company_key'], module)).fetchall()
        previous = json.loads(rows[0][0]) if rows else None
        if previous:
            if previous['cycle_id'] != cycle_id:
                raise ValueError('同一公司此模块已有维护任务，请查看原实验记录，不能重复申请')
            if not _decorate(previous)['can_edit']:
                if previous['proposal'] == proposal:
                    return _decorate(previous)
                raise ValueError('此模块已有辅助填写或申请记录，只能检查原任务，不能覆盖或重放')
            if task_id not in (None, previous['id']):
                raise ValueError('任务编号不一致')
        elif task_id is not None:
            raise ValueError('原任务不存在，未另建任务')
        stamp = _now()
        record = {**(previous or {}), 'id': previous['id'] if previous else uuid.uuid4().hex,
            'cycle_id': cycle_id, 'company_name': company['name'], 'company_key': company['company_key'],
            'platform': 'qcc', 'module': module, 'module_label': MODULES[module], 'entry_url': ENTRY_URL,
            'proposal': proposal, 'proposal_sha256': hashlib.sha256(proposal.encode()).hexdigest(),
            'context_sha256': digest(company), 'reference_fact_ids': reference_fact_ids,
            'stage': 'draft', 'message': '公开说明已保存，请本人核对后打开企查查辅助窗口。',
            'created_at': previous['created_at'] if previous else stamp, 'updated_at': stamp,
            'native_task_id': None, 'write_attempted': False, 'confirmation_seen': False,
            'receipt': None, 'source': 'software_prepared', 'window_open': False}
        connection.execute('INSERT OR REPLACE INTO geo_source_maintenance VALUES(?,?,?,?,?,?,?)',
            (record['id'], cycle_id, company['company_key'], module, json.dumps(record, ensure_ascii=False), record['created_at'], stamp))
    return _decorate(record)


def get_task(database, cycle_id, task_id):
    from services.geo_content_store import _connect, _cycle
    with closing(_connect(database)) as connection:
        cycle = _cycle(connection, cycle_id)
        if cycle['profile'].get('entity_type') != 'company':
            raise ValueError('来源资料维护仅适用于公司实验')
        if not _exists(connection):
            raise ValueError('来源维护任务不存在')
        row = connection.execute('SELECT record FROM geo_source_maintenance WHERE id=? AND cycle_id=?', (task_id, cycle_id)).fetchone()
        if not row:
            raise ValueError('来源维护任务不属于当前公司实验')
        record = json.loads(row[0])
        if record['company_key'] != digest({'company_name': cycle['profile']['name'].strip().casefold()}):
            raise ValueError('公司主体已变化，请先核对原申请')
        if not _decorate(record)['read_only']:
            _company(cycle)
        return _decorate(record)


def record_state(database, cycle_id, task_id, *, stage, message, native_task_id=None,
                 write_attempted=False, confirmation_seen=False, window_open=False, submission_success_observed=False,
                 inspection_only=False):
    from services.geo_content_store import _connect
    if stage not in STAGES:
        raise ValueError('来源维护阶段无效')
    # Only fixed adapter messages and booleans reach this method. Page body,
    # applicant values, contact data and screenshots are never accepted.
    if not isinstance(message, str) or len(message) > 300:
        raise ValueError('来源维护提示无效')
    record = get_task(database, cycle_id, task_id)
    already_submitted = record['stage'] == 'submitted_pending_review'
    if already_submitted:
        stage = 'submitted_pending_review'
    elif stage == 'submitted_pending_review' and not (submission_success_observed is True
            and (record.get('confirmation_seen') or confirmation_seen)
            and (record.get('write_attempted') or write_attempted)):
        raise ValueError('没有与本任务绑定的明确成功观察，不能记为已提交')
    record.update(stage=stage, message=message, updated_at=_now(),
        native_task_id=native_task_id or record.get('native_task_id'),
        inspection_only=record.get('inspection_only') or inspection_only,
        write_attempted=record.get('write_attempted') or write_attempted,
        confirmation_seen=record.get('confirmation_seen') or confirmation_seen, window_open=bool(window_open))
    if stage == 'submitted_pending_review' and not already_submitted:
        record['receipt'] = {'state': 'submitted_pending_review', 'observed_at': _now(),
            'company_name': record['company_name'], 'module': record['module'], 'proposal_sha256': record['proposal_sha256'],
            'evidence': 'explicit_visible_submission_success', 'public_updated': None, 'search_adopted': None}
    for key in ('read_only', 'can_edit', 'can_open'):
        record.pop(key, None)
    with closing(_connect(database)) as connection, connection:
        connection.execute('UPDATE geo_source_maintenance SET record=?,updated_at=? WHERE id=? AND cycle_id=?',
            (json.dumps(record, ensure_ascii=False), record['updated_at'], task_id, cycle_id))
    return _decorate(record)


def import_research_receipt(database, cycle_id, module, proposal, expected_context_sha256,
                            reference_fact_ids, raw_receipt):
    """Domain-only migration of an operator's research receipt; no path input.

    The original bytes are hashed but never rewritten or stored wholesale.
    A source receipt lacking statement text binds the supplied public statement
    as an operator association, never as a software-verified submission body.
    """
    from services.geo_content_store import _connect, _cycle
    if not isinstance(raw_receipt, bytes) or not 0 < len(raw_receipt) <= 1024 * 1024:
        raise ValueError('请传入研究回执原始字节，不接受文件路径')
    payload = json.loads(raw_receipt.decode('utf-8'))
    with closing(_connect(database)) as connection, connection:
        connection.execute('BEGIN IMMEDIATE')
        cycle = _cycle(connection, cycle_id)
        company = _company(cycle)
        if digest(company) != expected_context_sha256:
            raise ValueError('公司或已确认事实已变化，未导入')
        proposal = _validate_proposal(company, module, proposal, reference_fact_ids)
        if (payload.get('company') != company['name'] or payload.get('platform') != 'qcc'
                or payload.get('module') not in (module, MODULES[module])
                or payload.get('status') != 'submitted_pending_review' or payload.get('receipt_visible') is not True
                or payload.get('applicant_details_stored') is not False or payload.get('automatic_retry') is not False
                or payload.get('software_native_task') is True or payload.get('cycle_id', cycle_id) != cycle_id):
            raise ValueError('研究回执与公司、模块或待审核提交观察不一致，未导入')
        if module == 'website' and str(payload.get('website_requested') or '').rstrip('/') != company['website'].rstrip('/'):
            raise ValueError('研究官网回执与本轮确认的公司官网不一致')
        statement_sha = hashlib.sha256(proposal.encode()).hexdigest()
        if payload.get('statement_sha256') and payload['statement_sha256'] != statement_sha:
            raise ValueError('研究回执的公开说明摘要不一致')
        receipt_sha = hashlib.sha256(raw_receipt).hexdigest()
        connection.execute(CREATE_TABLE)
        existing = connection.execute('SELECT record FROM geo_source_maintenance WHERE company_key=? AND module=?',
            (company['company_key'], module)).fetchone()
        if existing:
            record = json.loads(existing[0])
            if (record.get('source') == 'research_assisted_browser'
                    and record.get('receipt', {}).get('source_receipt_sha256') == receipt_sha):
                return _decorate(record)
            raise ValueError('同一公司该模块已有记录，未覆盖或新增申请')
        stamp = _now()
        record = {'id': uuid.uuid4().hex, 'cycle_id': cycle_id, 'company_name': company['name'],
            'company_key': company['company_key'], 'platform': 'qcc', 'module': module, 'module_label': MODULES[module],
            'entry_url': ENTRY_URL, 'proposal': proposal, 'proposal_sha256': statement_sha,
            'context_sha256': digest(company), 'reference_fact_ids': list(reference_fact_ids),
            'stage': 'submitted_pending_review', 'message': '研究辅助流程的原申请已导入为待审核，只能只读核对；不是软件原生提交。',
            'created_at': stamp, 'updated_at': stamp, 'native_task_id': None, 'window_open': False,
            'write_attempted': True, 'confirmation_seen': True, 'source': 'research_assisted_browser',
            'software_native_task': False, 'receipt': {'state': 'submitted_pending_review',
                'source_receipt_sha256': receipt_sha, 'proposal_sha256': statement_sha,
                'proposal_binding_basis': 'source_statement_sha256' if payload.get('statement_sha256') else 'operator_associated_public_statement',
                'company_name': company['name'], 'module': module, 'observed_at': payload.get('success_observed_at') or payload.get('at'),
                'evidence': 'operator_imported_visible_success_observation', 'public_updated': None, 'search_adopted': None}}
        connection.execute('INSERT INTO geo_source_maintenance VALUES(?,?,?,?,?,?,?)',
            (record['id'], cycle_id, company['company_key'], module, json.dumps(record, ensure_ascii=False), stamp, stamp))
    return _decorate(record)
