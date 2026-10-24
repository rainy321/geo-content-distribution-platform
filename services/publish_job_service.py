from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from services.article_service import ArticleNotFoundError
from services.platform_capability_service import (
    PLATFORM_BY_KEY,
    SUPPORTED_PUBLISH_PLATFORMS,
)
from services.publish_policy_service import assert_can_reserve_publish
from services.publish_retry_evidence import record_publish_submission_state
from services.publish_confirmation import article_review_fingerprint, publish_confirmation_snapshot


PUBLISH_JOB_STATUSES = frozenset(
    {"queued", "processing", "success", "failed", "need_action", "scheduled"}
)
_ALLOWED_TRANSITIONS = {
    "scheduled": frozenset({"queued", "failed"}),
    "queued": frozenset({"processing", "failed", "need_action"}),
    "processing": frozenset({"success", "failed", "need_action"}),
    "need_action": frozenset(),
    "failed": frozenset(),
    "success": frozenset(),
}
_RETRYABLE_STATUSES = frozenset({"failed", "need_action"})
PUBLISH_AUTHORIZATION_MISMATCH_MESSAGE = (
    "发布执行授权后，文章内容、审稿状态或分发参数已变化，或缺少有效确认快照；"
    "未访问平台，请重新审稿并确认"
)


class PublishJobNotFoundError(LookupError):
    """Raised when a publish job id does not exist."""


class UnsupportedPublishPlatformError(ValueError):
    """Raised when no publisher is planned for a platform."""


class InvalidPublishJobTransitionError(RuntimeError):
    """Raised when a job status change would break the workflow."""


class PublishReviewRequiredError(InvalidPublishJobTransitionError):
    """A real task cannot be claimed without its unchanged reviewed payload."""


class PublishConfirmationMismatchError(InvalidPublishJobTransitionError):
    """The user's confirmed view no longer identifies the current payload."""


def _confirmation_matches(expected, actual):
    return (isinstance(expected, str) and expected.isascii() and len(expected) == 64
            and hmac.compare_digest(expected, actual))


