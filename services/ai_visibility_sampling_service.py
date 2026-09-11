"""Controlled live sampling for the configured OpenAI-compatible model.

Only the explicit operator prompt crosses the provider boundary as natural-
language context; normal model/authorization transport metadata is still
required. Project metadata, aliases, competitors, prior content and media
credentials remain local and are used solely by the deterministic visibility
engine. Every request is durably idempotent before the first provider call.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import urlsplit

from services.ai_cost_ledger_service import AIUsageLedgerError, record_ai_usage
from services.ai_service import (
    AIConfigurationError,
    AIConnectError,
    AIProtocolError,
    AIProviderRateLimitError,
    AIReadTimeoutError,
    AIServiceError,
    AISettings,
    provider_label_for_settings,
    sample_ai_prompt_detailed,
)
from services.ai_visibility_run_service import save_ai_visibility_run
from services.ai_visibility_service import (
    VisibilityAnalysisError,
    analyze_ai_visibility,
)


VISIBILITY_SAMPLING_VERSION = "controlled-live-visibility-sampling-1.1.0"
VISIBILITY_SAMPLE_MAX_OUTPUT_TOKENS = 768
MIN_SAMPLE_COUNT = 1
MAX_SAMPLE_COUNT = 5
MAX_PROMPT_LENGTH = 8_000


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
) -> dict[str, Any]:
    """Read one durable sampling request without invoking the AI provider.

    This is the safe resume seam used by the desktop UI after route changes or
    window reloads.  A ``started`` row is reported as in progress; a completed
    row returns the exact persisted response envelope.
    """

    normalized_request_id = _request_identifier(request_id, "X-Request-ID", 128)
    with closing(_connect(database_path)) as conn:
        row = conn.execute(
            "SELECT * FROM ai_visibility_sample_requests WHERE request_id = ?",
            (normalized_request_id,),
        ).fetchone()
    if row is None:
        raise VisibilitySamplingRequestNotFoundError("可见度采样请求不存在")
    return _serialize_request(row)


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
    fingerprint = _request_fingerprint(normalized, settings=settings)
    request_record, replayed = _begin_or_replay(
        database_path,
        project_id=normalized["project_id"],
        request_id=normalized["request_id"],
        idempotency_key=normalized["idempotency_key"],
        request_fingerprint=fingerprint,
    )
    if replayed:
        return {
            "http_status": int(request_record["http_status"]),
            "payload": request_record["result"],
            "request_id": request_record["request_id"],
            "idempotency_key": request_record["idempotency_key"],
            "replayed": True,
        }

    executions: list[dict[str, Any]] = []
    analysis_inputs: list[dict[str, Any]] = []
    failure: dict[str, Any] | None = None
    provider = provider_label_for_settings(settings)
    # A visibility probe asks for a short answer, not a full article.  Reusing
    # the writer's 4096-token ceiling allowed reasoning/generation to run until
    # the 120-second transport timeout even though the fixed "reply OK" health
    # check succeeded.  Keep credentials, endpoint, model and timeout intact,
    # but cap only this workflow's output budget.
    sampling_settings = replace(
        settings,
        max_tokens=max(
            1,
            min(int(settings.max_tokens), VISIBILITY_SAMPLE_MAX_OUTPUT_TOKENS),
        ),
    )

    for index in range(1, normalized["sample_count"] + 1):
        try:
            completion = sampler(
                prompt=normalized["prompt"],
                settings=sampling_settings,
            )
        except AIServiceError as exc:
            failure = _provider_failure(exc, sample_index=index)
            break

        model = _bounded_metadata(completion.model or settings.model, 200, "unknown")
        provider_request_id = _bounded_metadata(
            completion.provider_request_id, 200, ""
        )
        usage = dict(completion.usage) if isinstance(completion.usage, Mapping) else None
        text = str(completion.content or "").strip()
        if not text:
            failure = {
                "code": "empty_provider_response",
                "message": "模型返回了空回答，后续样本已停止；不要自动重试",
                "sample_index": index,
                "outcome_unknown": False,
                "retry_after_seconds": None,
            }
            break

        analysis_inputs.append(
            {"text": text, "provider": provider, "model": model}
        )
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
                    "outcome": "succeeded",
                    "usage_origin": (
                        "provider_response" if usage is not None else "not_reported"
                    ),
                },
            )
        except (AIUsageLedgerError, sqlite3.Error):
            # The model answered, so the response must be retained and the key
            # closed as ambiguous. Retrying could incur a duplicate charge.
            usage_record = None
            failure = {
                "code": "cost_ledger_failed",
                "message": "模型已返回，但本地费用凭据未能保存；请人工核对，勿自动重试",
                "sample_index": index,
                "outcome_unknown": False,
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
        if failure is not None:
            break

    analysis = None
    run_id = None
    created_at = None
    if analysis_inputs:
        try:
            analysis = analyze_ai_visibility(
                project_id=normalized["project_id"],
                project_name=normalized["project_name"],
                project_product=normalized["project_product"],
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
            if normalized["persist"]:
                saved = save_ai_visibility_run(
                    database_path,
                    project_id=normalized["project_id"],
                    prompt=normalized["prompt"],
                    input_samples=analysis_inputs,
                    coverage_items={
                        "aliases": normalized["aliases"],
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

    payload = {
        "status": status,
        "need_action": need_action,
        "sampling_version": VISIBILITY_SAMPLING_VERSION,
        "request_id": request_record["request_id"],
        "idempotency_key": request_record["idempotency_key"],
        "project_id": normalized["project_id"],
        "requested_sample_count": normalized["sample_count"],
        "completed_sample_count": completed_count,
        "unattempted_sample_count": max(
            0, normalized["sample_count"] - completed_count - (1 if failure else 0)
        ),
        "persist_requested": normalized["persist"],
        "persisted": run_id is not None,
        "run_id": run_id,
        "created_at": created_at,
        "executions": executions,
        "captured_responses": analysis_inputs,
        "failure": failure,
        "analysis": analysis,
        "data_disclosure": _data_disclosure(
            normalized["prompt"], normalized["sample_count"]
        ),
        "grounding": _grounding_boundary(),
        "request_policy": {
            "execution": "sequential_stop_on_first_failure",
            "max_output_tokens": sampling_settings.max_tokens,
            "timeout_seconds": sampling_settings.timeout_seconds,
            "connection_test_equivalent": False,
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
        raise AIConfigurationError("缺少有效的 AI 配置")
    normalized_request_id = _request_identifier(request_id, "X-Request-ID", 128)
    normalized_key = _request_identifier(
        idempotency_key, "Idempotency-Key", 150
    )

    project_name = str(project.get("name") or "").strip()
    project_product = str(project.get("product") or "").strip()
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
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


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


def _serialize_request(row: sqlite3.Row) -> dict[str, Any]:
    result = None
    if row["result_json"]:
        try:
            decoded = json.loads(row["result_json"])
        except json.JSONDecodeError as exc:
            raise VisibilitySamplingConflictError("已保存的采样结果无法读取") from exc
        if not isinstance(decoded, dict):
            raise VisibilitySamplingConflictError("已保存的采样结果格式无效")
        result = decoded
    return {
        "id": int(row["id"]),
        "project_id": int(row["project_id"]),
        "request_id": row["request_id"],
        "idempotency_key": row["idempotency_key"],
        "request_fingerprint": row["request_fingerprint"],
        "status": row["status"],
        "http_status": row["http_status"],
        "result": result,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _provider_failure(exc: AIServiceError, *, sample_index: int) -> dict[str, Any]:
    if isinstance(exc, AIReadTimeoutError):
        code = "provider_outcome_unknown"
        outcome_unknown = True
    elif isinstance(exc, AIProviderRateLimitError):
        code = "provider_rate_limited"
        outcome_unknown = False
    elif isinstance(exc, AIConnectError):
        code = "provider_unavailable"
        outcome_unknown = False
    elif isinstance(exc, AIConfigurationError):
        code = "provider_not_configured"
        outcome_unknown = False
    elif isinstance(exc, AIProtocolError):
        code = "provider_protocol_error"
        outcome_unknown = False
    else:
        code = "provider_error"
        outcome_unknown = False
    return {
        "code": code,
        "message": str(exc),
        "sample_index": sample_index,
        "outcome_unknown": outcome_unknown,
        "retry_after_seconds": getattr(exc, "retry_after_seconds", None),
    }


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
    "MAX_SAMPLE_COUNT",
    "MIN_SAMPLE_COUNT",
    "VISIBILITY_SAMPLE_MAX_OUTPUT_TOKENS",
    "VISIBILITY_SAMPLING_VERSION",
    "VisibilitySamplingConflictError",
    "VisibilitySamplingInProgressError",
    "VisibilitySamplingRequestNotFoundError",
    "VisibilitySamplingValidationError",
    "get_ai_visibility_sample_request",
    "run_ai_visibility_sample",
]
