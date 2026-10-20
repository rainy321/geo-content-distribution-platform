"""Read existing local reports without accepting filesystem paths from clients."""
from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime
from pathlib import Path


def _instant(value):
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return parsed.timestamp() if parsed.tzinfo is not None else None
    except (AttributeError, TypeError, ValueError):
        return None


def _source_sidecars(root):
    """Index only the designated local inspection tree, never client paths."""
    index = {}
    for path in (root / 'source-inspections').glob('inspect-*/result.json'):
        try:
            path = path.resolve()
            path.relative_to(root)
            if path.stat().st_size > 5 * 1024 * 1024:
                continue
            content = path.read_bytes()
            item = json.loads(content.decode('utf-8'))
            proof = item.get('inspection_provenance') or {}
            original_path = Path(item.get('original_result_path') or '').resolve()
            original_path.relative_to(root)
            if (item.get('mode') != 'inspect_existing_answer' or item.get('collection_status') not in {'success', 'partial'}
                    or item.get('answer_completed') is not True or item.get('question_submitted') is not True
                    or proof.get('kind') != 'later_read_only_sources_not_new_answer' or proof.get('question_sent') is not False
                    or proof.get('original_result_unchanged') is not True or _instant(item.get('timestamp')) is None
                    or not isinstance(item.get('citations'), list) or not isinstance(item.get('displayed_sources'), list)):
                continue
            index.setdefault(str(original_path), []).append((item, hashlib.sha256(content).hexdigest()))
        except (OSError, ValueError, TypeError, AttributeError, UnicodeError):
            continue
    for candidates in index.values():
        candidates.sort(key=lambda value: _instant(value[0]['timestamp']), reverse=True)
    return index


def _project_source_sidecars(report, root, index, report_sha256):
    """Resolve later source evidence without inventing a new answer or run."""
    from services.geo_test_service import assess_answer

    projected = copy.deepcopy(report)
    applied = []
    for row in projected['cases']:
        if (not isinstance(row.get('assessment'), dict) or row['assessment'].get('answer_evaluable') is not True
                or row.get('status') not in {'success', 'partial'} or not isinstance(row.get('result_path'), str)):
            continue
        try:
            original_path = Path(row['result_path']).resolve()
            original_path.relative_to(root)
            if original_path.name != 'result.json' or original_path.stat().st_size > 5 * 1024 * 1024:
                continue
            original_bytes = original_path.read_bytes()
            original_sha = hashlib.sha256(original_bytes).hexdigest()
            original = json.loads(original_bytes.decode('utf-8'))
            original_time = original.get('original_answer_timestamp') or original.get('timestamp')
            answer_time = _instant(original_time)
            if (answer_time is None or original.get('question') != row.get('question')
                    or original.get('response') != row.get('response') or row.get('collected_at') != original_time
                    or original.get('question_submitted') is not True or original.get('answer_completed') is not True):
                continue
            for item, inspection_sha in index.get(str(original_path), []):
                proof = item['inspection_provenance']
                if (item.get('original_result_sha256') != original_sha or item.get('question') != row.get('question')
                        or item.get('response') != row.get('response') or item.get('original_answer_timestamp') != original_time
                        or _instant(item['timestamp']) <= answer_time
                        or item.get('conversation_url') != original.get('conversation_url')
                        or item.get('original_response_sha256') != hashlib.sha256(original['response'].encode('utf-8')).hexdigest()
                        or proof.get('question_sha256') != hashlib.sha256(row['question'].encode('utf-8')).hexdigest()):
                    continue
                identity = proof.get('answer_identity_method')
                if identity == 'exact_complete_response':
                    if proof.get('current_answer_sha256') != item['original_response_sha256']:
                        continue
                elif identity == 'exact_final_body_with_dom_proven_progress_prefix':
                    snapshot_path = original_path.parent / 'answer.json'
                    snapshot_bytes = snapshot_path.read_bytes()
                    expected = next((value.get('sha256') for value in original.get('evidence_manifest', [])
                                     if isinstance(value, dict) and value.get('path') == 'answer.json'), None)
                    if (not expected or hashlib.sha256(snapshot_bytes).hexdigest() != expected
                            or proof.get('original_answer_snapshot_sha256') != expected
                            or proof.get('final_body_sha256') != proof.get('current_answer_sha256')
                            or type(proof.get('omitted_progress_body_count')) is not int
                            or not 1 <= proof['omitted_progress_body_count'] <= 6):
                        continue
                else:
                    continue
                # Current evidence may resolve URLs, while the original row's
                # eligible/mention/status/time evidence remains authoritative.
                assessed = assess_answer(projected['profile'], item, row['question'])
                if not assessed['answer_evaluable']:
                    continue
                for field in ('citations', 'displayed_sources', 'visible_source_count', 'visible_citation_count'):
                    row[field] = copy.deepcopy(item.get(field))
                for field in ('official_source_present', 'official_citation_present'):
                    row['assessment'][field] = assessed[field]
                row['source_inspected_at'] = item['timestamp']
                row['source_reinspection'] = {
                    'kind': 'later_read_only_source_view', 'inspected_at': item['timestamp'], 'after_answer': True,
                    'original_answer_timestamp': original_time, 'original_result_sha256': original_sha,
                    'inspection_result_sha256': inspection_sha, 'inspection_id': item.get('run_id'),
                    'answer_identity_method': identity, 'omitted_progress_body_count': proof.get('omitted_progress_body_count', 0),
                    'new_answer_collected': False, 'source_collection_status': item['collection_status'],
                    'note': '来源网址于原回答后另行只读核对；原问题、回答及采集时间未变，不是新回答或复测。'}
                applied.append(row['id'])
                break
        except (OSError, ValueError, TypeError, KeyError, AttributeError, UnicodeError):
            continue
    if applied:
        projected['source_reinspection'] = {'kind': 'later_read_only_source_projection', 'applied_case_ids': applied,
            'new_answer_collected': False, 'original_report_sha256': report_sha256,
            'note': '部分来源采用原回答后的只读复查视图，来源检查时间另行标注；未重新提问，未新增回答或改写原始报告。'}
    return projected


