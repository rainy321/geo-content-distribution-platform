"""Read sources of one frozen native answer without sending or changing it."""
from __future__ import annotations

import copy
import asyncio
import hashlib
import json
import re
import stat
import time
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timezone
from pathlib import Path

from services.doubao_source_collector import _blocked_reason, read_snapshot, reinspect_existing_sources, validate_session_slot
from services.geo_test_runner import write_json

EXPECTED_FIELDS = {'plan_sha256', 'revision', 'response_sha256', 'raw_result_sha256'}
MAX_JSON_BYTES = 5 * 1024 * 1024
MAX_EVIDENCE_FILES = 128
MAX_EVIDENCE_FILE_BYTES = 8 * 1024 * 1024
MAX_EVIDENCE_TOTAL_BYTES = 64 * 1024 * 1024
_ID = re.compile(r'[0-9a-f]{32}')
_SHA = re.compile(r'[0-9a-f]{64}')
_CHAT = re.compile(r'https://(?:www\.)?doubao\.com/chat/[0-9]{1,32}')
_PARTIAL = {'source_ui_not_resolved', 'source_context_not_verified', 'source_capture_binding_failed'}


def _sha(value):
    return hashlib.sha256(value).hexdigest()


def check_local_directory(path, root):
    """No creation here: reject existing links before a profile lock or write."""
    root, path = Path(root).absolute(), Path(path).absolute()
    try:
        path.relative_to(root)
    except ValueError:
        raise ValueError('证据或会话不在该回答的本机目录内') from None
    for candidate in (*reversed(path.parents), path):
        try:
            info = candidate.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise ValueError('来源证据路径不能包含符号链接或重解析点')


def _json_file(path, root):
    """Check every path component before resolving; reject Windows junctions too."""
    root, path = Path(root).absolute(), Path(path).absolute()
    check_local_directory(path, root)
    path = path.resolve(strict=True)
    path.relative_to(root.resolve(strict=True))
    if not path.is_file() or not 0 < path.stat().st_size <= MAX_JSON_BYTES:
        raise ValueError('来源证据文件大小或类型无效')
    content = path.read_bytes()
    value = json.loads(content.decode('utf-8'))
    if not isinstance(value, dict):
        raise ValueError('来源证据格式无效')
    return value, _sha(content), path


def _expected(value):
    if (not isinstance(value, dict) or set(value) != EXPECTED_FIELDS
            or type(value.get('revision')) is not int or value['revision'] < 1
            or any(not isinstance(value.get(key), str) or not _SHA.fullmatch(value[key])
                   for key in EXPECTED_FIELDS - {'revision'})):
        raise ValueError('请核对本轮计划、资料版本和原回答的四项冻结凭据')


def _partial(probe):
    if not isinstance(probe, dict) or not isinstance(probe.get('after'), dict):
        return False
    after = probe.get('after') or {}
    return (probe.get('source') == 'native_collector' and probe.get('native_verified') is True
            and after.get('answer_evaluable') is True and not after.get('answer_error')
            and (after.get('collection_status') == 'partial' or after.get('failure_reason') in _PARTIAL)
            and isinstance(probe.get('evidence'), dict) and _ID.fullmatch(str(probe.get('native_task_id', ''))))


