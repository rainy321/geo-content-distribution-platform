"""Controlled live sampling for the configured OpenAI-compatible model.

Only the explicit operator prompt crosses the provider boundary as natural-
language context; normal model/authorization transport metadata is still
required. Project metadata, aliases, competitors, prior content and media
credentials remain local and are used solely by the deterministic visibility
engine. Every request is durably idempotent before the first provider call.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import sqlite3
import time
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import urlsplit

from services.ai_cost_ledger_service import AIUsageLedgerError, record_ai_usage
from services.ai_service import (
    AIConfigurationError,
    AIConnectError,
    AIProtocolError,
    AIProviderHTTPError,
    AIProviderRateLimitError,
    AIReadTimeoutError,
    AIServiceError,
    AISettings,
    provider_label_for_settings,
    resolve_ai_chat_completions_url,
    sample_ai_prompt_detailed,
)
from services.ai_visibility_run_service import (
    get_ai_visibility_run,
    save_ai_visibility_run,
)
from services.ai_visibility_service import (
    VisibilityAnalysisError,
    analyze_ai_visibility,
    current_ai_visibility_algorithm_versions,
    inspect_ai_visibility_analysis_freshness,
    reanalyze_ai_visibility_locally,
)


VISIBILITY_SAMPLING_VERSION = "controlled-live-visibility-sampling-1.2.0"
VISIBILITY_SAMPLING_PREFLIGHT_VERSION = "visibility-sampling-preflight-1.0.0"
LOCAL_ANALYSIS_INPUT_SNAPSHOT_VERSION = "visibility-local-analysis-input-1.0.0"
IN_PROGRESS_EXPIRY_SECONDS = 15 * 60
VISIBILITY_SAMPLE_MAX_OUTPUT_TOKENS = 768
VISIBILITY_SAMPLE_MAX_TIMEOUT_SECONDS = 120.0
MIN_SAMPLE_COUNT = 1
MAX_SAMPLE_COUNT = 5
MAX_PROMPT_LENGTH = 8_000
_PANEL_RECORD_TYPE = "ai_visibility_model_panel"


class VisibilitySamplingValidationError(ValueError):
    """Raised before a provider request when the public contract is invalid."""


class VisibilitySamplingConflictError(RuntimeError):
    """Raised when an idempotency identity is bound to another request."""


class VisibilitySamplingInProgressError(RuntimeError):
    """Raised when a previous process may still own the provider outcome."""

    def __init__(self, record: Mapping[str, Any]) -> None:
        super().__init__(
            "该幂等采样请求仍处于处理中；请先核对模型账单或等待原请求完成，"
            "不要自动重试"
        )
        self.record = dict(record)


class VisibilitySamplingRequestNotFoundError(LookupError):
    """Raised when a persisted sampling request identity does not exist."""


def get_ai_visibility_sample_request(
    database_path: str | Path,
    request_id: Any,
    *,
    current_time: datetime | None = None,
) -> dict[str, Any]:
    """Read one durable sampling request without invoking the AI provider.

    This is the safe resume seam used by the desktop UI after route changes or
    window reloads.  A ``started`` row reports a read-only age/expiry signal;
    no state is changed.  Panel records sharing the storage table are rejected
    so the single-model endpoint can never deserialize a multi-model receipt.
    """

    normalized_request_id = _request_identifier(request_id, "X-Request-ID", 128)
    with closing(_connect(database_path)) as conn:
        row = conn.execute(
            "SELECT * FROM ai_visibility_sample_requests WHERE request_id = ?",
            (normalized_request_id,),
        ).fetchone()
    if row is None:
        raise VisibilitySamplingRequestNotFoundError("可见度采样请求不存在")
    return _serialize_request(row, current_time=current_time)


def refresh_ai_visibility_sample_request_locally(
    database_path: str | Path,
    request_id: Any,
    *,
    project: Mapping[str, Any],
) -> dict[str, Any]:
    """Return a parser-current view of a saved sampling receipt, if possible.

    Only captured response text is reused.  This function has no sampler,
    settings or HTTP client parameter, never records usage, and never changes
    the persisted receipt.  If an older receipt omitted the exact request-level
    alias/competitor inputs, its original analysis is retained and explicitly
    marked stale instead of guessing.
    """

    record = get_ai_visibility_sample_request(database_path, request_id)
    return _refresh_sample_record_locally(
        database_path,
        record=record,
        project=project,
        exact_overrides=None,
    )


def refresh_ai_visibility_run_locally(
    database_path: str | Path,
    run_id: int,
    *,
    project: Mapping[str, Any],
) -> dict[str, Any]:
    """Return a locally refreshed history-run view without mutating history."""

    saved = get_ai_visibility_run(database_path, run_id)
    project_values = _local_project_values(project, expected_id=saved["project_id"])
    coverage = saved.get("coverage_items")
    if not isinstance(coverage, Mapping) or not {
        "aliases",
        "competitors",
    }.issubset(coverage):
        return _mark_run_refresh_unavailable(
            saved,
            reason="saved_analysis_inputs_incomplete",
        )
    try:
        refreshed = _refresh_analysis_view(
            previous_analysis=saved.get("result"),
            project_values=project_values,
            prompt=saved.get("prompt"),
            responses=saved.get("input_samples"),
            aliases=coverage.get("aliases"),
            competitors=coverage.get("competitors"),
            stored_local_analysis=None,
            original_external_model_used=_analysis_used_external_model(
                saved.get("result")
            ),
        )
    except (VisibilityAnalysisError, VisibilitySamplingValidationError):
        return _mark_run_refresh_unavailable(
            saved,
            reason="saved_analysis_data_invalid",
        )
    output = copy.deepcopy(saved)
    output["result"] = refreshed["analysis"]
    output["analysis_refresh"] = refreshed["refresh"]
    return output


def run_ai_visibility_sample(
    database_path: str | Path,
    *,
    project: Mapping[str, Any],
    prompt: Any,
    sample_count: Any,
    aliases: Sequence[Any] | None,
    competitors: Sequence[Any] | None,
    persist: Any,
    confirm_external_call: Any,
    settings: AISettings,
    request_id: str,
    idempotency_key: str,
    sampler: Callable[..., Any] | None = None,
    usage_recorder: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Sample sequentially, stop on first failure, and never auto-retry.

    The return value contains ``http_status`` for the API transport, a
    canonical request identity, and an already JSON-safe ``payload``. A replay
    returns the exact saved payload without invoking ``sampler`` or the cost
    ledger again.
    """

    sampler = sampler or sample_ai_prompt_detailed
    usage_recorder = usage_recorder or record_ai_usage
    normalized = _validate_request(
        project=project,
        prompt=prompt,
        sample_count=sample_count,
        aliases=aliases,
        competitors=competitors,
        persist=persist,
        confirm_external_call=confirm_external_call,
        settings=settings,
        request_id=request_id,
        idempotency_key=idempotency_key,
    )
    # Validate the effective transport contract before reserving a durable
    # request or entering user-supplied sampler code.  This is deliberately a
    # local preflight: a synthetic "reply OK" request would be another
    # potentially billable provider call and still would not prove that the
    # real visibility prompt can complete within the same token budget.
    sampling_settings, contract_preflight = _prepare_sampling_contract(settings)
    # Preserve the v1.2 durable identity so receipts created before this
    # orchestration hardening still replay instead of conflicting after an
    # endpoint-normalization-only change.
    fingerprint = _request_fingerprint(normalized, settings=settings)
    request_record, replayed = _begin_or_replay(
        database_path,
        project_id=normalized["project_id"],
        request_id=normalized["request_id"],
        idempotency_key=normalized["idempotency_key"],
        request_fingerprint=fingerprint,
    )
    if replayed:
        refreshed_record = _refresh_sample_record_locally(
            database_path,
            record=request_record,
            project=project,
            exact_overrides={
                "aliases": normalized["aliases"],
                "competitors": normalized["competitors"],
            },
        )
        return {
            "http_status": int(refreshed_record["http_status"]),
            "payload": refreshed_record["result"],
            "request_id": refreshed_record["request_id"],
            "idempotency_key": refreshed_record["idempotency_key"],
            "replayed": True,
        }

    executions: list[dict[str, Any]] = []
    attempts: list[dict[str, Any]] = []
    analysis_inputs: list[dict[str, Any]] = []
    failure: dict[str, Any] | None = None
    provider = provider_label_for_settings(sampling_settings)

    for index in range(1, normalized["sample_count"] + 1):
        phase = "contract_probe" if index == 1 else "measurement"
        active_attempt = {
            "index": index,
            "phase": phase,
            "state": "dispatching_or_in_flight",
            "provider": provider,
            "model": sampling_settings.model,
            "request_state": "may_have_been_sent",
            "provider_outcome": "unknown",
            "billing_outcome": "unknown",
            "timeout_seconds": sampling_settings.timeout_seconds,
            "max_output_tokens": sampling_settings.max_tokens,
            "automatic_retry_allowed": False,
        }
        # Persist intent immediately before handing control to the transport.
        # If the process dies in or after that call, operators can distinguish
        # a never-started row from an ambiguous, potentially billable attempt.
        _checkpoint_request(
            database_path,
            record_id=request_record["id"],
            result=_processing_payload(
                request_record=request_record,
                normalized=normalized,
                settings=sampling_settings,
                provider=provider,
                contract_preflight=contract_preflight,
                attempts=attempts,
                executions=executions,
                analysis_inputs=analysis_inputs,
                active_attempt=active_attempt,
            ),
        )
        attempt_started = time.perf_counter()
        try:
            completion = sampler(
                prompt=normalized["prompt"],
                settings=sampling_settings,
            )
        except AIServiceError as exc:
            failure = _provider_failure(exc, sample_index=index)
            usage_record, usage_ledger_status = _record_failed_attempt_usage(
                database_path,
                usage_recorder=usage_recorder,
                error=exc,
                provider=provider,
                model=sampling_settings.model,
                project_id=normalized["project_id"],
                reference_id=f"{request_record['id']}:{index}",
                failure=failure,
            )
            failure["usage_ledger_status"] = usage_ledger_status
            attempts.append(
                {
                    **active_attempt,
                    "state": (
                        "outcome_unknown"
                        if failure["outcome_unknown"]
                        else "failed"
                    ),
                    "request_state": failure["request_state"],
                    "provider_outcome": (
                        "unknown" if failure["outcome_unknown"] else "failed"
                    ),
                    "billing_outcome": failure["billing_outcome"],
                    "elapsed_ms": max(
                        0, round((time.perf_counter() - attempt_started) * 1000)
                    ),
                    "usage": None,
                    "usage_status": (
                        "unknown" if usage_record is not None else "not_recorded"
                    ),
                    "ai_usage": usage_record,
                    "failure": dict(failure),
                }
            )
            break

        model = _bounded_metadata(
            completion.model or sampling_settings.model, 200, "unknown"
        )
        provider_request_id = _bounded_metadata(
            completion.provider_request_id, 200, ""
        )
        usage = dict(completion.usage) if isinstance(completion.usage, Mapping) else None
        text = str(completion.content or "").strip()
        try:
            usage_record = usage_recorder(
                database_path,
                operation="visibility_sample",
                provider=provider,
                model=model,
                usage=usage,
                usage_status="reported" if usage is not None else "unknown",
                project_id=normalized["project_id"],
                reference_type="visibility_sample",
                reference_id=f"{request_record['id']}:{index}",
                metadata={
                    "outcome": "succeeded" if text else "invalid_response",
                    "usage_origin": (
                        "provider_response" if usage is not None else "not_reported"
                    ),
                },
            )
        except (AIUsageLedgerError, sqlite3.Error):
            # The model answered, so the response must be retained and the key
            # closed as ambiguous. Retrying could incur a duplicate charge.
            usage_record = None
            usage_ledger_failed = True
        else:
            usage_ledger_failed = False

        if not text:
            failure = {
                "code": "empty_provider_response",
                "message": "模型返回了空回答，后续样本已停止；不要自动重试",
                "sample_index": index,
                "outcome_unknown": False,
                "request_state": "response_received",
                "billing_outcome": (
                    "reported" if usage is not None else "unknown"
                ),
                "usage_ledger_status": (
                    "failed" if usage_ledger_failed else "recorded"
                ),
                "retry_after_seconds": None,
            }
            attempts.append(
                {
                    **active_attempt,
                    "state": "failed",
                    "request_state": "response_received",
                    "provider_outcome": "invalid_response",
                    "billing_outcome": failure["billing_outcome"],
                    "provider_request_id": provider_request_id,
                    "elapsed_ms": max(0, int(completion.elapsed_ms)),
                    "usage": usage,
                    "usage_status": (
                        "reported" if usage is not None else "unknown"
                    ),
                    "ai_usage": usage_record,
                    "failure": dict(failure),
                }
            )
            break

        analysis_inputs.append(
            {
                "text": text,
                "provider": provider,
                "model": model,
                "channel_type": "provider_api",
                "entry_point": "openai_compatible_chat_completions",
                "web_search_verified": False,
                "consumer_surface_verified": False,
            }
        )
        if usage_ledger_failed:
            failure = {
                "code": "cost_ledger_failed",
                "message": "模型已返回，但本地费用凭据未能保存；请人工核对，勿自动重试",
                "sample_index": index,
                "outcome_unknown": False,
                "request_state": "response_received",
                "billing_outcome": "unknown",
                "usage_ledger_status": "failed",
                "retry_after_seconds": None,
            }

        executions.append(
            {
                "index": index,
                "provider": provider,
                "model": model,
                "provider_request_id": provider_request_id,
                "elapsed_ms": max(0, int(completion.elapsed_ms)),
                "usage": usage,
                "usage_status": "reported" if usage is not None else "unknown",
                "ai_usage": usage_record,
            }
        )
        attempts.append(
            {
                **active_attempt,
                "state": "succeeded",
                "request_state": "response_received",
                "provider_outcome": "succeeded",
                "billing_outcome": (
                    "reported" if usage is not None else "unknown"
                ),
                "provider_request_id": provider_request_id,
                "elapsed_ms": max(0, int(completion.elapsed_ms)),
                "usage": usage,
                "usage_status": "reported" if usage is not None else "unknown",
                "ai_usage": usage_record,
                "failure": None,
            }
        )
        if failure is None:
            reported_output_tokens = _reported_output_limit_mismatch(
                usage,
                requested_max_output_tokens=sampling_settings.max_tokens,
            )
            if reported_output_tokens is not None:
                # The answer and its usage receipt are valid and must be kept,
                # but a provider which substantially exceeds the requested
                # output ceiling can make a multi-sample run unexpectedly
                # expensive. Stop before another external request instead of
                # silently repeating the same contract mismatch.
                failure = {
                    "code": "provider_output_limit_mismatch",
                    "message": (
                        "模型已返回并保留本条回答，但供应商报告的输出 Token "
                        "明显超过本次请求上限；为避免继续超额计费，系统已停止该角色"
                        "后续采样。请检查网关是否支持 max_tokens，或改用低推理模型"
                    ),
                    "sample_index": index,
                    "outcome_unknown": False,
                    "request_state": "response_received",
                    "billing_outcome": (
                        "reported" if usage is not None else "unknown"
                    ),
                    "usage_ledger_status": "recorded",
                    "retry_after_seconds": None,
                    "requested_max_output_tokens": sampling_settings.max_tokens,
                    "reported_output_tokens": reported_output_tokens,
                }
                attempts[-1]["contract_mismatch"] = {
                    "code": failure["code"],
                    "requested_max_output_tokens": sampling_settings.max_tokens,
                    "reported_output_tokens": reported_output_tokens,
                }
        if failure is not None:
            break
        _checkpoint_request(
            database_path,
            record_id=request_record["id"],
            result=_processing_payload(
                request_record=request_record,
                normalized=normalized,
                settings=sampling_settings,
                provider=provider,
                contract_preflight=contract_preflight,
                attempts=attempts,
                executions=executions,
                analysis_inputs=analysis_inputs,
                active_attempt=None,
            ),
        )

    analysis = None
    run_id = None
    created_at = None
    if analysis_inputs:
        try:
            analysis = analyze_ai_visibility(
                project_id=normalized["project_id"],
                project_name=normalized["project_name"],
                project_product=normalized["project_product"],
                project_keywords=normalized["project_keywords"],
                project_website=normalized["project_website"],
                stored_competitors=normalized["stored_competitors"],
                prompt=normalized["prompt"],
                responses=analysis_inputs,
                aliases=normalized["aliases"],
                competitors=normalized["competitors"],
            )
            analysis["methodology"] = {
                **analysis["methodology"],
                "external_model_used": True,
                "sampling_transport": "configured-openai-compatible-chat-completions",
                "sampling_version": VISIBILITY_SAMPLING_VERSION,
                "failed_or_unattempted_samples_in_metrics": False,
            }
            local_analysis_fingerprint = _local_analysis_fingerprint(
                project_values=_local_project_values(
                    project, expected_id=normalized["project_id"]
                ),
                prompt=normalized["prompt"],
                responses=analysis_inputs,
                aliases=normalized["aliases"],
                competitors=normalized["competitors"],
            )
            analysis["methodology"]["local_analysis_input_fingerprint"] = (
                local_analysis_fingerprint
            )
            if normalized["persist"]:
                saved = save_ai_visibility_run(
                    database_path,
                    project_id=normalized["project_id"],
                    prompt=normalized["prompt"],
                    input_samples=analysis_inputs,
                    coverage_items={
                        "aliases": normalized["aliases"],
                        "project_keyword_aliases": (
                            analysis.get("project", {}).get("effective_brand_aliases", [])
                        ),
                        "competitors": normalized["competitors"],
                        "sampling_request_id": request_record["id"],
                    },
                    result=analysis,
                    algorithm_version=analysis["algorithm_versions"]["integration"],
                )
                run_id = saved["id"]
                created_at = saved["created_at"]
        except (VisibilityAnalysisError, ValueError, LookupError, sqlite3.Error):
            failure = failure or {
                "code": "local_analysis_failed",
                "message": "模型回答已取得，但本地分析或保存失败；请人工处理，勿自动重试",
                "sample_index": len(analysis_inputs),
                "outcome_unknown": False,
                "request_state": "provider_responses_received",
                "billing_outcome": "see_attempt_receipts",
                "usage_ledger_status": "see_attempt_receipts",
                "retry_after_seconds": None,
            }
            analysis = None

    completed_count = len(analysis_inputs)
    if failure is None and completed_count == normalized["sample_count"]:
        status = "completed"
        http_status = 200
        need_action = False
        message = "真实采样与本地可见度分析已完成"
    elif completed_count:
        status = "partial"
        http_status = 207
        need_action = True
        message = "采样部分完成，已停止后续请求；请按错误提示人工处理"
    else:
        status = "need_action"
        http_status = _failure_http_status(failure)
        need_action = True
        message = "采样未完成，未自动重试；请人工核对后再决定是否使用新幂等键"

    local_analysis = (
        {
            "input_fingerprint": local_analysis_fingerprint,
            "input_snapshot_version": LOCAL_ANALYSIS_INPUT_SNAPSHOT_VERSION,
            "algorithm_versions": current_ai_visibility_algorithm_versions(),
        }
        if analysis is not None
        else None
    )
    current_versions = current_ai_visibility_algorithm_versions()
    analysis_refresh = {
        "status": "current" if analysis is not None else "not_available",
        "reanalysis_required": analysis is None,
        "performed": False,
        "reason": None if analysis is not None else "analysis_not_available",
        "provider_calls": 0,
        "network_requests": 0,
        "stale_components": [] if analysis is not None else list(current_versions),
        "stored_versions": current_versions if analysis is not None else {},
        "current_versions": current_versions,
    }
    contract_probe = _contract_probe_summary(attempts, failure=failure)

    payload = {
        "status": status,
        "need_action": need_action,
        "sampling_version": VISIBILITY_SAMPLING_VERSION,
        "request_id": request_record["request_id"],
        "idempotency_key": request_record["idempotency_key"],
        "project_id": normalized["project_id"],
        # Keep the requested identity even when the very first provider call
        # fails and therefore produces no execution receipt.  A failed model
        # must never be rendered as ``unknown`` when its frozen configuration
        # is already known locally.
        "configured_provider": provider,
        "configured_model": sampling_settings.model,
        "requested_sample_count": normalized["sample_count"],
        "completed_sample_count": completed_count,
        "provider_attempt_count": len(attempts),
        "unknown_outcome_count": sum(
            item.get("state") == "outcome_unknown" for item in attempts
        ),
        "unattempted_sample_count": max(
            0,
            normalized["sample_count"]
            - completed_count
            - _failed_provider_attempt_count(failure, completed_count),
        ),
        "persist_requested": normalized["persist"],
        "persisted": run_id is not None,
        "run_id": run_id,
        "created_at": created_at,
        "executions": executions,
        "attempts": attempts,
        "captured_responses": analysis_inputs,
        "failure": failure,
        "contract_preflight": contract_preflight,
        "contract_probe": contract_probe,
        "analysis": analysis,
        "local_analysis": local_analysis,
        "local_analysis_input_snapshot": {
            "version": LOCAL_ANALYSIS_INPUT_SNAPSHOT_VERSION,
            "aliases": normalized["aliases"],
            "competitors": normalized["competitors"],
        },
        "analysis_refresh": analysis_refresh,
        "channel_type": "provider_api",
        "entry_point": "openai_compatible_chat_completions",
        "distinct_model_count": 1 if completed_count else 0,
        "multi_model_executed": False,
        "consumer_parity": {
            "status": "unverified",
            "claim_allowed": False,
            "message": (
                "本结果来自配置模型的 API，不代表豆包、元宝、千问等消费者网页"
                "或手机 App 会返回相同答案。"
            ),
        },
        "channel_coverage": [
            {
                "channel_type": "provider_api",
                "status": "sampled" if completed_count else "not_completed",
                "sampled_model_count": 1 if completed_count else 0,
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
        "data_disclosure": _data_disclosure(
            normalized["prompt"], normalized["sample_count"]
        ),
        "grounding": _grounding_boundary(),
        "request_policy": {
            "execution": "sequential_stop_on_first_failure",
            "max_output_tokens": sampling_settings.max_tokens,
            "stop_on_reported_output_limit_mismatch": True,
            "timeout_seconds": sampling_settings.timeout_seconds,
            "max_timeout_seconds": VISIBILITY_SAMPLE_MAX_TIMEOUT_SECONDS,
            "connection_test_equivalent": False,
            "first_requested_sample_is_contract_probe": True,
            "extra_contract_probe_provider_calls": 0,
        },
        "retry_policy": {
            "automatic_retry": False,
            "same_key_replays_saved_result": True,
            "ambiguous_outcome_action": (
                "先核对模型账单与原请求；仅在确认需要新调用后使用新的幂等键"
            ),
        },
    }
    response_payload = {"code": http_status, "msg": message, "data": payload}
    _finish_request(
        database_path,
        record_id=request_record["id"],
        status=status,
        http_status=http_status,
        result=response_payload,
    )
    return {
        "http_status": http_status,
        "payload": response_payload,
        "request_id": request_record["request_id"],
        "idempotency_key": request_record["idempotency_key"],
        "replayed": False,
    }


def _validate_request(
    *,
    project: Mapping[str, Any],
    prompt: Any,
    sample_count: Any,
    aliases: Sequence[Any] | None,
    competitors: Sequence[Any] | None,
    persist: Any,
    confirm_external_call: Any,
    settings: AISettings,
    request_id: str,
    idempotency_key: str,
) -> dict[str, Any]:
    if not isinstance(project, Mapping):
        raise VisibilitySamplingValidationError("品牌项目不存在")
    raw_project_id = project.get("id")
    if isinstance(raw_project_id, bool):
        raise VisibilitySamplingValidationError("project_id 必须是正整数")
    try:
        project_id = int(raw_project_id)
    except (TypeError, ValueError) as exc:
        raise VisibilitySamplingValidationError("project_id 必须是正整数") from exc
    if project_id <= 0:
        raise VisibilitySamplingValidationError("project_id 必须是正整数")
    if not isinstance(prompt, str):
        raise VisibilitySamplingValidationError("prompt 必须是字符串")
    normalized_prompt = prompt.strip()
    if not normalized_prompt or "\0" in normalized_prompt:
        raise VisibilitySamplingValidationError("prompt 不能为空")
    if len(normalized_prompt) > MAX_PROMPT_LENGTH:
        raise VisibilitySamplingValidationError(
            f"prompt 不能超过 {MAX_PROMPT_LENGTH} 个字符"
        )
    if isinstance(sample_count, bool) or not isinstance(sample_count, int):
        raise VisibilitySamplingValidationError("sample_count 必须是 1 到 5 的整数")
    if not MIN_SAMPLE_COUNT <= sample_count <= MAX_SAMPLE_COUNT:
        raise VisibilitySamplingValidationError("sample_count 必须是 1 到 5 的整数")
    if not isinstance(persist, bool):
        raise VisibilitySamplingValidationError("persist 必须是布尔值")
    if confirm_external_call is not True:
        raise VisibilitySamplingValidationError(
            "必须设置 confirm_external_call=true，确认只把 prompt 发送给当前配置模型"
        )
    if not isinstance(settings, AISettings):
        raise VisibilitySamplingValidationError("缺少有效的 AI 配置")
    normalized_request_id = _request_identifier(request_id, "X-Request-ID", 128)
    normalized_key = _request_identifier(
        idempotency_key, "Idempotency-Key", 150
    )

    project_name = str(project.get("name") or "").strip()
    project_product = str(project.get("product") or "").strip()
    project_keywords = project.get("keywords") or []
    project_website = str(project.get("website") or "").strip()
    stored_competitors = project.get("competitors") or []
    # Run the deterministic parser against one local placeholder before any
    # network operation. This reuses the exact alias/competitor validation
    # contract instead of maintaining a divergent copy.
    try:
        analyze_ai_visibility(
            project_id=project_id,
            project_name=project_name,
            project_product=project_product,
            project_keywords=project_keywords,
            project_website=project_website,
            stored_competitors=stored_competitors,
            prompt=normalized_prompt,
            responses=["LOCAL_CONTRACT_PREFLIGHT"],
            aliases=aliases,
            competitors=competitors,
        )
    except VisibilityAnalysisError as exc:
        raise VisibilitySamplingValidationError(str(exc)) from exc

    return {
        "project_id": project_id,
        "project_name": project_name,
        "project_product": project_product,
        "project_keywords": list(project_keywords),
        "project_website": project_website,
        "stored_competitors": list(stored_competitors),
        "prompt": normalized_prompt,
        "sample_count": sample_count,
        "aliases": list(aliases) if aliases is not None else None,
        "competitors": list(competitors) if competitors is not None else None,
        "persist": persist,
        "request_id": normalized_request_id,
        "idempotency_key": normalized_key,
    }


def _prepare_sampling_contract(
    settings: AISettings,
) -> tuple[AISettings, dict[str, Any]]:
    """Validate and freeze the effective sampling transport without I/O.

    The first real requested sample is the only useful runtime compatibility
    probe.  A separate tiny prompt would add cost while proving little about a
    provider's reasoning-token behaviour for the actual GEO question.
    """

    if not isinstance(settings, AISettings):
        raise VisibilitySamplingValidationError("缺少有效的 AI 配置")
    try:
        validated = AISettings.from_mapping(
            {
                "base_url": settings.base_url,
                "api_key": settings.api_key,
                "model": settings.model,
                "provider": settings.provider,
            }
        )
        endpoint = resolve_ai_chat_completions_url(validated.base_url)
    except (AIConfigurationError, TypeError, ValueError) as exc:
        raise VisibilitySamplingValidationError(
            f"采样前契约检测失败：{exc}"
        ) from exc

    raw_timeout = settings.timeout_seconds
    if isinstance(raw_timeout, bool):
        raise VisibilitySamplingValidationError(
            "采样前契约检测失败：timeout_seconds 必须是正数"
        )
    try:
        configured_timeout = float(raw_timeout)
    except (TypeError, ValueError) as exc:
        raise VisibilitySamplingValidationError(
            "采样前契约检测失败：timeout_seconds 必须是正数"
        ) from exc
    if not math.isfinite(configured_timeout) or configured_timeout <= 0:
        raise VisibilitySamplingValidationError(
            "采样前契约检测失败：timeout_seconds 必须是正数"
        )

    raw_max_tokens = settings.max_tokens
    if isinstance(raw_max_tokens, bool):
        raise VisibilitySamplingValidationError(
            "采样前契约检测失败：max_tokens 必须是正整数"
        )
    try:
        configured_max_tokens = int(raw_max_tokens)
    except (TypeError, ValueError, OverflowError) as exc:
        raise VisibilitySamplingValidationError(
            "采样前契约检测失败：max_tokens 必须是正整数"
        ) from exc
    if configured_max_tokens <= 0 or configured_max_tokens != raw_max_tokens:
        raise VisibilitySamplingValidationError(
            "采样前契约检测失败：max_tokens 必须是正整数"
        )

    effective_timeout = min(
        configured_timeout,
        VISIBILITY_SAMPLE_MAX_TIMEOUT_SECONDS,
    )
    effective_max_tokens = min(
        configured_max_tokens,
        VISIBILITY_SAMPLE_MAX_OUTPUT_TOKENS,
    )
    sampling_settings = replace(
        validated,
        timeout_seconds=effective_timeout,
        max_tokens=effective_max_tokens,
    )
    provider = provider_label_for_settings(sampling_settings)
    contract = {
        "version": VISIBILITY_SAMPLING_PREFLIGHT_VERSION,
        "status": "passed",
        "mode": "local_before_provider_call",
        "provider": provider,
        "model": sampling_settings.model,
        "endpoint_sha256": hashlib.sha256(endpoint.encode("utf-8")).hexdigest(),
        "timeout_seconds": effective_timeout,
        "configured_timeout_seconds": configured_timeout,
        "timeout_capped": effective_timeout != configured_timeout,
        "max_output_tokens": effective_max_tokens,
        "configured_max_tokens": configured_max_tokens,
        "max_output_tokens_capped": (
            effective_max_tokens != configured_max_tokens
        ),
        "network_requests": 0,
        "provider_capabilities_verified": False,
        "runtime_probe_sample_index": 1,
        "runtime_probe_reuses_requested_sample": True,
        "extra_provider_requests": 0,
        "automatic_retry_on_unknown_outcome": False,
        "boundary": (
            "本地预检只验证端点、凭据形态、模型标识、超时和输出上限；"
            "供应商是否真正遵守 OpenAI-compatible 响应及 max_tokens，"
            "由第一条正式样本验证，不额外发送测试请求。"
        ),
    }
    return sampling_settings, contract


def _request_fingerprint(
    normalized: Mapping[str, Any], *, settings: AISettings
) -> str:
    try:
        provider_host = (urlsplit(settings.base_url).hostname or "unknown").lower()
    except ValueError:
        provider_host = "unknown"
    payload = {
        "project_id": normalized["project_id"],
        "prompt": normalized["prompt"],
        "sample_count": normalized["sample_count"],
        "aliases": normalized["aliases"],
        "competitors": normalized["competitors"],
        "persist": normalized["persist"],
        "provider_host": provider_host,
        "provider_endpoint_sha256": hashlib.sha256(
            settings.base_url.strip().rstrip("/").encode("utf-8")
        ).hexdigest(),
        "model": settings.model,
        "sampling_version": VISIBILITY_SAMPLING_VERSION,
    }
    # Keep the durable provider-effect identity compatible with receipts made
    # before project keywords participated in local parsing.  Keywords never
    # cross the provider boundary; their changing the external idempotency
    # fingerprint would turn a safe replay into a conflict.  They are tracked
    # separately by ``_local_analysis_fingerprint`` so the captured answer can
    # be reparsed without another billable request.
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _local_project_values(
    project: Mapping[str, Any], *, expected_id: int
) -> dict[str, Any]:
    if not isinstance(project, Mapping):
        raise VisibilitySamplingValidationError("品牌项目不存在")
    raw_project_id = project.get("id")
    if isinstance(raw_project_id, bool):
        raise VisibilitySamplingValidationError("project_id 必须是正整数")
    try:
        project_id = int(raw_project_id)
    except (TypeError, ValueError) as exc:
        raise VisibilitySamplingValidationError("project_id 必须是正整数") from exc
    if project_id != int(expected_id):
        raise VisibilitySamplingValidationError("品牌项目与历史采样记录不匹配")
    return {
        "project_id": project_id,
        "project_name": str(project.get("name") or "").strip(),
        "project_product": str(project.get("product") or "").strip(),
        "project_keywords": copy.deepcopy(project.get("keywords") or []),
        "project_website": str(project.get("website") or "").strip(),
        "stored_competitors": copy.deepcopy(project.get("competitors") or []),
    }


def _local_analysis_fingerprint(
    *,
    project_values: Mapping[str, Any],
    prompt: Any,
    responses: Any,
    aliases: Any,
    competitors: Any,
) -> str:
    payload = {
        "snapshot_version": LOCAL_ANALYSIS_INPUT_SNAPSHOT_VERSION,
        "project": dict(project_values),
        "prompt": prompt,
        "responses": responses,
        "aliases": aliases,
        "competitors": competitors,
        "algorithm_versions": current_ai_visibility_algorithm_versions(),
    }
    try:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise VisibilitySamplingValidationError(
            "本地可见度分析输入无法可靠序列化"
        ) from exc
    return hashlib.sha256(encoded).hexdigest()


def _analysis_used_external_model(analysis: Any) -> bool:
    if not isinstance(analysis, Mapping):
        return False
    methodology = analysis.get("methodology")
    return bool(
        isinstance(methodology, Mapping)
        and methodology.get("external_model_used") is True
    )


def _refresh_analysis_view(
    *,
    previous_analysis: Any,
    project_values: Mapping[str, Any],
    prompt: Any,
    responses: Any,
    aliases: Any,
    competitors: Any,
    stored_local_analysis: Any,
    original_external_model_used: bool,
) -> dict[str, Any]:
    previous = previous_analysis if isinstance(previous_analysis, Mapping) else {}
    desired_fingerprint = _local_analysis_fingerprint(
        project_values=project_values,
        prompt=prompt,
        responses=responses,
        aliases=aliases,
        competitors=competitors,
    )
    stored_fingerprint = (
        stored_local_analysis.get("input_fingerprint")
        if isinstance(stored_local_analysis, Mapping)
        else None
    )
    freshness = inspect_ai_visibility_analysis_freshness(previous)
    force = stored_fingerprint != desired_fingerprint
    reason = None
    if force and not freshness["reanalysis_required"]:
        reason = (
            "local_analysis_input_changed"
            if stored_fingerprint
            else "local_analysis_input_fingerprint_missing"
        )
    outcome = reanalyze_ai_visibility_locally(
        previous_analysis=previous,
        **dict(project_values),
        prompt=prompt,
        responses=responses,
        aliases=aliases,
        competitors=competitors,
        force=force,
        reason=reason,
    )
    analysis = outcome["analysis"]
    refresh = outcome["refresh"]
    if refresh["performed"] and original_external_model_used:
        analysis["methodology"] = {
            **analysis["methodology"],
            "external_model_used": True,
            "external_model_used_semantics": (
                "The response text was captured from the original provider call; "
                "this local refresh made zero provider or network requests."
            ),
        }
    analysis["methodology"] = {
        **analysis["methodology"],
        "local_analysis_input_fingerprint": desired_fingerprint,
    }
    return {
        "analysis": analysis,
        "refresh": refresh,
        "local_analysis": {
            "input_fingerprint": desired_fingerprint,
            "input_snapshot_version": LOCAL_ANALYSIS_INPUT_SNAPSHOT_VERSION,
            "algorithm_versions": current_ai_visibility_algorithm_versions(),
        },
        "freshness_before_refresh": freshness,
    }


def _saved_analysis_overrides(
    database_path: str | Path,
    *,
    record_data: Mapping[str, Any],
    expected_project_id: int,
) -> dict[str, Any] | None:
    snapshot = record_data.get("local_analysis_input_snapshot")
    if (
        isinstance(snapshot, Mapping)
        and snapshot.get("version") == LOCAL_ANALYSIS_INPUT_SNAPSHOT_VERSION
        and {"aliases", "competitors"}.issubset(snapshot)
    ):
        return {
            "aliases": copy.deepcopy(snapshot.get("aliases")),
            "competitors": copy.deepcopy(snapshot.get("competitors")),
        }

    run_id = record_data.get("run_id")
    if isinstance(run_id, bool) or not isinstance(run_id, int) or run_id <= 0:
        return None
    try:
        saved_run = get_ai_visibility_run(
            database_path,
            run_id,
            project_id=expected_project_id,
        )
    except (LookupError, RuntimeError, ValueError, sqlite3.Error):
        return None
    coverage = saved_run.get("coverage_items")
    if not isinstance(coverage, Mapping) or not {
        "aliases",
        "competitors",
    }.issubset(coverage):
        return None
    return {
        "aliases": copy.deepcopy(coverage.get("aliases")),
        "competitors": copy.deepcopy(coverage.get("competitors")),
    }


def _unavailable_refresh(previous_analysis: Any, *, reason: str) -> dict[str, Any]:
    previous = previous_analysis if isinstance(previous_analysis, Mapping) else {}
    freshness = inspect_ai_visibility_analysis_freshness(previous)
    return {
        **freshness,
        "status": "stale_unresolved",
        "reanalysis_required": True,
        "performed": False,
        "reason": reason,
        "provider_calls": 0,
        "network_requests": 0,
    }


def _mark_sample_refresh_unavailable(
    record: Mapping[str, Any], *, reason: str
) -> dict[str, Any]:
    output = copy.deepcopy(dict(record))
    result = output.get("result")
    if not isinstance(result, dict):
        output["analysis_refresh"] = _unavailable_refresh({}, reason=reason)
        return output
    data = result.get("data")
    if not isinstance(data, dict):
        output["analysis_refresh"] = _unavailable_refresh({}, reason=reason)
        return output
    refresh = _unavailable_refresh(data.get("analysis"), reason=reason)
    data["analysis_refresh"] = refresh
    result["data"] = data
    output["result"] = result
    output["analysis_refresh"] = refresh
    return output


def _mark_run_refresh_unavailable(
    saved: Mapping[str, Any], *, reason: str
) -> dict[str, Any]:
    output = copy.deepcopy(dict(saved))
    refresh = _unavailable_refresh(output.get("result"), reason=reason)
    output["analysis_refresh"] = refresh
    return output


def _refresh_sample_record_locally(
    database_path: str | Path,
    *,
    record: Mapping[str, Any],
    project: Mapping[str, Any],
    exact_overrides: Mapping[str, Any] | None,
) -> dict[str, Any]:
    result = record.get("result")
    if not isinstance(result, Mapping):
        return _mark_sample_refresh_unavailable(
            record, reason="saved_sampling_result_incomplete"
        )
    data = result.get("data")
    if not isinstance(data, Mapping):
        return _mark_sample_refresh_unavailable(
            record, reason="saved_sampling_result_incomplete"
        )
    responses = data.get("captured_responses")
    if not isinstance(responses, list) or not responses:
        existing_refresh = data.get("analysis_refresh")
        if (
            isinstance(existing_refresh, Mapping)
            and existing_refresh.get("status") == "not_available"
        ):
            return copy.deepcopy(dict(record))
        return _mark_sample_refresh_unavailable(
            record, reason="captured_responses_unavailable"
        )
    project_values = _local_project_values(
        project, expected_id=int(record["project_id"])
    )
    overrides = (
        dict(exact_overrides)
        if isinstance(exact_overrides, Mapping)
        and {"aliases", "competitors"}.issubset(exact_overrides)
        else _saved_analysis_overrides(
            database_path,
            record_data=data,
            expected_project_id=project_values["project_id"],
        )
    )
    if overrides is None:
        return _mark_sample_refresh_unavailable(
            record, reason="saved_analysis_inputs_incomplete"
        )
    previous_analysis = data.get("analysis")
    saved_prompt = (
        previous_analysis.get("prompt")
        if isinstance(previous_analysis, Mapping)
        else None
    )
    if not isinstance(saved_prompt, str) or not saved_prompt.strip():
        return _mark_sample_refresh_unavailable(
            record, reason="saved_prompt_unavailable"
        )
    try:
        refreshed = _refresh_analysis_view(
            previous_analysis=previous_analysis,
            project_values=project_values,
            prompt=saved_prompt,
            responses=responses,
            aliases=overrides.get("aliases"),
            competitors=overrides.get("competitors"),
            stored_local_analysis=data.get("local_analysis"),
            original_external_model_used=True,
        )
    except (VisibilityAnalysisError, VisibilitySamplingValidationError):
        return _mark_sample_refresh_unavailable(
            record, reason="saved_analysis_data_invalid"
        )

    output = copy.deepcopy(dict(record))
    output_result = copy.deepcopy(dict(result))
    output_data = copy.deepcopy(dict(data))
    output_data["analysis"] = refreshed["analysis"]
    output_data["local_analysis"] = refreshed["local_analysis"]
    output_data["analysis_refresh"] = refreshed["refresh"]
    output_result["data"] = output_data
    output["result"] = output_result
    output["analysis_refresh"] = refreshed["refresh"]
    return output


def _processing_payload(
    *,
    request_record: Mapping[str, Any],
    normalized: Mapping[str, Any],
    settings: AISettings,
    provider: str,
    contract_preflight: Mapping[str, Any],
    attempts: Sequence[Mapping[str, Any]],
    executions: Sequence[Mapping[str, Any]],
    analysis_inputs: Sequence[Mapping[str, Any]],
    active_attempt: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Build a durable, secret-free in-progress receipt.

    ``active_attempt`` is intentionally conservative: once the sampler is
    about to receive control, a crash cannot prove whether bytes reached the
    provider.  The checkpoint therefore forbids automatic retry.
    """

    attempt_count = len(attempts) + (1 if active_attempt is not None else 0)
    completed_count = len(analysis_inputs)
    return {
        "code": 202,
        "msg": "采样处理中；请按原 request_id 查询，勿自动重试或更换幂等键",
        "data": {
            "status": "processing",
            "need_action": False,
            "sampling_version": VISIBILITY_SAMPLING_VERSION,
            "request_id": request_record["request_id"],
            "idempotency_key": request_record["idempotency_key"],
            "project_id": normalized["project_id"],
            "configured_provider": provider,
            "configured_model": settings.model,
            "requested_sample_count": normalized["sample_count"],
            "completed_sample_count": completed_count,
            "provider_attempt_count": attempt_count,
            "unattempted_sample_count": max(
                0, normalized["sample_count"] - attempt_count
            ),
            "executions": copy.deepcopy(list(executions)),
            "attempts": copy.deepcopy(list(attempts)),
            "active_attempt": (
                copy.deepcopy(dict(active_attempt))
                if active_attempt is not None
                else None
            ),
            "captured_responses": copy.deepcopy(list(analysis_inputs)),
            "contract_preflight": copy.deepcopy(dict(contract_preflight)),
            "contract_probe": _contract_probe_summary(attempts, failure=None),
            "outcome_unknown": active_attempt is not None,
            "request_policy": {
                "execution": "sequential_stop_on_first_failure",
                "max_output_tokens": settings.max_tokens,
                "timeout_seconds": settings.timeout_seconds,
                "first_requested_sample_is_contract_probe": True,
                "extra_contract_probe_provider_calls": 0,
            },
            "retry_policy": {
                "automatic_retry": False,
                "same_key_replays_saved_result": True,
                "ambiguous_outcome_action": (
                    "先核对模型账单与原请求；仅在确认需要新调用后使用新的幂等键"
                ),
            },
        },
    }


def _contract_probe_summary(
    attempts: Sequence[Mapping[str, Any]],
    *,
    failure: Mapping[str, Any] | None,
) -> dict[str, Any]:
    first = next(
        (
            dict(item)
            for item in attempts
            if not isinstance(item.get("index"), bool)
            and item.get("index") == 1
        ),
        None,
    )
    if first is None:
        status = "not_attempted"
        outcome_unknown = False
        failure_code = None
    else:
        first_failure = (
            first.get("failure")
            if isinstance(first.get("failure"), Mapping)
            else None
        )
        mismatch = (
            first.get("contract_mismatch")
            if isinstance(first.get("contract_mismatch"), Mapping)
            else None
        )
        outcome_unknown = first.get("state") == "outcome_unknown"
        if outcome_unknown:
            status = "outcome_unknown"
        elif first.get("state") != "succeeded" or mismatch is not None:
            status = "failed"
        else:
            status = "passed"
        failure_code = str(
            (first_failure or mismatch or {}).get("code") or ""
        ) or None
        if (
            status == "passed"
            and isinstance(failure, Mapping)
            and failure.get("sample_index") == 1
            and failure.get("code") == "provider_output_limit_mismatch"
        ):
            status = "failed"
            failure_code = "provider_output_limit_mismatch"
    return {
        "status": status,
        "sample_index": 1,
        "reuses_requested_sample": True,
        "extra_provider_requests": 0,
        "outcome_unknown": outcome_unknown,
        "failure_code": failure_code,
    }


def _begin_or_replay(
    database_path: str | Path,
    *,
    project_id: int,
    request_id: str,
    idempotency_key: str,
    request_fingerprint: str,
) -> tuple[dict[str, Any], bool]:
    with closing(_connect(database_path)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            request_row = conn.execute(
                "SELECT * FROM ai_visibility_sample_requests WHERE request_id = ?",
                (request_id,),
            ).fetchone()
            if request_row is not None and request_row["idempotency_key"] != idempotency_key:
                raise VisibilitySamplingConflictError(
                    "X-Request-ID 已绑定到其他 Idempotency-Key"
                )
            row = conn.execute(
                "SELECT * FROM ai_visibility_sample_requests WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if row is not None:
                if row["request_fingerprint"] != request_fingerprint:
                    raise VisibilitySamplingConflictError(
                        "Idempotency-Key 已用于不同的可见度采样请求"
                    )
                record = _serialize_request(row)
                conn.commit()
                if row["status"] == "started" or not row["result_json"]:
                    raise VisibilitySamplingInProgressError(record)
                return record, True
            if request_row is not None:
                raise VisibilitySamplingConflictError(
                    "X-Request-ID 已绑定到不同的可见度采样请求"
                )
            cursor = conn.execute(
                """
                INSERT INTO ai_visibility_sample_requests (
                    project_id, request_id, idempotency_key,
                    request_fingerprint, status
                ) VALUES (?, ?, ?, ?, 'started')
                """,
                (project_id, request_id, idempotency_key, request_fingerprint),
            )
            row = conn.execute(
                "SELECT * FROM ai_visibility_sample_requests WHERE id = ?",
                (cursor.lastrowid,),
            ).fetchone()
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return _serialize_request(row), False


def _checkpoint_request(
    database_path: str | Path,
    *,
    record_id: int,
    result: Mapping[str, Any],
) -> None:
    encoded = json.dumps(
        result, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    with closing(_connect(database_path)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        cursor = conn.execute(
            """
            UPDATE ai_visibility_sample_requests
            SET result_json = ?,
                updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
            WHERE id = ? AND status = 'started'
            """,
            (encoded, int(record_id)),
        )
        if cursor.rowcount != 1:
            conn.rollback()
            raise VisibilitySamplingConflictError(
                "采样请求状态已变化，未启动新的供应商调用"
            )
        conn.commit()


def _finish_request(
    database_path: str | Path,
    *,
    record_id: int,
    status: str,
    http_status: int,
    result: Mapping[str, Any],
) -> None:
    encoded = json.dumps(
        result, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    with closing(_connect(database_path)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        cursor = conn.execute(
            """
            UPDATE ai_visibility_sample_requests
            SET status = ?, http_status = ?, result_json = ?,
                updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
            WHERE id = ? AND status = 'started'
            """,
            (status, int(http_status), encoded, int(record_id)),
        )
        if cursor.rowcount != 1:
            conn.rollback()
            raise VisibilitySamplingConflictError(
                "采样请求状态已变化，未覆盖既有结果"
            )
        conn.commit()


def _serialize_request(
    row: sqlite3.Row,
    *,
    current_time: datetime | None = None,
) -> dict[str, Any]:
    result = None
    if row["result_json"]:
        try:
            decoded = json.loads(row["result_json"])
        except json.JSONDecodeError as exc:
            raise VisibilitySamplingConflictError("已保存的采样结果无法读取") from exc
        if not isinstance(decoded, dict):
            raise VisibilitySamplingConflictError("已保存的采样结果格式无效")
        if decoded.get("record_type") == _PANEL_RECORD_TYPE:
            raise VisibilitySamplingConflictError(
                "该请求标识属于多模型采样，不能通过单模型采样接口读取"
            )
        result = decoded
    status = str(row["status"])
    in_progress_age_seconds = None
    in_progress_expired = False
    if status == "started":
        in_progress_age_seconds = _started_age_seconds(
            row["created_at"], current_time=current_time
        )
        in_progress_expired = (
            in_progress_age_seconds >= IN_PROGRESS_EXPIRY_SECONDS
        )
    return {
        "id": int(row["id"]),
        "project_id": int(row["project_id"]),
        "request_id": row["request_id"],
        "idempotency_key": row["idempotency_key"],
        "request_fingerprint": row["request_fingerprint"],
        "status": status,
        "in_progress": status == "started",
        "in_progress_age_seconds": in_progress_age_seconds,
        "in_progress_expired": in_progress_expired,
        "in_progress_expiry_seconds": IN_PROGRESS_EXPIRY_SECONDS,
        "http_status": row["http_status"],
        "result": result,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _started_age_seconds(
    created_at: Any,
    *,
    current_time: datetime | None,
) -> int:
    if not isinstance(created_at, str):
        raise VisibilitySamplingConflictError("采样请求创建时间格式无效")
    try:
        created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise VisibilitySamplingConflictError("采样请求创建时间格式无效") from exc
    if created.tzinfo is None or created.utcoffset() is None:
        raise VisibilitySamplingConflictError("采样请求创建时间必须使用 UTC 时区")
    observed_at = current_time or datetime.now(timezone.utc)
    if not isinstance(observed_at, datetime):
        raise VisibilitySamplingValidationError("current_time 必须是 datetime")
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise VisibilitySamplingValidationError("current_time 必须包含时区")
    return max(0, int((observed_at.astimezone(timezone.utc) - created).total_seconds()))


def _provider_failure(exc: AIServiceError, *, sample_index: int) -> dict[str, Any]:
    if isinstance(exc, AIReadTimeoutError):
        code = "provider_outcome_unknown"
        outcome_unknown = True
        request_state = "sent_outcome_unknown"
        billing_outcome = "unknown"
    elif isinstance(exc, AIProviderRateLimitError):
        code = "provider_rate_limited"
        outcome_unknown = False
        request_state = "response_rejected_before_completion"
        billing_outcome = "not_expected"
    elif isinstance(exc, AIConnectError):
        code = "provider_unavailable"
        outcome_unknown = False
        request_state = "not_sent"
        billing_outcome = "not_expected"
    elif isinstance(exc, AIConfigurationError):
        code = "provider_not_configured"
        outcome_unknown = False
        request_state = "not_sent"
        billing_outcome = "not_expected"
    elif isinstance(exc, AIProtocolError):
        code = "provider_protocol_error"
        outcome_unknown = False
        request_state = "response_received"
        billing_outcome = "unknown"
    elif isinstance(exc, AIProviderHTTPError):
        upstream_status = int(exc.status_code)
        outcome_unknown = bool(exc.outcome_unknown) or upstream_status == 408
        code = "provider_outcome_unknown" if outcome_unknown else "provider_error"
        request_state = (
            "sent_outcome_unknown" if outcome_unknown else "error_response_received"
        )
        billing_outcome = "unknown" if upstream_status >= 500 else "not_expected"
    else:
        # The base error type does not prove whether transport setup failed or
        # bytes reached the remote gateway. Fail closed and require manual
        # reconciliation rather than making a second potentially paid call.
        code = "provider_outcome_unknown"
        outcome_unknown = True
        request_state = "unknown"
        billing_outcome = "unknown"
    return {
        "code": code,
        "message": str(exc),
        "sample_index": sample_index,
        "outcome_unknown": outcome_unknown,
        "request_state": request_state,
        "billing_outcome": billing_outcome,
        "upstream_status": getattr(exc, "status_code", None),
        "retry_after_seconds": getattr(exc, "retry_after_seconds", None),
    }


def _record_failed_attempt_usage(
    database_path: str | Path,
    *,
    usage_recorder: Callable[..., dict[str, Any]],
    error: AIServiceError,
    provider: str,
    model: str,
    project_id: int,
    reference_id: str,
    failure: Mapping[str, Any],
) -> tuple[dict[str, Any] | None, str]:
    """Record only attempts whose charge cannot be ruled out locally."""

    if failure.get("billing_outcome") != "unknown":
        return None, "not_applicable"
    try:
        record = usage_recorder(
            database_path,
            operation="visibility_sample",
            provider=provider,
            model=model,
            usage=None,
            usage_status="unknown",
            project_id=project_id,
            reference_type="visibility_sample",
            reference_id=reference_id,
            metadata={
                "outcome": (
                    "unknown" if failure.get("outcome_unknown") else "failed"
                ),
                "usage_origin": (
                    "provider_error_without_usage"
                    if isinstance(error, (AIProtocolError, AIProviderHTTPError))
                    else "transport_outcome_unknown"
                ),
            },
        )
    except (AIUsageLedgerError, sqlite3.Error):
        return None, "failed"
    return record, "recorded_unknown"


def _reported_output_limit_mismatch(
    usage: Mapping[str, Any] | None,
    *,
    requested_max_output_tokens: int,
) -> int | None:
    """Return a clearly excessive reported output count, if one exists.

    Small provider-side accounting differences are tolerated. The guard only
    trips once the reported usage is both 128 tokens and 2x beyond the limit,
    which keeps it focused on the costly mismatch seen with reasoning gateways.
    """

    if not isinstance(usage, Mapping):
        return None
    raw_output_tokens = usage.get("output_tokens")
    if raw_output_tokens is None or isinstance(raw_output_tokens, bool):
        return None
    try:
        output_tokens = int(raw_output_tokens)
    except (TypeError, ValueError):
        return None
    allowed = max(1, int(requested_max_output_tokens))
    mismatch_threshold = max(allowed + 128, allowed * 2)
    return output_tokens if output_tokens > mismatch_threshold else None


def _failed_provider_attempt_count(
    failure: Mapping[str, Any] | None,
    completed_count: int,
) -> int:
    """Count a failed external request without miscounting post-success guards."""

    if not isinstance(failure, Mapping):
        return 0
    sample_index = failure.get("sample_index")
    if isinstance(sample_index, bool):
        return 0
    try:
        failed_index = int(sample_index)
    except (TypeError, ValueError):
        return 0
    return 1 if failed_index > max(0, int(completed_count)) else 0


def _failure_http_status(failure: Mapping[str, Any] | None) -> int:
    code = str((failure or {}).get("code") or "")
    if code == "provider_rate_limited":
        return 429
    if code == "provider_outcome_unknown":
        return 504
    if code in {"provider_unavailable", "provider_not_configured"}:
        return 503
    return 502


def _data_disclosure(prompt: str, sample_count: int) -> dict[str, Any]:
    return {
        "sent_to_provider": ["prompt"],
        "sent_as_transport_metadata": [
            "configured_model_identifier",
            "ai_provider_authorization_header",
            "temperature",
            "max_tokens",
            "stream_false",
        ],
        "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        "operator_prompt_controls_all_text_content": True,
        "provider_request_count_requested": sample_count,
        "not_sent_to_provider": [
            "project_record",
            "project_description",
            "project_website",
            "stored_articles",
            "aliases",
            "competitors",
            "media_accounts",
            "cookies",
            "media_credentials",
        ],
        "boundary": (
            "每次调用的 messages 仅包含一个 user 消息，其 content 为原始 prompt；"
            "模型标识、该 AI 服务自身的鉴权头和生成参数仅用于完成传输。"
        ),
    }


def _grounding_boundary() -> dict[str, Any]:
    return {
        "web_search_requested": False,
        "web_search_verified": False,
        "provider_grounding_guaranteed": False,
        "status": "not_requested_unverified",
        "boundary": (
            "请求未传递网页搜索工具、检索上下文或引用材料；回答是否基于当前网页"
            "无法由本接口确认，显式 URL 仅按回答文本证据分析。"
        ),
    }


def _request_identifier(value: Any, field: str, maximum: int) -> str:
    text = str(value or "").strip()
    if not text or len(text) > maximum:
        raise VisibilitySamplingValidationError(f"{field} 格式无效")
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:-")
    if any(character not in allowed for character in text):
        raise VisibilitySamplingValidationError(f"{field} 格式无效")
    return text


def _bounded_metadata(value: Any, maximum: int, fallback: str) -> str:
    text = str(value or "").strip().replace("\0", "")
    return (text[:maximum] or fallback)


def _connect(database_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(Path(database_path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


__all__ = [
    "IN_PROGRESS_EXPIRY_SECONDS",
    "LOCAL_ANALYSIS_INPUT_SNAPSHOT_VERSION",
    "MAX_SAMPLE_COUNT",
    "MIN_SAMPLE_COUNT",
    "VISIBILITY_SAMPLE_MAX_OUTPUT_TOKENS",
    "VISIBILITY_SAMPLE_MAX_TIMEOUT_SECONDS",
    "VISIBILITY_SAMPLING_PREFLIGHT_VERSION",
    "VISIBILITY_SAMPLING_VERSION",
    "VisibilitySamplingConflictError",
    "VisibilitySamplingInProgressError",
    "VisibilitySamplingRequestNotFoundError",
    "VisibilitySamplingValidationError",
    "get_ai_visibility_sample_request",
    "refresh_ai_visibility_run_locally",
    "refresh_ai_visibility_sample_request_locally",
    "run_ai_visibility_sample",
]