def project_report_evaluation(report: dict) -> dict:
    """Downgrade obsolete assessments without rewriting collection evidence.

    Saved reports flatten collector results and do not retain all submission,
    answer completion and extraction flags. Reconstructing a successful result
    from their status or answer text would invent evidence. Existing eligible
    assessments may therefore be excluded by current rules, never upgraded.
    """
    from services.geo_test_service import (
        EVALUATION_VERSION, answer_evaluation_error, report_cohorts, evaluation_semantics,
    )

    projected = copy.deepcopy(report)
    semantics=evaluation_semantics(projected.get('profile') or {})
    changed_case_ids = []
    invalid_statuses = {'failed', 'stopped', 'pending', 'running', 'needs_review',
                        'need_action', 'service_unavailable', 'deferred_answer', 'private_account_context'}
    for index, row in enumerate(projected['cases']):
        stored_assessment = row.get('assessment')
        assessment = dict(stored_assessment) if isinstance(stored_assessment, dict) else {}
        assessment.setdefault('answer_evaluable', False)
        for field in ('brand_mentioned', 'official_source_present', 'official_citation_present'):
            assessment.setdefault(field, None)
        if semantics=='target_entity_scoped_urls':
            for field in ('company_mentioned','related_company_mentioned'):
                assessment.setdefault(field,None)
            assessment['evaluation_semantics']=semantics
        response = row.get('response')
        client_mode = row.get('client_mode') or (projected.get('execution') or {}).get('client_mode')
        error = answer_evaluation_error(response if isinstance(response, str) else '', client_mode=client_mode)
        if error or row.get('status') in invalid_statuses or assessment['answer_evaluable'] is not True:
            assessment.update(answer_evaluable=False, brand_mentioned=None,
                              official_source_present=None, official_citation_present=None,
                              recommended=None, fact_accuracy=None, matched_terms=[], mention_evidence=[])
            for field in ('company_mentioned','related_company_mentioned'):
                if field in assessment:
                    assessment[field]=None
            if error:
                assessment.update(answer_error=error, matched_terms=[], mention_evidence=[],
                                  evaluation_note=('回答声称使用工作账户的内部资料，不能作为公开 GEO 曝光；只读排除有效题分母，不作为零曝光。'
                                                   if error == 'private_account_context' else
                                                   '读取时按当前规则排除未完成或不可用回答；不作为零曝光。'))
                if row.get('status') in ('success', 'completed', 'partial', 'completed_with_gaps'):
                    row['status'] = {'background_task_not_final': 'deferred_answer',
                                     'private_account_context': 'private_account_context'}.get(error, 'service_unavailable')
        row['assessment'] = assessment
        if row != report['cases'][index]:
            changed_case_ids.append(row.get('id') or f'case-{index + 1}')

    projected['cohorts'] = report_cohorts(projected['cases'])
    if projected.get('status') == 'completed' and any(
            row['assessment']['answer_evaluable'] is not True for row in projected['cases']):
        projected['status'] = 'completed_with_gaps'
    projected['evaluation_version'] = EVALUATION_VERSION
    projected['evaluation_semantics']=semantics
    projected['evaluation_projection'] = {
        'mode': 'read_only_conservative_current_rules',
        'stored_evaluation_version': report.get('evaluation_version') or 'unknown',
        'applied_evaluation_version': EVALUATION_VERSION,
        'stored_evaluation_semantics':report.get('evaluation_semantics') or semantics,
        'applied_evaluation_semantics':semantics,
        'changed_case_ids': changed_case_ids,
        'cohorts_changed': projected['cohorts'] != report.get('cohorts'),
        'original_status': report.get('status'),
        'source_file_unchanged': True,
        'new_collection_performed': False,
        'note': ('本页为读取时的只读评估投影：按当前规则保守排除后台委托占位、声称使用账户内部资料及不可用回答，'
                 '重新计算有效题分母；缺少原始完成证据的旧题不会提升为有效。'
                 '原始报告、运行编号、采集时间与证据文件未修改，也未重新采集。'),
    }
    if semantics=='legacy_mixed_entity_hostwide':
        projected['evaluation_projection']['note'] += ' 此历史口径混合品牌、产品和公司名称，官网按域名判断；不能作为新对象隔离口径的产品曝光结论。'
    else:
        projected['evaluation_projection']['note'] += ' 当前口径仅统计本测试对象名称与别名，官网按明确URL范围判断；运营公司提及单独记录。'
    if isinstance(report.get('source_reinspection'), dict) and isinstance(report['source_reinspection'].get('note'), str):
        projected['evaluation_projection']['note'] += report['source_reinspection']['note']
    return projected


