from __future__ import annotations

import inspect
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from services.platform_capability_service import (
    BILIBILI_ACCOUNT_TYPE,
    BILIBILI_RUNTIME_DISABLED_MESSAGE,
    PLATFORM_CAPABILITIES,
)
from services.media_account_risk_service import (
    ConcurrentMediaAccountCredentialUpdateError,
    FROZEN_RISK_CATEGORIES,
    get_active_media_account_risk,
    get_active_media_account_risks,
    record_media_account_risk,
    resolve_recoverable_media_account_risks,
)


PLATFORM_DEFINITIONS = tuple(
    {
        "type": item["account_type"],
        "key": item["key"],
        "name": item["name"],
        "priority": item["priority"],
    }
    for item in PLATFORM_CAPABILITIES
)
_PLATFORM_BY_TYPE = {item["type"]: item for item in PLATFORM_DEFINITIONS}
DISCONNECTED_ACCOUNT_STATUS = -2


class MediaAccountNotFoundError(LookupError):
    """Raised when a media account id does not exist."""


class MediaAccountCheckError(RuntimeError):
    """Raised when a platform login check cannot produce a reliable result."""

    def __init__(self, message: str, *, risk_event: dict[str, Any] | None = None):
        super().__init__(message)
        self.risk_event = risk_event


class MediaAccountFrozenError(MediaAccountCheckError):
    """Raised before network access when an active risk freezes the account."""


class MediaAccountRuntimeDisabledError(MediaAccountCheckError):
    """Raised before touching a platform runtime disabled by policy."""


class MediaAccountDisconnectError(RuntimeError):
    """Raised when a local account binding cannot be removed safely."""


class MediaAccountInvalidCookiePathError(MediaAccountDisconnectError):
    """Raised when a stored session filename escapes the configured root."""


class MediaAccountBusyError(MediaAccountDisconnectError):
    """Raised when a real publish is currently using the account."""


def get_media_accounts_overview(
    database_path: str | Path,
    *,
    cookies_directory: str | Path,
) -> dict[str, Any]:
    cookie_dir = Path(cookies_directory)
    with closing(_connect(database_path)) as conn:
        rows = conn.execute(
            """
            SELECT id, type, filePath, userName, status, last_checked_at,
                   credential_epoch
            FROM user_info
            WHERE status != ?
            ORDER BY type, id
            """,
            (DISCONNECTED_ACCOUNT_STATUS,),
        ).fetchall()

    active_risks = get_active_media_account_risks(
        database_path,
        [int(row["id"]) for row in rows],
    )
    # Databases created before the durable risk ledger represented an expired
    # login only as ``user_info.status = 0``.  Backfill that state centrally so
    # every API/UI sees the same actionable session-expired warning instead of
    # requiring a frontend guess.  The epoch CAS makes this safe if the user
    # completes a new login while the overview is being assembled.
    for row in rows:
        account_id = int(row["id"])
        if int(row["status"]) != 0 or account_id in active_risks:
            continue
        try:
            risk = record_media_account_risk(
                database_path,
                account_id,
                source="login_check",
                error_text="登录会话已失效，需要重新登录",
                expected_credential_epoch=int(row["credential_epoch"]),
            )
        except (ConcurrentMediaAccountCredentialUpdateError, LookupError):
            continue
        if risk is not None:
            active_risks[account_id] = risk
    accounts = [
        _serialize_account(
            row,
            cookie_dir,
            risk_event=active_risks.get(int(row["id"])),
        )
        for row in rows
    ]
    platforms = []
    for definition in PLATFORM_DEFINITIONS:
        platform_accounts = [
            account for account in accounts if account["type"] == definition["type"]
        ]
        platforms.append(
            {
                **definition,
                "account_count": len(platform_accounts),
                "connected_count": sum(
                    account["status"] == "connected" for account in platform_accounts
                ),
                "accounts": platform_accounts,
            }
        )

    return {
        "summary": {
            "total": len(accounts),
            "connected": sum(account["status"] == "connected" for account in accounts),
            "needs_action": sum(
                account["status"]
                in {
                    "expired",
                    "missing_cookie",
                    "permanent_ban",
                    "temporary_restriction",
                    "verification_required",
                    "session_expired",
                }
                for account in accounts
            ),
            "checking": sum(account["status"] == "checking" for account in accounts),
        },
        "platforms": platforms,
        "accounts": accounts,
    }


