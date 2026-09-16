"""OpenAI-compatible article-cover generation with auditable boundaries."""

from __future__ import annotations

import base64
import binascii
import re
import socket
import time
from dataclasses import dataclass
from ipaddress import ip_address, ip_network
from typing import Any, Callable, Mapping
from urllib.parse import urljoin, urlsplit

import requests

from services.ai_service import (
    AIConnectError,
    AIProviderRateLimitError,
    AIReadTimeoutError,
    AIServiceError,
    AISettings,
    ai_image_role_compatibility_issue,
    is_definite_pre_send_connection_error,
    provider_label_for_settings,
    resolve_ai_image_generations_url,
)


MAX_IMAGE_RESPONSE_BYTES = 15 * 1024 * 1024
MAX_BASE64_CHARACTERS = ((MAX_IMAGE_RESPONSE_BYTES + 2) // 3) * 4 + 16
_PROXY_FAKE_IP_NETWORK = ip_network("198.18.0.0/15")
_PROXY_FAKE_IP_PROBE_HOST = "example.com"
_IP_MAPPING_DOMAINS = (
    "nip.io",
    "sslip.io",
    "xip.io",
    "localtest.me",
    "lvh.me",
    "vcap.me",
)


@dataclass(frozen=True)
class AIImageGenerationResult:
    image_bytes: bytes
    provider: str
    model: str
    provider_request_id: str
    elapsed_ms: int
    usage: Mapping[str, int] | None
    endpoint: str
    request_profile: str = "minimal_openai_images"


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
        diagnostic_code: str = "image_provider_error",
        upstream_status: int | None = None,
        endpoint: str = "",
        request_profile: str = "minimal_openai_images",
    ) -> None:
        super().__init__(message)
        self.request_sent = bool(request_sent)
        self.outcome_unknown = bool(outcome_unknown)
        self.provider = str(provider or "")
        self.model = str(model or "")
        self.provider_request_id = str(provider_request_id or "")
        self.elapsed_ms = max(0, int(elapsed_ms or 0))
        self.usage = dict(usage) if isinstance(usage, Mapping) else None
        self.diagnostic_code = str(diagnostic_code or "image_provider_error")
        self.upstream_status = (
            int(upstream_status) if upstream_status is not None else None
        )
        self.endpoint = str(endpoint or "")
        self.request_profile = str(request_profile or "minimal_openai_images")


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
    compatibility_issue = ai_image_role_compatibility_issue(
        base_url=settings.base_url,
        model=settings.model,
    )
    if compatibility_issue is not None:
        raise AIImageGenerationError(
            compatibility_issue[1],
            request_sent=False,
            provider=provider,
            model=settings.model,
            diagnostic_code=compatibility_issue[0],
            endpoint=endpoint,
        )
    prompt = _build_cover_prompt(article=article, project=project)
    request_payload, request_profile = _build_image_request_payload(
        settings=settings,
        prompt=prompt,
    )
    started_at = time.perf_counter()
    try:
        response = post(
            endpoint,
            headers={
                "Authorization": f"Bearer {settings.api_key}",
                "Content-Type": "application/json",
            },
            json=request_payload,
            timeout=settings.timeout_seconds,
            allow_redirects=False,
        )
    except requests.ConnectTimeout as exc:
        raise AIImageGenerationError(
            f"连接生图服务超时（{settings.timeout_seconds:g} 秒）；"
            "请核对 Base URL 、网络与服务商状态后再试",
            request_sent=False,
            provider=provider,
            model=settings.model,
            elapsed_ms=_elapsed_ms(started_at),
            diagnostic_code="connection_timeout",
            endpoint=endpoint,
            request_profile=request_profile,
        ) from exc
    except requests.ReadTimeout as exc:
        raise AIImageGenerationError(
            f"生图服务在 {settings.timeout_seconds:g} 秒内未返回；请求结果未知，请勿自动重试",
            request_sent=True,
            outcome_unknown=True,
            provider=provider,
            model=settings.model,
            elapsed_ms=_elapsed_ms(started_at),
            diagnostic_code="response_timeout_unknown",
            endpoint=endpoint,
            request_profile=request_profile,
        ) from exc
    except requests.Timeout as exc:
        raise AIImageGenerationError(
            f"生图服务在 {settings.timeout_seconds:g} 秒内未返回；请求结果未知，请勿自动重试",
            request_sent=True,
            outcome_unknown=True,
            provider=provider,
            model=settings.model,
            elapsed_ms=_elapsed_ms(started_at),
            diagnostic_code="response_timeout_unknown",
            endpoint=endpoint,
            request_profile=request_profile,
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
            diagnostic_code="connection_interrupted_unknown",
            endpoint=endpoint,
            request_profile=request_profile,
        ) from exc
    except requests.RequestException as exc:
        raise AIImageGenerationError(
            "生图服务请求失败",
            request_sent=True,
            outcome_unknown=True,
            provider=provider,
            model=settings.model,
            elapsed_ms=_elapsed_ms(started_at),
            diagnostic_code="request_failed_unknown",
            endpoint=endpoint,
            request_profile=request_profile,
        ) from exc

    elapsed_ms = _elapsed_ms(started_at)
    status_code = int(getattr(response, "status_code", 0) or 0)
    response_headers = getattr(response, "headers", {}) or {}
    response_request_id = _safe_provider_request_id(
        response_headers.get("X-Request-ID")
        or response_headers.get("x-request-id")
        or response_headers.get("X-One-API-Request-ID")
        or response_headers.get("x-one-api-request-id")
    )
    if 300 <= status_code < 400:
        raise AIImageGenerationError(
            "生图服务地址发生重定向，已为安全起见停止",
            request_sent=True,
            provider=provider,
            model=settings.model,
            provider_request_id=response_request_id,
            elapsed_ms=elapsed_ms,
            diagnostic_code="redirect_rejected",
            upstream_status=status_code,
            endpoint=endpoint,
            request_profile=request_profile,
        )
    if status_code == 429:
        retry_after = _retry_after_seconds(getattr(response, "headers", {}) or {})
        wait_hint = (
            f"；请至少等待 {retry_after} 秒后由人工重试"
            if retry_after is not None
            else "；请按服务商限流窗口稍后由人工重试"
        )
        raise AIProviderRateLimitError(
            f"生图服务返回 HTTP 429，已达到模型、账户或网关限流{wait_hint}",
            retry_after_seconds=retry_after,
        )
    if status_code >= 400 or status_code < 200:
        diagnostic_code, message, outcome_unknown = _image_provider_http_error(
            status_code,
            endpoint=endpoint,
            provider=provider,
            model=settings.model,
            provider_error_signal=_safe_provider_error_signal(response),
            request_profile=request_profile,
        )
        raise AIImageGenerationError(
            message,
            request_sent=True,
            outcome_unknown=outcome_unknown,
            provider=provider,
            model=settings.model,
            provider_request_id=response_request_id,
            elapsed_ms=elapsed_ms,
            diagnostic_code=diagnostic_code,
            upstream_status=status_code or None,
            endpoint=endpoint,
            request_profile=request_profile,
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
            diagnostic_code="invalid_response",
            endpoint=endpoint,
            request_profile=request_profile,
        ) from exc
    request_id = response_request_id or _safe_provider_request_id(
        payload.get("id") if isinstance(payload, Mapping) else ""
    )
    model = str(
        (payload.get("model") if isinstance(payload, Mapping) else "")
        or settings.model
    ).strip() or settings.model
    usage = _normalize_usage(payload.get("usage") if isinstance(payload, Mapping) else None)
    try:
        image_bytes = _extract_image_bytes(
            payload,
            http_get=get,
            dns_resolver=resolver,
            source_endpoint=endpoint,
        )
    except AIServiceError as exc:
        raise AIImageGenerationError(
            str(exc),
            request_sent=True,
            provider=provider,
            model=model,
            provider_request_id=request_id,
            elapsed_ms=elapsed_ms,
            usage=usage,
            diagnostic_code="invalid_image_result",
            endpoint=endpoint,
            request_profile=request_profile,
        ) from exc
    return AIImageGenerationResult(
        image_bytes=image_bytes,
        provider=provider,
        model=model,
        provider_request_id=request_id,
        elapsed_ms=elapsed_ms,
        usage=usage,
        endpoint=endpoint,
        request_profile=request_profile,
    )