def create_publish_job(
    database_path: str | Path,
    *,
    article_id: int,
    platform: str,
    images: list[str] | tuple[str, ...] | None = None,
    video: str | None = None,
    publish_at: str | datetime | None = None,
    auto_execute: bool = False,
    demo: bool = False,
    account_id: int | None = None,
    require_account: bool = False,
    enforce_publish_policy: bool = False,
    expected_review_fingerprint: str | None = None,
) -> dict[str, Any]:
    if not isinstance(auto_execute, bool):
        raise ValueError("auto_execute 必须是布尔值")
    normalized_platform = _normalize_platform(platform)
    normalized_images = normalize_publish_images(images)
    normalized_video = normalize_publish_video(video)
    normalized_publish_at = _normalize_publish_at(publish_at)
    if auto_execute and normalized_publish_at is None:
        raise ValueError("auto_execute 仅适用于定时发布任务")
    normalized_auto_execute = bool(auto_execute and not demo)
    initial_status = "scheduled" if normalized_publish_at else "queued"

    with closing(_connect(database_path)) as conn:
        with conn:
            conn.execute('BEGIN IMMEDIATE')
            article_row = _fetch_authorization_article(conn, article_id, platform=normalized_platform)
            if article_row is None:
                raise ArticleNotFoundError("文章不存在")
            if (expected_review_fingerprint is not None and not _confirmation_matches(
                    expected_review_fingerprint, article_review_fingerprint(article_row))):
                raise PublishConfirmationMismatchError("稿件内容或审稿状态已变化，请重新查看并确认；未创建发布任务")
            if not demo and article_row['status'] != 'ready':
                raise ValueError('真实发布前请先审稿，并把文章标记为就绪；已发布文章需重新确认后另建任务')
            resolved_account_id = _resolve_publish_account_id(
                conn,
                normalized_platform,
                requested_account_id=account_id,
                demo=demo,
            )
            if require_account and not demo and resolved_account_id is None:
                raise ValueError("真实发布任务必须绑定一个已连接的对应平台账号")
            if not demo:
                _assert_target_publish_policy(conn, article_row, normalized_platform, resolved_account_id,
                                              publish_at=normalized_publish_at)
            if enforce_publish_policy and not demo and resolved_account_id is not None:
                assert_can_reserve_publish(
                    conn,
                    account_id=resolved_account_id,
                    platform=normalized_platform,
                    publish_at=normalized_publish_at,
                )
            authorization_fingerprint = (
                build_publish_authorization_fingerprint(
                    article=dict(article_row),
                    platform=normalized_platform,
                    images=normalized_images,
                    video=normalized_video,
                    publish_at=normalized_publish_at,
                    account_id=resolved_account_id,
                )
                if not demo
                else ""
            )
            cursor = conn.execute(
                """
                INSERT INTO publish_jobs (
                    article_id, account_id, platform, status, images, video, publish_at,
                    authorization_fingerprint, auto_execute, demo
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    article_id,
                    resolved_account_id,
                    normalized_platform,
                    initial_status,
                    json.dumps(normalized_images, ensure_ascii=False),
                    normalized_video,
                    normalized_publish_at,
                    authorization_fingerprint,
                    int(normalized_auto_execute),
                    int(bool(demo)),
                ),
            )
            row = _fetch_job(conn, cursor.lastrowid)
    return _serialize_job(row)


def build_publish_authorization_fingerprint(
    *,
    article: dict[str, Any],
    platform: str,
    images: list[str] | tuple[str, ...] | None,
    video: str | None = None,
    publish_at: str | datetime | None,
    account_id: int | None = None,
) -> str:
    """Bind every real task to the exact reviewed publishable payload."""

    canonical_payload = {
        "schema": 3,
        "article_id": int(article["id"]),
        "title": str(article.get("title") or "").strip(),
        "content": str(article.get("content") or "").strip(),
        "tags": _normalize_authorization_tags(article.get("tags")),
        "platform": _normalize_platform(platform),
        "images": normalize_publish_images(images),
        "video": normalize_publish_video(video),
        "publish_at": _normalize_publish_at(publish_at),
        "account_id": _normalize_optional_account_id(account_id),
    }
    if article.get('_destination_authorization') is not None:
        canonical_payload['schema'] = 4
        canonical_payload['destination'] = article['_destination_authorization']
    encoded = json.dumps(
        canonical_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def publish_authorization_matches(
    job: dict[str, Any],
    article: dict[str, Any],
    *, connection: sqlite3.Connection | None = None,
) -> bool:
    expected = str(job.get("authorization_fingerprint") or "").strip().lower()
    if not expected:
        return False
    if connection is not None:
        from services.geo_target_publish_guard import article_target_context
        try:
            target = article_target_context(connection, article['id'], platform=job['platform'], require_builtin=True)
        except ValueError:
            return False
        article = dict(article)
        if target:
            article['_destination_authorization'] = target['authorization']
    actual = build_publish_authorization_fingerprint(
        article=article,
        platform=job["platform"],
        images=job.get("images"),
        video=job.get("video"),
        publish_at=job.get("publish_at"),
        account_id=job.get("account_id"),
    )
    return hmac.compare_digest(expected, actual)


def get_publish_job(
    database_path: str | Path,
    job_id: int,
) -> dict[str, Any]:
    with closing(_connect(database_path)) as conn:
        row = _fetch_job(conn, job_id)
    if row is None:
        raise PublishJobNotFoundError("发布任务不存在")
    return _serialize_job(row)


def _new_attempt_time(previous: str | None) -> str:
    """Give a real processing transition a distinct, UTC attempt binding."""
    current = datetime.now(timezone.utc)
    if previous:
        try:
            prior = datetime.fromisoformat(str(previous).strip().replace("Z", "+00:00"))
            if prior.tzinfo is None:
                prior = prior.replace(tzinfo=timezone.utc)
            prior = prior.astimezone(timezone.utc)
        except (ValueError, TypeError) as exc:
            raise InvalidPublishJobTransitionError("上次发布尝试时间无法核对，未开始新的发布") from exc
        if prior > current:
            raise InvalidPublishJobTransitionError("系统时间早于上次发布尝试，请校准时间后重试；未开始新的发布")
        if prior == current:
            current += timedelta(microseconds=1)
    return current.isoformat(timespec="microseconds")


def claim_publish_job(
    database_path: str | Path,
    job_id: int,
    *,
    message: str = "正在准备发布",
    expected_confirmation_snapshot: str | None = None,
) -> dict[str, Any]:
    """Atomically move one queued job to processing for a single worker."""

    with closing(_connect(database_path)) as conn:
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            current_row = _fetch_job(conn, job_id)
            if current_row is None:
                raise PublishJobNotFoundError("发布任务不存在")
            if (expected_confirmation_snapshot is not None and not _confirmation_matches(
                    expected_confirmation_snapshot, _serialize_job(current_row)["confirmation_snapshot"])):
                raise PublishConfirmationMismatchError("发布任务已变化，请重新查看并确认；未开始新的发布")
            if current_row["status"] != "queued":
                raise InvalidPublishJobTransitionError(
                    f"状态为 {current_row['status']} 的发布任务不能开始执行"
                )
            if not bool(current_row["demo"]) and current_row["account_id"] is not None:
                account_busy = conn.execute(
                    """
                    SELECT 1
                    FROM publish_jobs
                    WHERE account_id = ? AND platform = ? AND status = 'processing'
                      AND id <> ?
                    LIMIT 1
                    """,
                    (
                        current_row["account_id"],
                        current_row["platform"],
                        job_id,
                    ),
                ).fetchone()
                if account_busy is not None:
                    raise InvalidPublishJobTransitionError(
                        "同一账号在该平台已有发布任务处理中，请等待完成后再试"
                    )
            if not bool(current_row['demo']):
                try:
                    article = _fetch_authorization_article(conn, current_row['article_id'], platform=current_row['platform'])
                    reviewed = (article is not None and article['status'] in ('ready', 'publishing', 'published')
                                and publish_authorization_matches(_serialize_job(current_row), dict(article)))
                    if reviewed:
                        _assert_target_publish_policy(conn, article, current_row['platform'], current_row['account_id'],
                                                      exclude_job_id=job_id)
                except (ValueError, TypeError) as error:
                    raise PublishReviewRequiredError(str(error)) from error
                if not reviewed:
                    raise PublishReviewRequiredError(PUBLISH_AUTHORIZATION_MISMATCH_MESSAGE)
            cursor = conn.execute(
                """
                UPDATE publish_jobs
                SET status = 'processing', message = ?, result_url = '',
                    started_at = ?,
                    finished_at = NULL
                WHERE id = ? AND status = 'queued'
                """,
                (_normalize_text(message), _new_attempt_time(current_row["started_at"]), job_id),
            )
            if cursor.rowcount != 1:
                raise InvalidPublishJobTransitionError("发布任务已被其他执行器领取")
            if not bool(current_row['demo']) and article.get('_distribution_target'):
                from services.geo_target_publish_guard import record_builtin_attempt
                record_builtin_attempt(conn, article['_distribution_target'], job_id, current_row['article_id'])
            _sync_article_status(conn, current_row["article_id"])
            updated_row = _fetch_job(conn, job_id)
    return _serialize_job(updated_row)


def list_publish_jobs(
    database_path: str | Path,
    *,
    article_id: int | None = None,
    platform: str | None = None,
    status: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    if page <= 0 or page_size <= 0:
        raise ValueError("分页参数必须是正整数")
    conditions: list[str] = []
    parameters: list[Any] = []
    if article_id is not None:
        conditions.append("j.article_id = ?")
        parameters.append(article_id)
    if platform is not None:
        conditions.append("j.platform = ?")
        parameters.append(_normalize_platform(platform))
    if status is not None:
        _validate_status(status)
        conditions.append("j.status = ?")
        parameters.append(status)

    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    offset = (page - 1) * page_size
    with closing(_connect(database_path)) as conn:
        total = conn.execute(
            f"SELECT COUNT(*) FROM publish_jobs AS j {where_clause}",
            parameters,
        ).fetchone()[0]
        rows = conn.execute(
            f"""
            SELECT j.*, a.title AS article_title,
                   u.userName AS account_name
            FROM publish_jobs AS j
            JOIN articles AS a ON a.id = j.article_id
            LEFT JOIN user_info AS u ON u.id = j.account_id
            {where_clause}
            ORDER BY j.created_at DESC, j.id DESC
            LIMIT ? OFFSET ?
            """,
            [*parameters, page_size, offset],
        ).fetchall()
    return {
        "items": [_serialize_job(row) for row in rows],
        "pagination": {
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": (total + page_size - 1) // page_size,
        },
    }


def update_publish_job_progress(
    database_path: str | Path,
    job_id: int,
    *,
    message: str,
    result_url: str = "",
) -> dict[str, Any]:
    """Persist a non-terminal result while a publisher awaits confirmation."""

    with closing(_connect(database_path)) as conn:
        with conn:
            current_row = _fetch_job(conn, job_id)
            if current_row is None:
                raise PublishJobNotFoundError("发布任务不存在")
            if current_row["status"] != "processing":
                raise InvalidPublishJobTransitionError(
                    f"状态为 {current_row['status']} 的发布任务不能更新进度"
                )
            conn.execute(
                """
                UPDATE publish_jobs
                SET message = ?, result_url = ?
                WHERE id = ?
                """,
                (
                    _normalize_text(message),
                    _normalize_text(result_url),
                    job_id,
                ),
            )
            updated_row = _fetch_job(conn, job_id)
    return _serialize_job(updated_row)


def transition_publish_job(
    database_path: str | Path,
    job_id: int,
    new_status: str,
    *,
    message: str = "",
    result_url: str = "",
) -> dict[str, Any]:
    _validate_status(new_status)
    with closing(_connect(database_path)) as conn:
        with conn:
            current_row = _fetch_job(conn, job_id)
            if current_row is None:
                raise PublishJobNotFoundError("发布任务不存在")
            current_status = current_row["status"]
            if new_status == current_status:
                _sync_article_status(conn, current_row["article_id"])
                return _serialize_job(current_row)
            if new_status not in _ALLOWED_TRANSITIONS[current_status]:
                raise InvalidPublishJobTransitionError(
                    f"发布任务不能从 {current_status} 变更为 {new_status}"
                )

            if new_status == "processing":
                conn.execute(
                    """
                    UPDATE publish_jobs
                    SET status = ?, message = ?, result_url = '',
                        started_at = ?,
                        finished_at = NULL
                    WHERE id = ?
                    """,
                    (new_status, _normalize_text(message), _new_attempt_time(current_row["started_at"]), job_id),
                )
            elif new_status in {"success", "failed"}:
                conn.execute(
                    """
                    UPDATE publish_jobs
                    SET status = ?, message = ?, result_url = ?,
                        finished_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (
                        new_status,
                        _normalize_text(message),
                        _normalize_text(result_url),
                        job_id,
                    ),
                )
            else:
                conn.execute(
                    """
                    UPDATE publish_jobs
                    SET status = ?, message = ?, result_url = ?
                    WHERE id = ?
                    """,
                    (
                        new_status,
                        _normalize_text(message),
                        _normalize_text(result_url),
                        job_id,
                    ),
                )
            _sync_article_status(conn, current_row["article_id"])
            updated_row = _fetch_job(conn, job_id)
    return _serialize_job(updated_row)


