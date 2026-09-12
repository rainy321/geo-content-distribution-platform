from __future__ import annotations

import os
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from services.ai_service import (
    AIEvaluationResult,
    AIOptimizationResult,
    AIServiceError,
    AISettings,
    evaluate_geo_content,
    optimize_geo_content,
    provider_label_for_settings,
)
from services.ai_agent_audit_service import (
    AI_AGENT_PIPELINE_VERSION,
    article_diff,
    article_snapshot,
    stage_run_id,
    summarize_model_independence,
)
from services.content_engine_adapters import (
    ColleagueContentEngineAdapter,
    ContentEngineAdapter,
    QwenContentEngineAdapter,
)
from services.content_engine_contract import (
    ContentEngineConfigurationError,
    ContentEngineError,
    ContentGenerationRequest,
    ContentGenerationResult,
)
from services.content_generation_run_service import (
    GenerationRunOutcome,
    begin_or_replay,
    mark_failed,
    mark_succeeded,
    mark_unknown,
)
from services.content_hygiene_service import (
    authoring_visible_length,
    constrain_generated_content_length,
    ensure_owned_website_reference,
    sanitize_generated_content,
)
from services.geo_score_service import score_geo_content
from services.geo_article_compiler_service import (
    resolve_generation_length_context,
    score_ai_citeability,
)
from services.target_length_score_profile import resolve_target_length_score_profile


class ContentGenerationInProgressError(ContentEngineError):
    error_code = "generation_in_progress"


class ReplayedContentGenerationError(ContentEngineError):
    def __init__(
        self,
        message: str,
        *,
        error_code: str,
        trace_id: str,
        state_unknown: bool,
        retry_after_seconds: int | None = None,
    ) -> None:
        super().__init__(
            message,
            trace_id=trace_id,
            retry_after_seconds=retry_after_seconds,
        )
        self.error_code = error_code or "engine_error"
        self.state_unknown = state_unknown


@dataclass(frozen=True)
class _AutomaticReviewOutcome:
    result: ContentGenerationResult
    score: Mapping[str, Any]
    citeability: Mapping[str, Any]
    metadata: Mapping[str, Any]
    usage_records: tuple[Mapping[str, Any], ...] = ()


@dataclass(frozen=True)
class _AutomaticEvaluationOutcome:
    metadata: Mapping[str, Any]
    usage_records: tuple[Mapping[str, Any], ...] = ()


