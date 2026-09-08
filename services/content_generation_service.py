from __future__ import annotations

import os
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

from services.ai_service import AISettings
from services.content_engine_adapters import (
    ColleagueContentEngineAdapter,
    ContentEngineAdapter,
    QwenContentEngineAdapter,
)
from services.content_engine_contract import (
    ContentEngineConfigurationError,
    ContentEngineError,
    ContentGenerationRequest,
)
from services.content_generation_run_service import (
    GenerationRunOutcome,
    begin_or_replay,
    mark_failed,
    mark_succeeded,
    mark_unknown,
)
from services.geo_score_service import score_geo_content


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


class ContentGenerationService:
    """Route one request through a configured engine and persist its audit trail."""

    def __init__(
        self,
        database_path: str | Path,
        primary_adapter: ContentEngineAdapter,
        fallback_adapter: ContentEngineAdapter | None = None,
    ) -> None:
        self.database_path = Path(database_path)
        self.primary_adapter = primary_adapter
        self.fallback_adapter = (
            fallback_adapter
            if fallback_adapter is not None
            and fallback_adapter.name != primary_adapter.name
            else None
        )

    @classmethod
    def from_environment(
        cls,
        database_path: str | Path,
        *,
        qwen_settings: AISettings | None = None,
        qwen_http_post=None,
        colleague_http_post=None,
        colleague_http_get=None,
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
        return cls(database_path, primary, build(fallback_name))

    def generate(self, request: ContentGenerationRequest) -> dict[str, Any]:
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
                attempted_engine=self.primary_adapter.name,
            )
            if primary_error.safe_to_fallback and self.fallback_adapter is not None:
                try:
                    result = self.fallback_adapter.generate(request).with_fallback(
                        self.primary_adapter.name,
                        f"主引擎连接失败，已回退到 {self.fallback_adapter.name}",
                    )
                except ContentEngineError as fallback_error:
                    self._attach_run(
                        fallback_error,
                        run,
                        request,
                        attempted_engine=self.fallback_adapter.name,
                        fallback_from=self.primary_adapter.name,
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
                attempted_engine=self.primary_adapter.name,
            )
            mark_failed(self.database_path, run.run_id, error)
            raise error from exc

        try:
            result = replace(
                result,
                elapsed_ms=round((time.perf_counter() - started_at) * 1000),
            )
            score = score_geo_content(
                title=result.title,
                content=result.content,
                brand=str(request.project_context.get("name") or "").strip(),
                keywords=request.keywords,
            )
            analysis = {
                "dimensions": score["dimensions"],
                "suggestions": score["suggestions"],
            }
            return mark_succeeded(
                self.database_path,
                run.run_id,
                result,
                local_score=score["score"],
                local_analysis=analysis,
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
