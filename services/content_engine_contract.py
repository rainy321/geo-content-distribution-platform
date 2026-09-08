from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field, replace
from typing import Any, Mapping, Sequence


CONTENT_ENGINE_CONTRACT_VERSION = "1.0"


class ContentEngineError(RuntimeError):
    """A sanitized content-engine failure that is safe to expose to the UI."""

    error_code = "engine_error"
    safe_to_fallback = False
    state_unknown = False

    def __init__(
        self,
        message: str,
        *,
        trace_id: str = "",
        retry_after_seconds: int | None = None,
    ) -> None:
        super().__init__(message)
        self.trace_id = str(trace_id or "").strip()
        self.retry_after_seconds = retry_after_seconds
        self.run_id: int | None = None
        self.request_id = ""
        self.attempted_engine = ""
        self.fallback_used = False
        self.fallback_from = ""


class ContentEngineConfigurationError(ContentEngineError):
    error_code = "engine_not_configured"


class ContentEngineUnavailableError(ContentEngineError):
    error_code = "engine_unavailable"
    safe_to_fallback = True


class ContentEngineTimeoutError(ContentEngineError):
    error_code = "engine_timeout_unknown"
    state_unknown = True


class ContentEngineRateLimitError(ContentEngineError):
    error_code = "engine_rate_limited"


class ContentEngineProtocolError(ContentEngineError):
    error_code = "engine_protocol_error"


@dataclass(frozen=True)
class ContentGenerationRequest:
    project_context: Mapping[str, Any]
    topic: str
    keywords: tuple[str, ...]
    length: int
    content_type: str
    target_platform: str = ""
    template_instruction: str = ""
    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    idempotency_key: str = ""

    def __post_init__(self) -> None:
        project = dict(self.project_context or {})
        if not str(project.get("name") or "").strip():
            raise ValueError("品牌项目缺少名称，不能生成内容")
        if not self.topic.strip():
            raise ValueError("文章主题不能为空")
        if self.length not in {600, 1000, 1500}:
            raise ValueError("文章长度仅支持 600、1000、1500")
        if not self.content_type.strip():
            raise ValueError("内容类型不能为空")
        if not self.request_id.strip():
            raise ValueError("request_id 不能为空")

    @classmethod
    def create(
        cls,
        *,
        project_context: Mapping[str, Any],
        topic: str,
        keywords: Sequence[str],
        length: int,
        content_type: str,
        target_platform: str = "",
        template_instruction: str = "",
        request_id: str = "",
        idempotency_key: str = "",
    ) -> "ContentGenerationRequest":
        return cls(
            project_context=dict(project_context),
            topic=str(topic or "").strip(),
            keywords=tuple(_normalize_strings(keywords)),
            length=int(length),
            content_type=str(content_type or "").strip(),
            target_platform=str(target_platform or "").strip(),
            template_instruction=str(template_instruction or "").strip(),
            request_id=str(request_id or uuid.uuid4()).strip(),
            idempotency_key=str(idempotency_key or "").strip(),
        )

    def provider_payload(self) -> dict[str, Any]:
        return {
            "contract_version": CONTENT_ENGINE_CONTRACT_VERSION,
            "request_id": self.request_id,
            "project_context": dict(self.project_context),
            "brief": {
                "topic": self.topic,
                "keywords": list(self.keywords),
                "length": self.length,
                "content_type": self.content_type,
                "target_platform": self.target_platform,
                "template_instruction": self.template_instruction,
            },
        }

    def fingerprint(self) -> str:
        # request_id is transport metadata.  Including it would make every retry
        # look like a different logical generation and would defeat the
        # Idempotency-Key contract.
        payload = self.provider_payload()
        payload.pop("request_id", None)
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ContentGenerationResult:
    title: str
    summary: str
    content: str
    tags: tuple[str, ...]
    faq: tuple[dict[str, str], ...]
    sources: tuple[dict[str, str], ...]
    engine: str
    engine_version: str
    request_id: str
    trace_id: str
    elapsed_ms: int
    usage: Mapping[str, int] | None = None
    provider_geo_score: float | None = None
    warnings: tuple[str, ...] = ()
    fallback_used: bool = False
    fallback_from: str = ""

    @classmethod
    def from_payload(
        cls,
        payload: Mapping[str, Any],
        *,
        default_engine: str,
        default_engine_version: str,
        request_id: str,
        elapsed_ms: int,
        trace_id: str = "",
    ) -> "ContentGenerationResult":
        if not isinstance(payload, Mapping):
            raise ContentEngineProtocolError(
                "内容引擎返回格式无效",
                trace_id=trace_id,
            )
        contract_version = payload.get("contract_version")
        if (
            contract_version is not None
            and str(contract_version).strip() != CONTENT_ENGINE_CONTRACT_VERSION
        ):
            raise ContentEngineProtocolError(
                "内容引擎协议版本不兼容",
                trace_id=trace_id,
            )
        data = payload.get("data") if isinstance(payload.get("data"), Mapping) else payload
        title = str(data.get("title") or "").strip()
        content = str(data.get("content") or "").strip()
        if not title or not content:
            missing = "标题和正文" if not title and not content else ("标题" if not title else "正文")
            raise ContentEngineProtocolError(
                f"内容引擎响应缺少{missing}",
                trace_id=str(data.get("trace_id") or trace_id),
            )

        warnings = _normalize_strings(data.get("warnings") or [])
        provider_request_id = str(data.get("request_id") or "").strip()
        if provider_request_id and provider_request_id != request_id:
            warnings.append("引擎返回的 request_id 不一致，已保留本地请求标识")
        summary = str(data.get("summary") or "").strip()
        if not summary:
            warnings.append("引擎未返回摘要，可在编辑器中补充")
        faq = _normalize_faq(data.get("faq"))
        if not faq:
            warnings.append("引擎未返回 FAQ")
        sources = _normalize_sources(data.get("sources"))
        if not sources:
            warnings.append("引擎未返回来源信息")

        usage = _normalize_usage(data.get("usage"))
        if usage is None:
            warnings.append("引擎未返回 Token 用量，成本暂不可计量")
        provider_geo_score = _normalize_score(data.get("provider_geo_score", data.get("geo_score")))
        if data.get("provider_geo_score", data.get("geo_score")) is not None and provider_geo_score is None:
            warnings.append("引擎诊断分格式无效，已忽略")

        return cls(
            title=title,
            summary=summary,
            content=content,
            tags=tuple(_normalize_strings(data.get("tags") or [])),
            faq=tuple(faq),
            sources=tuple(sources),
            engine=str(data.get("engine") or default_engine).strip() or default_engine,
            engine_version=str(data.get("engine_version") or default_engine_version).strip() or "unknown",
            request_id=request_id,
            trace_id=str(data.get("trace_id") or trace_id or request_id).strip(),
            elapsed_ms=max(0, int(data.get("elapsed_ms") or elapsed_ms)),
            usage=usage,
            provider_geo_score=provider_geo_score,
            warnings=tuple(dict.fromkeys(warnings)),
        )

    def with_fallback(self, primary_engine: str, warning: str) -> "ContentGenerationResult":
        return replace(
            self,
            fallback_used=True,
            fallback_from=str(primary_engine or "").strip(),
            warnings=tuple(dict.fromkeys([*self.warnings, warning])),
        )

    def public_payload(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "summary": self.summary,
            "content": self.content,
            "tags": list(self.tags),
            "faq": list(self.faq),
            "sources": list(self.sources),
            "generation": {
                "contract_version": CONTENT_ENGINE_CONTRACT_VERSION,
                "engine": self.engine,
                "engine_version": self.engine_version,
                "request_id": self.request_id,
                "trace_id": self.trace_id,
                "elapsed_ms": self.elapsed_ms,
                "usage": dict(self.usage) if self.usage is not None else None,
                "usage_status": "reported" if self.usage is not None else "unknown",
                "provider_geo_score": self.provider_geo_score,
                "fallback_used": self.fallback_used,
                "fallback_from": self.fallback_from,
                "warnings": list(self.warnings),
            },
        }


