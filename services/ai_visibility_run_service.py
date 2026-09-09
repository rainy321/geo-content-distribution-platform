"""Local-only persistence for deterministic AI visibility analyses.

The saved input samples can contain complete AI response text.  This module only
writes to the configured SQLite database; it never performs network or provider
calls.  Callers should keep that database inside the desktop application's local
data directory.
"""

from __future__ import annotations

import json
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from services.project_service import ProjectNotFoundError


DEFAULT_HISTORY_LIMIT = 20
MAX_HISTORY_LIMIT = 100
MAX_PROMPT_LENGTH = 8_000
MAX_ALGORITHM_VERSION_LENGTH = 200
_UTC_TIMESTAMP_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$"
)


class VisibilityRunValidationError(ValueError):
    """Raised when an AI visibility history request is invalid."""


class VisibilityRunNotFoundError(LookupError):
    """Raised when an AI visibility run does not exist in the requested scope."""


class VisibilityRunDataError(RuntimeError):
    """Raised when persisted run data no longer satisfies the storage contract."""


def save_ai_visibility_run(
    database_path: str | Path,
    *,
    project_id: int,
    prompt: str,
    input_samples: list[Any],
    coverage_items: list[Any] | Mapping[str, Any],
    result: Mapping[str, Any],
    algorithm_version: str,
) -> dict[str, Any]:
    """Persist one analysis atomically and return its complete representation."""

    normalized_project_id = _positive_integer(project_id, "project_id")
    normalized_prompt = _non_empty_text(prompt, "prompt", MAX_PROMPT_LENGTH)
    normalized_version = _non_empty_text(
        algorithm_version,
        "algorithm_version",
        MAX_ALGORITHM_VERSION_LENGTH,
    )
    if not isinstance(input_samples, list):
        raise VisibilityRunValidationError("input_samples 必须是数组")
    if not isinstance(coverage_items, (list, Mapping)):
        raise VisibilityRunValidationError("coverage_items 必须是数组或对象")
    if not isinstance(result, Mapping):
        raise VisibilityRunValidationError("result 必须是对象")

    encoded_samples = _encode_json(input_samples, "input_samples")
    encoded_coverage = _encode_json(coverage_items, "coverage_items")
    encoded_result = _encode_json(result, "result")
    created_at = _utc_now()

    with closing(_connect(database_path)) as conn:
        try:
            conn.execute("BEGIN IMMEDIATE")
            _require_project(conn, normalized_project_id)
            cursor = conn.execute(
                """
                INSERT INTO ai_visibility_runs (
                    project_id, prompt, input_samples_json,
                    coverage_items_json, result_json,
                    algorithm_version, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    normalized_project_id,
                    normalized_prompt,
                    encoded_samples,
                    encoded_coverage,
                    encoded_result,
                    normalized_version,
                    created_at,
                ),
            )
            row = _fetch_run(conn, int(cursor.lastrowid))
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    if row is None:  # Defensive: the insert and fetch occur in the same transaction.
        raise VisibilityRunDataError("可见度分析记录保存后无法读取")
    return _serialize_full_run(row)


def list_ai_visibility_runs(
    database_path: str | Path,
    *,
    project_id: int,
    limit: int = DEFAULT_HISTORY_LIMIT,
) -> list[dict[str, Any]]:
    """Return newest-first summaries without returning stored response text."""

    normalized_project_id = _positive_integer(project_id, "project_id")
    normalized_limit = _history_limit(limit)
    with closing(_connect(database_path)) as conn:
        _require_project(conn, normalized_project_id)
        rows = conn.execute(
            """
            SELECT id, project_id, prompt, result_json,
                   algorithm_version, created_at
            FROM ai_visibility_runs
            WHERE project_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (normalized_project_id, normalized_limit),
        ).fetchall()
    return [_serialize_run_summary(row) for row in rows]


def get_ai_visibility_run(
    database_path: str | Path,
    run_id: int,
    *,
    project_id: int | None = None,
) -> dict[str, Any]:
    """Return a complete run, optionally restricted to one project."""

    normalized_run_id = _positive_integer(run_id, "run_id")
    normalized_project_id = (
        _positive_integer(project_id, "project_id")
        if project_id is not None
        else None
    )
    with closing(_connect(database_path)) as conn:
        if normalized_project_id is not None:
            _require_project(conn, normalized_project_id)
        row = _fetch_run(
            conn,
            normalized_run_id,
            project_id=normalized_project_id,
        )
    if row is None:
        raise VisibilityRunNotFoundError("AI 可见度分析记录不存在")
    return _serialize_full_run(row)


