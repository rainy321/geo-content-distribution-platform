from __future__ import annotations

import hashlib
import hmac
import json
import re
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from services.article_service import ArticleNotFoundError
from services.platform_capability_service import (
    PLATFORM_BY_KEY,
    SUPPORTED_PUBLISH_PLATFORMS,
)
from services.publish_preflight_service import (
    assert_platform_publish_variant_ready,
    build_platform_publish_variant,
)
from services.sohu_publication_guard_service import (
    SohuPublicationGuardError,
    evaluate_sohu_account_publication_guard_in_connection,
)


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
    "定时自动执行授权后，文章内容或分发参数已变化；未访问平台，请重新确认"
)

# Manual reconciliation links are durable evidence, not arbitrary destinations.
# Keep this allowlist intentionally narrower than each platform's general domain:
# creator centres, login pages, short-link redirectors and unrelated official
# pages must never be recorded as proof that a particular work is public.
_PUBLIC_WORK_URL_RULES = {
    "zhihu": (
        ("zhuanlan.zhihu.com", re.compile(r"/p/[1-9]\d*/?")),
        # Kept for existing, working Zhihu article links used by the product.
        ("www.zhihu.com", re.compile(r"/p/[1-9]\d*/?")),
    ),
    "toutiao": (
        (
            "www.toutiao.com",
            re.compile(r"/(?:article|item)/[1-9]\d*/?"),
        ),
        ("toutiao.com", re.compile(r"/(?:article|item)/[1-9]\d*/?")),
    ),
    "baijiahao": (
        ("baijiahao.baidu.com", re.compile(r"/s/?")),
    ),
    "sohu": (
        ("www.sohu.com", re.compile(r"/a/[1-9]\d*_[1-9]\d*/?")),
        ("sohu.com", re.compile(r"/a/[1-9]\d*_[1-9]\d*/?")),
    ),
    "xiaohongshu": (
        (
            "www.xiaohongshu.com",
            re.compile(r"/(?:explore|discovery/item)/[0-9A-Fa-f]{24}/?"),
        ),
        (
            "xiaohongshu.com",
            re.compile(r"/(?:explore|discovery/item)/[0-9A-Fa-f]{24}/?"),
        ),
    ),
    "douyin": (
        ("www.douyin.com", re.compile(r"/(?:video|note)/[1-9]\d*/?")),
        ("douyin.com", re.compile(r"/(?:video|note)/[1-9]\d*/?")),
    ),
    "kuaishou": (
        (
            "www.kuaishou.com",
            re.compile(r"/short-video/[0-9A-Za-z_-]{6,128}/?"),
        ),
        (
            "kuaishou.com",
            re.compile(r"/short-video/[0-9A-Za-z_-]{6,128}/?"),
        ),
    ),
    "bilibili": (
        (
            "www.bilibili.com",
            re.compile(r"/video/(?:BV[0-9A-Za-z]{10}|av[1-9]\d*)/?"),
        ),
        (
            "bilibili.com",
            re.compile(r"/video/(?:BV[0-9A-Za-z]{10}|av[1-9]\d*)/?"),
        ),
    ),
    "tiktok": (
        (
            "www.tiktok.com",
            re.compile(
                r"/@[0-9A-Za-z_](?:[0-9A-Za-z._]{0,22}[0-9A-Za-z_])?"
                r"/video/[1-9]\d*/?"
            ),
        ),
    ),
    "weibo": (
        ("m.weibo.cn", re.compile(r"/detail/[1-9]\d*/?")),
        (
            "weibo.com",
            re.compile(r"/[1-9]\d*/[0-9A-Za-z]{5,32}/?"),
        ),
        (
            "www.weibo.com",
            re.compile(r"/[1-9]\d*/[0-9A-Za-z]{5,32}/?"),
        ),
    ),
}
_PLATFORMS_WITHOUT_STABLE_PUBLIC_WORK_URL = frozenset({"channels"})


class PublishJobNotFoundError(LookupError):
    """Raised when a publish job id does not exist."""


class UnsupportedPublishPlatformError(ValueError):
    """Raised when no publisher is planned for a platform."""


class InvalidPublishJobTransitionError(RuntimeError):
    """Raised when a job status change would break the workflow."""


