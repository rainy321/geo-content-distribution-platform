from __future__ import annotations

import ipaddress
import re
from collections.abc import Sequence
from typing import Any
from urllib.parse import urlsplit

from services.ai_visibility_service import (
    VisibilityAnalysisError,
    normalize_citation_url,
)
from services.content_hygiene_service import authoring_visible_length
from services.keyword_hygiene_service import has_no_keyword_stuffing
from services.target_length_score_profile import (
    resolve_effective_length_score_profile,
    resolve_target_length_score_profile,
)


MIN_CONTENT_LENGTH = 600
MIN_PARAGRAPHS = 3
GEO_SCORE_VERSION = "geo-score-mvp-rules-1.2.0"

_NUMBER_PATTERN = r"\d+(?:[.,]\d+)*"
_FACT_UNIT_PATTERN = (
    r"(?:个百分点|摄氏度|毫秒|分钟|小时|人民币|万美元|万元|亿元|"
    r"字符|字节|公里|平方米|%|％|年|月|日|天|秒|次|轮|批|人|户|家|"
    r"个|项|条|篇|份|台|套|种|倍|元|万|亿|千|KB|MB|GB|TB|ms)"
)
_FACT_CONTEXT_PATTERN = (
    r"(?:截至|根据|样本(?:量|数)?|统计(?:结果|数据)?|测试(?:结果|数据)?|"
    r"实验(?:结果|数据)?|数据显示|版本(?:号)?|批次|编号|价格|成本|数量|"
    r"规模|容量|时延|耗时|准确率|召回率|成功率|通过率|发布日期|更新时间)"
)
_INLINE_HTTP_URL_PATTERN = re.compile(
    r"https?://[^\s<>\]\[()（）\uff0c\u3002\uff1b\uff1a\uff01\uff1f\u3001\u201c\u201d\u2018\u2019]+",
    re.IGNORECASE,
)
_REFERENCE_LINE_PATTERN = re.compile(
    r"(?mi)^\s*(?:#{1,6}\s*)?(?:数据来源|信息来源|参考资料|来源)\s*[:\uff1a]\s*(?P<reference>[^\r\n]+)$"
)
_DOI_PATTERN = re.compile(
    r"(?i)(?:doi\s*[:\uff1a]\s*)?10\.\d{4,9}/[-._;()/:a-z0-9]+"
)
_FORMAL_REFERENCE_TITLE_PATTERN = re.compile(r"《[^》\r\n]{2,120}》")
_REFERENCE_YEAR_PATTERN = re.compile(r"(?:19|20)\d{2}(?:\s*年)?")
_NON_PUBLIC_HOST_SUFFIXES = (
    ".internal",
    ".local",
    ".localhost",
    ".lan",
    ".home",
    ".home.arpa",
    ".corp",
    ".invalid",
    ".test",
    ".example",
    ".onion",
)


