"""Parallel, truth-labeled visibility sampling across configured API models.

This service orchestrates existing durable single-model sample requests.  It
does not turn provider API responses into evidence about a consumer web page or
mobile app: those entry points remain explicitly unverified until separately
captured evidence is imported.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from services.ai_service import (
    AISettings,
    provider_label_for_settings,
    resolve_ai_chat_completions_url,
)
from services.ai_visibility_sampling_service import run_ai_visibility_sample
from services.ai_visibility_service import analyze_ai_visibility


VISIBILITY_PANEL_VERSION = "api-model-visibility-panel-1.2.0"
_LEGACY_VERSIONED_REQUEST_SHAPE_VERSIONS = (
    "api-model-visibility-panel-1.1.0",
    "api-model-visibility-panel-1.2.0",
)
MAX_PANEL_MODELS = 3
PANEL_IN_PROGRESS_EXPIRY_SECONDS = 15 * 60
_PANEL_RECORD_TYPE = "ai_visibility_model_panel"


class VisibilityPanelValidationError(ValueError):
    pass


class VisibilityPanelConflictError(VisibilityPanelValidationError):
    """Raised before any new call when an identity is already bound elsewhere."""


class VisibilityPanelInProgressError(VisibilityPanelValidationError):
    """Raised when the frozen panel manifest may still own provider calls."""

    def __init__(self, record: Mapping[str, Any]) -> None:
        super().__init__(
            "该多模型采样仍在处理中；请按原 request_id 查询，不要自动重试或新增角色"
        )
        self.record = dict(record)


class VisibilityPanelRequestNotFoundError(LookupError):
    pass


def get_ai_visibility_panel_request(
    database_path: str | Path,
    request_id: Any,
    *,
    current_time: datetime | None = None,
) -> dict[str, Any]:
    """Read a frozen panel and its child progress without writes or model calls.

    A parent left in ``started`` remains ``processing`` for fifteen minutes.
    Once that read-only age limit is reached, this helper returns a synthetic
    ``need_action`` / ``outcome_unknown`` aggregate.  It deliberately does not
    change the stored row, retry a child request, or infer success merely from
    child rows that happen to exist.
    """

    normalized_request_id = _request_identifier(request_id, "X-Request-ID", 128)
    with closing(_connect(database_path)) as conn:
        row = conn.execute(
            "SELECT * FROM ai_visibility_sample_requests WHERE request_id = ?",
            (normalized_request_id,),
        ).fetchone()
        if row is None:
            raise VisibilityPanelRequestNotFoundError("多模型采样请求不存在")
        record = _serialize_panel_row(row)
        aggregate = record["aggregate_result"]
        targets = (
            list(aggregate.get("targets") or [])
            if isinstance(aggregate, Mapping)
            else _recover_manifest_targets(conn, record["manifest"])
        )
    stored_status = record["status"]
    in_progress_age_seconds = None
    in_progress_expired = False
    if stored_status == "started":
        in_progress_age_seconds = _started_age_seconds(
            record["created_at"], current_time=current_time
        )
        in_progress_expired = (
            in_progress_age_seconds >= PANEL_IN_PROGRESS_EXPIRY_SECONDS
        )
        aggregate = _started_panel_result(
            record=record,
            targets=targets,
            age_seconds=in_progress_age_seconds,
            expired=in_progress_expired,
        )
    observed_status = (
        "need_action"
        if in_progress_expired
        else ("processing" if stored_status == "started" else stored_status)
    )
    return {
        "id": record["id"],
        "project_id": record["project_id"],
        "request_id": record["request_id"],
        "idempotency_key": record["idempotency_key"],
        "request_fingerprint": record["request_fingerprint"],
        "status": observed_status,
        "stored_status": stored_status,
        "in_progress": stored_status == "started" and not in_progress_expired,
        "in_progress_age_seconds": in_progress_age_seconds,
        "in_progress_expired": in_progress_expired,
        "in_progress_expiry_seconds": PANEL_IN_PROGRESS_EXPIRY_SECONDS,
        "http_status": record["http_status"],
        "manifest": record["manifest"],
        "targets": targets,
        "result": aggregate,
        "created_at": record["created_at"],
        "updated_at": record["updated_at"],
        "recovery_policy": {
            "read_only": True,
            "automatic_retry": False,
            "same_idempotency_key_adds_no_roles": True,
            "expired_parent_is_not_retried": True,
        },
    }


def run_ai_visibility_panel(
    database_path,
    *,
    project: Mapping[str, Any],
    prompt: str,
    sample_count: int,
    aliases: Sequence[Any] | None,
    competitors: Sequence[Any] | None,
    persist: bool,
    confirm_external_call: bool,
    targets: Sequence[Mapping[str, Any]],
    request_id: str,
    idempotency_key: str,
    runner: Callable[..., Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Run one frozen, durable comparison across distinct API models.

    A parent request is reserved before any child provider call. Reusing its
    idempotency key returns the saved aggregate (or an in-progress recovery
    record) and can never append newly configured roles.
    """

    normalized = _validate_panel_request(
        project=project,
        prompt=prompt,
        sample_count=sample_count,
        aliases=aliases,
        competitors=competitors,
        persist=persist,
        confirm_external_call=confirm_external_call,
        targets=targets,
        request_id=request_id,
        idempotency_key=idempotency_key,
    )
    existing = _find_existing_panel(
        database_path,
        request_id=normalized["request_id"],
        idempotency_key=normalized["idempotency_key"],
        request_shape_sha256=normalized["request_shape_sha256"],
        compatible_request_shape_sha256s=normalized[
            "compatible_request_shape_sha256s"
        ],
    )
    if existing is not None:
        return _replay_panel_or_raise(database_path, existing)

    runner = runner or run_ai_visibility_sample
    normalized_targets, runnable = _normalize_targets(normalized["targets"])
    if len(runnable) < 2:
        raise VisibilityPanelValidationError(
            "多模型对照至少需要 2 个规范化后不同、且已独立配置的 API 模型；"
            "回退角色和重复 provider/endpoint/model 不会调用"
        )

    manifest = _panel_manifest(
        normalized_targets,
        request_id=normalized["request_id"],
        idempotency_key=normalized["idempotency_key"],
        request_shape_sha256=normalized["request_shape_sha256"],
        requested_sample_count_per_role=normalized["sample_count"],
    )
    fingerprint = _panel_fingerprint(normalized, manifest)
    request_record, replayed = _begin_panel_request(
        database_path,
        project_id=normalized["project_id"],
        request_id=normalized["request_id"],
        idempotency_key=normalized["idempotency_key"],
        request_fingerprint=fingerprint,
        request_shape_sha256=normalized["request_shape_sha256"],
        compatible_request_shape_sha256s=normalized[
            "compatible_request_shape_sha256s"
        ],
        manifest=manifest,
    )
    if replayed:
        return _replay_panel_or_raise(database_path, request_record)

    outcomes: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=min(MAX_PANEL_MODELS, len(runnable))) as pool:
        future_map = {
            pool.submit(
                runner,
                database_path,
                project=project,
                prompt=normalized["prompt"],
                sample_count=normalized["sample_count"],
                aliases=normalized["aliases"],
                competitors=normalized["competitors"],
                persist=normalized["persist"],
                confirm_external_call=True,
                settings=target["settings"],
                request_id=target["subrequest_id"],
                idempotency_key=target["subrequest_idempotency_key"],
            ): target
            for target in runnable
        }
        for future in as_completed(future_map):
            target = future_map[future]
            try:
                raw = future.result()
                payload = dict(raw.get("payload") or {})
                data = dict(payload.get("data") or {})
                outcomes[target["role"]] = _completed_target(target, raw, data)
            except Exception as exc:  # preserve sibling evidence; never auto-retry
                outcomes[target["role"]] = _failed_target(target, exc)

    results = [
        _skipped_target(target)
        if target.get("skip_reason")
        else outcomes[target["role"]]
        for target in normalized_targets
    ]
    aggregate = _build_panel_result(
        request_record=request_record,
        manifest=manifest,
        results=results,
        prompt_sha256=normalized["prompt_sha256"],
        replayed=False,
        analysis_context={
            "project_id": normalized["project_id"],
            "project_name": normalized["project_name"],
            "project_product": normalized["project_product"],
            "project_keywords": normalized["project_keywords"],
            "project_website": normalized["project_website"],
            "stored_competitors": normalized["stored_competitors"],
            "prompt": normalized["prompt"],
            "aliases": normalized["aliases"],
            "competitors": normalized["competitors"],
        },
    )
    _finish_panel_request(
        database_path,
        record_id=request_record["id"],
        status=aggregate["status"],
        http_status=_panel_http_status(aggregate["status"]),
        manifest=manifest,
        aggregate=aggregate,
    )
    return aggregate