class ConcurrentPublishJobUpdateError(InvalidPublishJobTransitionError):
    """Raised when a writer is using an obsolete publish-job snapshot."""


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
    publish_title: str | None = None,
    publish_content: str | None = None,
) -> dict[str, Any]:
    if not isinstance(auto_execute, bool):
        raise ValueError("auto_execute 必须是布尔值")
    normalized_article_id = _normalize_required_positive_integer(
        article_id,
        "article_id",
    )
    normalized_platform = _normalize_platform(platform)
    normalized_images = normalize_publish_images(images)
    normalized_video = normalize_publish_video(video)
    normalized_publish_at = _normalize_publish_at(publish_at)
    if auto_execute and normalized_publish_at is None:
        raise ValueError("auto_execute 仅适用于定时发布任务")
    normalized_auto_execute = bool(auto_execute and not demo)
    initial_status = "scheduled" if normalized_publish_at else "queued"

    with closing(_connect(database_path)) as conn:
        # Keep the Sohu guard read and job insert in one write transaction.
        # Otherwise two concurrent callers could both pass an empty-history
        # check before either one persisted its task.
        if normalized_platform == "sohu" and not demo:
            conn.execute("BEGIN IMMEDIATE")
        with conn:
            article_row = _fetch_authorization_article(conn, normalized_article_id)
            if article_row is None:
                raise ArticleNotFoundError("文章不存在")
            if article_row["status"] != "ready":
                raise ValueError("文章必须先标记为待发布，才能创建发布任务")
            variant = build_platform_publish_variant(
                platform=normalized_platform,
                title=article_row["title"],
                content=article_row["content"],
                tags=_normalize_authorization_tags(article_row["tags"]),
                publish_title=publish_title,
                publish_content=publish_content,
                required_phrases=(str(article_row["project_name"] or "").strip(),),
            )
            assert_platform_publish_variant_ready(variant)
            resolved_account_id = _resolve_publish_account_id(
                conn,
                normalized_platform,
                requested_account_id=account_id,
                demo=demo,
            )
            if normalized_platform == "sohu" and resolved_account_id is not None:
                publication_guard = evaluate_sohu_account_publication_guard_in_connection(
                    conn,
                    account_id=resolved_account_id,
                    article_id=int(article_row["id"]),
                    title=variant["title"],
                    content=variant["publish_content"],
                )
                if publication_guard["passed"] is not True:
                    raise SohuPublicationGuardError(publication_guard)
            authorization_fingerprint = (
                build_publish_authorization_fingerprint(
                    article=dict(article_row),
                    platform=normalized_platform,
                    images=normalized_images,
                    video=normalized_video,
                    publish_at=normalized_publish_at,
                    account_id=resolved_account_id,
                    publish_title=variant["title"],
                    publish_content=variant["publish_content"],
                    content_policy_version=variant["policy_version"],
                )
                if normalized_auto_execute
                else ""
            )
            cursor = conn.execute(
                """
                INSERT INTO publish_jobs (
                    article_id, account_id, platform, status, images, video,
                    publish_title, publish_content, content_policy_version,
                    publish_at, authorization_fingerprint, auto_execute, demo
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    normalized_article_id,
                    resolved_account_id,
                    normalized_platform,
                    initial_status,
                    json.dumps(normalized_images, ensure_ascii=False),
                    normalized_video,
                    variant["title"],
                    variant["publish_content"],
                    variant["policy_version"],
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
    publish_title: str | None = None,
    publish_content: str | None = None,
    content_policy_version: str | None = None,
) -> str:
    """Bind automatic execution to the exact publishable payload."""

    canonical_payload = {
        "schema": 4,
        "article_id": int(article["id"]),
        "title": str(article.get("title") or "").strip(),
        "content": str(article.get("content") or "").strip(),
        "tags": _normalize_authorization_tags(article.get("tags")),
        "platform": _normalize_platform(platform),
        "images": normalize_publish_images(images),
        "video": normalize_publish_video(video),
        "publish_at": _normalize_publish_at(publish_at),
        "account_id": _normalize_optional_account_id(account_id),
        "publish_title": (
            str(article.get("title") or "").strip()
            if publish_title is None
            else str(publish_title or "").strip()
        ),
        "publish_content": (
            str(article.get("content") or "").strip()
            if publish_content is None
            else str(publish_content or "").strip()
        ),
        "content_policy_version": str(content_policy_version or "").strip(),
    }
    encoded = json.dumps(
        canonical_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_publish_confirmation_fingerprint(
    *,
    job: dict[str, Any],
    article: dict[str, Any],
) -> str:
    """Bind one interactive confirmation to an exact job snapshot.

    This digest is intentionally not a long-lived credential.  It prevents a
    confirmation rendered for one article body, account, media selection or
    job state from being replayed after any of those values change.
    """

    job_id = job.get("id")
    state_version = job.get("state_version")
    if isinstance(job_id, bool) or not isinstance(job_id, int) or job_id <= 0:
        raise ValueError("job_id 必须是正整数")
    if (
        isinstance(state_version, bool)
        or not isinstance(state_version, int)
        or state_version < 0
    ):
        raise ValueError("state_version 必须是非负整数")

    publish_payload_fingerprint = build_publish_authorization_fingerprint(
        article=article,
        platform=job["platform"],
        images=job.get("images"),
        video=job.get("video"),
        publish_at=job.get("publish_at"),
        account_id=job.get("account_id"),
        publish_title=job.get("publish_title") or article.get("title"),
        publish_content=job.get("publish_content") or article.get("content"),
        content_policy_version=job.get("content_policy_version"),
    )
    encoded = json.dumps(
        {
            "schema": 1,
            "job_id": job_id,
            "state_version": state_version,
            "publish_payload_fingerprint": publish_payload_fingerprint,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def publish_authorization_matches(
    job: dict[str, Any],
    article: dict[str, Any],
) -> bool:
    expected = str(job.get("authorization_fingerprint") or "").strip().lower()
    if not expected:
        return False
    actual = build_publish_authorization_fingerprint(
        article=article,
        platform=job["platform"],
        images=job.get("images"),
        video=job.get("video"),
        publish_at=job.get("publish_at"),
        account_id=job.get("account_id"),
        publish_title=job.get("publish_title") or article.get("title"),
        publish_content=job.get("publish_content") or article.get("content"),
        content_policy_version=job.get("content_policy_version"),
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


def get_publish_job_with_confirmation_snapshot(
    database_path: str | Path,
    job_id: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Read a job and its confirmable article fields from one SQLite row."""

    with closing(_connect(database_path)) as conn:
        row = conn.execute(
            """
            SELECT j.*, a.title AS article_title,
                   u.userName AS account_name,
                   a.id AS confirmation_article_id,
                   a.title AS confirmation_title,
                   a.content AS confirmation_content,
                   a.tags AS confirmation_tags,
                   a.updated_at AS confirmation_updated_at
            FROM publish_jobs AS j
            JOIN articles AS a ON a.id = j.article_id
            LEFT JOIN user_info AS u ON u.id = j.account_id
            WHERE j.id = ?
            """,
            (job_id,),
        ).fetchone()
    if row is None:
        raise PublishJobNotFoundError("发布任务不存在")

    job = _serialize_job(row)
    article = {
        "id": int(job.pop("confirmation_article_id")),
        "title": str(job.pop("confirmation_title") or ""),
        "content": str(job.pop("confirmation_content") or ""),
        "tags": _normalize_authorization_tags(job.pop("confirmation_tags")),
        "updated_at": job.pop("confirmation_updated_at"),
    }
    return job, article


def claim_publish_job(
    database_path: str | Path,
    job_id: int,
    *,
    message: str = "正在准备发布",
    expected_state_version: int | None = None,
    include_account_credential_epoch: bool = False,
) -> dict[str, Any]:
    """Atomically move one queued job to processing for a single worker."""

    with closing(_connect(database_path)) as conn:
        with conn:
            current_row = _fetch_job(conn, job_id)
            if current_row is None:
                raise PublishJobNotFoundError("发布任务不存在")
            expected_version = _normalize_expected_state_version(
                expected_state_version
            )
            if (
                expected_version is not None
                and expected_version != _state_version(current_row)
            ):
                raise ConcurrentPublishJobUpdateError(
                    "领取发布任务被拒绝：发布任务状态已并发变化"
                    f"（期望版本 {expected_version}，当前为 "
                    f"{current_row['status']} 版本 {_state_version(current_row)}）"
                )
            if current_row["status"] != "queued":
                raise InvalidPublishJobTransitionError(
                    f"状态为 {current_row['status']} 的发布任务不能开始执行"
                )
            cursor = conn.execute(
                """
                UPDATE publish_jobs
                SET status = 'processing', message = ?, result_url = '',
                    reconciliation_required = 0,
                    started_at = COALESCE(started_at, CURRENT_TIMESTAMP),
                    finished_at = NULL,
                    state_version = state_version + 1
                WHERE id = ? AND status = 'queued' AND state_version = ?
                """,
                (
                    _normalize_text(message),
                    job_id,
                    _state_version(current_row),
                ),
            )
            if cursor.rowcount != 1:
                raise InvalidPublishJobTransitionError("发布任务已被其他执行器领取")
            _sync_article_status(conn, current_row["article_id"])
            updated_row = _fetch_job(conn, job_id)
            claimed_job = _serialize_job(updated_row)
            if include_account_credential_epoch:
                credential_row = conn.execute(
                    """
                    SELECT credential_epoch
                    FROM user_info
                    WHERE id = ? AND status != -2
                    """,
                    (claimed_job.get("account_id"),),
                ).fetchone()
                claimed_job["_account_credential_epoch"] = (
                    int(credential_row["credential_epoch"])
                    if credential_row is not None
                    else None
                )
    return claimed_job


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
    expected_state_version: int | None = None,
) -> dict[str, Any]:
    """Persist a non-terminal result while a publisher awaits confirmation."""

    with closing(_connect(database_path)) as conn:
        with conn:
            current_row = _fetch_job(conn, job_id)
            if current_row is None:
                raise PublishJobNotFoundError("发布任务不存在")
            stale_row = _resolve_expected_state(
                current_row,
                expected_state_version,
                operation="更新发布进度",
            )
            if stale_row is not None:
                return _serialize_job(stale_row)
            if current_row["status"] != "processing":
                raise InvalidPublishJobTransitionError(
                    f"状态为 {current_row['status']} 的发布任务不能更新进度"
                )
            current_version = _state_version(current_row)
            cursor = conn.execute(
                """
                UPDATE publish_jobs
                SET message = ?, result_url = ?,
                    state_version = state_version + 1
                WHERE id = ? AND status = 'processing' AND state_version = ?
                """,
                (
                    _normalize_text(message),
                    _normalize_text(result_url),
                    job_id,
                    current_version,
                ),
            )
            if cursor.rowcount != 1:
                latest_row = _resolve_cas_conflict(
                    conn,
                    job_id,
                    expected_status="processing",
                    expected_state_version=current_version,
                    operation="更新发布进度",
                )
                return _serialize_job(latest_row)
            updated_row = _fetch_job(conn, job_id)
    return _serialize_job(updated_row)