def score_geo_content(
    *,
    title: str,
    content: str,
    brand: str,
    keywords: Sequence[str],
    target_length: int | None = None,
    requested_target_length: int | None = None,
    source: str | None = None,
) -> dict[str, Any]:
    """Score GEO readiness against checks applicable to the intended length."""
    if requested_target_length is not None:
        resolve_target_length_score_profile(requested_target_length)
    scoring_target_length = (
        target_length if target_length is not None else requested_target_length
    )
    actual_visible_length = authoring_visible_length(content)
    length_profile = resolve_effective_length_score_profile(
        scoring_target_length,
        actual_length=actual_visible_length,
    )
    applicable = length_profile["applicable_checks"]
    normalized_keywords = _normalize_keywords(keywords)
    keyword_limit = length_profile.get("keyword_limit")
    evaluated_keywords = (
        normalized_keywords[: int(keyword_limit)]
        if isinstance(keyword_limit, int)
        else normalized_keywords
    )
    combined_text = f"{title}\n{content}".casefold()
    normalized_brand = brand.strip()

    brand_present = normalized_brand.casefold() in combined_text
    covered_keywords = [
        keyword
        for keyword in evaluated_keywords
        if keyword.casefold() in combined_text
    ]
    keyword_coverage_points = _coverage_points(
        covered_count=len(covered_keywords),
        total_count=len(evaluated_keywords),
    )
    title_has_keyword = any(
        keyword.casefold() in title.casefold()
        for keyword in normalized_keywords
    )
    paragraph_count = _paragraph_count(content)
    minimum_content_length = int(length_profile["minimum_content_length"])
    minimum_paragraphs = int(length_profile["minimum_paragraphs"])
    length_reached = actual_visible_length >= minimum_content_length
    multiple_paragraphs = paragraph_count >= minimum_paragraphs
    has_headings = _has_h2_or_h3(content)
    has_faq = _has_faq(content)
    has_facts = _has_numeric_fact(content)
    has_source = _has_external_source(content)
    has_summary = _has_summary(content)
    no_keyword_stuffing = has_no_keyword_stuffing(
        content,
        normalized_keywords,
    )

    maximum_points = {
        "brand": 15,
        "keyword_coverage": 15,
        "title_keyword": 10,
        "length": 10,
        "paragraphs": 10,
        "headings": 10 if applicable["headings"] else 0,
        "faq": 10 if applicable["faq"] else 0,
        "facts": 5,
        "source": 5,
        "summary": 5 if applicable["summary"] else 0,
        "no_stuffing": 5,
    }
    points = {
        "brand": 15 if brand_present else 0,
        "keyword_coverage": keyword_coverage_points,
        "title_keyword": 10 if title_has_keyword else 0,
        "length": 10 if length_reached else 0,
        "paragraphs": 10 if multiple_paragraphs else 0,
        "headings": 10 if applicable["headings"] and has_headings else 0,
        "faq": 10 if applicable["faq"] and has_faq else 0,
        "facts": 5 if has_facts else 0,
        "source": 5 if has_source else 0,
        "summary": 5 if applicable["summary"] and has_summary else 0,
        "no_stuffing": 5 if no_keyword_stuffing else 0,
    }

    structure_maximum = sum(
        maximum_points[key]
        for key in ("length", "paragraphs", "headings", "summary")
    )
    dimensions: dict[str, int | None] = {
        "entity": _as_percentage(points["brand"], 15),
        "keywords": _as_percentage(
            points["keyword_coverage"]
            + points["title_keyword"]
            + points["no_stuffing"],
            30,
        ),
        "structure": _as_percentage(
            points["length"]
            + points["paragraphs"]
            + points["headings"]
            + points["summary"],
            structure_maximum,
        ),
        "faq": _as_percentage(points["faq"], maximum_points["faq"]),
        "citation": _as_percentage(
            points["facts"] + points["source"],
            10,
        ),
    }

    suggestions = _build_suggestions(
        brand=normalized_brand,
        keywords=evaluated_keywords,
        covered_keywords=covered_keywords,
        brand_present=brand_present,
        title_has_keyword=title_has_keyword,
        length_reached=length_reached,
        multiple_paragraphs=multiple_paragraphs,
        has_headings=has_headings,
        has_faq=has_faq,
        has_facts=has_facts,
        has_source=has_source,
        has_summary=has_summary,
        no_keyword_stuffing=no_keyword_stuffing,
        minimum_content_length=minimum_content_length,
        minimum_paragraphs=minimum_paragraphs,
        applicable=applicable,
    )

    maximum_score = sum(maximum_points.values())
    score = round(sum(points.values()) * 100 / maximum_score)
    profile_result = _public_length_profile(
        length_profile,
        target_length=(
            requested_target_length
            if requested_target_length is not None
            else scoring_target_length
        ),
        actual_length=actual_visible_length,
        source=(
            str(length_profile["source"])
            if length_profile["source"] == "actual_length_upgrade"
            else str(source or length_profile["source"])
        ),
        requested_target_length=(
            requested_target_length
            if requested_target_length is not None
            else length_profile["requested_target_length"]
        ),
        effective_target_length=length_profile["effective_target_length"],
        over_target=bool(length_profile["over_target"]),
        dimensions=dimensions,
        effective_max_points=maximum_score,
        evaluated_keywords=evaluated_keywords,
        deferred_keyword_count=len(normalized_keywords) - len(evaluated_keywords),
    )

    return {
        "version": GEO_SCORE_VERSION,
        "score": score,
        "dimensions": dimensions,
        "suggestions": suggestions,
        "length_profile": profile_result,
        "score_details": {
            key: {
                "earned_points": points[key],
                "max_points": maximum_points[key],
                "applicable": maximum_points[key] > 0,
            }
            for key in points
        },
    }


