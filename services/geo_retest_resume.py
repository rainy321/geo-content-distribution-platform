"""Validate and copy a saved experiment before resuming only unsent cases."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat

from services.geo_test_service import validate_plan


def _read_json(path):
    if path.stat().st_size > 5 * 1024 * 1024:
        raise ValueError('原采集记录过大，不能自动继续')
    return json.loads(path.read_text(encoding='utf-8'))


def _ordinary(path):
    info = path.lstat()
    if path.is_symlink() or getattr(info, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0):
        raise ValueError('原采集目录包含链接，已停止；原记录保留')
    return info


def resume_source(data, source, cycle):
    if (not isinstance(source, dict) or source.get('kind') != 'run'
            or source.get('status') not in {'failed', 'need_action', 'stopped', 'needs_review', 'completed'}
            or source.get('cycle_id') != cycle.get('id')):
        raise ValueError('只能继续本轮已停止的采集任务')
    runs = (Path(data) / 'geo-tests/desktop-runs').resolve()
    path = Path(source.get('run_dir') or '')
    if (not path.is_absolute() or path.resolve().parent != runs
            or not re.fullmatch(r'[0-9a-f]{32}', path.name)):
        raise ValueError('原采集目录不属于本机受控任务目录')
    _ordinary(path)
    for name in ('plan.json', 'state.json', 'report.json'):
        if not stat.S_ISREG(_ordinary(path / name).st_mode):
            raise ValueError('原采集文件不完整，请先核对原记录')
    plan = validate_plan(_read_json(path / 'plan.json'))
    state = _read_json(path / 'state.json')
    report = _read_json(path / 'report.json')
    current = validate_plan(cycle['test_plan'])
    stored_report = (source.get('result') or {}).get('report') or {}
    if (plan['plan_sha256'] != current['plan_sha256'] or state.get('plan_sha256') != plan['plan_sha256']
            or report.get('plan_sha256') != plan['plan_sha256']
            or stored_report.get('plan_sha256') != plan['plan_sha256']
            or not isinstance(state.get('run_id'), str) or not state['run_id']
            or report.get('run_id') != state['run_id'] or stored_report.get('run_id') != state['run_id']):
        raise ValueError('原采集与当前固定问题或实验编号不一致')
    if state.get('status') == 'running':
        raise ValueError('原采集仍在执行或提交状态待核对，不能继续')
    cases = state.get('cases')
    if not isinstance(cases, dict) or set(cases) != {case['id'] for case in plan['cases']}:
        raise ValueError('原采集问题清单不完整')
    pending = []
    for case in plan['cases']:
        identifier = case['id']
        entry = cases[identifier]
        if not isinstance(entry, dict) or entry.get('status') == 'running':
            raise ValueError('原采集中有提交状态未知的问题，请先人工核对')
        result = entry.get('result') or {}
        if not isinstance(result, dict):
            raise ValueError('原问题的发送记录格式无效')
        if entry.get('status') == 'pending':
            if (set(entry) != {'status'} or result or entry.get('started_at') or entry.get('result_path')
                    or (path / 'evidence' / identifier).exists()):
                raise ValueError('待执行问题存在发送或采集痕迹，已停止以免重复发送')
            pending.append(identifier)
        elif entry.get('status') in {'failed', 'need_action'} and result.get('question_submission_attempted') is False:
            if (result.get('question_submitted') is not False or result.get('submission_receipt')
                    or entry.get('question_submission_attempted', False) is not False
                    or entry.get('question_submitted', False) is not False):
                raise ValueError('原问题的发送状态不明确，不能自动继续')
            pending.append(identifier)
        # Every other completed, submitted, failed or unknown entry is kept;
        # the existing runner skips it without resetting the first question.
    if not pending:
        raise ValueError('没有可继续的未发送问题；已发送问题不会重发')
    execution = state.get('execution') or {}
    if (execution.get('client_mode') not in {'consumer', 'work'}
            or source.get('requested_client_mode') != execution['client_mode']
            or source.get('requested_model_label') != execution.get('requested_model_label')):
        raise ValueError('原采集界面或模型配置缺失或不一致')
    if ('profile_mode' in execution and (execution['profile_mode'] not in ('consumer', 'work')
            or source.get('profile_mode') != execution['profile_mode'])):
        raise ValueError('原专用网页会话类型缺失或不一致')
    if ('mode_policy' in execution and (execution['mode_policy'] != 'available_web'
            or source.get('mode_policy') != execution['mode_policy'] or 'profile_mode' not in execution)):
        raise ValueError('原网页模式策略缺失或不一致')
    return {'run_dir': path, 'plan': plan, 'state': state, 'case_ids': pending, 'execution': execution}


def file_digest(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def source_manifest(root):
    root = Path(root)
    _ordinary(root)
    files = {}
    for parent, directories, names in os.walk(root, followlinks=False):
        for name in directories:
            if not stat.S_ISDIR(_ordinary(Path(parent) / name).st_mode):
                raise ValueError('原采集目录结构无法确认')
        for name in sorted(names):
            path = Path(parent) / name
            info = _ordinary(path)
            if not stat.S_ISREG(info.st_mode):
                raise ValueError('原采集文件结构无法确认')
            files[path.relative_to(root).as_posix()] = {'bytes': info.st_size, 'sha256': file_digest(path)}
    serialized = json.dumps(files, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return {'files': files, 'sha256': hashlib.sha256(serialized).hexdigest()}


def copy_resume_source(source, destination, manifest):
    shutil.copytree(source, destination)
    if source_manifest(source) != manifest or source_manifest(destination) != manifest:
        raise ValueError('复制期间原采集文件发生变化，已暂停；不会发送问题')
