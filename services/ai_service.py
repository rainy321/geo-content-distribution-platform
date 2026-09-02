from __future__ import annotations

import json
import os
import re
from ipaddress import ip_address
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import requests


class AIServiceError(RuntimeError):
    """Raised when the configured AI provider cannot complete a request."""


class AIConfigurationError(AIServiceError):
    """Raised when required AI environment variables are missing."""


@dataclass(frozen=True)
class AISettings:
    base_url: str
    api_key: str
    model: str
    timeout_seconds: float = 120.0

    @classmethod
    def from_environment(cls) -> "AISettings":
        values = {
            "AI_BASE_URL": os.getenv("AI_BASE_URL", "").strip(),
            "AI_API_KEY": os.getenv("AI_API_KEY", "").strip(),
            "AI_MODEL": os.getenv("AI_MODEL", "").strip(),
        }
        missing = [name for name, value in values.items() if not value]
        if missing:
            raise AIConfigurationError(
                f"缺少 AI 配置: {', '.join(missing)}"
            )
        return cls(
            base_url=values["AI_BASE_URL"],
            api_key=values["AI_API_KEY"],
            model=values["AI_MODEL"],
        )

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "AISettings":
        """Build a request-scoped OpenAI-compatible configuration safely."""

        if not isinstance(value, Mapping):
            raise AIConfigurationError("ai_config 必须是对象")

        values = {
            "base_url": str(value.get("base_url") or "").strip(),
            "api_key": str(value.get("api_key") or "").strip(),
            "model": str(value.get("model") or "").strip(),
        }
        missing = [name for name, item in values.items() if not item]
        if missing:
            raise AIConfigurationError(
                f"自定义 AI 配置缺少: {', '.join(missing)}"
            )

        _validate_custom_base_url(values["base_url"])
        if len(values["api_key"]) > 8192 or any(
            marker in values["api_key"] for marker in ("\r", "\n")
        ):
            raise AIConfigurationError("自定义 AI API Key 格式无效")
        if len(values["model"]) > 200 or any(
            marker in values["model"] for marker in ("\r", "\n")
        ):
            raise AIConfigurationError("自定义 AI 模型名称格式无效")

        return cls(**values)

    @classmethod
    def environment_is_configured(cls) -> bool:
        return all(
            str(os.getenv(name, "")).strip()
            for name in ("AI_BASE_URL", "AI_API_KEY", "AI_MODEL")
        )


def generate_geo_content(
    *,
    project: Mapping[str, Any],
    topic: str,
    keywords: Sequence[str],
    length: int,
    content_type: str,
    target_platform: str = "",
    settings: AISettings | None = None,
    http_post=None,
) -> dict[str, Any]:
    """Generate one GEO article through an OpenAI-compatible endpoint."""
    settings = settings or AISettings.from_environment()
    http_post = http_post or requests.post
    prompt = _build_prompt(
        project=project,
        topic=topic,
        keywords=keywords,
        length=length,
        content_type=content_type,
        target_platform=target_platform,
    )

    raw_content = _request_chat_completion(
        settings=settings,
        prompt=prompt,
        temperature=0.7,
        http_post=http_post,
    )
    return _parse_model_output(raw_content, fallback_tags=keywords)


def optimize_geo_content(
    *,
    article: Mapping[str, Any],
    score: Mapping[str, Any],
    settings: AISettings | None = None,
    http_post=None,
) -> dict[str, Any]:
    """Improve one article from its rule score without saving it."""
    settings = settings or AISettings.from_environment()
    http_post = http_post or requests.post
    prompt = _build_optimization_prompt(article=article, score=score)
    raw_content = _request_chat_completion(
        settings=settings,
        prompt=prompt,
        temperature=0.45,
        http_post=http_post,
    )
    return _parse_model_output(
        raw_content,
        fallback_tags=article.get("tags", []),
    )