def _normalize_keywords(keywords: Sequence[str]) -> list[str]:
    result = []
    seen = set()
    for value in keywords:
        keyword = str(value).strip()
        identity = keyword.casefold()
        if keyword and identity not in seen:
            result.append(keyword)
            seen.add(identity)
    return result


def _coverage_points(*, covered_count: int, total_count: int) -> int:
    if total_count == 0:
        return 0
    return int((15 * covered_count / total_count) + 0.5)


def _paragraph_count(content: str) -> int:
    blocks = re.split(r"(?:\r?\n\s*){2,}", content.strip())
    return len([block for block in blocks if block.strip()])


def _has_h2_or_h3(content: str) -> bool:
    return bool(
        re.search(r"(?m)^\s*#{2,3}\s+\S", content)
        or re.search(r"<h[23](?:\s[^>]*)?>.*?</h[23]>", content, re.IGNORECASE | re.DOTALL)
    )


def _has_faq(content: str) -> bool:
    heading_pattern = r"(?:^|\n)\s*(?:#{1,6}\s*)?(?:FAQ|Q&A|常见问题|常见问答)\s*(?:\n|$)"
    if re.search(heading_pattern, content, re.IGNORECASE):
        return True
    question_markers = re.findall(
        r"(?:^|\n)\s*(?:Q\s*[:：]|问\s*[:：])",
        content,
        re.IGNORECASE,
    )
    return len(question_markers) >= 2


def _has_numeric_fact(content: str) -> bool:
    without_urls = re.sub(r"https?://\S+|www\.\S+", "", content, flags=re.IGNORECASE)
    # A publication year or identifier inside the bibliography describes the
    # citation, not a factual claim in the article body.
    without_urls = _REFERENCE_LINE_PATTERN.sub("", without_urls)
    explicit_unit = re.search(
        rf"{_NUMBER_PATTERN}\s*{_FACT_UNIT_PATTERN}",
        without_urls,
        re.IGNORECASE,
    )
    if explicit_unit:
        return True
    return bool(
        re.search(
            rf"{_FACT_CONTEXT_PATTERN}\s*(?:为|是|达到|约|共|[:：=])?\s*[vV]?{_NUMBER_PATTERN}",
            without_urls,
            re.IGNORECASE,
        )
    )


def _has_external_source(content: str) -> bool:
    for match in _INLINE_HTTP_URL_PATTERN.finditer(content):
        candidate = match.group(0).rstrip(".,;:!?，。；：！？'\"")
        if _is_public_http_url(candidate):
            return True

    for match in _REFERENCE_LINE_PATTERN.finditer(content):
        reference = match.group("reference").strip()
        if not reference or reference in {"-", "--", "---", "待补充", "暂无"}:
            continue
        if _DOI_PATTERN.search(reference):
            return True
        if (
            _FORMAL_REFERENCE_TITLE_PATTERN.search(reference)
            and _REFERENCE_YEAR_PATTERN.search(reference)
        ):
            return True
    return False


def _is_public_http_url(value: str) -> bool:
    """Validate a syntactically public HTTP(S) citation without network I/O."""

    try:
        normalized = normalize_citation_url(value)
        parsed = urlsplit(str(normalized["canonical_url"]))
        port = parsed.port
    except (KeyError, TypeError, ValueError, VisibilityAnalysisError):
        return False
    if parsed.scheme.casefold() not in {"http", "https"} or port == 0:
        return False
    hostname = str(parsed.hostname or "").rstrip(".").casefold()
    if not hostname or hostname in {"localhost", "localhost.localdomain", "internal"}:
        return False
    if any(hostname.endswith(suffix) for suffix in _NON_PUBLIC_HOST_SUFFIXES):
        return False
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        numeric_hostname = re.fullmatch(
            r"(?:0x[0-9a-f]+|[0-9]+)(?:\.(?:0x[0-9a-f]+|[0-9]+))*",
            hostname,
        )
        if "." not in hostname or numeric_hostname:
            return False
    else:
        if (
            not address.is_global
            or address.is_loopback
            or address.is_private
            or address.is_link_local
            or address.is_unspecified
            or address.is_reserved
            or address.is_multicast
        ):
            return False
    return True


