from __future__ import annotations

import sqlite3
from collections import Counter
from contextlib import closing
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any


_DASHBOARD_PLATFORMS = (
    ("zhihu", "知乎", 9),
    ("toutiao", "今日头条", 7),
    ("baijiahao", "百家号", 5),
    ("sohu", "搜狐号", 8),
    ("xiaohongshu", "小红书", 1),
)


def get_dashboard_overview(
    database_path: str | Path,
    *,
    now: datetime | None = None,
    recent_limit: int = 8,
) -> dict[str, Any]:
    if recent_limit <= 0:
        raise ValueError("recent_limit 必须是正整数")
    local_now = _localize_now(now)
    local_timezone = local_now.tzinfo
    dates = [local_now.date() - timedelta(days=offset) for offset in range(6, -1, -1)]
    first_day_start = datetime.combine(dates[0], time.min, tzinfo=local_timezone)
    lower_bound_utc = first_day_start.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    with closing(_connect(database_path)) as conn:
        article_rows = conn.execute(
            "SELECT created_at FROM articles WHERE created_at >= ?",
            (lower_bound_utc,),
        ).fetchall()
        job_rows = conn.execute(
            """
            SELECT status, demo, created_at, finished_at
            FROM publish_jobs
            WHERE created_at >= ? OR finished_at >= ?
            """,
            (lower_bound_utc, lower_bound_utc),
        ).fetchall()
        account_rows = conn.execute(
            "SELECT type, status FROM user_info"
        ).fetchall()
        recent_rows = conn.execute(
            """
            SELECT j.*, a.title AS article_title
            FROM publish_jobs AS j
            JOIN articles AS a ON a.id = j.article_id
            ORDER BY COALESCE(j.finished_at, j.started_at, j.created_at) DESC, j.id DESC
            LIMIT ?
            """,
            (recent_limit,),
        ).fetchall()

    generated_by_date = Counter(
        _local_date(row["created_at"], local_timezone) for row in article_rows
    )
    published_by_date = Counter(
        _local_date(row["created_at"], local_timezone) for row in job_rows
    )
    success_by_date = Counter(
        _local_date(row["finished_at"], local_timezone)
        for row in job_rows
        if row["status"] == "success" and row["finished_at"]
    )
    failed_by_date = Counter(
        _local_date(row["finished_at"], local_timezone)
        for row in job_rows
        if row["status"] == "failed" and row["finished_at"]
    )
    today = local_now.date()
    connected_types = {
        row["type"] for row in account_rows if row["status"] == 1
    }
    demo_jobs_today = sum(
        1
        for row in job_rows
        if row["demo"] and _local_date(row["created_at"], local_timezone) == today
    )

    return {
        "summary": {
            "today_generated": generated_by_date[today],
            "today_published": published_by_date[today],
            "today_success": success_by_date[today],
            "today_failed": failed_by_date[today],
            "connected_media": len(connected_types),
            "demo_jobs_today": demo_jobs_today,
        },
        "trend": [
            {
                "date": day.isoformat(),
                "label": day.strftime("%m/%d"),
                "generated": generated_by_date[day],
                "published": published_by_date[day],
                "success": success_by_date[day],
            }
            for day in dates
        ],
        "platforms": [
            {
                "key": key,
                "name": name,
                "connected": platform_type in connected_types,
                "account_count": sum(
                    1
                    for row in account_rows
                    if row["type"] == platform_type and row["status"] == 1
                ),
            }
            for key, name, platform_type in _DASHBOARD_PLATFORMS
        ],
        "recent_jobs": [_serialize_recent_job(row) for row in recent_rows],
        "generated_at": local_now.isoformat(timespec="seconds"),
    }


def _connect(database_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(Path(database_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _localize_now(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now().astimezone()
    if not isinstance(value, datetime):
        raise ValueError("now 必须是 datetime")
    if value.tzinfo is None:
        return value.replace(tzinfo=datetime.now().astimezone().tzinfo)
    return value


def _local_date(value: str, local_timezone) -> date:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(local_timezone).date()


def _serialize_recent_job(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "job_id": row["id"],
        "article_id": row["article_id"],
        "article_title": row["article_title"],
        "platform": row["platform"],
        "status": row["status"],
        "message": row["message"],
        "url": row["result_url"],
        "demo": bool(row["demo"]),
        "publish_at": row["publish_at"],
        "created_at": row["created_at"],
        "finished_at": row["finished_at"],
    }
