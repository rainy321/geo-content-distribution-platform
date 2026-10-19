"""Append-only evidence for an explicitly retried, provably zero-write attempt.

This does not authorize publishing. Only the same reviewed job may discount its
own archived zero-write claim, and every new execution still needs confirmation.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path


LEGACY_BEFORE_FILL_MESSAGE = '发布页已有内容或自动保存草稿，已停止以免覆盖；请人工核对后重新发起'
_CLAIM = re.compile(r'^publish-job:([1-9][0-9]*)(?::attempt:[a-f0-9]{32})?$')


def claim_job_id(event):
    match = _CLAIM.fullmatch(event['id'])
    return int(match[1]) if match else None


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')


def _stamp(value):
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def _directory(connection):
    path = next(row[2] for row in connection.execute('PRAGMA database_list') if row[1] == 'main')
    if not path:
        raise ValueError('零写入恢复需要可持久保存的发布证据目录')
    return Path(path).resolve().parent / 'publish-evidence'


def latest_claim(connection, target_id, job_id):
    rows = connection.execute("SELECT * FROM geo_distribution_events WHERE target_id=? AND kind='builtin_publish' ORDER BY created_at DESC,id DESC", (target_id,)).fetchall()
    return next((dict(row) for row in rows if claim_job_id(row) == job_id), None)


def _is_zero_write(journal, job, claim, message):
    if (not isinstance(journal, dict) or journal.get('job_id') != job['id']
            or journal.get('platform') != job['platform'] or job['platform'] != 'toutiao'
            or journal.get('submission_phase') != 'stopped_before_submission'
            or journal.get('submission_attempted') is not False):
        return False
    if 'content_write_attempted' in journal:
        if journal['content_write_attempted'] is not False:
            return False
    elif message != LEGACY_BEFORE_FILL_MESSAGE:
        return False
    for field, expected in (('article_id', job['article_id']), ('account_id', job['account_id']),
                            ('authorization_fingerprint', job['authorization_fingerprint']),
                            ('started_at', job['started_at']), ('claim_event_id', claim['id'])):
        if field in journal and journal[field] != expected:
            return False
    try:
        stamp = _stamp(journal['updated_at'])
        return (_stamp(job['started_at']) <= stamp and _stamp(claim['created_at']) <= stamp
                and stamp <= datetime.now(timezone.utc) + timedelta(seconds=5))
    except (KeyError, AttributeError, ValueError, TypeError):
        return False


def archive_zero_write_retry(connection, job, target):
    """Called only inside a user's retry transaction, with unchanged review."""
    if job['demo'] or job['status'] not in ('need_action', 'failed') or job['result_url']:
        return False
    claim = latest_claim(connection, target['id'], job['id'])
    if not claim or claim['article_id'] != job['article_id'] or not job['started_at']:
        return False
    directory = _directory(connection)
    journal_path = directory / f'job-{job["id"]}.json'
    try:
        if journal_path.is_symlink() or journal_path.stat().st_size > 1024 * 1024:
            return False
        raw = journal_path.read_bytes()
        journal = json.loads(raw.decode('utf-8'))
    except (OSError, ValueError, UnicodeError):
        return False
    if not _is_zero_write(journal, job, claim, job['message']):
        return False
    digest = _digest(raw)
    relative = f'history/job-{job["id"]}/zero-{_digest(claim["id"].encode())[:16]}-{digest}.json'
    archived = directory / relative
    archived.parent.mkdir(parents=True, exist_ok=True)
    archived.resolve().relative_to(directory.resolve())
    try:
        with archived.open('xb') as stream:
            stream.write(raw)
    except FileExistsError:
        if archived.is_symlink() or archived.read_bytes() != raw:
            raise ValueError('原发布证据归档冲突，请先人工核对；未重新排队')
    metadata = {'schema': 1, 'job_id': job['id'], 'article_id': job['article_id'],
        'account_id': job['account_id'], 'platform': job['platform'],
        'event_id': claim['id'], 'claim_sha256': _digest(_canonical(claim)),
        'journal': relative, 'journal_sha256': digest,
        'authorization_fingerprint': job['authorization_fingerprint'],
        'started_at': job['started_at'], 'message': job['message']}
    # Append a review row. The original claim row, timestamp and message are
    # never rewritten, and an existing review cannot silently be replaced.
    identifier = 'zero-write-review:' + _digest(claim['id'].encode())
    prior = connection.execute('SELECT message FROM geo_distribution_events WHERE id=?', (identifier,)).fetchone()
    encoded = _canonical(metadata).decode('utf-8')
    if prior:
        if prior['message'] != encoded:
            raise ValueError('原零写入复核记录与当前证据不一致，请人工核对')
    else:
        connection.execute('''INSERT INTO geo_distribution_events(id,target_id,article_id,kind,state,message,created_at)
            VALUES(?,?,?,'builtin_retry_review','zero_write_verified',?,?)''',
            (identifier, target['id'], job['article_id'], encoded, datetime.now(timezone.utc).isoformat()))
    return True


