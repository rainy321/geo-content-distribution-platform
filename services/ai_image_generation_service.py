"""OpenAI-compatible article-cover generation with auditable boundaries."""

from __future__ import annotations

import base64
import binascii
import socket
import time
from dataclasses import dataclass
from ipaddress import ip_address
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit

import requests

from services.ai_service import (
    AIConnectError,
    AIProviderRateLimitError,
    AIReadTimeoutError,
    AIServiceError,
    AISettings,
    is_definite_pre_send_connection_error,
    provider_label_for_settings,
    resolve_ai_image_generations_url,
)


MAX_IMAGE_RESPONSE_BYTES = 15 * 1024 * 1024
MAX_BASE64_CHARACTERS = ((MAX_IMAGE_RESPONSE_BYTES + 2) // 3) * 4 + 16


@dataclass(frozen=True)
class AIImageGenerationResult:
    image_bytes: bytes
    provider: str
    model: str
    provider_request_id: str
    elapsed_ms: int
    usage: Mapping[str, int] | None
    endpoint: str


class AIImageGenerationError(AIServiceError):
    """An image call failed while retaining safe billing metadata."""

    def __init__(
        self,
        message: str,
        *,
        request_sent: bool = False,
        outcome_unknown: bool = False,
        provider: str = "",
        model: str = "",
        provider_request_id: str = "",
        elapsed_ms: int = 0,
        usage: Mapping[str, int] | None = None,
    ) -> None:
        super().__init__(message)
        self.request_sent = bool(request_sent)
        self.outcome_unknown = bool(outcome_unknown)
        self.provider = str(provider or "")
        self.model = str(model or "")
        self.provider_request_id = str(provider_request_id or "")
        self.elapsed_ms = max(0, int(elapsed_ms or 0))
        self.usage = dict(usage) if isinstance(usage, Mapping) else None


def generate_ai_article_cover(
    *,
    article: Mapping[str, Any],
    project: Mapping[str, Any],
    settings: AISettings,
    http_post: Callable[..., Any] | None = None,
    http_get: Callable[..., Any] | None = None,
    dns_resolver: Callable[..., Any] | None = None,
) -> AIImageGenerationResult:
    """Generate one cover without retries and return provider metadata."""

    post = http_post or requests.post
    get = http_get or requests.get
    resolver = dns_resolver or socket.getaddrinfo
    endpoint = resolve_ai_image_generations_url(settings.base_url)
    provider = provider_label_for_settings(settings)
    prompt = _build_cover_prompt(article=article, project=project)
    started_at = time.perf_counter()
    try:
        response = post(
            endpoint,
            headers={
                "Authorization": f"Bearer {settings.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": settings.model,
                "prompt": prompt,
                "n": 1,
                "size": "1024x1024",
                "response_format": "b64_json",
            },
            timeout=settings.timeout_seconds,
            allow_redirects=False,
        )
    except requests.ConnectTimeout as exc:
        raise AIConnectError("无法连接生图服务") from exc
    except requests.ReadTimeout as exc:
        raise AIImageGenerationError(
            f"生图服务在 {settings.timeout_seconds:g} 秒内未返回；请求结果未知，请勿自动重试",
            request_sent=True,
            outcome_unknown=True,
            provider=provider,
            model=settings.model,
            elapsed_ms=_elapsed_ms(started_at),
        ) from exc
    except requests.Timeout as exc:
        raise AIImageGenerationError(
            f"生图服务在 {settings.timeout_seconds:g} 秒内未返回；请求结果未知，请勿自动重试",
            request_sent=True,
            outcome_unknown=True,
            provider=provider,
            model=settings.model,
            elapsed_ms=_elapsed_ms(started_at),
        ) from exc
    except requests.ConnectionError as exc:
        if is_definite_pre_send_connection_error(exc):
            raise AIConnectError("无法连接生图服务") from exc
        raise AIImageGenerationError(
            "生图服务连接中断；请求结果未知，请勿自动重试",
            request_sent=True,
            outcome_unknown=True,
            provider=provider,
            model=settings.model,
            elapsed_ms=_elapsed_ms(started_at),
        ) from exc
    except requests.RequestException as exc:
        raise AIImageGenerationError(
            "生图服务请求失败",
            request_sent=True,
            outcome_unknown=True,
            provider=provider,
            model=settings.model,
            elapsed_ms=_elapsed_ms(started_at),
        ) from exc

    elapsed_ms = _elapsed_ms(started_at)
    status_code = int(getattr(response, "status_code", 0) or 0)
    if 300 <= status_code < 400:
        raise AIImageGenerationError(
            "生图服务地址发生重定向，已为安全起见停止",
            request_sent=True,
            provider=provider,
            model=settings.model,
            elapsed_ms=elapsed_ms,
        )
    if status_code == 429:
        retry_after = _retry_after_seconds(getattr(response, "headers", {}) or {})
        raise AIProviderRateLimitError(
            "生图服务已达到限流，请稍后再试",
            retry_after_seconds=retry_after,
        )
    if status_code >= 400 or status_code < 200:
        raise AIImageGenerationError(
            f"生图服务返回 HTTP {status_code or '未知'}；请核对 IMAGE 角色的 Base URL、模型 ID 与权限",
            request_sent=True,
            provider=provider,
            model=settings.model,
            elapsed_ms=elapsed_ms,
        )

    try:
        payload = response.json()
    except (TypeError, ValueError) as exc:
        raise AIImageGenerationError(
            "生图服务返回格式无效；不要直接重试，请先核对供应商账单",
            request_sent=True,
            provider=provider,
            model=settings.model,
            elapsed_ms=elapsed_ms,
        ) from exc
    headers = getattr(response, "headers", {}) or {}
    request_id = str(
        headers.get("X-Request-ID")
        or headers.get("x-request-id")
        or (payload.get("id") if isinstance(payload, Mapping) else "")
        or ""
    ).strip()
    model = str(
        (payload.get("model") if isinstance(payload, Mapping) else "")
        or settings.model
    ).strip() or settings.model
    usage = _normalize_usage(payload.get("usage") if isinstance(payload, Mapping) else None)
    try:
        image_bytes = _extract_image_bytes(payload, http_get=get, dns_resolver=resolver)
    except AIServiceError as exc:
        raise AIImageGenerationError(
            str(exc),
            request_sent=True,
            provider=provider,
            model=model,
            provider_request_id=request_id,
            elapsed_ms=elapsed_ms,
            usage=usage,
        ) from exc
    return AIImageGenerationResult(
        image_bytes=image_bytes,
        provider=provider,
        model=model,
        provider_request_id=request_id,
        elapsed_ms=elapsed_ms,
        usage=usage,
        endpoint=endpoint,
    )


def _build_cover_prompt(
    *,
    article: Mapping[str, Any],
    project: Mapping[str, Any],
) -> str:
    title = str(article.get("title") or "GEO industry analysis").strip()[:160]
    brand = str(project.get("name") or "").strip()[:100]
    product = str(project.get("product") or "").strip()[:120]
    industry = str(project.get("industry") or "technology").strip()[:80]
    return (
        "Create a clean editorial cover background for a Chinese business article. "
        f"Topic: {title}. Brand context: {brand}. Product context: {product}. "
        f"Industry: {industry}. Minimal modern composition, trustworthy and restrained, "
        "high contrast focal area, no logos, no watermarks, no readable words or letters, "
        "no UI screenshot, no fake certificates or statistics. Leave calm negative space "
        "near the lower third because exact title text will be added locally."
    )


def _extract_image_bytes(
    payload: Any,
    *,
    http_get: Callable[..., Any],
    dns_resolver: Callable[..., Any],
) -> bytes:
    if not isinstance(payload, Mapping):
        raise AIServiceError("生图服务返回格式无效")
    data = payload.get("data")
    if not isinstance(data, list) or not data or not isinstance(data[0], Mapping):
        raise AIServiceError("生图服务响应缺少图片数据")
    item = data[0]
    encoded = str(item.get("b64_json") or "").strip()
    if encoded:
        return _decode_base64_image(encoded)
    url = str(item.get("url") or "").strip()
    if url.startswith("data:image/"):
        marker = ";base64,"
        if marker not in url:
            raise AIServiceError("生图服务返回的数据图片格式无效")
        return _decode_base64_image(url.split(marker, 1)[1])
    if not url:
        raise AIServiceError("生图服务响应没有 b64_json 或图片 URL")
    _validate_public_image_url(url, dns_resolver=dns_resolver)
    try:
        response = http_get(
            url,
            stream=True,
            timeout=(10, 30),
            allow_redirects=False,
        )
    except requests.RequestException as exc:
        raise AIServiceError("生图已完成，但图片下载失败；不要直接重试模型") from exc
    status_code = int(getattr(response, "status_code", 0) or 0)
    if status_code != 200:
        raise AIServiceError(
            f"生图已完成，但图片下载返回 HTTP {status_code or '未知'}；不要直接重试模型"
        )
    headers = getattr(response, "headers", {}) or {}
    try:
        declared = int(headers.get("Content-Length") or 0)
    except (TypeError, ValueError):
        declared = 0
    if declared > MAX_IMAGE_RESPONSE_BYTES:
        raise AIServiceError("生图已完成，但返回图片超过 15MB 安全上限")
    chunks: list[bytes] = []
    total = 0
    iterator = getattr(response, "iter_content", None)
    if callable(iterator):
        for chunk in iterator(chunk_size=64 * 1024):
            if not chunk:
                continue
            total += len(chunk)
            if total > MAX_IMAGE_RESPONSE_BYTES:
                raise AIServiceError("生图已完成，但返回图片超过 15MB 安全上限")
            chunks.append(bytes(chunk))
        result = b"".join(chunks)
    else:
        result = bytes(getattr(response, "content", b"") or b"")
    if not result or len(result) > MAX_IMAGE_RESPONSE_BYTES:
        raise AIServiceError("生图已完成，但没有取得安全可用的图片")
    return result


def _decode_base64_image(value: str) -> bytes:
    if len(value) > MAX_BASE64_CHARACTERS:
        raise AIServiceError("生图服务返回的图片超过 15MB 安全上限")
    try:
        decoded = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise AIServiceError("生图服务返回的 base64 图片无效") from exc
    if not decoded or len(decoded) > MAX_IMAGE_RESPONSE_BYTES:
        raise AIServiceError("生图服务返回的图片为空或超过安全上限")
    return decoded


def _validate_public_image_url(
    value: str,
    *,
    dns_resolver: Callable[..., Any],
) -> None:
    try:
        parsed = urlsplit(value)
        hostname = (parsed.hostname or "").rstrip(".").lower()
        port = parsed.port
    except ValueError as exc:
        raise AIServiceError("生图服务返回了无效图片 URL") from exc
    if (
        parsed.scheme != "https"
        or not hostname
        or parsed.username
        or parsed.password
        or parsed.fragment
        or (port is not None and port != 443)
    ):
        raise AIServiceError("生图服务返回的图片 URL 不符合 HTTPS 安全要求")
    if hostname == "localhost" or hostname.endswith((".local", ".internal")):
        raise AIServiceError("生图服务返回的图片 URL 指向本机或内部网络")
    try:
        literal = ip_address(hostname)
        addresses = [literal]
    except ValueError:
        try:
            resolved = dns_resolver(hostname, 443, type=socket.SOCK_STREAM)
        except OSError as exc:
            raise AIServiceError("无法安全解析生图图片地址") from exc
        addresses = []
        for item in resolved:
            try:
                addresses.append(ip_address(item[4][0]))
            except (IndexError, ValueError):
                continue
    if not addresses or any(not address.is_global for address in addresses):
        raise AIServiceError("生图服务返回的图片 URL 指向私网或保留地址")


def _normalize_usage(value: Any) -> dict[str, int] | None:
    if not isinstance(value, Mapping):
        return None
    aliases = {
        "input_tokens": ("input_tokens", "prompt_tokens"),
        "output_tokens": ("output_tokens", "completion_tokens"),
        "total_tokens": ("total_tokens",),
        "cached_tokens": ("cached_tokens",),
        "reasoning_tokens": ("reasoning_tokens",),
    }
    result: dict[str, int] = {}
    for target, candidates in aliases.items():
        raw = next((value.get(name) for name in candidates if value.get(name) is not None), None)
        if isinstance(raw, bool):
            continue
        try:
            number = int(raw)
        except (TypeError, ValueError):
            continue
        if number >= 0:
            result[target] = number
    return result or None


def _retry_after_seconds(headers: Mapping[str, Any]) -> int | None:
    try:
        value = int(str(headers.get("Retry-After") or "").strip())
    except (TypeError, ValueError):
        return None
    return max(0, min(value, 86_400))


def _elapsed_ms(started_at: float) -> int:
    return max(0, round((time.perf_counter() - started_at) * 1000))


__all__ = [
    "AIImageGenerationError",
    "AIImageGenerationResult",
    "generate_ai_article_cover",
]