def _request_chat_completion(
    *,
    settings: AISettings,
    prompt: str,
    temperature: float,
    http_post,
) -> str:
    """Call the shared OpenAI-compatible chat completion endpoint."""

    try:
        response = http_post(
            _chat_completions_url(settings.base_url),
            headers={
                "Authorization": f"Bearer {settings.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": settings.model,
                "messages": [
                    {
                        "role": "system",
                        "content": "你是一名 GEO / AEO 内容优化专家。",
                    },
                    {"role": "user", "content": prompt},
                ],
                "temperature": temperature,
            },
            timeout=settings.timeout_seconds,
            allow_redirects=False,
        )
    except requests.RequestException as exc:
        raise AIServiceError(f"AI 服务请求失败: {exc}") from exc

    if 300 <= response.status_code < 400:
        raise AIServiceError("AI 服务地址发生重定向，已为安全起见停止请求")
    if response.status_code >= 400:
        detail = (response.text or "").strip()[:300]
        message = f"AI 服务返回 HTTP {response.status_code}"
        if detail:
            message = f"{message}: {detail}"
        raise AIServiceError(message)

    try:
        payload = response.json()
        raw_content = _extract_message_content(payload)
    except (TypeError, ValueError, KeyError) as exc:
        raise AIServiceError("AI 服务返回格式无效") from exc

    return raw_content


def _chat_completions_url(base_url: str) -> str:
    normalized = base_url.strip().rstrip("/")
    if normalized.endswith("/chat/completions"):
        return normalized
    if normalized.endswith("/v1"):
        return f"{normalized}/chat/completions"
    return f"{normalized}/v1/chat/completions"