def resolve(data, cycle, probe_id, expected=None):
    """Resolve only internally stored native IDs. No caller path or URL is accepted."""
    if (not _ID.fullmatch(str(cycle.get('id', ''))) or not isinstance(probe_id, str)
            or not _ID.fullmatch(probe_id) or cycle.get('baseline_source') == 'user_reported_external'):
        raise ValueError('只可补采本轮已有的原生单题回答')
    rows = [row for row in cycle.get('probes', []) if isinstance(row, dict) and row.get('id') == probe_id]
    if len(rows) != 1 or not _partial(rows[0]):
        raise ValueError('只可补采有效原生回答中未完整取得的来源，不会重新提问')
    probe = rows[0]
    after, execution = probe['after'], probe.get('execution') or {}
    evidence = probe['evidence']
    question, response = probe.get('question'), after.get('response')
    if not isinstance(question, str) or not question.strip() or not isinstance(response, str) or not response.strip():
        raise ValueError('原问题或完整回答缺失')
    scope = {'plan_sha256': cycle['test_plan']['plan_sha256'], 'revision': cycle['revision'],
             'response_sha256': _sha(response.encode('utf-8')), 'raw_result_sha256': evidence.get('result_sha256')}
    _expected(scope)
    if expected is not None:
        _expected(expected)
        if expected != scope:
            raise ValueError('本轮资料或原回答已变化，请刷新；没有重新提问')
    selected = [item for item in cycle['test_plan']['cases'] if item.get('id') == probe.get('case_id')]
    if (len(selected) != 1 or selected[0].get('question') != question or probe.get('cycle_id') != cycle['id']
            or probe.get('plan_sha256') != scope['plan_sha256'] or probe.get('question_sha256') != _sha(question.encode('utf-8'))):
        raise ValueError('原计划、问题或观察的归属无法绑定')
    root = Path(data) / 'geo-content' / 'probes' / probe_id
    supplied_path = evidence.get('result_path')
    if not isinstance(supplied_path, str):
        raise ValueError('原始证据路径缺失')
    raw, raw_sha, raw_path = _json_file(Path(supplied_path), root / 'evidence')
    if (raw_path.name != 'result.json' or raw_sha != scope['raw_result_sha256']
            or raw.get('question') != question or raw.get('response') != response
            or raw.get('question_submitted') is not True or raw.get('answer_completed') is not True
            or raw.get('collection_status') != 'partial'
            or (raw.get('source_extraction_status') not in ('incomplete', 'partial', 'not_attempted')
                and raw.get('failure_reason') not in _PARTIAL)
            or raw.get('original_answer_timestamp', raw.get('timestamp')) != after.get('collected_at')
            or not _CHAT.fullmatch(str(raw.get('conversation_url', '')))):
        raise ValueError('原始完整回答、时间或会话地址无法绑定')
    environment = raw.get('environment') or {}
    mode, model = environment.get('client_mode'), environment.get('model_label')
    profile_mode = execution.get('profile_mode', mode)
    slot = validate_session_slot(execution.get('session_slot'), mode, profile_mode)
    if (mode not in ('consumer', 'work') or profile_mode not in ('consumer', 'work')
            or not isinstance(model, str) or not model.strip() or len(model) > 100
            or after.get('client_mode') != mode or after.get('model_label') != model
            or execution.get('client_mode') != mode or environment.get('session_slot') != slot
            or after.get('session_slot') != slot or execution.get('mode_policy') not in (None, 'available_web')):
        raise ValueError('原账号会话、模型或界面归属无法绑定')
    task_id = probe['native_task_id']
    job, job_sha, _ = _json_file(Path(data) / 'desktop-jobs' / (task_id + '.json'), Path(data) / 'desktop-jobs')
    saved = (job.get('result') or {}).get('probe') or {}
    if (job.get('id') != task_id or job.get('kind') != 'probe' or job.get('status') != 'completed'
            or job.get('probe_id') != probe_id or job.get('cycle_id') != cycle['id']
            or job.get('session_slot') != slot or job.get('requested_client_mode') != mode
            or job.get('profile_mode', profile_mode) != profile_mode
            or saved.get('id') != probe_id or saved.get('source') != 'native_collector'
            or saved.get('native_verified') is not True or saved.get('question') != question
            or saved.get('evidence') != evidence or saved.get('after') != after):
        raise ValueError('原生采集任务与该回答的绑定不完整')
    snapshot = None
    manifest = [item for item in raw.get('evidence_manifest', [])
                if isinstance(item, dict) and item.get('path') == 'answer.json']
    if len(manifest) != 1 or not _SHA.fullmatch(str(manifest[0].get('sha256', ''))):
        raise ValueError('原回答缺少可核对的完成快照')
    snapshot, snapshot_sha, _ = _json_file(raw_path.parent / 'answer.json', root / 'evidence')
    if snapshot_sha != manifest[0]['sha256']:
        raise ValueError('原回答完成快照已变化')
    details = {'probe_id': probe_id, 'cycle_id': cycle['id'], 'case_id': probe['case_id'], 'expected': scope,
            'raw': raw, 'raw_path': raw_path, 'job_sha256': job_sha, 'task_id': task_id,
            'answer_snapshot': snapshot, 'answer_snapshot_sha256': snapshot_sha,
            'question': question, 'response': response, 'conversation_url': raw['conversation_url'],
            'session_slot': slot, 'client_mode': mode, 'profile_mode': profile_mode, 'model_label': model,
            'mode_policy': execution.get('mode_policy'), 'original_answer_timestamp': after['collected_at']}
    verify_snapshot(snapshot, details)
    return details