def retry_publish_job(
    database_path: str | Path,
    job_id: int,
) -> dict[str, Any]:
    with closing(_connect(database_path)) as conn:
        with conn:
            conn.execute('BEGIN IMMEDIATE')
            current_row = _fetch_job(conn, job_id)
            if current_row is None:
                raise PublishJobNotFoundError("发布任务不存在")
            if current_row["status"] not in _RETRYABLE_STATUSES:
                raise InvalidPublishJobTransitionError(
                    f"状态为 {current_row['status']} 的发布任务不能重试"
                )
            authorization = ''
            if not current_row['demo']:
                try:
                    article = _fetch_authorization_article(conn, current_row['article_id'], platform=current_row['platform'])
                    if article:
                        if article['status'] != 'ready':
                            raise ValueError('重试真实发布前请重新审稿，并把文章标记为就绪')
                        if (article.get('_distribution_target') and
                                publish_authorization_matches(_serialize_job(current_row), article)):
                            from services.publish_retry_evidence import archive_zero_write_retry
                            archive_zero_write_retry(conn, current_row, article['_distribution_target'])
                        _assert_target_publish_policy(conn, article, current_row['platform'], current_row['account_id'], exclude_job_id=job_id)
                except ValueError as error:
                    raise InvalidPublishJobTransitionError(str(error)) from error
                if article is None or article['status'] != 'ready':
                    raise InvalidPublishJobTransitionError('重试真实发布前请重新审稿，并把文章标记为就绪')
                authorization = build_publish_authorization_fingerprint(
                    article=dict(article), platform=current_row['platform'],
                    images=json.loads(current_row['images'] or '[]'), video=current_row['video'],
                    publish_at=current_row['publish_at'], account_id=current_row['account_id'])
            conn.execute(
                """
                UPDATE publish_jobs
                SET status = 'queued', message = '', result_url = '',
                    authorization_fingerprint = ?, auto_execute = 0,
                    finished_at = NULL
                WHERE id = ?
                """,
                (authorization, job_id),
            )
            # Requeueing is not an execution. Keep the last actual attempt's
            # timestamp for quotas and evidence until a worker claims again.
            _sync_article_status(conn, current_row["article_id"])
            updated_row = _fetch_job(conn, job_id)
    return _serialize_job(updated_row)