def _build_image_request_payload(
    *,
    settings: AISettings,
    prompt: str,
) -> tuple[dict[str, Any], str]:
    """Build one non-retried request using the smallest compatible contract.

    OpenAI image fields such as ``n``, ``size`` and ``response_format`` are
    optional, but their accepted values are provider/model specific.  The old
    hard-coded ``1024x1024`` + ``b64_json`` combination caused valid Seedream
    routes to reject the request before generation.  The configured gateway's
    own Images example sends only ``model`` and ``prompt``; URL and base64
    responses are both accepted below, so forcing either format is unnecessary.
    """

    payload: dict[str, Any] = {
        "model": settings.model,
        "prompt": prompt,
    }
    return payload, "minimal_openai_images"


def _safe_provider_error_signal(response: Any) -> str:
    """Reduce an untrusted provider body to a bounded, non-display signal.

    The returned text is used only for local keyword classification and is
    never attached to API responses, logs or exceptions.  This lets the UI
    distinguish a bad model ID from an unsupported size without leaking an
    upstream body that may contain customer content or credentials.
    """

    try:
        payload = response.json()
    except (AttributeError, TypeError, ValueError):
        return ""
    if not isinstance(payload, Mapping):
        return ""
    error = payload.get("error")
    sources = [error] if isinstance(error, Mapping) else []
    sources.append(payload)
    fragments: list[str] = []
    for source in sources:
        if not isinstance(source, Mapping):
            continue
        for key in ("code", "type", "param", "message"):
            value = source.get(key)
            if isinstance(value, (str, int, float)):
                fragments.append(f"{key}:{str(value)[:512]}")
    return " ".join(fragments)[:2048].casefold()