def _has_summary(content: str) -> bool:
    return bool(
        re.search(
            r"(?:^|\n)\s*(?:#{1,6}\s*)?(?:总结|结语|小结)\s*(?:\n|$)",
            content,
        )
    )


def _as_percentage(points: int, maximum: int) -> int | None:
    if maximum <= 0:
        return None
    return round(points * 100 / maximum)


def _public_length_profile(
    profile: dict[str, Any],
    *,
    target_length: int | None,
    actual_length: int,
    source: str,
    requested_target_length: int | None,
    effective_target_length: int | None,
    over_target: bool,
    dimensions: dict[str, int | None],
    effective_max_points: int,
    evaluated_keywords: Sequence[str],
    deferred_keyword_count: int,
) -> dict[str, Any]:
    band = str(profile["band"])
    labels = {
        "legacy": "兼容长文规则",
        "compact": "精简稿（200–400字）",
        "short": "短稿（401–799字）",
        "standard": "标准稿（800–1199字）",
        "long": "长稿（1200–3000字）",
    }
    applicable_dimensions = [key for key, value in dimensions.items() if value is not None]
    not_applicable_dimensions = [key for key, value in dimensions.items() if value is None]
    return {
        "version": profile["version"],
        "key": band,
        "label": labels[band],
        "target_length": target_length,
        "requested_target_length": requested_target_length,
        "effective_target_length": effective_target_length,
        "over_target": over_target,
        "actual_length": actual_length,
        "minimum_length": int(profile["minimum_content_length"]),
        "source": source,
        "applicable_dimensions": applicable_dimensions,
        "not_applicable_dimensions": not_applicable_dimensions,
        "criteria": {
            "preset": profile["preset"],
            "scoring_target_length": profile.get("requested_length"),
            "minimum_paragraphs": int(profile["minimum_paragraphs"]),
            "headings_required": bool(profile["applicable_checks"]["headings"]),
            "faq_required": bool(profile["applicable_checks"]["faq"]),
            "summary_required": bool(profile["applicable_checks"]["summary"]),
            "effective_max_points": effective_max_points,
            "evaluated_keywords": list(evaluated_keywords),
            "deferred_keyword_count": deferred_keyword_count,
        },
        "guidance": [str(profile["scoring_policy"])],
    }


def _build_suggestions(
    *,
    brand: str,
    keywords: Sequence[str],
    covered_keywords: Sequence[str],
    brand_present: bool,
    title_has_keyword: bool,
    length_reached: bool,
    multiple_paragraphs: bool,
    has_headings: bool,
    has_faq: bool,
    has_facts: bool,
    has_source: bool,
    has_summary: bool,
    no_keyword_stuffing: bool,
    minimum_content_length: int,
    minimum_paragraphs: int,
    applicable: dict[str, bool],
) -> list[str]:
    suggestions = []
    if not brand_present:
        suggestions.append(f"建议在正文中自然出现品牌名称“{brand}”")

    if not keywords:
        suggestions.append("建议提供并覆盖至少一个核心关键词")
    else:
        covered_identities = {keyword.casefold() for keyword in covered_keywords}
        missing_keywords = [
            keyword
            for keyword in keywords
            if keyword.casefold() not in covered_identities
        ]
        if missing_keywords:
            suggestions.append(
                f"建议自然补充未覆盖的核心关键词：{'、'.join(missing_keywords)}"
            )
        if not title_has_keyword:
            suggestions.append("建议在标题中自然包含一个核心关键词")

    if not length_reached:
        suggestions.append(f"建议将正文扩充至至少 {minimum_content_length} 字")
    if not multiple_paragraphs:
        suggestions.append(f"建议将正文拆分为至少 {minimum_paragraphs} 个清晰段落")
    if applicable["headings"] and not has_headings:
        suggestions.append("建议增加清晰的 H2/H3 小标题")
    if applicable["faq"] and not has_faq:
        suggestions.append("建议增加 2–3 个常见问题及答案")
    if not has_facts:
        suggestions.append("建议补充可核实的数字或事实数据")
    if not has_source:
        suggestions.append("建议增加权威数据来源或外部链接")
    if applicable["summary"] and not has_summary:
        suggestions.append("建议增加总结或结语部分")
    if not no_keyword_stuffing:
        suggestions.append("核心关键词出现过于密集，建议降低重复频率")
    return suggestions