def _normalize_strings(value: Sequence[Any] | Any) -> list[str]:
    if isinstance(value, str) or not isinstance(value, Sequence):
        value = [] if value in (None, "") else [value]
    result: list[str] = []
    for item in value:
        text = str(item or "").strip().lstrip("#").strip()
        if text and text not in result:
            result.append(text)
    return result


def _normalize_faq(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    result: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        question = str(item.get("question") or item.get("q") or "").strip()
        answer = str(item.get("answer") or item.get("a") or "").strip()
        if question and answer:
            result.append({"question": question, "answer": answer})
    return result


def _normalize_sources(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    result: list[dict[str, str]] = []
    for item in value:
        if isinstance(item, str):
            url = item.strip()
            if url:
                result.append({"title": "", "url": url})
            continue
        if not isinstance(item, Mapping):
            continue
        url = str(item.get("url") or "").strip()
        title = str(item.get("title") or item.get("name") or "").strip()
        if url or title:
            result.append({"title": title, "url": url})
    return result


def _normalize_usage(value: Any) -> dict[str, int] | None:
    if not isinstance(value, Mapping):
        return None
    aliases = {
        "input_tokens": ("input_tokens", "prompt_tokens"),
        "output_tokens": ("output_tokens", "completion_tokens"),
        "total_tokens": ("total_tokens",),
        "cached_tokens": ("cached_tokens", "cache_tokens"),
        "reasoning_tokens": ("reasoning_tokens",),
    }
    result: dict[str, int] = {}
    for target, names in aliases.items():
        raw = next((value.get(name) for name in names if value.get(name) is not None), None)
        if raw is None:
            continue
        try:
            number = int(raw)
        except (TypeError, ValueError):
            continue
        if number >= 0:
            result[target] = number
    return result or None


def _normalize_score(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        score = float(value)
    except (TypeError, ValueError):
        return None
    if not 0 <= score <= 100:
        return None
    return round(score, 2)