def reconcile_publish_job_success(
    database_path: str | Path,
    job_id: int,
    *,
    result_url: str,
    message: str = "平台侧已核验发布成功",
    expected_binding: dict[str, Any] | None = None,
    platform_evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Correct an ambiguous/failed local result using explicit platform proof."""

    normalized_url = _normalize_text(result_url)
    if not normalized_url.startswith(("https://", "http://")):
        raise ValueError("平台对账成功必须提供 http(s) 文章链接")
    with closing(_connect(database_path)) as conn:
        with conn:
            conn.execute('BEGIN IMMEDIATE')
            current_row = _fetch_job(conn, job_id)
            if current_row is None:
                raise PublishJobNotFoundError("发布任务不存在")
            if expected_binding is not None:
                fields = ('article_id','account_id','started_at','authorization_fingerprint')
                if (set(expected_binding) != set(fields) or current_row['demo']
                        or current_row['status'] != 'processing'
                        or any(current_row[field] != expected_binding[field] for field in fields)):
                    raise InvalidPublishJobTransitionError('核对期间任务绑定已变化，原证据未更新到当前任务')
                article = _fetch_authorization_article(conn,current_row['article_id'],platform=current_row['platform'])
                if not article or article['status']=='draft' or not publish_authorization_matches(_serialize_job(current_row),article):
                    raise InvalidPublishJobTransitionError('核对期间已授权内容或目标已变化，未更改发布结果')
            if current_row["status"] == "success":
                return _serialize_job(current_row)
            if current_row["status"] not in {"processing", "failed", "need_action"}:
                raise InvalidPublishJobTransitionError(
                    f"状态为 {current_row['status']} 的发布任务不能通过平台证据对账"
                )
            conn.execute(
                """
                UPDATE publish_jobs
                SET status = 'success', message = ?, result_url = ?,
                    finished_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (_normalize_text(message), normalized_url, job_id),
            )
            if platform_evidence is not None:
                from datetime import timezone
                if expected_binding is None or current_row['platform'] != 'toutiao':
                    raise ValueError('平台公开时间需绑定已核验的真实头条任务')
                stamp = datetime.fromisoformat(platform_evidence['published_at'].replace('Z','+00:00'))
                if stamp.tzinfo is None or platform_evidence.get('precision') != 'minute':
                    raise ValueError('平台公开时间必须包含时区与可见分钟精度')
                conn.execute('''UPDATE publish_jobs SET platform_published_at=?,platform_published_precision=?,
                    result_checked_at=?,result_check_evidence=? WHERE id=?''',
                    (stamp.isoformat(),'minute',datetime.now(timezone.utc).isoformat(),
                     json.dumps(platform_evidence,ensure_ascii=False),job_id))
            _sync_article_status(conn, current_row["article_id"])
            updated_row = _fetch_job(conn, job_id)
    return _serialize_job(updated_row)