def _instant(value):
    value = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if value.tzinfo is None:
        raise ValueError('来源检查时间必须有时区')
    return value.timestamp()


def verify_snapshot(snapshot, details):
    """A reopened page must still prove the original full answer and model."""
    from services.doubao_answer_identity import prove_progress_body_elision
    if not isinstance(snapshot, dict):
        raise ValueError('原回答页面状态无法核对')
    blocked = _blocked_reason(snapshot)
    if blocked:
        raise SourceInspectionNeedAction(blocked)
    if (snapshot.get('page_url') != details['conversation_url'] or snapshot.get('question_verified') is not True
            or snapshot.get('user_message_count') != 1 or snapshot.get('assistant_count', 0) < 1
            or snapshot.get('completion_marker') is not True or snapshot.get('busy')
            or snapshot.get('challenge_visible') or snapshot.get('login_dialog_visible')
            or snapshot.get('client_mode') != details['client_mode']
            or snapshot.get('model_label') != details['model_label']):
        raise ValueError('原回答会话、完整性、界面或模型已变化，未补采来源')
    current_sha = _sha((snapshot.get('answer') or '').encode('utf-8'))
    if current_sha == details['expected']['response_sha256']:
        return {'method': 'exact_complete_response', 'current_answer_sha256': current_sha}
    identity = prove_progress_body_elision(details['response'], details['answer_snapshot'], snapshot)
    if not identity:
        raise ValueError('原完整回答已变化，未补采来源')
    return {'method': 'exact_final_body_with_dom_proven_progress_prefix', **identity}


class SourceInspectionNeedAction(ValueError):
    def __init__(self, reason):
        self.reason = reason
        text = {'human_verification_required': '原会话需要正常完成安全验证',
                'login_required': '原账号会话需要正常登录',
                'session_redirected_to_logout': '原账号会话已退出，需要正常登录',
                'rate_limited': '豆包当前限制访问，请稍后手动检查',
                'website_temporarily_unavailable': '豆包页面暂不可用',
                'service_capacity_unavailable': '豆包服务当前不可用',
                'original_answer_not_ready': '原历史回答在等待时间内仍未完整加载'}.get(reason, '原会话需要检查')
        super().__init__(text + '；已停止只读补采，没有重新提问。')


@contextmanager
def _target_navigation(page, url):
    if not all(hasattr(page, name) for name in ('main_frame', 'on', 'remove_listener')):
        raise ValueError('来源补采需要可观察导航的原页面')
    state = {'away': False}
    def navigated(frame):
        if frame == page.main_frame and frame.url != url:
            state['away'] = True
    page.on('framenavigated', navigated)
    try:
        yield state
    finally:
        page.remove_listener('framenavigated', navigated)


def _check_navigation(state):
    if state['away']:
        raise ValueError('原页面曾离开目标聊天，已停止来源补采；不会重新提问')


