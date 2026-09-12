"""Deterministic AI-answer visibility analysis.

This module is a Python port of the useful, pure-analysis portion of the
``@geo/geo-engine`` hand-off package.  It deliberately performs no network
requests and has no database dependency: callers provide already captured AI
responses and project metadata, and receive auditable evidence plus versioned
metrics.

The archive digest below is part of the runtime result so every score remains
traceable to the reviewed hand-off snapshot.
"""

from __future__ import annotations

import copy
import hashlib
import ipaddress
import math
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

import idna


AI_VISIBILITY_ALGORITHM_VERSION = "ai-visibility-python-port-1.5.0"
MENTION_PARSER_VERSION = "mention-python-port-1.5.0"
RANK_PARSER_VERSION = "rank-python-port-1.1.0"
CITATION_PARSER_VERSION = "citation-inline-url-python-port-1.1.0"
CITATION_EXTRACTION_VERSION = "explicit-response-url-1.0.0"
PUBLIC_SUFFIX_RULESET_VERSION = "public-suffix-curated-python-1.1.0"
SOURCE_CLASSIFIER_VERSION = "source-classifier-python-1.1.0"
SENTIMENT_PARSER_VERSION = "sentiment-python-port-1.1.0"
METRICS_ALGORITHM_VERSION = "metrics-ad-hoc-python-1.1.0"
RECOMMENDATION_RULE_SET_VERSION = "recommendations-ad-hoc-python-1.1.0"

HANDOFF_ARCHIVE_NAME = (
    "GEO-AI-Visibility-SaaS-v3.0-dev-handoff-20260908-181903.zip"
)
HANDOFF_ARCHIVE_SHA256 = (
    "2ff20604dad3b68807c47d0f3aef3d59ce613bf927dcde0b90ad80bc4b96e96c"
)
HANDOFF_MANIFEST_SHA256 = (
    "e0c5bdc1ced83f82ce2ecf6c9f12f646885fc283b6d11e40472492ce3a0db239"
)
HANDOFF_SOURCE_FILE_SHA256 = {
    "citation.ts": "f5ae67f4c24b6d5adf656dba4b8111fc840607894900855bc0e4f16089a13fbb",
    "mention.ts": "ce0134c3a42e6dece8ce6498e591e8adec551c9dc6d426d8ffbe417de009f16a",
    "metrics.ts": "840c5d970d3a0d5993ebc800ea6fc6b61517ec2a88837d05da9bf8280ae75799",
    "perception.ts": "db12c7e8cabffd340a2f29fc757d7e2fe33e753e50816af151f9d04672a306e2",
    "rank.ts": "9ef40589cc4b76aaa6b5352ccc4e14325369722ce7cb69c9e9784692de99fcc4",
    "recommendations.ts": "1bc37fd8476fdac30b343bd0f7e3ce35b7b1b95fa6b8dc3c44b7ff9f27422b51",
}

MAX_RESPONSES = 20
MAX_RESPONSE_LENGTH = 50_000
MAX_TOTAL_RESPONSE_LENGTH = 250_000
MAX_PROMPT_LENGTH = 4_000
MAX_URL_LENGTH = 2_048
MAX_CITATIONS_PER_RESPONSE = 200
MAX_FUZZY_COMPARISONS_PER_RESPONSE = 10_000
Z_SCORE_95 = 1.959_963_984_540_054


class VisibilityAnalysisError(ValueError):
    """Raised when visibility-analysis input violates the public contract."""


def current_ai_visibility_algorithm_versions() -> dict[str, str]:
    """Return the exact local parser/metric version set used for new results.

    Keeping this in one public helper gives history readers a stable way to
    decide whether saved response text needs to be parsed again.  It performs
    no I/O and does not know how to call an AI provider.
    """

    return {
        "integration": AI_VISIBILITY_ALGORITHM_VERSION,
        "mention": MENTION_PARSER_VERSION,
        "rank": RANK_PARSER_VERSION,
        "citation": CITATION_PARSER_VERSION,
        "citation_extraction": CITATION_EXTRACTION_VERSION,
        "public_suffix_rules": PUBLIC_SUFFIX_RULESET_VERSION,
        "source_classifier": SOURCE_CLASSIFIER_VERSION,
        "sentiment": SENTIMENT_PARSER_VERSION,
        "metrics": METRICS_ALGORITHM_VERSION,
        "recommendations": RECOMMENDATION_RULE_SET_VERSION,
    }


def inspect_ai_visibility_analysis_freshness(
    analysis: Mapping[str, Any],
) -> dict[str, Any]:
    """Compare a saved result with the current deterministic implementation."""

    if not isinstance(analysis, Mapping):
        raise VisibilityAnalysisError("previous_analysis 必须是对象")
    current_versions = current_ai_visibility_algorithm_versions()
    raw_versions = analysis.get("algorithm_versions")
    stored_versions = (
        {
            str(key): str(value)
            for key, value in raw_versions.items()
            if isinstance(key, str) and isinstance(value, str)
        }
        if isinstance(raw_versions, Mapping)
        else {}
    )
    stale_components = [
        component
        for component, current_version in current_versions.items()
        if stored_versions.get(component) != current_version
    ]
    status = "current" if not stale_components else (
        "unversioned" if not stored_versions else "stale"
    )
    return {
        "status": status,
        "reanalysis_required": bool(stale_components),
        "stale_components": stale_components,
        "stored_versions": stored_versions,
        "current_versions": current_versions,
    }


@dataclass(frozen=True)
class _Entity:
    entity_id: str
    name: str
    entity_type: str
    aliases: tuple[str, ...]
    domains: tuple[str, ...]
    alias_sources: tuple[tuple[str, tuple[str, ...]], ...] = ()


@dataclass(frozen=True)
class _SearchableText:
    value: str
    start_offsets: tuple[int, ...]
    end_offsets: tuple[int, ...]


_CONTEXT_PATTERN = re.compile(
    r"(?:推荐|品牌|产品|平台|工具|公司|官网|选择|方案|"
    r"recommend(?:ed|ation)?|brand|product|platform|tool|company|vendor|"
    r"service|website|solution)",
    re.IGNORECASE,
)
_SHORT_TOKEN_BLACKLIST = {
    "ai",
    "an",
    "as",
    "at",
    "be",
    "by",
    "do",
    "go",
    "he",
    "if",
    "in",
    "is",
    "it",
    "me",
    "my",
    "no",
    "of",
    "on",
    "or",
    "so",
    "to",
    "up",
    "us",
    "we",
}
# Category aliases are classified compositionally instead of by comparing only
# a few complete phrases.  The residual algorithm below repeatedly peels these
# generic modifiers/nouns from both ends, so combinations such as
# ``企业 + 知识库`` and ``模型 + 服务 + 平台`` are rejected without enumerating
# every possible full phrase.  A remaining token is still not automatically a
# brand: request aliases must additionally be traceable to the configured
# project/product or have a conservative Latin brand shape.
_GENERIC_ENTITY_CATEGORY_FRAGMENTS = {
    # Chinese modifiers and organization/technology descriptors.
    "一站式",
    "企业级",
    "人工智能",
    "生成式",
    "大语言模型",
    "大模型",
    "私有化",
    "本地化",
    "国产化",
    "自动化",
    "智能化",
    "数字化",
    "有限责任公司",
    "股份有限公司",
    "有限公司",
    "企业",
    "公司",
    "集团",
    "科技",
    "技术",
    "行业",
    "专业",
    "通用",
    "在线",
    "国内",
    "国际",
    "全球",
    "开源",
    # Chinese category nouns and composable functional terms.
    "知识管理",
    "知识库",
    "数据库",
    "云计算",
    "云服务",
    "开发者平台",
    "开发平台",
    "开放平台",
    "管理平台",
    "服务平台",
    "模型服务",
    "中转服务",
    "中转站",
    "解决方案",
    "人工",
    "智能",
    "知识",
    "信息",
    "数据",
    "内容",
    "媒体",
    "营销",
    "开发",
    "开放",
    "管理",
    "运营",
    "发布",
    "创作",
    "生成",
    "搜索",
    "问答",
    "写作",
    "分析",
    "模型",
    "接口",
    "接入",
    "中转",
    "聚合",
    "路由",
    "网关",
    "服务",
    "平台",
    "系统",
    "软件",
    "工具",
    "方案",
    "产品",
    "应用",
    "助手",
    "引擎",
    "中心",
    "网络",
    "云",
    "与",
    "和",
    # English category words. Compact keys also catch forms such as
    # ``EnterpriseKnowledgeBase`` while prefix/suffix-only peeling avoids
    # deleting ``api`` from the middle of an unrelated name like Capital.
    "artificial intelligence",
    "generative ai",
    "large language model",
    "knowledge base",
    "cloud service",
    "developer platform",
    "development platform",
    "model service",
    "open source",
    "enterprise",
    "business",
    "knowledge",
    "database",
    "cloud",
    "service",
    "services",
    "developer",
    "development",
    "platform",
    "model",
    "models",
    "software",
    "system",
    "systems",
    "tool",
    "tools",
    "solution",
    "solutions",
    "product",
    "products",
    "application",
    "applications",
    "automation",
    "intelligent",
    "intelligence",
    "management",
    "marketing",
    "analytics",
    "analysis",
    "publishing",
    "publish",
    "content",
    "media",
    "data",
    "gateway",
    "routing",
    "router",
    "assistant",
    "engine",
    "center",
    "search",
    "writing",
    "writer",
    "agent",
    "agents",
    "global",
    "online",
    "private",
    "local",
    "smart",
    "saas",
    "llm",
    "hub",
    "app",
    "apps",
    "api",
    "ai",
}
_GENERIC_ENTITY_CATEGORY_KEYS = tuple(
    sorted(
        {
            re.sub(r"[^\w]", "", value.casefold(), flags=re.UNICODE)
            for value in _GENERIC_ENTITY_CATEGORY_FRAGMENTS
        },
        key=lambda value: (-len(value), value),
    )
)
_MATCH_PRIORITY = {"EXACT": 5, "ALIAS": 4, "DOMAIN": 3, "STRUCTURED": 2, "FUZZY": 1}
_RECOMMENDATION_PATTERN = re.compile(
    r"(?:\brecommend(?:ed|ing|ation|s)?\b|"
    r"\bbest\s+(?:choices?|options?|tools?|products?|platforms?)\b|"
    r"\btop\s+(?:choices?|picks?|options?)\b|推荐|首选|最佳|排名)",
    re.IGNORECASE,
)
_URL_PATTERN = re.compile(r"(?:(?:https?:)?//|www\.)[^\s<>\"'`]+", re.IGNORECASE)
_TRAILING_URL_PUNCTUATION = ".,;:!?，。；：！？、)]}）】》」』"
_TRACKING_PARAMETERS = {
    "_ga",
    "_gl",
    "dclid",
    "fbclid",
    "gclid",
    "gbraid",
    "igshid",
    "li_fat_id",
    "mc_cid",
    "mc_eid",
    "msclkid",
    "s_cid",
    "ttclid",
    "twclid",
    "wbraid",
    "yclid",
}

