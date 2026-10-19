from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from services.platform_capability_service import get_platform_capability


POLICY_VERSION = "2026-09-08"
COUNTED_JOB_STATUSES = frozenset(
    {"scheduled", "queued", "processing", "success", "failed", "need_action"}
)


class PublishPolicyViolation(ValueError):
    """Raised when reserving a real publish slot would violate a safety rule."""

    def __init__(self, message: str, *, decision: dict[str, Any]):
        super().__init__(message)
        self.decision = decision


def evaluate_publish_reservation(
    conn: sqlite3.Connection,
    *,
    account_id: int,
    platform: str,
    publish_at: str | datetime | None = None,
    now: datetime | None = None,
    exclude_job_id: int | None = None,
) -> dict[str, Any]:
    """Evaluate one task against the product's conservative rolling policy.

    The result only knows about jobs created by this installation. Content
    posted directly in a platform backend remains intentionally unknown.
    """

    capability = get_platform_capability(platform)
    if capability is None:
        raise ValueError("暂不支持该发布平台")

    reference = _normalize_reference_time(publish_at, now=now)
    rows = conn.execute(
        """
        SELECT id, status, created_at, publish_at, started_at
        FROM publish_jobs
        WHERE account_id = ? AND platform = ? AND demo = 0
        """,
        (int(account_id), capability["key"]),
    ).fetchall()
    reserved_times = [
        timestamp
        for row in rows
        if row['id'] != exclude_job_id
        if str(row["status"]) in COUNTED_JOB_STATUSES
        if (timestamp := _job_effective_time(row)) is not None
    ]

    recommended_limit = int(capability["safe_daily_new"])
    stable_limit = int(capability["safe_daily_stable"])
    official_limit = capability.get("official_daily_limit")
    effective_limit = min(
        recommended_limit,
        int(official_limit) if isinstance(official_limit, int) else recommended_limit,
    )
    used_24h = _max_existing_in_rolling_window(reserved_times, reference)
    interval_hours = int(capability["min_interval_hours"])
    interval = timedelta(hours=interval_hours)
    nearest_conflict = min(
        (
            (abs((timestamp - reference).total_seconds()), timestamp)
            for timestamp in reserved_times
        ),
        default=None,
    )

    decision = {
        "allowed": True,
        "reason_code": "allowed",
        "policy_version": POLICY_VERSION,
        "platform": capability["key"],
        "account_id": int(account_id),
        "reference_at": reference.isoformat(),
        "recommended_limit": recommended_limit,
        "stable_limit": stable_limit,
        "official_limit": official_limit,
        "effective_limit": effective_limit,
        "used_24h": used_24h,
        "remaining": max(effective_limit - used_24h, 0),
        "minimum_interval_hours": interval_hours,
        "next_allowed_at": None,
        "manual_activity_unknown": True,
        "delivery_state": capability["delivery_state"],
    }

    if capability["delivery_state"] == "paused":
        return {
            **decision,
            "allowed": False,
            "reason_code": "platform_delivery_paused",
            "remaining": 0,
        }

    if nearest_conflict and nearest_conflict[0] < interval.total_seconds():
        next_allowed = max(timestamp for timestamp in reserved_times) + interval
        return {
            **decision,
            "allowed": False,
            "reason_code": "minimum_interval",
            "remaining": 0,
            "next_allowed_at": next_allowed.isoformat(),
        }

    if used_24h >= effective_limit:
        next_allowed = min(reserved_times) + timedelta(hours=24)
        return {
            **decision,
            "allowed": False,
            "reason_code": "rolling_limit_reached",
            "remaining": 0,
            "next_allowed_at": next_allowed.isoformat(),
        }

    return decision


def assert_can_reserve_publish(
    conn: sqlite3.Connection,
    *,
    account_id: int,
    platform: str,
    publish_at: str | datetime | None = None,
    exclude_job_id: int | None = None,
) -> dict[str, Any]:
    decision = evaluate_publish_reservation(
        conn,
        account_id=account_id,
        platform=platform,
        publish_at=publish_at,
        exclude_job_id=exclude_job_id,
    )
    if decision["allowed"]:
        return decision

    reason_code = decision["reason_code"]
    if reason_code == "platform_delivery_paused":
        message = "该平台真实自动发布当前暂停，本次不会访问平台"
    elif reason_code == "minimum_interval":
        message = (
            f"同一账号在该平台的发布间隔至少为 "
            f"{decision['minimum_interval_hours']} 小时"
        )
    else:
        message = (
            "该账号已达到本系统建议上限（滚动 24 小时 "
            f"{decision['used_24h']}/{decision['effective_limit']}）；"
            "本次不会自动提交"
        )
    raise PublishPolicyViolation(message, decision=decision)


def _normalize_reference_time(
    publish_at: str | datetime | None,
    *,
    now: datetime | None,
) -> datetime:
    if publish_at not in {None, ""}:
        return _parse_time(publish_at, naive_timezone=_local_timezone())
    current = now or datetime.now().astimezone()
    if current.tzinfo is None:
        current = current.replace(tzinfo=_local_timezone())
    return current.astimezone(timezone.utc)


def _job_effective_time(row: sqlite3.Row) -> datetime | None:
    if 'started_at' in row.keys() and row['started_at']:
        return _parse_time(row['started_at'], naive_timezone=timezone.utc)
    publish_at = row["publish_at"]
    if publish_at:
        return _parse_time(publish_at, naive_timezone=_local_timezone())
    created_at = row["created_at"]
    if not created_at:
        return None
    return _parse_time(created_at, naive_timezone=timezone.utc)


def _parse_time(value: str | datetime, *, naive_timezone) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=naive_timezone)
    return parsed.astimezone(timezone.utc)


def _local_timezone():
    return datetime.now().astimezone().tzinfo or timezone.utc


def _max_existing_in_rolling_window(
    reserved_times: list[datetime],
    reference: datetime,
) -> int:
    if not reserved_times:
        return 0
    points = sorted([*reserved_times, reference])
    window = timedelta(hours=24)
    maximum = 1
    for start in points:
        end = start + window
        if not (start <= reference < end):
            continue
        maximum = max(maximum, sum(start <= point < end for point in points))
    return max(maximum - 1, 0)
