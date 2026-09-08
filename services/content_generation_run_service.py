from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from services.content_engine_contract import (
    ContentEngineError,
    ContentGenerationRequest,
    ContentGenerationResult,
)


class GenerationRunNotFoundError(LookupError):
    """Raised when a generation run id does not exist."""


class GenerationRunStateError(RuntimeError):
    """Raised when a generation run cannot make the requested transition."""


class IdempotencyConflictError(ContentEngineError):
    error_code = "idempotency_conflict"


DEFAULT_STARTED_STALE_AFTER_SECONDS = 300


@dataclass(frozen=True)
class GenerationRunOutcome:
    run_id: int
    request_id: str
    status: str
    replayed: bool
    result: Mapping[str, Any] | None = None
    local_score: int | None = None
    local_analysis: Mapping[str, Any] | None = None
    error_code: str = ""
    error_message: str = ""
    trace_id: str = ""
    state_unknown: bool = False
    retry_after_seconds: int | None = None


def begin_or_replay(
    database_path: str | Path,
    request: ContentGenerationRequest,
    *,
    configured_engine: str,
    started_stale_after_seconds: int = DEFAULT_STARTED_STALE_AFTER_SECONDS,
) -> GenerationRunOutcome:
    """Create one durable run or return the existing idempotent outcome."""

    fingerprint = request.fingerprint()
    idempotency_key = request.idempotency_key or None
    with closing(_connect(database_path)) as conn:
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            row_by_key = None
            if idempotency_key is not None:
                row_by_key = conn.execute(
                    "SELECT * FROM content_generation_runs WHERE idempotency_key = ?",
                    (idempotency_key,),
                ).fetchone()
            row_by_request = conn.execute(
                "SELECT * FROM content_generation_runs WHERE request_id = ?",
                (request.request_id,),
            ).fetchone()
            if (
                row_by_key is not None
                and row_by_request is not None
                and int(row_by_key["id"]) != int(row_by_request["id"])
            ):
                error = IdempotencyConflictError(
                    "Idempotency-Key 与 X-Request-ID 已绑定到不同请求",
                    trace_id=row_by_request["trace_id"] or row_by_request["request_id"],
                )
                error.run_id = int(row_by_request["id"])
                error.request_id = request.request_id
                raise error
            if row_by_request is not None and (
                (row_by_request["idempotency_key"] or None) != idempotency_key
            ):
                error = IdempotencyConflictError(
                    "X-Request-ID 已绑定到其他 Idempotency-Key",
                    trace_id=row_by_request["trace_id"] or row_by_request["request_id"],
                )
                error.run_id = int(row_by_request["id"])
                error.request_id = request.request_id
                raise error
            row = row_by_key or row_by_request
            if row is not None:
                if row["request_fingerprint"] != fingerprint:
                    error = IdempotencyConflictError(
                        "Idempotency-Key 已用于不同的生成请求",
                        trace_id=row["trace_id"] or row["request_id"],
                    )
                    error.run_id = int(row["id"])
                    error.request_id = request.request_id
                    raise error
                row = _expire_stale_started_run(
                    conn,
                    row,
                    stale_after_seconds=started_stale_after_seconds,
                )
                return _outcome_from_row(row, replayed=True)

            cursor = conn.execute(
                """
                INSERT INTO content_generation_runs (
                    request_id, idempotency_key, request_fingerprint,
                    project_id, project_snapshot, brief_snapshot,
                    configured_engine, status, trace_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'started', ?)
                """,
                (
                    request.request_id,
                    idempotency_key,
                    fingerprint,
                    _project_id(request.project_context),
                    _encode_json(dict(request.project_context)),
                    _encode_json(request.provider_payload()["brief"]),
                    str(configured_engine or "").strip() or "unknown",
                    request.request_id,
                ),
            )
            row = _fetch_run(conn, cursor.lastrowid)
    return _outcome_from_row(row, replayed=False)


def _expire_stale_started_run(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
    *,
    stale_after_seconds: int,
) -> sqlite3.Row:
    """Turn an abandoned in-flight marker into a durable unknown outcome.

    A process can exit after reserving an idempotency key but before recording
    the provider result. Keeping that row as ``started`` forever would turn
    every later replay into a permanent 409. We never retry the provider here;
    after a conservative lease expires we expose the outcome as unknown so an
    operator can reconcile it without risking a duplicate generation.
    """

    if row["status"] != "started":
        return row
    try:
        stale_after_seconds = int(stale_after_seconds)
    except (TypeError, ValueError):
        stale_after_seconds = DEFAULT_STARTED_STALE_AFTER_SECONDS
    stale_after_seconds = max(30, min(stale_after_seconds, 3600))
    cursor = conn.execute(
        """
        UPDATE content_generation_runs
        SET status = 'unknown',
            error_code = 'generation_state_unknown',
            error_message = '生成进程未完成，最终状态未知；请人工核对后再决定是否重试',
            retry_after_seconds = NULL,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
          AND status = 'started'
          AND updated_at <= datetime('now', '-' || ? || ' seconds')
        """,
        (int(row["id"]), stale_after_seconds),
    )
    return _fetch_run(conn, int(row["id"])) if cursor.rowcount else row


