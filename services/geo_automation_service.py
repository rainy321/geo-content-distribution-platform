from __future__ import annotations

import hashlib
import json
import re
import secrets
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from services.article_service import (
    article_content_fingerprint,
    article_publish_payload_fingerprint,
)
from services.platform_capability_service import PLATFORM_BY_KEY, normalize_platform_key


GEO_AUTOMATION_VERSION = "geo-automation-1.1.0"
MAX_AUTOMATION_ITEMS = 20
MAX_AUTOMATION_CHANNELS = 10
AUTOMATION_WORKER_LEASE_SECONDS = 900
AUTOMATION_WORKER_STAGES = frozenset({"generating", "optimizing"})
AUTOMATION_CONTENT_TYPES = frozenset(
    {"行业科普", "品牌介绍", "产品介绍", "解决方案", "对比文章", "FAQ", "新闻稿"}
)
AUTOMATION_PUBLISH_MODES = frozenset({"prepare", "demo", "real"})
AUTOMATION_RUN_STATUSES = frozenset(
    {
        "queued",
        "running",
        "paused",
        "completed",
        "completed_with_issues",
        "cancelled",
    }
)
AUTOMATION_ITEM_STATUSES = frozenset(
    {
        "queued",
        "generating",
        "optimizing",
        "preflighting",
        "publishing",
        "completed",
        "need_action",
        "failed",
        "skipped",
    }
)
TERMINAL_ITEM_STATUSES = frozenset(
    {"completed", "need_action", "failed", "skipped"}
)
ISSUE_ITEM_STATUSES = frozenset({"need_action", "failed", "skipped"})
ACTIVE_ITEM_STATUSES = frozenset(
    {"generating", "optimizing", "preflighting", "publishing"}
)

_ALLOWED_ITEM_TRANSITIONS = {
    "queued": frozenset({"generating", "need_action", "failed", "skipped"}),
    "generating": frozenset({"optimizing", "need_action", "failed"}),
    "optimizing": frozenset({"preflighting", "need_action", "failed"}),
    "preflighting": frozenset(
        {"publishing", "completed", "need_action", "failed"}
    ),
    "publishing": frozenset({"completed", "need_action", "failed"}),
    "completed": frozenset(),
    "need_action": frozenset(),
    "failed": frozenset(),
    "skipped": frozenset(),
}
_IDENTITY_PATTERN = re.compile(r"[A-Za-z0-9._:-]+")


class GEOAutomationError(RuntimeError):
    """Base error for durable one-click GEO orchestration records."""


class GEOAutomationValidationError(GEOAutomationError, ValueError):
    """Raised before a workflow record is created or changed."""


class GEOAutomationNotFoundError(GEOAutomationError, LookupError):
    """Raised when a workflow run or item does not exist."""


class GEOAutomationConflictError(GEOAutomationError):
    """Raised for idempotency conflicts or stale state transitions."""

    error_code = "automation_state_conflict"
    state_unknown = False


class GEOAutomationProjectChangedError(GEOAutomationConflictError):
    """Raised when the live project no longer matches the confirmed snapshot."""

    error_code = "automation_project_snapshot_changed"


class GEOAutomationCredentialChangedError(GEOAutomationConflictError):
    """Raised when a confirmed real-publish credential generation changed."""

    error_code = "automation_account_credential_changed"


class GEOAutomationLeaseBusyError(GEOAutomationConflictError):
    """Raised when another browser worker still owns an item stage."""

    error_code = "automation_item_busy"


class GEOAutomationAttemptUnknownError(GEOAutomationConflictError):
    """Raised when an expired worker may already have called an external model."""

    error_code = "automation_attempt_outcome_unknown"
    state_unknown = True


def replay_geo_automation_run(
    database_path: str | Path,
    *,
    request_id: str,
    idempotency_key: str,
    project_id: int,
    config: Mapping[str, Any],
    authorization_scope: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Recover a committed run using only its frozen manifest, never live gates."""

    normalized_request_id = _normalize_identity(request_id, "request_id", 128)
    normalized_idempotency_key = _normalize_identity(
        idempotency_key, "idempotency_key", 160
    )
    if isinstance(project_id, bool) or not isinstance(project_id, int) or project_id <= 0:
        raise GEOAutomationValidationError("project_id 必须是正整数")
    with closing(_connect(database_path)) as conn:
        by_key = conn.execute(
            "SELECT * FROM geo_automation_runs WHERE idempotency_key = ?",
            (normalized_idempotency_key,),
        ).fetchone()
        by_request = conn.execute(
            "SELECT * FROM geo_automation_runs WHERE request_id = ?",
            (normalized_request_id,),
        ).fetchone()
        existing = _resolve_existing_run_identity(
            by_key,
            by_request,
            request_id=normalized_request_id,
            idempotency_key=normalized_idempotency_key,
        )
        if existing is None:
            return None
        frozen_project = _decode_object(existing["project_snapshot"])
        normalized_config = normalize_geo_automation_config(
            config, project=frozen_project
        )
        normalized_config["authorization_scope"] = _normalize_authorization_scope(
            authorization_scope,
            project_id=project_id,
            config=normalized_config,
            project=frozen_project,
        )
        fingerprint = _run_request_fingerprint(
            project_id,
            normalized_config,
            normalized_config["authorization_scope"]["project_snapshot"],
        )
        if not _constant_text_equal(
            str(existing["request_fingerprint"]), fingerprint
        ):
            raise GEOAutomationConflictError(
                "该一键 GEO 请求标识已用于不同配置，请新建运行"
            )
        run = _serialize_run(conn, existing, include_items=True)
        run["replayed"] = True
        return run


def create_geo_automation_run(
    database_path: str | Path,
    *,
    request_id: str,
    idempotency_key: str,
    project_id: int,
    config: Mapping[str, Any],
    authorization_scope: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_request_id = _normalize_identity(request_id, "request_id", 128)
    normalized_idempotency_key = _normalize_identity(
        idempotency_key, "idempotency_key", 160
    )
    if isinstance(project_id, bool) or not isinstance(project_id, int) or project_id <= 0:
        raise GEOAutomationValidationError("project_id 必须是正整数")

    with closing(_connect(database_path)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            by_key = conn.execute(
                "SELECT * FROM geo_automation_runs WHERE idempotency_key = ?",
                (normalized_idempotency_key,),
            ).fetchone()
            by_request = conn.execute(
                "SELECT * FROM geo_automation_runs WHERE request_id = ?",
                (normalized_request_id,),
            ).fetchone()
            existing = _resolve_existing_run_identity(
                by_key,
                by_request,
                request_id=normalized_request_id,
                idempotency_key=normalized_idempotency_key,
            )
            if existing is not None:
                frozen_project = _decode_object(existing["project_snapshot"])
                normalized_config = normalize_geo_automation_config(
                    config, project=frozen_project
                )
                normalized_config["authorization_scope"] = (
                    _normalize_authorization_scope(
                        authorization_scope,
                        project_id=project_id,
                        config=normalized_config,
                        project=frozen_project,
                    )
                )
                confirmed_project_snapshot = normalized_config[
                    "authorization_scope"
                ]["project_snapshot"]
                fingerprint = _run_request_fingerprint(
                    project_id,
                    normalized_config,
                    confirmed_project_snapshot,
                )
                if not _constant_text_equal(
                    str(existing["request_fingerprint"]), fingerprint
                ):
                    raise GEOAutomationConflictError(
                        "该一键 GEO 请求标识已用于不同配置，请新建运行"
                    )
                run = _serialize_run(conn, existing, include_items=True)
                run["replayed"] = True
                conn.commit()
                return run

            current_project_row = conn.execute(
                """
                SELECT id, name, website, product, industry, description,
                       keywords, competitors, updated_at
                FROM projects WHERE id = ?
                """,
                (project_id,),
            ).fetchone()
            if current_project_row is None:
                raise GEOAutomationValidationError("品牌项目不存在")
            current_project_snapshot = geo_automation_project_snapshot(
                {
                    **dict(current_project_row),
                    "keywords": _decode_list(current_project_row["keywords"]),
                    "competitors": _decode_list(current_project_row["competitors"]),
                }
            )
            normalized_config = normalize_geo_automation_config(
                config, project=current_project_snapshot
            )
            normalized_config["authorization_scope"] = _normalize_authorization_scope(
                authorization_scope,
                project_id=project_id,
                config=normalized_config,
                project=current_project_snapshot,
            )
            confirmed_project_snapshot = normalized_config["authorization_scope"][
                "project_snapshot"
            ]
            fingerprint = _run_request_fingerprint(
                project_id,
                normalized_config,
                confirmed_project_snapshot,
            )
            if _fingerprint(current_project_snapshot) != _fingerprint(
                confirmed_project_snapshot
            ):
                raise GEOAutomationConflictError(
                    "品牌项目底稿已变化，请重新核对后启动"
                )
            _assert_live_real_publish_accounts(conn, normalized_config)

            cursor = conn.execute(
                """
                INSERT INTO geo_automation_runs (
                    request_id, idempotency_key, request_fingerprint,
                    project_id, project_snapshot, config_json, total_items
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    normalized_request_id,
                    normalized_idempotency_key,
                    fingerprint,
                    project_id,
                    _encode_json(current_project_snapshot),
                    _encode_json(normalized_config),
                    len(normalized_config["topics"]),
                ),
            )
            run_id = int(cursor.lastrowid)
            conn.executemany(
                """
                INSERT INTO geo_automation_items (run_id, position, topic)
                VALUES (?, ?, ?)
                """,
                [
                    (run_id, position, topic)
                    for position, topic in enumerate(
                        normalized_config["topics"], start=1
                    )
                ],
            )
            row = _fetch_run(conn, run_id)
            run = _serialize_run(conn, row, include_items=True)
            run["replayed"] = False
            conn.commit()
            return run
        except Exception:
            conn.rollback()
            raise