def _validate_custom_base_url(base_url: str) -> None:
    if len(base_url) > 2048:
        raise AIConfigurationError("自定义 AI 服务地址过长")

    try:
        parsed = urlsplit(base_url)
        hostname = (parsed.hostname or "").rstrip(".").lower()
    except ValueError as exc:
        raise AIConfigurationError(
            "自定义 AI 服务地址必须是有效的 HTTPS URL"
        ) from exc
    if parsed.scheme != "https" or not hostname:
        raise AIConfigurationError("自定义 AI 服务地址必须是有效的 HTTPS URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise AIConfigurationError("自定义 AI 服务地址不能包含凭据、查询参数或锚点")
    if (
        hostname == "localhost"
        or hostname.endswith((".localhost", ".local", ".internal"))
        or "." not in hostname
    ):
        raise AIConfigurationError("自定义 AI 服务地址不能指向本机或内部网络")

    try:
        address = ip_address(hostname)
    except ValueError:
        return
    if not address.is_global:
        raise AIConfigurationError("自定义 AI 服务地址不能指向私网或保留地址")


def _build_prompt(
    *,
    project: Mapping[str, Any],
    topic: str,
    keywords: Sequence[str],
    length: int,
    content_type: str,
    target_platform: str,
) -> str:
    keyword_text = "、".join(keywords) if keywords else "无指定关键词"
    platform_text = target_platform or "通用内容媒体平台"
    return f"""
请根据以下资料生成一篇适合搜索引擎、生成式 AI 以及内容媒体平台收录的中文文章。

品牌：{project.get('name', '')}
官网：{project.get('website', '')}
产品：{project.get('product', '')}
行业：{project.get('industry', '')}
品牌介绍：{project.get('description', '')}
核心关键词：{keyword_text}
文章主题：{topic}
内容类型：{content_type}
目标平台：{platform_text}

要求：
1. 生成一个自然、可读的标题。
2. 正文约 {length} 字。
3. 自然出现品牌实体并覆盖目标关键词，不允许关键词堆砌。
4. 使用清晰的 H2/H3 内容结构。
5. 只使用输入中能够确认的品牌或产品事实，不编造数据。
6. 包含 FAQ 和总结。
7. 内容应适合媒体平台直接发布。

只返回一个 JSON 对象，不要附加解释：
{{
  "title": "",
  "summary": "",
  "content": "",
  "tags": [],
  "faq": []
}}
""".strip()


def _build_optimization_prompt(
    *,
    article: Mapping[str, Any],
    score: Mapping[str, Any],
) -> str:
    score_value = score.get("score", 0)
    suggestions = score.get("suggestions")
    suggestion_text = (
        "\n".join(f"- {item}" for item in suggestions)
        if isinstance(suggestions, list) and suggestions
        else "- 当前规则未给出额外建议，请只改善表达与结构。"
    )
    tags = article.get("tags")
    tag_text = "、".join(str(tag) for tag in tags) if isinstance(tags, list) else ""
    return f"""
根据以下 GEO 评分和建议优化文章。

要求：
1. 保持原意，不改变已确认的品牌、产品和结论。
2. 不要编造无法从原文确认的数据、案例、来源或链接。
3. 自然修复评分建议指出的问题，不要关键词堆砌。
4. 保留 Markdown 格式，使用清晰的 H2/H3 结构。
5. FAQ、总结或来源只在原文事实足够支持时补充；无法确认的信息应明确写成待补充，而不是虚构。
6. 正文 content 必须包含完整文章，包括 FAQ 和总结，不要只返回修改片段。

原始标题：
{article.get('title', '')}

原始摘要：
{article.get('summary', '')}

原文：
{article.get('content', '')}

原始标签：
{tag_text}

当前评分：
{score_value} / 100

优化建议：
{suggestion_text}

只返回一个 JSON 对象，不要附加解释：
{{
  "title": "",
  "summary": "",
  "content": "",
  "tags": [],
  "faq": []
}}
""".strip()


def _extract_message_content(payload: Mapping[str, Any]) -> str:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise KeyError("choices")
    first_choice = choices[0]
    if not isinstance(first_choice, Mapping):
        raise KeyError("choices[0]")
    message = first_choice.get("message")
    if not isinstance(message, Mapping):
        raise KeyError("message")
    content = message.get("content")
    if isinstance(content, str) and content.strip():
        return content.strip()
    if isinstance(content, list):
        text = "".join(
            str(part.get("text", ""))
            for part in content
            if isinstance(part, Mapping)
        ).strip()
        if text:
            return text
    raise ValueError("message.content")


def _parse_model_output(raw_content: str, fallback_tags: Sequence[str]) -> dict[str, Any]:
    candidate = _strip_code_fence(raw_content)
    parsed = _load_json_object(candidate)
    if parsed is None:
        return _fallback_result(raw_content, fallback_tags)

    content = str(parsed.get("content") or "").strip()
    if not content:
        content = raw_content.strip()
    title = str(parsed.get("title") or "").strip() or _derive_title(content)
    summary = str(parsed.get("summary") or "").strip() or _derive_summary(content)

    return {
        "title": title[:100],
        "summary": summary,
        "content": content,
        "tags": _normalize_tags(parsed.get("tags"), fallback_tags),
        "faq": _normalize_faq(parsed.get("faq")),
    }


def _strip_code_fence(value: str) -> str:
    stripped = value.strip()
    match = re.fullmatch(
        r"```(?:json)?\s*(.*?)\s*```",
        stripped,
        flags=re.DOTALL | re.IGNORECASE,
    )
    return match.group(1).strip() if match else stripped


def _load_json_object(value: str) -> dict[str, Any] | None:
    candidates = [value]
    start = value.find("{")
    end = value.rfind("}")
    if start >= 0 and end > start:
        candidates.append(value[start : end + 1])
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _fallback_result(raw_content: str, fallback_tags: Sequence[str]) -> dict[str, Any]:
    content = raw_content.strip()
    return {
        "title": _derive_title(content),
        "summary": _derive_summary(content),
        "content": content,
        "tags": _normalize_tags(None, fallback_tags),
        "faq": [],
    }


def _derive_title(content: str) -> str:
    first_line = next(
        (line.strip() for line in content.splitlines() if line.strip()),
        "AI 生成内容",
    )
    return re.sub(r"^#+\s*", "", first_line)[:100]


def _derive_summary(content: str) -> str:
    compact = re.sub(r"\s+", " ", content).strip()
    return compact[:160]


def _normalize_tags(value: Any, fallback_tags: Sequence[str]) -> list[str]:
    source = value if isinstance(value, list) else fallback_tags
    result = []
    for item in source:
        tag = str(item).strip().lstrip("#")
        if tag and tag not in result:
            result.append(tag)
    return result


def _normalize_faq(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    result = []
    for item in value:
        if isinstance(item, Mapping):
            question = str(item.get("question") or item.get("q") or "").strip()
            answer = str(item.get("answer") or item.get("a") or "").strip()
        else:
            question = str(item).strip()
            answer = ""
        if question:
            result.append({"question": question, "answer": answer})
    return result