def transition_publish_job(
    database_path: str | Path,
    job_id: int,
    new_status: str,
    *,
    message: str = "",
    result_url: str = "",
    expected_state_version: int | None = None,
) -> dict[str, Any]:
    _validate_status(new_status)
    with closing(_connect(database_path)) as conn:
        with conn:
            current_row = _fetch_job(conn, job_id)
            if current_row is None:
                raise PublishJobNotFoundError("发布任务不存在")
            current_status = current_row["status"]
            stale_row = _resolve_expected_state(
                current_row,
                expected_state_version,
                operation=f"将发布任务变更为 {new_status}",
            )
            if stale_row is not None:
                return _serialize_job(stale_row)
            if new_status == current_status:
                _sync_article_status(conn, current_row["article_id"])
                return _serialize_job(current_row)
            if new_status not in _ALLOWED_TRANSITIONS[current_status]:
                raise InvalidPublishJobTransitionError(
                    f"发布任务不能从 {current_status} 变更为 {new_status}"
                )

            current_version = _state_version(current_row)
            if new_status == "processing":
                cursor = conn.execute(
                    """
                    UPDATE publish_jobs
                    SET status = ?, message = ?, result_url = '',
                        reconciliation_required = 0,
                        started_at = COALESCE(started_at, CURRENT_TIMESTAMP),
                        finished_at = NULL,
                        state_version = state_version + 1
                    WHERE id = ? AND status = ? AND state_version = ?
                    """,
                    (
                        new_status,
                        _normalize_text(message),
                        job_id,
                        current_status,
                        current_version,
                    ),
                )
            elif new_status in {"success", "failed"}:
                cursor = conn.execute(
                    """
                    UPDATE publish_jobs
                    SET status = ?, message = ?, result_url = ?,
                        reconciliation_required = 0,
                        finished_at = CURRENT_TIMESTAMP,
                        state_version = state_version + 1
                    WHERE id = ? AND status = ? AND state_version = ?
                    """,
                    (
                        new_status,
                        _normalize_text(message),
                        _normalize_text(result_url),
                        job_id,
                        current_status,
                        current_version,
                    ),
                )
            else:
                cursor = conn.execute(
                    """
                    UPDATE publish_jobs
                    SET status = ?, message = ?, result_url = ?,
                        reconciliation_required = 0,
                        state_version = state_version + 1
                    WHERE id = ? AND status = ? AND state_version = ?
                    """,
                    (
                        new_status,
                        _normalize_text(message),
                        _normalize_text(result_url),
                        job_id,
                        current_status,
                        current_version,
                    ),
                )
            if cursor.rowcount != 1:
                latest_row = _resolve_cas_conflict(
                    conn,
                    job_id,
                    expected_status=current_status,
                    expected_state_version=current_version,
                    operation=f"将发布任务变更为 {new_status}",
                )
                return _serialize_job(latest_row)
            _sync_article_status(conn, current_row["article_id"])
            updated_row = _fetch_job(conn, job_id)
    return _serialize_job(updated_row)