def mark_succeeded(
    database_path: str | Path,
    run_id: int,
    result: ContentGenerationResult,
    *,
    local_score: int,
    local_analysis: Mapping[str, Any],
) -> dict[str, Any]:
    """Persist the normalized result and return the stable public payload."""

    analysis = dict(local_analysis or {})
    payload = result.public_payload()
    payload["generation"]["replayed"] = False
    payload["geo_score"] = int(local_score)
    payload["geo_analysis"] = analysis
    payload["generation"]["run_id"] = int(run_id)
    result_digest = _result_digest(payload)

    with closing(_connect(database_path)) as conn:
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            current = _fetch_run(conn, run_id)
            if current is None:
                raise GenerationRunNotFoundError("生成记录不存在")
            if current["status"] == "succeeded":
                stored = _decode_object(current["result_payload"])
                if stored:
                    return stored
            if current["status"] != "started":
                raise GenerationRunStateError(
                    f"生成记录不能从 {current['status']} 变更为 succeeded"
                )
            conn.execute(
                """
                UPDATE content_generation_runs
                SET actual_engine = ?, engine_version = ?, status = 'succeeded',
                    trace_id = ?, elapsed_ms = ?, usage = ?, usage_status = ?,
                    provider_geo_score = ?, fallback_used = ?, fallback_from = ?,
                    warnings = ?, local_score = ?, local_analysis = ?,
                    result_payload = ?, result_digest = ?,
                    error_code = '', error_message = '',
                    retry_after_seconds = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    result.engine,
                    result.engine_version,
                    result.trace_id,
                    result.elapsed_ms,
                    _encode_json(dict(result.usage)) if result.usage is not None else None,
                    "reported" if result.usage is not None else "unknown",
                    result.provider_geo_score,
                    int(result.fallback_used),
                    result.fallback_from,
                    _encode_json(list(result.warnings)),
                    int(local_score),
                    _encode_json(analysis),
                    _encode_json(payload),
                    result_digest,
                    int(run_id),
                ),
            )
    return payload


def mark_failed(
    database_path: str | Path,
    run_id: int,
    error: ContentEngineError,
) -> dict[str, Any]:
    return _mark_error(database_path, run_id, error, status="failed")


def mark_unknown(
    database_path: str | Path,
    run_id: int,
    error: ContentEngineError,
) -> dict[str, Any]:
    return _mark_error(database_path, run_id, error, status="unknown")


def bind_article(
    database_path: str | Path,
    run_id: int,
    article_id: int,
    *,
    connection: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """Bind a successful run once; pass a connection to share the save transaction."""

    if connection is not None:
        _bind_article_on_connection(connection, run_id, article_id)
        row = _fetch_run(connection, run_id)
        return _serialize_run(row)

    with closing(_connect(database_path)) as conn:
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            _bind_article_on_connection(conn, run_id, article_id)
            row = _fetch_run(conn, run_id)
    return _serialize_run(row)


def get_run(database_path: str | Path, run_id: int) -> dict[str, Any] | None:
    with closing(_connect(database_path)) as conn:
        row = _fetch_run(conn, run_id)
    return _serialize_run(row) if row is not None else None


def get_run_by_request_id(
    database_path: str | Path,
    request_id: str,
) -> dict[str, Any] | None:
    with closing(_connect(database_path)) as conn:
        row = conn.execute(
            "SELECT * FROM content_generation_runs WHERE request_id = ?",
            (str(request_id or "").strip(),),
        ).fetchone()
    return _serialize_run(row) if row is not None else None


def get_run_by_idempotency_key(
    database_path: str | Path,
    idempotency_key: str,
) -> dict[str, Any] | None:
    normalized = str(idempotency_key or "").strip()
    if not normalized:
        return None
    with closing(_connect(database_path)) as conn:
        row = conn.execute(
            "SELECT * FROM content_generation_runs WHERE idempotency_key = ?",
            (normalized,),
        ).fetchone()
    return _serialize_run(row) if row is not None else None


def get_run_score_context(database_path: str | Path, run_id: int) -> dict[str, Any]:
    run = get_run(database_path, run_id)
    if run is None:
        raise GenerationRunNotFoundError("生成记录不存在")
    if run["status"] != "succeeded":
        raise GenerationRunStateError("仅成功的生成记录可以作为评分上下文")
    project = run["project_snapshot"]
    brief = run["brief_snapshot"]
    keywords = brief.get("keywords")
    if not isinstance(keywords, list):
        keywords = project.get("keywords") if isinstance(project.get("keywords"), list) else []
    return {
        "brand": str(project.get("name") or "").strip(),
        "keywords": [str(item).strip() for item in keywords if str(item).strip()],
        "project_snapshot": project,
        "topic": str(brief.get("topic") or "").strip(),
        "target_platform": str(brief.get("target_platform") or "").strip(),
    }


def _mark_error(
    database_path: str | Path,
    run_id: int,
    error: ContentEngineError,
    *,
    status: str,
) -> dict[str, Any]:
    with closing(_connect(database_path)) as conn:
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            current = _fetch_run(conn, run_id)
            if current is None:
                raise GenerationRunNotFoundError("生成记录不存在")
            if current["status"] == "succeeded":
                raise GenerationRunStateError("成功的生成记录不能改为失败")
            if current["status"] in {"failed", "unknown"}:
                return _serialize_run(current)
            conn.execute(
                """
                UPDATE content_generation_runs
                SET status = ?, trace_id = ?, error_code = ?, error_message = ?,
                    actual_engine = ?, fallback_used = ?, fallback_from = ?,
                    retry_after_seconds = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    status,
                    error.trace_id or current["request_id"],
                    error.error_code,
                    str(error)[:500],
                    str(getattr(error, "attempted_engine", "") or ""),
                    int(bool(getattr(error, "fallback_used", False))),
                    str(getattr(error, "fallback_from", "") or ""),
                    getattr(error, "retry_after_seconds", None),
                    int(run_id),
                ),
            )
            row = _fetch_run(conn, run_id)
    return _serialize_run(row)