def _connect(database_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(Path(database_path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def _require_project(conn: sqlite3.Connection, project_id: int) -> None:
    row = conn.execute(
        "SELECT 1 FROM projects WHERE id = ?",
        (project_id,),
    ).fetchone()
    if row is None:
        raise ProjectNotFoundError("品牌项目不存在")


def _fetch_run(
    conn: sqlite3.Connection,
    run_id: int,
    *,
    project_id: int | None = None,
) -> sqlite3.Row | None:
    if project_id is None:
        return conn.execute(
            "SELECT * FROM ai_visibility_runs WHERE id = ?",
            (run_id,),
        ).fetchone()
    return conn.execute(
        "SELECT * FROM ai_visibility_runs WHERE id = ? AND project_id = ?",
        (run_id, project_id),
    ).fetchone()


def _serialize_full_run(row: sqlite3.Row) -> dict[str, Any]:
    input_samples = _decode_json(row["input_samples_json"], "input_samples")
    coverage_items = _decode_json(row["coverage_items_json"], "coverage_items")
    result = _decode_json(row["result_json"], "result")
    if not isinstance(input_samples, list):
        raise VisibilityRunDataError("input_samples 必须是数组")
    if not isinstance(coverage_items, (list, dict)):
        raise VisibilityRunDataError("coverage_items 必须是数组或对象")
    if not isinstance(result, dict):
        raise VisibilityRunDataError("result 必须是对象")
    return {
        "id": int(row["id"]),
        "project_id": int(row["project_id"]),
        "prompt": str(row["prompt"]),
        "input_samples": input_samples,
        "coverage_items": coverage_items,
        "result": result,
        "algorithm_version": str(row["algorithm_version"]),
        "created_at": _normalize_timestamp(row["created_at"]),
    }


def _serialize_run_summary(row: sqlite3.Row) -> dict[str, Any]:
    result = _decode_json(row["result_json"], "result")
    if not isinstance(result, dict):
        raise VisibilityRunDataError("result 必须是对象")
    summary = result.get("summary")
    if summary is None:
        summary = {}
    if not isinstance(summary, dict):
        raise VisibilityRunDataError("result.summary 必须是对象")
    warnings = result.get("warnings")
    warnings_count = len(warnings) if isinstance(warnings, list) else 0
    return {
        "id": int(row["id"]),
        "project_id": int(row["project_id"]),
        "prompt": str(row["prompt"]),
        "summary": summary,
        "warnings_count": warnings_count,
        "algorithm_version": str(row["algorithm_version"]),
        "created_at": _normalize_timestamp(row["created_at"]),
    }


def _encode_json(value: Any, field_name: str) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise VisibilityRunValidationError(
            f"{field_name} 必须是有效 JSON 数据"
        ) from exc


def _decode_json(value: Any, field_name: str) -> Any:
    if not isinstance(value, str):
        raise VisibilityRunDataError(f"{field_name} 存储格式无效")
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        raise VisibilityRunDataError(f"{field_name} 存储的 JSON 无效") from exc


def _positive_integer(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise VisibilityRunValidationError(f"{field_name} 必须是正整数")
    return value


def _history_limit(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise VisibilityRunValidationError("limit 必须是整数")
    if not 1 <= value <= MAX_HISTORY_LIMIT:
        raise VisibilityRunValidationError(
            f"limit 必须在 1 到 {MAX_HISTORY_LIMIT} 之间"
        )
    return value


def _non_empty_text(value: Any, field_name: str, max_length: int) -> str:
    if not isinstance(value, str):
        raise VisibilityRunValidationError(f"{field_name} 必须是字符串")
    normalized = value.strip()
    if not normalized:
        raise VisibilityRunValidationError(f"{field_name} 不能为空")
    if len(normalized) > max_length:
        raise VisibilityRunValidationError(
            f"{field_name} 最多支持 {max_length} 个字符"
        )
    return normalized


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _normalize_timestamp(value: Any) -> str:
    if not isinstance(value, str) or not _UTC_TIMESTAMP_PATTERN.fullmatch(value):
        raise VisibilityRunDataError("created_at 必须是 ISO 8601 UTC 时间")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise VisibilityRunDataError("created_at 必须是有效时间") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise VisibilityRunDataError("created_at 必须使用 UTC 时区")
    return value


__all__ = [
    "DEFAULT_HISTORY_LIMIT",
    "MAX_HISTORY_LIMIT",
    "VisibilityRunDataError",
    "VisibilityRunNotFoundError",
    "VisibilityRunValidationError",
    "get_ai_visibility_run",
    "list_ai_visibility_runs",
    "save_ai_visibility_run",
]
