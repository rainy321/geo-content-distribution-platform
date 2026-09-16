from __future__ import annotations

import sqlite3
from collections.abc import Callable, Mapping
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from services.media_account_risk_service import (
    ConcurrentMediaAccountCredentialUpdateError,
    ConcurrentMediaAccountPublishJobUpdateError,
    FROZEN_RISK_CATEGORIES,
    classify_media_account_risk,
    ensure_media_account_risk_schema,
    get_active_media_account_risks,
    record_media_account_risk,
)
from services.publish_job_service import (
    ConcurrentPublishJobUpdateError,
    InvalidPublishJobTransitionError,
    get_publish_job,
    reconcile_publish_job_failure,
    reconcile_publish_job_platform_success,
)


PUBLISH_REVIEW_MONITOR_VERSION = "publish-review-monitor-1.1.0"
DEFAULT_MAX_ATTEMPTS = 12
DEFAULT_BATCH_LIMIT = 2

# Delays are deliberately conservative.  The scheduler may tick frequently,
# but a platform is not contacted again until the durable ``next_check_at``.
# Twelve attempts cover roughly two days without turning a creator account into
# a high-frequency polling target.
_BACKOFF_SECONDS = (120, 300, 900, 1800, 3600, 7200, 14400, 21600)
_MONITOR_STATES = frozenset(
    {"watching", "checking", "published", "rejected", "action_required", "exhausted", "paused"}
)
_CHECK_STATES = frozenset(
    {"published", "processing", "rejected", "not_found", "risk", "unsupported", "error"}
)
_ALERT_CATEGORIES = frozenset(
    {"published", "rejected", "account_risk", "monitor_exhausted", "manual_check"}
)


class PublishReviewMonitorError(RuntimeError):
    """Raised when a monitor result violates the read-only contract."""


ReviewVerifier = Callable[[dict[str, Any]], Mapping[str, Any]]