_KNOWN_PUBLIC_SUFFIXES = {
    "ac",
    "ad",
    "ae",
    "ai",
    "app",
    "ar",
    "at",
    "au",
    "be",
    "biz",
    "br",
    "ca",
    "ch",
    "cl",
    "cloud",
    "cn",
    "co",
    "com",
    "cz",
    "de",
    "dev",
    "dk",
    "edu",
    "es",
    "eu",
    "fi",
    "fr",
    "gov",
    "gr",
    "hk",
    "hu",
    "id",
    "ie",
    "il",
    "in",
    "info",
    "int",
    "io",
    "ir",
    "it",
    "jp",
    "kr",
    "me",
    "mil",
    "mx",
    "my",
    "net",
    "nl",
    "no",
    "nz",
    "online",
    "org",
    "ph",
    "pl",
    "pt",
    "ro",
    "ru",
    "sa",
    "se",
    "sg",
    "site",
    "sk",
    "tech",
    "tr",
    "tv",
    "tw",
    "uk",
    "us",
    "vn",
    "xyz",
    "za",
}
_MULTI_LABEL_PUBLIC_SUFFIXES = {
    "ac.jp",
    "ac.uk",
    "asn.au",
    "co.id",
    "co.il",
    "co.in",
    "co.jp",
    "co.kr",
    "co.nz",
    "co.uk",
    "co.za",
    "com.ar",
    "com.au",
    "com.br",
    "com.cn",
    "com.hk",
    "com.mx",
    "com.my",
    "com.ph",
    "com.sg",
    "com.tr",
    "com.tw",
    "com.vn",
    "edu.au",
    "edu.cn",
    "edu.sg",
    "gov.au",
    "gov.cn",
    "gov.uk",
    "id.au",
    "ltd.uk",
    "me.uk",
    "ne.jp",
    "net.au",
    "net.cn",
    "net.uk",
    "or.jp",
    "org.au",
    "org.cn",
    "org.uk",
    "plc.uk",
    "sch.uk",
}
_SOURCE_RULES = {
    "COMMUNITY": {
        "reddit.com",
        "quora.com",
        "stackexchange.com",
        "stackoverflow.com",
        "zhihu.com",
        "news.ycombinator.com",
    },
    "GOVERNMENT_ACADEMIC": {
        "arxiv.org",
        "doi.org",
        "jstor.org",
        "nature.com",
        "ncbi.nlm.nih.gov",
        "sciencedirect.com",
        "springer.com",
    },
    "PUBLISHER": {
        "bbc.com",
        "bloomberg.com",
        "forbes.com",
        "medium.com",
        "nytimes.com",
        "reuters.com",
        "techcrunch.com",
        "theguardian.com",
        "wsj.com",
        "wikipedia.org",
    },
    "REVIEW": {
        "capterra.com",
        "g2.com",
        "getapp.com",
        "softwareadvice.com",
        "trustpilot.com",
        "trustradius.com",
    },
    "SOCIAL": {
        "facebook.com",
        "instagram.com",
        "linkedin.com",
        "tiktok.com",
        "twitter.com",
        "weibo.com",
        "x.com",
        "youtube.com",
    },
}
_SOURCE_RULE_ORDER = (
    "GOVERNMENT_ACADEMIC",
    "REVIEW",
    "COMMUNITY",
    "SOCIAL",
    "PUBLISHER",
)