async def check_media_account(
    database_path: str | Path,
    account_id: int,
    *,
    cookies_directory: str | Path,
    checker: Callable[[int, str], Any],
    enable_bilibili_runtime: bool = False,
    checked_at: datetime | None = None,
) -> dict[str, Any]:
    cookie_dir = Path(cookies_directory)
    with closing(_connect(database_path)) as conn:
        row = _fetch_account(conn, account_id)
    if row is None:
        raise MediaAccountNotFoundError("媒体账号不存在")
    if row["status"] == DISCONNECTED_ACCOUNT_STATUS:
        raise MediaAccountNotFoundError("媒体账号已在本机解绑")
    active_risk = get_active_media_account_risk(database_path, account_id)
    if active_risk and active_risk["category"] in FROZEN_RISK_CATEGORIES:
        raise MediaAccountFrozenError(
            f"{active_risk['label']}：{active_risk['recommended_action']}",
            risk_event=active_risk,
        )
    if row["type"] == BILIBILI_ACCOUNT_TYPE and not enable_bilibili_runtime:
        raise MediaAccountRuntimeDisabledError(BILIBILI_RUNTIME_DISABLED_MESSAGE)

    timestamp = _normalize_checked_at(checked_at)
    credential_epoch = int(row["credential_epoch"])
    cookie_path = cookie_dir / row["filePath"]
    if not cookie_path.is_file():
        connected = False
        message = "Cookie 文件不存在，请重新登录或上传 Cookie"
        try:
            risk_event = record_media_account_risk(
                database_path,
                account_id,
                source="login_check",
                error_text="登录会话已失效：Cookie 文件不存在",
                occurred_at=timestamp,
                expected_credential_epoch=credential_epoch,
            )
        except (ConcurrentMediaAccountCredentialUpdateError, LookupError):
            return _stale_check_account_result(database_path, account_id, cookie_dir)
    else:
        try:
            result = checker(row["type"], row["filePath"])
            resolved_result = await result if inspect.isawaitable(result) else result
            if isinstance(resolved_result, dict):
                connected = bool(resolved_result.get("connected"))
                result_detail = str(
                    resolved_result.get("message")
                    or resolved_result.get("reason")
                    or ""
                ).strip()
            else:
                connected = bool(resolved_result)
                result_detail = ""
        except Exception as exc:
            try:
                risk_event = record_media_account_risk(
                    database_path,
                    account_id,
                    source="login_check",
                    error_text=str(exc),
                    occurred_at=timestamp,
                    expected_credential_epoch=credential_epoch,
                )
            except (ConcurrentMediaAccountCredentialUpdateError, LookupError):
                return _stale_check_account_result(
                    database_path, account_id, cookie_dir
                )
            if risk_event is not None:
                error_type = (
                    MediaAccountFrozenError
                    if risk_event["frozen"]
                    else MediaAccountCheckError
                )
                raise error_type(
                    f"{risk_event['label']}：{risk_event['recommended_action']}",
                    risk_event=risk_event,
                ) from exc
            raise MediaAccountCheckError("平台登录状态检测失败，请稍后重试") from exc
        message = "登录状态正常" if connected else "登录已失效，需要重新登录"
        try:
            if connected:
                resolve_recoverable_media_account_risks(
                    database_path,
                    account_id,
                    resolved_at=timestamp,
                    expected_credential_epoch=credential_epoch,
                )
                risk_event = None
            else:
                risk_event = record_media_account_risk(
                    database_path,
                    account_id,
                    source="login_check",
                    error_text=(
                        "登录会话已失效，需要重新登录。"
                        f"平台返回：{result_detail}"
                        if result_detail
                        else "登录会话已失效，需要重新登录"
                    ),
                    occurred_at=timestamp,
                    expected_credential_epoch=credential_epoch,
                )
                if risk_event is not None:
                    message = risk_event["reason"]
        except (ConcurrentMediaAccountCredentialUpdateError, LookupError):
            return _stale_check_account_result(database_path, account_id, cookie_dir)

    with closing(_connect(database_path)) as conn:
        with conn:
            updated_cursor = conn.execute(
                """
                UPDATE user_info
                SET status = ?, last_checked_at = ?
                WHERE id = ? AND status != ?
                  AND credential_epoch = ?
                """,
                (
                    int(connected),
                    _utc_sql(timestamp),
                    account_id,
                    DISCONNECTED_ACCOUNT_STATUS,
                    credential_epoch,
                ),
            )
            updated = _fetch_account(conn, account_id)

    if updated_cursor.rowcount != 1:
        return _stale_check_account_result(database_path, account_id, cookie_dir)

    if updated is None or updated["status"] == DISCONNECTED_ACCOUNT_STATUS:
        raise MediaAccountNotFoundError("媒体账号已在检测期间解绑")

    current_risk = get_active_media_account_risk(database_path, account_id)
    account = _serialize_account(updated, cookie_dir, risk_event=current_risk)
    account["check_message"] = message
    return account