def reviewed_zero_write_claims(connection, target_id, job_id):
    """Revalidate immutable evidence on retry, claim and immediately before I/O."""
    job = connection.execute('SELECT * FROM publish_jobs WHERE id=? AND demo=0', (job_id,)).fetchone()
    if not job:
        return set()
    directory = _directory(connection)
    result = set()
    rows = connection.execute("SELECT * FROM geo_distribution_events WHERE target_id=? AND kind='builtin_retry_review' AND state='zero_write_verified'", (target_id,)).fetchall()
    for row in rows:
        try:
            metadata = json.loads(row['message'])
            if (metadata['job_id'] != job_id or metadata['article_id'] != job['article_id']
                    or metadata['account_id'] != job['account_id'] or metadata['platform'] != job['platform']
                    or metadata['authorization_fingerprint'] != job['authorization_fingerprint']):
                continue
            claim = connection.execute("SELECT * FROM geo_distribution_events WHERE id=? AND target_id=? AND kind='builtin_publish'", (metadata['event_id'], target_id)).fetchone()
            if (not claim or claim_job_id(claim) != job_id or claim['article_id'] != job['article_id']
                    or _digest(_canonical(dict(claim))) != metadata['claim_sha256']):
                continue
            path = directory / metadata['journal']
            path.resolve().relative_to((directory / 'history' / f'job-{job_id}').resolve())
            if path.is_symlink() or path.stat().st_size > 1024 * 1024:
                continue
            raw = path.read_bytes()
            original_job = {**dict(job), 'started_at': metadata['started_at']}
            if (_digest(raw) == metadata['journal_sha256'] and
                    _is_zero_write(json.loads(raw.decode('utf-8')), original_job, dict(claim), metadata['message'])):
                result.add(claim['id'])
        except (KeyError, AttributeError, TypeError, OSError, ValueError, UnicodeError):
            continue
    return result


def record_publish_submission_state(database, job_id, platform, state):
    """Append every future phase, then atomically update the latest pointer."""
    with closing(sqlite3.connect(database)) as connection:
        connection.row_factory = sqlite3.Row
        job = connection.execute('SELECT * FROM publish_jobs WHERE id=?', (job_id,)).fetchone()
        if not job or job['demo'] or job['platform'] != platform or job['status'] != 'processing':
            raise ValueError('发布阶段记录必须绑定当前真实执行任务')
        directory = _directory(connection)
        payload = {**dict(state), 'job_id': job_id, 'platform': platform,
            'article_id': job['article_id'], 'account_id': job['account_id'],
            'authorization_fingerprint': job['authorization_fingerprint'],
            'started_at': job['started_at'], 'updated_at': datetime.now(timezone.utc).isoformat()}
        row = connection.execute("SELECT platform FROM geo_content_drafts WHERE article_id=?", (job['article_id'],)).fetchone()
        if row and row['platform'].startswith('target_'):
            claim = latest_claim(connection, row['platform'][len('target_'):], job_id)
            if claim:
                payload['claim_event_id'] = claim['id']
    data = json.dumps(payload, ensure_ascii=False, indent=2).encode('utf-8')
    history = directory / 'history' / f'job-{job_id}' / 'phases'
    history.mkdir(parents=True, exist_ok=True)
    history.resolve().relative_to(directory.resolve())
    identifier = uuid.uuid4().hex
    with (history / (identifier + '.json')).open('xb') as stream:
        stream.write(data)
    temporary = directory / f'.job-{job_id}-{identifier}.tmp'
    with temporary.open('xb') as stream:
        stream.write(data)
    temporary.replace(directory / f'job-{job_id}.json')