def mark_publish_job_awaiting_reconciliation(
    database_path: str | Path,
    job_id: int,
    *,
    message: str,
    result_url: str = "",
    expected_state_version: int | None = None,
) -> dict[str, Any]:
    """Finish one publisher attempt whose platform outcome is ambiguous.

    ``processing`` is reserved for a publisher call that may still be running.
    Only this explicit, finished state may be reconciled by an operator, which
    prevents a page reload from closing and retrying a live attempt.
    """

    normalized_message = _normalize_text(message)
    if not normalized_message:
        raise ValueError("待核验任务必须提供平台结果说明")
    with closing(_connect(database_path)) as conn:
        with conn:
            current_row = _fetch_job(conn, job_id)
            if current_row is None:
                raise PublishJobNotFoundError("发布任务不存在")
            stale_row = _resolve_expected_state(
                current_row,
                expected_state_version,
                operation="将发布任务标记为已提交待核验",
            )
            if stale_row is not None:
                return _serialize_job(stale_row)
            if current_row["status"] != "processing":
                raise InvalidPublishJobTransitionError(
                    f"状态为 {current_row['status']} 的发布任务不能标记为待核验"
                )
            current_version = _state_version(current_row)
            cursor = conn.execute(
                """
                UPDATE publish_jobs
                SET status = 'need_action', message = ?, result_url = ?,
                    reconciliation_required = 1,
                    finished_at = CURRENT_TIMESTAMP,
                    state_version = state_version + 1
                WHERE id = ? AND status = 'processing' AND state_version = ?
                """,
                (
                    normalized_message,
                    _normalize_text(result_url),
                    job_id,
                    current_version,
                ),
            )
            if cursor.rowcount != 1:
                latest_row = _resolve_cas_conflict(
                    conn,
                    job_id,
                    expected_status="processing",
                    expected_state_version=current_version,
                    operation="将发布任务标记为已提交待核验",
                )
                return _serialize_job(latest_row)
            _sync_article_status(conn, current_row["article_id"])
            updated_row = _fetch_job(conn, job_id)
    return _serialize_job(updated_row)