def _bind_article_on_connection(
    conn: sqlite3.Connection,
    run_id: int,
    article_id: int,
) -> None:
    row = conn.execute(
        "SELECT status, article_id FROM content_generation_runs WHERE id = ?",
        (int(run_id),),
    ).fetchone()
    if row is None:
        raise GenerationRunNotFoundError("生成记录不存在")
    if row[0] != "succeeded":
        raise GenerationRunStateError("仅成功的生成记录可以绑定文章")
    if row[1] is not None:
        if int(row[1]) == int(article_id):
            return
        raise GenerationRunStateError("生成记录已经绑定到另一篇文章")
    if conn.execute("SELECT 1 FROM articles WHERE id = ?", (int(article_id),)).fetchone() is None:
        raise GenerationRunStateError("待绑定文章不存在")
    try:
        cursor = conn.execute(
            """
            UPDATE content_generation_runs
            SET article_id = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND article_id IS NULL
            """,
            (int(article_id), int(run_id)),
        )
    except sqlite3.IntegrityError as exc:
        raise GenerationRunStateError("文章已绑定到另一条生成记录") from exc
    if cursor.rowcount != 1:
        raise GenerationRunStateError("生成记录绑定发生并发冲突")


def _connect(database_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(Path(database_path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def _fetch_run(conn: sqlite3.Connection, run_id: int):
    return conn.execute(
        "SELECT * FROM content_generation_runs WHERE id = ?",
        (int(run_id),),
    ).fetchone()


def _outcome_from_row(row: sqlite3.Row, *, replayed: bool) -> GenerationRunOutcome:
    result = _decode_object(row["result_payload"])
    analysis = _decode_object(row["local_analysis"])
    return GenerationRunOutcome(
        run_id=int(row["id"]),
        request_id=row["request_id"],
        status=row["status"],
        replayed=replayed,
        result=result or None,
        local_score=row["local_score"],
        local_analysis=analysis or None,
        error_code=row["error_code"],
        error_message=row["error_message"],
        trace_id=row["trace_id"],
        state_unknown=row["status"] == "unknown",
        retry_after_seconds=row["retry_after_seconds"],
    )


def _serialize_run(row: sqlite3.Row) -> dict[str, Any]:
    run = dict(row)
    for field in (
        "project_snapshot",
        "brief_snapshot",
        "local_analysis",
        "result_payload",
    ):
        run[field] = _decode_object(run.get(field))
    run["usage"] = _decode_object(run.get("usage")) or None
    run["warnings"] = _decode_list(run.get("warnings"))
    run["fallback_used"] = bool(run.get("fallback_used"))
    return run


def _project_id(project: Mapping[str, Any]) -> int | None:
    try:
        value = int(project.get("id"))
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _result_digest(payload: Mapping[str, Any]) -> str:
    encoded = _encode_json(payload).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _encode_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _decode_object(value: Any) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value) if isinstance(value, str) else value
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _decode_list(value: Any) -> list[Any]:
    if not value:
        return []
    try:
        parsed = json.loads(value) if isinstance(value, str) else value
    except (TypeError, json.JSONDecodeError):
        return []
    return parsed if isinstance(parsed, list) else []