def _reports(root: Path):
    root = root.resolve()
    if not root.is_dir():
        return
    inspections = _source_sidecars(root)
    for path in root.rglob("report.json"):
        try:
            resolved = path.resolve()
            relative = resolved.relative_to(root)
            if resolved.stat().st_size > 5 * 1024 * 1024:
                continue
            report_bytes = resolved.read_bytes()
            report = json.loads(report_bytes.decode('utf-8'))
            if (report.get("schema_version") != "1.0"
                    or report.get("kind") != "geo_search_test_report"
                    or not isinstance(report.get("profile"), dict)
                    or not isinstance(report["profile"].get("name"), str)
                    or not isinstance(report.get("cases"), list)
                    or any(not isinstance(case, dict) or case.get('category') not in (
                        'discovery', 'brand_identity', 'website_guided') for case in report['cases'])):
                continue
            report_id = hashlib.sha256(relative.as_posix().encode("utf-8")).hexdigest()[:24]
            report = _project_source_sidecars(report, root, inspections, hashlib.sha256(report_bytes).hexdigest())
            yield report_id, project_report_evaluation(report)
        except (OSError, ValueError, AttributeError):
            continue


def list_saved_reports(root: Path) -> list[dict]:
    reports = [{"id": report_id, "name": report["profile"]["name"],
                "run_id": report.get("run_id"), "status": report.get("status"),
                "updated_at": report.get("updated_at"), "case_count": len(report["cases"]),
                "evaluation_version": report.get("evaluation_version"),
                "evaluation_semantics":report.get('evaluation_semantics'),
                "entity_type":report['profile'].get('entity_type'),"website":report['profile'].get('website'),
                "source_reinspection": report.get("source_reinspection"),
                "evaluation_projection": report.get("evaluation_projection")}
               for report_id, report in _reports(root)]
    return sorted(reports, key=lambda item: str(item.get("updated_at") or ""), reverse=True)


def read_saved_report(root: Path, report_id: str) -> dict | None:
    for candidate, report in _reports(root):
        if candidate == report_id:
            return report
    return None