def disconnect_media_account(
    database_path: str | Path,
    account_id: int,
    *,
    cookies_directory: str | Path,
) -> dict[str, Any]:
    """Disable one local binding before removing its credential file.

    A disconnected account remains as a hidden audit row (status ``-2``), so
    completed and failed publish history keeps its account label.  The disabled
    state is committed before unlinking the credential.  A process crash can
    therefore leave an unusable file for bounded cleanup, but cannot destroy
    the only credential while the account still appears enabled.
    """

    if (
        isinstance(account_id, bool)
        or not isinstance(account_id, int)
        or account_id <= 0
    ):
        raise ValueError("account_id 必须是正整数")

    cookies_root = Path(cookies_directory).expanduser().resolve()
    cookies_root.mkdir(parents=True, exist_ok=True)
    source_path: Path | None = None
    filename = ""
    cookie_missing = True
    cookie_shared = False
    jobs_unbound = 0

    with closing(_connect(database_path)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = _fetch_account(conn, account_id)
            if row is None:
                raise MediaAccountNotFoundError("媒体账号不存在")
            if row["status"] == DISCONNECTED_ACCOUNT_STATUS:
                raise MediaAccountNotFoundError("媒体账号已在本机解绑")

            processing_jobs = conn.execute(
                """
                SELECT COUNT(*)
                FROM publish_jobs
                WHERE account_id = ? AND status = 'processing'
                """,
                (account_id,),
            ).fetchone()[0]
            if processing_jobs:
                raise MediaAccountBusyError(
                    "该账号正在执行发布任务，请等待任务结束后再退出解绑"
                )

            filename = _normalize_cookie_filename(row["filePath"])
            if filename:
                source_path = cookies_root / filename
                if source_path.exists() and not source_path.is_symlink():
                    resolved_source = source_path.resolve(strict=True)
                    if (
                        resolved_source.parent != cookies_root
                        or not resolved_source.is_file()
                    ):
                        raise MediaAccountInvalidCookiePathError(
                            "账号凭证路径不安全，已拒绝解绑"
                        )
                cookie_missing = not (
                    source_path.exists() or source_path.is_symlink()
                )
                cookie_shared = bool(
                    conn.execute(
                        """
                        SELECT 1
                        FROM user_info
                        WHERE id != ? AND status != ? AND filePath = ?
                        LIMIT 1
                        """,
                        (
                            account_id,
                            DISCONNECTED_ACCOUNT_STATUS,
                            filename,
                        ),
                    ).fetchone()
                )

            updated = conn.execute(
                """
                UPDATE publish_jobs
                SET account_id = NULL,
                    authorization_fingerprint = '',
                    auto_execute = 0,
                    status = CASE
                        WHEN status IN ('queued', 'scheduled') THEN 'need_action'
                        ELSE status
                    END,
                    message = CASE
                        WHEN status IN ('queued', 'scheduled')
                            THEN '媒体账号已在本机解绑，请重新选择账号并确认发布'
                        ELSE message
                    END,
                    state_version = state_version + 1
                WHERE account_id = ?
                  AND status IN ('queued', 'scheduled', 'need_action')
                """,
                (account_id,),
            )
            jobs_unbound = updated.rowcount
            disconnected = conn.execute(
                """
                UPDATE user_info
                SET status = ?, last_checked_at = CURRENT_TIMESTAMP
                WHERE id = ? AND status != ?
                """,
                (
                    DISCONNECTED_ACCOUNT_STATUS,
                    account_id,
                    DISCONNECTED_ACCOUNT_STATUS,
                ),
            )
            if disconnected.rowcount != 1:
                raise MediaAccountDisconnectError("媒体账号解绑发生并发冲突")
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    cleanup_pending = False
    cookie_removed = False
    if source_path is not None and not cookie_missing and not cookie_shared:
        try:
            # Unlink removes a symlink itself and never follows its target.
            source_path.unlink()
            cookie_removed = True
        except OSError:
            cleanup_pending = True

    # Status -2 is the security boundary. Clearing the stale filename simply
    # makes the durable cleanup journal self-pruning.
    if not cleanup_pending and filename:
        try:
            with closing(_connect(database_path)) as conn:
                with conn:
                    conn.execute(
                        """
                        UPDATE user_info
                        SET filePath = ''
                        WHERE id = ? AND status = ? AND filePath = ?
                        """,
                        (account_id, DISCONNECTED_ACCOUNT_STATUS, filename),
                    )
        except sqlite3.Error:
            cleanup_pending = True

    return {
        "account_id": account_id,
        "jobs_unbound": jobs_unbound,
        "cookie_removed": cookie_removed,
        "cookie_missing": cookie_missing,
        "cookie_shared": cookie_shared,
        "cleanup_pending": cleanup_pending,
        "account_disabled": True,
    }


def cleanup_disconnected_account_files(
    database_path: str | Path,
    cookies_directory: str | Path,
) -> dict[str, int]:
    """Retry only credentials durably referenced by disconnected rows."""

    cookies_root = Path(cookies_directory).expanduser().resolve()
    cookies_root.mkdir(parents=True, exist_ok=True)
    removed = 0
    remaining = 0
    with closing(_connect(database_path)) as conn:
        account_ids = [
            int(row["id"])
            for row in conn.execute(
                """
                SELECT id
                FROM user_info
                WHERE status = ? AND filePath != ''
                ORDER BY id
                """,
                (DISCONNECTED_ACCOUNT_STATUS,),
            ).fetchall()
        ]

    for account_id in account_ids:
        try:
            with closing(_connect(database_path)) as conn:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    row = conn.execute(
                        """
                        SELECT filePath
                        FROM user_info
                        WHERE id = ? AND status = ? AND filePath != ''
                        """,
                        (account_id, DISCONNECTED_ACCOUNT_STATUS),
                    ).fetchone()
                    if row is None:
                        conn.rollback()
                        continue
                    filename = _normalize_cookie_filename(row["filePath"])
                    active_reference = conn.execute(
                        """
                        SELECT 1
                        FROM user_info
                        WHERE id != ? AND status != ? AND filePath = ?
                        LIMIT 1
                        """,
                        (
                            account_id,
                            DISCONNECTED_ACCOUNT_STATUS,
                            filename,
                        ),
                    ).fetchone()
                    if active_reference is None:
                        candidate = cookies_root / filename
                        if candidate.exists() and not candidate.is_symlink():
                            resolved_candidate = candidate.resolve(strict=True)
                            if (
                                resolved_candidate.parent != cookies_root
                                or not resolved_candidate.is_file()
                            ):
                                raise MediaAccountInvalidCookiePathError(
                                    "账号凭证路径不安全，已拒绝清理"
                                )
                        if candidate.exists() or candidate.is_symlink():
                            candidate.unlink()
                            removed += 1
                    cleared = conn.execute(
                        """
                        UPDATE user_info
                        SET filePath = ''
                        WHERE id = ? AND status = ? AND filePath = ?
                        """,
                        (
                            account_id,
                            DISCONNECTED_ACCOUNT_STATUS,
                            filename,
                        ),
                    )
                    if cleared.rowcount != 1:
                        raise sqlite3.OperationalError(
                            "disconnected credential cleanup lost its row"
                        )
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise
        except (OSError, sqlite3.Error, MediaAccountInvalidCookiePathError):
            remaining += 1
    return {"removed": removed, "remaining": remaining}


def _serialize_account(
    row: sqlite3.Row,
    cookie_dir: Path,
    *,
    risk_event: dict[str, Any] | None = None,
) -> dict[str, Any]:
    definition = _PLATFORM_BY_TYPE.get(
        row["type"],
        {
            "type": row["type"],
            "key": f"unknown-{row['type']}",
            "name": "未知平台",
            "priority": "UNKNOWN",
        },
    )
    cookie_present = bool(row["filePath"]) and (cookie_dir / row["filePath"]).is_file()
    if row["status"] == -1:
        status = "checking"
    elif not cookie_present:
        status = "missing_cookie"
    elif row["status"] == 1:
        status = "connected"
    else:
        status = "expired"
    if (
        risk_event
        and risk_event.get("frozen")
        and not (
            status == "missing_cookie"
            and risk_event.get("category") == "session_expired"
        )
    ):
        status = str(risk_event["category"])
    return {
        "id": row["id"],
        "type": row["type"],
        "platform_key": definition["key"],
        "platform_name": definition["name"],
        "priority": definition["priority"],
        "account_name": row["userName"],
        "credential_epoch": int(row["credential_epoch"]),
        "status": status,
        "cookie_present": cookie_present,
        "last_checked_at": row["last_checked_at"],
        "risk_event": risk_event,
    }


def _fetch_account(conn: sqlite3.Connection, account_id: int):
    return conn.execute(
        """
        SELECT id, type, filePath, userName, status, last_checked_at,
               credential_epoch
        FROM user_info
        WHERE id = ?
        """,
        (account_id,),
    ).fetchone()


def _stale_check_account_result(
    database_path: str | Path,
    account_id: int,
    cookie_dir: Path,
) -> dict[str, Any]:
    with closing(_connect(database_path)) as conn:
        current = _fetch_account(conn, account_id)
    if current is None or current["status"] == DISCONNECTED_ACCOUNT_STATUS:
        raise MediaAccountNotFoundError("媒体账号已在检测期间解绑")
    risk = get_active_media_account_risk(database_path, account_id)
    account = _serialize_account(current, cookie_dir, risk_event=risk)
    account["check_message"] = "账号已重新登录，本次旧会话检测结果已丢弃"
    return account


def _normalize_cookie_filename(value: Any) -> str:
    filename = str(value or "").strip()
    if not filename:
        return ""
    if (
        Path(filename).name != filename
        or "/" in filename
        or "\\" in filename
        or ".." in filename
    ):
        raise MediaAccountInvalidCookiePathError(
            "账号凭证路径不安全，已拒绝解绑"
        )
    return filename


def _connect(database_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(Path(database_path))
    conn.row_factory = sqlite3.Row
    return conn


def _normalize_checked_at(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if not isinstance(value, datetime):
        raise ValueError("checked_at 必须是 datetime")
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _utc_sql(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
