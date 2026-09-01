from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any


MIN_CONTENT_LENGTH = 600
MIN_PARAGRAPHS = 3
MAX_KEYWORD_DENSITY = 0.08


def score_geo_content(
    *,
    title: str,
    content: str,
    brand: str,
    keywords: Sequence[str],
) -> dict[str, Any]:
    """Score GEO readiness using only the MVP rules from the task brief."""
    normalized_keywords = _normalize_keywords(keywords)
    combined_text = f"{title}\n{content}".casefold()
    normalized_brand = brand.strip()

    brand_present = normalized_brand.casefold() in combined_text
    covered_keywords = [
        keyword
        for keyword in normalized_keywords
        if keyword.casefold() in combined_text
    ]
    keyword_coverage_points = _coverage_points(
        covered_count=len(covered_keywords),
        total_count=len(normalized_keywords),
    )
    title_has_keyword = any(
        keyword.casefold() in title.casefold()
        for keyword in normalized_keywords
    )
    length_reached = _visible_length(content) >= MIN_CONTENT_LENGTH
    multiple_paragraphs = _paragraph_count(content) >= MIN_PARAGRAPHS
    has_headings = _has_h2_or_h3(content)
    has_faq = _has_faq(content)
    has_facts = _has_numeric_fact(content)
    has_source = _has_external_source(content)
    has_summary = _has_summary(content)
    no_keyword_stuffing = _has_no_keyword_stuffing(
        content,
        normalized_keywords,
    )

    points = {
        "brand": 15 if brand_present else 0,
        "keyword_coverage": keyword_coverage_points,
        "title_keyword": 10 if title_has_keyword else 0,
        "length": 10 if length_reached else 0,
        "paragraphs": 10 if multiple_paragraphs else 0,
        "headings": 10 if has_headings else 0,
        "faq": 10 if has_faq else 0,
        "facts": 5 if has_facts else 0,
        "source": 5 if has_source else 0,
        "summary": 5 if has_summary else 0,
        "no_stuffing": 5 if no_keyword_stuffing else 0,
    }

    dimensions = {
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
            35,
        ),
        "faq": _as_percentage(points["faq"], 10),
        "citation": _as_percentage(
            points["facts"] + points["source"],
            10,
        ),
    }

    suggestions = _build_suggestions(
        brand=normalized_brand,
        keywords=normalized_keywords,
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
    )

    return {
        "score": sum(points.values()),
        "dimensions": dimensions,
        "suggestions": suggestions,
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


def _visible_length(content: str) -> int:
    return len(re.sub(r"\s+", "", content))


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
    return bool(re.search(r"\d+(?:[.,]\d+)*(?:\s*(?:%|％|年|月|日|个|家|项|倍|元|万|亿))?", without_urls))


def _has_external_source(content: str) -> bool:
    return bool(
        re.search(r"https?://\S+|www\.\S+", content, re.IGNORECASE)
        or re.search(r"(?:数据来源|信息来源|参考资料|来源)\s*[:：]", content)
    )


def _has_summary(content: str) -> bool:
    return bool(
        re.search(
            r"(?:^|\n)\s*(?:#{1,6}\s*)?(?:总结|结语|小结)\s*(?:\n|$)",
            content,
        )
    )


def _has_no_keyword_stuffing(content: str, keywords: Sequence[str]) -> bool:
    if not keywords:
        return True
    normalized_content = content.casefold()
    visible_length = max(_visible_length(content), 1)
    keyword_characters = 0
    for keyword in keywords:
        normalized_keyword = keyword.casefold()
        occurrences = normalized_content.count(normalized_keyword)
        keyword_characters += occurrences * len(normalized_keyword)
        repeated_pattern = rf"(?:{re.escape(normalized_keyword)}\s*){{3,}}"
        if re.search(repeated_pattern, normalized_content):
            return False
    if visible_length < 200:
        return True
    return keyword_characters / visible_length <= MAX_KEYWORD_DENSITY


def _as_percentage(points: int, maximum: int) -> int:
    return round(points * 100 / maximum)


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
        suggestions.append(f"建议将正文扩充至至少 {MIN_CONTENT_LENGTH} 字")
    if not multiple_paragraphs:
        suggestions.append(f"建议将正文拆分为至少 {MIN_PARAGRAPHS} 个清晰段落")
    if not has_headings:
        suggestions.append("建议增加清晰的 H2/H3 小标题")
    if not has_faq:
        suggestions.append("建议增加 2–3 个常见问题及答案")
    if not has_facts:
        suggestions.append("建议补充可核实的数字或事实数据")
    if not has_source:
        suggestions.append("建议增加权威数据来源或外部链接")
    if not has_summary:
        suggestions.append("建议增加总结或结语部分")
    if not no_keyword_stuffing:
        suggestions.append("核心关键词出现过于密集，建议降低重复频率")
    return suggestions