def reconcile_publish_job_platform_success(
    database_path: str | Path,
    job_id: int,
    *,
    message: str,
    result_url: str = "",
) -> dict[str, Any]:
    """Close an ambiguous job using platform-side proof when no public URL exists."""

    normalized_message = _normalize_text(message)
    if not normalized_message:
        raise ValueError("平台后台对账成功必须提供核验说明")
    normalized_url = _normalize_text(result_url)
    if normalized_url and not normalized_url.startswith(("https://", "http://")):
        raise ValueError("平台后台对账链接必须是 http(s) 地址")

    with closing(_connect(database_path)) as conn:
        with conn:
            current_row = _fetch_job(conn, job_id)
            if current_row is None:
                raise PublishJobNotFoundError("发布任务不存在")
            if current_row["status"] == "success":
                return _serialize_job(current_row)
            if current_row["status"] not in {"processing", "failed", "need_action"}:
                raise InvalidPublishJobTransitionError(
                    f"状态为 {current_row['status']} 的发布任务不能通过平台后台证据对账"
                )
            conn.execute(
                """
                UPDATE publish_jobs
                SET status = 'success', message = ?, result_url = ?,
                    finished_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (normalized_message, normalized_url, job_id),
            )
            _sync_article_status(conn, current_row["article_id"])
            updated_row = _fetch_job(conn, job_id)
    return _serialize_job(updated_row)


def reconcile_publish_job_failure(
    database_path: str | Path,
    job_id: int,
    *,
    message: str,
) -> dict[str, Any]:
    """Close an ambiguous job when read-only platform evidence proves no submit."""

    normalized_message = _normalize_text(message)
    if not normalized_message:
        raise ValueError("平台失败对账必须提供核验说明")
    with closing(_connect(database_path)) as conn:
        with conn:
            current_row = _fetch_job(conn, job_id)
            if current_row is None:
                raise PublishJobNotFoundError("发布任务不存在")
            if current_row["status"] == "failed":
                return _serialize_job(current_row)
            if current_row["status"] not in {"processing", "need_action"}:
                raise InvalidPublishJobTransitionError(
                    f"状态为 {current_row['status']} 的发布任务不能通过平台证据结案"
                )
            conn.execute(
                """
                UPDATE publish_jobs
                SET status = 'failed', message = ?, result_url = '',
                    finished_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (normalized_message, job_id),
            )
            _sync_article_status(conn, current_row["article_id"])
            updated_row = _fetch_job(conn, job_id)
    return _serialize_job(updated_row)


