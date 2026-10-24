from __future__ import annotations

import inspect
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from services.platform_capability_service import (
    PLATFORM_CAPABILITIES,
    list_planned_platforms,
)
from services.publish_policy_service import evaluate_publish_reservation, normalize_publish_reference


PLATFORM_DEFINITIONS = tuple(
    {
        **item,
        "type": item["account_type"],
    }
    for item in PLATFORM_CAPABILITIES
)
_PLATFORM_BY_TYPE = {item["type"]: item for item in PLATFORM_DEFINITIONS}


class MediaAccountNotFoundError(LookupError):
    """Raised when a media account id does not exist."""


class MediaAccountCheckError(RuntimeError):
    """Raised when a platform login check cannot produce a reliable result."""


def get_media_accounts_overview(
    database_path: str | Path,
    *,
    cookies_directory: str | Path,
    publish_at: str | datetime | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    cookie_dir = Path(cookies_directory)
    reference = normalize_publish_reference(publish_at, now=now)
    with closing(_connect(database_path)) as conn:
        rows = conn.execute(
            """
            SELECT id, type, filePath, userName, status, last_checked_at
            FROM user_info
            ORDER BY type, id
            """
        ).fetchall()
        decisions = {
            int(row["id"]): evaluate_publish_reservation(
                conn, account_id=int(row["id"]), platform=_PLATFORM_BY_TYPE[row["type"]]["key"],
                publish_at=reference,
            )
            for row in rows if row["type"] in _PLATFORM_BY_TYPE
        }
    accounts = [_serialize_account(row, cookie_dir) for row in rows]
    platforms = []
    for definition in PLATFORM_DEFINITIONS:
        platform_accounts = [
            account for account in accounts if account["type"] == definition["type"]
        ]
        for account in platform_accounts:
            decision = decisions[account["id"]]
            reserved_24h = decision["used_24h"]
            safe_daily_new = int(definition["safe_daily_new"])
            safe_daily_stable = int(definition["safe_daily_stable"])
            account["publish_policy"] = {
                "reserved_24h": reserved_24h,
                "attempted_24h": decision["attempted_24h"],
                "remaining_new": decision["remaining"],
                "remaining_stable": max(safe_daily_stable - reserved_24h, 0),
                "safe_daily_new": safe_daily_new,
                "safe_daily_stable": safe_daily_stable,
                "min_interval_hours": int(definition["min_interval_hours"]),
                "last_attempt_at": decision["last_attempt_at"],
                "delivery_state": definition["delivery_state"],
                **{key: decision[key] for key in (
                    "allowed", "reason_code", "reference_at", "next_allowed_at",
                    "policy_version", "manual_activity_unknown",
                )},
            }
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
        "reference_at": reference.isoformat(),
        "reference_mode": "scheduled" if publish_at not in (None, "") else "now",
        "summary": {
            "total": len(accounts),
            "connected": sum(account["status"] == "connected" for account in accounts),
            "needs_action": sum(
                account["status"] in {"expired", "missing_cookie"}
                for account in accounts
            ),
            "checking": sum(account["status"] == "checking" for account in accounts),
        },
        "platforms": platforms,
        "planned_platforms": list_planned_platforms(),
        "accounts": accounts,
    }


async def check_media_account(
    database_path: str | Path,
    account_id: int,
    *,
    cookies_directory: str | Path,
    checker: Callable[[int, str], Any],
    checked_at: datetime | None = None,
) -> dict[str, Any]:
    cookie_dir = Path(cookies_directory)
    with closing(_connect(database_path)) as conn:
        row = _fetch_account(conn, account_id)
    if row is None:
        raise MediaAccountNotFoundError("媒体账号不存在")

    timestamp = _normalize_checked_at(checked_at)
    cookie_path = cookie_dir / row["filePath"]
    if not cookie_path.is_file():
        connected = False
        message = "Cookie 文件不存在，请重新登录或上传 Cookie"
    else:
        try:
            result = checker(row["type"], row["filePath"])
            connected = bool(await result) if inspect.isawaitable(result) else bool(result)
        except Exception as exc:
            raise MediaAccountCheckError("平台登录状态检测失败，请稍后重试") from exc
        message = "登录状态正常" if connected else "登录已失效，需要重新登录"

    with closing(_connect(database_path)) as conn:
        with conn:
            conn.execute(
                """
                UPDATE user_info
                SET status = ?, last_checked_at = ?
                WHERE id = ?
                """,
                (int(connected), _utc_sql(timestamp), account_id),
            )
            updated = _fetch_account(conn, account_id)

    account = _serialize_account(updated, cookie_dir)
    account["check_message"] = message
    return account


def _serialize_account(row: sqlite3.Row, cookie_dir: Path) -> dict[str, Any]:
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
    return {
        "id": row["id"],
        "type": row["type"],
        "platform_key": definition["key"],
        "platform_name": definition["name"],
        "priority": definition["priority"],
        "account_name": row["userName"],
        "status": status,
        "cookie_present": cookie_present,
        "last_checked_at": row["last_checked_at"],
    }


def _fetch_account(conn: sqlite3.Connection, account_id: int):
    return conn.execute(
        """
        SELECT id, type, filePath, userName, status, last_checked_at
        FROM user_info
        WHERE id = ?
        """,
        (account_id,),
    ).fetchone()


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
