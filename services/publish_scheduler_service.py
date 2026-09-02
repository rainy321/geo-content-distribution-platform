from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from services.publish_job_executor import (
    DEFAULT_MEDIA_ROOT,
    PublisherFactory,
    execute_publish_job,
)
from services.publish_job_service import (
    PUBLISH_AUTHORIZATION_MISMATCH_MESSAGE,
    get_publish_job,
    transition_publish_job,
)


def queue_due_publish_jobs(
    database_path: str | Path,
    *,
    now: datetime | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Atomically promote due scheduled jobs without executing a publisher."""

    if limit <= 0:
        raise ValueError("limit 必须是正整数")
    reference_now = now or datetime.now().astimezone()
    if not isinstance(reference_now, datetime):
        raise ValueError("now 必须是 datetime")

    promoted_ids: list[int] = []
    with closing(_connect(database_path)) as conn:
        with conn:
            rows = conn.execute(
                """
                SELECT id, publish_at
                FROM publish_jobs
                WHERE status = 'scheduled' AND publish_at IS NOT NULL
                """
            ).fetchall()
            due_rows = sorted(
                (row for row in rows if _is_due(row["publish_at"], reference_now)),
                key=lambda row: (_to_comparable(row["publish_at"], reference_now), row["id"]),
            )[:limit]
            for row in due_rows:
                cursor = conn.execute(
                    """
                    UPDATE publish_jobs
                    SET status = 'queued', message = '已到计划时间，等待执行',
                        result_url = '', started_at = NULL, finished_at = NULL
                    WHERE id = ? AND status = 'scheduled'
                    """,
                    (row["id"],),
                )
                if cursor.rowcount == 1:
                    promoted_ids.append(row["id"])

    return [get_publish_job(database_path, job_id) for job_id in promoted_ids]


def run_publish_scheduler_tick(
    database_path: str | Path,
    *,
    now: datetime | None = None,
    limit: int = 100,
    publisher_factory: PublisherFactory | None = None,
    allow_real: bool = False,
    media_root: str | Path = DEFAULT_MEDIA_ROOT,
) -> dict[str, Any]:
    """Promote due jobs and execute Demo or pre-authorized real tasks."""

    promoted = queue_due_publish_jobs(database_path, now=now, limit=limit)
    results = []
    executed_demo_count = 0
    attempted_real_count = 0
    blocked_real_count = 0
    blocked_authorization_count = 0
    for job in promoted:
        if not job["demo"] and not job.get("auto_execute"):
            results.append(job)
            continue
        if not job["demo"] and (not allow_real or publisher_factory is None):
            result = transition_publish_job(
                database_path,
                job["id"],
                "need_action",
                message=(
                    "定时任务已到期，但真实发布总开关或发布器未就绪；"
                    "未访问平台，请人工确认后重试"
                ),
            )
            blocked_real_count += 1
            results.append(result)
            continue
        try:
            result = execute_publish_job(
                database_path,
                job["id"],
                publisher_factory=publisher_factory,
                media_root=media_root,
                require_authorization_match=not job["demo"],
            )
            if job["demo"]:
                executed_demo_count += 1
            elif result.get("message") == PUBLISH_AUTHORIZATION_MISMATCH_MESSAGE:
                blocked_authorization_count += 1
            else:
                attempted_real_count += 1
        except Exception:
            # A scheduler tick is best-effort. The atomic queued state remains
            # available for a later worker or manual retry if this process exits.
            result = get_publish_job(database_path, job["id"])
        results.append(result)

    return {
        "promoted_count": len(promoted),
        "executed_demo_count": executed_demo_count,
        "attempted_real_count": attempted_real_count,
        "blocked_real_count": blocked_real_count,
        "blocked_authorization_count": blocked_authorization_count,
        "jobs": results,
    }


def _connect(database_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(Path(database_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _is_due(value: str, reference_now: datetime) -> bool:
    target, reference = _comparison_pair(value, reference_now)
    return target <= reference


def _to_comparable(value: str, reference_now: datetime) -> datetime:
    target, _reference = _comparison_pair(value, reference_now)
    return target


def _comparison_pair(value: str, reference_now: datetime) -> tuple[datetime, datetime]:
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ValueError("数据库中的 publish_at 不是有效 ISO 8601 时间") from exc
    if parsed.tzinfo is None and reference_now.tzinfo is None:
        return parsed, reference_now
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=reference_now.tzinfo)
    if reference_now.tzinfo is None:
        reference_now = reference_now.replace(tzinfo=parsed.tzinfo)
    return parsed.astimezone(timezone.utc), reference_now.astimezone(timezone.utc)
