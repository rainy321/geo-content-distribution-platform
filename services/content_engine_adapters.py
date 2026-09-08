from __future__ import annotations

import json
import os
import time
from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any, Protocol
from urllib.parse import urlsplit

import requests

from services.ai_service import (
    AIConfigurationError,
    AIConnectError,
    AIProtocolError,
    AIProviderRateLimitError,
    AIReadTimeoutError,
    AIServiceError,
    AISettings,
    generate_geo_content_detailed,
    is_definite_pre_send_connection_error,
)
from services.content_engine_contract import (
    CONTENT_ENGINE_CONTRACT_VERSION,
    ContentEngineConfigurationError,
    ContentEngineError,
    ContentEngineProtocolError,
    ContentEngineRateLimitError,
    ContentEngineTimeoutError,
    ContentEngineUnavailableError,
    ContentGenerationRequest,
    ContentGenerationResult,
)


DEFAULT_COLLEAGUE_CONNECT_TIMEOUT_SECONDS = 3.0
DEFAULT_COLLEAGUE_READ_TIMEOUT_SECONDS = 120.0
DEFAULT_COLLEAGUE_MAX_RESPONSE_BYTES = 2 * 1024 * 1024


class ContentEngineAdapter(Protocol):
    name: str
    version: str

    def generate(self, request: ContentGenerationRequest) -> ContentGenerationResult:
        ...

    def health(self) -> dict[str, Any]:
        ...


class QwenContentEngineAdapter:
    name = "qwen"

    def __init__(self, *, settings: AISettings | None = None, http_post=None) -> None:
        self.settings = settings
        self.http_post = http_post
        self.version = settings.model if settings is not None else (os.getenv("AI_MODEL", "").strip() or "unknown")

    def generate(self, request: ContentGenerationRequest) -> ContentGenerationResult:
        try:
            generated = generate_geo_content_detailed(
                project=request.project_context,
                topic=request.topic,
                keywords=request.keywords,
                length=request.length,
                content_type=request.content_type,
                target_platform=request.target_platform,
                template_instruction=request.template_instruction,
                settings=self.settings,
                http_post=self.http_post,
                strict=True,
            )
        except AIConfigurationError as exc:
            raise ContentEngineConfigurationError(str(exc), trace_id=request.request_id) from exc
        except AIConnectError as exc:
            raise ContentEngineUnavailableError(str(exc), trace_id=request.request_id) from exc
        except AIReadTimeoutError as exc:
            raise ContentEngineTimeoutError(str(exc), trace_id=request.request_id) from exc
        except AIProviderRateLimitError as exc:
            raise ContentEngineRateLimitError(
                str(exc),
                trace_id=request.request_id,
                retry_after_seconds=exc.retry_after_seconds,
            ) from exc
        except AIProtocolError as exc:
            raise ContentEngineProtocolError(str(exc), trace_id=request.request_id) from exc
        except AIServiceError as exc:
            raise ContentEngineError(str(exc), trace_id=request.request_id) from exc

        payload = {
            **dict(generated.article),
            "engine": self.name,
            "engine_version": generated.model,
            "request_id": request.request_id,
            "trace_id": generated.provider_request_id or request.request_id,
            "elapsed_ms": generated.elapsed_ms,
            "usage": dict(generated.usage) if generated.usage is not None else None,
        }
        return ContentGenerationResult.from_payload(
            payload,
            default_engine=self.name,
            default_engine_version=generated.model,
            request_id=request.request_id,
            trace_id=generated.provider_request_id,
            elapsed_ms=generated.elapsed_ms,
        )

    def health(self) -> dict[str, Any]:
        configured = self.settings is not None or AISettings.environment_is_configured()
        model = self.settings.model if self.settings is not None else os.getenv("AI_MODEL", "").strip()
        return {
            "engine": self.name,
            "version": model or "unknown",
            "ready": configured,
            "status": "ready" if configured else "not_configured",
        }