def _validate_panel_request(
    *,
    project: Mapping[str, Any],
    prompt: Any,
    sample_count: Any,
    aliases: Sequence[Any] | None,
    competitors: Sequence[Any] | None,
    persist: Any,
    confirm_external_call: Any,
    targets: Sequence[Mapping[str, Any]],
    request_id: Any,
    idempotency_key: Any,
) -> dict[str, Any]:
    if confirm_external_call is not True:
        raise VisibilityPanelValidationError(
            "必须确认将同一 prompt 分别发送给每个列出的 API 模型"
        )
    if not isinstance(project, Mapping):
        raise VisibilityPanelValidationError("品牌项目不存在")
    raw_project_id = project.get("id")
    if isinstance(raw_project_id, bool):
        raise VisibilityPanelValidationError("project_id 必须是正整数")
    try:
        project_id = int(raw_project_id)
    except (TypeError, ValueError) as exc:
        raise VisibilityPanelValidationError("project_id 必须是正整数") from exc
    if project_id <= 0:
        raise VisibilityPanelValidationError("project_id 必须是正整数")
    if not isinstance(targets, Sequence) or isinstance(targets, (str, bytes)):
        raise VisibilityPanelValidationError("targets 必须是 AI 角色数组")
    if not 1 <= len(targets) <= MAX_PANEL_MODELS:
        raise VisibilityPanelValidationError(
            f"一次可见度面板必须包含 1 到 {MAX_PANEL_MODELS} 个角色"
        )
    roles: list[str] = []
    for raw_target in targets:
        if not isinstance(raw_target, Mapping):
            raise VisibilityPanelValidationError("每个目标必须是 AI 角色对象")
        role = str(raw_target.get("role") or "").strip().lower()
        if role not in {"writer", "reviewer", "evaluator"} or role in roles:
            raise VisibilityPanelValidationError(
                "角色仅支持不重复的 writer、reviewer、evaluator"
            )
        roles.append(role)
    if not isinstance(prompt, str):
        raise VisibilityPanelValidationError("prompt 必须是字符串")
    normalized_prompt = prompt.strip()
    if not normalized_prompt or "\0" in normalized_prompt:
        raise VisibilityPanelValidationError("prompt 不能为空")
    if len(normalized_prompt) > 8_000:
        raise VisibilityPanelValidationError("prompt 不能超过 8000 个字符")
    if not isinstance(sample_count, int) or isinstance(sample_count, bool):
        raise VisibilityPanelValidationError("sample_count 必须是整数")
    if not 1 <= sample_count <= 5:
        raise VisibilityPanelValidationError("sample_count 必须是 1 到 5")
    if not isinstance(persist, bool):
        raise VisibilityPanelValidationError("persist 必须是布尔值")
    normalized_aliases = _optional_sequence(aliases, "aliases")
    normalized_competitors = _optional_sequence(competitors, "competitors")
    project_name = str(project.get("name") or "").strip()
    project_product = str(project.get("product") or "").strip()
    project_keywords = project.get("keywords") or []
    project_website = str(project.get("website") or "").strip()
    stored_competitors = project.get("competitors") or []
    normalized_request_id = _request_identifier(request_id, "X-Request-ID", 128)
    normalized_key = _request_identifier(idempotency_key, "Idempotency-Key", 150)
    prompt_sha256 = hashlib.sha256(normalized_prompt.encode("utf-8")).hexdigest()
    request_shape = {
        "project_id": project_id,
        "prompt_sha256": prompt_sha256,
        "sample_count": sample_count,
        "aliases": normalized_aliases,
        "competitors": normalized_competitors,
        "persist": persist,
        "roles": roles,
    }
    # The idempotency shape describes external-call semantics, not the local
    # presentation/aggregation implementation.  Older 1.1 releases included
    # panel_version in this hash, so accept those exact legacy hashes during
    # replay while storing the stable version-independent hash going forward.
    compatible_request_shape_sha256s = tuple(
        _json_digest({**request_shape, "panel_version": legacy_version})
        for legacy_version in _LEGACY_VERSIONED_REQUEST_SHAPE_VERSIONS
    )
    return {
        "project_id": project_id,
        "project_name": project_name,
        "project_product": project_product,
        "project_keywords": list(project_keywords),
        "project_website": project_website,
        "stored_competitors": list(stored_competitors),
        "prompt": normalized_prompt,
        "prompt_sha256": prompt_sha256,
        "sample_count": sample_count,
        "aliases": normalized_aliases,
        "competitors": normalized_competitors,
        "persist": persist,
        "targets": list(targets),
        "roles": roles,
        "request_id": normalized_request_id,
        "idempotency_key": normalized_key,
        "request_shape_sha256": _json_digest(request_shape),
        "compatible_request_shape_sha256s": compatible_request_shape_sha256s,
    }