def recover_interrupted_publish_jobs(database_path: str | Path) -> int:
    """Move attempts left active by a previous desktop process to safe review.

    The desktop host calls this before accepting requests, under its
    single-instance mutex.  It never retries or contacts a platform.
    """

    with closing(_connect(database_path)) as conn:
        with conn:
            cursor = conn.execute(
                """
                UPDATE publish_jobs
                SET status = 'need_action',
                    message = CASE
                        WHEN trim(message) = '' THEN
                            '上次软件异常退出时发布仍在执行，平台结果未知；请先核验平台后台'
                        ELSE message || '；上次软件异常退出，平台结果未知，请先核验平台后台'
                    END,
                    reconciliation_required = 1,
                    finished_at = CURRENT_TIMESTAMP,
                    state_version = state_version + 1
                WHERE status = 'processing'
                """
            )
            affected_article_ids = [
                int(row[0])
                for row in conn.execute(
                    """
                    SELECT DISTINCT article_id
                    FROM publish_jobs
                    WHERE status = 'need_action' AND reconciliation_required = 1
                    """
                ).fetchall()
            ]
            for article_id in affected_article_ids:
                _sync_article_status(conn, article_id)
    return int(cursor.rowcount)


def retry_publish_job(
    database_path: str | Path,
    job_id: int,
    *,
    expected_state_version: int | None = None,
) -> dict[str, Any]:
    with closing(_connect(database_path)) as conn:
        with conn:
            current_row = _fetch_job(conn, job_id)
            if current_row is None:
                raise PublishJobNotFoundError("发布任务不存在")
            stale_row = _resolve_expected_state(
                current_row,
                expected_state_version,
                operation="重试发布任务",
            )
            if stale_row is not None:
                return _serialize_job(stale_row)
            if current_row["status"] not in _RETRYABLE_STATUSES:
                raise InvalidPublishJobTransitionError(
                    f"状态为 {current_row['status']} 的发布任务不能重试"
                )
            if bool(current_row["reconciliation_required"]):
                raise InvalidPublishJobTransitionError(
                    "平台结果尚未核验；请先使用“核验平台结果”确认未发布，禁止直接重试"
                )
            if _has_explicit_platform_rejection(conn, current_row):
                raise InvalidPublishJobTransitionError(
                    "平台已明确拒绝该版本；禁止原稿重试，请生成修订副本并重新确认"
                )
            current_status = current_row["status"]
            current_version = _state_version(current_row)
            cursor = conn.execute(
                """
                UPDATE publish_jobs
                SET status = 'queued', message = '', result_url = '',
                    reconciliation_required = 0,
                    authorization_fingerprint = '', auto_execute = 0,
                    started_at = NULL, finished_at = NULL,
                    state_version = state_version + 1
                WHERE id = ? AND status = ? AND state_version = ?
                """,
                (job_id, current_status, current_version),
            )
            if cursor.rowcount != 1:
                latest_row = _resolve_cas_conflict(
                    conn,
                    job_id,
                    expected_status=current_status,
                    expected_state_version=current_version,
                    operation="重试发布任务",
                )
                return _serialize_job(latest_row)
            _sync_article_status(conn, current_row["article_id"])
            updated_row = _fetch_job(conn, job_id)
    return _serialize_job(updated_row)


