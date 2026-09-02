from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Any

from services.article_service import ArticleNotFoundError


PUBLISH_JOB_STATUSES = frozenset(
    {"queued", "processing", "success", "failed", "need_action", "scheduled"}
)
SUPPORTED_PUBLISH_PLATFORMS = frozenset(
    {"zhihu", "toutiao", "baijiahao", "sohu", "xiaohongshu"}
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


class PublishJobNotFoundError(LookupError):
    """Raised when a publish job id does not exist."""


class UnsupportedPublishPlatformError(ValueError):
    """Raised when no publisher is planned for a platform."""


class InvalidPublishJobTransitionError(RuntimeError):
    """Raised when a job status change would break the workflow."""


def create_publish_job(
    database_path: str | Path,
    *,
    article_id: int,
    platform: str,
    images: list[str] | tuple[str, ...] | None = None,
    publish_at: str | datetime | None = None,
    auto_execute: bool = False,
    demo: bool = False,
) -> dict[str, Any]:
    if not isinstance(auto_execute, bool):
        raise ValueError("auto_execute 必须是布尔值")
    normalized_platform = _normalize_platform(platform)
    normalized_images = normalize_publish_images(images)
    normalized_publish_at = _normalize_publish_at(publish_at)
    if auto_execute and normalized_publish_at is None:
        raise ValueError("auto_execute 仅适用于定时发布任务")
    normalized_auto_execute = bool(auto_execute and not demo)
    initial_status = "scheduled" if normalized_publish_at else "queued"

    with closing(_connect(database_path)) as conn:
        with conn:
            article_row = _fetch_authorization_article(conn, article_id)
            if article_row is None:
                raise ArticleNotFoundError("文章不存在")
            authorization_fingerprint = (
                build_publish_authorization_fingerprint(
                    article=dict(article_row),
                    platform=normalized_platform,
                    images=normalized_images,
                    publish_at=normalized_publish_at,
                )
                if normalized_auto_execute
                else ""
            )
            cursor = conn.execute(
                """
                INSERT INTO publish_jobs (
                    article_id, platform, status, images, publish_at,
                    authorization_fingerprint, auto_execute, demo
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    article_id,
                    normalized_platform,
                    initial_status,
                    json.dumps(normalized_images, ensure_ascii=False),
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
    publish_at: str | datetime | None,
) -> str:
    """Bind automatic execution to the exact publishable payload."""

    canonical_payload = {
        "schema": 1,
        "article_id": int(article["id"]),
        "title": str(article.get("title") or "").strip(),
        "content": str(article.get("content") or "").strip(),
        "tags": _normalize_authorization_tags(article.get("tags")),
        "platform": _normalize_platform(platform),
        "images": normalize_publish_images(images),
        "publish_at": _normalize_publish_at(publish_at),
    }
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
) -> bool:
    expected = str(job.get("authorization_fingerprint") or "").strip().lower()
    if not expected:
        return False
    actual = build_publish_authorization_fingerprint(
        article=article,
        platform=job["platform"],
        images=job.get("images"),
        publish_at=job.get("publish_at"),
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


def claim_publish_job(
    database_path: str | Path,
    job_id: int,
    *,
    message: str = "正在准备发布",
) -> dict[str, Any]:
    """Atomically move one queued job to processing for a single worker."""

    with closing(_connect(database_path)) as conn:
        with conn:
            current_row = _fetch_job(conn, job_id)
            if current_row is None:
                raise PublishJobNotFoundError("发布任务不存在")
            if current_row["status"] != "queued":
                raise InvalidPublishJobTransitionError(
                    f"状态为 {current_row['status']} 的发布任务不能开始执行"
                )
            cursor = conn.execute(
                """
                UPDATE publish_jobs
                SET status = 'processing', message = ?, result_url = '',
                    started_at = COALESCE(started_at, CURRENT_TIMESTAMP),
                    finished_at = NULL
                WHERE id = ? AND status = 'queued'
                """,
                (_normalize_text(message), job_id),
            )
            if cursor.rowcount != 1:
                raise InvalidPublishJobTransitionError("发布任务已被其他执行器领取")
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
            SELECT j.*, a.title AS article_title
            FROM publish_jobs AS j
            JOIN articles AS a ON a.id = j.article_id
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
                        started_at = COALESCE(started_at, CURRENT_TIMESTAMP),
                        finished_at = NULL
                    WHERE id = ?
                    """,
                    (new_status, _normalize_text(message), job_id),
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
            current_row = _fetch_job(conn, job_id)
            if current_row is None:
                raise PublishJobNotFoundError("发布任务不存在")
            if current_row["status"] not in _RETRYABLE_STATUSES:
                raise InvalidPublishJobTransitionError(
                    f"状态为 {current_row['status']} 的发布任务不能重试"
                )
            conn.execute(
                """
                UPDATE publish_jobs
                SET status = 'queued', message = '', result_url = '',
                    authorization_fingerprint = '', auto_execute = 0,
                    started_at = NULL, finished_at = NULL
                WHERE id = ?
                """,
                (job_id,),
            )
            _sync_article_status(conn, current_row["article_id"])
            updated_row = _fetch_job(conn, job_id)
    return _serialize_job(updated_row)


def reconcile_publish_job_success(
    database_path: str | Path,
    job_id: int,
    *,
    result_url: str,
    message: str = "平台侧已核验发布成功",
) -> dict[str, Any]:
    """Correct an ambiguous/failed local result using explicit platform proof."""

    normalized_url = _normalize_text(result_url)
    if not normalized_url.startswith(("https://", "http://")):
        raise ValueError("平台对账成功必须提供 http(s) 文章链接")
    with closing(_connect(database_path)) as conn:
        with conn:
            current_row = _fetch_job(conn, job_id)
            if current_row is None:
                raise PublishJobNotFoundError("发布任务不存在")
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
            _sync_article_status(conn, current_row["article_id"])
            updated_row = _fetch_job(conn, job_id)
    return _serialize_job(updated_row)


def _connect(database_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(Path(database_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _fetch_authorization_article(conn: sqlite3.Connection, article_id: int):
    return conn.execute(
        "SELECT id, title, content, tags FROM articles WHERE id = ?",
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
        SELECT j.*, a.title AS article_title
        FROM publish_jobs AS j
        JOIN articles AS a ON a.id = j.article_id
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
    job["authorization_bound"] = bool(
        str(job.get("authorization_fingerprint") or "").strip()
    )
    try:
        raw_images = json.loads(job.get("images") or "[]")
        job["images"] = normalize_publish_images(raw_images)
    except (TypeError, json.JSONDecodeError, ValueError):
        job["images"] = []
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