def _normalize_targets(
    targets: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    normalized_targets: list[dict[str, Any]] = []
    runnable: list[dict[str, Any]] = []
    identities: dict[str, str] = {}
    for index, raw_target in enumerate(targets, start=1):
        role = str(raw_target.get("role") or "").strip().lower()
        role_status = (
            dict(raw_target.get("role_status") or {})
            if isinstance(raw_target.get("role_status"), Mapping)
            else {}
        )
        settings = raw_target.get("settings")
        target = {
            "index": index,
            "role": role,
            "role_status": role_status,
            "settings": settings,
        }
        if not isinstance(settings, AISettings):
            target["skip_reason"] = "role_not_explicitly_configured"
        elif role != "writer" and (
            not bool(role_status.get("configured"))
            or bool(role_status.get("fallback"))
        ):
            target["skip_reason"] = "writer_fallback_not_a_distinct_model"
        else:
            identity = _model_identity(settings)
            duplicate_role = identities.get(identity)
            if duplicate_role is not None:
                target["skip_reason"] = f"same_model_as_{duplicate_role}"
            else:
                identities[identity] = role
                target["identity"] = identity
                runnable.append(target)
        normalized_targets.append(target)
    return normalized_targets, runnable


def _panel_manifest(
    targets: Sequence[dict[str, Any]],
    *,
    request_id: str,
    idempotency_key: str,
    request_shape_sha256: str,
    requested_sample_count_per_role: int,
) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    for target in targets:
        settings = target.get("settings")
        runnable = not bool(target.get("skip_reason"))
        if runnable:
            target["subrequest_id"] = f"{request_id}:{target['role']}"
            target["subrequest_idempotency_key"] = (
                f"{idempotency_key}:{target['role']}"
            )
        entries.append(
            {
                "index": int(target["index"]),
                "role": target["role"],
                "runnable": runnable,
                "skip_reason": str(target.get("skip_reason") or ""),
                "provider": (
                    provider_label_for_settings(settings)
                    if isinstance(settings, AISettings)
                    else str((target.get("role_status") or {}).get("provider") or "")
                ),
                "model": (
                    str(settings.model)
                    if isinstance(settings, AISettings)
                    else str((target.get("role_status") or {}).get("model") or "")
                ),
                "identity_sha256": str(target.get("identity") or ""),
                "subrequest_id": str(target.get("subrequest_id") or ""),
                "subrequest_idempotency_key": str(
                    target.get("subrequest_idempotency_key") or ""
                ),
            }
        )
    return {
        "panel_version": VISIBILITY_PANEL_VERSION,
        "request_shape_sha256": request_shape_sha256,
        "roles": [entry["role"] for entry in entries],
        "runnable_role_count": sum(1 for entry in entries if entry["runnable"]),
        "requested_sample_count_per_role": int(requested_sample_count_per_role),
        "targets": entries,
        "frozen": True,
        "automatic_role_expansion": False,
    }


def _panel_fingerprint(
    normalized: Mapping[str, Any],
    manifest: Mapping[str, Any],
) -> str:
    return _json_digest(
        {
            "request_shape_sha256": normalized["request_shape_sha256"],
            "targets": [
                {
                    "role": item.get("role"),
                    "runnable": item.get("runnable"),
                    "skip_reason": item.get("skip_reason"),
                    "identity_sha256": item.get("identity_sha256"),
                }
                for item in manifest.get("targets", [])
            ],
            "panel_version": VISIBILITY_PANEL_VERSION,
        }
    )


def _build_panel_result(
    *,
    request_record: Mapping[str, Any],
    manifest: Mapping[str, Any],
    results: Sequence[Mapping[str, Any]],
    prompt_sha256: str,
    replayed: bool,
    analysis_context: Mapping[str, Any],
) -> dict[str, Any]:
    target_results = [dict(item) for item in results]
    manifest_identities = {
        str(item.get("role") or ""): str(item.get("identity_sha256") or "")
        for item in manifest.get("targets", [])
        if isinstance(item, Mapping)
    }
    for item in target_results:
        identity = manifest_identities.get(str(item.get("role") or ""), "")
        if identity:
            item["identity_sha256"] = identity
    executed = [
        item for item in target_results if item.get("execution_status") != "skipped"
    ]
    sampled = [
        item for item in executed if int(item.get("completed_sample_count") or 0) > 0
    ]
    distinct_sampled = {_panel_target_identity(item) for item in sampled}
    completed = [
        item
        for item in executed
        if item.get("status") == "completed"
        and int(item.get("requested_sample_count") or 0) > 0
        and int(item.get("completed_sample_count") or 0)
        >= int(item.get("requested_sample_count") or 0)
    ]
    comparison_eligible = bool(
        len(executed) >= 2
        and len(distinct_sampled) >= 2
        and len(completed) == len(executed)
    )
    panel_status = (
        "completed"
        if comparison_eligible
        else ("partial" if sampled else "need_action")
    )
    measurement_quality = _measurement_quality(
        executed=executed,
        sampled=sampled,
        distinct_model_count=len(distinct_sampled),
        comparison_eligible=comparison_eligible,
        requested_sample_count_per_role=int(
            manifest.get("requested_sample_count_per_role") or 0
        ),
    )
    api_channel_analysis, api_channel_analysis_unavailable_reason = (
        _build_api_channel_analysis(
            targets=completed,
            measurement_quality=measurement_quality,
            analysis_context=analysis_context,
        )
    )
    return {
        "status": panel_status,
        "need_action": panel_status != "completed",
        "panel_version": VISIBILITY_PANEL_VERSION,
        "panel_record_id": request_record.get("id"),
        "request_id": request_record["request_id"],
        "idempotency_key": request_record["idempotency_key"],
        "replayed": bool(replayed),
        "prompt_sha256": prompt_sha256,
        "manifest": dict(manifest),
        "requested_role_count": len(target_results),
        "executed_role_count": len(executed),
        "sampled_role_count": len(sampled),
        "distinct_model_count": len(distinct_sampled),
        "multi_model_executed": len(distinct_sampled) >= 2,
        "comparison_eligible": comparison_eligible,
        "measurement_quality": measurement_quality,
        "api_channel_analysis": api_channel_analysis,
        "api_channel_analysis_unavailable_reason": (
            api_channel_analysis_unavailable_reason
        ),
        "targets": target_results,
        "channel_coverage": [
            {
                "channel_type": "provider_api",
                "status": "sampled" if sampled else "not_completed",
                "sampled_model_count": len(distinct_sampled),
                "web_search_verified": False,
            },
            {
                "channel_type": "consumer_web",
                "status": "unverified",
                "sampled_model_count": 0,
                "web_search_verified": False,
            },
            {
                "channel_type": "mobile_app",
                "status": "unverified",
                "sampled_model_count": 0,
                "web_search_verified": False,
            },
        ],
        "consumer_parity": {
            "status": "unverified",
            "claim_allowed": False,
            "message": (
                "API 模型回答不能证明豆包、元宝、千问等消费者网页或手机 App 会返回"
                "相同答案；必须分别从真实入口采集同题、时间、账号与联网状态证据。"
            ),
        },
        "retry_policy": {
            "automatic_retry": False,
            "same_key_replays_frozen_manifest": True,
            "same_key_can_add_roles": False,
        },
        "claim_boundary": (
            "本面板只比较已配置的 OpenAI-compatible API 模型。"
            "completed 仅表示至少两个不同 provider/endpoint/model "
            "完整返回样本；"
            "不表示消费者入口、联网检索、收录或引用已验证。"
        ),
    }


def _measurement_quality(
    *,
    executed: Sequence[Mapping[str, Any]],
    sampled: Sequence[Mapping[str, Any]],
    distinct_model_count: int,
    comparison_eligible: bool,
    requested_sample_count_per_role: int,
) -> dict[str, Any]:
    """Describe whether this panel can support an API-channel GEO metric.

    A numerical score is deliberately withheld for zero/partial evidence.  A
    complete two-model comparison with fewer than five answers is useful as a
    directional check, but it is not yet a quantitative measurement.  Consumer
    app/web claims always remain disallowed because this panel calls provider
    APIs rather than those user-facing entry points.
    """

    valid_response_count = sum(
        max(0, int(item.get("completed_sample_count") or 0)) for item in sampled
    )
    requested_response_count = sum(
        max(0, int(item.get("requested_sample_count") or 0)) for item in executed
    )
    if requested_sample_count_per_role > 0:
        requested_response_count = max(
            requested_response_count,
            len(executed) * requested_sample_count_per_role,
        )
    requested_response_count = max(requested_response_count, valid_response_count)
    valid_response_rate = (
        round(valid_response_count / requested_response_count, 4)
        if requested_response_count
        else None
    )

    if not valid_response_count:
        status = "invalid"
        confidence_level = "NONE"
        reason = "未取得任何有效回答，当前批次不可量化。"
    elif not comparison_eligible:
        status = "insufficient"
        confidence_level = "LOW"
        reason = "有效模型或完整样本不足，当前结果只能用于排障，不能形成对照分。"
    elif valid_response_count < 5:
        status = "directional"
        confidence_level = "LOW"
        reason = "至少两个模型完整返回，但有效回答少于 5 条，仅能作方向性判断。"
    else:
        status = "quantitative"
        confidence_level = (
            "HIGH"
            if valid_response_count >= 10 and distinct_model_count >= 3
            else "MEDIUM"
        )
        reason = "API 渠道样本达到最低量化门槛；仍不能外推到消费者网页或 App。"

    return {
        "status": status,
        "score_allowed": status == "quantitative",
        "directional_comparison_allowed": bool(comparison_eligible),
        "api_channel_claim_allowed": status == "quantitative",
        "consumer_geo_claim_allowed": False,
        "confidence_level": confidence_level,
        "valid_response_count": valid_response_count,
        "requested_response_count": requested_response_count,
        "valid_response_rate": valid_response_rate,
        "valid_model_count": int(distinct_model_count),
        "minimum_distinct_models": 2,
        "minimum_quantitative_responses": 5,
        "reason": reason,
    }


def _build_api_channel_analysis(
    *,
    targets: Sequence[Mapping[str, Any]],
    measurement_quality: Mapping[str, Any],
    analysis_context: Mapping[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Aggregate already captured API answers without another model call."""

    quality_status = str(measurement_quality.get("status") or "invalid")
    if quality_status != "quantitative":
        return None, _api_analysis_unavailable(
            code=f"measurement_quality_{quality_status}",
            message=str(measurement_quality.get("reason") or "当前批次未达到量化门槛。"),
            measurement_quality_status=quality_status,
        )

    responses: list[dict[str, Any]] = []
    source_models: dict[str, dict[str, Any]] = {}
    for target in targets:
        analysis = target.get("analysis")
        samples = analysis.get("samples") if isinstance(analysis, Mapping) else None
        if not isinstance(samples, list):
            continue
        target_provider = str(target.get("provider") or "unknown").strip() or "unknown"
        target_model = str(target.get("model") or "unknown").strip() or "unknown"
        for sample in samples:
            if not isinstance(sample, Mapping):
                continue
            text = sample.get("text")
            if not isinstance(text, str) or not text.strip():
                continue
            provider = (
                str(sample.get("provider") or target_provider).strip()
                or target_provider
            )
            model = str(sample.get("model") or target_model).strip() or target_model
            entry_point = (
                str(
                    sample.get("entry_point")
                    or target.get("entry_point")
                    or "openai_compatible_chat_completions"
                ).strip()
                or "openai_compatible_chat_completions"
            )
            response = {
                "text": text,
                "provider": provider,
                "model": model,
                # The panel only invoked provider APIs. Never promote a sample
                # to consumer web/app evidence based on provider output fields.
                "channel_type": "provider_api",
                "entry_point": entry_point,
                "web_search_verified": sample.get("web_search_verified") is True,
                "consumer_surface_verified": False,
            }
            responses.append(response)
            identity_sha256 = str(target.get("identity_sha256") or "")
            key = _panel_target_identity(target)
            source = source_models.setdefault(
                key,
                {
                    "provider": provider,
                    "model": model,
                    "identity_sha256": identity_sha256,
                    "channel_type": "provider_api",
                    "entry_points": set(),
                    "roles": set(),
                    "sample_count": 0,
                    "consumer_surface_verified": False,
                },
            )
            source["entry_points"].add(entry_point)
            source["roles"].add(str(target.get("role") or ""))
            source["sample_count"] += 1

    expected_sample_count = int(
        measurement_quality.get("valid_response_count") or 0
    )
    if (
        len(responses) != expected_sample_count
        or len(source_models)
        < int(measurement_quality.get("minimum_distinct_models") or 2)
    ):
        return None, _api_analysis_unavailable(
            code="aggregate_source_samples_incomplete",
            message=(
                "质量门已达到量化级别，但成功目标未保留完整的原始回答样本或独立模型元数据；"
                "未生成 API 渠道聚合指标。"
            ),
            measurement_quality_status=quality_status,
        )

    try:
        aggregate = analyze_ai_visibility(
            project_id=int(analysis_context["project_id"]),
            project_name=str(analysis_context.get("project_name") or ""),
            project_product=str(analysis_context.get("project_product") or ""),
            project_keywords=analysis_context.get("project_keywords") or [],
            project_website=str(analysis_context.get("project_website") or ""),
            stored_competitors=analysis_context.get("stored_competitors") or [],
            prompt=str(analysis_context.get("prompt") or ""),
            responses=responses,
            aliases=analysis_context.get("aliases"),
            competitors=analysis_context.get("competitors"),
        )
    except Exception as exc:
        return None, {
            **_api_analysis_unavailable(
                code="aggregate_analysis_unavailable",
                message=(
                    "已保留各模型原始结果，但本地 API 渠道聚合分析失败；"
                    "面板未重试模型，也未把不完整指标当成量化结果。"
                ),
                measurement_quality_status=quality_status,
            ),
            "error_type": type(exc).__name__,
        }

    serialized_sources = [
        {
            **source,
            "entry_points": sorted(source["entry_points"]),
            "roles": sorted(role for role in source["roles"] if role),
        }
        for source in source_models.values()
    ]
    result = dict(aggregate)
    result["status"] = "available"
    result["channel_type"] = "provider_api"
    result["metric_scope"] = "configured_provider_api_responses_only"
    result["consumer_surface_verified"] = False
    result["consumer_geo_claim_allowed"] = False
    result["single_composite_score_produced"] = False
    result["source_model_count"] = len(serialized_sources)
    result["source_models"] = serialized_sources
    result["methodology"] = {
        **dict(result.get("methodology") or {}),
        "panel_aggregation": True,
        "aggregation_external_model_call": False,
        "source_responses_from_external_models": True,
        "source_channel": "provider_api",
        "consumer_surface_inference": False,
    }
    return result, None


def _api_analysis_unavailable(
    *,
    code: str,
    message: str,
    measurement_quality_status: str,
) -> dict[str, Any]:
    return {
        "code": code,
        "message": message,
        "measurement_quality_status": measurement_quality_status,
        "channel_type": "provider_api",
        "external_call_attempted": False,
        "consumer_surface_verified": False,
    }


def _started_panel_result(
    *,
    record: Mapping[str, Any],
    targets: Sequence[Mapping[str, Any]],
    age_seconds: int,
    expired: bool,
) -> dict[str, Any]:
    """Build a display-only view for an unfinished persisted parent request."""

    target_results = [dict(item) for item in targets]
    manifest = (
        record.get("manifest")
        if isinstance(record.get("manifest"), Mapping)
        else {}
    )
    manifest_identities = {
        str(item.get("role") or ""): str(item.get("identity_sha256") or "")
        for item in manifest.get("targets", [])
        if isinstance(item, Mapping)
    }
    for item in target_results:
        identity = manifest_identities.get(str(item.get("role") or ""), "")
        if identity:
            item["identity_sha256"] = identity
    sampled = [
        item
        for item in target_results
        if int(item.get("completed_sample_count") or 0) > 0
    ]
    distinct_sampled = {_panel_target_identity(item) for item in sampled}
    observed_executed = [
        item
        for item in target_results
        if item.get("execution_status")
        in {"executed", "failed", "outcome_unknown", "in_progress"}
    ]
    status = "need_action" if expired else "processing"
    provider_channel_status = "outcome_unknown" if expired else "processing"
    failure = None
    if expired:
        failure = {
            "code": "panel_outcome_unknown",
            "message": (
                "多模型父请求超过 15 分钟仍未收口；即使存在子请求记录，"
                "也不能据此推断整组调用成功。请先核对模型账单和既有结果，"
                "系统不会自动重试。"
            ),
            "outcome_unknown": True,
            "automatic_retry": False,
        }
    measurement_quality = _measurement_quality(
        executed=observed_executed,
        sampled=sampled,
        distinct_model_count=len(distinct_sampled),
        comparison_eligible=False,
        requested_sample_count_per_role=int(
            record.get("manifest", {}).get("requested_sample_count_per_role") or 0
        )
        if isinstance(record.get("manifest"), Mapping)
        else 0,
    )
    return {
        "status": status,
        "need_action": bool(expired),
        "outcome_unknown": bool(expired),
        "panel_version": VISIBILITY_PANEL_VERSION,
        "panel_record_id": record.get("id"),
        "request_id": record["request_id"],
        "idempotency_key": record["idempotency_key"],
        "replayed": True,
        "read_only_recovery": True,
        "in_progress_age_seconds": age_seconds,
        "in_progress_expired": bool(expired),
        "in_progress_expiry_seconds": PANEL_IN_PROGRESS_EXPIRY_SECONDS,
        "manifest": dict(record["manifest"]),
        "requested_role_count": len(target_results),
        "executed_role_count": len(observed_executed),
        "sampled_role_count": len(sampled),
        "distinct_model_count": len(distinct_sampled),
        "multi_model_executed": len(distinct_sampled) >= 2,
        "comparison_eligible": False,
        "measurement_quality": measurement_quality,
        "api_channel_analysis": None,
        "api_channel_analysis_unavailable_reason": _api_analysis_unavailable(
            code=(
                "panel_outcome_unknown"
                if expired
                else "panel_still_processing"
            ),
            message=(
                "父请求结果未知，不能生成 API 渠道聚合指标。"
                if expired
                else "多模型采样仍在处理，尚不能生成 API 渠道聚合指标。"
            ),
            measurement_quality_status=str(
                measurement_quality.get("status") or "invalid"
            ),
        ),
        "targets": target_results,
        "failure": failure,
        "channel_coverage": [
            {
                "channel_type": "provider_api",
                "status": provider_channel_status,
                "sampled_model_count": len(distinct_sampled),
                "web_search_verified": False,
            },
            {
                "channel_type": "consumer_web",
                "status": "unverified",
                "sampled_model_count": 0,
                "web_search_verified": False,
            },
            {
                "channel_type": "mobile_app",
                "status": "unverified",
                "sampled_model_count": 0,
                "web_search_verified": False,
            },
        ],
        "consumer_parity": {
            "status": "unverified",
            "claim_allowed": False,
            "message": (
                "API 模型回答不能证明消费者网页或手机 App 会返回相同答案；"
                "必须从真实入口另行采集证据。"
            ),
        },
        "retry_policy": {
            "automatic_retry": False,
            "same_key_replays_frozen_manifest": True,
            "same_key_can_add_roles": False,
        },
        "claim_boundary": (
            "这是未完成父请求的只读恢复视图。超过 15 分钟仅收敛为"
            " outcome_unknown，不会把子记录拼成一次已完成的多模型比较。"
        ),
    }


def _find_existing_panel(
    database_path: str | Path,
    *,
    request_id: str,
    idempotency_key: str,
    request_shape_sha256: str,
    compatible_request_shape_sha256s: Sequence[str] = (),
) -> dict[str, Any] | None:
    with closing(_connect(database_path)) as conn:
        return _find_existing_panel_on_connection(
            conn,
            request_id=request_id,
            idempotency_key=idempotency_key,
            request_shape_sha256=request_shape_sha256,
            compatible_request_shape_sha256s=compatible_request_shape_sha256s,
        )


def _find_existing_panel_on_connection(
    conn: sqlite3.Connection,
    *,
    request_id: str,
    idempotency_key: str,
    request_shape_sha256: str,
    compatible_request_shape_sha256s: Sequence[str] = (),
) -> dict[str, Any] | None:
    request_row = conn.execute(
        "SELECT * FROM ai_visibility_sample_requests WHERE request_id = ?",
        (request_id,),
    ).fetchone()
    if request_row is not None and request_row["idempotency_key"] != idempotency_key:
        raise VisibilityPanelConflictError(
            "X-Request-ID 已绑定到其他 Idempotency-Key"
        )
    key_row = conn.execute(
        "SELECT * FROM ai_visibility_sample_requests WHERE idempotency_key = ?",
        (idempotency_key,),
    ).fetchone()
    row = key_row or request_row
    if row is None:
        return None
    record = _serialize_panel_row(row)
    stored_shape = str(record["manifest"].get("request_shape_sha256") or "")
    accepted_shapes = {
        str(value)
        for value in (request_shape_sha256, *compatible_request_shape_sha256s)
        if str(value)
    }
    if stored_shape not in accepted_shapes:
        raise VisibilityPanelConflictError(
            "Idempotency-Key 已绑定到冻结的多模型请求；不能新增角色或修改参数"
        )
    return record


def _begin_panel_request(
    database_path: str | Path,
    *,
    project_id: int,
    request_id: str,
    idempotency_key: str,
    request_fingerprint: str,
    request_shape_sha256: str,
    compatible_request_shape_sha256s: Sequence[str],
    manifest: Mapping[str, Any],
) -> tuple[dict[str, Any], bool]:
    with closing(_connect(database_path)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            existing = _find_existing_panel_on_connection(
                conn,
                request_id=request_id,
                idempotency_key=idempotency_key,
                request_shape_sha256=request_shape_sha256,
                compatible_request_shape_sha256s=compatible_request_shape_sha256s,
            )
            if existing is not None:
                conn.commit()
                return existing, True
            storage = _panel_storage(manifest=manifest, aggregate=None)
            cursor = conn.execute(
                """
                INSERT INTO ai_visibility_sample_requests (
                    project_id, request_id, idempotency_key,
                    request_fingerprint, status, result_json
                ) VALUES (?, ?, ?, ?, 'started', ?)
                """,
                (
                    project_id,
                    request_id,
                    idempotency_key,
                    request_fingerprint,
                    _json_encode(storage),
                ),
            )
            row = conn.execute(
                "SELECT * FROM ai_visibility_sample_requests WHERE id = ?",
                (cursor.lastrowid,),
            ).fetchone()
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return _serialize_panel_row(row), False


def _finish_panel_request(
    database_path: str | Path,
    *,
    record_id: int,
    status: str,
    http_status: int,
    manifest: Mapping[str, Any],
    aggregate: Mapping[str, Any],
) -> None:
    storage = _panel_storage(manifest=manifest, aggregate=aggregate)
    with closing(_connect(database_path)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        cursor = conn.execute(
            """
            UPDATE ai_visibility_sample_requests
            SET status = ?, http_status = ?, result_json = ?,
                updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
            WHERE id = ? AND status = 'started'
            """,
            (status, int(http_status), _json_encode(storage), int(record_id)),
        )
        if cursor.rowcount != 1:
            conn.rollback()
            raise VisibilityPanelConflictError(
                "多模型采样状态已变化，未覆盖既有聚合结果"
            )
        conn.commit()


def _replay_panel_or_raise(
    database_path: str | Path,
    record: Mapping[str, Any],
) -> dict[str, Any]:
    if record.get("status") == "started":
        raise VisibilityPanelInProgressError(
            get_ai_visibility_panel_request(database_path, record["request_id"])
        )
    aggregate = record.get("aggregate_result")
    if not isinstance(aggregate, Mapping):
        raise VisibilityPanelConflictError("已保存的多模型聚合结果无法读取")
    replay = json.loads(_json_encode(aggregate))
    replay["replayed"] = True
    return replay


def _serialize_panel_row(row: sqlite3.Row) -> dict[str, Any]:
    storage = _decode_panel_storage(row["result_json"])
    return {
        "id": int(row["id"]),
        "project_id": int(row["project_id"]),
        "request_id": str(row["request_id"]),
        "idempotency_key": str(row["idempotency_key"]),
        "request_fingerprint": str(row["request_fingerprint"]),
        "status": str(row["status"]),
        "http_status": row["http_status"],
        "manifest": dict(storage["manifest"]),
        "aggregate_result": storage.get("aggregate_result"),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _panel_storage(
    *,
    manifest: Mapping[str, Any],
    aggregate: Mapping[str, Any] | None,
) -> dict[str, Any]:
    return {
        "record_type": _PANEL_RECORD_TYPE,
        "panel_version": VISIBILITY_PANEL_VERSION,
        "manifest": dict(manifest),
        "aggregate_result": dict(aggregate) if aggregate is not None else None,
    }


def _decode_panel_storage(value: Any) -> dict[str, Any]:
    try:
        parsed = json.loads(value) if isinstance(value, str) else value
    except (TypeError, json.JSONDecodeError) as exc:
        raise VisibilityPanelConflictError("多模型采样记录无法读取") from exc
    if not isinstance(parsed, Mapping) or parsed.get("record_type") != _PANEL_RECORD_TYPE:
        raise VisibilityPanelConflictError(
            "请求标识已被其他类型的可见度采样占用"
        )
    if not isinstance(parsed.get("manifest"), Mapping):
        raise VisibilityPanelConflictError("多模型采样清单格式无效")
    aggregate = parsed.get("aggregate_result")
    if aggregate is not None and not isinstance(aggregate, Mapping):
        raise VisibilityPanelConflictError("多模型聚合结果格式无效")
    return dict(parsed)


def _recover_manifest_targets(
    conn: sqlite3.Connection,
    manifest: Mapping[str, Any],
) -> list[dict[str, Any]]:
    recovered: list[dict[str, Any]] = []
    for raw_target in manifest.get("targets", []):
        target = dict(raw_target) if isinstance(raw_target, Mapping) else {}
        if not target.get("runnable"):
            recovered.append(_recovered_skipped_target(target))
            continue
        subrequest_id = str(target.get("subrequest_id") or "")
        row = conn.execute(
            "SELECT * FROM ai_visibility_sample_requests WHERE request_id = ?",
            (subrequest_id,),
        ).fetchone()
        if row is None or row["status"] == "started":
            recovered.append(
                {
                    "role": str(target.get("role") or ""),
                    "agent_run_id": f"visibility:{subrequest_id}",
                    "execution_status": "pending" if row is None else "in_progress",
                    "status": "not_started" if row is None else "started",
                    "provider": str(target.get("provider") or ""),
                    "model": str(target.get("model") or ""),
                    "request_id": subrequest_id,
                    "idempotency_key": str(
                        target.get("subrequest_idempotency_key") or ""
                    ),
                    "replayed": True,
                    "completed_sample_count": 0,
                    "requested_sample_count": 0,
                    "failure": None,
                    "channel_type": "provider_api",
                    "entry_point": "openai_compatible_chat_completions",
                    "web_search_verified": False,
                    "consumer_surface_verified": False,
                }
            )
            continue
        try:
            payload = json.loads(row["result_json"] or "{}")
            data = dict(payload.get("data") or {})
            recovered.append(
                _completed_target(
                    target,
                    {
                        "request_id": row["request_id"],
                        "idempotency_key": row["idempotency_key"],
                        "replayed": True,
                    },
                    data,
                )
            )
        except (TypeError, ValueError, json.JSONDecodeError):
            recovered.append(
                {
                    **_recovered_skipped_target(target),
                    "execution_status": "failed",
                    "status": "need_action",
                    "skip_reason": "",
                    "failure": {
                        "code": "panel_child_result_unreadable",
                        "message": "子采样结果无法读取；未自动重试",
                        "automatic_retry": False,
                    },
                }
            )
    return recovered


def _recovered_skipped_target(target: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "role": str(target.get("role") or ""),
        "agent_run_id": "",
        "execution_status": "skipped",
        "status": "not_configured",
        "provider": str(target.get("provider") or ""),
        "model": str(target.get("model") or ""),
        "completed_sample_count": 0,
        "requested_sample_count": 0,
        "failure": None,
        "skip_reason": str(target.get("skip_reason") or ""),
        "channel_type": "provider_api",
        "entry_point": "not_invoked",
        "web_search_verified": False,
        "consumer_surface_verified": False,
    }


def _panel_http_status(status: str) -> int:
    return 200 if status == "completed" else (207 if status == "partial" else 503)


def _optional_sequence(value: Sequence[Any] | None, field: str) -> list[Any] | None:
    if value is None:
        return None
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise VisibilityPanelValidationError(f"{field} 必须是数组")
    return list(value)


def _request_identifier(value: Any, field: str, maximum: int) -> str:
    text = str(value or "").strip()
    if not text or len(text) > maximum:
        raise VisibilityPanelValidationError(f"{field} 格式无效")
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:-")
    if any(character not in allowed for character in text):
        raise VisibilityPanelValidationError(f"{field} 格式无效")
    return text


def _started_age_seconds(
    created_at: Any,
    *,
    current_time: datetime | None,
) -> int:
    if not isinstance(created_at, str):
        raise VisibilityPanelConflictError("多模型采样请求创建时间格式无效")
    try:
        created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise VisibilityPanelConflictError("多模型采样请求创建时间格式无效") from exc
    if created.tzinfo is None or created.utcoffset() is None:
        raise VisibilityPanelConflictError("多模型采样请求创建时间必须使用 UTC 时区")
    observed_at = current_time or datetime.now(timezone.utc)
    if not isinstance(observed_at, datetime):
        raise VisibilityPanelValidationError("current_time 必须是 datetime")
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise VisibilityPanelValidationError("current_time 必须包含时区")
    return max(
        0,
        int((observed_at.astimezone(timezone.utc) - created).total_seconds()),
    )


def _json_digest(value: Any) -> str:
    return hashlib.sha256(_json_encode(value).encode("utf-8")).hexdigest()


def _json_encode(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _connect(database_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(Path(database_path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def _completed_target(
    target: Mapping[str, Any],
    raw: Mapping[str, Any],
    data: Mapping[str, Any],
) -> dict[str, Any]:
    executions = list(data.get("executions") or [])
    first = executions[0] if executions and isinstance(executions[0], Mapping) else {}
    grounding = dict(data.get("grounding") or {})
    settings = target.get("settings")
    role_status = (
        dict(target.get("role_status") or {})
        if isinstance(target.get("role_status"), Mapping)
        else {}
    )
    configured_provider = (
        provider_label_for_settings(settings)
        if isinstance(settings, AISettings)
        else str(target.get("provider") or role_status.get("provider") or "")
    )
    configured_model = (
        str(settings.model)
        if isinstance(settings, AISettings)
        else str(target.get("model") or role_status.get("model") or "")
    )
    failure = data.get("failure") if isinstance(data.get("failure"), Mapping) else None
    completed_sample_count = int(data.get("completed_sample_count") or 0)
    if completed_sample_count > 0:
        execution_status = "executed"
    elif failure and bool(failure.get("outcome_unknown")):
        execution_status = "outcome_unknown"
    elif failure:
        execution_status = "failed"
    else:
        execution_status = "executed"
    return {
        "role": target["role"],
        "agent_run_id": f"visibility:{raw.get('request_id') or ''}",
        "execution_status": execution_status,
        "status": str(data.get("status") or "need_action"),
        "provider": str(
            first.get("provider")
            or data.get("configured_provider")
            or configured_provider
            or "unknown"
        ),
        "model": str(
            first.get("model")
            or data.get("configured_model")
            or configured_model
            or "unknown"
        ),
        "request_id": str(raw.get("request_id") or ""),
        "idempotency_key": str(raw.get("idempotency_key") or ""),
        "replayed": bool(raw.get("replayed")),
        "completed_sample_count": completed_sample_count,
        "requested_sample_count": int(data.get("requested_sample_count") or 0),
        "run_id": data.get("run_id"),
        "analysis": data.get("analysis"),
        "executions": executions,
        "failure": dict(failure) if failure else None,
        "channel_type": "provider_api",
        "entry_point": "openai_compatible_chat_completions",
        "web_search_verified": bool(grounding.get("web_search_verified", False)),
        "consumer_surface_verified": False,
    }


def _failed_target(target: Mapping[str, Any], exc: Exception) -> dict[str, Any]:
    settings = target["settings"]
    return {
        "role": target["role"],
        "agent_run_id": "",
        "execution_status": "failed",
        "status": "need_action",
        "provider": provider_label_for_settings(settings),
        "model": settings.model,
        "request_id": str(target.get("subrequest_id") or ""),
        "idempotency_key": str(
            target.get("subrequest_idempotency_key") or ""
        ),
        "replayed": False,
        "completed_sample_count": 0,
        "requested_sample_count": 0,
        "run_id": None,
        "analysis": None,
        "executions": [],
        "failure": {
            "code": "panel_target_failed",
            "message": str(exc)[:1_000] or "模型目标执行失败",
            "automatic_retry": False,
        },
        "channel_type": "provider_api",
        "entry_point": "openai_compatible_chat_completions",
        "web_search_verified": False,
        "consumer_surface_verified": False,
    }


def _skipped_target(target: Mapping[str, Any]) -> dict[str, Any]:
    settings = target.get("settings")
    return {
        "role": target["role"],
        "agent_run_id": "",
        "execution_status": "skipped",
        "status": "not_configured",
        "provider": (
            provider_label_for_settings(settings)
            if isinstance(settings, AISettings)
            else str((target.get("role_status") or {}).get("provider") or "")
        ),
        "model": (
            settings.model
            if isinstance(settings, AISettings)
            else str((target.get("role_status") or {}).get("model") or "")
        ),
        "completed_sample_count": 0,
        "requested_sample_count": 0,
        "failure": None,
        "skip_reason": target.get("skip_reason"),
        "channel_type": "provider_api",
        "entry_point": "not_invoked",
        "web_search_verified": False,
        "consumer_surface_verified": False,
    }


def _panel_target_identity(target: Mapping[str, Any]) -> str:
    """Return the frozen model identity, with a conservative legacy fallback."""

    manifest_identity = str(target.get("identity_sha256") or "").strip().casefold()
    if len(manifest_identity) == 64 and not (
        set(manifest_identity) - set("0123456789abcdef")
    ):
        return f"manifest:{manifest_identity}"

    endpoint = ""
    for field in ("resolved_endpoint", "endpoint", "base_url"):
        raw_endpoint = target.get(field)
        if not isinstance(raw_endpoint, str) or not raw_endpoint.strip():
            continue
        try:
            endpoint = _normalized_model_endpoint(raw_endpoint)
        except Exception:
            # Legacy/recovery targets may not retain a validated endpoint. Do
            # not guess at it or expose it; provider/model remains a safe,
            # conservative identity that cannot inflate the distinct count.
            continue
        break

    return "fallback:" + _json_digest(
        {
            "provider": str(target.get("provider") or "unknown")
            .strip()
            .casefold(),
            "endpoint": endpoint,
            "model": str(target.get("model") or "unknown").strip().casefold(),
        }
    )


def _normalized_model_endpoint(base_url: str) -> str:
    resolved = urlsplit(resolve_ai_chat_completions_url(base_url))
    hostname = (resolved.hostname or "").rstrip(".").casefold()
    host = f"[{hostname}]" if ":" in hostname else hostname
    if resolved.port is not None and resolved.port != 443:
        host = f"{host}:{resolved.port}"
    return urlunsplit(("https", host, resolved.path.rstrip("/"), "", ""))


def _model_identity(settings: AISettings) -> str:
    endpoint = _normalized_model_endpoint(settings.base_url)
    raw = "\0".join(
        [
            provider_label_for_settings(settings).strip().casefold(),
            endpoint,
            settings.model.strip().casefold(),
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


__all__ = [
    "MAX_PANEL_MODELS",
    "PANEL_IN_PROGRESS_EXPIRY_SECONDS",
    "VISIBILITY_PANEL_VERSION",
    "VisibilityPanelConflictError",
    "VisibilityPanelInProgressError",
    "VisibilityPanelRequestNotFoundError",
    "VisibilityPanelValidationError",
    "get_ai_visibility_panel_request",
    "run_ai_visibility_panel",
]
