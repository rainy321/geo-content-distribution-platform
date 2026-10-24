from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from services.platform_capability_service import get_platform_capability


POLICY_VERSION = "2026-10-24"
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

    reference = normalize_publish_reference(publish_at, now=now)
    observation = observe_publish_quota(
        conn, account_id=account_id, platform=capability["key"],
        reference=reference, exclude_job_id=exclude_job_id,
    )
    reserved_times = observation["reserved_times"]

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
        "attempted_24h": observation["attempted_24h"],
        "last_attempt_at": observation["last_attempt_at"],
    }

    if capability["delivery_state"] == "paused":
        return {
            **decision,
            "allowed": False,
            "reason_code": "platform_delivery_paused",
            "remaining": 0,
        }

    reason = _quota_conflict(reserved_times, reference, interval, effective_limit)
    if reason:
        return {
            **decision,
            "allowed": False,
            "reason_code": reason,
            "remaining": 0,
            "next_allowed_at": _next_available_reference(
                reserved_times, reference, interval, effective_limit,
            ).isoformat(),
        }
    return decision


def observe_publish_quota(
    conn: sqlite3.Connection,
    *,
    account_id: int,
    platform: str,
    reference: datetime,
    exclude_job_id: int | None = None,
) -> dict[str, Any]:
    """Read each real job once using the reservation policy's effective time.

    Failed, pending-action and unrecognized legacy states retain their slot;
    there is no implicit quota-release state. Excluding the candidate's own
    job lets retry validation evaluate a replacement rather than an extra slot.
    """
    rows = conn.execute(
        """
        SELECT id, status, created_at, publish_at, started_at
        FROM publish_jobs
        WHERE account_id = ? AND platform = ? AND demo = 0
        """,
        (int(account_id), platform),
    ).fetchall()
    reserved_times, attempts = [], []
    for row in rows:
        if row["id"] == exclude_job_id:
            continue
        timestamp = _job_effective_time(row)
        if timestamp is None:
            raise ValueError("历史发布任务缺少可靠的预留时间，请先核对任务记录")
        reserved_times.append(timestamp)
        if row["started_at"]:
            attempts.append(_parse_time(row["started_at"], naive_timezone=timezone.utc))
    previous_attempts = [timestamp for timestamp in attempts if timestamp <= reference]
    return {
        "reserved_times": reserved_times,
        "attempted_24h": sum(reference - timedelta(hours=24) < timestamp <= reference
                              for timestamp in previous_attempts),
        "last_attempt_at": max(previous_attempts).isoformat() if previous_attempts else None,
    }


def _quota_conflict(reserved_times, reference, interval, effective_limit):
    if any(abs(timestamp - reference) < interval for timestamp in reserved_times):
        return "minimum_interval"
    if _max_existing_in_rolling_window(reserved_times, reference) >= effective_limit:
        return "rolling_limit_reached"
    return None


def _next_available_reference(reserved_times, reference, interval, effective_limit):
    # Moving forward can resolve a conflict only when a prior reservation's
    # interval or rolling window expires. Test each boundary against *both*
    # rules, so unrelated old/future reservations cannot give a false date.
    candidates = sorted({timestamp + delay for timestamp in reserved_times
                         for delay in (interval, timedelta(hours=24))
                         if timestamp + delay > reference})
    for candidate in candidates:
        if not _quota_conflict(reserved_times, candidate, interval, effective_limit):
            return candidate
    # The final reservation's 24h expiry is always present for positive limits.
    raise ValueError("无法确定可用的发布时间，请先核对发布策略")


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


def normalize_publish_reference(
    publish_at: str | datetime | None = None,
    *,
    now: datetime | None = None,
) -> datetime:
    if publish_at not in {None, ""}:
        return _parse_time(publish_at, naive_timezone=_local_timezone())
    current = now or datetime.now().astimezone()
    if current.tzinfo is None:
        current = current.replace(tzinfo=_local_timezone())
    return current.astimezone(timezone.utc)


# Existing target-frequency guards share this normalization helper.
_normalize_reference_time = normalize_publish_reference


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
    window = timedelta(hours=24)
    # Only points strictly within one day of the proposed slot can share a
    # half-open window with it. Keep duplicates: each existing job is one slot.
    points = sorted([reference, *(point for point in reserved_times
                                 if reference - window < point < reference + window)])
    maximum = 1
    right = 0
    for left, start in enumerate(points):
        if start > reference:
            break
        if left and points[left - 1] == start:
            continue
        end = start + window
        while right < len(points) and points[right] < end:
            right += 1
        maximum = max(maximum, right - left)
    return max(maximum - 1, 0)