def _safe_provider_request_id(value: Any) -> str:
    """Return a bounded opaque request id suitable for support correlation."""

    candidate = str(value or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", candidate):
        return ""
    return candidate


def _image_provider_http_error(
    status_code: int,
    *,
    endpoint: str,
    provider: str,
    model: str,
    provider_error_signal: str = "",
    request_profile: str = "minimal_openai_images",
) -> tuple[str, str, bool]:
    """Classify an untrusted provider response without exposing its body."""

    label = str(provider or "OpenAI-compatible")
    safe_model = str(model or "").strip()
    context = f"{label}，请求地址 {endpoint}，模型 {safe_model}"
    if status_code in {400, 422}:
        signal = str(provider_error_signal or "").casefold()
        if _has_any_error_marker(
            signal,
            "insufficient_quota",
            "insufficient quota",
            "quota exceeded",
            "insufficient_balance",
            "insufficient balance",
            "余额不足",
            "额度不足",
        ):
            return (
                "image_quota_or_balance_unavailable",
                f"生图服务返回 HTTP {status_code}（{context}）："
                "上游将其识别为账户余额或图片额度不足。"
                "请先在服务商后台检查 IMAGE 模型额度和账单；软件不会自动重试扣费。",
                False,
            )
        if _has_any_error_marker(
            signal,
            "model_not_found",
            "model not found",
            "invalid_model",
            "invalid model",
            "unknown model",
            "unsupported model",
            "model does not exist",
            "模型不存在",
            "模型未找到",
            "无效模型",
        ) or "param:model" in signal:
            return (
                "image_model_identifier_rejected",
                f"生图服务返回 HTTP {status_code}（{context}）："
                "上游拒绝了模型标识。请从当前 API Key 可访问的 GET /v1/models "
                "结果中复制精确图片模型 ID，并确认该模型能力包含 images。",
                False,
            )
        if _has_any_error_marker(
            signal,
            "response_format",
            "response format",
            "param:responseformat",
            "返回格式",
        ):
            return (
                "image_response_format_rejected",
                f"生图服务返回 HTTP {status_code}（{context}）："
                "上游要求了特定图片返回格式，但当前最小请求没有强制 url 或 b64_json。"
                "请核对网关是否为该图片路由配置了默认返回格式。",
                False,
            )
        if _has_any_error_marker(
            signal,
            "resolution",
            "image_size",
            "param:size",
            "invalid size",
            "unsupported size",
            "size must",
            "分辨率",
            "尺寸",
        ):
            return (
                "image_size_rejected",
                f"生图服务返回 HTTP {status_code}（{context}）："
                "上游要求了特定图片尺寸，但当前最小请求没有强制尺寸。"
                "请确认网关已为该公开模型路由绑定默认图片规格。",
                False,
            )
        if _has_any_error_marker(
            signal,
            "content_policy",
            "content policy",
            "safety",
            "sensitive",
            "prompt rejected",
            "内容安全",
            "敏感",
        ):
            return (
                "image_prompt_rejected",
                f"生图服务返回 HTTP {status_code}（{context}）："
                "上游内容安全策略拒绝了合成测试提示词。"
                "这不是 Base URL 故障，请在服务商后台查看安全策略或模型可用状态。",
                False,
            )
        profile_hint = "本次只发送 model 和 prompt 两个最小必需字段"
        return (
            "invalid_request_or_image_model",
            f"生图服务返回 HTTP {status_code}（{context}）："
            f"请求已到达服务；{profile_hint}，仍被上游判定为无效请求。"
            "请在服务商请求日志中按时间定位该调用，重点核对当前密钥的模型白名单和上游路由；"
            "软件不会显示或记录不受信任的上游错误正文。",
            False,
        )
    if status_code == 401:
        return (
            "authentication_failed",
            "生图服务返回 HTTP 401：API Key 无效、已过期，"
            "或不属于当前 Base URL 对应的服务；请重新生成密钥并核对网关。",
            False,
        )
    if status_code == 403:
        return (
            "permission_denied",
            "生图服务返回 HTTP 403：当前 API Key 没有该图片模型、"
            "接入点或生图功能权限；请在服务商控制台开通并核对账户额度。",
            False,
        )
    if status_code == 404:
        return (
            "image_endpoint_not_found",
            f"生图服务返回 HTTP 404（{context}）："
            "当前地址已按 Base URL 解析为 OpenAI-compatible "
            "/images/generations，但网关可能未实现/未开放该端点，"
            "或该模型未映射到图片路由。请向供应商确认完整图片 API "
            "地址和图片模型 ID；Seedance 视频模型不能用于此接口。",
            False,
        )
    if status_code in {408, 504}:
        return (
            "provider_timeout_unknown",
            f"生图服务返回 HTTP {status_code}（{context}）："
            "服务商或其上游处理超时，请求结果未知，请勿自动重试。",
            True,
        )
    if status_code >= 500:
        return (
            "provider_unavailable",
            f"生图服务返回 HTTP {status_code}（{context}）："
            "服务商或网关内部故障；请保留时间与请求记录后查看服务状态。",
            False,
        )
    return (
        "image_provider_http_error",
        f"生图服务返回 HTTP {status_code or '未知'}（{context}）；"
        "请核对 IMAGE 角色的 Base URL、图片模型 ID 与权限。",
        False,
    )


def _has_any_error_marker(signal: str, *markers: str) -> bool:
    normalized = str(signal or "").casefold()
    return any(marker.casefold() in normalized for marker in markers)


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
    source_endpoint: str,
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
    url = _normalize_image_result_url(url, source_endpoint=source_endpoint)
    _validate_public_image_url(
        url,
        dns_resolver=dns_resolver,
        source_endpoint=source_endpoint,
    )
    try:
        response = http_get(
            url,
            stream=True,
            timeout=(10, 30),
            allow_redirects=False,
            verify=True,
        )
    except requests.RequestException as exc:
        raise AIServiceError("生图已完成，但图片下载失败；不要直接重试模型") from exc
    try:
        status_code = int(getattr(response, "status_code", 0) or 0)
        if status_code != 200:
            raise AIServiceError(
                f"生图已完成，但图片下载返回 HTTP {status_code or '未知'}；不要直接重试模型"
            )
        headers = getattr(response, "headers", {}) or {}
        content_type = (
            str(headers.get("Content-Type") or "")
            .split(";", 1)[0]
            .strip()
            .lower()
        )
        if content_type and not (
            content_type.startswith("image/")
            or content_type in {"application/octet-stream", "binary/octet-stream"}
        ):
            raise AIServiceError("生图已完成，但下载地址返回网页或非图片内容")
        try:
            declared = int(headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            declared = 0
        if declared > MAX_IMAGE_RESPONSE_BYTES:
            raise AIServiceError("生图已完成，但返回图片超过 15MB 安全上限")
        chunks: list[bytes] = []
        total = 0
        iterator = getattr(response, "iter_content", None)
        try:
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
        except requests.RequestException as exc:
            raise AIServiceError("生图已完成，但图片下载中断；不要直接重试模型") from exc
        if not result or len(result) > MAX_IMAGE_RESPONSE_BYTES:
            raise AIServiceError("生图已完成，但没有取得安全可用的图片")
        return result
    finally:
        close = getattr(response, "close", None)
        if callable(close):
            close()


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
    source_endpoint: str,
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
    if hostname == "localhost" or hostname.endswith(
        (".localhost", ".local", ".internal")
    ):
        raise AIServiceError("生图服务返回的图片 URL 指向本机或内部网络")
    try:
        literal = _normalize_ip_address(ip_address(hostname))
    except ValueError:
        literal = None
    if literal is None and _hostname_contains_encoded_ip(hostname):
        raise AIServiceError("生图服务返回的图片 URL 使用了高风险 IP 映射域名")
    if literal is not None:
        addresses = [literal]
        hostname_is_literal = True
    else:
        addresses = _resolve_ip_addresses(hostname, dns_resolver=dns_resolver)
        hostname_is_literal = False
    if not addresses:
        raise AIServiceError("无法安全解析生图图片地址")
    if all(_is_public_unicast(address) for address in addresses):
        return
    if (
        not hostname_is_literal
        and all(
            _is_public_unicast(address) or _is_proxy_fake_ip(address)
            for address in addresses
        )
        and any(_is_proxy_fake_ip(address) for address in addresses)
        and _proxy_fake_ip_dns_mode_is_confirmed(
            source_endpoint=source_endpoint,
            dns_resolver=dns_resolver,
        )
    ):
        return
    raise AIServiceError("生图服务返回的图片 URL 指向私网或保留地址")


def _normalize_image_result_url(value: str, *, source_endpoint: str) -> str:
    """Accept only absolute HTTPS URLs or same-origin relative result paths."""

    candidate = str(value or "").strip()
    if not candidate or "\\" in candidate or any(ord(char) < 32 for char in candidate):
        raise AIServiceError("生图服务返回了无效图片 URL")
    try:
        parsed = urlsplit(candidate)
    except ValueError as exc:
        raise AIServiceError("生图服务返回了无效图片 URL") from exc
    if parsed.scheme or parsed.netloc:
        return candidate
    try:
        source = urlsplit(source_endpoint)
        normalized = urlsplit(urljoin(source_endpoint, candidate))
        source_port = source.port or (443 if source.scheme == "https" else None)
        normalized_port = normalized.port or (
            443 if normalized.scheme == "https" else None
        )
    except ValueError as exc:
        raise AIServiceError("生图服务返回了无效图片 URL") from exc
    if (
        normalized.scheme != source.scheme
        or (normalized.hostname or "").rstrip(".").lower()
        != (source.hostname or "").rstrip(".").lower()
        or normalized_port != source_port
    ):
        raise AIServiceError("生图服务返回的相对图片 URL 脱离了原服务地址")
    return normalized.geturl()


def _resolve_ip_addresses(
    hostname: str,
    *,
    dns_resolver: Callable[..., Any],
) -> list[Any]:
    try:
        resolved = dns_resolver(hostname, 443, type=socket.SOCK_STREAM)
    except OSError:
        return []
    addresses: list[Any] = []
    seen: set[str] = set()
    for item in resolved:
        try:
            raw_address = str(item[4][0]).split("%", 1)[0]
            address = _normalize_ip_address(ip_address(raw_address))
        except (IndexError, TypeError, ValueError):
            # A malformed resolver answer must not be hidden behind another
            # apparently public result.
            return []
        key = str(address)
        if key not in seen:
            seen.add(key)
            addresses.append(address)
    return addresses


def _normalize_ip_address(address: Any) -> Any:
    mapped = getattr(address, "ipv4_mapped", None)
    return mapped or address


def _is_public_unicast(address: Any) -> bool:
    return bool(
        address.is_global
        and not address.is_multicast
        and not address.is_unspecified
        and not address.is_loopback
        and not address.is_link_local
    )


def _is_proxy_fake_ip(address: Any) -> bool:
    return address.version == 4 and address in _PROXY_FAKE_IP_NETWORK


def _hostname_contains_encoded_ip(hostname: str) -> bool:
    """Reject public DNS names commonly used to tunnel requests to an IP.

    This check matters in Fake-IP mode: the local resolver would otherwise
    hide a hostname's eventual private target behind a synthetic 198.18/15
    answer.  It intentionally runs before the Fake-IP exception is considered.
    """

    normalized = str(hostname or "").rstrip(".").casefold()
    if any(
        normalized == domain or normalized.endswith(f".{domain}")
        for domain in _IP_MAPPING_DOMAINS
    ):
        return True
    labels = normalized.split(".")
    for start in range(max(0, len(labels) - 3)):
        candidate = labels[start : start + 4]
        if all(
            part.isdecimal() and 0 <= int(part) <= 255
            for part in candidate
        ):
            return True
    for label in labels:
        dashed_ipv4 = label.split("-")
        if len(dashed_ipv4) == 4 and all(
            part.isdecimal() and 0 <= int(part) <= 255
            for part in dashed_ipv4
        ):
            return True
        if re.fullmatch(r"0x[0-9a-f]{1,8}", label):
            return True
        if label.isdecimal() and len(label) >= 7:
            try:
                if int(label, 10) <= 0xFFFFFFFF:
                    return True
                if label.startswith("0") and int(label, 8) <= 0xFFFFFFFF:
                    return True
            except ValueError:
                pass
        if re.fullmatch(r"[0-9a-f]{8}", label) and any(
            char in "abcdef" for char in label
        ):
            return True
        if "-" in label and not label.startswith("xn--"):
            ipv6_candidate = label.replace("--", "::").replace("-", ":")
            try:
                if ip_address(ipv6_candidate).version == 6:
                    return True
            except ValueError:
                pass
    return False


def _proxy_fake_ip_dns_mode_is_confirmed(
    *,
    source_endpoint: str,
    dns_resolver: Callable[..., Any],
) -> bool:
    """Recognize system-wide proxy Fake-IP DNS without trusting one result host.

    198.18/15 remains blocked for IP literals and ordinary DNS.  It is accepted
    only when both the already configured upstream hostname and an independent
    public probe hostname resolve through the same Fake-IP regime, while every
    returned address remains either public unicast or inside that one reserved
    proxy range.
    """

    source_hostname = ""
    try:
        source = urlsplit(source_endpoint)
        source_hostname = (source.hostname or "").rstrip(".").lower()
        ip_address(source_hostname)
    except (ValueError, AttributeError):
        pass
    else:
        return False
    if not source_hostname:
        return False
    source_addresses = _resolve_ip_addresses(
        source_hostname,
        dns_resolver=dns_resolver,
    )
    probe_addresses = _resolve_ip_addresses(
        _PROXY_FAKE_IP_PROBE_HOST,
        dns_resolver=dns_resolver,
    )
    for addresses in (source_addresses, probe_addresses):
        if (
            not addresses
            or not any(_is_proxy_fake_ip(address) for address in addresses)
            or any(
                not (_is_public_unicast(address) or _is_proxy_fake_ip(address))
                for address in addresses
            )
        ):
            return False
    return True


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