def seed_publish_review_monitors(
    database_path: str | Path,
    *,
    now: datetime | str | None = None,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> int:
    """Persist watches for submitted jobs whose remote outcome is unresolved.

    Seeding is local-only.  It never creates a publisher, opens a browser or
    modifies a publish job.  Repeated calls for the same job version are
    idempotent.  A strictly newer publish-job version resets the durable watch
    so an old verifier result cannot close a newer submission attempt.
    """

    normalized_max_attempts = _normalize_max_attempts(max_attempts)
    timestamp = _normalize_timestamp(now)
    with closing(_connect(database_path)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            _ensure_schema(conn)
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO publish_review_monitors (
                    publish_job_id, account_id, state, attempt_count,
                    max_attempts, next_check_at, observed_job_version,
                    observed_account_epoch,
                    created_at, updated_at
                )
                SELECT j.id, j.account_id, 'watching', 0, ?, ?,
                       j.state_version, u.credential_epoch, ?, ?
                FROM publish_jobs AS j
                JOIN user_info AS u ON u.id = j.account_id
                WHERE j.demo = 0
                  AND j.status = 'need_action'
                  AND j.reconciliation_required = 1
                  AND j.account_id IS NOT NULL
                """,
                (
                    normalized_max_attempts,
                    timestamp,
                    timestamp,
                    timestamp,
                ),
            )
            inserted = int(cursor.rowcount)
            # One publish_job_id can acquire a newer state_version when a
            # previously interrupted submission is recovered or re-entered
            # into reconciliation.  INSERT OR IGNORE alone would leave the
            # old terminal/backoff state attached to that new attempt.  Only a
            # strictly newer version may replace monitor state; equal versions
            # remain idempotent and a defensive older job snapshot can never
            # overwrite a newer observed version.
            reset = conn.execute(
                """
                UPDATE publish_review_monitors AS m
                SET account_id = (
                        SELECT j.account_id FROM publish_jobs AS j
                        WHERE j.id = m.publish_job_id
                    ),
                    state = 'watching',
                    attempt_count = 0,
                    max_attempts = ?,
                    next_check_at = ?,
                    last_checked_at = NULL,
                    last_result = '',
                    last_message = '',
                    observed_job_version = (
                        SELECT j.state_version FROM publish_jobs AS j
                        WHERE j.id = m.publish_job_id
                    ),
                    observed_account_epoch = (
                        SELECT u.credential_epoch
                        FROM publish_jobs AS j
                        JOIN user_info AS u ON u.id = j.account_id
                        WHERE j.id = m.publish_job_id
                    ),
                    updated_at = ?,
                    completed_at = NULL
                WHERE EXISTS (
                    SELECT 1
                    FROM publish_jobs AS j
                    JOIN user_info AS u ON u.id = j.account_id
                    WHERE j.id = m.publish_job_id
                      AND j.demo = 0
                      AND j.status = 'need_action'
                      AND j.reconciliation_required = 1
                      AND j.account_id IS NOT NULL
                      AND (
                          j.state_version > m.observed_job_version
                          OR (
                              j.state_version = m.observed_job_version
                              AND (
                                  j.account_id != m.account_id
                                  OR (
                                      j.account_id = m.account_id
                                      AND u.credential_epoch > m.observed_account_epoch
                                  )
                              )
                          )
                      )
                )
                """,
                (
                    normalized_max_attempts,
                    timestamp,
                    timestamp,
                ),
            )
            synchronized = inserted + int(reset.rowcount)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return synchronized


def run_publish_review_monitor_tick(
    database_path: str | Path,
    *,
    verifier: ReviewVerifier,
    now: datetime | str | None = None,
    limit: int = DEFAULT_BATCH_LIMIT,
) -> dict[str, Any]:
    """Run a bounded batch of read-only platform checks.

    The verifier receives one immutable request snapshot and must only inspect
    platform state.  This service has no publisher dependency and therefore
    cannot submit or resubmit content.  Positive/rejected evidence is applied
    through the existing optimistic-lock reconciliation functions.
    """

    if not callable(verifier):
        raise ValueError("verifier 必须可调用")
    normalized_limit = _normalize_limit(limit)
    reference_time = _parse_timestamp(now)
    reference_timestamp = _format_timestamp(reference_time)
    seed_publish_review_monitors(database_path, now=reference_time)
    _sync_terminal_publish_review_state(database_path, now=reference_time)
    _recover_stale_claims(database_path, now=reference_time)
    # Legacy tasks can lose or change their account binding after a login is
    # removed.  Such a monitor can never satisfy the claim query, so leaving it
    # in ``watching`` creates a permanent false "still monitoring" state.  Stop
    # it locally and make the manual-check boundary explicit; never migrate an
    # old publish attempt to a newly logged-in account.
    _pause_detached_account_monitors(database_path, now=reference_time)
    # Account risk is a hard local safety boundary.  Resolve it before any
    # verifier snapshot is claimed so a permanently banned, restricted,
    # verification-blocked or expired account is never opened in the
    # background.  The claim query repeats the enabled-account condition to
    # close the race with a risk recorded immediately after this check.
    _pause_ineligible_account_monitors(database_path, now=reference_time)

    claimed = _claim_due_monitors(
        database_path,
        now=reference_time,
        limit=normalized_limit,
    )
    results: list[dict[str, Any]] = []
    for request_snapshot in claimed:
        if not _snapshot_account_credential_is_current(
            database_path, request_snapshot
        ):
            results.append(
                _discard_stale_account_check_result(
                    database_path,
                    request_snapshot=request_snapshot,
                    checked_at=reference_time,
                )
            )
            continue
        # Re-check immediately before the external read.  A risk may have been
        # written after the batch claim transaction completed.  In that case
        # freeze locally and never invoke the verifier for this snapshot.
        if not _snapshot_account_is_eligible(database_path, request_snapshot):
            _pause_ineligible_account_monitors(database_path, now=reference_time)
            monitor = get_publish_review_monitor(
                database_path,
                int(request_snapshot["job_id"]),
            )
            results.append(
                {
                    "job_id": int(request_snapshot["job_id"]),
                    "state": str((monitor or {}).get("state") or "paused"),
                    "message": str(
                        (monitor or {}).get("last_message")
                        or "账号状态已变化，本轮未访问平台"
                    ),
                }
            )
            continue
        try:
            raw_result = verifier(dict(request_snapshot))
            result = _normalize_check_result(raw_result)
        except Exception as exc:
            # A checker failure is not evidence of rejection, publication or
            # account punishment.  Keep the task unresolved and back off.
            result = {
                "state": "error",
                "message": (
                    "只读平台核验暂未完成："
                    f"{str(exc).strip() or exc.__class__.__name__}"
                )[:500],
                "result_url": "",
                "evidence": "",
            }
        applied = _apply_check_result(
            database_path,
            request_snapshot=request_snapshot,
            result=result,
            checked_at=reference_time,
        )
        results.append(applied)

    return {
        "version": PUBLISH_REVIEW_MONITOR_VERSION,
        "checked_at": reference_timestamp,
        "checked_count": len(results),
        "limit": normalized_limit,
        "results": results,
    }


def get_publish_review_monitor(
    database_path: str | Path,
    job_id: int,
) -> dict[str, Any] | None:
    normalized_job_id = _normalize_positive_id(job_id, field="job_id")
    with closing(_connect(database_path)) as conn:
        _ensure_schema(conn)
        row = conn.execute(
            """
            SELECT m.*, j.platform, j.status AS job_status,
                   j.reconciliation_required, j.result_url,
                   a.title AS article_title, u.userName AS account_name
            FROM publish_review_monitors AS m
            JOIN publish_jobs AS j ON j.id = m.publish_job_id
            JOIN articles AS a ON a.id = j.article_id
            LEFT JOIN user_info AS u ON u.id = m.account_id
            WHERE m.publish_job_id = ?
            """,
            (normalized_job_id,),
        ).fetchone()
    return _serialize_monitor(row) if row is not None else None


def get_publish_review_overview(
    database_path: str | Path,
    *,
    unread_only: bool = False,
    alert_limit: int = 20,
) -> dict[str, Any]:
    """Return durable monitor/alert state without contacting any platform."""

    normalized_alert_limit = _normalize_limit(alert_limit, maximum=100)
    seed_publish_review_monitors(database_path)
    _sync_terminal_publish_review_state(database_path)
    _sync_account_risk_alerts(database_path)
    acknowledged_at = _normalize_timestamp(None)
    with closing(_connect(database_path)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            _ensure_schema(conn)
            # ``account-risk:<risk_id>`` alerts are durable audit records, but
            # they only describe the credential generation that produced the
            # corresponding risk.  Once a successful login advances the
            # account epoch (or the risk is resolved), close the old alert in
            # the same write transaction that builds the overview.  This
            # prevents an epoch change racing between a stale alert read and
            # the unread count from surfacing a superseded red warning.
            conn.execute(
                """
                UPDATE publish_review_alerts AS alert
                SET acknowledged_at = COALESCE(alert.acknowledged_at, ?)
                WHERE alert.category = 'account_risk'
                  AND alert.acknowledged_at IS NULL
                  AND NOT EXISTS (
                      SELECT 1
                      FROM media_account_risk_events AS risk
                      JOIN user_info AS account ON account.id = risk.account_id
                      WHERE alert.event_key =
                                'account-risk:' || CAST(risk.id AS TEXT)
                        AND alert.account_id = risk.account_id
                        AND risk.active = 1
                        AND risk.credential_epoch = account.credential_epoch
                  )
                """,
                (acknowledged_at,),
            )
            monitor_rows = conn.execute(
                """
                SELECT m.*, j.platform, j.status AS job_status,
                       j.reconciliation_required, j.result_url,
                       a.title AS article_title, u.userName AS account_name
                FROM publish_review_monitors AS m
                JOIN publish_jobs AS j ON j.id = m.publish_job_id
                JOIN articles AS a ON a.id = j.article_id
                LEFT JOIN user_info AS u ON u.id = m.account_id
                ORDER BY m.updated_at DESC, m.publish_job_id DESC
                """
            ).fetchall()
            alert_conditions = [
                """
                (
                    alert.category != 'account_risk'
                    OR EXISTS (
                        SELECT 1
                        FROM media_account_risk_events AS risk
                        JOIN user_info AS account ON account.id = risk.account_id
                        WHERE alert.event_key =
                                  'account-risk:' || CAST(risk.id AS TEXT)
                          AND alert.account_id = risk.account_id
                          AND risk.active = 1
                          AND risk.credential_epoch = account.credential_epoch
                    )
                )
                """
            ]
            if unread_only:
                alert_conditions.append("alert.acknowledged_at IS NULL")
            where = "WHERE " + " AND ".join(alert_conditions)
            alert_rows = conn.execute(
                f"""
                SELECT alert.* FROM publish_review_alerts AS alert
                {where}
                ORDER BY alert.created_at DESC, alert.id DESC
                LIMIT ?
                """,
                (normalized_alert_limit,),
            ).fetchall()
            unread_count = int(
                conn.execute(
                    "SELECT COUNT(*) FROM publish_review_alerts "
                    "WHERE acknowledged_at IS NULL"
                ).fetchone()[0]
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    monitors = [_serialize_monitor(row) for row in monitor_rows]
    return {
        "version": PUBLISH_REVIEW_MONITOR_VERSION,
        "summary": {
            "watching": sum(item["state"] in {"watching", "checking"} for item in monitors),
            "action_required": sum(item["state"] == "action_required" for item in monitors),
            "completed": sum(item["state"] in {"published", "rejected"} for item in monitors),
            "exhausted": sum(item["state"] == "exhausted" for item in monitors),
            "unread_alerts": unread_count,
        },
        "monitors": monitors,
        "alerts": [_serialize_alert(row) for row in alert_rows],
    }


def acknowledge_publish_review_alert(
    database_path: str | Path,
    alert_id: int,
    *,
    acknowledged_at: datetime | str | None = None,
) -> dict[str, Any]:
    normalized_alert_id = _normalize_positive_id(alert_id, field="alert_id")
    timestamp = _normalize_timestamp(acknowledged_at)
    with closing(_connect(database_path)) as conn:
        with conn:
            _ensure_schema(conn)
            row = conn.execute(
                "SELECT * FROM publish_review_alerts WHERE id = ?",
                (normalized_alert_id,),
            ).fetchone()
            if row is None:
                raise LookupError("发布监测提醒不存在")
            conn.execute(
                """
                UPDATE publish_review_alerts
                SET acknowledged_at = COALESCE(acknowledged_at, ?)
                WHERE id = ?
                """,
                (timestamp, normalized_alert_id),
            )
            updated = conn.execute(
                "SELECT * FROM publish_review_alerts WHERE id = ?",
                (normalized_alert_id,),
            ).fetchone()
    return _serialize_alert(updated)


def rearm_publish_review_monitor(
    database_path: str | Path,
    job_id: int,
    *,
    now: datetime | str | None = None,
) -> dict[str, Any]:
    """Allow an operator to restart checks without submitting content."""

    normalized_job_id = _normalize_positive_id(job_id, field="job_id")
    timestamp = _normalize_timestamp(now)
    seed_publish_review_monitors(database_path, now=now)
    active_risks = get_active_media_account_risks(database_path)
    with closing(_connect(database_path)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            _ensure_schema(conn)
            job = conn.execute(
                """
                SELECT status, reconciliation_required
                FROM publish_jobs WHERE id = ?
                """,
                (normalized_job_id,),
            ).fetchone()
            if job is None:
                raise LookupError("发布任务不存在")
            if job["status"] != "need_action" or not bool(job["reconciliation_required"]):
                raise PublishReviewMonitorError("只有待平台核验的任务可以恢复监测")
            monitor_account = conn.execute(
                "SELECT account_id FROM publish_review_monitors WHERE publish_job_id = ?",
                (normalized_job_id,),
            ).fetchone()
            active_risk = (
                active_risks.get(int(monitor_account["account_id"]))
                if monitor_account is not None
                else None
            )
            if active_risk and active_risk.get("frozen"):
                raise PublishReviewMonitorError(
                    f"账号仍处于“{active_risk['label']}”状态，请先按提示恢复并重新登录"
                )
            cursor = conn.execute(
                """
                UPDATE publish_review_monitors
                SET state = 'watching', attempt_count = 0,
                    next_check_at = ?, last_checked_at = NULL,
                    last_result = '', last_message = '', completed_at = NULL,
                    updated_at = ?
                WHERE publish_job_id = ?
                """,
                (timestamp, timestamp, normalized_job_id),
            )
            if cursor.rowcount != 1:
                raise LookupError("发布监测记录不存在")
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    monitor = get_publish_review_monitor(database_path, normalized_job_id)
    if monitor is None:  # pragma: no cover - defensive race boundary
        raise LookupError("发布监测记录不存在")
    return monitor


def _claim_due_monitors(
    database_path: str | Path,
    *,
    now: datetime,
    limit: int,
) -> list[dict[str, Any]]:
    timestamp = _format_timestamp(now)
    claimed_ids: list[int] = []
    with closing(_connect(database_path)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            _ensure_schema(conn)
            rows = conn.execute(
                """
                SELECT m.publish_job_id
                FROM publish_review_monitors AS m
                JOIN publish_jobs AS j ON j.id = m.publish_job_id
                JOIN user_info AS u ON u.id = m.account_id
                WHERE m.state = 'watching'
                  AND m.attempt_count < m.max_attempts
                  AND m.next_check_at <= ?
                  AND m.observed_job_version = j.state_version
                  AND m.observed_account_epoch = u.credential_epoch
                  AND m.account_id = j.account_id
                  AND j.status = 'need_action'
                  AND j.reconciliation_required = 1
                  AND j.demo = 0
                  AND u.status = 1
                ORDER BY m.next_check_at ASC, m.publish_job_id DESC
                LIMIT ?
                """,
                (timestamp, limit),
            ).fetchall()
            for row in rows:
                cursor = conn.execute(
                    """
                    UPDATE publish_review_monitors
                    SET state = 'checking', attempt_count = attempt_count + 1,
                        last_checked_at = ?, updated_at = ?
                    WHERE publish_job_id = ? AND state = 'watching'
                      AND next_check_at <= ?
                      AND observed_job_version = (
                          SELECT state_version FROM publish_jobs
                          WHERE id = publish_review_monitors.publish_job_id
                      )
                      AND observed_account_epoch = (
                          SELECT u.credential_epoch
                          FROM user_info AS u
                          WHERE u.id = publish_review_monitors.account_id
                      )
                    """,
                    (timestamp, timestamp, int(row["publish_job_id"]), timestamp),
                )
                if cursor.rowcount == 1:
                    claimed_ids.append(int(row["publish_job_id"]))

            snapshots = []
            for job_id in claimed_ids:
                snapshot = conn.execute(
                    """
                    SELECT m.publish_job_id AS job_id, m.account_id,
                           m.attempt_count, m.max_attempts,
                           m.observed_job_version,
                           m.observed_account_epoch AS account_credential_epoch,
                           j.state_version,
                           j.platform, j.result_url, j.message,
                           j.started_at, j.created_at,
                           j.publish_title, a.title AS article_title,
                           u.filePath AS account_file,
                           u.userName AS account_name, u.status AS account_status
                    FROM publish_review_monitors AS m
                    JOIN publish_jobs AS j ON j.id = m.publish_job_id
                    JOIN articles AS a ON a.id = j.article_id
                    JOIN user_info AS u ON u.id = m.account_id
                    WHERE m.publish_job_id = ?
                    """,
                    (job_id,),
                ).fetchone()
                if snapshot is not None:
                    payload = dict(snapshot)
                    payload["title"] = str(
                        payload.get("publish_title") or payload.get("article_title") or ""
                    ).strip()
                    snapshots.append(payload)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return snapshots


def _pause_detached_account_monitors(
    database_path: str | Path,
    *,
    now: datetime,
) -> int:
    """Close monitors whose immutable attempt/account binding was detached.

    This repair is intentionally local-only.  A task created with account A
    must never be inspected through account B merely because B is now the
    active login for that platform.  Older databases also contain tasks whose
    ``publish_jobs.account_id`` was cleared while the monitor retained A; those
    rows otherwise remain ``watching`` forever because the claim query quite
    correctly refuses them.
    """

    timestamp = _format_timestamp(now)
    paused_count = 0
    with closing(_connect(database_path)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            _ensure_schema(conn)
            rows = conn.execute(
                """
                SELECT m.publish_job_id, m.account_id,
                       m.observed_job_version, j.state_version,
                       j.account_id AS job_account_id, j.platform
                FROM publish_review_monitors AS m
                JOIN publish_jobs AS j ON j.id = m.publish_job_id
                WHERE m.state IN ('watching', 'checking')
                  AND j.status = 'need_action'
                  AND j.reconciliation_required = 1
                  AND (
                      j.account_id IS NULL
                      OR j.account_id != m.account_id
                  )
                ORDER BY m.publish_job_id
                """
            ).fetchall()
            for row in rows:
                job_id = int(row["publish_job_id"])
                monitor_account_id = int(row["account_id"])
                observed_version = int(row["observed_job_version"])
                job_version = int(row["state_version"])
                job_account_id = row["job_account_id"]
                if job_account_id is None:
                    message = (
                        "任务原账号已解绑，旧任务只读监测已停止；软件不会自动改用"
                        "新账号核验旧发布尝试，请到原平台后台人工核对。"
                    )
                else:
                    message = (
                        "任务账号绑定已变更，旧任务只读监测已停止；软件不会使用"
                        "新账号核验旧发布尝试，请按原账号与原任务人工核对。"
                    )

                cursor = conn.execute(
                    """
                    UPDATE publish_review_monitors
                    SET state = 'paused', next_check_at = NULL,
                        last_result = 'account_binding_changed',
                        last_message = ?, completed_at = ?, updated_at = ?
                    WHERE publish_job_id = ?
                      AND account_id = ?
                      AND observed_job_version = ?
                      AND state IN ('watching', 'checking')
                      AND EXISTS (
                          SELECT 1
                          FROM publish_jobs AS current_job
                          WHERE current_job.id = publish_review_monitors.publish_job_id
                            AND current_job.state_version = ?
                            AND current_job.status = 'need_action'
                            AND current_job.reconciliation_required = 1
                            AND (
                                current_job.account_id IS NULL
                                OR current_job.account_id != publish_review_monitors.account_id
                            )
                      )
                    """,
                    (
                        message[:500],
                        timestamp,
                        timestamp,
                        job_id,
                        monitor_account_id,
                        observed_version,
                        job_version,
                    ),
                )
                if cursor.rowcount != 1:
                    continue
                conn.execute(
                    """
                    INSERT OR IGNORE INTO publish_review_alerts (
                        event_key, category, severity, title, message,
                        publish_job_id, account_id, result_url, created_at
                    ) VALUES (?, 'manual_check', 'error', ?, ?, ?, ?, '', ?)
                    """,
                    (
                        f"job:{job_id}:account-binding-changed"[:160],
                        f"{_platform_name(row['platform'])}旧任务账号绑定已变化"[:160],
                        message[:1000],
                        job_id,
                        monitor_account_id,
                        timestamp,
                    ),
                )
                paused_count += 1
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return paused_count


def _pause_ineligible_account_monitors(
    database_path: str | Path,
    *,
    now: datetime,
) -> int:
    """Freeze local monitors whose account may not be safely inspected.

    This function is deliberately local-only: it reads the durable account and
    risk ledger, updates monitor state and creates a deduplicated alert.  It
    never constructs or invokes a platform verifier.
    """

    timestamp = _format_timestamp(now)
    paused_count = 0
    with closing(_connect(database_path)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            _ensure_schema(conn)
            rows = conn.execute(
                """
                SELECT m.publish_job_id, m.account_id, u.status,
                       u.credential_epoch, j.platform,
                       r.id AS risk_id, r.category AS risk_category,
                       r.reason AS risk_reason, r.evidence AS risk_evidence,
                       r.occurred_at AS risk_occurred_at
                FROM publish_review_monitors AS m
                JOIN publish_jobs AS j ON j.id = m.publish_job_id
                JOIN user_info AS u ON u.id = m.account_id
                LEFT JOIN media_account_risk_events AS r
                  ON r.id = (
                      SELECT candidate.id
                      FROM media_account_risk_events AS candidate
                      WHERE candidate.account_id = m.account_id
                        AND candidate.active = 1
                        AND candidate.credential_epoch = u.credential_epoch
                      ORDER BY
                          CASE candidate.category
                              WHEN 'permanent_ban' THEN 1
                              WHEN 'temporary_restriction' THEN 2
                              WHEN 'verification_required' THEN 3
                              WHEN 'session_expired' THEN 4
                              ELSE 5
                          END,
                          candidate.occurred_at DESC,
                          candidate.id DESC
                      LIMIT 1
                  )
                WHERE m.state IN ('watching', 'checking')
                  AND j.status = 'need_action'
                  AND j.reconciliation_required = 1
                  AND m.account_id = j.account_id
                ORDER BY m.publish_job_id
                """
            ).fetchall()
            for row in rows:
                account_id = int(row["account_id"])
                account_epoch = int(row["credential_epoch"])
                risk_category = str(row["risk_category"] or "")
                risk_is_frozen = risk_category in FROZEN_RISK_CATEGORIES
                if int(row["status"]) == 1 and not risk_is_frozen:
                    continue
                if risk_is_frozen:
                    risk = classify_media_account_risk(
                        row["risk_evidence"] or row["risk_reason"]
                    )
                    if risk is None or str(risk["category"]) != risk_category:
                        # Risk rows originate from the central classifier.  If
                        # a legacy/corrupt row no longer round-trips, fail
                        # closed without inventing a stronger public label.
                        risk = {
                            "category": risk_category,
                            "label": "账号风险待处理",
                            "reason": str(row["risk_reason"] or "账号当前存在平台风险。"),
                            "recommended_action": "请在媒体账户页面核对平台提示后处理。",
                        }
                    state = "action_required"
                    last_result = str(risk["category"])
                    message = f"{risk['reason']}{risk['recommended_action']}"
                    event_key = f"account-risk:{int(row['risk_id'])}"
                    category = "account_risk"
                    severity = "critical" if risk["category"] == "permanent_ban" else "error"
                    title = f"{risk['label']} · {_platform_name(row['platform'])}"
                else:
                    state = "paused"
                    last_result = "account_unavailable"
                    message = (
                        "账号已解绑，后台只读监测已停止。"
                        if int(row["status"]) == -2
                        else "账号当前未启用，后台只读监测已停止；请先检查账号状态并重新登录。"
                    )
                    event_key = f"job:{int(row['publish_job_id'])}:account-unavailable"
                    category = "manual_check"
                    severity = "error"
                    title = f"{_platform_name(row['platform'])}账号不可用于监测"
                update_cas = """
                    AND EXISTS (
                        SELECT 1
                        FROM user_info AS current_account
                        WHERE current_account.id = publish_review_monitors.account_id
                          AND current_account.status = ?
                          AND current_account.credential_epoch = ?
                    )
                    AND EXISTS (
                        SELECT 1
                        FROM publish_jobs AS current_job
                        WHERE current_job.id = publish_review_monitors.publish_job_id
                          AND current_job.account_id = publish_review_monitors.account_id
                          AND current_job.status = 'need_action'
                          AND current_job.reconciliation_required = 1
                    )
                """
                update_parameters: list[Any] = [
                    state,
                    last_result,
                    message[:500],
                    timestamp,
                    timestamp,
                    int(row["publish_job_id"]),
                    account_id,
                    int(row["status"]),
                    account_epoch,
                ]
                if risk_is_frozen:
                    update_cas += """
                        AND EXISTS (
                            SELECT 1
                            FROM media_account_risk_events AS current_risk
                            WHERE current_risk.id = ?
                              AND current_risk.account_id = ?
                              AND current_risk.active = 1
                              AND current_risk.credential_epoch = ?
                        )
                    """
                    update_parameters.extend(
                        [int(row["risk_id"]), account_id, account_epoch]
                    )
                cursor = conn.execute(
                    f"""
                    UPDATE publish_review_monitors
                    SET state = ?, next_check_at = NULL,
                        last_result = ?, last_message = ?,
                        completed_at = ?, updated_at = ?
                    WHERE publish_job_id = ?
                      AND account_id = ?
                      AND state IN ('watching', 'checking')
                      {update_cas}
                    """,
                    update_parameters,
                )
                if cursor.rowcount == 1:
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO publish_review_alerts (
                            event_key, category, severity, title, message,
                            publish_job_id, account_id, result_url, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, '', ?)
                        """,
                        (
                            event_key[:160],
                            category,
                            severity[:20],
                            title[:160],
                            message[:1000],
                            int(row["publish_job_id"]),
                            account_id,
                            timestamp,
                        ),
                    )
                    paused_count += 1
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return paused_count


def _snapshot_account_is_eligible(
    database_path: str | Path,
    snapshot: Mapping[str, Any],
) -> bool:
    account_id = int(snapshot["account_id"])
    with closing(_connect(database_path)) as conn:
        row = conn.execute(
            "SELECT status FROM user_info WHERE id = ?",
            (account_id,),
        ).fetchone()
    if row is None or int(row["status"]) != 1:
        return False
    risk = get_active_media_account_risks(database_path, [account_id]).get(account_id)
    return not bool(risk and risk.get("frozen"))


def _snapshot_account_credential_is_current(
    database_path: str | Path,
    snapshot: Mapping[str, Any],
) -> bool:
    """Compare only a monotonic integer; never expose or hash credentials."""

    with closing(_connect(database_path)) as conn:
        _ensure_schema(conn)
        row = conn.execute(
            """
            SELECT credential_epoch
            FROM user_info
            WHERE id = ? AND status != -2
            """,
            (int(snapshot["account_id"]),),
        ).fetchone()
    return bool(
        row is not None
        and int(row["credential_epoch"])
        == int(snapshot["account_credential_epoch"])
    )


def _claim_snapshot_is_current(
    database_path: str | Path,
    snapshot: Mapping[str, Any],
) -> bool:
    """Return whether this exact claimed attempt may still apply evidence.

    Job state_version protects the publish state while observed_job_version
    binds the durable monitor to that same publish attempt.  The monitor claim
    state and attempt counter additionally prevent a verifier that outlived a
    stale-claim recovery from racing a newer check of the same job version.
    """

    job_id = int(snapshot["job_id"])
    with closing(_connect(database_path)) as conn:
        _ensure_schema(conn)
        row = conn.execute(
            """
            SELECT j.state_version, j.account_id AS job_account_id,
                   u.credential_epoch AS account_credential_epoch,
                   m.observed_job_version, m.state AS monitor_state,
                   m.observed_account_epoch, m.attempt_count,
                   m.account_id AS monitor_account_id
            FROM publish_jobs AS j
            JOIN user_info AS u ON u.id = j.account_id
            LEFT JOIN publish_review_monitors AS m
              ON m.publish_job_id = j.id
            WHERE j.id = ?
            """,
            (job_id,),
        ).fetchone()
    if row is None or row["observed_job_version"] is None:
        return False
    current_version = int(row["state_version"])
    return (
        int(snapshot["state_version"]) == current_version
        and int(snapshot["observed_job_version"]) == current_version
        and int(row["observed_job_version"]) == current_version
        and int(snapshot["account_credential_epoch"])
        == int(row["account_credential_epoch"])
        and int(row["observed_account_epoch"])
        == int(row["account_credential_epoch"])
        and str(row["monitor_state"]) == "checking"
        and int(row["attempt_count"]) == int(snapshot["attempt_count"])
        and int(row["monitor_account_id"]) == int(snapshot["account_id"])
        and int(row["job_account_id"]) == int(snapshot["account_id"])
    )


def _discard_stale_account_check_result(
    database_path: str | Path,
    *,
    request_snapshot: Mapping[str, Any],
    checked_at: datetime,
) -> dict[str, Any]:
    """Drop evidence from an obsolete credential generation, locally only."""

    job_id = int(request_snapshot["job_id"])
    timestamp = _format_timestamp(checked_at)
    with closing(_connect(database_path)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            _ensure_schema(conn)
            row = conn.execute(
                """
                SELECT j.status, j.reconciliation_required, j.demo,
                       j.state_version, j.account_id,
                       u.credential_epoch,
                       m.observed_job_version, m.observed_account_epoch,
                       m.account_id AS monitor_account_id,
                       m.state, m.attempt_count, m.next_check_at
                FROM publish_jobs AS j
                JOIN user_info AS u ON u.id = j.account_id
                LEFT JOIN publish_review_monitors AS m
                  ON m.publish_job_id = j.id
                WHERE j.id = ?
                """,
                (job_id,),
            ).fetchone()
            if row is None:
                conn.commit()
                return {
                    "job_id": job_id,
                    "state": "paused",
                    "stale_account_result_discarded": True,
                    "message": "账号或任务已变化，旧会话核验结果已丢弃",
                }

            needs_monitor = (
                str(row["status"]) == "need_action"
                and bool(row["reconciliation_required"])
                and not bool(row["demo"])
                and row["account_id"] is not None
            )
            if needs_monitor and row["observed_job_version"] is not None:
                current_job_version = int(row["state_version"])
                current_account_epoch = int(row["credential_epoch"])
                observed_job_version = int(row["observed_job_version"])
                observed_account_epoch = int(row["observed_account_epoch"])
                should_reset = (
                    current_job_version > observed_job_version
                    or (
                        current_job_version == observed_job_version
                        and (
                            int(row["account_id"]) != int(row["monitor_account_id"])
                            or current_account_epoch > observed_account_epoch
                        )
                    )
                )
                if should_reset:
                    conn.execute(
                        """
                        UPDATE publish_review_monitors
                        SET account_id = ?, state = 'watching',
                            attempt_count = 0, next_check_at = ?,
                            last_checked_at = NULL, last_result = '',
                            last_message = '', observed_job_version = ?,
                            observed_account_epoch = ?, completed_at = NULL,
                            updated_at = ?
                        WHERE publish_job_id = ?
                          AND observed_job_version <= ?
                          AND observed_account_epoch <= ?
                        """,
                        (
                            int(row["account_id"]),
                            timestamp,
                            current_job_version,
                            current_account_epoch,
                            timestamp,
                            job_id,
                            current_job_version,
                            current_account_epoch,
                        ),
                    )
                current = conn.execute(
                    """
                    SELECT state, attempt_count, next_check_at
                    FROM publish_review_monitors
                    WHERE publish_job_id = ?
                    """,
                    (job_id,),
                ).fetchone()
                conn.commit()
                return {
                    "job_id": job_id,
                    "state": str(current["state"]),
                    "attempt_count": int(current["attempt_count"]),
                    "next_check_at": current["next_check_at"],
                    "stale_account_result_discarded": True,
                    "message": "账号已重新登录，旧会话核验结果已丢弃；当前会话保持监测",
                }

            terminal_state = "published" if str(row["status"]) == "success" else "paused"
            conn.execute(
                """
                UPDATE publish_review_monitors
                SET state = ?, next_check_at = NULL,
                    last_result = 'job_changed',
                    last_message = '账号或任务已变化，旧会话核验结果已丢弃',
                    completed_at = ?, updated_at = ?
                WHERE publish_job_id = ?
                """,
                (terminal_state, timestamp, timestamp, job_id),
            )
            conn.commit()
            return {
                "job_id": job_id,
                "state": terminal_state,
                "stale_account_result_discarded": True,
                "message": "账号或任务已变化，旧会话核验结果已丢弃",
            }
        except Exception:
            conn.rollback()
            raise


def _discard_stale_check_result(
    database_path: str | Path,
    *,
    request_snapshot: Mapping[str, Any],
    checked_at: datetime,
) -> dict[str, Any]:
    """Discard old evidence and preserve/reset the current monitor version.

    This helper is intentionally local-only.  In particular it never calls a
    publish-job reconciliation function and never retries a submission.
    """

    job_id = int(request_snapshot["job_id"])
    timestamp = _format_timestamp(checked_at)
    fallback_max_attempts = int(request_snapshot.get("max_attempts") or DEFAULT_MAX_ATTEMPTS)
    with closing(_connect(database_path)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            _ensure_schema(conn)
            job = conn.execute(
                """
                SELECT j.id, j.account_id, j.status,
                       j.reconciliation_required, j.demo, j.state_version,
                       u.credential_epoch
                FROM publish_jobs AS j
                LEFT JOIN user_info AS u ON u.id = j.account_id
                WHERE j.id = ?
                """,
                (job_id,),
            ).fetchone()
            if job is None:
                conn.execute(
                    """
                    UPDATE publish_review_monitors
                    SET state = 'paused', next_check_at = NULL,
                        last_result = 'job_missing',
                        last_message = '本地发布任务已不存在，监测已停止',
                        completed_at = ?, updated_at = ?
                    WHERE publish_job_id = ?
                    """,
                    (timestamp, timestamp, job_id),
                )
                conn.commit()
                return {
                    "job_id": job_id,
                    "state": "paused",
                    "stale_result_discarded": True,
                    "message": "旧核验结果已丢弃；本地任务不存在",
                }

            current_version = int(job["state_version"])
            needs_monitor = (
                str(job["status"]) == "need_action"
                and bool(job["reconciliation_required"])
                and not bool(job["demo"])
                and job["account_id"] is not None
            )
            monitor = conn.execute(
                """
                SELECT * FROM publish_review_monitors
                WHERE publish_job_id = ?
                """,
                (job_id,),
            ).fetchone()

            if needs_monitor:
                if monitor is None:
                    conn.execute(
                        """
                        INSERT INTO publish_review_monitors (
                            publish_job_id, account_id, state, attempt_count,
                            max_attempts, next_check_at, observed_job_version,
                            observed_account_epoch,
                            created_at, updated_at
                        ) VALUES (?, ?, 'watching', 0, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            job_id,
                            int(job["account_id"]),
                            fallback_max_attempts,
                            timestamp,
                            current_version,
                            int(job["credential_epoch"]),
                            timestamp,
                            timestamp,
                        ),
                    )
                elif (
                    current_version > int(monitor["observed_job_version"])
                    or (
                        current_version == int(monitor["observed_job_version"])
                        and (
                            int(job["account_id"]) != int(monitor["account_id"])
                            or int(job["credential_epoch"])
                            > int(monitor["observed_account_epoch"])
                        )
                    )
                ):
                    conn.execute(
                        """
                        UPDATE publish_review_monitors
                        SET account_id = ?, state = 'watching',
                            attempt_count = 0, next_check_at = ?,
                            last_checked_at = NULL, last_result = '',
                            last_message = '', observed_job_version = ?,
                            observed_account_epoch = ?,
                            completed_at = NULL, updated_at = ?
                        WHERE publish_job_id = ?
                          AND (
                              observed_job_version < ?
                              OR (
                                  observed_job_version = ?
                                  AND (
                                      account_id != ?
                                      OR (
                                          account_id = ?
                                          AND observed_account_epoch < ?
                                      )
                                  )
                              )
                          )
                        """,
                        (
                            int(job["account_id"]),
                            timestamp,
                            current_version,
                            int(job["credential_epoch"]),
                            timestamp,
                            job_id,
                            current_version,
                            current_version,
                            int(job["account_id"]),
                            int(job["account_id"]),
                            int(job["credential_epoch"]),
                        ),
                    )
                # Equal means a concurrent worker already owns or backed off
                # the current version.  Greater is defensive future state.
                # Both must be preserved verbatim.
                current_monitor = conn.execute(
                    """
                    SELECT state, attempt_count, next_check_at,
                           observed_job_version
                    FROM publish_review_monitors
                    WHERE publish_job_id = ?
                    """,
                    (job_id,),
                ).fetchone()
                conn.commit()
                return {
                    "job_id": job_id,
                    "state": str(current_monitor["state"]),
                    "attempt_count": int(current_monitor["attempt_count"]),
                    "next_check_at": current_monitor["next_check_at"],
                    "observed_job_version": int(current_monitor["observed_job_version"]),
                    "stale_result_discarded": True,
                    "message": "任务版本已变化，旧核验结果已丢弃；当前版本保持监测",
                }

            terminal_state = "published" if str(job["status"]) == "success" else "paused"
            conn.execute(
                """
                UPDATE publish_review_monitors
                SET state = ?, next_check_at = NULL,
                    last_result = 'job_changed',
                    last_message = '任务已由其他操作收口，旧核验结果已丢弃',
                    completed_at = ?, updated_at = ?
                WHERE publish_job_id = ?
                  AND observed_job_version <= ?
                """,
                (terminal_state, timestamp, timestamp, job_id, current_version),
            )
            conn.commit()
            return {
                "job_id": job_id,
                "state": terminal_state,
                "stale_result_discarded": True,
                "message": "任务状态已变化，旧核验结果已丢弃",
            }
        except Exception:
            conn.rollback()
            raise


def _apply_check_result(
    database_path: str | Path,
    *,
    request_snapshot: Mapping[str, Any],
    result: Mapping[str, Any],
    checked_at: datetime,
) -> dict[str, Any]:
    job_id = int(request_snapshot["job_id"])
    account_id = int(request_snapshot["account_id"])
    attempt_count = int(request_snapshot["attempt_count"])
    max_attempts = int(request_snapshot["max_attempts"])
    state = str(result["state"])
    message = str(result.get("message") or "").strip()[:500]
    result_url = str(result.get("result_url") or "").strip()
    evidence = str(result.get("evidence") or "").strip()[:500]
    timestamp = _format_timestamp(checked_at)

    try:
        current_job = get_publish_job(database_path, job_id)
    except Exception:
        _finish_monitor(
            database_path,
            job_id=job_id,
            monitor_state="paused",
            last_result="job_missing",
            message="本地发布任务已不存在，监测已停止",
            checked_at=timestamp,
        )
        return {"job_id": job_id, "state": "paused", "message": "本地任务不存在"}

    if not _snapshot_account_credential_is_current(
        database_path, request_snapshot
    ):
        return _discard_stale_account_check_result(
            database_path,
            request_snapshot=request_snapshot,
            checked_at=checked_at,
        )

    claim_job_version = int(request_snapshot["state_version"])
    claim_observed_version = int(request_snapshot["observed_job_version"])
    current_job_version = int(current_job["state_version"])
    if (
        claim_job_version != current_job_version
        or claim_observed_version != current_job_version
        or not _claim_snapshot_is_current(database_path, request_snapshot)
    ):
        return _discard_stale_check_result(
            database_path,
            request_snapshot=request_snapshot,
            checked_at=checked_at,
        )

    if (
        current_job["status"] != "need_action"
        or not current_job.get("reconciliation_required", False)
    ):
        terminal_state = "published" if current_job["status"] == "success" else "paused"
        finished = _finish_monitor(
            database_path,
            job_id=job_id,
            monitor_state=terminal_state,
            last_result="job_changed",
            message="任务已由其他操作收口，监测没有覆盖新状态",
            checked_at=timestamp,
            expected_observed_job_version=claim_observed_version,
            expected_attempt_count=attempt_count,
            expected_current_job_version=claim_job_version,
        )
        if not finished:
            return _discard_stale_check_result(
                database_path,
                request_snapshot=request_snapshot,
                checked_at=checked_at,
            )
        return {"job_id": job_id, "state": terminal_state, "message": "任务状态已变化"}

    if state == "published":
        try:
            reconciled = reconcile_publish_job_platform_success(
                database_path,
                job_id,
                message=(
                    "后台只读监测已核验平台发布结果："
                    f"{message or evidence or '精确标题与平台状态匹配'}"
                ),
                result_url=result_url,
                expected_state_version=claim_job_version,
            )
        except (ConcurrentPublishJobUpdateError, InvalidPublishJobTransitionError):
            return _discard_stale_check_result(
                database_path,
                request_snapshot=request_snapshot,
                checked_at=checked_at,
            )
        finished = _finish_monitor(
            database_path,
            job_id=job_id,
            monitor_state="published",
            last_result="published",
            message=message or "平台审核通过",
            checked_at=timestamp,
            expected_observed_job_version=claim_observed_version,
            expected_attempt_count=attempt_count,
            expected_current_job_version=int(reconciled["state_version"]),
        )
        if not finished:
            return _discard_stale_check_result(
                database_path,
                request_snapshot=request_snapshot,
                checked_at=checked_at,
            )
        _insert_alert(
            database_path,
            event_key=f"job:{job_id}:published:v{reconciled['state_version']}",
            category="published",
            severity="success",
            title=f"{_platform_name(current_job['platform'])}内容已通过并发布",
            message=message or "后台只读监测已取得平台发布证据。",
            job_id=job_id,
            account_id=account_id,
            result_url=str(reconciled.get("result_url") or result_url),
            created_at=timestamp,
        )
        return {
            "job_id": job_id,
            "state": "published",
            "result_url": str(reconciled.get("result_url") or result_url),
            "message": message,
        }

    if state == "rejected":
        try:
            reconciled = reconcile_publish_job_failure(
                database_path,
                job_id,
                message=(
                    "后台只读监测发现平台明确未通过："
                    f"{message or evidence or '平台状态为未通过'}"
                ),
                expected_state_version=claim_job_version,
            )
        except (ConcurrentPublishJobUpdateError, InvalidPublishJobTransitionError):
            return _discard_stale_check_result(
                database_path,
                request_snapshot=request_snapshot,
                checked_at=checked_at,
            )
        finished = _finish_monitor(
            database_path,
            job_id=job_id,
            monitor_state="rejected",
            last_result="rejected",
            message=message or "平台明确未通过",
            checked_at=timestamp,
            expected_observed_job_version=claim_observed_version,
            expected_attempt_count=attempt_count,
            expected_current_job_version=int(reconciled["state_version"]),
        )
        if not finished:
            return _discard_stale_check_result(
                database_path,
                request_snapshot=request_snapshot,
                checked_at=checked_at,
            )
        _insert_alert(
            database_path,
            event_key=f"job:{job_id}:rejected:v{reconciled['state_version']}",
            category="rejected",
            severity="error",
            title=f"{_platform_name(current_job['platform'])}内容未通过",
            message=message or "平台管理页显示该内容未通过，请按平台原因处理。",
            job_id=job_id,
            account_id=account_id,
            created_at=timestamp,
        )
        return {"job_id": job_id, "state": "rejected", "message": message}

    if state == "risk":
        detail = evidence or message
        try:
            risk = record_media_account_risk(
                database_path,
                account_id,
                source="publish",
                error_text=detail,
                occurred_at=checked_at,
                expected_credential_epoch=int(
                    request_snapshot["account_credential_epoch"]
                ),
                expected_publish_job_id=job_id,
                expected_publish_job_state_version=claim_job_version,
            )
        except ConcurrentMediaAccountCredentialUpdateError:
            return _discard_stale_account_check_result(
                database_path,
                request_snapshot=request_snapshot,
                checked_at=checked_at,
            )
        except ConcurrentMediaAccountPublishJobUpdateError:
            return _discard_stale_check_result(
                database_path,
                request_snapshot=request_snapshot,
                checked_at=checked_at,
            )
        if risk is None:
            # A verifier may only claim ``risk`` with text recognized by the
            # central classifier.  Otherwise it is an unknown observation.
            return _schedule_next_check(
                database_path,
                request_snapshot=request_snapshot,
                job_id=job_id,
                attempt_count=attempt_count,
                max_attempts=max_attempts,
                last_result="error",
                message="平台风险提示无法可靠分类，等待下次只读核验",
                checked_at=checked_at,
            )
        risk_finish = _finish_monitor_for_current_account_risk(
            database_path,
            request_snapshot=request_snapshot,
            risk=risk,
            platform=current_job["platform"],
            checked_at=timestamp,
        )
        if risk_finish == "account_changed":
            return _discard_stale_account_check_result(
                database_path,
                request_snapshot=request_snapshot,
                checked_at=checked_at,
            )
        if risk_finish != "finished":
            return _discard_stale_check_result(
                database_path,
                request_snapshot=request_snapshot,
                checked_at=checked_at,
            )
        return {
            "job_id": job_id,
            "state": "action_required",
            "risk": risk,
            "message": risk["reason"],
        }

    if state == "unsupported":
        finished = _finish_monitor(
            database_path,
            job_id=job_id,
            monitor_state="paused",
            last_result="unsupported",
            message=message or "该平台暂不支持安全自动核验",
            checked_at=timestamp,
            expected_observed_job_version=claim_observed_version,
            expected_attempt_count=attempt_count,
            expected_current_job_version=claim_job_version,
        )
        if not finished:
            return _discard_stale_check_result(
                database_path,
                request_snapshot=request_snapshot,
                checked_at=checked_at,
            )
        _insert_alert(
            database_path,
            event_key=f"job:{job_id}:manual-check",
            category="manual_check",
            severity="warning",
            title=f"{_platform_name(current_job['platform'])}需要人工核验",
            message=message or "该平台没有可安全读取的作品结果入口。",
            job_id=job_id,
            account_id=account_id,
            created_at=timestamp,
        )
        return {"job_id": job_id, "state": "paused", "message": message}

    return _schedule_next_check(
        database_path,
        request_snapshot=request_snapshot,
        job_id=job_id,
        attempt_count=attempt_count,
        max_attempts=max_attempts,
        last_result=state,
        message=message or _default_pending_message(state),
        checked_at=checked_at,
    )


def _schedule_next_check(
    database_path: str | Path,
    *,
    request_snapshot: Mapping[str, Any],
    job_id: int,
    attempt_count: int,
    max_attempts: int,
    last_result: str,
    message: str,
    checked_at: datetime,
) -> dict[str, Any]:
    timestamp = _format_timestamp(checked_at)
    if not _claim_snapshot_is_current(database_path, request_snapshot):
        return _discard_stale_check_result(
            database_path,
            request_snapshot=request_snapshot,
            checked_at=checked_at,
        )
    if attempt_count >= max_attempts:
        finished = _finish_monitor(
            database_path,
            job_id=job_id,
            monitor_state="exhausted",
            last_result=last_result,
            message=message,
            checked_at=timestamp,
            expected_observed_job_version=int(request_snapshot["observed_job_version"]),
            expected_attempt_count=attempt_count,
            expected_current_job_version=int(request_snapshot["state_version"]),
        )
        if not finished:
            return _discard_stale_check_result(
                database_path,
                request_snapshot=request_snapshot,
                checked_at=checked_at,
            )
        job = get_publish_job(database_path, job_id)
        _insert_alert(
            database_path,
            event_key=f"job:{job_id}:monitor-exhausted:{attempt_count}",
            category="monitor_exhausted",
            severity="warning",
            title=f"{_platform_name(job['platform'])}自动核验已到上限",
            message="任务仍保持待核验且从未自动重发；请打开平台后台人工确认，或手动恢复监测。",
            job_id=job_id,
            account_id=job.get("account_id"),
            created_at=timestamp,
        )
        return {
            "job_id": job_id,
            "state": "exhausted",
            "attempt_count": attempt_count,
            "message": message,
        }

    delay = _BACKOFF_SECONDS[min(max(attempt_count - 1, 0), len(_BACKOFF_SECONDS) - 1)]
    next_check = _format_timestamp(checked_at + timedelta(seconds=delay))
    with closing(_connect(database_path)) as conn:
        with conn:
            _ensure_schema(conn)
            cursor = conn.execute(
                """
                UPDATE publish_review_monitors
                SET state = 'watching', next_check_at = ?,
                    last_result = ?, last_message = ?, updated_at = ?
                WHERE publish_job_id = ? AND state = 'checking'
                  AND observed_job_version = ?
                  AND attempt_count = ?
                  AND EXISTS (
                      SELECT 1 FROM publish_jobs AS j
                      WHERE j.id = publish_review_monitors.publish_job_id
                        AND j.state_version = ?
                        AND j.status = 'need_action'
                        AND j.reconciliation_required = 1
                  )
                """,
                (
                    next_check,
                    last_result,
                    message[:500],
                    timestamp,
                    job_id,
                    int(request_snapshot["observed_job_version"]),
                    attempt_count,
                    int(request_snapshot["state_version"]),
                ),
            )
    if cursor.rowcount != 1:
        return _discard_stale_check_result(
            database_path,
            request_snapshot=request_snapshot,
            checked_at=checked_at,
        )
    return {
        "job_id": job_id,
        "state": "watching",
        "attempt_count": attempt_count,
        "next_check_at": next_check,
        "message": message,
    }


def _finish_monitor(
    database_path: str | Path,
    *,
    job_id: int,
    monitor_state: str,
    last_result: str,
    message: str,
    checked_at: str,
    expected_observed_job_version: int | None = None,
    expected_attempt_count: int | None = None,
    expected_current_job_version: int | None = None,
) -> bool:
    if monitor_state not in _MONITOR_STATES - {"watching", "checking"}:
        raise ValueError("监测终态无效")
    with closing(_connect(database_path)) as conn:
        with conn:
            _ensure_schema(conn)
            where = "WHERE publish_job_id = ?"
            parameters: list[Any] = [
                monitor_state,
                str(last_result or "")[:80],
                str(message or "")[:500],
                checked_at,
                checked_at,
                job_id,
            ]
            if expected_observed_job_version is not None:
                where += " AND observed_job_version = ?"
                parameters.append(int(expected_observed_job_version))
            if expected_attempt_count is not None:
                where += " AND attempt_count = ? AND state = 'checking'"
                parameters.append(int(expected_attempt_count))
            if expected_current_job_version is not None:
                where += (
                    " AND EXISTS (SELECT 1 FROM publish_jobs AS j "
                    "WHERE j.id = publish_review_monitors.publish_job_id "
                    "AND j.state_version = ?)"
                )
                parameters.append(int(expected_current_job_version))
            cursor = conn.execute(
                f"""
                UPDATE publish_review_monitors
                SET state = ?, next_check_at = NULL, last_result = ?,
                    last_message = ?, completed_at = ?, updated_at = ?
                {where}
                """,
                parameters,
            )
    return cursor.rowcount == 1


def _finish_monitor_for_current_account_risk(
    database_path: str | Path,
    *,
    request_snapshot: Mapping[str, Any],
    risk: Mapping[str, Any],
    platform: Any,
    checked_at: str,
) -> str:
    """Atomically bind a risk terminal state and alert to its claim context.

    ``record_media_account_risk`` commits the classified evidence first.  A
    successful re-login may then advance ``credential_epoch`` before monitor
    finalization.  This second transaction therefore rechecks the exact risk
    row, account generation, job generation and monitor claim before changing
    either operator-visible state.  Alert creation shares that transaction.
    """

    job_id = int(request_snapshot["job_id"])
    account_id = int(request_snapshot["account_id"])
    account_epoch = int(request_snapshot["account_credential_epoch"])
    job_version = int(request_snapshot["state_version"])
    observed_job_version = int(request_snapshot["observed_job_version"])
    attempt_count = int(request_snapshot["attempt_count"])
    risk_id = int(risk["id"])
    risk_category = str(risk["category"])
    with closing(_connect(database_path)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            _ensure_schema(conn)
            cursor = conn.execute(
                """
                UPDATE publish_review_monitors
                SET state = 'action_required', next_check_at = NULL,
                    last_result = ?, last_message = ?,
                    completed_at = ?, updated_at = ?
                WHERE publish_job_id = ?
                  AND account_id = ?
                  AND state = 'checking'
                  AND attempt_count = ?
                  AND observed_job_version = ?
                  AND observed_account_epoch = ?
                  AND EXISTS (
                      SELECT 1
                      FROM publish_jobs AS current_job
                      WHERE current_job.id = publish_review_monitors.publish_job_id
                        AND current_job.account_id = publish_review_monitors.account_id
                        AND current_job.state_version = ?
                        AND current_job.status = 'need_action'
                        AND current_job.reconciliation_required = 1
                  )
                  AND EXISTS (
                      SELECT 1
                      FROM user_info AS current_account
                      WHERE current_account.id = publish_review_monitors.account_id
                        AND current_account.credential_epoch = ?
                        AND current_account.status != -2
                  )
                  AND EXISTS (
                      SELECT 1
                      FROM media_account_risk_events AS current_risk
                      JOIN user_info AS risk_account
                        ON risk_account.id = current_risk.account_id
                      WHERE current_risk.id = ?
                        AND current_risk.account_id = publish_review_monitors.account_id
                        AND current_risk.category = ?
                        AND current_risk.active = 1
                        AND current_risk.credential_epoch = ?
                        AND current_risk.credential_epoch = risk_account.credential_epoch
                  )
                """,
                (
                    risk_category,
                    str(risk["reason"])[:500],
                    checked_at,
                    checked_at,
                    job_id,
                    account_id,
                    attempt_count,
                    observed_job_version,
                    account_epoch,
                    job_version,
                    account_epoch,
                    risk_id,
                    risk_category,
                    account_epoch,
                ),
            )
            if cursor.rowcount == 1:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO publish_review_alerts (
                        event_key, category, severity, title, message,
                        publish_job_id, account_id, result_url, created_at
                    ) VALUES (?, 'account_risk', ?, ?, ?, ?, ?, '', ?)
                    """,
                    (
                        f"account-risk:{risk_id}",
                        "critical" if risk_category == "permanent_ban" else "error",
                        f"{risk['label']} · {_platform_name(platform)}"[:160],
                        f"{risk['reason']}{risk['recommended_action']}"[:1000],
                        job_id,
                        account_id,
                        checked_at,
                    ),
                )
                conn.commit()
                return "finished"

            account = conn.execute(
                """
                SELECT credential_epoch
                FROM user_info
                WHERE id = ? AND status != -2
                """,
                (account_id,),
            ).fetchone()
            conn.commit()
            if account is None or int(account["credential_epoch"]) != account_epoch:
                return "account_changed"
            return "claim_changed"
        except Exception:
            conn.rollback()
            raise


def _recover_stale_claims(
    database_path: str | Path,
    *,
    now: datetime,
) -> int:
    threshold = _format_timestamp(now - timedelta(minutes=10))
    timestamp = _format_timestamp(now)
    with closing(_connect(database_path)) as conn:
        with conn:
            _ensure_schema(conn)
            cursor = conn.execute(
                """
                UPDATE publish_review_monitors
                SET state = 'watching', next_check_at = ?,
                    last_result = 'interrupted',
                    last_message = '上次只读核验被中断，已恢复监测；未触发发布',
                    updated_at = ?
                WHERE state = 'checking' AND updated_at < ?
                """,
                (timestamp, timestamp, threshold),
            )
    return int(cursor.rowcount)


def _normalize_check_result(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise PublishReviewMonitorError("只读核验器必须返回结构化结果")
    state = str(value.get("state") or "").strip().lower()
    if state not in _CHECK_STATES:
        raise PublishReviewMonitorError("只读核验器返回了不支持的状态")
    result_url = str(value.get("result_url") or value.get("url") or "").strip()
    message = str(value.get("message") or "").strip()
    evidence = str(value.get("evidence") or "").strip()
    if len(result_url) > 2048 or len(message) > 2000 or len(evidence) > 2000:
        raise PublishReviewMonitorError("只读核验结果超出安全长度")
    if state == "published" and not result_url and value.get("url_optional") is not True:
        raise PublishReviewMonitorError("发布通过结果缺少可核验作品链接")
    if state == "risk" and classify_media_account_risk(evidence or message) is None:
        raise PublishReviewMonitorError("账号风险结果缺少可分类的平台证据")
    return {
        "state": state,
        "result_url": result_url,
        "message": message,
        "evidence": evidence,
    }


def _sync_terminal_publish_review_state(
    database_path: str | Path,
    *,
    now: datetime | str | None = None,
) -> int:
    """Synchronize terminal jobs with their obsolete local monitor state.

    Reconciliation is committed by ``publish_job_service`` before this service
    can finish the monitor and insert its alert.  A process interruption in
    either gap must not cause another platform read (and especially not another
    submission).  Monitor-origin prefixes remain the provenance gate for
    creating a new published/rejected alert.  Human reconciliation and other
    terminal transitions still close stale ``watching``/``exhausted`` rows and
    superseded monitor alerts, but they never manufacture a platform verdict.
    """

    timestamp = _normalize_timestamp(now)
    success_prefix = "后台只读监测已核验平台发布结果："
    rejected_prefix = "后台只读监测发现平台明确未通过："
    changed = 0
    with closing(_connect(database_path)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            _ensure_schema(conn)
            rows = conn.execute(
                """
                SELECT m.publish_job_id, m.account_id, m.state,
                       m.last_result, m.completed_at,
                       m.observed_job_version,
                       j.platform, j.status, j.message, j.result_url,
                       j.state_version, j.finished_at
                FROM publish_review_monitors AS m
                JOIN publish_jobs AS j ON j.id = m.publish_job_id
                WHERE j.reconciliation_required = 0
                  AND j.status IN ('success', 'failed')
                ORDER BY m.publish_job_id
                """,
            ).fetchall()
            for row in rows:
                terminal_version = int(row["state_version"])
                # A normal reconciliation advances the job exactly once after
                # the monitor claim.  Never let an older terminal job rewrite
                # a defensive/future monitor generation.
                if int(row["observed_job_version"]) >= terminal_version:
                    continue
                published = str(row["status"]) == "success"
                raw_message = str(row["message"] or "")
                monitor_origin = raw_message.startswith(
                    success_prefix if published else rejected_prefix
                )
                if published:
                    monitor_state = "published"
                    last_result = "published"
                elif monitor_origin:
                    monitor_state = "rejected"
                    last_result = "rejected"
                else:
                    # A human-confirmed "not published" result, a pre-submit
                    # failure, or any other terminal failure is not evidence of
                    # platform rejection.  Close the obsolete watcher without
                    # inventing that stronger claim.
                    monitor_state = "paused"
                    last_result = "manual_reconciled"
                event_time = str(row["finished_at"] or timestamp)
                message = raw_message[:1000]
                result_url = str(row["result_url"] or "") if published else ""

                monitor_update = conn.execute(
                    """
                    UPDATE publish_review_monitors
                    SET state = ?, next_check_at = NULL,
                        last_result = ?, last_message = ?,
                        observed_job_version = ?,
                        completed_at = COALESCE(completed_at, ?),
                        updated_at = ?
                    WHERE publish_job_id = ?
                      AND observed_job_version < ?
                      AND (
                          state != ? OR last_result != ?
                          OR completed_at IS NULL
                      )
                    """,
                    (
                        monitor_state,
                        last_result,
                        message[:500],
                        terminal_version,
                        event_time,
                        timestamp,
                        int(row["publish_job_id"]),
                        terminal_version,
                        monitor_state,
                        last_result,
                    ),
                )
                acknowledged = conn.execute(
                    """
                    UPDATE publish_review_alerts
                    SET acknowledged_at = COALESCE(acknowledged_at, ?)
                    WHERE publish_job_id = ?
                      AND acknowledged_at IS NULL
                      AND category IN ('monitor_exhausted', 'manual_check')
                    """,
                    (timestamp, int(row["publish_job_id"])),
                )
                alert_count = 0
                if monitor_origin:
                    category = "published" if published else "rejected"
                    alert = conn.execute(
                        """
                        INSERT OR IGNORE INTO publish_review_alerts (
                            event_key, category, severity, title, message,
                            publish_job_id, account_id, result_url, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            (
                                f"job:{int(row['publish_job_id'])}:"
                                f"{category}:v{terminal_version}"
                            ),
                            category,
                            "success" if published else "error",
                            (
                                f"{_platform_name(row['platform'])}内容已通过并发布"
                                if published
                                else f"{_platform_name(row['platform'])}内容未通过"
                            ),
                            message,
                            int(row["publish_job_id"]),
                            int(row["account_id"]),
                            result_url,
                            event_time,
                        ),
                    )
                    alert_count = int(alert.rowcount)
                changed += (
                    int(monitor_update.rowcount)
                    + int(acknowledged.rowcount)
                    + alert_count
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return changed


def _sync_account_risk_alerts(database_path: str | Path) -> int:
    """Backfill one alert per account from only its current credential epoch."""

    with closing(_connect(database_path)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            _ensure_schema(conn)
            inserted = conn.execute(
                """
                INSERT OR IGNORE INTO publish_review_alerts (
                    event_key, category, severity, title, message,
                    publish_job_id, account_id, result_url, created_at
                )
                SELECT
                    'account-risk:' || CAST(r.id AS TEXT),
                    'account_risk',
                    CASE r.category
                        WHEN 'permanent_ban' THEN 'critical'
                        ELSE 'error'
                    END,
                    CASE r.category
                        WHEN 'permanent_ban' THEN '账号永久封禁'
                        WHEN 'temporary_restriction' THEN '账号暂时受限'
                        WHEN 'verification_required' THEN '需要本人验证'
                        WHEN 'session_expired' THEN '登录会话已失效'
                        ELSE '网络检测失败'
                    END,
                    r.reason || CASE r.category
                        WHEN 'permanent_ban' THEN
                            '停止该账号的发布任务，前往平台安全中心查看原因并按官方流程申诉；申诉确认恢复后，再在软件中重新验证。软件不会自动换号。'
                        WHEN 'temporary_restriction' THEN
                            '暂停该账号的发布，查看平台通知中的限制期限或申诉入口；限制解除后再重新验证。'
                        WHEN 'verification_required' THEN
                            '在平台窗口中由账号本人完成验证，然后回到软件重新检测登录状态。'
                        WHEN 'session_expired' THEN
                            '重新登录该账号并完成平台要求的验证。'
                        ELSE
                            '检查网络和平台页面是否可访问后再检测；本次错误不会把账号判定为封禁。'
                    END,
                    NULL,
                    r.account_id,
                    '',
                    r.occurred_at
                FROM media_account_risk_events AS r
                JOIN user_info AS u ON u.id = r.account_id
                WHERE r.active = 1
                  AND r.credential_epoch = u.credential_epoch
                  AND r.id = (
                      SELECT candidate.id
                      FROM media_account_risk_events AS candidate
                      WHERE candidate.account_id = r.account_id
                        AND candidate.active = 1
                        AND candidate.credential_epoch = u.credential_epoch
                      ORDER BY
                          CASE candidate.category
                              WHEN 'permanent_ban' THEN 1
                              WHEN 'temporary_restriction' THEN 2
                              WHEN 'verification_required' THEN 3
                              WHEN 'session_expired' THEN 4
                              ELSE 5
                          END,
                          candidate.occurred_at DESC,
                          candidate.id DESC
                      LIMIT 1
                  )
                """
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return int(inserted.rowcount)


def _insert_alert(
    database_path: str | Path,
    *,
    event_key: str,
    category: str,
    severity: str,
    title: str,
    message: str,
    job_id: int | None = None,
    account_id: int | None = None,
    result_url: str = "",
    created_at: str,
) -> None:
    if category not in _ALERT_CATEGORIES:
        raise ValueError("监测提醒类型无效")
    with closing(_connect(database_path)) as conn:
        with conn:
            _ensure_schema(conn)
            conn.execute(
                """
                INSERT OR IGNORE INTO publish_review_alerts (
                    event_key, category, severity, title, message,
                    publish_job_id, account_id, result_url, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(event_key)[:160],
                    category,
                    str(severity or "warning")[:20],
                    str(title or "")[:160],
                    str(message or "")[:1000],
                    job_id,
                    account_id,
                    str(result_url or "")[:2048],
                    created_at,
                ),
            )


def _alert_exists(database_path: str | Path, event_key: str) -> bool:
    with closing(_connect(database_path)) as conn:
        _ensure_schema(conn)
        return conn.execute(
            "SELECT 1 FROM publish_review_alerts WHERE event_key = ?",
            (event_key,),
        ).fetchone() is not None


def _serialize_monitor(row: sqlite3.Row) -> dict[str, Any]:
    payload = dict(row)
    payload["publish_job_id"] = int(payload["publish_job_id"])
    payload["account_id"] = int(payload["account_id"])
    payload["attempt_count"] = int(payload["attempt_count"])
    payload["max_attempts"] = int(payload["max_attempts"])
    payload["observed_job_version"] = int(payload["observed_job_version"])
    # Internal credential generations are concurrency tokens, not product
    # data.  Do not expose them through monitor/overview APIs.
    payload.pop("observed_account_epoch", None)
    if "reconciliation_required" in payload:
        payload["reconciliation_required"] = bool(payload["reconciliation_required"])
    return payload


def _serialize_alert(row: sqlite3.Row) -> dict[str, Any]:
    payload = dict(row)
    payload["id"] = int(payload["id"])
    payload["publish_job_id"] = (
        int(payload["publish_job_id"]) if payload["publish_job_id"] is not None else None
    )
    payload["account_id"] = int(payload["account_id"]) if payload["account_id"] is not None else None
    payload["acknowledged"] = bool(payload.get("acknowledged_at"))
    return payload


def _ensure_schema(conn: sqlite3.Connection) -> None:
    ensure_media_account_risk_schema(conn)
    account_columns = {
        str(row[1]) for row in conn.execute("PRAGMA table_info(user_info)")
    }
    if account_columns and "credential_epoch" not in account_columns:
        conn.execute(
            "ALTER TABLE user_info ADD COLUMN credential_epoch "
            "INTEGER NOT NULL DEFAULT 1 CHECK (credential_epoch >= 1)"
        )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS publish_review_monitors (
            publish_job_id INTEGER PRIMARY KEY,
            account_id INTEGER NOT NULL,
            state TEXT NOT NULL CHECK (state IN (
                'watching', 'checking', 'published', 'rejected',
                'action_required', 'exhausted', 'paused'
            )),
            attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
            max_attempts INTEGER NOT NULL DEFAULT 12 CHECK (max_attempts BETWEEN 1 AND 50),
            next_check_at TEXT,
            last_checked_at TEXT,
            last_result TEXT NOT NULL DEFAULT '',
            last_message TEXT NOT NULL DEFAULT '',
            observed_job_version INTEGER NOT NULL DEFAULT 0,
            observed_account_epoch INTEGER NOT NULL DEFAULT 1
                CHECK (observed_account_epoch >= 1),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            completed_at TEXT,
            FOREIGN KEY (publish_job_id) REFERENCES publish_jobs(id) ON DELETE CASCADE,
            FOREIGN KEY (account_id) REFERENCES user_info(id) ON DELETE CASCADE
        )
        """
    )
    monitor_columns = {
        str(row[1])
        for row in conn.execute("PRAGMA table_info(publish_review_monitors)")
    }
    if "observed_account_epoch" not in monitor_columns:
        conn.execute(
            "ALTER TABLE publish_review_monitors "
            "ADD COLUMN observed_account_epoch INTEGER NOT NULL DEFAULT 1 "
            "CHECK (observed_account_epoch >= 1)"
        )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_publish_review_monitor_due
        ON publish_review_monitors(state, next_check_at, publish_job_id)
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS publish_review_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_key TEXT NOT NULL UNIQUE,
            category TEXT NOT NULL CHECK (category IN (
                'published', 'rejected', 'account_risk',
                'monitor_exhausted', 'manual_check'
            )),
            severity TEXT NOT NULL,
            title TEXT NOT NULL,
            message TEXT NOT NULL,
            publish_job_id INTEGER,
            account_id INTEGER,
            result_url TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            acknowledged_at TEXT,
            FOREIGN KEY (publish_job_id) REFERENCES publish_jobs(id) ON DELETE CASCADE,
            FOREIGN KEY (account_id) REFERENCES user_info(id) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_publish_review_alert_unread
        ON publish_review_alerts(acknowledged_at, created_at DESC, id DESC)
        """
    )


def _default_pending_message(state: str) -> str:
    return {
        "processing": "平台仍在处理或审核中",
        "not_found": "平台管理页尚未出现精确标题",
        "error": "本轮只读核验未取得可靠结论",
    }.get(state, "平台状态仍未确认")


def _platform_name(platform: Any) -> str:
    return {
        "zhihu": "知乎",
        "toutiao": "今日头条",
        "baijiahao": "百家号",
        "sohu": "搜狐号",
        "xiaohongshu": "小红书",
        "douyin": "抖音",
        "kuaishou": "快手",
        "bilibili": "Bilibili",
        "channels": "视频号",
        "tiktok": "TikTok",
        "weibo": "微博",
    }.get(str(platform or "").strip().lower(), str(platform or "未知平台"))


def _normalize_max_attempts(value: Any) -> int:
    normalized = _normalize_positive_id(value, field="max_attempts")
    if normalized > 50:
        raise ValueError("max_attempts 不能超过 50")
    return normalized


def _normalize_limit(value: Any, *, maximum: int = 20) -> int:
    normalized = _normalize_positive_id(value, field="limit")
    if normalized > maximum:
        raise ValueError(f"limit 不能超过 {maximum}")
    return normalized


def _normalize_positive_id(value: Any, *, field: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field} 必须是正整数")
    try:
        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} 必须是正整数") from exc
    if normalized <= 0:
        raise ValueError(f"{field} 必须是正整数")
    return normalized


def _normalize_timestamp(value: datetime | str | None) -> str:
    return _format_timestamp(_parse_timestamp(value))


def _parse_timestamp(value: datetime | str | None) -> datetime:
    if value is None:
        parsed = datetime.now(timezone.utc)
    elif isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("时间必须是 ISO 8601") from exc
    else:
        raise ValueError("时间必须是 datetime 或 ISO 8601")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _format_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _connect(database_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(Path(database_path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


__all__ = [
    "DEFAULT_BATCH_LIMIT",
    "DEFAULT_MAX_ATTEMPTS",
    "PUBLISH_REVIEW_MONITOR_VERSION",
    "PublishReviewMonitorError",
    "acknowledge_publish_review_alert",
    "get_publish_review_monitor",
    "get_publish_review_overview",
    "rearm_publish_review_monitor",
    "run_publish_review_monitor_tick",
    "seed_publish_review_monitors",
]