class ContentGenerationService:
    """Route one request through a configured engine and persist its audit trail."""

    def __init__(
        self,
        database_path: str | Path,
        primary_adapter: ContentEngineAdapter,
        fallback_adapter: ContentEngineAdapter | None = None,
        *,
        reviewer_settings: AISettings | None = None,
        reviewer_role: Mapping[str, Any] | None = None,
        reviewer_optimizer: Callable[..., Mapping[str, Any] | AIOptimizationResult]
        | None = None,
        evaluator_settings: AISettings | None = None,
        evaluator_role: Mapping[str, Any] | None = None,
        evaluator: Callable[..., AIEvaluationResult | Mapping[str, Any]]
        | None = None,
    ) -> None:
        self.database_path = Path(database_path)
        self.primary_adapter = primary_adapter
        self.fallback_adapter = (
            fallback_adapter
            if fallback_adapter is not None
            and fallback_adapter.name != primary_adapter.name
            else None
        )
        self.reviewer_role = _normalize_reviewer_role(
            reviewer_role,
            configured=reviewer_settings is not None,
        )
        # ``load_settings("reviewer")`` deliberately falls back to the writer
        # role.  A second provider call is only justified when the reviewer
        # role itself was explicitly configured; otherwise it would fabricate
        # the appearance (and cost) of a two-role workflow.
        self.reviewer_settings = (
            reviewer_settings if self.reviewer_role["configured"] else None
        )
        self.reviewer_optimizer = reviewer_optimizer or optimize_geo_content
        self.evaluator_role = _normalize_evaluator_role(
            evaluator_role,
            configured=evaluator_settings is not None,
        )
        self.evaluator_settings = (
            evaluator_settings if self.evaluator_role["independent"] else None
        )
        self.evaluator = evaluator or evaluate_geo_content

    @property
    def reviewer_enabled(self) -> bool:
        return self.reviewer_settings is not None

    @property
    def evaluator_enabled(self) -> bool:
        return self.evaluator_settings is not None

    @classmethod
    def from_environment(
        cls,
        database_path: str | Path,
        *,
        qwen_settings: AISettings | None = None,
        qwen_http_post=None,
        colleague_http_post=None,
        colleague_http_get=None,
        reviewer_settings: AISettings | None = None,
        reviewer_role: Mapping[str, Any] | None = None,
        reviewer_optimizer: Callable[..., Mapping[str, Any] | AIOptimizationResult]
        | None = None,
        evaluator_settings: AISettings | None = None,
        evaluator_role: Mapping[str, Any] | None = None,
        evaluator: Callable[..., AIEvaluationResult | Mapping[str, Any]]
        | None = None,
    ) -> "ContentGenerationService":
        # A request-scoped custom AI configuration is an explicit user choice.
        # It must keep the legacy behavior by selecting Qwen/OpenAI-compatible
        # directly and must not silently send the same prompt to another engine.
        if qwen_settings is not None:
            return cls(
                database_path,
                QwenContentEngineAdapter(
                    settings=qwen_settings,
                    http_post=qwen_http_post,
                ),
                None,
                reviewer_settings=reviewer_settings,
                reviewer_role=reviewer_role,
                reviewer_optimizer=reviewer_optimizer,
                evaluator_settings=evaluator_settings,
                evaluator_role=evaluator_role,
                evaluator=evaluator,
            )

        primary_name = str(os.getenv("CONTENT_ENGINE", "qwen")).strip().lower()
        fallback_name = str(os.getenv("CONTENT_ENGINE_FALLBACK", "qwen")).strip().lower()

        def build(name: str) -> ContentEngineAdapter | None:
            if not name or name in {"none", "disabled", "off"}:
                return None
            if name == "qwen":
                return QwenContentEngineAdapter(
                    settings=qwen_settings,
                    http_post=qwen_http_post,
                )
            if name == "colleague":
                return ColleagueContentEngineAdapter.from_environment(
                    http_post=colleague_http_post,
                    http_get=colleague_http_get,
                )
            raise ContentEngineConfigurationError(f"不支持的内容引擎: {name}")

        primary = build(primary_name)
        if primary is None:
            raise ContentEngineConfigurationError("CONTENT_ENGINE 不能为空")
        return cls(
            database_path,
            primary,
            build(fallback_name),
            reviewer_settings=reviewer_settings,
            reviewer_role=reviewer_role,
            reviewer_optimizer=reviewer_optimizer,
            evaluator_settings=evaluator_settings,
            evaluator_role=evaluator_role,
            evaluator=evaluator,
        )

    def generate(self, request: ContentGenerationRequest) -> dict[str, Any]:
        length_context = resolve_generation_length_context(
            request.length,
            request.target_platform,
        )
        requested_length = int(length_context["requested_length"])
        effective_length = int(length_context["effective_length"])
        run = begin_or_replay(
            self.database_path,
            request,
            configured_engine=self.primary_adapter.name,
        )
        if run.replayed:
            return self._replay(run)

        started_at = time.perf_counter()
        try:
            result = self.primary_adapter.generate(request)
        except ContentEngineError as primary_error:
            self._attach_run(
                primary_error,
                run,
                request,
                attempted_engine=_adapter_provider_name(self.primary_adapter),
            )
            if primary_error.safe_to_fallback and self.fallback_adapter is not None:
                try:
                    result = self.fallback_adapter.generate(request).with_fallback(
                        _adapter_provider_name(self.primary_adapter),
                        f"主引擎连接失败，已回退到 {self.fallback_adapter.name}",
                    )
                except ContentEngineError as fallback_error:
                    self._attach_run(
                        fallback_error,
                        run,
                        request,
                        attempted_engine=_adapter_provider_name(self.fallback_adapter),
                        fallback_from=_adapter_provider_name(self.primary_adapter),
                    )
                    self._record_error(run.run_id, fallback_error)
                    raise
            else:
                self._record_error(run.run_id, primary_error)
                raise
        except Exception as exc:
            error = ContentEngineError(
                "内容生成发生内部错误",
                trace_id=request.request_id,
            )
            self._attach_run(
                error,
                run,
                request,
                attempted_engine=_adapter_provider_name(self.primary_adapter),
            )
            mark_failed(self.database_path, run.run_id, error)
            raise error from exc

        try:
            hygiene = sanitize_generated_content(result.content)
            if hygiene.changed:
                result = replace(
                    result,
                    content=hygiene.content,
                    warnings=tuple(
                        dict.fromkeys(
                            [
                                *result.warnings,
                                "已移除未完成的作者、日期或证据占位标记",
                            ]
                        )
                    ),
                )
            owned_reference = ensure_owned_website_reference(
                result.content,
                website=request.project_context.get("website"),
                brand=request.project_context.get("name"),
            )
            if owned_reference.source is not None:
                sources = list(result.sources)
                if not any(
                    str(item.get("url") or "").casefold()
                    == owned_reference.source["url"].casefold()
                    for item in sources
                ):
                    sources.append(owned_reference.source)
                warnings = [
                    warning
                    for warning in result.warnings
                    if warning != "引擎未返回来源信息"
                ]
                if owned_reference.appended:
                    warnings.append(
                        "已附加项目配置中的 HTTPS 自有官网；该来源不代表第三方背书"
                    )
                result = replace(
                    result,
                    content=owned_reference.content,
                    sources=tuple(sources),
                    warnings=tuple(dict.fromkeys(warnings)),
                )
            if length_context["source"] == "platform_safe_limit":
                result = replace(
                    result,
                    warnings=tuple(
                        dict.fromkeys(
                            [
                                *result.warnings,
                                (
                                    f"原始请求 {requested_length} 字，已按"
                                    f"{request.target_platform or length_context['platform_key']} "
                                    f"{effective_length} 字平台安全线作为生成、清洗与评分上限"
                                ),
                            ]
                        )
                    ),
                )
            length_guard = constrain_generated_content_length(
                result.content,
                effective_length,
            )
            if length_guard.changed:
                result = replace(
                    result,
                    content=length_guard.content,
                    warnings=tuple(
                        dict.fromkeys(
                            [
                                *result.warnings,
                                (
                                    "模型正文超过目标字数，已在本地按完整语句自动压缩："
                                    f"{length_guard.original_length} → "
                                    f"{length_guard.final_length} 字（上限 "
                                    f"{length_guard.limit} 字）"
                                ),
                            ]
                        )
                    ),
                )
            length_profile = resolve_target_length_score_profile(effective_length)
            recommended_minimum = max(
                int(length_profile["minimum_content_length"]),
                (effective_length * 4 + 4) // 5,
            )
            final_visible_length = authoring_visible_length(result.content)
            if final_visible_length < recommended_minimum:
                result = replace(
                    result,
                    warnings=tuple(
                        dict.fromkeys(
                            [
                                *result.warnings,
                                (
                                    "模型正文可见字数低于目标合理下界："
                                    f"{final_visible_length} / {effective_length} 字"
                                    f"（建议至少 {recommended_minimum} 字）；"
                                    "未自动补字，请人工补充证据或重新生成"
                                ),
                            ]
                        )
                    ),
                )
            result = replace(
                result,
                elapsed_ms=round((time.perf_counter() - started_at) * 1000),
                diversity_profile=dict(request.diversity_profile or {}),
                ai_role=dict(request.ai_role or {}),
            )
            score, citeability = self._score_result(
                result,
                request,
                requested_length=requested_length,
                effective_length=effective_length,
                length_source=str(length_context["source"]),
            )
            writer_snapshot = article_snapshot(_public_article(result))
            writer_stage = {
                **dict(request.ai_role or {}),
                "role": "writer",
                "agent_run_id": stage_run_id(run.run_id, "writer"),
                "invoked": True,
                "completed": True,
                "provider": result.engine,
                "model": result.engine_version,
                "provider_request_id": result.trace_id,
            }
            review = self._automatically_review(
                result,
                score,
                citeability,
                request,
                requested_length=requested_length,
                effective_length=effective_length,
                length_source=str(length_context["source"]),
            )
            review_metadata = {
                **dict(review.metadata),
                "agent_run_id": stage_run_id(run.run_id, "reviewer"),
            }
            result = replace(
                review.result,
                elapsed_ms=round((time.perf_counter() - started_at) * 1000),
            )
            score = review.score
            citeability = review.citeability
            evaluation = self._automatically_evaluate(
                result,
                score,
                citeability,
            )
            evaluation_metadata = {
                **dict(evaluation.metadata),
                "agent_run_id": stage_run_id(run.run_id, "evaluator"),
            }
            pipeline_stages = [writer_stage, review_metadata, evaluation_metadata]
            final_snapshot = article_snapshot(_public_article(result))
            pipeline_audit = {
                "version": AI_AGENT_PIPELINE_VERSION,
                "generation_run_id": run.run_id,
                "stages": pipeline_stages,
                "drafts": {
                    "writer": writer_snapshot,
                    "reviewer": review_metadata.get("candidate"),
                    "selected": final_snapshot,
                },
                "review_diff": review_metadata.get("diff")
                or article_diff(writer_snapshot, final_snapshot),
                "selected_stage": str(review_metadata.get("selected") or "writer"),
                "model_independence": summarize_model_independence(pipeline_stages),
            }
            analysis = {
                "dimensions": score["dimensions"],
                "suggestions": score["suggestions"],
                "length_profile": score.get("length_profile"),
                "citeability": citeability,
                "diversity_profile": dict(request.diversity_profile or {}),
                "ai_role": dict(request.ai_role or {}),
                "generation_length_context": dict(length_context),
                "review": review_metadata,
                "evaluation": evaluation_metadata,
            }
            return mark_succeeded(
                self.database_path,
                run.run_id,
                result,
                local_score=score["score"],
                local_analysis=analysis,
                generation_metadata={
                    "review": review_metadata,
                    "evaluation": evaluation_metadata,
                    "agent_pipeline": pipeline_audit,
                },
                additional_usage_records=(
                    *review.usage_records,
                    *evaluation.usage_records,
                ),
            )
        except Exception as exc:
            # The provider may already have accepted the request. Treat every
            # post-provider processing/persistence failure as unknown instead
            # of leaving the idempotency reservation permanently in started or
            # inviting an unsafe automatic retry.
            error = ContentEngineError(
                "内容已生成，但结果处理状态未知；请人工核对后再决定是否重试",
                trace_id=getattr(result, "trace_id", "") or request.request_id,
            )
            error.error_code = "generation_result_state_unknown"
            error.state_unknown = True
            self._attach_run(
                error,
                run,
                request,
                attempted_engine=getattr(result, "engine", "")
                or self.primary_adapter.name,
                fallback_from=getattr(result, "fallback_from", ""),
            )
            try:
                mark_unknown(self.database_path, run.run_id, error)
            except Exception:
                # If the database failure happened after a successful commit,
                # replaying this key will return the stored result. Otherwise
                # the started-row lease will age into unknown on the next call.
                pass
            raise error from exc

    def _score_result(
        self,
        result: ContentGenerationResult,
        request: ContentGenerationRequest,
        *,
        requested_length: int,
        effective_length: int,
        length_source: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        score = score_geo_content(
            title=result.title,
            content=result.content,
            brand=str(request.project_context.get("name") or "").strip(),
            keywords=request.keywords,
            target_length=effective_length,
            requested_target_length=requested_length,
            source=length_source,
        )
        citeability = score_ai_citeability(
            title=result.title,
            summary=result.summary,
            content=result.content,
            brand=str(request.project_context.get("name") or "").strip(),
            product=str(request.project_context.get("product") or "").strip(),
            website=str(request.project_context.get("website") or "").strip(),
            keywords=request.keywords,
            sources=result.sources,
            target_length=effective_length,
            requested_target_length=requested_length,
            source=length_source,
        )
        return score, citeability

    def _automatically_review(
        self,
        writer_result: ContentGenerationResult,
        writer_score: Mapping[str, Any],
        writer_citeability: Mapping[str, Any],
        request: ContentGenerationRequest,
        *,
        requested_length: int,
        effective_length: int,
        length_source: str,
    ) -> _AutomaticReviewOutcome:
        if not self.reviewer_enabled:
            return _AutomaticReviewOutcome(
                result=writer_result,
                score=writer_score,
                citeability=writer_citeability,
                metadata={
                    **self.reviewer_role,
                    "mode": "writer_only",
                    "invoked": False,
                    "completed": False,
                    "selected": "writer",
                    "writer_fallback_used": True,
                    "reason": "reviewer_not_explicitly_configured",
                    "message": (
                        "reviewer 未独立配置；未发起第二次模型调用，"
                        "保留 writer 初稿且不生成双模型质量对比。"
                    ),
                    "comparison": None,
                    "provider": None,
                    "model": None,
                    "provider_request_id": "",
                    "elapsed_ms": None,
                    "usage": None,
                    "usage_status": "not_invoked",
                    "candidate": None,
                    "diff": article_diff(
                        _public_article(writer_result),
                        _public_article(writer_result),
                    ),
                    "review_opinion": None,
                },
            )

        settings = self.reviewer_settings
        assert settings is not None
        configured_provider = provider_label_for_settings(settings)
        configured_model = settings.model
        review_started_at = time.perf_counter()
        try:
            raw_review = self.reviewer_optimizer(
                article=_public_article(writer_result),
                score={
                    **dict(writer_score),
                    "citeability": dict(writer_citeability),
                },
                settings=settings,
                include_metadata=True,
                target_length=effective_length,
            )
        except AIServiceError:
            return self._review_failure(
                writer_result,
                writer_score,
                writer_citeability,
                provider=configured_provider,
                model=configured_model,
                elapsed_ms=_elapsed_ms(review_started_at),
                reason="reviewer_call_failed",
                message="reviewer 调用失败，已保留 writer 初稿；未生成质量对比。",
            )
        except Exception:
            # An injected/custom reviewer can fail outside AIServiceError. The
            # writer result is still valid, so keep it and make the missing
            # comparison explicit instead of turning a successful first call
            # into an unknown generation state.
            return self._review_failure(
                writer_result,
                writer_score,
                writer_citeability,
                provider=configured_provider,
                model=configured_model,
                elapsed_ms=_elapsed_ms(review_started_at),
                reason="reviewer_call_failed",
                message="reviewer 调用失败，已保留 writer 初稿；未生成质量对比。",
            )

        if isinstance(raw_review, AIOptimizationResult):
            reviewed_article = raw_review.article
            provider = str(raw_review.provider or configured_provider).strip()
            model = str(raw_review.model or configured_model).strip()
            provider_request_id = str(raw_review.provider_request_id or "").strip()
            elapsed_ms = max(0, int(raw_review.elapsed_ms or 0))
            usage = (
                raw_review.usage
                if isinstance(raw_review.usage, Mapping) and raw_review.usage
                else None
            )
        elif isinstance(raw_review, Mapping):
            # Compatibility for locally injected optimizers. No usage metadata
            # means no token estimate is invented; the ledger says unknown.
            reviewed_article = raw_review
            provider = configured_provider
            model = configured_model
            provider_request_id = ""
            elapsed_ms = _elapsed_ms(review_started_at)
            usage = None
        else:
            return self._review_failure(
                writer_result,
                writer_score,
                writer_citeability,
                provider=configured_provider,
                model=configured_model,
                elapsed_ms=_elapsed_ms(review_started_at),
                reason="reviewer_invalid_result",
                message="reviewer 返回格式无效，已保留 writer 初稿；未生成质量对比。",
            )

        usage_record = _review_usage_record(
            provider=provider,
            model=model,
            usage=usage,
            outcome="succeeded",
        )
        try:
            reviewer_result = self._prepare_reviewer_result(
                writer_result,
                reviewed_article,
                effective_length=effective_length,
                website=request.project_context.get("website"),
                brand=request.project_context.get("name"),
            )
            reviewer_score, reviewer_citeability = self._score_result(
                reviewer_result,
                request,
                requested_length=requested_length,
                effective_length=effective_length,
                length_source=length_source,
            )
        except Exception:
            usage_record = {
                **usage_record,
                "metadata": {
                    "outcome": "invalid_result",
                    "fallback_used": True,
                    "usage_origin": (
                        "provider_response" if usage is not None else "not_reported"
                    ),
                },
            }
            return self._review_failure(
                writer_result,
                writer_score,
                writer_citeability,
                provider=provider,
                model=model,
                provider_request_id=provider_request_id,
                elapsed_ms=elapsed_ms,
                usage=usage,
                reason="reviewer_invalid_result",
                message="reviewer 结果未通过本地校验，已保留 writer 初稿；未生成质量对比。",
                usage_record=usage_record,
            )

        comparison = _compare_quality(
            writer_score,
            writer_citeability,
            reviewer_score,
            reviewer_citeability,
        )
        reviewer_selected = comparison["selected"] == "reviewer"
        final_result = reviewer_result if reviewer_selected else writer_result
        final_score = reviewer_score if reviewer_selected else writer_score
        final_citeability = (
            reviewer_citeability if reviewer_selected else writer_citeability
        )
        return _AutomaticReviewOutcome(
            result=final_result,
            score=final_score,
            citeability=final_citeability,
            metadata={
                **self.reviewer_role,
                "mode": "automatic",
                "invoked": True,
                "completed": True,
                "selected": comparison["selected"],
                "writer_fallback_used": False,
                "reason": comparison["reason"],
                "message": (
                    "reviewer 已完成优化；最终稿由本地确定性质量比较选择。"
                ),
                "comparison": comparison,
                "provider": provider,
                "model": model,
                "provider_request_id": provider_request_id,
                "elapsed_ms": elapsed_ms,
                "usage": dict(usage) if usage is not None else None,
                "usage_status": "reported" if usage is not None else "unknown",
                "candidate": article_snapshot(_public_article(reviewer_result)),
                "diff": article_diff(
                    _public_article(writer_result),
                    _public_article(reviewer_result),
                ),
                "review_opinion": (
                    dict(reviewed_article.get("review_notes") or {})
                    if isinstance(reviewed_article.get("review_notes"), Mapping)
                    else None
                ),
            },
            usage_records=(usage_record,),
        )

    def _automatically_evaluate(
        self,
        result: ContentGenerationResult,
        score: Mapping[str, Any],
        citeability: Mapping[str, Any],
    ) -> _AutomaticEvaluationOutcome:
        if not self.evaluator_enabled:
            non_independent = bool(
                self.evaluator_role.get("configured")
                and not self.evaluator_role.get("independent")
            )
            return _AutomaticEvaluationOutcome(
                metadata={
                    **self.evaluator_role,
                    "mode": "non_independent" if non_independent else "not_invoked",
                    "invoked": False,
                    "completed": False,
                    "provider": None,
                    "model": None,
                    "provider_request_id": "",
                    "elapsed_ms": None,
                    "usage": None,
                    "usage_status": "not_invoked",
                    "opinion": None,
                    "message": (
                        (
                            "evaluator 与 writer 或 reviewer 使用同一 provider/model；"
                            "未发起付费互审调用，也不计为独立模型。"
                        )
                        if non_independent
                        else (
                            "evaluator 未独立配置；未发起第三次模型调用，"
                            "本地规则分不是独立 AI 事实核验。"
                        )
                    ),
                    "fact_check_scope": "not_invoked",
                    "external_verification": False,
                }
            )

        settings = self.evaluator_settings
        assert settings is not None
        provider = provider_label_for_settings(settings)
        model = settings.model
        started_at = time.perf_counter()
        try:
            evaluated = self.evaluator(
                article=_public_article(result),
                score={
                    **dict(score),
                    "citeability": dict(citeability),
                },
                settings=settings,
            )
        except Exception:
            return _AutomaticEvaluationOutcome(
                metadata={
                    **self.evaluator_role,
                    "mode": "independent_opinion",
                    "invoked": True,
                    "completed": False,
                    "provider": provider,
                    "model": model,
                    "provider_request_id": "",
                    "elapsed_ms": _elapsed_ms(started_at),
                    "usage": None,
                    "usage_status": "unknown",
                    "opinion": None,
                    "message": (
                        "evaluator 调用或响应校验失败；文章仍保留，"
                        "但不能声称已经完成独立事实/合规评估。"
                    ),
                    "fact_check_scope": "not_completed",
                    "external_verification": False,
                },
                usage_records=(
                    _evaluation_usage_record(
                        provider=provider,
                        model=model,
                        usage=None,
                        outcome="failed",
                    ),
                ),
            )

        if isinstance(evaluated, AIEvaluationResult):
            opinion = dict(evaluated.evaluation)
            provider = str(evaluated.provider or provider).strip()
            model = str(evaluated.model or model).strip()
            provider_request_id = str(evaluated.provider_request_id or "").strip()
            elapsed_ms = max(0, int(evaluated.elapsed_ms or 0))
            usage = (
                dict(evaluated.usage)
                if isinstance(evaluated.usage, Mapping) and evaluated.usage
                else None
            )
        elif isinstance(evaluated, Mapping):
            opinion = dict(evaluated)
            provider_request_id = ""
            elapsed_ms = _elapsed_ms(started_at)
            usage = None
        else:
            opinion = {}
            provider_request_id = ""
            elapsed_ms = _elapsed_ms(started_at)
            usage = None

        if not opinion:
            return _AutomaticEvaluationOutcome(
                metadata={
                    **self.evaluator_role,
                    "mode": "independent_opinion",
                    "invoked": True,
                    "completed": False,
                    "provider": provider,
                    "model": model,
                    "provider_request_id": provider_request_id,
                    "elapsed_ms": elapsed_ms,
                    "usage": usage,
                    "usage_status": "reported" if usage is not None else "unknown",
                    "opinion": None,
                    "message": "evaluator 未返回可用意见；不能声称评估通过。",
                    "fact_check_scope": "not_completed",
                    "external_verification": False,
                },
                usage_records=(
                    _evaluation_usage_record(
                        provider=provider,
                        model=model,
                        usage=usage,
                        outcome="invalid_result",
                    ),
                ),
            )

        return _AutomaticEvaluationOutcome(
            metadata={
                **self.evaluator_role,
                "mode": "independent_opinion",
                "invoked": True,
                "completed": True,
                "provider": provider,
                "model": model,
                "provider_request_id": provider_request_id,
                "elapsed_ms": elapsed_ms,
                "usage": usage,
                "usage_status": "reported" if usage is not None else "unknown",
                "opinion": opinion,
                "message": (
                    "evaluator 已给出独立意见；该模型未联网核验，"
                    "结论不是事实证明或平台合规保证。"
                ),
                "fact_check_scope": str(
                    opinion.get("fact_check_scope")
                    or "article_and_declared_sources_only_no_web"
                ),
                "external_verification": False,
            },
            usage_records=(
                _evaluation_usage_record(
                    provider=provider,
                    model=model,
                    usage=usage,
                    outcome="succeeded",
                ),
            ),
        )

    def _review_failure(
        self,
        writer_result: ContentGenerationResult,
        writer_score: Mapping[str, Any],
        writer_citeability: Mapping[str, Any],
        *,
        provider: str,
        model: str,
        elapsed_ms: int,
        reason: str,
        message: str,
        provider_request_id: str = "",
        usage: Mapping[str, Any] | None = None,
        usage_record: Mapping[str, Any] | None = None,
    ) -> _AutomaticReviewOutcome:
        record = usage_record or _review_usage_record(
            provider=provider,
            model=model,
            usage=usage,
            outcome="failed",
            fallback_used=True,
        )
        return _AutomaticReviewOutcome(
            result=replace(
                writer_result,
                warnings=tuple(dict.fromkeys([*writer_result.warnings, message])),
            ),
            score=writer_score,
            citeability=writer_citeability,
            metadata={
                **self.reviewer_role,
                "mode": "automatic",
                "invoked": True,
                "completed": False,
                "selected": "writer",
                "writer_fallback_used": True,
                "reason": reason,
                "message": message,
                "comparison": None,
                "provider": provider,
                "model": model,
                "provider_request_id": provider_request_id,
                "elapsed_ms": elapsed_ms,
                "usage": dict(usage) if usage is not None else None,
                "usage_status": "reported" if usage is not None else "unknown",
                "candidate": None,
                "diff": article_diff(
                    _public_article(writer_result),
                    _public_article(writer_result),
                ),
                "review_opinion": None,
            },
            usage_records=(record,),
        )

    @staticmethod
    def _prepare_reviewer_result(
        writer_result: ContentGenerationResult,
        reviewed_article: Mapping[str, Any],
        *,
        effective_length: int,
        website: object = "",
        brand: object = "",
    ) -> ContentGenerationResult:
        candidate_payload = dict(reviewed_article or {})
        if not str(candidate_payload.get("summary") or "").strip():
            candidate_payload["summary"] = writer_result.summary
        if not candidate_payload.get("tags"):
            candidate_payload["tags"] = list(writer_result.tags)
        if not candidate_payload.get("faq"):
            candidate_payload["faq"] = list(writer_result.faq)
        # Reviewing prose does not create a new evidence receipt. Preserve the
        # exact writer sources so a model cannot silently invent provenance.
        candidate_payload["sources"] = list(writer_result.sources)
        normalized = ContentGenerationResult.from_payload(
            candidate_payload,
            default_engine=writer_result.engine,
            default_engine_version=writer_result.engine_version,
            request_id=writer_result.request_id,
            elapsed_ms=writer_result.elapsed_ms,
            trace_id=writer_result.trace_id,
        )

        warnings = [
            warning
            for warning in writer_result.warnings
            if not any(
                str(warning).startswith(prefix)
                for prefix in _LOCAL_CONTENT_WARNING_PREFIXES
            )
        ]
        warnings.extend(normalized.warnings)
        hygiene = sanitize_generated_content(normalized.content)
        if hygiene.changed:
            warnings.append("reviewer 优化稿中的未完成占位标记已在本地移除")
        owned_reference = ensure_owned_website_reference(
            hygiene.content,
            website=website,
            brand=brand,
        )
        if owned_reference.appended:
            warnings.append(
                "reviewer 优化稿已恢复项目 HTTPS 自有官网引用；该来源不代表第三方背书"
            )
        length_guard = constrain_generated_content_length(
            owned_reference.content,
            effective_length,
        )
        if length_guard.changed:
            warnings.append(
                "reviewer 优化稿超过目标字数，已在本地按完整语句自动压缩："
                f"{length_guard.original_length} → {length_guard.final_length} 字"
                f"（上限 {length_guard.limit} 字）"
            )
        length_profile = resolve_target_length_score_profile(effective_length)
        recommended_minimum = max(
            int(length_profile["minimum_content_length"]),
            (effective_length * 4 + 4) // 5,
        )
        final_visible_length = authoring_visible_length(length_guard.content)
        if final_visible_length < recommended_minimum:
            warnings.append(
                "reviewer 优化稿可见字数低于目标合理下界："
                f"{final_visible_length} / {effective_length} 字"
                f"（建议至少 {recommended_minimum} 字）；未自动补字"
            )
        return replace(
            writer_result,
            title=normalized.title,
            summary=normalized.summary,
            content=length_guard.content,
            tags=normalized.tags or writer_result.tags,
            faq=normalized.faq or writer_result.faq,
            sources=writer_result.sources,
            warnings=tuple(dict.fromkeys(warnings)),
        )

    def health(self) -> dict[str, Any]:
        primary = self.primary_adapter.health()
        fallback = self.fallback_adapter.health() if self.fallback_adapter is not None else None
        if primary.get("ready"):
            status = "ready"
            ready = True
        elif fallback is not None and fallback.get("ready"):
            status = "degraded"
            ready = True
        else:
            status = "unavailable"
            ready = False
        return {
            "status": status,
            "ready": ready,
            "primary": primary,
            "fallback": fallback,
        }

    def _record_error(self, run_id: int, error: ContentEngineError) -> None:
        if error.state_unknown:
            mark_unknown(self.database_path, run_id, error)
        else:
            mark_failed(self.database_path, run_id, error)

    @staticmethod
    def _attach_run(
        error: ContentEngineError,
        run: GenerationRunOutcome,
        request: ContentGenerationRequest,
        *,
        attempted_engine: str,
        fallback_from: str = "",
    ) -> None:
        error.run_id = run.run_id
        error.request_id = request.request_id
        error.attempted_engine = attempted_engine
        error.fallback_used = bool(fallback_from)
        error.fallback_from = fallback_from

    @staticmethod
    def _replay(run: GenerationRunOutcome) -> dict[str, Any]:
        if run.status == "succeeded" and run.result is not None:
            payload = dict(run.result)
            generation = dict(payload.get("generation") or {})
            generation["replayed"] = True
            payload["generation"] = generation
            return payload
        if run.status == "started":
            error = ContentGenerationInProgressError(
                "相同请求正在生成中，请等待原请求完成",
                trace_id=run.trace_id or run.request_id,
            )
        else:
            error = ReplayedContentGenerationError(
                run.error_message or "相同请求此前未成功",
                error_code=run.error_code,
                trace_id=run.trace_id or run.request_id,
                state_unknown=run.state_unknown,
                retry_after_seconds=run.retry_after_seconds,
            )
        error.run_id = run.run_id
        error.request_id = run.request_id
        raise error


def _adapter_provider_name(adapter: ContentEngineAdapter) -> str:
    """Use a vendor identity for audit fields while preserving route names."""

    return str(getattr(adapter, "provider_name", "") or adapter.name).strip()


_LOCAL_CONTENT_WARNING_PREFIXES = (
    "已移除未完成的作者、日期或证据占位标记",
    "模型正文超过目标字数",
    "模型正文可见字数低于目标合理下界",
)


def _normalize_reviewer_role(
    value: Mapping[str, Any] | None,
    *,
    configured: bool,
) -> dict[str, Any]:
    role = dict(value or {})
    explicitly_configured = bool(
        configured
        and (not role or bool(role.get("configured")))
        and not bool(role.get("fallback"))
    )
    return {
        "role": "reviewer",
        "configured": explicitly_configured,
        "effective_configured": bool(
            role.get("effective_configured", configured)
        ),
        "fallback": not explicitly_configured,
        "source_role": (
            str(role.get("source_role") or "reviewer")
            if explicitly_configured
            else "writer"
        ),
        "same_as_writer": (
            bool(role.get("same_as_writer", False))
            if explicitly_configured
            else True
        ),
    }


def _normalize_evaluator_role(
    value: Mapping[str, Any] | None,
    *,
    configured: bool,
) -> dict[str, Any]:
    role = dict(value or {})
    explicitly_configured = bool(
        (configured if not role else bool(role.get("configured")))
        and not bool(role.get("fallback"))
    )
    independent = bool(
        configured
        and explicitly_configured
        and not bool(role.get("same_as_writer"))
        and not bool(role.get("same_as_reviewer"))
    )
    return {
        "role": "evaluator",
        "configured": explicitly_configured,
        "effective_configured": bool(role.get("effective_configured", configured)),
        "fallback": not explicitly_configured,
        "independent": independent,
        "source_role": (
            str(role.get("source_role") or "evaluator")
            if explicitly_configured
            else "writer"
        ),
        "same_as_writer": (
            bool(role.get("same_as_writer", False))
            if explicitly_configured
            else True
        ),
        "same_as_reviewer": bool(role.get("same_as_reviewer", False)),
    }


def _public_article(result: ContentGenerationResult) -> dict[str, Any]:
    return {
        "title": result.title,
        "summary": result.summary,
        "content": result.content,
        "tags": list(result.tags),
        "faq": list(result.faq),
        "sources": list(result.sources),
    }


def _elapsed_ms(started_at: float) -> int:
    return max(0, round((time.perf_counter() - started_at) * 1000))


def _review_usage_record(
    *,
    provider: str,
    model: str,
    usage: Mapping[str, Any] | None,
    outcome: str,
    fallback_used: bool = False,
) -> dict[str, Any]:
    reported_usage = isinstance(usage, Mapping) and bool(usage)
    return {
        "operation": "optimize",
        "provider": str(provider or "unknown"),
        "model": str(model or "unknown"),
        "usage": dict(usage) if reported_usage else None,
        "usage_status": "reported" if reported_usage else "unknown",
        "metadata": {
            "outcome": outcome,
            "fallback_used": fallback_used,
            "usage_origin": (
                "provider_response" if reported_usage else "not_reported"
            ),
        },
    }


def _evaluation_usage_record(
    *,
    provider: str,
    model: str,
    usage: Mapping[str, Any] | None,
    outcome: str,
) -> dict[str, Any]:
    reported_usage = isinstance(usage, Mapping) and bool(usage)
    return {
        "operation": "evaluate",
        "provider": str(provider or "unknown"),
        "model": str(model or "unknown"),
        "usage": dict(usage) if reported_usage else None,
        "usage_status": "reported" if reported_usage else "unknown",
        "metadata": {
            "role": "evaluator",
            "outcome": outcome,
            "usage_origin": (
                "provider_response" if reported_usage else "not_reported"
            ),
            "external_verification": False,
        },
    }


def _quality_snapshot(
    score: Mapping[str, Any],
    citeability: Mapping[str, Any],
) -> dict[str, Any]:
    geo_score = _bounded_score(score.get("score"))
    citeability_score = _bounded_score(citeability.get("score"))
    return {
        "publish_gate_passed": bool(citeability.get("publish_gate_passed")),
        "geo_score": geo_score,
        "citeability_score": citeability_score,
        "combined_score": round((geo_score + citeability_score) / 2, 1),
    }


def _compare_quality(
    writer_score: Mapping[str, Any],
    writer_citeability: Mapping[str, Any],
    reviewer_score: Mapping[str, Any],
    reviewer_citeability: Mapping[str, Any],
) -> dict[str, Any]:
    writer = _quality_snapshot(writer_score, writer_citeability)
    reviewer = _quality_snapshot(reviewer_score, reviewer_citeability)
    writer_rank = (
        int(writer["publish_gate_passed"]),
        writer["combined_score"],
        writer["citeability_score"],
        writer["geo_score"],
    )
    reviewer_rank = (
        int(reviewer["publish_gate_passed"]),
        reviewer["combined_score"],
        reviewer["citeability_score"],
        reviewer["geo_score"],
    )
    selected = "reviewer" if reviewer_rank > writer_rank else "writer"
    return {
        "method": "deterministic_local_quality_v1",
        "writer": writer,
        "reviewer": reviewer,
        "delta": {
            "geo_score": reviewer["geo_score"] - writer["geo_score"],
            "citeability_score": (
                reviewer["citeability_score"] - writer["citeability_score"]
            ),
            "combined_score": round(
                reviewer["combined_score"] - writer["combined_score"],
                1,
            ),
        },
        "selected": selected,
        "reason": (
            "reviewer_higher_local_quality"
            if selected == "reviewer"
            else "reviewer_not_strictly_better"
        ),
    }


def _bounded_score(value: Any) -> int:
    try:
        score = int(value)
    except (TypeError, ValueError):
        score = 0
    return max(0, min(100, score))