def reconcile_publish_job_success(
    database_path: str | Path,
    job_id: int,
    *,
    result_url: str,
    message: str = "平台侧已核验发布成功",
    expected_state_version: int | None = None,
) -> dict[str, Any]:
    """Correct an ambiguous/failed local result using explicit platform proof."""

    normalized_url = _normalize_text(result_url)
    with closing(_connect(database_path)) as conn:
        with conn:
            current_row = _fetch_job(conn, job_id)
            if current_row is None:
                raise PublishJobNotFoundError("发布任务不存在")
            _validate_reconciliation_result_url(
                platform=current_row["platform"],
                result_url=normalized_url,
                allow_empty=False,
            )
            stale_row = _resolve_expected_state(
                current_row,
                expected_state_version,
                operation="通过公开链接对账发布成功",
            )
            if stale_row is not None:
                return _serialize_job(stale_row)
            if current_row["status"] == "success":
                return _serialize_job(current_row)
            if current_row["status"] not in {"failed", "need_action"}:
                raise InvalidPublishJobTransitionError(
                    f"状态为 {current_row['status']} 的发布任务不能通过平台证据对账"
                )
            if (
                current_row["status"] == "need_action"
                and not bool(current_row["reconciliation_required"])
            ):
                raise InvalidPublishJobTransitionError("该任务没有待核验的平台提交结果")
            current_status = current_row["status"]
            current_version = _state_version(current_row)
            cursor = conn.execute(
                """
                UPDATE publish_jobs
                SET status = 'success', message = ?, result_url = ?,
                    reconciliation_required = 0,
                    finished_at = CURRENT_TIMESTAMP,
                    state_version = state_version + 1
                WHERE id = ? AND status = ? AND state_version = ?
                """,
                (
                    _normalize_text(message),
                    normalized_url,
                    job_id,
                    current_status,
                    current_version,
                ),
            )
            if cursor.rowcount != 1:
                latest_row = _resolve_cas_conflict(
                    conn,
                    job_id,
                    expected_status=current_status,
                    expected_state_version=current_version,
                    operation="通过公开链接对账发布成功",
                )
                return _serialize_job(latest_row)
            _sync_article_status(conn, current_row["article_id"])
            updated_row = _fetch_job(conn, job_id)
    return _serialize_job(updated_row)


def reconcile_publish_job_platform_success(
    database_path: str | Path,
    job_id: int,
    *,
    message: str,
    result_url: str = "",
    expected_state_version: int | None = None,
) -> dict[str, Any]:
    """Close an ambiguous job using platform-side proof when no public URL exists."""

    normalized_message = _normalize_text(message)
    if not normalized_message:
        raise ValueError("平台后台对账成功必须提供核验说明")
    normalized_url = _normalize_text(result_url)

    with closing(_connect(database_path)) as conn:
        with conn:
            current_row = _fetch_job(conn, job_id)
            if current_row is None:
                raise PublishJobNotFoundError("发布任务不存在")
            _validate_reconciliation_result_url(
                platform=current_row["platform"],
                result_url=normalized_url,
                allow_empty=True,
            )
            stale_row = _resolve_expected_state(
                current_row,
                expected_state_version,
                operation="通过平台后台对账发布成功",
            )
            if stale_row is not None:
                return _serialize_job(stale_row)
            if current_row["status"] == "success":
                return _serialize_job(current_row)
            if (
                current_row["status"] != "need_action"
                or not bool(current_row["reconciliation_required"])
            ):
                raise InvalidPublishJobTransitionError(
                    f"状态为 {current_row['status']} 的发布任务不能通过平台后台证据对账"
                )
            current_status = current_row["status"]
            current_version = _state_version(current_row)
            cursor = conn.execute(
                """
                UPDATE publish_jobs
                SET status = 'success', message = ?, result_url = ?,
                    reconciliation_required = 0,
                    finished_at = CURRENT_TIMESTAMP,
                    state_version = state_version + 1
                WHERE id = ? AND status = ? AND state_version = ?
                """,
                (
                    normalized_message,
                    normalized_url,
                    job_id,
                    current_status,
                    current_version,
                ),
            )
            if cursor.rowcount != 1:
                latest_row = _resolve_cas_conflict(
                    conn,
                    job_id,
                    expected_status=current_status,
                    expected_state_version=current_version,
                    operation="通过平台后台对账发布成功",
                )
                return _serialize_job(latest_row)
            _sync_article_status(conn, current_row["article_id"])
            updated_row = _fetch_job(conn, job_id)
    return _serialize_job(updated_row)