class ColleagueContentEngineAdapter:
    name = "colleague"

    def __init__(
        self,
        *,
        base_url: str,
        token: str = "",
        connect_timeout_seconds: float = DEFAULT_COLLEAGUE_CONNECT_TIMEOUT_SECONDS,
        read_timeout_seconds: float = DEFAULT_COLLEAGUE_READ_TIMEOUT_SECONDS,
        max_response_bytes: int = DEFAULT_COLLEAGUE_MAX_RESPONSE_BYTES,
        http_post=None,
        http_get=None,
        allowed_hosts: Sequence[str] | str | None = None,
    ) -> None:
        self.allowed_hosts = _normalize_allowed_hosts(allowed_hosts)
        self.base_url = _validate_internal_base_url(base_url, self.allowed_hosts)
        self.token = str(token or "").strip()
        if not self.token:
            raise ContentEngineConfigurationError(
                "缺少内容引擎配置: COLLEAGUE_CONTENT_ENGINE_TOKEN"
            )
        if len(self.token) > 8192 or any(marker in self.token for marker in ("\r", "\n")):
            raise ContentEngineConfigurationError("同事内容引擎 Token 格式无效")
        self.connect_timeout_seconds = _positive_float(
            connect_timeout_seconds,
            "COLLEAGUE_CONNECT_TIMEOUT_SECONDS",
        )
        self.read_timeout_seconds = _positive_float(
            read_timeout_seconds,
            "COLLEAGUE_READ_TIMEOUT_SECONDS",
        )
        self.max_response_bytes = _positive_int(
            max_response_bytes,
            "COLLEAGUE_MAX_RESPONSE_BYTES",
        )
        self.http_post = http_post or requests.post
        self.http_get = http_get or requests.get
        self.version = "unknown"

    @classmethod
    def from_environment(cls, *, http_post=None, http_get=None) -> "ColleagueContentEngineAdapter":
        base_url = str(os.getenv("COLLEAGUE_CONTENT_ENGINE_URL", "")).strip()
        if not base_url:
            raise ContentEngineConfigurationError("缺少内容引擎配置: COLLEAGUE_CONTENT_ENGINE_URL")
        return cls(
            base_url=base_url,
            token=os.getenv("COLLEAGUE_CONTENT_ENGINE_TOKEN", ""),
            connect_timeout_seconds=os.getenv(
                "COLLEAGUE_CONNECT_TIMEOUT_SECONDS",
                str(DEFAULT_COLLEAGUE_CONNECT_TIMEOUT_SECONDS),
            ),
            read_timeout_seconds=os.getenv(
                "COLLEAGUE_READ_TIMEOUT_SECONDS",
                str(DEFAULT_COLLEAGUE_READ_TIMEOUT_SECONDS),
            ),
            max_response_bytes=os.getenv(
                "COLLEAGUE_MAX_RESPONSE_BYTES",
                str(DEFAULT_COLLEAGUE_MAX_RESPONSE_BYTES),
            ),
            allowed_hosts=os.getenv(
                "COLLEAGUE_CONTENT_ENGINE_ALLOWED_HOSTS",
                "content-engine",
            ),
            http_post=http_post,
            http_get=http_get,
        )

    def generate(self, request: ContentGenerationRequest) -> ContentGenerationResult:
        started_at = time.perf_counter()
        response = None
        try:
            response = self.http_post(
                _endpoint(self.base_url, "/api/v1/generate"),
                headers=self._headers(request),
                json=request.provider_payload(),
                timeout=(self.connect_timeout_seconds, self.read_timeout_seconds),
                allow_redirects=False,
                stream=True,
            )
        except requests.ConnectTimeout as exc:
            raise ContentEngineUnavailableError(
                "无法连接同事内容引擎",
                trace_id=request.request_id,
            ) from exc
        except requests.ReadTimeout as exc:
            raise ContentEngineTimeoutError(
                "同事内容引擎读取超时，请求结果未知，请勿自动重试",
                trace_id=request.request_id,
            ) from exc
        except requests.Timeout as exc:
            raise ContentEngineTimeoutError(
                "同事内容引擎请求超时，请求结果未知，请勿自动重试",
                trace_id=request.request_id,
            ) from exc
        except requests.ConnectionError as exc:
            if is_definite_pre_send_connection_error(exc):
                raise ContentEngineUnavailableError(
                    "无法连接同事内容引擎",
                    trace_id=request.request_id,
                ) from exc
            raise ContentEngineTimeoutError(
                "同事内容引擎连接中断，请求结果未知，请勿自动重试",
                trace_id=request.request_id,
            ) from exc
        except requests.RequestException as exc:
            raise ContentEngineError(
                "同事内容引擎请求失败",
                trace_id=request.request_id,
            ) from exc

        elapsed_ms = round((time.perf_counter() - started_at) * 1000)
        trace_id = _response_trace_id(response) or request.request_id
        try:
            if 300 <= response.status_code < 400:
                raise ContentEngineProtocolError(
                    "同事内容引擎地址发生重定向，已停止请求",
                    trace_id=trace_id,
                )
            if response.status_code == 429:
                raise ContentEngineRateLimitError(
                    "同事内容引擎已达到限流，请稍后再试",
                    trace_id=trace_id,
                    retry_after_seconds=_retry_after(response),
                )
            if response.status_code >= 400:
                raise ContentEngineError(
                    f"同事内容引擎返回 HTTP {response.status_code}",
                    trace_id=trace_id,
                )
            payload = _read_bounded_json(response, self.max_response_bytes, trace_id=trace_id)
            if str(payload.get("contract_version") or "").strip() != CONTENT_ENGINE_CONTRACT_VERSION:
                raise ContentEngineProtocolError(
                    "同事内容引擎协议版本缺失或不兼容",
                    trace_id=trace_id,
                )
            result = ContentGenerationResult.from_payload(
                payload,
                default_engine=self.name,
                default_engine_version=self.version,
                request_id=request.request_id,
                trace_id=trace_id,
                elapsed_ms=elapsed_ms,
            )
            result = replace(result, engine=self.name, request_id=request.request_id)
            self.version = result.engine_version
            return result
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()

    def health(self) -> dict[str, Any]:
        response = None
        try:
            response = self.http_get(
                _endpoint(self.base_url, "/health/ready"),
                headers=self._base_headers(),
                timeout=(self.connect_timeout_seconds, min(self.read_timeout_seconds, 5.0)),
                allow_redirects=False,
                stream=True,
            )
        except requests.RequestException:
            return {
                "engine": self.name,
                "version": self.version,
                "ready": False,
                "status": "unavailable",
            }
        try:
            ready = response.status_code == 200
            version = self.version
            if ready:
                try:
                    payload = _read_bounded_json(
                        response,
                        self.max_response_bytes,
                        trace_id=_response_trace_id(response),
                    )
                    data = payload.get("data") if isinstance(payload.get("data"), Mapping) else payload
                    version = str(data.get("version") or data.get("engine_version") or version).strip()
                except ContentEngineError:
                    ready = False
            self.version = version or "unknown"
            return {
                "engine": self.name,
                "version": self.version,
                "ready": ready,
                "status": "ready" if ready else "unavailable",
            }
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()

    def _base_headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _headers(self, request: ContentGenerationRequest) -> dict[str, str]:
        return {
            **self._base_headers(),
            "Content-Type": "application/json",
            "X-Request-ID": request.request_id,
            "Idempotency-Key": request.idempotency_key or request.fingerprint(),
        }