async def wait_for_original_answer(page, config, details, *, navigation=None,
                                   timeout_seconds=15, interval_seconds=.25):
    """Wait only for SPA hydration; never navigate again, fill, send or switch model."""
    if navigation is None:
        with _target_navigation(page, details['conversation_url']) as navigation:
            return await wait_for_original_answer(page, config, details, navigation=navigation,
                timeout_seconds=timeout_seconds, interval_seconds=interval_seconds)
    deadline, stable, last_identity = time.monotonic() + timeout_seconds, 0, None
    while True:
        _check_navigation(navigation)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise SourceInspectionNeedAction('original_answer_not_ready')
        try:
            snapshot = await asyncio.wait_for(read_snapshot(page, config), timeout=remaining)
        except asyncio.TimeoutError:
            raise SourceInspectionNeedAction('original_answer_not_ready') from None
        _check_navigation(navigation)
        if not isinstance(snapshot, dict):
            raise ValueError('原页面状态无法核对，未补采来源')
        blocked = _blocked_reason(snapshot)
        if blocked:
            raise SourceInspectionNeedAction(blocked)
        if (snapshot.get('page_url') != details['conversation_url']
                or snapshot.get('client_mode') not in (None, 'unknown', details['client_mode'])
                or snapshot.get('model_label') not in (None, '', details['model_label'])
                or type(snapshot.get('user_message_count')) is int and snapshot['user_message_count'] > 1
                or snapshot.get('user_message_count') == 1 and snapshot.get('question_verified') is False):
            raise ValueError('原页面的问题、会话、界面或模型不匹配，未补采来源')
        ready = (snapshot.get('question_verified') is True and type(snapshot.get('user_message_count')) is int
                 and snapshot['user_message_count'] == 1 and type(snapshot.get('assistant_count')) is int
                 and snapshot['assistant_count'] >= 1 and snapshot.get('completion_marker') is True
                 and not snapshot.get('busy') and isinstance(snapshot.get('answer'), str) and snapshot['answer'])
        if ready:
            identity = verify_snapshot(snapshot, details)
            stable = stable + 1 if identity == last_identity else 1
            last_identity = identity
            if stable >= 2:
                return identity
        else:
            stable, last_identity = 0, None
        if time.monotonic() >= deadline:
            raise SourceInspectionNeedAction('original_answer_not_ready')
        await asyncio.sleep(min(interval_seconds, max(0, deadline - time.monotonic())))


@asynccontextmanager
async def _watched_answer(page, config, details):
    with _target_navigation(page, details['conversation_url']) as navigation:
        before = await wait_for_original_answer(page, config, details, navigation=navigation)
        yield {'before': before, 'navigation': navigation}


def _inspection_result(item, details):
    proof = item.get('inspection_provenance') or {}
    context = item.get('source_context_binding') or {}
    identity = proof.get('answer_identity_method')
    identity_ok = (identity == 'exact_complete_response'
                   and proof.get('current_answer_sha256') == details['expected']['response_sha256'])
    if identity == 'exact_final_body_with_dom_proven_progress_prefix':
        identity_ok = (proof.get('original_answer_snapshot_sha256') == details['answer_snapshot_sha256']
                       and proof.get('final_body_sha256') == proof.get('current_answer_sha256')
                       and _SHA.fullmatch(str(proof.get('current_answer_sha256', '')))
                       and type(proof.get('omitted_progress_body_count')) is int
                       and 1 <= proof['omitted_progress_body_count'] <= 6)
    if (item.get('mode') != 'inspect_existing_answer' or item.get('question') != details['question']
            or item.get('response') != details['response'] or item.get('conversation_url') != details['conversation_url']
            or item.get('original_result_sha256') != details['expected']['raw_result_sha256']
            or item.get('original_response_sha256') != details['expected']['response_sha256']
            or item.get('original_answer_timestamp') != details['original_answer_timestamp']
            or item.get('question_submission_attempted') is not False or item.get('question_submitted') is not True
            or item.get('answer_completed') is not True or item.get('collection_status') not in ('success', 'partial')
            or proof.get('kind') != 'later_read_only_sources_not_new_answer' or proof.get('question_sent') is not False
            or proof.get('original_result_unchanged') is not True
            or proof.get('question_sha256') != _sha(details['question'].encode('utf-8'))
            or not isinstance(proof.get('navigation_urls'), list) or not proof['navigation_urls']
            or any(url != details['conversation_url'] for url in proof['navigation_urls'])
            or type(context.get('schema_version')) is not int or context['schema_version'] != 1 or context.get('status') != 'verified'
            or context.get('evidence_file') != 'source-context.json'
            or context.get('target_question_sha256') != _sha(details['question'].encode('utf-8'))
            or context.get('target_answer_sha256') != details['expected']['response_sha256']
            or context.get('canonical_url_sha256') != _sha(details['conversation_url'].encode('utf-8'))
            or not identity_ok
            or not isinstance(item.get('displayed_sources'), list) or not isinstance(item.get('citations'), list)
            or _instant(item.get('timestamp')) <= _instant(details['original_answer_timestamp'])):
        raise ValueError('后采来源的原回答绑定未通过，未作为来源展示')
    return item