def _connect(database_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(Path(database_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _fetch_authorization_article(conn: sqlite3.Connection, article_id: int, *, platform=None):
    from services.geo_target_publish_guard import article_target_context
    row = conn.execute(
        "SELECT id, project_id, title, content, tags, status FROM articles WHERE id = ?",
        (article_id,),
    ).fetchone()
    if row is None:
        return None
    article = dict(row)
    target = article_target_context(conn, article_id, platform=platform, require_builtin=True)
    if target:
        article['_distribution_target'] = target
        article['_destination_authorization'] = target['authorization']
    return article


def _assert_target_publish_policy(conn, article, platform, account_id, *, exclude_job_id=None,
                                  exclude_attempt_job_id=None, publish_at=None):
    target = article.get('_distribution_target')
    if not target:
        return
    from services.geo_target_publish_guard import assert_target_frequency
    if account_id is None:
        raise ValueError('目标稿接入内置发布器必须绑定已连接的对应平台账号')
    _resolve_publish_account_id(conn, platform, requested_account_id=account_id, demo=False)
    assert_can_reserve_publish(conn, account_id=account_id, platform=platform,
                              publish_at=publish_at, exclude_job_id=exclude_job_id)
    assert_target_frequency(conn, target, exclude_job_id=exclude_job_id,
                            exclude_attempt_job_id=exclude_attempt_job_id, publish_at=publish_at)


def validate_publish_job_for_execution(database, job, article):
    """Recheck current lineage and both policies immediately before an adapter."""
    with closing(_connect(database)) as conn:
        try:
            current = _fetch_authorization_article(conn, job['article_id'], platform=job['platform'])
            if (not current or current['status'] == 'draft'
                    or not publish_authorization_matches(job, current)
                    or not publish_authorization_matches(job, article, connection=conn)):
                raise ValueError(PUBLISH_AUTHORIZATION_MISMATCH_MESSAGE)
            _assert_target_publish_policy(conn, current, job['platform'], job['account_id'],
                                          exclude_job_id=job['id'], exclude_attempt_job_id=job['id'])
        except (ValueError, TypeError) as error:
            raise PublishReviewRequiredError(str(error)) from error


def _normalize_authorization_tags(value: Any) -> list[str]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            value = []
    if not isinstance(value, (list, tuple)):
        return []
    return [
        normalized
        for item in value
        if (normalized := str(item or "").strip())
    ]


def _fetch_job(conn: sqlite3.Connection, job_id: int):
    return conn.execute(
        """
        SELECT j.*, a.title AS article_title,
               u.userName AS account_name
        FROM publish_jobs AS j
        JOIN articles AS a ON a.id = j.article_id
        LEFT JOIN user_info AS u ON u.id = j.account_id
        WHERE j.id = ?
        """,
        (job_id,),
    ).fetchone()


def _sync_article_status(conn: sqlite3.Connection, article_id: int) -> str | None:
    article_row = conn.execute(
        "SELECT status FROM articles WHERE id = ?",
        (article_id,),
    ).fetchone()
    if article_row is None:
        return None
    current_status = article_row["status"]
    job_statuses = {
        row["status"]
        for row in conn.execute(
            "SELECT status FROM publish_jobs WHERE article_id = ?",
            (article_id,),
        ).fetchall()
    }
    # A real task could only have been created from a reviewed ready article.
    # A later draft status is an explicit withdrawal of that review, including
    # after another task succeeded. Do not revive permission while syncing the
    # failed/blocked task. Demo-only workflows retain their historical behavior.
    withdrawn_real_review = current_status == 'draft' and conn.execute(
        'SELECT 1 FROM publish_jobs WHERE article_id=? AND demo=0 LIMIT 1', (article_id,)).fetchone() is not None
    if withdrawn_real_review:
        desired_status = 'draft'
    elif current_status == "published" or "success" in job_statuses:
        desired_status = "published"
    elif "processing" in job_statuses:
        desired_status = "publishing"
    elif current_status == "publishing":
        desired_status = "ready"
    else:
        desired_status = current_status
    if desired_status != current_status:
        conn.execute(
            """
            UPDATE articles
            SET status = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (desired_status, article_id),
        )
    return desired_status


def _serialize_job(row: sqlite3.Row) -> dict[str, Any]:
    job = dict(row)
    job["demo"] = bool(job["demo"])
    job["auto_execute"] = bool(job.get("auto_execute", False))
    job["authorization_bound"] = bool(
        str(job.get("authorization_fingerprint") or "").strip()
    )
    try:
        raw_images = json.loads(job.get("images") or "[]")
        job["images"] = normalize_publish_images(raw_images)
    except (TypeError, json.JSONDecodeError, ValueError):
        job["images"] = []
    job["confirmation_snapshot"] = publish_confirmation_snapshot(job)
    return job


def normalize_publish_images(
    images: list[str] | tuple[str, ...] | None,
) -> list[str]:
    """Normalize portable media filenames stored with a publish job."""

    if images is None:
        return []
    if not isinstance(images, (list, tuple)):
        raise ValueError("images 必须是字符串列表")
    if len(images) > 9:
        raise ValueError("单个发布任务最多支持 9 张图片")

    normalized: list[str] = []
    seen: set[str] = set()
    for image in images:
        if not isinstance(image, str):
            raise ValueError("images 必须是字符串列表")
        filename = image.strip()
        if not filename:
            raise ValueError("图片文件名不能为空")
        if Path(filename).name != filename or "/" in filename or "\\" in filename:
            raise ValueError("图片只能引用素材库中的文件名")
        if filename not in seen:
            normalized.append(filename)
            seen.add(filename)
    return normalized


def normalize_publish_video(video: str | None) -> str:
    """Normalize one portable video filename stored with a publish job."""

    if video is None:
        return ""
    if not isinstance(video, str):
        raise ValueError("video 必须是字符串")
    filename = video.strip()
    if not filename:
        return ""
    if Path(filename).name != filename or "/" in filename or "\\" in filename:
        raise ValueError("视频只能引用素材库中的文件名")
    return filename


def _normalize_optional_account_id(account_id: Any) -> int | None:
    if account_id is None or account_id == "":
        return None
    if isinstance(account_id, bool):
        raise ValueError("account_id 必须是正整数")
    try:
        normalized = int(account_id)
    except (TypeError, ValueError) as exc:
        raise ValueError("account_id 必须是正整数") from exc
    if normalized <= 0:
        raise ValueError("account_id 必须是正整数")
    return normalized


def _resolve_publish_account_id(
    conn: sqlite3.Connection,
    platform: str,
    *,
    requested_account_id: int | None,
    demo: bool,
) -> int | None:
    normalized_requested = _normalize_optional_account_id(requested_account_id)
    if demo and normalized_requested is None:
        return None

    account_type = int(PLATFORM_BY_KEY[platform]["account_type"])
    if normalized_requested is not None:
        row = conn.execute(
            """
            SELECT id
            FROM user_info
            WHERE id = ? AND type = ? AND status = 1
            """,
            (normalized_requested, account_type),
        ).fetchone()
        if row is None:
            raise ValueError("指定账号不存在、平台不匹配或当前不可用")
        return int(row["id"])

    row = conn.execute(
        """
        SELECT id
        FROM user_info
        WHERE type = ? AND status = 1
        ORDER BY COALESCE(last_checked_at, '') DESC, id DESC
        LIMIT 1
        """,
        (account_type,),
    ).fetchone()
    return int(row["id"]) if row is not None else None


def _normalize_platform(platform: str) -> str:
    normalized = str(platform or "").strip().lower()
    if normalized not in SUPPORTED_PUBLISH_PLATFORMS:
        raise UnsupportedPublishPlatformError("暂不支持该发布平台")
    return normalized


def _validate_status(status: str) -> None:
    if status not in PUBLISH_JOB_STATUSES:
        raise ValueError("不支持的发布任务状态")


def _normalize_publish_at(value: str | datetime | None) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        raw_value = value.strip()
        if not raw_value:
            return None
        try:
            parsed = datetime.fromisoformat(raw_value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("publish_at 必须是 ISO 8601 时间") from exc
    else:
        raise ValueError("publish_at 必须是 ISO 8601 时间")
    return parsed.isoformat(sep=" ", timespec="seconds")


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()