def normalize_geo_automation_config(
    config: Mapping[str, Any],
    *,
    project: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(config, Mapping):
        raise GEOAutomationValidationError("config 必须是对象")

    topics = _normalize_string_list(
        config.get("topics"),
        field="topics",
        max_items=MAX_AUTOMATION_ITEMS,
        max_length=160,
        required=True,
    )
    keywords = _normalize_string_list(
        config.get("keywords", project.get("keywords", [])),
        field="keywords",
        max_items=20,
        max_length=80,
        required=True,
    )
    direction = _normalize_text(
        config.get("optimization_direction"),
        field="optimization_direction",
        max_length=2_000,
        required=True,
    )
    run_name = _normalize_text(
        config.get("name") or f"{project.get('name', '品牌')} GEO 自动化",
        field="name",
        max_length=100,
        required=True,
    )
    content_type = str(config.get("content_type") or "行业科普").strip()
    if content_type not in AUTOMATION_CONTENT_TYPES:
        raise GEOAutomationValidationError("不支持的内容类型")
    target_length = config.get("target_length", 1_000)
    if (
        isinstance(target_length, bool)
        or not isinstance(target_length, int)
        or not 200 <= target_length <= 3_000
    ):
        raise GEOAutomationValidationError("target_length 必须为 200 至 3000 的整数")

    publish_mode = str(config.get("publish_mode") or "prepare").strip().lower()
    if publish_mode not in AUTOMATION_PUBLISH_MODES:
        raise GEOAutomationValidationError("publish_mode 必须是 prepare、demo 或 real")
    channels = _normalize_channels(config.get("channels", []), publish_mode)
    if publish_mode in {"demo", "real"} and not channels:
        raise GEOAutomationValidationError("自动发布至少需要选择一个渠道")

    continue_on_error = config.get("continue_on_error", True)
    if continue_on_error is not True:
        raise GEOAutomationValidationError("一键 GEO 必须启用单篇失败后继续")
    optimize = config.get("optimize", True)
    if optimize is not True:
        raise GEOAutomationValidationError("一键 GEO 全流程必须包含内容优化")

    raw_target_platform = str(config.get("target_platform") or "").strip()
    target_platform = normalize_platform_key(raw_target_platform)
    if raw_target_platform and not target_platform:
        raise GEOAutomationValidationError("不支持的写作目标平台")
    if not target_platform and len(channels) == 1:
        target_platform = channels[0]["platform"]

    return {
        "schema_version": GEO_AUTOMATION_VERSION,
        "name": run_name,
        "topics": topics,
        "keywords": keywords,
        "optimization_direction": direction,
        "target_length": target_length,
        "content_type": content_type,
        "target_platform": target_platform or "",
        "channels": channels,
        "publish_mode": publish_mode,
        "optimize": True,
        "continue_on_error": True,
        "estimated_model_calls": len(topics) * 2,
        "maximum_publish_tasks": len(topics) * len(channels),
    }


def get_geo_automation_run(
    database_path: str | Path,
    run_id: int,
) -> dict[str, Any]:
    with closing(_connect(database_path)) as conn:
        row = _fetch_run(conn, run_id)
        if row is None:
            raise GEOAutomationNotFoundError("一键 GEO 运行不存在")
        return _serialize_run(conn, row, include_items=True)


def list_geo_automation_runs(
    database_path: str | Path,
    *,
    project_id: int | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise GEOAutomationValidationError("limit 必须为 1 至 100 的整数")
    parameters: list[Any] = []
    where = ""
    if project_id is not None:
        if (
            isinstance(project_id, bool)
            or not isinstance(project_id, int)
            or project_id <= 0
        ):
            raise GEOAutomationValidationError("project_id 必须是正整数")
        where = "WHERE project_id = ?"
        parameters.append(project_id)
    parameters.append(limit)
    with closing(_connect(database_path)) as conn:
        rows = conn.execute(
            f"""
            SELECT * FROM geo_automation_runs
            {where}
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            parameters,
        ).fetchall()
        return [_serialize_run(conn, row, include_items=False) for row in rows]


def acquire_geo_automation_item_lease(
    database_path: str | Path,
    *,
    run_id: int,
    item_id: int,
    expected_state_version: int,
    worker_id: str,
    stage: str,
) -> dict[str, Any]:
    """Claim one model-call stage without allowing a second browser to race it."""

    normalized_worker = _normalize_identity(worker_id, "worker_id", 160)
    normalized_stage = str(stage or "").strip()
    if normalized_stage not in AUTOMATION_WORKER_STAGES:
        raise GEOAutomationValidationError("只有写稿或优化阶段可以领取模型工作租约")
    if (
        isinstance(expected_state_version, bool)
        or not isinstance(expected_state_version, int)
        or expected_state_version < 0
    ):
        raise GEOAutomationValidationError("expected_state_version 必须是非负整数")

    with closing(_connect(database_path)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            run_row = _fetch_run(conn, run_id)
            if run_row is None:
                raise GEOAutomationNotFoundError("一键 GEO 运行不存在")
            if run_row["status"] in {"completed", "completed_with_issues", "cancelled"}:
                raise GEOAutomationConflictError("已结束的一键 GEO 运行不能领取工作租约")
            if bool(run_row["pause_requested"]):
                error = GEOAutomationConflictError("运行已请求暂停，不能开始新的模型调用")
                error.error_code = "automation_paused"
                raise error
            item_row = conn.execute(
                "SELECT * FROM geo_automation_items WHERE id = ? AND run_id = ?",
                (item_id, run_id),
            ).fetchone()
            if item_row is None:
                raise GEOAutomationNotFoundError("一键 GEO 条目不存在")
            if int(item_row["state_version"]) != expected_state_version:
                raise GEOAutomationConflictError("条目状态已变化，请刷新运行后继续")
            if str(item_row["status"]) != normalized_stage:
                raise GEOAutomationConflictError(
                    f"条目当前不是 {normalized_stage} 阶段，不能领取工作租约"
                )
            _assert_project_snapshot_current(conn, run_row)

            current_owner = str(item_row["worker_lease_owner"] or "")
            current_stage = str(item_row["worker_lease_stage"] or "")
            lease_active = _worker_lease_is_active(conn, item_id)
            same_worker = (
                current_owner == normalized_worker
                and current_stage == normalized_stage
            )
            attempt_requires_manual = _external_attempt_requires_manual_recovery(
                conn, item_row, normalized_stage
            )
            if lease_active:
                if not same_worker:
                    raise GEOAutomationLeaseBusyError(
                        "该文章正由另一个页面执行，当前页面不会重复调用模型"
                    )
                lease = _serialize_worker_lease(item_row)
                conn.commit()
                return lease
            if attempt_requires_manual:
                raise GEOAutomationAttemptUnknownError(
                    "上一个页面的模型调用租约已过期，最终结果未知；未自动重试"
                )

            lease_token = secrets.token_urlsafe(32)
            conn.execute(
                """
                UPDATE geo_automation_items
                SET worker_lease_owner = ?, worker_lease_stage = ?,
                    worker_lease_token = ?,
                    worker_lease_epoch = worker_lease_epoch + 1,
                    worker_lease_expires_at = datetime(
                        'now', '+' || ? || ' seconds'
                    ),
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND run_id = ?
                """,
                (
                    normalized_worker,
                    normalized_stage,
                    lease_token,
                    AUTOMATION_WORKER_LEASE_SECONDS,
                    item_id,
                    run_id,
                ),
            )
            lease = _serialize_worker_lease(
                conn.execute(
                    "SELECT * FROM geo_automation_items WHERE id = ?", (item_id,)
                ).fetchone()
            )
            conn.commit()
            return lease
        except Exception:
            conn.rollback()
            raise


def renew_geo_automation_item_lease(
    database_path: str | Path,
    *,
    run_id: int,
    item_id: int,
    worker_id: str,
    stage: str,
    lease_token: str,
    lease_epoch: int,
) -> dict[str, Any]:
    normalized_worker = _normalize_identity(worker_id, "worker_id", 160)
    normalized_token = _normalize_identity(lease_token, "lease_token", 200)
    normalized_epoch = _required_positive_integer(lease_epoch, "lease_epoch")
    normalized_stage = str(stage or "").strip()
    if normalized_stage not in AUTOMATION_WORKER_STAGES:
        raise GEOAutomationValidationError("工作租约阶段无效")
    with closing(_connect(database_path)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            run_row = _fetch_run(conn, run_id)
            if run_row is None:
                raise GEOAutomationNotFoundError("一键 GEO 运行不存在")
            item_row = conn.execute(
                "SELECT * FROM geo_automation_items WHERE id = ? AND run_id = ?",
                (item_id, run_id),
            ).fetchone()
            if item_row is None:
                raise GEOAutomationNotFoundError("一键 GEO 条目不存在")
            if (
                str(item_row["status"]) != normalized_stage
                or str(item_row["worker_lease_owner"] or "") != normalized_worker
                or str(item_row["worker_lease_stage"] or "") != normalized_stage
                or not _constant_text_equal(
                    str(item_row["worker_lease_token"] or ""),
                    normalized_token,
                )
                or int(item_row["worker_lease_epoch"] or 0) != normalized_epoch
            ):
                raise GEOAutomationLeaseBusyError("工作租约已不属于当前页面")
            if not _worker_lease_is_active(conn, item_id):
                if _external_attempt_requires_manual_recovery(
                    conn, item_row, normalized_stage
                ):
                    raise GEOAutomationAttemptUnknownError(
                        "模型调用工作租约已过期，最终结果未知；未自动续租"
                    )
                raise GEOAutomationLeaseBusyError("工作租约已过期，请重新领取")
            conn.execute(
                """
                UPDATE geo_automation_items
                SET worker_lease_expires_at = datetime(
                        'now', '+' || ? || ' seconds'
                    ),
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND run_id = ?
                  AND worker_lease_token = ? AND worker_lease_epoch = ?
                """,
                (
                    AUTOMATION_WORKER_LEASE_SECONDS,
                    item_id,
                    run_id,
                    normalized_token,
                    normalized_epoch,
                ),
            )
            lease = _serialize_worker_lease(
                conn.execute(
                    "SELECT * FROM geo_automation_items WHERE id = ?", (item_id,)
                ).fetchone()
            )
            conn.commit()
            return lease
        except Exception:
            conn.rollback()
            raise


def release_geo_automation_item_lease(
    database_path: str | Path,
    *,
    run_id: int,
    item_id: int,
    worker_id: str,
    stage: str,
    lease_token: str,
    lease_epoch: int,
) -> dict[str, Any]:
    normalized_worker = _normalize_identity(worker_id, "worker_id", 160)
    normalized_token = _normalize_identity(lease_token, "lease_token", 200)
    normalized_epoch = _required_positive_integer(lease_epoch, "lease_epoch")
    normalized_stage = str(stage or "").strip()
    if normalized_stage not in AUTOMATION_WORKER_STAGES:
        raise GEOAutomationValidationError("工作租约阶段无效")
    with closing(_connect(database_path)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            if _fetch_run(conn, run_id) is None:
                raise GEOAutomationNotFoundError("一键 GEO 运行不存在")
            item_row = conn.execute(
                "SELECT * FROM geo_automation_items WHERE id = ? AND run_id = ?",
                (item_id, run_id),
            ).fetchone()
            if item_row is None:
                raise GEOAutomationNotFoundError("一键 GEO 条目不存在")
            current_owner = str(item_row["worker_lease_owner"] or "")
            current_stage = str(item_row["worker_lease_stage"] or "")
            current_token = str(item_row["worker_lease_token"] or "")
            if current_owner and (
                current_owner != normalized_worker
                or current_stage != normalized_stage
                or not _constant_text_equal(current_token, normalized_token)
                or int(item_row["worker_lease_epoch"] or 0) != normalized_epoch
            ):
                raise GEOAutomationLeaseBusyError("工作租约属于另一个页面")
            conn.execute(
                """
                UPDATE geo_automation_items
                SET worker_lease_owner = '', worker_lease_stage = '',
                    worker_lease_token = '',
                    worker_lease_expires_at = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND run_id = ?
                  AND worker_lease_token = ? AND worker_lease_epoch = ?
                """,
                (item_id, run_id, normalized_token, normalized_epoch),
            )
            _refresh_run_summary(conn, run_id)
            conn.commit()
            return {
                "item_id": item_id,
                "worker_id": normalized_worker,
                "stage": normalized_stage,
                "lease_token": normalized_token,
                "lease_epoch": normalized_epoch,
                "released": True,
            }
        except Exception:
            conn.rollback()
            raise


def assert_geo_automation_item_lease(
    database_path: str | Path,
    *,
    run_id: int,
    item_id: int,
    worker_id: str,
    stage: str,
    lease_token: str,
    lease_epoch: int,
    generation_request_id: str | None = None,
    article_id: int | None = None,
) -> None:
    """Linearization point immediately before an automated provider call."""

    normalized_worker = _normalize_identity(worker_id, "worker_id", 160)
    normalized_token = _normalize_identity(lease_token, "lease_token", 200)
    normalized_epoch = _required_positive_integer(lease_epoch, "lease_epoch")
    normalized_stage = str(stage or "").strip()
    if normalized_stage not in AUTOMATION_WORKER_STAGES:
        raise GEOAutomationValidationError("工作租约阶段无效")
    with closing(_connect(database_path)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            run_row = _fetch_run(conn, run_id)
            if run_row is None:
                raise GEOAutomationNotFoundError("一键 GEO 运行不存在")
            if bool(run_row["pause_requested"]):
                error = GEOAutomationConflictError("运行已请求暂停，未发起模型调用")
                error.error_code = "automation_paused"
                raise error
            item_row = conn.execute(
                "SELECT * FROM geo_automation_items WHERE id = ? AND run_id = ?",
                (item_id, run_id),
            ).fetchone()
            if item_row is None:
                raise GEOAutomationNotFoundError("一键 GEO 条目不存在")
            if (
                str(item_row["status"]) != normalized_stage
                or str(item_row["worker_lease_owner"] or "") != normalized_worker
                or str(item_row["worker_lease_stage"] or "") != normalized_stage
                or not _constant_text_equal(
                    str(item_row["worker_lease_token"] or ""),
                    normalized_token,
                )
                or int(item_row["worker_lease_epoch"] or 0) != normalized_epoch
                or not _worker_lease_is_active(conn, item_id)
            ):
                raise GEOAutomationLeaseBusyError(
                    "模型调用工作租约已失效，未发起新的外部请求"
                )
            if generation_request_id is not None and str(
                item_row["generation_request_id"] or ""
            ) != str(generation_request_id):
                raise GEOAutomationConflictError("生成请求标识与工作条目不一致")
            if article_id is not None and int(item_row["article_id"] or 0) != int(
                article_id
            ):
                raise GEOAutomationConflictError("优化文章与工作条目不一致")
            _assert_project_snapshot_current(conn, run_row)
            result = _decode_object(item_row["result_json"])
            attempt_prefix = (
                "generation" if normalized_stage == "generating" else "optimization"
            )
            provider_token_key = f"{attempt_prefix}_provider_attempt_token"
            provider_started_key = f"{attempt_prefix}_provider_attempt_started_at"
            previous_provider_token = str(result.get(provider_token_key) or "")
            if previous_provider_token:
                replay_is_durable = False
                if normalized_stage == "generating" and generation_request_id:
                    replay_is_durable = conn.execute(
                        """
                        SELECT 1 FROM content_generation_runs
                        WHERE request_id = ?
                        """,
                        (str(generation_request_id),),
                    ).fetchone() is not None
                if not replay_is_durable:
                    raise GEOAutomationAttemptUnknownError(
                        "该阶段已有模型调用记录，未重复发起外部请求"
                    )
            else:
                attempt_key = f"{attempt_prefix}_attempt_started"
                result[attempt_key] = True
                result[provider_token_key] = normalized_token
                result[provider_started_key] = conn.execute(
                    "SELECT CURRENT_TIMESTAMP"
                ).fetchone()[0]
            conn.execute(
                """
                UPDATE geo_automation_items
                SET result_json = ?,
                    worker_lease_expires_at = datetime(
                        'now', '+' || ? || ' seconds'
                    ),
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND run_id = ? AND worker_lease_token = ?
                  AND worker_lease_epoch = ?
                """,
                (
                    _encode_json(result),
                    AUTOMATION_WORKER_LEASE_SECONDS,
                    item_id,
                    run_id,
                    normalized_token,
                    normalized_epoch,
                ),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def checkpoint_geo_automation_item(
    database_path: str | Path,
    *,
    run_id: int,
    item_id: int,
    expected_state_version: int,
    status: str,
    stage: str,
    message: str = "",
    article_id: int | None = None,
    generation_request_id: str | None = None,
    generation_run_id: int | None = None,
    publish_jobs: Sequence[Mapping[str, Any]] | None = None,
    result: Mapping[str, Any] | None = None,
    error: Mapping[str, Any] | None = None,
    preflight_article_fingerprint: str | None = None,
    worker_id: str | None = None,
    lease_token: str | None = None,
    lease_epoch: int | None = None,
) -> dict[str, Any]:
    normalized_status = str(status or "").strip()
    if normalized_status not in AUTOMATION_ITEM_STATUSES:
        raise GEOAutomationValidationError("不支持的一键 GEO 条目状态")
    normalized_stage = _normalize_text(
        stage, field="stage", max_length=64, required=True
    )
    normalized_message = _normalize_text(
        message, field="message", max_length=500, required=False
    )
    if (
        isinstance(expected_state_version, bool)
        or not isinstance(expected_state_version, int)
        or expected_state_version < 0
    ):
        raise GEOAutomationValidationError("expected_state_version 必须是非负整数")
    normalized_preflight_fingerprint = None
    if preflight_article_fingerprint is not None:
        if (
            not isinstance(preflight_article_fingerprint, str)
            or re.fullmatch(r"[0-9a-f]{64}", preflight_article_fingerprint) is None
        ):
            raise GEOAutomationValidationError(
                "preflight_article_fingerprint 格式无效"
            )
        normalized_preflight_fingerprint = preflight_article_fingerprint
    normalized_worker_id = None
    if worker_id is not None:
        normalized_worker_id = _normalize_identity(worker_id, "worker_id", 160)
    normalized_lease_token = None
    if (lease_token is None) != (lease_epoch is None):
        raise GEOAutomationValidationError(
            "lease_token 与 lease_epoch 必须同时提供"
        )
    if lease_token is not None:
        normalized_lease_token = _normalize_identity(
            lease_token,
            "lease_token",
            200,
        )
    normalized_lease_epoch = None
    if lease_epoch is not None:
        normalized_lease_epoch = _required_positive_integer(
            lease_epoch,
            "lease_epoch",
        )

    with closing(_connect(database_path)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            run_row = _fetch_run(conn, run_id)
            if run_row is None:
                raise GEOAutomationNotFoundError("一键 GEO 运行不存在")
            if run_row["status"] in {"completed", "completed_with_issues", "cancelled"}:
                raise GEOAutomationConflictError("已结束的一键 GEO 运行不能继续写入")
            item_row = conn.execute(
                "SELECT * FROM geo_automation_items WHERE id = ? AND run_id = ?",
                (item_id, run_id),
            ).fetchone()
            if item_row is None:
                raise GEOAutomationNotFoundError("一键 GEO 条目不存在")
            if int(item_row["state_version"]) != expected_state_version:
                raise GEOAutomationConflictError("条目状态已变化，请刷新运行后继续")
            current_status = str(item_row["status"])
            lease_owner = str(item_row["worker_lease_owner"] or "")
            lease_active = _worker_lease_is_active(conn, item_id)
            lease_matches = bool(
                lease_owner
                and lease_owner == str(normalized_worker_id or "")
                and str(item_row["worker_lease_stage"] or "") == current_status
                and normalized_lease_token
                and _constant_text_equal(
                    str(item_row["worker_lease_token"] or ""),
                    normalized_lease_token,
                )
                and int(item_row["worker_lease_epoch"] or 0)
                == normalized_lease_epoch
            )
            manual_unknown_park = bool(
                not lease_active
                and normalized_status == "need_action"
                and isinstance(error, Mapping)
                and str(error.get("code") or "")
                == "automation_attempt_outcome_unknown"
                and error.get("state_unknown") is True
            )
            if lease_owner and not lease_matches and not manual_unknown_park:
                raise GEOAutomationLeaseBusyError(
                    "工作租约已更新或属于另一个页面，不能覆盖其检查点"
                )
            if normalized_status != current_status and normalized_status not in _ALLOWED_ITEM_TRANSITIONS[current_status]:
                raise GEOAutomationConflictError(
                    f"条目不能从 {current_status} 变更为 {normalized_status}"
                )
            if current_status in TERMINAL_ITEM_STATUSES:
                raise GEOAutomationConflictError("已结束条目不能再次执行")
            if (
                current_status == "publishing"
                and normalized_status in {"need_action", "failed"}
            ):
                unfinished_job = conn.execute(
                    """
                    SELECT 1 FROM publish_jobs
                    WHERE automation_item_id = ?
                      AND status IN ('queued', 'processing', 'scheduled')
                    LIMIT 1
                    """,
                    (item_id,),
                ).fetchone()
                if unfinished_job is not None:
                    raise GEOAutomationConflictError(
                        "仍有渠道任务排队或正在执行，不能提前结束一键 GEO 条目"
                    )
            if (
                bool(run_row["pause_requested"])
                and current_status == "preflighting"
                and normalized_status in {"publishing", "completed"}
            ):
                raise GEOAutomationConflictError(
                    "运行已请求暂停，不能确认预检完成或开始发布"
                )
            if (
                (
                    normalized_status in ACTIVE_ITEM_STATUSES
                    and not (
                        current_status == "publishing"
                        and normalized_status == "publishing"
                    )
                )
                or (
                    current_status == "preflighting"
                    and normalized_status == "completed"
                )
            ):
                _assert_project_snapshot_current(conn, run_row)
            if current_status == "queued" and normalized_status == "generating":
                if bool(run_row["pause_requested"]):
                    raise GEOAutomationConflictError("运行已请求暂停，不能开始下一篇")
                unfinished_earlier = conn.execute(
                    """
                    SELECT 1 FROM geo_automation_items
                    WHERE run_id = ? AND position < ?
                      AND status NOT IN ('completed', 'need_action', 'failed', 'skipped')
                    LIMIT 1
                    """,
                    (run_id, int(item_row["position"])),
                ).fetchone()
                active_other = conn.execute(
                    """
                    SELECT 1 FROM geo_automation_items
                    WHERE run_id = ? AND id != ?
                      AND status IN ('generating', 'optimizing', 'preflighting', 'publishing')
                    LIMIT 1
                    """,
                    (run_id, item_id),
                ).fetchone()
                if unfinished_earlier is not None or active_other is not None:
                    raise GEOAutomationConflictError(
                        "一键 GEO 必须严格串行，前序文章尚未到达安全终态"
                    )

            resolved_article_id = _optional_positive_integer(
                article_id if article_id is not None else item_row["article_id"],
                "article_id",
            )
            resolved_generation_run_id = _optional_positive_integer(
                (
                    generation_run_id
                    if generation_run_id is not None
                    else item_row["generation_run_id"]
                ),
                "generation_run_id",
            )
            article = None
            if resolved_article_id is not None:
                article = conn.execute(
                    """
                    SELECT id, project_id, title, summary, content, tags, status
                    FROM articles WHERE id = ?
                    """,
                    (resolved_article_id,),
                ).fetchone()
                if article is None or int(article["project_id"]) != int(
                    run_row["project_id"]
                ):
                    raise GEOAutomationValidationError(
                        "article_id 不属于该一键 GEO 品牌项目"
                    )
            if normalized_status in {"optimizing", "preflighting", "publishing", "completed"} and resolved_article_id is None:
                raise GEOAutomationValidationError("该阶段必须绑定已保存文章")

            resolved_request_id = (
                str(generation_request_id).strip()
                if generation_request_id is not None
                else str(item_row["generation_request_id"] or "")
            )
            if resolved_request_id:
                _normalize_identity(resolved_request_id, "generation_request_id", 128)
            if resolved_generation_run_id is not None:
                generation_row = conn.execute(
                    """
                    SELECT request_id, project_id, article_id
                    FROM content_generation_runs
                    WHERE id = ?
                    """,
                    (resolved_generation_run_id,),
                ).fetchone()
                if generation_row is None or int(generation_row["project_id"] or 0) != int(
                    run_row["project_id"]
                ):
                    raise GEOAutomationValidationError(
                        "generation_run_id 不属于该一键 GEO 品牌项目"
                    )
                if resolved_request_id and generation_row["request_id"] != resolved_request_id:
                    raise GEOAutomationValidationError(
                        "generation_request_id 与生成运行不一致"
                    )
                if (
                    resolved_article_id is not None
                    and generation_row["article_id"] is not None
                    and int(generation_row["article_id"]) != resolved_article_id
                ):
                    raise GEOAutomationValidationError(
                        "生成运行绑定的文章与一键 GEO 条目不一致"
                    )
            resolved_publish_jobs = (
                _normalize_publish_jobs(publish_jobs)
                if publish_jobs is not None
                else _decode_list(item_row["publish_jobs_json"])
            )
            resolved_publish_jobs = _canonicalize_publish_job_evidence(
                conn,
                item_id=item_id,
                article_id=resolved_article_id,
                publish_jobs=resolved_publish_jobs,
            )
            resolved_result = (
                _normalize_mapping(result, "result", max_bytes=32_000)
                if result is not None
                else _decode_object(item_row["result_json"])
            )
            previous_result = _decode_object(item_row["result_json"])
            for attempt_prefix in ("generation", "optimization"):
                provider_token_key = f"{attempt_prefix}_provider_attempt_token"
                provider_started_key = (
                    f"{attempt_prefix}_provider_attempt_started_at"
                )
                if previous_result.get(provider_token_key):
                    resolved_result[provider_token_key] = previous_result[
                        provider_token_key
                    ]
                    resolved_result[provider_started_key] = previous_result.get(
                        provider_started_key,
                        "",
                    )
                    resolved_result[f"{attempt_prefix}_attempt_started"] = True
            if current_status == "preflighting" and normalized_status in {
                "publishing",
                "completed",
            }:
                if article is None:
                    raise GEOAutomationValidationError(
                        "完成平台预检前必须绑定文章快照"
                    )
                if normalized_preflight_fingerprint is None:
                    raise GEOAutomationValidationError(
                        "完成平台预检前必须提交预检文章指纹"
                    )
                current_article_fingerprint = _article_snapshot_fingerprint(article)
                if not _constant_text_equal(
                    normalized_preflight_fingerprint,
                    current_article_fingerprint,
                ):
                    raise GEOAutomationConflictError(
                        "平台预检后文章已发生变化，未确认预检完成"
                    )
                resolved_result = {
                    **resolved_result,
                    "article_snapshot_fingerprint": current_article_fingerprint,
                    "article_payload_fingerprint": (
                        article_publish_payload_fingerprint(dict(article))
                    ),
                }
            elif (
                normalized_status == "publishing"
                and previous_result.get("article_snapshot_fingerprint")
            ):
                resolved_result = {
                    **resolved_result,
                    "article_snapshot_fingerprint": previous_result[
                        "article_snapshot_fingerprint"
                    ],
                }
            resolved_error = (
                _normalize_mapping(error, "error", max_bytes=8_000)
                if error is not None
                else _decode_object(item_row["error_json"])
            )
            if normalized_status == "completed":
                publish_mode, resolved_publish_jobs = _validate_completed_item(
                    conn,
                    run_row=run_row,
                    item_id=item_id,
                    article_id=resolved_article_id,
                    current_status=current_status,
                )
                if publish_mode in {"demo", "real"}:
                    resolved_result = {
                        **resolved_result,
                        "published_platforms": [
                            job["platform"] for job in resolved_publish_jobs
                        ],
                    }
                resolved_error = {}
            elif normalized_status in ISSUE_ITEM_STATUSES and not resolved_error:
                resolved_error = {
                    "code": normalized_status,
                    "message": normalized_message or "该条目未完成",
                }

            cursor = conn.execute(
                """
                UPDATE geo_automation_items
                SET status = ?, stage = ?, message = ?, article_id = ?,
                    generation_request_id = ?, generation_run_id = ?,
                    publish_jobs_json = ?, result_json = ?, error_json = ?,
                    worker_lease_owner = CASE WHEN ? THEN ''
                        ELSE worker_lease_owner END,
                    worker_lease_stage = CASE WHEN ? THEN ''
                        ELSE worker_lease_stage END,
                    worker_lease_token = CASE WHEN ? THEN ''
                        ELSE worker_lease_token END,
                    worker_lease_expires_at = CASE WHEN ? THEN NULL
                        ELSE worker_lease_expires_at END,
                    state_version = state_version + 1,
                    started_at = CASE
                        WHEN started_at IS NULL AND ? != 'queued'
                        THEN CURRENT_TIMESTAMP ELSE started_at END,
                    finished_at = CASE
                        WHEN ? IN ('completed', 'need_action', 'failed', 'skipped')
                        THEN CURRENT_TIMESTAMP ELSE NULL END,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND run_id = ? AND state_version = ?
                """,
                (
                    normalized_status,
                    normalized_stage,
                    normalized_message,
                    resolved_article_id,
                    resolved_request_id,
                    resolved_generation_run_id,
                    _encode_json(resolved_publish_jobs),
                    _encode_json(resolved_result),
                    _encode_json(resolved_error),
                    int(normalized_status != current_status),
                    int(normalized_status != current_status),
                    int(normalized_status != current_status),
                    int(normalized_status != current_status),
                    normalized_status,
                    normalized_status,
                    item_id,
                    run_id,
                    expected_state_version,
                ),
            )
            if cursor.rowcount != 1:
                raise GEOAutomationConflictError("条目状态发生并发变化")
            _refresh_run_summary(conn, run_id)
            updated_run = _fetch_run(conn, run_id)
            payload = _serialize_run(conn, updated_run, include_items=True)
            conn.commit()
            return payload
        except Exception:
            conn.rollback()
            raise


def set_geo_automation_pause(
    database_path: str | Path,
    *,
    run_id: int,
    paused: bool,
    expected_state_version: int,
) -> dict[str, Any]:
    if not isinstance(paused, bool):
        raise GEOAutomationValidationError("paused 必须是布尔值")
    if (
        isinstance(expected_state_version, bool)
        or not isinstance(expected_state_version, int)
        or expected_state_version < 0
    ):
        raise GEOAutomationValidationError("expected_state_version 必须是非负整数")
    with closing(_connect(database_path)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = _fetch_run(conn, run_id)
            if row is None:
                raise GEOAutomationNotFoundError("一键 GEO 运行不存在")
            if int(row["state_version"]) != expected_state_version:
                raise GEOAutomationConflictError("运行状态已变化，请刷新后重试")
            if row["status"] in {"completed", "completed_with_issues", "cancelled"}:
                raise GEOAutomationConflictError("已结束的一键 GEO 运行不能暂停或恢复")
            cursor = conn.execute(
                """
                UPDATE geo_automation_runs
                SET pause_requested = ?, message = ?,
                    state_version = state_version + 1,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND state_version = ?
                """,
                (
                    int(paused),
                    "将在当前安全检查点暂停" if paused else "已恢复，等待继续执行",
                    run_id,
                    expected_state_version,
                ),
            )
            if cursor.rowcount != 1:
                raise GEOAutomationConflictError("运行状态发生并发变化")
            _refresh_run_summary(conn, run_id)
            updated = _serialize_run(conn, _fetch_run(conn, run_id), include_items=True)
            conn.commit()
            return updated
        except Exception:
            conn.rollback()
            raise


def _refresh_run_summary(conn: sqlite3.Connection, run_id: int) -> None:
    run = _fetch_run(conn, run_id)
    counts = {
        str(row["status"]): int(row["count"])
        for row in conn.execute(
            """
            SELECT status, COUNT(*) AS count
            FROM geo_automation_items
            WHERE run_id = ?
            GROUP BY status
            """,
            (run_id,),
        ).fetchall()
    }
    completed_count = sum(counts.get(status, 0) for status in TERMINAL_ITEM_STATUSES)
    issue_count = sum(counts.get(status, 0) for status in ISSUE_ITEM_STATUSES)
    active_count = sum(counts.get(status, 0) for status in ACTIVE_ITEM_STATUSES)
    total = int(run["total_items"])
    pause_is_unsafe = False
    if bool(run["pause_requested"]):
        active_model_items = conn.execute(
            """
            SELECT * FROM geo_automation_items
            WHERE run_id = ? AND status IN ('generating', 'optimizing')
            """,
            (run_id,),
        ).fetchall()
        pause_is_unsafe = any(
            _worker_lease_is_active(conn, int(item["id"]))
            or _external_attempt_requires_manual_recovery(
                conn,
                item,
                str(item["status"]),
            )
            for item in active_model_items
        )
        if not pause_is_unsafe:
            pause_is_unsafe = conn.execute(
                """
                SELECT 1
                FROM publish_jobs AS j
                JOIN geo_automation_items AS i
                  ON i.id = j.automation_item_id
                WHERE i.run_id = ? AND j.status = 'processing'
                LIMIT 1
                """,
                (run_id,),
            ).fetchone() is not None
    if completed_count >= total:
        status = "completed_with_issues" if issue_count else "completed"
        message = (
            f"流程结束，{issue_count} 篇需要处理"
            if issue_count
            else "全部文章与已授权渠道处理完成"
        )
        finished_sql = "CURRENT_TIMESTAMP"
    elif bool(run["pause_requested"]) and pause_is_unsafe:
        status = "running"
        message = "暂停请求已记录；正在等待当前外部调用到达安全检查点"
        finished_sql = "NULL"
    elif bool(run["pause_requested"]):
        status = "paused"
        message = "流程已停在安全检查点"
        finished_sql = "NULL"
    elif active_count or completed_count:
        status = "running"
        message = "正在按顺序执行，单篇异常不会阻断后续文章"
        finished_sql = "NULL"
    else:
        status = "queued"
        message = "配置已保存，等待开始"
        finished_sql = "NULL"
    conn.execute(
        f"""
        UPDATE geo_automation_runs
        SET status = ?, message = ?, completed_items = ?, issue_items = ?,
            started_at = CASE
                WHEN ? IN ('running', 'paused')
                THEN COALESCE(started_at, CURRENT_TIMESTAMP)
                ELSE started_at END,
            finished_at = {finished_sql},
            state_version = state_version + 1,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (status, message, completed_count, issue_count, status, run_id),
    )


def _normalize_channels(raw_channels: Any, publish_mode: str) -> list[dict[str, Any]]:
    if raw_channels is None:
        raw_channels = []
    if not isinstance(raw_channels, list):
        raise GEOAutomationValidationError("channels 必须是数组")
    if len(raw_channels) > MAX_AUTOMATION_CHANNELS:
        raise GEOAutomationValidationError(
            f"一次最多选择 {MAX_AUTOMATION_CHANNELS} 个渠道"
        )
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw_channel in raw_channels:
        if isinstance(raw_channel, str):
            raw_channel = {"platform": raw_channel}
        if not isinstance(raw_channel, Mapping):
            raise GEOAutomationValidationError("渠道配置必须是对象")
        platform = normalize_platform_key(str(raw_channel.get("platform") or ""))
        if not platform:
            raise GEOAutomationValidationError("包含不支持的发布渠道")
        if platform in seen:
            continue
        account_id = _optional_positive_integer(
            raw_channel.get("account_id"), f"{platform} account_id"
        )
        if publish_mode == "real" and account_id is None:
            raise GEOAutomationValidationError(
                f"真实发布必须为 {platform} 绑定明确账号"
            )
        channel = {"platform": platform, "account_id": account_id}
        if publish_mode == "real":
            credential_epoch = _optional_positive_integer(
                raw_channel.get("credential_epoch"),
                f"{platform} credential_epoch",
            )
            if credential_epoch is None:
                raise GEOAutomationValidationError(
                    f"真实发布必须冻结 {platform} 账号凭证版本"
                )
            channel["credential_epoch"] = credential_epoch
        result.append(channel)
        seen.add(platform)
    return result


def _assert_live_real_publish_accounts(
    conn: sqlite3.Connection,
    config: Mapping[str, Any],
) -> None:
    if str(config.get("publish_mode") or "") != "real":
        return
    for channel in config.get("channels", []):
        platform = str(channel.get("platform") or "")
        capability = PLATFORM_BY_KEY.get(platform)
        account_id = channel.get("account_id")
        credential_epoch = channel.get("credential_epoch")
        if capability is None or account_id is None or credential_epoch is None:
            raise GEOAutomationCredentialChangedError(
                "真实发布账号快照不完整，请重新核对后启动"
            )
        account = conn.execute(
            """
            SELECT type, status, credential_epoch
            FROM user_info
            WHERE id = ?
            """,
            (int(account_id),),
        ).fetchone()
        if (
            account is None
            or int(account["type"]) != int(capability["account_type"])
            or int(account["status"]) != 1
            or int(account["credential_epoch"]) != int(credential_epoch)
        ):
            raise GEOAutomationCredentialChangedError(
                f"{platform} 账号凭证已变化或当前不可用，请重新选择账号并确认"
            )


def _normalize_authorization_scope(
    scope: Mapping[str, Any] | None,
    *,
    project_id: int,
    config: Mapping[str, Any],
    project: Mapping[str, Any],
) -> dict[str, Any]:
    project_snapshot = geo_automation_project_snapshot(project)
    if scope is None:
        return {
            "confirmed": False,
            "project_id": project_id,
            "topic_count": len(config["topics"]),
            "publish_mode": config["publish_mode"],
            "channels": config["channels"],
            "project_snapshot": project_snapshot,
        }
    if not isinstance(scope, Mapping) or scope.get("confirmed") is not True:
        raise GEOAutomationValidationError("必须明确确认本次一键 GEO 执行范围")
    expected = {
        "project_id": project_id,
        "topic_count": len(config["topics"]),
        "publish_mode": config["publish_mode"],
        "channels": config["channels"],
        "project_snapshot": project_snapshot,
    }
    actual = {
        "project_id": scope.get("project_id"),
        "topic_count": scope.get("topic_count"),
        "publish_mode": scope.get("publish_mode"),
        "channels": scope.get("channels"),
        "project_snapshot": geo_automation_project_snapshot(
            scope.get("project_snapshot")
            if isinstance(scope.get("project_snapshot"), Mapping)
            else {}
        ),
    }
    if _fingerprint(actual) != _fingerprint(expected):
        raise GEOAutomationConflictError("执行范围已变化，请重新核对后启动")
    return {"confirmed": True, **expected}


def _normalize_publish_jobs(
    publish_jobs: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    if isinstance(publish_jobs, (str, bytes)) or not isinstance(
        publish_jobs, Sequence
    ):
        raise GEOAutomationValidationError("publish_jobs 必须是数组")
    if len(publish_jobs) > MAX_AUTOMATION_CHANNELS:
        raise GEOAutomationValidationError("publish_jobs 数量超过渠道上限")
    result = []
    seen = set()
    for raw in publish_jobs:
        if not isinstance(raw, Mapping):
            raise GEOAutomationValidationError("publish_jobs 条目必须是对象")
        platform = normalize_platform_key(str(raw.get("platform") or ""))
        job_id = _optional_positive_integer(raw.get("job_id"), "job_id")
        if not platform or job_id is None:
            raise GEOAutomationValidationError("发布任务必须包含 platform 和 job_id")
        if platform in seen:
            raise GEOAutomationValidationError("同一渠道只能绑定一个发布任务")
        result.append(
            {
                "platform": platform,
                "job_id": job_id,
                "status": str(raw.get("status") or "queued")[:40],
                "message": str(raw.get("message") or "")[:500],
                "result_url": str(raw.get("result_url") or "")[:2_048],
                "outcome_category": str(raw.get("outcome_category") or "")[:80],
            }
        )
        seen.add(platform)
    return result


def _canonicalize_publish_job_evidence(
    conn: sqlite3.Connection,
    *,
    item_id: int,
    article_id: int | None,
    publish_jobs: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    canonical = []
    for publish_job in publish_jobs:
        row = conn.execute(
            """
            SELECT id, article_id, platform, status, message, result_url
            FROM publish_jobs
            WHERE id = ? AND automation_item_id = ? AND platform = ?
            """,
            (publish_job["job_id"], item_id, publish_job["platform"]),
        ).fetchone()
        if (
            row is None
            or article_id is None
            or int(row["article_id"]) != article_id
        ):
            raise GEOAutomationValidationError(
                "发布任务证据不属于该一键 GEO 条目"
            )
        canonical.append(_publish_job_evidence_from_row(row))
    return canonical


def _validate_completed_item(
    conn: sqlite3.Connection,
    *,
    run_row: sqlite3.Row,
    item_id: int,
    article_id: int | None,
    current_status: str,
) -> tuple[str, list[dict[str, Any]]]:
    config = _decode_object(run_row["config_json"])
    publish_mode = str(config.get("publish_mode") or "")
    rows = conn.execute(
        """
        SELECT id, article_id, automation_item_id, idempotency_key,
               account_id, platform, status, message, result_url, demo
        FROM publish_jobs
        WHERE automation_item_id = ?
        ORDER BY id
        """,
        (item_id,),
    ).fetchall()
    if publish_mode == "prepare":
        if current_status != "preflighting":
            raise GEOAutomationValidationError(
                "仅准备运行只能从平台预检阶段完成"
            )
        if rows:
            raise GEOAutomationValidationError(
                "仅准备运行不能绑定发布任务"
            )
        return publish_mode, []
    if publish_mode not in {"demo", "real"}:
        raise GEOAutomationValidationError("一键 GEO 发布模式快照无效")
    if current_status != "publishing":
        raise GEOAutomationValidationError(
            "自动发布运行只能从发布阶段完成"
        )
    if article_id is None:
        raise GEOAutomationValidationError("自动发布完成前必须绑定文章")

    channels = config.get("channels")
    if not isinstance(channels, list) or not channels:
        raise GEOAutomationValidationError("一键 GEO 渠道授权快照无效")
    expected_by_platform = {
        str(channel.get("platform") or ""): channel
        for channel in channels
        if isinstance(channel, Mapping)
    }
    actual_by_platform = {str(row["platform"]): row for row in rows}
    if (
        len(expected_by_platform) != len(channels)
        or set(actual_by_platform) != set(expected_by_platform)
    ):
        raise GEOAutomationValidationError(
            "发布任务尚未完整覆盖全部授权渠道"
        )

    canonical = []
    for channel in channels:
        platform = str(channel["platform"])
        row = actual_by_platform[platform]
        expected_account_id = channel.get("account_id")
        actual_account_id = row["account_id"]
        if (
            int(row["article_id"]) != article_id
            or bool(row["demo"]) != (publish_mode == "demo")
            or (
                None if actual_account_id is None else int(actual_account_id)
            )
            != (
                None if expected_account_id is None else int(expected_account_id)
            )
            or not str(row["idempotency_key"] or "").strip()
        ):
            raise GEOAutomationValidationError(
                f"{platform} 发布任务与授权快照不一致"
            )
        if str(row["status"]) != "success":
            raise GEOAutomationValidationError(
                f"{platform} 发布任务尚未确认成功"
            )
        canonical.append(_publish_job_evidence_from_row(row))
    return publish_mode, canonical


def _publish_job_evidence_from_row(row: sqlite3.Row) -> dict[str, Any]:
    status = str(row["status"] or "")
    return {
        "platform": str(row["platform"] or ""),
        "job_id": int(row["id"]),
        "status": status,
        "message": str(row["message"] or "")[:500],
        "result_url": str(row["result_url"] or "")[:2_048],
        "outcome_category": status,
    }


def _assert_project_snapshot_current(
    conn: sqlite3.Connection,
    run_row: sqlite3.Row,
) -> None:
    expected = _decode_object(run_row["project_snapshot"])
    row = conn.execute(
        """
        SELECT id, name, website, product, industry, description,
               keywords, competitors, updated_at
        FROM projects
        WHERE id = ?
        """,
        (int(run_row["project_id"]),),
    ).fetchone()
    if row is None:
        raise GEOAutomationProjectChangedError("品牌项目已不存在，运行不能继续")
    current = {
        "id": int(row["id"]),
        "name": row["name"],
        "website": row["website"],
        "product": row["product"],
        "industry": row["industry"],
        "description": row["description"],
        "keywords": _decode_list(row["keywords"]),
        "competitors": _decode_list(row["competitors"]),
        "updated_at": row["updated_at"],
    }
    if _fingerprint(current) != _fingerprint(expected):
        raise GEOAutomationProjectChangedError(
            "品牌项目底稿已在运行后变更；已停止新步骤以避免混用快照"
        )


def _article_snapshot_fingerprint(article: Mapping[str, Any]) -> str:
    return article_content_fingerprint(dict(article))


def _serialize_run(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
    *,
    include_items: bool,
) -> dict[str, Any]:
    run = dict(row)
    run["project_snapshot"] = _decode_object(run.pop("project_snapshot"))
    run["config"] = _decode_object(run.pop("config_json"))
    run["pause_requested"] = bool(run["pause_requested"])
    run["progress"] = (
        round(int(run["completed_items"]) / int(run["total_items"]) * 100)
        if int(run["total_items"])
        else 0
    )
    if include_items:
        rows = conn.execute(
            """
            SELECT * FROM geo_automation_items
            WHERE run_id = ?
            ORDER BY position, id
            """,
            (int(row["id"]),),
        ).fetchall()
        run["items"] = [_serialize_item(item) for item in rows]
    return run


def _serialize_item(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    item["publish_jobs"] = _decode_list(item.pop("publish_jobs_json"))
    item["result"] = _decode_object(item.pop("result_json"))
    item["error"] = _decode_object(item.pop("error_json"))
    item.pop("worker_lease_token", None)
    expires_at = str(item.get("worker_lease_expires_at") or "")
    try:
        expires = datetime.fromisoformat(expires_at).replace(tzinfo=timezone.utc)
    except ValueError:
        expires = None
    item["worker_lease_active"] = bool(
        item.get("worker_lease_owner")
        and expires is not None
        and expires > datetime.now(timezone.utc)
    )
    return item


def _worker_lease_is_active(conn: sqlite3.Connection, item_id: int) -> bool:
    row = conn.execute(
        """
        SELECT CASE
            WHEN worker_lease_owner != ''
             AND worker_lease_expires_at IS NOT NULL
             AND worker_lease_expires_at > CURRENT_TIMESTAMP
            THEN 1 ELSE 0 END AS active
        FROM geo_automation_items WHERE id = ?
        """,
        (item_id,),
    ).fetchone()
    return bool(row and row["active"])


def _external_attempt_requires_manual_recovery(
    conn: sqlite3.Connection,
    item_row: sqlite3.Row,
    stage: str,
) -> bool:
    result = _decode_object(item_row["result_json"])
    if stage == "generating":
        started = (
            result.get("generation_attempt_started") is True
            and result.get("generation_completed") is not True
        )
        if not started:
            return False
        request_id = str(item_row["generation_request_id"] or "")
        generation_row = conn.execute(
            """
            SELECT status, project_id, result_payload, result_digest
            FROM content_generation_runs
            WHERE request_id = ?
            """,
            (request_id,),
        ).fetchone() if request_id else None
        if generation_row is None or str(generation_row["status"]) != "succeeded":
            return True
        run_row = conn.execute(
            "SELECT project_id FROM geo_automation_runs WHERE id = ?",
            (int(item_row["run_id"]),),
        ).fetchone()
        payload = _decode_object(generation_row["result_payload"])
        encoded_payload = _encode_json(payload).encode("utf-8")
        digest = hashlib.sha256(encoded_payload).hexdigest()
        return bool(
            run_row is None
            or int(generation_row["project_id"] or 0)
            != int(run_row["project_id"])
            or not str(payload.get("title") or "").strip()
            or not str(payload.get("content") or "").strip()
            or not _constant_text_equal(
                str(generation_row["result_digest"] or ""),
                digest,
            )
        )
    return (
        result.get("optimization_attempt_started") is True
        and result.get("optimization_completed") is not True
    )


def _serialize_worker_lease(item_row: sqlite3.Row) -> dict[str, Any]:
    return {
        "item_id": int(item_row["id"]),
        "worker_id": str(item_row["worker_lease_owner"] or ""),
        "stage": str(item_row["worker_lease_stage"] or ""),
        "lease_token": str(item_row["worker_lease_token"] or ""),
        "lease_epoch": int(item_row["worker_lease_epoch"] or 0),
        "expires_at": item_row["worker_lease_expires_at"],
    }


def geo_automation_project_snapshot(
    project: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        key: project.get(key)
        for key in (
            "id",
            "name",
            "website",
            "product",
            "industry",
            "description",
            "keywords",
            "competitors",
            "updated_at",
        )
    }


def _normalize_string_list(
    value: Any,
    *,
    field: str,
    max_items: int,
    max_length: int,
    required: bool,
) -> list[str]:
    if not isinstance(value, list):
        raise GEOAutomationValidationError(f"{field} 必须是字符串数组")
    result: list[str] = []
    for raw in value:
        if not isinstance(raw, str):
            raise GEOAutomationValidationError(f"{field} 必须是字符串数组")
        text = raw.strip()
        if not text:
            continue
        if len(text) > max_length:
            raise GEOAutomationValidationError(
                f"{field} 单项不能超过 {max_length} 个字符"
            )
        if text not in result:
            result.append(text)
    if required and not result:
        raise GEOAutomationValidationError(f"{field} 至少需要一项")
    if len(result) > max_items:
        raise GEOAutomationValidationError(f"{field} 一次最多 {max_items} 项")
    return result


def _normalize_text(
    value: Any,
    *,
    field: str,
    max_length: int,
    required: bool,
) -> str:
    if value is None:
        text = ""
    elif isinstance(value, str):
        text = value.strip()
    else:
        raise GEOAutomationValidationError(f"{field} 必须是字符串")
    if required and not text:
        raise GEOAutomationValidationError(f"{field} 不能为空")
    if len(text) > max_length:
        raise GEOAutomationValidationError(
            f"{field} 不能超过 {max_length} 个字符"
        )
    return text


def _normalize_mapping(
    value: Mapping[str, Any],
    field: str,
    *,
    max_bytes: int,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise GEOAutomationValidationError(f"{field} 必须是对象")
    result = dict(value)
    if len(_encode_json(result).encode("utf-8")) > max_bytes:
        raise GEOAutomationValidationError(f"{field} 内容过大")
    return result


def _normalize_identity(value: Any, field: str, max_length: int) -> str:
    text = str(value or "").strip()
    if (
        not text
        or len(text) > max_length
        or _IDENTITY_PATTERN.fullmatch(text) is None
    ):
        raise GEOAutomationValidationError(f"{field} 格式无效")
    return text


def _optional_positive_integer(value: Any, field: str) -> int | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        raise GEOAutomationValidationError(f"{field} 必须是正整数")
    try:
        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise GEOAutomationValidationError(f"{field} 必须是正整数") from exc
    if normalized <= 0:
        raise GEOAutomationValidationError(f"{field} 必须是正整数")
    return normalized


def _required_positive_integer(value: Any, field: str) -> int:
    normalized = _optional_positive_integer(value, field)
    if normalized is None:
        raise GEOAutomationValidationError(f"{field} 必须是正整数")
    return normalized


def _fetch_run(conn: sqlite3.Connection, run_id: int) -> sqlite3.Row | None:
    if isinstance(run_id, bool) or not isinstance(run_id, int) or run_id <= 0:
        raise GEOAutomationValidationError("run_id 必须是正整数")
    return conn.execute(
        "SELECT * FROM geo_automation_runs WHERE id = ?", (run_id,)
    ).fetchone()


def _connect(database_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(Path(database_path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def _resolve_existing_run_identity(
    by_key: sqlite3.Row | None,
    by_request: sqlite3.Row | None,
    *,
    request_id: str,
    idempotency_key: str,
) -> sqlite3.Row | None:
    if (
        by_key is not None
        and by_request is not None
        and int(by_key["id"]) != int(by_request["id"])
    ):
        raise GEOAutomationConflictError(
            "request_id 与 idempotency_key 已绑定不同运行"
        )
    if by_key is not None and str(by_key["request_id"]) != request_id:
        raise GEOAutomationConflictError("idempotency_key 已绑定其他 request_id")
    if (
        by_request is not None
        and str(by_request["idempotency_key"]) != idempotency_key
    ):
        raise GEOAutomationConflictError("request_id 已绑定其他 idempotency_key")
    return by_key or by_request


def _run_request_fingerprint(
    project_id: int,
    normalized_config: Mapping[str, Any],
    project_snapshot: Mapping[str, Any],
) -> str:
    return _fingerprint(
        {
            "schema": GEO_AUTOMATION_VERSION,
            "project_id": project_id,
            "project_snapshot": project_snapshot,
            "config": normalized_config,
        }
    )


def _fingerprint(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_encode_json(value).encode("utf-8")).hexdigest()


def _constant_text_equal(left: str, right: str) -> bool:
    # Both values are public SHA-256 digests. compare_digest avoids a separate
    # equality path while keeping this helper independent of secret material.
    import hmac

    return hmac.compare_digest(left, right)


def _encode_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _decode_object(value: Any) -> dict[str, Any]:
    try:
        parsed = json.loads(value) if isinstance(value, str) else value
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _decode_list(value: Any) -> list[Any]:
    try:
        parsed = json.loads(value) if isinstance(value, str) else value
    except (TypeError, json.JSONDecodeError):
        return []
    return parsed if isinstance(parsed, list) else []