def _context_manifest(item, result_path, root):
    _evidence_manifest(item, result_path, root)
    entries = [row for row in item.get('evidence_manifest', [])
               if isinstance(row, dict) and row.get('path') == 'source-context.json']
    if len(entries) != 1:
        raise ValueError('后采来源缺少上下文完整性凭据')
    proof, digest, _ = _json_file(result_path.parent / 'source-context.json', root)
    summary = item['source_context_binding']
    if (digest != entries[0].get('sha256') or proof.get('blocked') is not False
            or proof.get('source_phase_completed') is not True or proof.get('navigation_away_seen') is not False
            or any(proof.get(key) != summary.get(key) for key in (
                'schema_version', 'evidence_file', 'target_question_sha256', 'target_answer_sha256', 'canonical_url_sha256'))):
        raise ValueError('后采来源上下文凭据已变化')


def _evidence_manifest(item, result_path, root):
    """Verify all flat evidence on every use; no metadata-only integrity cache."""
    manifest = item.get('evidence_manifest')
    if not isinstance(manifest, list) or not 1 <= len(manifest) <= MAX_EVIDENCE_FILES:
        raise ValueError('后采证据清单缺失或文件数量超限')
    names, total = {}, result_path.stat().st_size
    for row in manifest:
        if (not isinstance(row, dict) or set(row) != {'path', 'sha256', 'bytes'}
                or not isinstance(row.get('path'), str)
                or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,120}', row['path'])
                or row['path'].endswith('.')
                or row['path'].split('.')[0].upper() in {'CON', 'PRN', 'AUX', 'NUL',
                    *('COM' + str(i) for i in range(1, 10)), *('LPT' + str(i) for i in range(1, 10))}
                or row['path'].casefold() in {'result.json', 'binding.json'}
                or row['path'].casefold() in names or not _SHA.fullmatch(str(row.get('sha256', '')))
                or type(row.get('bytes')) is not int or not 1 <= row['bytes'] <= MAX_EVIDENCE_FILE_BYTES):
            raise ValueError('后采证据清单存在重复、越界或无效条目')
        total += row['bytes']
        if total > MAX_EVIDENCE_TOTAL_BYTES:
            raise ValueError('后采证据总体积超限')
        names[row['path'].casefold()] = row
    refs = ['source-context.json']
    screenshots = item.get('screenshots', [])
    if not isinstance(screenshots, list):
        raise ValueError('后采截图引用格式无效')
    refs.extend(screenshots)
    for source in [*item.get('citations', []), *item.get('displayed_sources', [])]:
        if not isinstance(source, dict):
            raise ValueError('后采来源条目格式无效')
        if source.get('evidence_file') is not None:
            refs.append(source['evidence_file'])
        if 'evidence_files' in source:
            if not isinstance(source['evidence_files'], list):
                raise ValueError('后采来源证据引用格式无效')
            refs.extend(source['evidence_files'])
    if any(not isinstance(name, str) or name.casefold() not in names
           or names[name.casefold()]['path'] != name for name in refs):
        raise ValueError('后采来源引用了未列入清单的证据')
    check_local_directory(result_path.parent, root)
    entries = list(result_path.parent.iterdir())
    if len(entries) > MAX_EVIDENCE_FILES + 2:
        raise ValueError('后采证据目录文件数量超限')
    allowed = {*names, 'result.json', 'binding.json'}
    actual_total = 0
    for path in entries:
        info = path.lstat()
        if (path.name.casefold() not in allowed or not stat.S_ISREG(info.st_mode)
                or stat.S_ISLNK(info.st_mode)
                or getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
            raise ValueError('后采证据目录包含未登记或非普通文件')
        actual_total += info.st_size
        if actual_total > MAX_EVIDENCE_TOTAL_BYTES:
            raise ValueError('后采证据总体积超限')
    for row in names.values():
        path = result_path.parent / row['path']
        before = path.lstat()
        if (not stat.S_ISREG(before.st_mode) or stat.S_ISLNK(before.st_mode)
                or getattr(before, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
                or before.st_size != row['bytes']):
            raise ValueError('后采证据文件缺失、类型或大小不符')
        digest = hashlib.sha256()
        with path.open('rb') as stream:
            for chunk in iter(lambda: stream.read(64 * 1024), b''):
                digest.update(chunk)
        after = path.lstat()
        signature = lambda value: (value.st_size, value.st_mtime_ns, value.st_ctime_ns, value.st_ino, value.st_dev,
                                   getattr(value, 'st_file_attributes', 0))
        if digest.hexdigest() != row['sha256'] or signature(before) != signature(after):
            raise ValueError('后采证据文件已变化')


def _complete(item):
    from services.geo_test_service import _urls_complete
    absent = item.get('source_absence_verified') is True
    return (item.get('collection_status') == 'success'
            and (item.get('source_extraction_status') == 'captured' or absent)
            and all(_urls_complete(item.get(key)) or absent and item.get(key) == []
                    for key in ('displayed_sources', 'citations')))


def saved_inspection(data, cycle, details):
    root = Path(data) / 'geo-content' / 'probe-source-inspections' / details['probe_id']
    for binding_path in sorted(root.glob('inspect-*/binding.json'), reverse=True)[:100]:
        try:
            binding, _, _ = _json_file(binding_path, root)
            if (binding.get('kind') != 'geo_probe_source_inspection_v1' or binding.get('cycle_id') != cycle['id']
                    or binding.get('probe_id') != details['probe_id'] or binding.get('question_sent') is not False
                    or binding.get('raw_unchanged') is not True or binding.get('integrity_passed') is not True
                    or binding.get('bound_original_job_sha256') != details['job_sha256']
                    or type(binding.get('revision')) is not int or binding['revision'] != details['expected']['revision']
                    or binding.get('plan_sha256') != details['expected']['plan_sha256']
                    or binding.get('bound_original_raw_sha256') != details['expected']['raw_result_sha256']
                    or binding.get('response_sha256') != details['expected']['response_sha256']
                    or binding.get('session_slot') != details['session_slot']
                    or binding.get('client_mode') != details['client_mode'] or binding.get('model_label') != details['model_label']):
                continue
            result, result_sha, result_path = _json_file(binding_path.parent / 'result.json', root)
            if result_sha != binding.get('inspection_result_sha256'):
                continue
            _inspection_result(result, details)
            _context_manifest(result, result_path, root)
            from services.geo_test_service import assess_answer
            from services.geo_research_probe_service import project_probe_address_evidence
            assessed = assess_answer(cycle['test_plan']['profile'], result, details['question'])
            if not assessed['answer_evaluable']:
                continue
            source_assessment = {key: assessed[key] for key in ('official_source_present', 'official_citation_present')}
            source_side = {'response': details['response'], 'answer_evaluable': True,
                           'citations': result['citations'], 'displayed_sources': result['displayed_sources'], **source_assessment}
            [addresses] = project_probe_address_evidence(cycle['test_plan']['profile'],
                [{'category': 'brand_identity', 'source': 'native_collector', 'before': {}, 'after': source_side}])
            return {'kind': 'later_read_only_source_view', 'inspected_at': result['timestamp'],
                'original_answer_timestamp': details['original_answer_timestamp'], 'inspection_result_sha256': result_sha,
                'bound_original_raw_sha256': details['expected']['raw_result_sha256'],
                'integrity_passed': True, 'new_answer_collected': False, 'question_sent': False,
                'collection_status': result['collection_status'], 'source_extraction_status': result.get('source_extraction_status'),
                'source_collection_complete': _complete(result),
                'displayed_sources': copy.deepcopy(result['displayed_sources']), 'citations': copy.deepcopy(result['citations']),
                'source_assessment': source_assessment, 'address_evidence': addresses.get('address_evidence', {}).get('after', []),
                'note': '后来只读补采同一回答的来源；原回答、原采集时间和业务核对不变，不是新回答或复测。'}
        except (OSError, ValueError, TypeError, KeyError, AttributeError, UnicodeError):
            continue
    return None


def project(data, cycle):
    """Optional read-only fields; original after/assessment/time remain untouched."""
    if not isinstance(cycle, dict) or not isinstance(cycle.get('probes'), list):
        return []
    projected = copy.deepcopy(cycle['probes'])
    for probe in projected:
        if not _partial(probe):
            continue
        try:
            details = resolve(data, cycle, probe['id'])
            saved = saved_inspection(data, cycle, details)
            if saved:
                probe['source_reinspection'] = saved
            if not saved or saved['source_collection_complete'] is not True:
                probe['source_inspection'] = {'available': True, 'expected': details['expected'],
                    'session_slot': details['session_slot'], 'client_mode': details['client_mode'],
                    'original_answer_timestamp': details['original_answer_timestamp'], 'question_sent': False}
        except (OSError, ValueError, TypeError, KeyError, AttributeError, UnicodeError):
            continue
    return projected


@asynccontextmanager
async def open_original_page(config, url):
    """Normal browser navigation to the stored target; never fill or send."""
    from playwright.async_api import async_playwright
    from services.browser_runtime import launch_context
    async with async_playwright() as playwright:
        context = await launch_context(playwright, config.profile_dir.resolve(),
            {'headless': not config.headed, 'viewport': {'width': 1440, 'height': 1000},
             'locale': 'zh-CN', 'timezone_id': 'Asia/Hong_Kong',
             **({'executable_path': config.executable_path} if config.executable_path else {})},
            config.fallback_executable_path)
        try:
            page = await context.new_page()
            await page.goto(url, wait_until='domcontentloaded', timeout=config.navigation_timeout_seconds * 1000)
            yield page
        finally:
            await context.close()


async def inspect(data, cycle, details, config, *, progress=None):
    """Caller holds the original profile lock and the native serial task slot."""
    profile_name = ('consumer-secondary-browser-profile' if details['session_slot'] == 'secondary'
                    else 'consumer-browser-profile' if details['profile_mode'] == 'consumer' else 'browser-profile')
    if (config.session_slot != details['session_slot'] or config.client_mode != details['client_mode']
            or config.profile_dir.absolute() != (Path(data) / 'doubao' / profile_name).absolute()):
        raise ValueError('补采只能使用原账号会话和界面')
    check_local_directory(config.profile_dir, data)
    check_local_directory(config.output_root, data)
    saved = saved_inspection(data, cycle, details)
    if saved and saved['source_collection_complete'] is True:
        raise ValueError('这条回答已补采来源，请查看已有补采时间；不会重复读取')
    async with open_original_page(config, details['conversation_url']) as page, _watched_answer(page, config, details) as identities:
        before = identities['before']
        result, path = await reinspect_existing_sources(config, details['raw_path'], page=page, progress=progress)
        _check_navigation(identities['navigation'])
        after = verify_snapshot(await read_snapshot(page, config), details)
        _check_navigation(identities['navigation'])
        from services.geo_content_store import get_cycle
        current_cycle = get_cycle(Path(data) / 'database.db', cycle['id'])
        frozen = resolve(data, current_cycle, details['probe_id'], details['expected'])
        if frozen['job_sha256'] != details['job_sha256'] or frozen['answer_snapshot_sha256'] != details['answer_snapshot_sha256']:
            raise ValueError('原任务或回答证据已变化，后采来源未生效')
        root = Path(data) / 'geo-content' / 'probe-source-inspections' / details['probe_id']
        persisted, result_sha, path = _json_file(path, root)
        if (path.name != 'result.json' or path.parent.parent != root.resolve()
                or not re.fullmatch(r'inspect-[0-9]{8}-[0-9]{6}-[0-9a-f]{8}', path.parent.name)):
            raise ValueError('后采结果必须属于本条回答的独立证据目录')
        if persisted != result:
            raise ValueError('后采来源持久结果不一致')
        _inspection_result(result, details)
        _context_manifest(result, path, root)
        write_json(path.parent / 'binding.json', {'kind': 'geo_probe_source_inspection_v1',
            'cycle_id': cycle['id'], 'probe_id': details['probe_id'], **details['expected'],
            'bound_original_raw_sha256': details['expected']['raw_result_sha256'],
            'bound_original_job_sha256': details['job_sha256'], 'inspection_result_sha256': result_sha,
            'session_slot': details['session_slot'], 'client_mode': details['client_mode'], 'model_label': details['model_label'],
            'integrity_passed': True, 'raw_unchanged': True, 'question_sent': False,
            'before_identity': before, 'after_identity': after, 'bound_at': datetime.now(timezone.utc).isoformat()})
    view = saved_inspection(data, cycle, details)
    return {'probe_id': details['probe_id'], 'cycle_id': cycle['id'], 'source_reinspection': view,
        'message': ('同一回答的来源已单独保存并标注补采时间；没有重新提问，不计为新回答或复测。'
                    if view and view['source_collection_complete'] else
                    '同一回答的后采来源已保存，但仍未完整解析；未知不能算没有来源，可手动复查，不会自动重复。')}