class MockContentEngineAdapter:
    """Deterministic in-process adapter for contract and orchestration tests."""

    name = "mock"

    def __init__(
        self,
        responses: Sequence[Mapping[str, Any] | ContentGenerationResult | Exception],
        *,
        version: str = "test",
    ) -> None:
        self.version = version
        self._responses = list(responses)
        self.calls: list[ContentGenerationRequest] = []

    def generate(self, request: ContentGenerationRequest) -> ContentGenerationResult:
        self.calls.append(request)
        if not self._responses:
            raise AssertionError("MockContentEngineAdapter 没有剩余响应")
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, ContentGenerationResult):
            return item
        return ContentGenerationResult.from_payload(
            item,
            default_engine=self.name,
            default_engine_version=self.version,
            request_id=request.request_id,
            trace_id=request.request_id,
            elapsed_ms=0,
        )

    def health(self) -> dict[str, Any]:
        return {
            "engine": self.name,
            "version": self.version,
            "ready": True,
            "status": "ready",
        }


def _normalize_allowed_hosts(value: Sequence[str] | str | None) -> frozenset[str]:
    if value is None:
        value = ("content-engine",)
    elif isinstance(value, str):
        value = value.split(",")
    hosts = frozenset(str(item or "").strip().casefold().rstrip(".") for item in value if str(item or "").strip())
    if not hosts:
        raise ContentEngineConfigurationError("同事内容引擎主机允许列表不能为空")
    return hosts


