"""Reuse one completed local editorial reply; never query or publish anything."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat

from services import geo_content_research as research, geo_content_store as store

TERMINAL = {'failed', 'need_action', 'needs_review', 'stopped', 'completed'}
MAX_EVIDENCE_BYTES = 12 * 1024 * 1024


def _identifier(value):
    return isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,100}', value) is not None


def _read(data, path, maximum):
    data, path = Path(os.path.abspath(data)), Path(os.path.abspath(path))
    try:
        path.relative_to(data)
    except ValueError:
        raise ValueError('生成记录不在本机受控目录内，未保存') from None
    parts = path.relative_to(data).parts
    for current in (data, *(data.joinpath(*parts[:index]) for index in range(1, len(parts) + 1))):
        row = current.lstat()
        if stat.S_ISLNK(row.st_mode) or getattr(row, 'st_file_attributes', 0) & 0x400:
            raise ValueError('生成记录包含链接，未读取或保存')
    before = path.stat()
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > maximum:
        raise ValueError('生成记录不是可读取的受控普通文件')
    raw = path.read_bytes()
    after = path.stat()
    if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
        raise ValueError('生成记录正在变化，请稍后检查原任务')
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError):
        raise ValueError('生成记录不完整，原文保留，未重新提问') from None
    if not isinstance(value, dict):
        raise ValueError('生成记录格式无效')
    return value, hashlib.sha256(raw).hexdigest()


def _prepare(data, task_id, cycle_id):
    if not _identifier(task_id) or not _identifier(cycle_id):
        raise ValueError('请选择本轮已结束的写稿任务')
    data = Path(os.path.abspath(data))
    task, task_sha = _read(data, data / 'desktop-jobs' / (task_id + '.json'), 1024 * 1024)
    if (task.get('id') != task_id or task.get('kind') != 'draft'
            or task.get('status') not in TERMINAL or task.get('cycle_id') != cycle_id):
        raise ValueError('只能读取本轮已结束的写稿任务，原任务没有重新发送')
    raw_path = task.get('evidence_path')
    if not isinstance(raw_path, str) or not Path(raw_path).is_absolute():
        raise ValueError('原任务未保存可核对的生成结果')
    path = Path(os.path.abspath(raw_path))
    evidence_root = data / 'geo-content/evidence'
    try:
        parts = path.relative_to(evidence_root).parts
    except ValueError:
        raise ValueError('原任务的生成结果不属于受控写稿目录') from None
    if (len(parts) != 3 or not re.fullmatch(r'[0-9a-f]{32}', parts[0])
            or not re.fullmatch(r'[0-9]{8}-[0-9]{6}-[0-9a-f]{8}', parts[1]) or parts[2] != 'result.json'):
        raise ValueError('原任务的生成结果路径无效')
    evidence, evidence_sha = _read(data, path, MAX_EVIDENCE_BYTES)
    if (evidence.get('collection_status') not in {'success', 'partial'}
            or evidence.get('question_submission_attempted') is not True
            or evidence.get('question_submitted') is not True or evidence.get('answer_completed') is not True
            or not isinstance(evidence.get('question'), str) or not isinstance(evidence.get('response'), str)):
        raise ValueError('原任务没有已确认提交的完整回答，不能保存为生成稿')
    cycle = store.get_cycle(data / 'database.db', cycle_id)
    briefs = (cycle.get('analysis') or {}).get('briefs') or []
    destinations = cycle.get('destinations') or []
    if len(briefs) > 6 or len(destinations) > 50:
        raise ValueError('选题或平台数量超出可核对范围')
    platforms = list(dict.fromkeys([*research.CHANNELS, *(item['key'] for item in destinations)]))
    matches = []
    for brief in briefs:
        for platform in platforms:
            try:
                prompt = research.draft_prompt(cycle, brief['id'], platform)
            except (ValueError, KeyError, TypeError):
                continue
            if prompt == evidence['question']:
                matches.append((brief['id'], platform))
    if len(matches) != 1:
        raise ValueError('当前事实、选题或平台与原生成资料不再唯一匹配；原文保留，未重新提问')
    brief_id, platform = matches[0]
    inputs = research.draft_input(cycle, brief_id, platform)
    draft = research.parse_draft(evidence['response'], inputs)
    # All bytes and current input/revision are bound to the visible preview.
    preview_sha = research.fingerprint({'task_sha256': task_sha, 'evidence_sha256': evidence_sha,
        'cycle_id': cycle_id, 'revision': cycle['revision'], 'brief_id': brief_id, 'platform': platform,
        'input_sha256': research.fingerprint(inputs), 'draft': draft})
    return {'cycle': cycle, 'evidence': evidence, 'path': path, 'draft': draft,
            'brief_id': brief_id, 'platform': platform, 'inputs': inputs, 'preview_sha256': preview_sha}


def preview(data, task_id, cycle_id):
    try:
        value = _prepare(data, task_id, cycle_id)
    except (ValueError, store.ResearchNotFoundError) as error:
        return {'available': False, 'message': str(error)}
    except OSError:
        return {'available': False, 'message': '原生成记录暂时无法读取，请保留原任务；不会重新提问。'}
    prior = store.existing_draft(Path(data) / 'database.db', value['cycle'], value['brief_id'], value['platform'])
    return {'available': True, 'preview_sha256': value['preview_sha256'], 'draft': value['draft'],
            'brief_id': value['brief_id'], 'platform': value['platform'],
            'already_saved': bool(prior), **({'article_id': prior['article_id']} if prior else {}),
            'message': '已读取原任务生成的稿件。核对后可直接存入内容库，不会重新提问或发布。'}


def recover(data, task_id, cycle_id, expected_preview_sha256):
    if not isinstance(expected_preview_sha256, str) or not re.fullmatch(r'[0-9a-f]{64}', expected_preview_sha256):
        raise ValueError('请先预览并核对原任务生成的稿件')
    value = _prepare(data, task_id, cycle_id)
    if value['preview_sha256'] != expected_preview_sha256:
        raise ValueError('预览后的生成记录或当前资料已变化，请重新核对预览；未保存或重新提问')
    cycle, evidence, path = value['cycle'], value['evidence'], value['path']
    provenance = {'method': 'doubao_web_ai', 'model_label': (evidence.get('environment') or {}).get('model_label'),
        'client_mode': (evidence.get('environment') or {}).get('client_mode'), 'evidence_path': str(path),
        'role': 'content_editing_not_exposure_test', 'prompt_sha256': research.fingerprint(evidence['question']),
        'input_sha256': research.fingerprint(value['inputs']), 'recovered_from_task_id': task_id,
        'response_sha256': hashlib.sha256(evidence['response'].encode('utf-8')).hexdigest(),
        'recovery_preview_sha256': expected_preview_sha256, 'reused_existing_response': True}
    result = store.save_draft(Path(data) / 'database.db', cycle_id, value['brief_id'], value['platform'],
                              evidence['response'], provenance, cycle['revision'])
    return {**result, 'cycle_id': cycle_id, 'reused_existing_response': True}