def reconcile_publish_job_failure(
    database_path: str | Path,
    job_id: int,
    *,
    message: str,
    expected_state_version: int | None = None,
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
            stale_row = _resolve_expected_state(
                current_row,
                expected_state_version,
                operation="通过平台证据结案为失败",
            )
            if stale_row is not None:
                return _serialize_job(stale_row)
            if current_row["status"] == "failed":
                return _serialize_job(current_row)
            if (
                current_row["status"] != "need_action"
                or not bool(current_row["reconciliation_required"])
            ):
                raise InvalidPublishJobTransitionError(
                    f"状态为 {current_row['status']} 的发布任务不能通过平台证据结案"
                )
            current_status = current_row["status"]
            current_version = _state_version(current_row)
            cursor = conn.execute(
                """
                UPDATE publish_jobs
                SET status = 'failed', message = ?, result_url = '',
                    reconciliation_required = 0,
                    finished_at = CURRENT_TIMESTAMP,
                    state_version = state_version + 1
                WHERE id = ? AND status = ? AND state_version = ?
                """,
                (normalized_message, job_id, current_status, current_version),
            )
            if cursor.rowcount != 1:
                latest_row = _resolve_cas_conflict(
                    conn,
                    job_id,
                    expected_status=current_status,
                    expected_state_version=current_version,
                    operation="通过平台证据结案为失败",
                )
                return _serialize_job(latest_row)
            _sync_article_status(conn, current_row["article_id"])
            updated_row = _fetch_job(conn, job_id)
    return _serialize_job(updated_row)


def _validate_reconciliation_result_url(
    *,
    platform: str,
    result_url: str,
    allow_empty: bool,
) -> None:
    """Require an exact public-work URL owned by the job's platform."""

    normalized_platform = _normalize_platform(platform)
    platform_name = str(PLATFORM_BY_KEY[normalized_platform]["name"])
    if not result_url:
        if (
            allow_empty
            and normalized_platform in _PLATFORMS_WITHOUT_STABLE_PUBLIC_WORK_URL
        ):
            return
        raise ValueError(
            f"{platform_name}人工成功核验必须提供 HTTPS 官方公开文章链接或作品链接"
        )
    if len(result_url) > 2048:
        raise ValueError("平台公开作品链接过长")
    if any(
        character.isspace() or ord(character) < 0x20
        for character in result_url
    ):
        raise ValueError("平台公开作品链接不能包含空白或控制字符")

    try:
        parsed = urlsplit(result_url)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise ValueError("平台公开作品链接格式无效") from exc

    if parsed.scheme.casefold() != "https":
        raise ValueError("平台公开作品链接必须使用 HTTPS")
    if not hostname:
        raise ValueError("平台公开作品链接缺少有效域名")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("平台公开作品链接不能包含用户信息")
    if port not in {None, 443}:
        raise ValueError("平台公开作品链接不能使用非标准端口")

    rules = _PUBLIC_WORK_URL_RULES.get(normalized_platform, ())
    normalized_hostname = hostname.casefold()
    normalized_authority = parsed.netloc.casefold()
    if normalized_authority not in {
        normalized_hostname,
        f"{normalized_hostname}:443",
    }:
        raise ValueError("平台公开作品链接包含异常端口或域名格式")
    matches_public_work = any(
        normalized_hostname == allowed_hostname
        and path_pattern.fullmatch(parsed.path) is not None
        for allowed_hostname, path_pattern in rules
    )
    if matches_public_work and normalized_platform == "baijiahao":
        article_ids = parse_qs(parsed.query, keep_blank_values=True).get("id", [])
        matches_public_work = (
            len(article_ids) == 1
            and re.fullmatch(r"[1-9]\d*", article_ids[0]) is not None
        )
    if matches_public_work and normalized_platform == "weibo":
        matches_public_work = not parsed.query and not parsed.fragment
    if not matches_public_work:
        raise ValueError(
            f"公开链接必须是{platform_name}官方公开作品链接，"
            "不能使用管理、登录或其他平台地址"
        )


def _state_version(row: sqlite3.Row) -> int:
    return int(row["state_version"])


def _normalize_expected_state_version(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("expected_state_version 必须是非负整数")
    return value


def _resolve_expected_state(
    current_row: sqlite3.Row,
    expected_state_version: int | None,
    *,
    operation: str,
) -> sqlite3.Row | None:
    """Reject an obsolete actor before it can mutate a later publish attempt.

    A confirmed success is the only irreversible terminal state. Returning it
    is safe for stale callers and prevents their error handling from trying to
    turn the job back into a retryable state.
    """

    expected = _normalize_expected_state_version(expected_state_version)
    if expected is None or expected == _state_version(current_row):
        return None
    if current_row["status"] == "success":
        return current_row
    raise ConcurrentPublishJobUpdateError(
        f"{operation}被拒绝：发布任务状态已并发变化"
        f"（期望版本 {expected}，当前为 {current_row['status']} "
        f"版本 {_state_version(current_row)}）"
    )


def _resolve_cas_conflict(
    conn: sqlite3.Connection,
    job_id: int,
    *,
    expected_status: str,
    expected_state_version: int,
    operation: str,
) -> sqlite3.Row:
    latest_row = _fetch_job(conn, job_id)
    if latest_row is None:
        raise PublishJobNotFoundError("发布任务不存在")
    if latest_row["status"] == "success":
        return latest_row
    raise ConcurrentPublishJobUpdateError(
        f"{operation}被拒绝：发布任务状态已并发变化"
        f"（期望 {expected_status} 版本 {expected_state_version}，"
        f"当前为 {latest_row['status']} 版本 {_state_version(latest_row)}）"
    )


def _connect(database_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(Path(database_path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def _has_explicit_platform_rejection(
    conn: sqlite3.Connection,
    job_row: sqlite3.Row,
) -> bool:
    if _normalize_text(job_row["message"]).startswith(
        "后台只读监测发现平台明确未通过："
    ):
        return True
    has_monitor_table = conn.execute(
        """
        SELECT 1 FROM sqlite_master
        WHERE type = 'table' AND name = 'publish_review_monitors'
        """
    ).fetchone() is not None
    if not has_monitor_table:
        return False
    row = conn.execute(
        """
        SELECT state, last_result
        FROM publish_review_monitors
        WHERE publish_job_id = ?
        """,
        (int(job_row["id"]),),
    ).fetchone()
    return bool(
        row is not None
        and (
            str(row["state"] or "") == "rejected"
            or str(row["last_result"] or "") == "rejected"
        )
    )


def _fetch_authorization_article(conn: sqlite3.Connection, article_id: int):
    return conn.execute(
        """
        SELECT
            articles.id,
            articles.title,
            articles.content,
            articles.tags,
            articles.status,
            projects.name AS project_name
        FROM articles
        JOIN projects ON projects.id = articles.project_id
        WHERE articles.id = ?
        """,
        (article_id,),
    ).fetchone()


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
    if current_status == "published" or "success" in job_statuses:
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
    job["reconciliation_required"] = bool(
        job.get("reconciliation_required", False)
    )
    job["authorization_bound"] = bool(
        str(job.get("authorization_fingerprint") or "").strip()
    )
    try:
        raw_images = json.loads(job.get("images") or "[]")
        job["images"] = normalize_publish_images(raw_images)
    except (TypeError, json.JSONDecodeError, ValueError):
        job["images"] = []
    job["publish_title"] = str(job.get("publish_title") or "").strip()
    job["publish_content"] = str(job.get("publish_content") or "").strip()
    job["content_policy_version"] = str(
        job.get("content_policy_version") or ""
    ).strip()
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


def _normalize_required_positive_integer(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field} 必须是正整数")
    if isinstance(value, int):
        normalized = value
    elif isinstance(value, str) and re.fullmatch(r"[1-9][0-9]*", value.strip()):
        normalized = int(value.strip())
    else:
        raise ValueError(f"{field} 必须是正整数")
    if normalized <= 0:
        raise ValueError(f"{field} 必须是正整数")
    return normalized


def _normalize_optional_account_id(account_id: Any) -> int | None:
    if account_id is None or account_id == "":
        return None
    return _normalize_required_positive_integer(account_id, "account_id")


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