def _validate_internal_base_url(value: str, allowed_hosts: frozenset[str]) -> str:
    normalized = str(value or "").strip().rstrip("/")
    try:
        parsed = urlsplit(normalized)
    except ValueError as exc:
        raise ContentEngineConfigurationError("同事内容引擎 URL 无效") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ContentEngineConfigurationError("同事内容引擎 URL 必须是 HTTP 或 HTTPS 地址")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ContentEngineConfigurationError("同事内容引擎 URL 不能包含凭据、查询参数或锚点")
    if parsed.hostname.casefold().rstrip(".") not in allowed_hosts:
        raise ContentEngineConfigurationError("同事内容引擎主机不在允许列表中")
    return normalized


def _endpoint(base_url: str, path: str) -> str:
    if base_url.endswith(path):
        return base_url
    return f"{base_url}{path}"


def _positive_float(value: Any, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ContentEngineConfigurationError(f"{name} 必须是正数") from exc
    if number <= 0:
        raise ContentEngineConfigurationError(f"{name} 必须是正数")
    return number


def _positive_int(value: Any, name: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ContentEngineConfigurationError(f"{name} 必须是正整数") from exc
    if number <= 0:
        raise ContentEngineConfigurationError(f"{name} 必须是正整数")
    return number


def _response_trace_id(response) -> str:
    headers = getattr(response, "headers", {}) or {}
    return str(
        headers.get("X-Trace-ID")
        or headers.get("x-trace-id")
        or headers.get("X-Request-ID")
        or headers.get("x-request-id")
        or ""
    ).strip()


def _retry_after(response) -> int | None:
    headers = getattr(response, "headers", {}) or {}
    try:
        return max(0, int(str(headers.get("Retry-After") or "").strip()))
    except (TypeError, ValueError):
        return None


def _read_bounded_json(response, maximum: int, *, trace_id: str) -> Mapping[str, Any]:
    headers = getattr(response, "headers", {}) or {}
    try:
        declared_length = int(str(headers.get("Content-Length") or "0"))
    except (TypeError, ValueError):
        declared_length = 0
    if declared_length > maximum:
        raise ContentEngineProtocolError("同事内容引擎响应过大", trace_id=trace_id)

    iterator = getattr(response, "iter_content", None)
    if callable(iterator):
        chunks: list[bytes] = []
        size = 0
        try:
            for chunk in iterator(chunk_size=65536):
                if not chunk:
                    continue
                size += len(chunk)
                if size > maximum:
                    raise ContentEngineProtocolError("同事内容引擎响应过大", trace_id=trace_id)
                chunks.append(chunk)
            payload = json.loads(b"".join(chunks).decode("utf-8"))
        except ContentEngineProtocolError:
            raise
        except requests.RequestException as exc:
            raise ContentEngineTimeoutError(
                "读取同事内容引擎响应时连接中断，请求结果未知，请勿自动重试",
                trace_id=trace_id,
            ) from exc
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ContentEngineProtocolError("同事内容引擎返回格式无效", trace_id=trace_id) from exc
    else:
        try:
            payload = response.json()
        except (TypeError, ValueError) as exc:
            raise ContentEngineProtocolError("同事内容引擎返回格式无效", trace_id=trace_id) from exc
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(encoded) > maximum:
            raise ContentEngineProtocolError("同事内容引擎响应过大", trace_id=trace_id)

    if not isinstance(payload, Mapping):
        raise ContentEngineProtocolError("同事内容引擎返回格式无效", trace_id=trace_id)
    return payload