def _required_text(value: Any, field: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise VisibilityAnalysisError(f"{field} 必须是字符串")
    text = value.strip()
    if not text:
        raise VisibilityAnalysisError(f"{field} 不能为空")
    if "\0" in text or len(text) > maximum:
        raise VisibilityAnalysisError(f"{field} 超出支持的长度")
    return text


def _canonical_term(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).strip().casefold()
    return re.sub(r"\s+", " ", normalized)


def _unique_strings(
    values: Sequence[Any],
    *,
    field: str,
    maximum_items: int,
    maximum_length: int = 253,
) -> list[str]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise VisibilityAnalysisError(f"{field} 必须是字符串数组")
    if len(values) > maximum_items:
        raise VisibilityAnalysisError(f"{field} 数量不能超过 {maximum_items}")
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            raise VisibilityAnalysisError(f"{field} 必须是字符串数组")
        text = value.strip()
        if not text or "\0" in text or len(text) > maximum_length:
            raise VisibilityAnalysisError(f"{field} 包含无效字符串")
        key = _canonical_term(text)
        if key not in seen:
            seen.add(key)
            output.append(text)
    return output


def _normalize_responses(responses: Any) -> list[dict[str, Any]]:
    if not isinstance(responses, list):
        raise VisibilityAnalysisError("responses 必须是数组")
    if not responses:
        raise VisibilityAnalysisError("responses 至少需要一条回答")
    if len(responses) > MAX_RESPONSES:
        raise VisibilityAnalysisError(f"responses 最多支持 {MAX_RESPONSES} 条")
    output: list[dict[str, Any]] = []
    total_length = 0
    for index, item in enumerate(responses):
        provider: str | None = None
        model: str | None = None
        channel_type: str | None = None
        entry_point: str | None = None
        web_search_verified: bool | None = None
        consumer_surface_verified: bool | None = None
        if isinstance(item, str):
            text = item
        elif isinstance(item, Mapping):
            unsupported = set(item) - {
                "text",
                "provider",
                "model",
                "channel_type",
                "entry_point",
                "web_search_verified",
                "consumer_surface_verified",
            }
            if unsupported:
                raise VisibilityAnalysisError(
                    f"responses[{index}] 包含不支持的字段: {', '.join(sorted(unsupported))}"
                )
            text = item.get("text")
            for field in ("provider", "model", "channel_type", "entry_point"):
                value = item.get(field)
                if value is not None:
                    normalized = _required_text(
                        value, f"responses[{index}].{field}", 120
                    )
                    if field == "provider":
                        provider = normalized
                    elif field == "model":
                        model = normalized
                    elif field == "channel_type":
                        channel_type = normalized
                    else:
                        entry_point = normalized
            for field in ("web_search_verified", "consumer_surface_verified"):
                value = item.get(field)
                if value is not None and not isinstance(value, bool):
                    raise VisibilityAnalysisError(
                        f"responses[{index}].{field} 必须是布尔值"
                    )
                if field == "web_search_verified":
                    web_search_verified = value
                else:
                    consumer_surface_verified = value
        else:
            raise VisibilityAnalysisError(
                f"responses[{index}] 必须是字符串或包含 text 的对象"
            )
        normalized_text = _required_text(
            text, f"responses[{index}].text", MAX_RESPONSE_LENGTH
        )
        total_length += len(normalized_text)
        if total_length > MAX_TOTAL_RESPONSE_LENGTH:
            raise VisibilityAnalysisError("responses 总长度超出支持范围")
        output.append(
            {
                "id": f"sample-{index + 1}",
                "index": index + 1,
                "text": normalized_text,
                "provider": provider,
                "model": model,
                "channel_type": channel_type,
                "entry_point": entry_point,
                "web_search_verified": web_search_verified,
                "consumer_surface_verified": consumer_surface_verified,
            }
        )
    return output


def _looks_like_domain(value: str) -> bool:
    candidate = value.strip()
    return (
        "." in candidate
        and not re.search(r"\s", candidate)
        and len(candidate) <= MAX_URL_LENGTH
    )


def _normalize_domain_reference(value: str) -> str:
    candidate = value.strip()
    if not candidate:
        raise VisibilityAnalysisError("域名不能为空")
    if "://" not in candidate:
        candidate = f"https://{candidate}"
    try:
        parsed = urlsplit(candidate)
        hostname = parsed.hostname
        if (
            parsed.scheme.casefold() not in {"http", "https"}
            or hostname is None
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ValueError
        normalized = _ascii_hostname(hostname)
        # A project configured as ``www.example.com`` owns sibling hosts such
        # as ``docs.example.com`` as well.  The hand-off mention normalizer also
        # removes this presentation-only prefix from tracked domains.
        return normalized[4:] if normalized.startswith("www.") else normalized
    except (UnicodeError, ValueError) as exc:
        raise VisibilityAnalysisError("域名或网址格式无效") from exc


def _parse_competitors(values: Sequence[Any]) -> list[dict[str, Any]]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise VisibilityAnalysisError("competitors 必须是字符串或对象数组")
    if len(values) > 50:
        raise VisibilityAnalysisError("competitors 数量不能超过 50")
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(values):
        aliases: list[str] = []
        domains: list[str] = []
        if isinstance(item, str):
            name = _required_text(item, f"competitors[{index}]", 160)
            if _looks_like_domain(name):
                try:
                    domains.append(_normalize_domain_reference(name))
                except VisibilityAnalysisError:
                    pass
        elif isinstance(item, Mapping):
            unsupported = set(item) - {"name", "aliases", "domains"}
            if unsupported:
                raise VisibilityAnalysisError(
                    f"competitors[{index}] 包含不支持的字段: "
                    f"{', '.join(sorted(unsupported))}"
                )
            name = _required_text(item.get("name"), f"competitors[{index}].name", 160)
            aliases = _unique_strings(
                item.get("aliases", []),
                field=f"competitors[{index}].aliases",
                maximum_items=100,
                maximum_length=160,
            )
            aliases = [
                alias
                for alias in aliases
                if _request_alias_is_admissible(
                    alias,
                    brand_name=name,
                    project_product="",
                )
            ]
            raw_domains = _unique_strings(
                item.get("domains", []),
                field=f"competitors[{index}].domains",
                maximum_items=20,
            )
            domains = [_normalize_domain_reference(value) for value in raw_domains]
        else:
            raise VisibilityAnalysisError(
                f"competitors[{index}] 必须是字符串或对象"
            )
        key = _canonical_term(name)
        if key in seen:
            continue
        seen.add(key)
        output.append({"name": name, "aliases": aliases, "domains": domains})
    return output


def _compact_entity_term(value: str) -> str:
    return re.sub(r"[^\w]", "", _canonical_term(value), flags=re.UNICODE)


def _entity_term_residual(value: str) -> str:
    """Return the non-category residue after conservative edge peeling."""

    residual = _compact_entity_term(value)
    previous = None
    while residual and residual != previous:
        previous = residual
        for fragment in _GENERIC_ENTITY_CATEGORY_KEYS:
            if residual == fragment:
                return ""
            if residual.startswith(fragment):
                residual = residual[len(fragment) :]
                break
            if residual.endswith(fragment):
                residual = residual[: -len(fragment)]
                break
    return residual


def _is_distinctive_entity_term(value: str) -> bool:
    compact = _compact_entity_term(value)
    residual = _entity_term_residual(value)
    if not compact or not residual:
        return False
    cjk_count = sum("\u3400" <= character <= "\u9fff" for character in residual)
    ascii_count = sum(
        character.isascii() and character.isalnum() for character in residual
    )
    original_cjk_count = sum(
        "\u3400" <= character <= "\u9fff" for character in compact
    )
    original_ascii_count = sum(
        character.isascii() and character.isalnum() for character in compact
    )
    if cjk_count >= 2:
        return True
    if (
        original_cjk_count >= 1
        and original_ascii_count >= 2
        and (cjk_count + ascii_count) >= 1
    ):
        return True
    return cjk_count == 0 and ascii_count >= 4


def _request_alias_is_admissible(
    value: str,
    *,
    brand_name: str,
    project_product: str,
) -> bool:
    """Admit explicit aliases only with entity evidence or brand morphology.

    Chinese and mixed-script aliases must be derivable from the configured
    company/project name or product.  An unanchored Latin trade name remains
    possible, but only when it looks like an identifier (all caps, CamelCase,
    or contains a digit) instead of ordinary prose.  This is intentionally
    conservative because an alias is used as scoring evidence, not merely as
    a search keyword.
    """

    if not _is_distinctive_entity_term(value):
        return False
    alias_key = _compact_entity_term(value)
    residual = _entity_term_residual(value)
    known_keys = [_compact_entity_term(brand_name)]
    # A product may anchor a nickname only if the product itself has already
    # passed the category-residual gate.  Otherwise a generic configured
    # product such as ``企业知识库`` could be wrapped in another generic phrase
    # and smuggled back in as an explicit alias.
    if project_product and _is_distinctive_entity_term(project_product):
        known_keys.append(_compact_entity_term(project_product))
    known_keys = tuple(key for key in known_keys if key)
    directly_anchored = any(alias_key in key or key in alias_key for key in known_keys)
    residual_anchor = (
        len(residual) >= 3
        and any(residual in key for key in known_keys)
    )
    if directly_anchored or residual_anchor:
        return True

    normalized = unicodedata.normalize("NFKC", value).strip()
    if any(_is_cjk_character(character) for character in normalized):
        return False
    if re.search(r"\s", normalized):
        return False
    ascii_letters = [character for character in normalized if character.isascii() and character.isalpha()]
    uppercase_count = sum(character.isupper() for character in ascii_letters)
    lowercase_count = sum(character.islower() for character in ascii_letters)
    return bool(ascii_letters) and (
        any(character.isdigit() for character in normalized)
        or (uppercase_count >= 3 and lowercase_count == 0)
        or (uppercase_count >= 2 and lowercase_count >= 1)
    )


def _project_keyword_aliases(
    *,
    brand_name: str,
    project_product: str,
    project_keywords: Sequence[Any],
) -> list[str]:
    """Keep only project keywords that carry a known entity anchor.

    A keyword is not a brand alias merely because it is a GEO target.  A
    candidate must be a distinctive substring of the legal/project name, or
    an exact distinctive token already present in the configured product
    name.  Longer keywords containing such a confirmed name anchor are also
    retained.  This admits ``同于野`` and ``同于野AI中转站`` while rejecting
    generic targets such as ``AI API中转站`` and ``AI模型接口``.
    """

    normalized_keywords = _unique_strings(
        project_keywords,
        field="项目关键词",
        maximum_items=100,
        maximum_length=160,
    )
    brand_key = _compact_entity_term(brand_name)
    product_tokens = {
        _compact_entity_term(token)
        for token in re.split(r"[\s/|｜·:：,，;；()（）\[\]【】_-]+", project_product)
        if _is_distinctive_entity_term(token)
    }
    confirmed_name_anchors = {
        _compact_entity_term(keyword)
        for keyword in normalized_keywords
        if _is_distinctive_entity_term(keyword)
        and _compact_entity_term(keyword) in brand_key
    }

    output: list[str] = []
    for keyword in normalized_keywords:
        if not _is_distinctive_entity_term(keyword):
            continue
        keyword_key = _compact_entity_term(keyword)
        carries_name_anchor = any(
            anchor and anchor in keyword_key for anchor in confirmed_name_anchors
        )
        is_product_token = keyword_key in product_tokens
        if carries_name_anchor or is_product_token:
            output.append(keyword)
    return output


def _build_entities(
    *,
    project_name: str,
    project_product: str,
    project_keywords: Sequence[Any],
    project_website: str,
    aliases: Sequence[Any] | None,
    competitors: Sequence[Any],
) -> tuple[list[_Entity], list[str]]:
    brand_name = _required_text(project_name, "项目名称", 160)
    warnings: list[str] = []
    alias_records: dict[str, dict[str, Any]] = {}

    def add_alias(value: str, source: str) -> None:
        key = _canonical_term(value)
        if not key or key == _canonical_term(brand_name):
            return
        record = alias_records.setdefault(key, {"term": value, "sources": []})
        if source not in record["sources"]:
            record["sources"].append(source)

    if project_product and _canonical_term(project_product) != _canonical_term(brand_name):
        normalized_product = _required_text(project_product, "项目产品", 160)
        if _is_distinctive_entity_term(normalized_product):
            add_alias(normalized_product, "project_product")
        else:
            warnings.append("项目产品仅包含通用品类词，不作为品牌命中别名。")
    for keyword in _project_keyword_aliases(
        brand_name=brand_name,
        project_product=project_product,
        project_keywords=project_keywords,
    ):
        add_alias(keyword, "project_keyword")
    if aliases is not None:
        ignored_request_alias_count = 0
        for alias in _unique_strings(
            aliases,
            field="aliases",
            maximum_items=100,
            maximum_length=160,
        ):
            # Request aliases are useful for real nicknames that do not occur
            # in the legal name (for example an English product name), so they
            # cannot require the project-keyword name anchor.  They must still
            # pass the same generic/distinctive gate, otherwise a caller could
            # turn a category word such as ``中转站`` into a brand mention in
            # every ordinary answer about that category.
            if _request_alias_is_admissible(
                alias,
                brand_name=brand_name,
                project_product=project_product,
            ):
                add_alias(alias, "request_alias")
            else:
                ignored_request_alias_count += 1
        if ignored_request_alias_count:
            warnings.append(
                f"已忽略 {ignored_request_alias_count} 个缺少可辨识品牌锚点的显式别名。"
            )
    brand_aliases = [record["term"] for record in alias_records.values()]
    brand_alias_sources = tuple(
        (record["term"], tuple(record["sources"]))
        for record in alias_records.values()
    )

    owned_domains: list[str] = []
    if project_website.strip():
        try:
            owned_domains.append(_normalize_domain_reference(project_website))
        except VisibilityAnalysisError:
            warnings.append("项目官网格式无法识别，引用来源不会标记为自有域名。")

    parsed_competitors = _parse_competitors(competitors)
    entities = [
        _Entity(
            entity_id="brand",
            name=brand_name,
            entity_type="brand",
            aliases=tuple(brand_aliases),
            domains=tuple(owned_domains),
            alias_sources=brand_alias_sources,
        )
    ]
    for index, competitor in enumerate(parsed_competitors):
        entities.append(
            _Entity(
                entity_id=f"competitor-{index + 1}",
                name=competitor["name"],
                entity_type="competitor",
                aliases=tuple(competitor["aliases"]),
                domains=tuple(competitor["domains"]),
                alias_sources=tuple(
                    (alias, ("competitor_alias",))
                    for alias in competitor["aliases"]
                ),
            )
        )
    lexical_term_count = sum(1 + len(entity.aliases) for entity in entities)
    domain_count = sum(len(entity.domains) for entity in entities)
    if lexical_term_count > 300:
        raise VisibilityAnalysisError("品牌与竞品名称/别名合计不能超过 300 个")
    if domain_count > 100:
        raise VisibilityAnalysisError("品牌与竞品域名合计不能超过 100 个")
    return entities, warnings


def _dedupe_terms(values: Iterable[str], *, excluded: set[str] | None = None) -> list[str]:
    output: list[str] = []
    seen = set(excluded or ())
    for value in values:
        key = _canonical_term(value)
        if key and key not in seen:
            seen.add(key)
            output.append(value)
    return output


def _searchable_text(value: str) -> _SearchableText:
    normalized: list[str] = []
    starts: list[int] = []
    ends: list[int] = []
    for index, character in enumerate(value):
        compatible = unicodedata.normalize("NFKC", character)
        normalized.append(compatible)
        starts.extend([index] * len(compatible))
        ends.extend([index + 1] * len(compatible))
    return _SearchableText("".join(normalized), tuple(starts), tuple(ends))


def _utf16_offsets(value: str) -> tuple[int, ...]:
    """Map Python code-point indexes to JavaScript UTF-16 code-unit indexes."""

    output = [0]
    total = 0
    for character in value:
        total += 2 if ord(character) > 0xFFFF else 1
        output.append(total)
    return tuple(output)


def _word_character(value: str) -> bool:
    return bool(value) and (value.isalnum() or value == "_")


def _is_cjk_character(value: str) -> bool:
    return bool(value) and "\u3400" <= value <= "\u9fff"


def _allows_cjk_brand_compound_boundary(
    surface: str,
    adjacent: str,
) -> bool:
    """Allow a distinctive CJK alias directly joined to an ASCII product tag.

    JavaScript-style word boundaries treat CJK and ASCII letters alike, so a
    configured alias such as ``同于野`` was incorrectly rejected inside
    ``同于野AI API中转站``.  The exemption is deliberately one-way and narrow:
    the complete matched surface must contain at least three CJK characters,
    and the adjacent character must be ASCII alphanumeric.  CJK-in-CJK
    substrings and short/generic aliases remain blocked.
    """

    compact = re.sub(r"\s+", "", surface)
    return (
        len(compact) >= 3
        and all(_is_cjk_character(character) for character in compact)
        and bool(adjacent)
        and adjacent.isascii()
        and adjacent.isalnum()
    )


def _allows_cjk_sentence_boundary(
    surface: str,
    before: str,
    after: str,
) -> bool:
    """Handle normal Chinese prose without admitting embedded short aliases.

    Chinese does not delimit words with spaces.  A three-character alias at a
    sentence edge may therefore be followed by ``是``/``提供`` (or preceded by
    ordinary prose) and still be an exact entity mention.  It is unsafe when
    both sides are Chinese, as in ``不同于野外``.  A long legal name is
    sufficiently distinctive to allow Chinese prose on both sides.
    """

    compact = re.sub(r"\s+", "", surface)
    if len(compact) < 3 or not all(
        _is_cjk_character(character) for character in compact
    ):
        return False
    left_cjk = _is_cjk_character(before)
    right_cjk = _is_cjk_character(after)
    if len(compact) >= 6:
        return left_cjk or right_cjk
    if left_cjk == right_cjk:
        return False
    other_side = after if left_cjk else before
    return not _word_character(other_side)


def _context_around(text: str, start: int, end: int) -> str:
    return text[max(0, start - 80) : min(len(text), end + 80)]


def _evidence_quote(text: str, start: int, end: int) -> str:
    return text[max(0, start - 120) : min(len(text), end + 120)][:800]


def _literal_matches(
    text: str,
    searchable: _SearchableText,
    term: str,
    *,
    lexical: bool,
) -> list[tuple[int, int, str]]:
    normalized_term = unicodedata.normalize("NFKC", term).strip()
    parts = re.split(r"\s+", normalized_term)
    expression = re.compile(r"\s+".join(re.escape(part) for part in parts), re.IGNORECASE)
    comparison_term = re.sub(r"\s+", " ", normalized_term).lower()
    matches: list[tuple[int, int, str]] = []
    for match in expression.finditer(searchable.value):
        normalized_start, normalized_end = match.span()
        if normalized_end <= normalized_start:
            continue
        try:
            start = searchable.start_offsets[normalized_start]
            end = searchable.end_offsets[normalized_end - 1]
        except IndexError:
            continue
        surface = text[start:end]
        # Python's Unicode IGNORECASE treats a few characters (notably the
        # Turkish dotless i) as ASCII I variants while JavaScript /iu does not.
        # Keep the regex for offset discovery, then reject those broader-only
        # matches with a conservative lower-case literal comparison.
        comparison_surface = re.sub(
            r"\s+",
            " ",
            unicodedata.normalize("NFKC", surface).strip(),
        ).lower()
        if comparison_surface != comparison_term:
            continue
        if lexical:
            before = text[start - 1] if start else ""
            after = text[end] if end < len(text) else ""
            left_conflict = _word_character(surface[:1]) and _word_character(before)
            right_conflict = _word_character(surface[-1:]) and _word_character(after)
            left_allowed = not left_conflict or _allows_cjk_brand_compound_boundary(
                surface, before
            )
            right_allowed = not right_conflict or _allows_cjk_brand_compound_boundary(
                surface, after
            )
            if not (left_allowed and right_allowed) and not _allows_cjk_sentence_boundary(
                surface,
                before,
                after,
            ):
                continue
            compact = re.sub(r"[^\w]", "", _canonical_term(term), flags=re.UNICODE)
            if (
                (len(compact) <= 2 or compact in _SHORT_TOKEN_BLACKLIST)
                and not _CONTEXT_PATTERN.search(_context_around(text, start, end))
            ):
                continue
        matches.append((start, end, surface))
    return matches


def _candidate(
    *,
    entity: _Entity,
    text: str,
    match_type: str,
    start: int,
    end: int,
    surface: str,
    confidence: float,
    ambiguous: bool,
    utf16_offsets: Sequence[int],
    matched_term: str,
    matched_term_sources: Sequence[str],
) -> dict[str, Any]:
    effective_confidence = min(confidence, 0.45) if ambiguous else confidence
    return {
        "ambiguous": ambiguous,
        "confidence": effective_confidence,
        "entity_id": entity.entity_id,
        "entity_name": entity.name,
        "entity_type": entity.entity_type,
        "evidence_quote": _evidence_quote(text, start, end),
        "is_valid": True,
        "match_type": match_type,
        "matched_term": matched_term,
        "matched_term_sources": list(matched_term_sources),
        "needs_review": ambiguous or effective_confidence < 0.75,
        # Public spans retain the source engine's JavaScript UTF-16 semantics.
        "span_end": utf16_offsets[end],
        "span_start": utf16_offsets[start],
        "surface_text": surface,
        # Python code-point positions stay internal for slicing and rule joins.
        "_span_end_index": end,
        "_span_start_index": start,
    }


def _levenshtein(left: str, right: str) -> int:
    if left == right:
        return 0
    if len(left) < len(right):
        left, right = right, left
    previous = list(range(len(right) + 1))
    for left_index, left_character in enumerate(left, start=1):
        current = [left_index]
        for right_index, right_character in enumerate(right, start=1):
            current.append(
                min(
                    current[right_index - 1] + 1,
                    previous[right_index] + 1,
                    previous[right_index - 1]
                    + (left_character != right_character),
                )
            )
        previous = current
    return previous[-1]


def _entity_term_sources(entity: _Entity, term: str) -> tuple[str, ...]:
    if _canonical_term(term) == _canonical_term(entity.name):
        return ("project_name",) if entity.entity_type == "brand" else ("competitor_name",)
    for alias, sources in entity.alias_sources:
        if _canonical_term(alias) == _canonical_term(term):
            return tuple(sources)
    return ("configured_alias",)


def _detect_mentions(
    text: str, entities: Sequence[_Entity]
) -> tuple[list[dict[str, Any]], bool]:
    searchable = _searchable_text(text)
    utf16_offsets = _utf16_offsets(text)
    term_owners: dict[str, set[str]] = {}
    for entity in entities:
        for term in (entity.name, *entity.aliases):
            term_owners.setdefault(_canonical_term(term), set()).add(entity.entity_id)

    candidates: list[dict[str, Any]] = []
    for entity in entities:
        lexical_terms = [
            (entity.name, "EXACT", 0.99, _entity_term_sources(entity, entity.name))
        ] + [
            (
                alias,
                "ALIAS",
                0.92,
                _entity_term_sources(entity, alias),
            )
            for alias in entity.aliases
        ]
        for term, match_type, confidence, term_sources in lexical_terms:
            ambiguous = len(term_owners.get(_canonical_term(term), ())) > 1
            for start, end, surface in _literal_matches(
                text, searchable, term, lexical=True
            ):
                candidates.append(
                    _candidate(
                        entity=entity,
                        text=text,
                        match_type=match_type,
                        start=start,
                        end=end,
                        surface=surface,
                        confidence=confidence,
                        ambiguous=ambiguous,
                        utf16_offsets=utf16_offsets,
                        matched_term=term,
                        matched_term_sources=term_sources,
                    )
                )
        for domain in entity.domains:
            for start, end, surface in _literal_matches(
                text, searchable, domain, lexical=False
            ):
                before = text[start - 1] if start else ""
                after = text[end] if end < len(text) else ""
                if re.match(r"[\w-]", before, re.UNICODE) or re.match(
                    r"[\w-]", after, re.UNICODE
                ):
                    continue
                candidates.append(
                    _candidate(
                        entity=entity,
                        text=text,
                        match_type="DOMAIN",
                        start=start,
                        end=end,
                        surface=surface,
                        confidence=0.98,
                        ambiguous=False,
                        utf16_offsets=utf16_offsets,
                        matched_term=domain,
                        matched_term_sources=(
                            ("project_website",)
                            if entity.entity_type == "brand"
                            else ("competitor_domain",)
                        ),
                    )
                )

    fuzzy_comparisons = 0
    fuzzy_scan_limited = False
    for token in re.finditer(r"[^\W_][\w._-]*", text, re.UNICODE):
        surface = token.group(0)
        start, end = token.span()
        if not _CONTEXT_PATTERN.search(_context_around(text, start, end)):
            continue
        normalized_surface = _canonical_term(surface)
        if len(normalized_surface) < 5:
            continue
        fuzzy_owners: list[tuple[_Entity, float]] = []
        for entity in entities:
            for term in (entity.name, *entity.aliases):
                normalized_term = _canonical_term(term)
                if len(normalized_term) < 4 or " " in normalized_term:
                    continue
                distance_limit = max(1, math.floor(len(normalized_term) * 0.2))
                if abs(len(normalized_surface) - len(normalized_term)) > distance_limit:
                    continue
                if fuzzy_comparisons >= MAX_FUZZY_COMPARISONS_PER_RESPONSE:
                    fuzzy_scan_limited = True
                    break
                fuzzy_comparisons += 1
                distance = _levenshtein(normalized_surface, normalized_term)
                similarity = 1 - distance / max(
                    len(normalized_surface), len(normalized_term)
                )
                if distance <= distance_limit and similarity >= 0.8:
                    fuzzy_owners.append((entity, similarity))
                    break
            if fuzzy_scan_limited:
                break
        if fuzzy_scan_limited:
            break
        owner_ids = {entity.entity_id for entity, _ in fuzzy_owners}
        for entity, similarity in fuzzy_owners:
            closest_terms = [
                term
                for term in (entity.name, *entity.aliases)
                if abs(len(normalized_surface) - len(_canonical_term(term)))
                <= max(1, math.floor(len(_canonical_term(term)) * 0.2))
            ]
            matched_term = min(
                closest_terms or [entity.name],
                key=lambda term: _levenshtein(
                    normalized_surface, _canonical_term(term)
                ),
            )
            matched_sources = (
                _entity_term_sources(entity, matched_term)
            )
            candidates.append(
                _candidate(
                    entity=entity,
                    text=text,
                    match_type="FUZZY",
                    start=start,
                    end=end,
                    surface=surface,
                    confidence=min(0.79, similarity * 0.82),
                    ambiguous=len(owner_ids) > 1,
                    utf16_offsets=utf16_offsets,
                    matched_term=matched_term,
                    matched_term_sources=matched_sources,
                )
            )

    grouped: dict[str, dict[tuple[int, int], dict[str, Any]]] = {}
    for item in candidates:
        span = (item["_span_start_index"], item["_span_end_index"])
        current = grouped.setdefault(item["entity_id"], {}).get(span)
        if current is None or (
            _MATCH_PRIORITY[item["match_type"]], item["confidence"]
        ) > (_MATCH_PRIORITY[current["match_type"]], current["confidence"]):
            grouped[item["entity_id"]][span] = item

    results: list[dict[str, Any]] = []
    for spans in grouped.values():
        values = list(spans.values())
        strongest = sorted(
            values,
            key=lambda item: (
                -_MATCH_PRIORITY[item["match_type"]],
                -item["confidence"],
                item["_span_start_index"],
            ),
        )[0]
        results.append(
            {
                **strongest,
                "matched_surfaces": [
                    item["surface_text"]
                    for item in sorted(
                        values, key=lambda value: value["_span_start_index"]
                    )
                ],
                "matched_terms": [
                    {
                        "match_type": item["match_type"],
                        "sources": list(item["matched_term_sources"]),
                        "term": item["matched_term"],
                    }
                    for item in sorted(
                        {
                            (
                                _canonical_term(item["matched_term"]),
                                tuple(item["matched_term_sources"]),
                                item["match_type"],
                            ): item
                            for item in values
                        }.values(),
                        key=lambda value: (
                            -_MATCH_PRIORITY[value["match_type"]],
                            _canonical_term(value["matched_term"]),
                        ),
                    )
                ],
                "occurrence_count": len(spans),
                "parser_version": MENTION_PARSER_VERSION,
            }
        )
    return (
        sorted(
            results,
            key=lambda item: (item["_span_start_index"], item["entity_id"]),
        ),
        fuzzy_scan_limited,
    )


def _list_items(text: str) -> list[dict[str, int]]:
    numbered = list(
        re.finditer(r"(?:^|[\s\n\r])(?P<rank>\d{1,3})[.)、]\s*", text)
    )
    if len(numbered) >= 2:
        ranks = [int(match.group("rank")) for match in numbered]
        if len(set(ranks)) == len(ranks) and all(1 <= rank <= 100 for rank in ranks):
            return [
                {
                    "start": match.end(),
                    "end": numbered[index + 1].start()
                    if index + 1 < len(numbered)
                    else len(text),
                    "rank": ranks[index],
                }
                for index, match in enumerate(numbered)
            ]
    bullets = list(re.finditer(r"(?:^|\r?\n)[\t ]*[-*•]\s+", text))
    if len(bullets) < 2:
        return []
    return [
        {
            "start": match.end(),
            "end": bullets[index + 1].start()
            if index + 1 < len(bullets)
            else len(text),
            "rank": index + 1,
        }
        for index, match in enumerate(bullets)
    ]


def _extract_ranks(text: str, mentions: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    valid_mentions = sorted(
        (item for item in mentions if item["is_valid"]),
        key=lambda item: item["_span_start_index"],
    )
    if not valid_mentions:
        return []
    items = _list_items(text)
    structured: dict[str, tuple[int, float]] = {}
    sequential = all(item["rank"] == index + 1 for index, item in enumerate(items))
    for item in items:
        for mention in valid_mentions:
            if item["start"] <= mention["_span_start_index"] < item["end"]:
                existing = structured.get(mention["entity_id"])
                if existing is None or item["rank"] < existing[0]:
                    structured[mention["entity_id"]] = (
                        item["rank"],
                        0.96 if sequential else 0.82,
                    )
    if items:
        method = "STRUCTURED_LIST"
    elif _RECOMMENDATION_PATTERN.search(text) and len(valid_mentions) >= 2:
        method = "RECOMMENDATION_ORDER"
        for index, mention in enumerate(valid_mentions):
            structured.setdefault(mention["entity_id"], (index + 1, 0.74))
    else:
        method = "MENTION_POSITION_ONLY"
    output = []
    for mention in valid_mentions:
        ranked = structured.get(mention["entity_id"])
        output.append(
            {
                "confidence": ranked[1]
                if ranked is not None
                else min(0.99, mention["confidence"]),
                "entity_id": mention["entity_id"],
                "entity_name": mention["entity_name"],
                "mention_position": mention["span_start"],
                "method": method if ranked is not None else "MENTION_POSITION_ONLY",
                "parser_version": RANK_PARSER_VERSION,
                "rank": ranked[0] if ranked is not None else None,
            }
        )
    return sorted(
        output,
        key=lambda item: (
            item["rank"] if item["rank"] is not None else math.inf,
            item["mention_position"],
        ),
    )


def _ascii_hostname(hostname: str) -> str:
    # Do not casefold before UTS #46 conversion: casefolding maps ``ß`` to
    # ``ss`` and would split one internationalized host into two identities.
    value = hostname.rstrip(".").lower()
    if not value or len(value) > 253 or any(ord(char) < 32 for char in value):
        raise ValueError("invalid hostname")
    try:
        normalized = idna.encode(
            value,
            uts46=True,
            std3_rules=True,
        ).decode("ascii").casefold()
    except idna.IDNAError as exc:
        raise ValueError("invalid hostname") from exc
    try:
        ipaddress.ip_address(normalized)
    except ValueError:
        label_pattern = re.compile(
            r"(?:xn--)?[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?",
            re.IGNORECASE,
        )
        labels = normalized.split(".")
        if any(not label_pattern.fullmatch(label) for label in labels):
            raise ValueError("invalid hostname")
    return normalized


def _registrable_domain(hostname: str) -> dict[str, Any]:
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        return {
            "status": "UNKNOWN",
            "reason": "IP_ADDRESS",
            "public_suffix": None,
            "registrable_domain": None,
            "resolver_version": PUBLIC_SUFFIX_RULESET_VERSION,
        }
    labels = hostname.split(".")
    if len(labels) == 1:
        return {
            "status": "UNKNOWN",
            "reason": "SINGLE_LABEL_HOST",
            "public_suffix": None,
            "registrable_domain": None,
            "resolver_version": PUBLIC_SUFFIX_RULESET_VERSION,
        }
    last_two = ".".join(labels[-2:])
    if last_two in _MULTI_LABEL_PUBLIC_SUFFIXES:
        suffix = last_two
        suffix_labels = 2
    elif labels[-1] in _KNOWN_PUBLIC_SUFFIXES:
        suffix = labels[-1]
        suffix_labels = 1
    else:
        return {
            "status": "UNKNOWN",
            "reason": "UNSUPPORTED_PUBLIC_SUFFIX",
            "public_suffix": None,
            "registrable_domain": None,
            "resolver_version": PUBLIC_SUFFIX_RULESET_VERSION,
        }
    if len(labels) <= suffix_labels:
        return {
            "status": "UNKNOWN",
            "reason": "PUBLIC_SUFFIX_ONLY",
            "public_suffix": suffix,
            "registrable_domain": None,
            "resolver_version": PUBLIC_SUFFIX_RULESET_VERSION,
        }
    return {
        "status": "RESOLVED",
        "reason": None,
        "public_suffix": suffix,
        "registrable_domain": ".".join(labels[-(suffix_labels + 1) :]),
        "resolver_version": PUBLIC_SUFFIX_RULESET_VERSION,
    }


def normalize_citation_url(raw_url: str) -> dict[str, Any]:
    """Normalize one explicit HTTP(S) citation without resolving the network."""

    if not isinstance(raw_url, str):
        raise VisibilityAnalysisError("引用 URL 必须是字符串")
    candidate = raw_url.strip()
    if not candidate or len(candidate) > MAX_URL_LENGTH or "\0" in candidate:
        raise VisibilityAnalysisError("引用 URL 超出支持范围")
    if candidate.startswith("//"):
        candidate = f"https:{candidate}"
    elif not re.match(r"^[a-z][a-z\d+.-]*://", candidate, re.IGNORECASE):
        candidate = f"https://{candidate}"
    try:
        parsed = urlsplit(candidate)
        if parsed.scheme.casefold() not in {"http", "https"}:
            raise ValueError("scheme")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("credentials")
        if parsed.hostname is None:
            raise ValueError("hostname")
        hostname = _ascii_hostname(parsed.hostname)
        port = parsed.port
    except (UnicodeError, ValueError) as exc:
        raise VisibilityAnalysisError("引用 URL 格式无效或不安全") from exc

    scheme = parsed.scheme.casefold()
    if (scheme == "https" and port == 443) or (scheme == "http" and port == 80):
        port = None
    host_for_url = f"[{hostname}]" if ":" in hostname else hostname
    netloc = host_for_url if port is None else f"{host_for_url}:{port}"
    removed: list[str] = []
    retained: list[tuple[str, str]] = []
    for name, value in parse_qsl(parsed.query, keep_blank_values=True):
        normalized_name = name.casefold()
        if normalized_name.startswith("utm_") or normalized_name in _TRACKING_PARAMETERS:
            removed.append(name)
        else:
            retained.append((name, value))
    # Preserve already escaped reserved octets (notably ``%2F``).  Decoding
    # before quoting would collapse ``/a%2Fb`` and ``/a/b`` into one identity.
    path = quote(parsed.path or "/", safe="%/:@-._~!$&'()*+,;=")
    query = urlencode(retained, doseq=True)
    canonical = urlunsplit((scheme, netloc, path, query, ""))
    resolution = _registrable_domain(hostname)
    registrable = resolution["registrable_domain"]
    try:
        unicode_domain = hostname.encode("ascii").decode("idna")
    except (UnicodeError, ValueError):
        unicode_domain = hostname
    if registrable is None:
        unicode_registrable = None
    else:
        try:
            unicode_registrable = registrable.encode("ascii").decode("idna")
        except (UnicodeError, ValueError):
            unicode_registrable = registrable
    return {
        "canonical_url": canonical,
        "domain": hostname,
        "domain_resolution": resolution,
        "path_depth": len([segment for segment in parsed.path.split("/") if segment]),
        "port": port,
        "raw_url": raw_url,
        "registrable_domain": registrable,
        "removed_tracking_parameters": removed,
        "scheme": scheme,
        "unicode_domain": unicode_domain,
        "unicode_registrable_domain": unicode_registrable,
        "url_hash": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    }


def _domain_matches(hostname: str, expected: str) -> bool:
    return hostname == expected or hostname.endswith(f".{expected}")


def classify_citation_source(
    *,
    domain: str,
    registrable_domain: str | None = None,
    owned_domains: Sequence[str] = (),
    competitor_domains: Sequence[str] = (),
) -> dict[str, Any]:
    """Classify a citation using explicit ownership before heuristic rules."""

    try:
        hostname = _ascii_hostname(domain)
        normalized_registrable = (
            _ascii_hostname(registrable_domain)
            if registrable_domain is not None
            else None
        )
        normalized_owned = tuple(_ascii_hostname(value) for value in owned_domains)
        normalized_competitors = tuple(
            _ascii_hostname(value) for value in competitor_domains
        )
    except (TypeError, ValueError) as exc:
        raise VisibilityAnalysisError("引用来源域名格式无效") from exc
    if normalized_registrable is not None and not _domain_matches(
        hostname, normalized_registrable
    ):
        raise VisibilityAnalysisError("registrable_domain 不是引用域名的父域")
    domains_to_match = (
        (hostname, normalized_registrable)
        if normalized_registrable
        else (hostname,)
    )

    def matches(candidates: Sequence[str]) -> bool:
        return any(
            _domain_matches(candidate, expected)
            for candidate in domains_to_match
            for expected in candidates
        )

    owned_match = matches(normalized_owned)
    competitor_match = matches(normalized_competitors)
    if owned_match and competitor_match:
        raise VisibilityAnalysisError("同一引用域名不能同时属于品牌和竞品")
    if owned_match:
        return {
            "classifier_version": SOURCE_CLASSIFIER_VERSION,
            "is_competitor": False,
            "is_owned": True,
            "rationale": "OWNED_DOMAIN",
            "source_type": "OWNED",
        }
    if competitor_match:
        return {
            "classifier_version": SOURCE_CLASSIFIER_VERSION,
            "is_competitor": True,
            "is_owned": False,
            "rationale": "COMPETITOR_DOMAIN",
            "source_type": "COMPETITOR_OWNED",
        }
    for source_type in _SOURCE_RULE_ORDER:
        if matches(tuple(_SOURCE_RULES[source_type])):
            return {
                "classifier_version": SOURCE_CLASSIFIER_VERSION,
                "is_competitor": False,
                "is_owned": False,
                "rationale": f"{source_type}_RULE",
                "source_type": source_type,
            }
    labels = hostname.split(".")
    if any(
        label in {"gov", "edu", "ac"} and index >= len(labels) - 2
        for index, label in enumerate(labels)
    ):
        return {
            "classifier_version": SOURCE_CLASSIFIER_VERSION,
            "is_competitor": False,
            "is_owned": False,
            "rationale": "GOVERNMENT_ACADEMIC_RULE",
            "source_type": "GOVERNMENT_ACADEMIC",
        }
    return {
        "classifier_version": SOURCE_CLASSIFIER_VERSION,
        "is_competitor": False,
        "is_owned": False,
        "rationale": "NO_MATCH",
        "source_type": "UNKNOWN",
    }


def _extract_citations(
    text: str,
    *,
    sample_id: str,
    owned_domains: Sequence[str],
    competitor_domains: Sequence[str],
) -> tuple[list[dict[str, Any]], list[str]]:
    citations: list[dict[str, Any]] = []
    warnings: list[str] = []
    for position, match in enumerate(_URL_PATTERN.finditer(text), start=1):
        if position > MAX_CITATIONS_PER_RESPONSE:
            raise VisibilityAnalysisError(
                f"每条 response 最多支持 {MAX_CITATIONS_PER_RESPONSE} 个显式 URL"
            )
        raw_url = match.group(0).rstrip(_TRAILING_URL_PUNCTUATION)
        if not raw_url:
            continue
        try:
            normalized = normalize_citation_url(raw_url)
        except VisibilityAnalysisError:
            warnings.append(f"{sample_id} 中有一个 URL 无法安全解析，已忽略。")
            continue
        classification = classify_citation_source(
            domain=normalized["domain"],
            registrable_domain=normalized["registrable_domain"],
            owned_domains=owned_domains,
            competitor_domains=competitor_domains,
        )
        citations.append(
            {
                **normalized,
                **classification,
                "citation_mode": "EXTERNAL",
                "evidence_origin": "EXPLICIT_RESPONSE_TEXT_URL",
                "extraction_version": CITATION_EXTRACTION_VERSION,
                "parser_version": CITATION_PARSER_VERSION,
                "position": position,
                "sample_id": sample_id,
                "url": normalized["canonical_url"],
            }
        )
    return citations, _dedupe_terms(warnings)


_ENGLISH_CASE_FLAGS = re.IGNORECASE | re.ASCII

_POSITIVE_TERMS = (
    (re.compile(r"\b(?:best|excellent|good|great|helpful|innovative|intuitive|powerful|reliable|recommend(?:ed)?)\b", _ENGLISH_CASE_FLAGS), 1),
    (re.compile(r"(?:推荐|优秀|很好|最佳|强大|易用|创新|可靠|适合)"), 1),
)
_NEGATIVE_TERMS = (
    (re.compile(r"\b(?:bad|confusing|difficult|expensive|poor|unreliable|weak)\b", _ENGLISH_CASE_FLAGS), 1),
    (re.compile(r"\b(?:do\s+not|don't|cannot|can't|wouldn't)\s+recommend\b", _ENGLISH_CASE_FLAGS), 2),
    (re.compile(r"(?:不推荐|不建议|昂贵|很贵|困难|复杂|糟糕|较弱|不可靠)"), 1),
)
_ATTRIBUTE_RULES = (
    ("EASE_OF_USE", "POSITIVE", re.compile(r"\b(?:easy(?:\s+to\s+use)?|intuitive|user-friendly)\b|易用|容易上手|直观", _ENGLISH_CASE_FLAGS)),
    ("EASE_OF_USE", "NEGATIVE", re.compile(r"\b(?:confusing|difficult\s+to\s+use|steep\s+learning\s+curve)\b|难用|复杂|学习曲线陡", _ENGLISH_CASE_FLAGS)),
    ("PRICING", "NEGATIVE", re.compile(r"\b(?:expensive|overpriced|costly)\b|昂贵|很贵|价格高", _ENGLISH_CASE_FLAGS)),
    ("PRICING", "POSITIVE", re.compile(r"\b(?:affordable|good\s+value|cost-effective)\b|实惠|性价比高", _ENGLISH_CASE_FLAGS)),
    ("CAPABILITY", "POSITIVE", re.compile(r"\b(?:powerful|capable|feature-rich|enterprise-grade)\b|强大|功能丰富|企业级", _ENGLISH_CASE_FLAGS)),
    ("CAPABILITY", "NEGATIVE", re.compile(r"\b(?:weak|limited|lacks?\s+features?)\b|较弱|能力有限|功能不足", _ENGLISH_CASE_FLAGS)),
    ("INNOVATION", "POSITIVE", re.compile(r"\b(?:innovative|cutting-edge|novel)\b|创新|前沿", _ENGLISH_CASE_FLAGS)),
    ("RELIABILITY", "POSITIVE", re.compile(r"\b(?:reliable|stable|dependable)\b|可靠|稳定", _ENGLISH_CASE_FLAGS)),
    ("RELIABILITY", "NEGATIVE", re.compile(r"\b(?:unreliable|unstable|frequent\s+outages?)\b|不可靠|不稳定|频繁宕机", _ENGLISH_CASE_FLAGS)),
)


def _segments(text: str) -> list[dict[str, Any]]:
    output = []
    for match in re.finditer(r"[^.!?。！？\n]+[.!?。！？]?", text):
        raw = match.group(0)
        leading = len(raw) - len(raw.lstrip())
        trailing = len(raw) - len(raw.rstrip())
        start = match.start() + leading
        end = match.end() - trailing
        if end > start:
            output.append({"start": start, "end": end, "text": text[start:end]})
    return output


def _polarity(text: str) -> dict[str, Any]:
    positive = sum(weight for pattern, weight in _POSITIVE_TERMS if pattern.search(text))
    negative = sum(weight for pattern, weight in _NEGATIVE_TERMS if pattern.search(text))
    if re.search(
        r"\b(?:do\s+not|don't|not|never)\s+(?:like|recommend|trust|prefer)\b|"
        r"(?:不|没|未)(?:喜欢|推荐|信任|偏好)",
        text,
        re.IGNORECASE,
    ):
        positive = max(0, positive - 1)
    signals = positive + negative
    if positive and negative:
        return {
            "confidence": min(0.95, 0.68 + signals * 0.06),
            "label": "MIXED",
            "score": (positive - negative) / signals,
        }
    if positive:
        return {
            "confidence": min(0.95, 0.65 + positive * 0.08),
            "label": "POSITIVE",
            "score": min(1, positive / 2),
        }
    if negative:
        return {
            "confidence": min(0.95, 0.65 + negative * 0.08),
            "label": "NEGATIVE",
            "score": max(-1, -negative / 2),
        }
    return {"confidence": 0.55, "label": "NEUTRAL", "score": 0.0}


def _parse_perception(
    text: str, mentions: Sequence[Mapping[str, Any]]
) -> dict[str, list[dict[str, Any]]]:
    valid = [item for item in mentions if item["is_valid"]]
    all_segments = _segments(text)
    sentiments: list[dict[str, Any]] = []
    attributes: list[dict[str, Any]] = []
    reasons: list[dict[str, Any]] = []
    seen: set[str] = set()
    for mention in valid:
        if mention["entity_id"] in seen:
            continue
        seen.add(mention["entity_id"])
        sentence = next(
            (
                segment
                for segment in all_segments
                if segment["start"]
                <= mention["_span_start_index"]
                < segment["end"]
            ),
            {
                "start": mention["_span_start_index"],
                "end": mention["_span_end_index"],
                "text": mention["surface_text"],
            },
        )
        clause_start = sentence["start"]
        clause_end = sentence["end"]
        separators = list(
            re.finditer(
                r"(?:\b(?:but|while|whereas)\b|[,;，；]|但|而)",
                sentence["text"],
                re.IGNORECASE,
            )
        )
        bounds = [sentence["start"]] + [
            sentence["start"] + match.end() for match in separators
        ] + [sentence["end"]]
        for index in range(len(bounds) - 1):
            if (
                bounds[index]
                <= mention["_span_start_index"]
                < bounds[index + 1]
            ):
                clause_start, clause_end = bounds[index], bounds[index + 1]
                break
        sentence_entity_count = len(
            {
                item["entity_id"]
                for item in valid
                if sentence["start"]
                <= item["_span_start_index"]
                < sentence["end"]
            }
        )
        evidence_text = sentence["text"].strip()[:800]
        attribute_text = (
            sentence["text"]
            if sentence_entity_count <= 1
            else text[clause_start:clause_end].strip()
        )
        detected = _polarity(attribute_text)
        sentiments.append(
            {
                "confidence": min(mention["confidence"], detected["confidence"]),
                "entity_id": mention["entity_id"],
                "entity_name": mention["entity_name"],
                "evidence_quote": evidence_text,
                "label": detected["label"],
                "parser_version": SENTIMENT_PARSER_VERSION,
                "score": detected["score"],
            }
        )
        for attribute, label, pattern in _ATTRIBUTE_RULES:
            match = pattern.search(attribute_text)
            if match:
                attributes.append(
                    {
                        "attribute": attribute,
                        "confidence": min(mention["confidence"], 0.82),
                        "entity_id": mention["entity_id"],
                        "entity_name": mention["entity_name"],
                        "evidence_quote": evidence_text,
                        "label": label,
                        "parser_version": SENTIMENT_PARSER_VERSION,
                        "reason": match.group(0)[:160],
                    }
                )
        not_recommended = bool(
            re.search(
                r"\b(?:do\s+not|don't|wouldn't|cannot|can't)\s+recommend\b|不推荐|不建议",
                sentence["text"],
                re.IGNORECASE,
            )
        )
        recommended = not not_recommended and bool(
            re.search(
                r"\brecommend(?:ed|ing|s)?\b|推荐|适合|首选",
                sentence["text"],
                re.IGNORECASE,
            )
        )
        if not_recommended or recommended:
            because = re.split(
                r"\bbecause\b|因为|由于", sentence["text"], maxsplit=1, flags=re.IGNORECASE
            )
            reason = because[1].strip() if len(because) > 1 else evidence_text
            reasons.append(
                {
                    "confidence": min(
                        mention["confidence"], 0.88 if len(because) > 1 else 0.72
                    ),
                    "entity_id": mention["entity_id"],
                    "entity_name": mention["entity_name"],
                    "evidence_quote": evidence_text,
                    "kind": "NOT_RECOMMENDED" if not_recommended else "RECOMMENDED",
                    "parser_version": SENTIMENT_PARSER_VERSION,
                    "reason": reason[:800],
                }
            )
    return {
        "attributes": sorted(
            attributes, key=lambda item: (item["entity_id"], item["attribute"])
        ),
        "reasons": sorted(reasons, key=lambda item: item["entity_id"]),
        "sentiments": sorted(sentiments, key=lambda item: item["entity_id"]),
    }


def _position_score(rank: int | None) -> float | None:
    return None if rank is None else 1 / math.log2(rank + 1)


def _sampling_semantics(sample_count: int, has_attempts: bool = True) -> str:
    if sample_count == 0:
        return "NO_SUCCESSFUL_SAMPLES" if has_attempts else "NO_DATA"
    if sample_count == 1:
        return "SINGLE_SAMPLE"
    if sample_count < 5:
        return "MEAN_WITH_DISPERSION"
    return "MEAN_WITH_95_CI"


def _clamp_ratio(value: float) -> float:
    return min(1.0, max(0.0, value))


def _weighted_statistics(values: Sequence[float], weights: Sequence[float]) -> dict[str, float | None]:
    weight_sum = math.fsum(weights)
    mean = _clamp_ratio(
        math.fsum(value * weight for value, weight in zip(values, weights))
        / weight_sum
    )
    squared_deviation_sum = math.fsum(
        weight * (value - mean) ** 2 for value, weight in zip(values, weights)
    )
    population_variance = max(0.0, squared_deviation_sum / weight_sum)
    squared_weight_sum = math.fsum(weight * weight for weight in weights)
    effective_sample_count = weight_sum * weight_sum / squared_weight_sum
    unbiased_denominator = weight_sum - squared_weight_sum / weight_sum
    unbiased_variance = (
        max(0.0, squared_deviation_sum / unbiased_denominator)
        if unbiased_denominator > 0
        else None
    )
    return {
        "effective_sample_count": effective_sample_count,
        "mean": mean,
        "population_variance": population_variance,
        "standard_deviation": math.sqrt(population_variance),
        "unbiased_variance": unbiased_variance,
        "weight_sum": weight_sum,
    }


def _mean_interval_95(statistics: Mapping[str, float | None]) -> dict[str, float]:
    unbiased = statistics["unbiased_variance"]
    standard_error = (
        0.0
        if unbiased is None
        else math.sqrt(unbiased / float(statistics["effective_sample_count"]))
    )
    margin = Z_SCORE_95 * standard_error
    mean = float(statistics["mean"])
    return {
        "confidence": 0.95,
        "lower": _clamp_ratio(mean - margin),
        "upper": _clamp_ratio(mean + margin),
    }


def _wilson_interval_95(success_count: int, total_count: int) -> dict[str, float]:
    probability = success_count / total_count
    squared_z = Z_SCORE_95**2
    denominator = 1 + squared_z / total_count
    center = (probability + squared_z / (2 * total_count)) / denominator
    margin = (
        Z_SCORE_95
        * math.sqrt(
            (probability * (1 - probability) + squared_z / (4 * total_count))
            / total_count
        )
        / denominator
    )
    return {
        "confidence": 0.95,
        "lower": _clamp_ratio(center - margin),
        "upper": _clamp_ratio(center + margin),
    }


def _metric_confidence(
    *, values: Sequence[float], parser_confidences: Sequence[float]
) -> dict[str, Any]:
    sample_count = len(values)
    if sample_count == 0:
        return {
            "algorithm_version": METRICS_ALGORITHM_VERSION,
            "effective_sample_count": 0,
            "interval_95": None,
            "level": None,
            "mean": None,
            "parser_confidence": None,
            "provider_success_rate": None,
            "sample_adequacy": 0,
            "sample_count": 0,
            "sampling_semantics": "NO_DATA",
            "score": None,
            "stability": None,
            "stable": False,
            "standard_deviation": None,
            "variance": None,
        }
    weights = [1.0] * sample_count
    statistics = _weighted_statistics(values, weights)
    parser_statistics = _weighted_statistics(parser_confidences, weights)
    sample_adequacy = min(1.0, math.sqrt(sample_count / 5))
    stability = _clamp_ratio(
        1 - float(statistics["population_variance"]) / 0.25
    )
    score = _clamp_ratio(
        sample_adequacy
        * (
            0.35
            + 0.3 * stability
            + 0.35 * float(parser_statistics["mean"])
        )
    )
    interval = _mean_interval_95(statistics) if sample_count >= 5 else None
    stable = bool(
        interval is not None
        and score >= 0.75
        and (interval["upper"] - interval["lower"]) / 2 <= 0.15
    )
    if sample_count <= 1 or score < 0.5:
        level = "LOW"
    elif sample_count < 5 or score < 0.75:
        level = "MEDIUM"
    else:
        level = "HIGH"
    return {
        "algorithm_version": METRICS_ALGORITHM_VERSION,
        "effective_sample_count": statistics["effective_sample_count"],
        "interval_95": interval,
        "level": level,
        "mean": statistics["mean"],
        "parser_confidence": parser_statistics["mean"],
        "provider_success_rate": 1.0,
        "sample_adequacy": sample_adequacy,
        "sample_count": sample_count,
        "sampling_semantics": _sampling_semantics(sample_count),
        "score": score,
        "stability": stability,
        "stable": stable,
        "standard_deviation": statistics["standard_deviation"]
        if sample_count >= 2
        else None,
        "variance": statistics["population_variance"],
    }


def _confidence_label(level: str | None) -> str:
    return {"HIGH": "高", "MEDIUM": "中", "LOW": "低"}.get(level, "无数据")


def _confidence_reason(confidence: Mapping[str, Any]) -> str:
    if confidence["sample_count"] < 5:
        return "有效样本少于 5 条，当前结果适合方向判断，不宜视为稳定基线。"
    if not confidence["stable"]:
        return "样本已可计算区间，但波动或区间宽度仍不足以形成稳定基线。"
    return "样本量、解析置信度和结果稳定性均达到当前算法的稳定门槛。"


def _infer_prompt_intent(prompt: str) -> str:
    if re.search(r"\b(?:vs\.?|versus|compare|comparison)\b|对比|比较|区别|哪个好", prompt, re.IGNORECASE):
        return "comparison"
    if re.search(r"\b(?:recommend|recommended|best|top|choose)\b|推荐|最佳|哪款|如何选择|首选", prompt, re.IGNORECASE):
        return "recommendation"
    if re.search(r"\b(?:buy|purchase|price|pricing|cost)\b|购买|价格|多少钱|报价", prompt, re.IGNORECASE):
        return "transactional"
    if re.search(r"\b(?:login|website|official)\b|官网|登录|入口", prompt, re.IGNORECASE):
        return "navigational"
    if re.search(r"\b(?:fix|solve|troubleshoot|error)\b|修复|解决|报错|故障", prompt, re.IGNORECASE):
        return "troubleshooting"
    return "informational"


def _recommendation_priority(
    *, expected_impact: float, confidence: float, importance: float, effort: float
) -> float:
    return round(
        expected_impact * confidence * importance / max(effort, 0.25), 4
    )


def _recommendations(
    *,
    project_id: int,
    prompt: str,
    samples: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    total = len(samples)
    if total < 2:
        return []
    sample_ids = [str(sample["id"]) for sample in samples]
    brand_ids = [str(sample["id"]) for sample in samples if sample["brand_mentioned"]]
    competitor_ids = [
        str(sample["id"]) for sample in samples if sample["competitor_mentions"]
    ]
    owned_ids = [
        str(sample["id"])
        for sample in samples
        if any(citation["is_owned"] for citation in sample["citations"])
    ]
    third_party_ids = [
        str(sample["id"])
        for sample in samples
        if any(
            not citation["is_owned"] and not citation["is_competitor"]
            for citation in sample["citations"]
        )
    ]
    brand_rate = len(brand_ids) / total
    competitor_rate = len(competitor_ids) / total
    owned_rate = len(owned_ids) / total
    third_party_rate = len(third_party_ids) / total
    sample_confidence = min(1.0, total / 5)
    importance = 1.0
    intent = _infer_prompt_intent(prompt)
    prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    analysis_scope = f"project:{project_id}:prompt:{prompt_hash}"
    candidates: list[dict[str, Any]] = []

    def add(
        *,
        rule_id: str,
        category: str,
        title: str,
        description: str,
        effort: float,
        expected_impact: float,
        evidence_ids: Sequence[str],
        facts: Mapping[str, Any],
        suppression_suffix: str,
    ) -> None:
        candidates.append(
            {
                "business_importance": importance,
                "category": category,
                "confidence": sample_confidence,
                "description": description,
                "effort_score": effort,
                "evidence": {
                    "audit_finding_ids": [],
                    "execution_ids": [],
                    "facts": dict(facts),
                    "metric_snapshot_ids": [],
                    "prompt_ids": [],
                    "sample_ids": sorted(set(evidence_ids)),
                    "schema_version": 1,
                    "source_kind": "AD_HOC_ANALYSIS",
                },
                "expected_impact": expected_impact,
                "priority_score": _recommendation_priority(
                    expected_impact=expected_impact,
                    confidence=sample_confidence,
                    importance=importance,
                    effort=effort,
                ),
                "retest_delay_days": 21,
                "rule_id": rule_id,
                "rule_version": RECOMMENDATION_RULE_SET_VERSION,
                "source_key": analysis_scope,
                "suppression_key": f"{analysis_scope}:{suppression_suffix}",
                "title": title,
            }
        )

    if brand_rate <= 0.25 and competitor_rate >= 0.5:
        add(
            rule_id="content.competitor-coverage-gap",
            category="CONTENT",
            title="补齐该问题下的竞品内容覆盖差距",
            description=(
                "竞品在多条回答中反复出现，而本品牌缺席或不稳定。应建设直接回答该问题的决策型内容，并明确适用边界。"
            ),
            effort=0.65,
            expected_impact=min(1.0, max(0.0, competitor_rate - brand_rate)),
            evidence_ids=[*brand_ids, *competitor_ids],
            facts={
                "brand_mention_rate": brand_rate,
                "competitor_mention_rate": competitor_rate,
                "successful_samples": total,
            },
            suppression_suffix="content-gap",
        )
    elif brand_rate == 0 and intent in {"comparison", "recommendation", "transactional"}:
        add(
            rule_id="content.high-intent-brand-absent",
            category="CONTENT",
            title="为高意图问题创建决策型内容",
            description=(
                "本品牌在高意图问题中完全缺席。应发布能直接帮助用户比较和选择的内容，真实说明产品优势与限制。"
            ),
            effort=0.6,
            expected_impact=min(1.0, 0.5 + importance * 0.5),
            evidence_ids=sample_ids,
            facts={
                "brand_mention_rate": brand_rate,
                "intent": intent,
                "successful_samples": total,
            },
            suppression_suffix="high-intent",
        )

    if third_party_rate >= 0.5 and owned_rate < 0.2:
        add(
            rule_id="source.owned-citation-gap",
            category="SOURCE",
            title="提升自有来源的引用覆盖",
            description=(
                "回答反复引用第三方页面，却很少引用品牌官网。应为最相关的自有页面补充可核验事实、稳定结构和清晰实体信号。"
            ),
            effort=0.55,
            expected_impact=min(1.0, max(0.0, third_party_rate - owned_rate)),
            evidence_ids=[*third_party_ids, *owned_ids],
            facts={
                "owned_citation_rate": owned_rate,
                "third_party_citation_rate": third_party_rate,
                "successful_samples": total,
            },
            suppression_suffix="owned-citation",
        )
    elif brand_rate >= 0.5 and owned_rate == 0:
        add(
            rule_id="source.mentioned-not-cited",
            category="SOURCE",
            title="把品牌提及转化为自有来源引用",
            description=(
                "回答已经反复提及品牌，但没有引用官网。应补充可验证的一手证据，并采用稳定、易引用的页面结构。"
            ),
            effort=0.45,
            expected_impact=min(1.0, brand_rate * 0.75),
            evidence_ids=brand_ids,
            facts={
                "brand_mention_rate": brand_rate,
                "owned_citation_rate": owned_rate,
                "successful_samples": total,
            },
            suppression_suffix="mentioned-not-cited",
        )
    return sorted(
        candidates,
        key=lambda item: (-item["priority_score"], item["rule_id"], item["source_key"]),
    )


def _aggregate_citations(samples: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for sample in samples:
        for citation in sample["citations"]:
            key = citation["canonical_url"]
            aggregate = grouped.get(key)
            if aggregate is None:
                aggregate = {
                    "url": key,
                    "canonical_url": key,
                    "citation_mode": citation["citation_mode"],
                    "classifier_version": citation["classifier_version"],
                    "count": 0,
                    "domain": citation["domain"],
                    "is_competitor": citation["is_competitor"],
                    "is_owned": citation["is_owned"],
                    "parser_version": citation["parser_version"],
                    "raw_urls": set(),
                    "registrable_domain": citation["registrable_domain"],
                    "samples": set(),
                    "source_type": citation["source_type"],
                    "url_hash": citation["url_hash"],
                }
                grouped[key] = aggregate
            aggregate["count"] += 1
            aggregate["raw_urls"].add(citation["raw_url"])
            aggregate["samples"].add(sample["id"])
    return [
        {
            **aggregate,
            "raw_urls": sorted(aggregate["raw_urls"]),
            "samples": sorted(aggregate["samples"]),
        }
        for _, aggregate in sorted(grouped.items())
    ]


def analyze_ai_visibility(
    *,
    project_id: int,
    project_name: str,
    project_product: str = "",
    project_keywords: Sequence[Any] = (),
    project_website: str = "",
    stored_competitors: Sequence[Any] = (),
    prompt: str,
    responses: Any,
    aliases: Sequence[Any] | None = None,
    competitors: Sequence[Any] | None = None,
) -> dict[str, Any]:
    """Analyze captured AI responses without calling an external model.

    ``competitors=None`` means use the project's stored competitor list.  An
    explicit empty list intentionally disables competitor comparison for this
    run.  The project's product is always retained as a useful brand alias.
    Project keywords are admitted only when they carry a confirmed name or
    product token; request aliases extend that auditable local entity set only
    after passing the same generic/distinctive-term gate.
    """

    if isinstance(project_id, bool) or not isinstance(project_id, int) or project_id <= 0:
        raise VisibilityAnalysisError("project_id 必须是正整数")
    normalized_prompt = _required_text(prompt, "prompt", MAX_PROMPT_LENGTH)
    normalized_responses = _normalize_responses(responses)
    effective_competitors = stored_competitors if competitors is None else competitors
    entities, warnings = _build_entities(
        project_name=project_name,
        project_product=project_product or "",
        project_keywords=project_keywords,
        project_website=project_website or "",
        aliases=aliases,
        competitors=effective_competitors,
    )
    owned_domains = entities[0].domains
    competitor_domains = tuple(
        domain
        for entity in entities
        if entity.entity_type == "competitor"
        for domain in entity.domains
    )

    samples: list[dict[str, Any]] = []
    for response in normalized_responses:
        mentions, fuzzy_scan_limited = _detect_mentions(response["text"], entities)
        ranks = _extract_ranks(response["text"], mentions)
        rank_by_entity = {item["entity_id"]: item for item in ranks}
        citations, citation_warnings = _extract_citations(
            response["text"],
            sample_id=response["id"],
            owned_domains=owned_domains,
            competitor_domains=competitor_domains,
        )
        perception = _parse_perception(response["text"], mentions)
        public_mentions = [
            {key: value for key, value in mention.items() if not key.startswith("_")}
            for mention in mentions
        ]
        brand_mention = next(
            (item for item in mentions if item["entity_id"] == "brand"), None
        )
        brand_rank = rank_by_entity.get("brand")
        competitor_mentions = [
            {
                "entity_id": item["entity_id"],
                "name": item["entity_name"],
                "occurrence_count": item["occurrence_count"],
            }
            for item in mentions
            if item["entity_type"] == "competitor"
        ]
        evidence = [
            {
                "confidence": mention["confidence"],
                "canonical_entity": mention["entity_name"],
                "entity_id": mention["entity_id"],
                "matched_term": mention["matched_term"],
                "matched_term_sources": mention["matched_term_sources"],
                "match_type": mention["match_type"],
                "quote": mention["evidence_quote"],
                "start": mention["span_start"],
                "end": mention["span_end"],
                "surface_text": mention["surface_text"],
                "type": "MENTION",
            }
            for mention in mentions
        ]
        sample_warnings = list(citation_warnings)
        if fuzzy_scan_limited:
            sample_warnings.append(
                "模糊匹配达到安全计算上限；精确名称、别名和域名匹配仍已完整执行。"
            )
        if any(mention["needs_review"] for mention in mentions):
            sample_warnings.append("存在模糊或低置信度品牌匹配，建议人工复核证据片段。")
        samples.append(
            {
                **response,
                "brand_mentioned": brand_mention is not None,
                "citations": citations,
                "competitor_mentions": competitor_mentions,
                "evidence": evidence,
                "matched_aliases": brand_mention["matched_surfaces"]
                if brand_mention is not None
                else [],
                "matched_entity_terms": brand_mention["matched_terms"]
                if brand_mention is not None
                else [],
                "mentions": public_mentions,
                "perception": perception,
                "ranks": ranks,
                "recommendation_rank": brand_rank["rank"]
                if brand_rank is not None
                else None,
                "warnings": _dedupe_terms(sample_warnings),
            }
        )

    entity_results: list[dict[str, Any]] = []
    voices: dict[str, float] = {}
    for entity in entities:
        entity_mentions = [
            next(
                (
                    item
                    for item in sample["mentions"]
                    if item["entity_id"] == entity.entity_id
                ),
                None,
            )
            for sample in samples
        ]
        entity_ranks = [
            next(
                (
                    item["rank"]
                    for item in sample["ranks"]
                    if item["entity_id"] == entity.entity_id
                ),
                None,
            )
            for sample in samples
        ]
        weighted_voice = math.fsum(
            0.0
            if mention is None
            else (_position_score(rank) if rank is not None else 1.0)
            for mention, rank in zip(entity_mentions, entity_ranks)
        )
        voices[entity.entity_id] = weighted_voice
        mentioned_count = sum(mention is not None for mention in entity_mentions)
        numeric_ranks = [rank for rank in entity_ranks if rank is not None]
        confidence = _metric_confidence(
            values=[1.0 if mention is not None else 0.0 for mention in entity_mentions],
            parser_confidences=[
                float(mention["confidence"]) if mention is not None else 1.0
                for mention in entity_mentions
            ],
        )
        sentiment_samples = [
            sentiment
            for sample in samples
            for sentiment in sample["perception"]["sentiments"]
            if sentiment["entity_id"] == entity.entity_id
        ]
        entity_results.append(
            {
                "alias_evidence": [
                    {"term": alias, "sources": list(sources)}
                    for alias, sources in entity.alias_sources
                ],
                "aliases": list(entity.aliases),
                "average_rank": math.fsum(numeric_ranks) / len(numeric_ranks)
                if numeric_ranks
                else None,
                "confidence": confidence,
                "domains": list(entity.domains),
                "id": entity.entity_id,
                "mention_rate": mentioned_count / len(samples),
                "mention_rate_interval_95": _wilson_interval_95(
                    mentioned_count, len(samples)
                )
                if len(samples) >= 5
                else None,
                "mentions": mentioned_count,
                "name": entity.name,
                "occurrences": sum(
                    int(mention["occurrence_count"])
                    for mention in entity_mentions
                    if mention is not None
                ),
                "sentiment_score": math.fsum(
                    float(item["score"]) for item in sentiment_samples
                )
                / len(sentiment_samples)
                if sentiment_samples
                else None,
                "share_of_voice": None,
                "type": entity.entity_type,
                "weighted_voice": weighted_voice,
            }
        )

    voice_denominator = math.fsum(voices.values())
    for entity_result in entity_results:
        entity_result["share_of_voice"] = (
            voices[entity_result["id"]] / voice_denominator
            if voice_denominator > 0
            else None
        )
    brand_result = next(item for item in entity_results if item["id"] == "brand")
    brand_confidence = brand_result["confidence"]
    confidence_summary = {
        **brand_confidence,
        "label": _confidence_label(brand_confidence["level"]),
        "reason": _confidence_reason(brand_confidence),
    }
    citations = _aggregate_citations(samples)
    cited_sample_count = sum(bool(sample["citations"]) for sample in samples)
    owned_cited_sample_count = sum(
        any(citation["is_owned"] for citation in sample["citations"])
        for sample in samples
    )
    recommendation_details = _recommendations(
        project_id=project_id,
        prompt=normalized_prompt,
        samples=samples,
    )
    if len(samples) < 5:
        warnings.append("样本少于 5 条，95% 区间与稳定结论暂不可用。")
    if len(entities) == 1:
        warnings.append("未配置竞品，声量份额只能反映品牌自身，不能代表市场对比。")
    if not brand_result["mentions"]:
        warnings.append("当前样本中未识别到品牌提及。")
    warnings.extend(
        warning for sample in samples for warning in sample["warnings"]
    )
    algorithm_versions = current_ai_visibility_algorithm_versions()
    return {
        "algorithm_versions": algorithm_versions,
        "citations": citations,
        "entities": entity_results,
        "methodology": {
            "engine": "deterministic-local-geo-engine-port",
            "external_model_used": False,
            "sample_semantics": _sampling_semantics(len(samples)),
            "response_limit": MAX_RESPONSES,
            "fuzzy_comparison_limit_per_response": MAX_FUZZY_COMPARISONS_PER_RESPONSE,
            "weight_policy": (
                "One submitted prompt with uniform prompt/provider/sample weights of 1.0; "
                "unranked valid mentions use position weight 1.0 as in the hand-off worker."
            ),
            "sov_formula": "sum(mention * 1/log2(rank+1)); unranked mention weight=1",
            "confidence_formula": (
                "sqrt(sample/5) * (0.35*success + 0.30*stability + "
                "0.35*parser_confidence)"
            ),
            "citation_semantics": (
                "Only explicit HTTP(S)/www URLs present in supplied response text "
                "are counted; no citation or redirect is inferred via network access."
            ),
            "metric_comparability": (
                "This ad-hoc Python port analyzes only submitted successful texts with "
                "uniform weights. Its metrics are not directly comparable with provider "
                "grounding runs, failed executions, or historical TypeScript-engine series."
            ),
            "confidence_population": (
                "Supplied non-empty response texts only; provider failures and omitted "
                "responses are outside this endpoint's observable population."
            ),
        },
        "project": {
            "id": project_id,
            "name": entities[0].name,
            "product": project_product or "",
            "website": project_website or "",
            "effective_brand_aliases": [
                {"term": alias, "sources": list(sources)}
                for alias, sources in entities[0].alias_sources
            ],
        },
        "prompt": normalized_prompt,
        "recommendation_details": recommendation_details,
        "recommendations": [item["title"] for item in recommendation_details],
        "samples": samples,
        "source_trace": {
            "handoff_archive": HANDOFF_ARCHIVE_NAME,
            "handoff_archive_sha256": HANDOFF_ARCHIVE_SHA256,
            "handoff_manifest_sha256": HANDOFF_MANIFEST_SHA256,
            "source_package": "packages/geo-engine",
            "source_package_version": "0.1.0",
            "source_files_sha256": dict(HANDOFF_SOURCE_FILE_SHA256),
            "provenance_semantics": (
                "The SHA values identify the upstream TypeScript reference; the running "
                "Python port is identified separately by its integration version."
            ),
            "python_implementation": {
                "module": "services.ai_visibility_service",
                "version": AI_VISIBILITY_ALGORITHM_VERSION,
            },
        },
        "summary": {
            "average_rank": brand_result["average_rank"],
            "citation_count": len(citations),
            "citation_evidence_origin": "EXPLICIT_RESPONSE_TEXT_URL",
            "citation_occurrence_count": sum(item["count"] for item in citations),
            "citation_rate": cited_sample_count / len(samples),
            "confidence": confidence_summary,
            "mentioned_count": brand_result["mentions"],
            "mention_rate": brand_result["mention_rate"],
            "mention_rate_interval_95": brand_result["mention_rate_interval_95"],
            "owned_citation_count": sum(item["is_owned"] for item in citations),
            "owned_citation_rate": owned_cited_sample_count / len(samples),
            "sample_count": len(samples),
            "sentiment_score": brand_result["sentiment_score"],
            "share_of_voice": brand_result["share_of_voice"],
        },
        "warnings": _dedupe_terms(warnings),
    }


def reanalyze_ai_visibility_locally(
    *,
    previous_analysis: Mapping[str, Any],
    project_id: int,
    project_name: str,
    project_product: str = "",
    project_keywords: Sequence[Any] = (),
    project_website: str = "",
    stored_competitors: Sequence[Any] = (),
    prompt: str,
    responses: Any,
    aliases: Sequence[Any] | None = None,
    competitors: Sequence[Any] | None = None,
    force: bool = False,
    reason: str | None = None,
) -> dict[str, Any]:
    """Refresh a saved analysis from captured text without provider or web I/O.

    The signature intentionally contains neither settings nor a sampler.  A
    current result is returned unchanged unless ``force`` is used for a known
    local-input change (for example, newly configured project keywords).
    """

    freshness = inspect_ai_visibility_analysis_freshness(previous_analysis)
    if not isinstance(force, bool):
        raise VisibilityAnalysisError("force 必须是布尔值")
    if reason is not None:
        if not isinstance(reason, str) or not reason.strip() or len(reason.strip()) > 200:
            raise VisibilityAnalysisError("reason 必须是 1 到 200 个字符的字符串")
        normalized_reason = reason.strip()
    else:
        normalized_reason = None

    if not force and not freshness["reanalysis_required"]:
        return {
            "analysis": copy.deepcopy(dict(previous_analysis)),
            "refresh": {
                **freshness,
                "status": "current",
                "performed": False,
                "reason": None,
                "provider_calls": 0,
                "network_requests": 0,
            },
        }

    refresh_reason = normalized_reason or (
        "algorithm_version_changed"
        if freshness["reanalysis_required"]
        else "local_analysis_input_changed"
    )
    refreshed = analyze_ai_visibility(
        project_id=project_id,
        project_name=project_name,
        project_product=project_product,
        project_keywords=project_keywords,
        project_website=project_website,
        stored_competitors=stored_competitors,
        prompt=prompt,
        responses=responses,
        aliases=aliases,
        competitors=competitors,
    )
    refreshed["methodology"] = {
        **refreshed["methodology"],
        "local_reanalysis": {
            "performed": True,
            "reason": refresh_reason,
            "provider_calls": 0,
            "network_requests": 0,
            "previous_algorithm_versions": freshness["stored_versions"],
            "current_algorithm_versions": freshness["current_versions"],
            "stale_components": freshness["stale_components"],
        },
    }
    return {
        "analysis": refreshed,
        "refresh": {
            **freshness,
            "status": "refreshed",
            "performed": True,
            "reason": refresh_reason,
            "provider_calls": 0,
            "network_requests": 0,
        },
    }


__all__ = [
    "AI_VISIBILITY_ALGORITHM_VERSION",
    "CITATION_EXTRACTION_VERSION",
    "CITATION_PARSER_VERSION",
    "HANDOFF_ARCHIVE_SHA256",
    "HANDOFF_SOURCE_FILE_SHA256",
    "MAX_RESPONSES",
    "MENTION_PARSER_VERSION",
    "METRICS_ALGORITHM_VERSION",
    "RANK_PARSER_VERSION",
    "RECOMMENDATION_RULE_SET_VERSION",
    "SENTIMENT_PARSER_VERSION",
    "SOURCE_CLASSIFIER_VERSION",
    "VisibilityAnalysisError",
    "analyze_ai_visibility",
    "classify_citation_source",
    "current_ai_visibility_algorithm_versions",
    "inspect_ai_visibility_analysis_freshness",
    "normalize_citation_url",
    "reanalyze_ai_visibility_locally",
]
