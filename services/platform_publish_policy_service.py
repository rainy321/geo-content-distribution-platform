from __future__ import annotations

from copy import deepcopy
import re
from typing import Any, Iterable

from services.platform_capability_service import (
    get_platform_capability,
    normalize_platform_key,
)


PLATFORM_PUBLISH_POLICY_VERSION = "2026-09-16.1"


# This matrix intentionally distinguishes a verified field constraint from a
# product-side recommendation.  A missing hard limit means "not verified in
# this repository", not "unlimited"; a product safe limit may still keep local
# publication bounded.  Do not turn either into an invented platform quota.
_PLATFORM_PUBLISH_POLICIES: dict[str, dict[str, Any]] = {
    "zhihu": {
        "format": "plain_article",
        "title": {
            "min_length": 1,
            "hard_limit": 100,
            "safe_limit": 100,
            "basis": "repository_uploader_guard",
        },
        "content": {
            "hard_limit": None,
            "safe_limit": 3000,
            "basis": "not_verified",
            "safe_limit_basis": "product_conservative_guardrail",
        },
        "tags": {
            "submission_mode": "not_submitted_by_article_adapter",
            "max_count": 0,
            "basis": "repository_adapter_behavior",
        },
    },
    "toutiao": {
        "format": "plain_article",
        "title": {
            "min_length": 1,
            "hard_limit": 30,
            "safe_limit": 30,
            "basis": "repository_uploader_guard",
        },
        "content": {
            "hard_limit": None,
            "safe_limit": 3000,
            "basis": "not_verified",
            "safe_limit_basis": "product_conservative_guardrail",
        },
        "tags": {
            "submission_mode": "first_matching_topic_once",
            "max_count": 1,
            "basis": "repository_adapter_behavior",
        },
    },
    "baijiahao": {
        "format": "plain_article",
        "title": {
            "min_length": 2,
            "hard_limit": 64,
            "safe_limit": 64,
            "basis": "repository_uploader_guard",
        },
        "content": {
            "hard_limit": None,
            "safe_limit": 3000,
            "basis": "not_verified",
            "safe_limit_basis": "product_conservative_guardrail",
        },
        "tags": {
            "submission_mode": "not_submitted_by_article_adapter",
            "max_count": 0,
            "basis": "product_operational_guardrail",
        },
    },
    "sohu": {
        "format": "conservative_enterprise_education_article",
        "title": {
            "min_length": 5,
            "hard_limit": 72,
            "safe_limit": 72,
            "basis": "repository_uploader_guard",
        },
        "content": {
            "hard_limit": None,
            "safe_limit": 3000,
            "basis": "not_verified",
            "safe_limit_basis": "product_conservative_guardrail",
        },
        "tags": {
            "submission_mode": "independent_topic_field_if_available",
            "max_count": 3,
            "basis": "product_conservative_guardrail",
            "disclaimer": "0-3 个是产品侧保守策略，不是搜狐号官方配额。",
        },
    },
    "xiaohongshu": {
        "format": "plain_image_note",
        "title": {
            "min_length": 1,
            "hard_limit": 20,
            "safe_limit": 20,
            "basis": "repository_uploader_guard",
        },
        "content": {
            "hard_limit": 1000,
            "safe_limit": 800,
            "fixed_reserve": 30,
            "tags_share_content_limit": True,
            "basis": "user_editor_screenshot_2026_09_10",
            "safe_limit_basis": "geo_conservative_headroom",
        },
        "tags": {
            "submission_mode": "topic_picker",
            "max_count": 5,
            "basis": "product_operational_guardrail",
        },
    },
    "douyin": {
        "format": "plain_image_note",
        "title": {
            "min_length": 1,
            "hard_limit": 20,
            "safe_limit": 18,
            "basis": "repository_uploader_guard",
            "safe_limit_basis": "geo_conservative_headroom",
        },
        "content": {
            "hard_limit": 1000,
            "safe_limit": 800,
            "fixed_reserve": 24,
            "tags_share_content_limit": True,
            "basis": "existing_repository_policy",
        },
        "tags": {
            "submission_mode": "literal_hashtags",
            "max_count": 5,
            "basis": "product_operational_guardrail",
        },
    },
    "kuaishou": {
        "format": "plain_image_note",
        "title": {
            "min_length": 1,
            "hard_limit": None,
            "safe_limit": 20,
            "basis": "not_verified",
            "safe_limit_basis": "product_conservative_guardrail",
        },
        "content": {
            "hard_limit": 500,
            "safe_limit": 380,
            "fixed_reserve": 18,
            "tags_share_content_limit": True,
            "basis": "existing_repository_policy",
        },
        "tags": {
            "submission_mode": "literal_hashtags",
            "max_count": 4,
            "basis": "repository_adapter_behavior",
        },
    },
    "bilibili": {
        "format": "plain_video_description",
        "title": {
            "min_length": 1,
            "hard_limit": None,
            "safe_limit": 80,
            "basis": "not_verified",
            "safe_limit_basis": "product_conservative_guardrail",
        },
        "content": {
            "hard_limit": 2000,
            "safe_limit": 1800,
            "fixed_reserve": 0,
            "tags_share_content_limit": False,
            "basis": "existing_repository_policy",
        },
        "tags": {
            "submission_mode": "comma_separated_cli_field",
            # These are internal safety bounds for the bundled CLI invocation,
            # not claims about Bilibili's current official limits.
            "max_count": 6,
            "max_length": 20,
            "basis": "product_conservative_guardrail",
        },
    },
    "channels": {
        "format": "plain_video_description",
        "title": {
            "min_length": 1,
            "hard_limit": None,
            "safe_limit": 30,
            "basis": "not_verified",
            "safe_limit_basis": "product_conservative_guardrail",
        },
        "content": {
            "hard_limit": None,
            "safe_limit": 800,
            "basis": "not_verified",
            "safe_limit_basis": "product_conservative_guardrail",
        },
        "tags": {
            "submission_mode": "literal_hashtags",
            "max_count": None,
            "basis": "not_verified",
        },
    },
    "tiktok": {
        "format": "plain_video_description",
        "title": {
            "min_length": 1,
            "hard_limit": None,
            "safe_limit": 80,
            "basis": "not_verified",
            "safe_limit_basis": "product_conservative_guardrail",
        },
        "content": {
            "hard_limit": None,
            "safe_limit": 800,
            "basis": "not_verified",
            "safe_limit_basis": "product_conservative_guardrail",
        },
        "tags": {
            "submission_mode": "literal_hashtags",
            # This is a product-side operational guardrail, not a claim about
            # TikTok's current platform quota. Keeping the browser interaction
            # bounded prevents long tag lists from exhausting the job timeout.
            "max_count": 5,
            "basis": "product_operational_guardrail",
        },
    },
    "weibo": {
        "format": "plain_social_post",
        "title": {
            "min_length": 1,
            "hard_limit": None,
            "safe_limit": 80,
            "basis": "not_verified",
            "safe_limit_basis": "product_conservative_guardrail",
        },
        "content": {
            "hard_limit": None,
            "safe_limit": 620,
            "fixed_reserve": 0,
            "tags_share_content_limit": False,
            "basis": "not_verified",
            "safe_limit_basis": "product_conservative_guardrail",
        },
        "tags": {
            "submission_mode": "literal_hashtags",
            "max_count": 5,
            "max_length": 16,
            "basis": "product_operational_guardrail",
        },
    },
}


_UNFINISHED_MARKER_PATTERN = re.compile(
    r"(?:\[(?:待补充|作者占位|日期占位|来源占位|证据占位)[^\]]*\]|"
    r"(?:作者|更新(?:日期|时间)|来源|证据)\s*[:：]\s*(?:待补充|TBD|TODO)|"
    r"待补充(?:证据|来源))",
    flags=re.IGNORECASE,
)
_UNVERIFIED_SOURCE_PATTERN = re.compile(
    r"(?:未经核实|未经验证|来源不明|无法核验|待核验|待补充(?:证据|来源))",
    flags=re.IGNORECASE,
)
_APPROVAL_GUARANTEE_PATTERN = re.compile(
    r"(?<!不)(?<!不能)(?<!无法)(?<!不可)(?<!不会)"
    r"(?:(?:保证|确保|承诺).{0,8}(?:审核)?通过|(?:审核)?包过|"
    r"100\s*%\s*(?:审核)?通过)",
    flags=re.IGNORECASE,
)
_ABSOLUTE_CLAIM_PATTERN = re.compile(
    r"(?<!不)(?<!不能)(?<!无法)(?<!不可)(?<!不会)"
    r"(?:(?:保证|确保).{0,8}(?:收录|推荐|排名)|100\s*%\s*(?:收录|推荐)|"
    r"全网最低|行业第一|唯一(?:选择|方案|平台)|永久免费|绝对安全)",
    flags=re.IGNORECASE,
)
_PROMOTIONAL_CTA_PATTERN = re.compile(
    r"(?:扫码|加微(?:信)?|私信).{0,8}(?:咨询|领取|购买)|"
    r"立即(?:购买|下单|咨询)|限时(?:抢购|优惠)|免费领取|"
    r"(?:点击|访问|前往).{0,8}(?:官网|链接|网站)|"
    r"(?:联系|咨询)(?:客服|我们)",
    flags=re.IGNORECASE,
)
_PUBLIC_URL_PATTERN = re.compile(
    r"(?:https?://|www\.)[^\s<>]+",
    flags=re.IGNORECASE,
)
_SOHU_CONTACT_PATTERN = re.compile(
    r"(?:微信|微\s*信|V\s*X|QQ|扣扣|电话|手机(?:号)?|联系(?:方式|人)?|"
    r"客服|邮箱|E-?mail)\s*[:：]?\s*[A-Za-z0-9_@.+-]{4,}|"
    r"(?<!\d)1[3-9]\d{9}(?!\d)|"
    r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}",
    flags=re.IGNORECASE,
)
_SOHU_PURE_REDIRECT_URL_LINE_PATTERN = re.compile(
    r"(?m)^\s*(?:(?:官网|官方网站|网址|链接|详情(?:链接)?|购买链接|"
    r"咨询链接|访问(?:地址|链接|官网)?|了解更多)\s*[:：]?\s*)?"
    r"(?:https?://|www\.)\S+\s*[。.]?\s*$",
    flags=re.IGNORECASE,
)
_SOHU_SEO_QUESTION_TITLE_PATTERN = re.compile(
    r"(?:如何|怎么|怎样|哪个|哪些|哪家|哪个好|怎么选|值得吗|靠谱吗|"
    r"是什么|为什么|指南|攻略|推荐).{0,24}[？?]?$",
    flags=re.IGNORECASE,
)
_SOHU_BRAND_MARKETING_PATTERN = re.compile(
    r"(?:品牌|公司|企业|产品|服务|平台|官网|解决方案|推出|提供|支持|"
    r"价格|计费|购买|优惠|推荐|首选)",
    flags=re.IGNORECASE,
)
_SOHU_TRAILING_HASHTAG_LINE_PATTERN = re.compile(
    r"^\s*(?:#[^\s#，,。；;]{1,32}\s*){1,10}$"
)
_SOHU_TRAILING_HASHTAG_SUFFIX_PATTERN = re.compile(
    r"(?:\s+#[^\s#，,。；;]{1,32}){1,10}\s*$"
)
_NO_PUBLIC_EVIDENCE_PATTERN = re.compile(
    r"(?:暂无|没有|未发现).{0,8}可核验(?:的)?公开(?:证据|来源)",
    flags=re.IGNORECASE,
)
_LITERAL_MARKDOWN_PATTERN = re.compile(
    r"(?m)^\s{0,3}#{1,6}\s+\S|^\s*```|^\s{0,3}(?:[-*_]\s*){3,}$|"
    r"\*\*[^*\n]+\*\*|__[^_\n]+__|!\[[^\]]*\]\([^\)]*\)|"
    r"\[[^\]]+\]\([^\)]*\)"
)


def get_platform_publish_policy(platform: str) -> dict[str, Any]:
    """Return one complete policy record for every supported platform.

    Numerical limits are accompanied by their evidence basis.  Unknown limits
    remain ``None`` so callers cannot mistake an internal guess for an official
    quota.
    """

    normalized = normalize_platform_key(platform)
    configured = _PLATFORM_PUBLISH_POLICIES.get(normalized)
    if configured is None:
        raise ValueError("暂不支持该发布平台")
    capability = get_platform_capability(normalized)
    if capability is None:  # pragma: no cover - matrix/capability invariant
        raise ValueError("暂不支持该发布平台")

    result = deepcopy(configured)
    result.update(
        {
            "version": PLATFORM_PUBLISH_POLICY_VERSION,
            "platform": normalized,
            "platform_name": capability["name"],
            "media": {
                "content_mode": capability["content_mode"],
                "requires_images": bool(capability["requires_images"]),
                "requires_video": bool(capability["requires_video"]),
                "supports_schedule": bool(capability["supports_schedule"]),
                "basis": "repository_platform_capability",
            },
            "review": {
                "precheck_scope": "offline_deterministic_checks_only",
                "guaranteed_pass": False,
                "disclaimer": (
                    "离线预检只能检查本地可确定的字段、格式和风险表达；"
                    "平台规则、账号状态和动态审核仍可能导致退回，软件不保证审核通过。"
                ),
            },
        }
    )
    return result


def list_platform_publish_policies() -> list[dict[str, Any]]:
    return [get_platform_publish_policy(key) for key in _PLATFORM_PUBLISH_POLICIES]


def normalize_policy_tags(
    tags: Iterable[Any] | None,
    *,
    max_length: int | None = None,
) -> list[str]:
    """Normalize tags and optionally enforce a per-tag product safety bound."""

    if tags is None or isinstance(tags, (str, bytes)):
        return []
    if max_length is not None:
        if isinstance(max_length, bool) or not isinstance(max_length, int):
            raise ValueError("标签长度安全线必须是整数")
        if max_length <= 0:
            raise ValueError("标签长度安全线必须大于 0")
    result: list[str] = []
    seen: set[str] = set()
    for raw in tags:
        tag = re.sub(r"\s+", " ", str(raw or "").replace("\u200b", " "))
        tag = re.sub(r"^#+", "", tag).strip()
        if max_length is not None:
            tag = tag[:max_length].rstrip()
        identity = tag.casefold()
        if not tag or identity in seen:
            continue
        seen.add(identity)
        result.append(tag)
    return result


def adapt_tags_for_platform(
    platform: str,
    tags: Iterable[Any] | None,
) -> dict[str, Any]:
    """Mirror only deterministic adapter-side tag selection.

    The returned list is a preview of what the bundled adapter can attempt. It
    does not claim that a dynamic platform topic picker will accept the tag.
    """

    policy = get_platform_publish_policy(platform)
    tag_policy = policy["tags"]
    original = normalize_policy_tags(tags)
    maximum = tag_policy.get("max_count")
    maximum_length = tag_policy.get("max_length")

    candidates: list[tuple[str, str]] = []
    truncated: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    seen_candidates: set[str] = set()
    for original_tag in original:
        normalized = normalize_policy_tags(
            [original_tag],
            max_length=(int(maximum_length) if maximum_length is not None else None),
        )
        if not normalized:  # pragma: no cover - normalized originals are non-empty
            dropped.append(
                {
                    "tag": original_tag,
                    "reason": "empty_after_length_normalization",
                }
            )
            continue
        safe_tag = normalized[0]
        if safe_tag != original_tag:
            truncated.append(
                {
                    "original": original_tag,
                    "selected": safe_tag,
                    "max_length": int(maximum_length),
                }
            )
        identity = safe_tag.casefold()
        if identity in seen_candidates:
            dropped.append(
                {
                    "tag": original_tag,
                    "normalized": safe_tag,
                    "reason": "duplicate_after_truncation",
                }
            )
            continue
        seen_candidates.add(identity)
        candidates.append((original_tag, safe_tag))

    selected_candidates = (
        candidates if maximum is None else candidates[: int(maximum)]
    )
    if maximum is not None:
        dropped.extend(
            {
                "tag": original_tag,
                "normalized": safe_tag,
                "reason": "max_count",
            }
            for original_tag, safe_tag in candidates[int(maximum) :]
        )
    selected = [safe_tag for _original_tag, safe_tag in selected_candidates]
    audit = {
        "dropped_count": len(dropped),
        "dropped": list(dropped),
        "truncated_count": len(truncated),
        "truncated": list(truncated),
    }
    return {
        "original": original,
        "selected": selected,
        "adapted": selected != original,
        "dropped_count": audit["dropped_count"],
        "dropped": list(dropped),
        "dropped_tags": list(dropped),
        "truncated_count": audit["truncated_count"],
        "truncated": list(truncated),
        "truncated_tags": list(truncated),
        "audit": audit,
        "policy": tag_policy,
    }


def sanitize_sohu_publish_copy(content: str) -> dict[str, Any]:
    """Remove deterministic Sohu diversion residue from an automatic copy.

    This is deliberately loss-minimising: only clauses containing a clear
    call-to-action/contact marker, pure redirect URL lines, trailing hashtag
    tails and exact repeated sentences are removed.  It never adds claims or
    mutates the source article.
    """

    original = str(content or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    lines = original.split("\n")
    removed_hashtag_tails = 0
    while lines and not lines[-1].strip():
        lines.pop()
    while lines and _SOHU_TRAILING_HASHTAG_LINE_PATTERN.fullmatch(lines[-1]):
        lines.pop()
        removed_hashtag_tails += 1
        while lines and not lines[-1].strip():
            lines.pop()

    cleaned_lines: list[str] = []
    seen_sentences: set[str] = set()
    removed_redirect_lines = 0
    removed_contact_clauses = 0
    removed_cta_clauses = 0
    removed_duplicate_sentences = 0

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            if cleaned_lines and cleaned_lines[-1] != "":
                cleaned_lines.append("")
            continue
        if _SOHU_PURE_REDIRECT_URL_LINE_PATTERN.fullmatch(line):
            removed_redirect_lines += 1
            continue

        suffix_match = _SOHU_TRAILING_HASHTAG_SUFFIX_PATTERN.search(line)
        if suffix_match:
            line = line[: suffix_match.start()].rstrip()
            removed_hashtag_tails += 1
            if not line:
                continue

        kept_fragments: list[str] = []
        for fragment in re.split(r"(?<=[，,；;。！？!?])", line):
            clause = fragment.strip()
            if not clause:
                continue
            if _SOHU_CONTACT_PATTERN.search(clause):
                removed_contact_clauses += 1
                continue
            if _PROMOTIONAL_CTA_PATTERN.search(clause):
                removed_cta_clauses += 1
                continue

            identity = re.sub(r"[\s，,。；;！!？?]+", "", clause).casefold()
            if len(identity) >= 8 and identity in seen_sentences:
                removed_duplicate_sentences += 1
                continue
            if len(identity) >= 8:
                seen_sentences.add(identity)
            kept_fragments.append(clause)

        if kept_fragments:
            cleaned_lines.append("".join(kept_fragments))

    while cleaned_lines and not cleaned_lines[-1]:
        cleaned_lines.pop()
    sanitized = "\n".join(cleaned_lines)
    sanitized = re.sub(r"\n{3,}", "\n\n", sanitized).strip()
    audit = {
        "applied": True,
        "changed": sanitized != original,
        "removed_redirect_lines": removed_redirect_lines,
        "removed_contact_clauses": removed_contact_clauses,
        "removed_cta_clauses": removed_cta_clauses,
        "removed_hashtag_tails": removed_hashtag_tails,
        "removed_duplicate_sentences": removed_duplicate_sentences,
    }
    return {"content": sanitized, "audit": audit}


def evaluate_platform_offline_preflight(
    *,
    platform: str,
    title: str,
    content: str,
    tags: Iterable[Any] | None = None,
) -> dict[str, Any]:
    """Return explainable checks without predicting dynamic moderation."""

    policy = get_platform_publish_policy(platform)
    normalized_title = str(title or "").strip()
    normalized_content = str(content or "").strip()
    tag_preview = adapt_tags_for_platform(platform, tags)
    issues: list[dict[str, str]] = []

    title_policy = policy["title"]
    title_minimum = int(title_policy.get("min_length") or 1)
    title_maximum = title_policy.get("safe_limit")
    if len(normalized_title) < title_minimum:
        issues.append(
            _issue(
                "title_too_short",
                "blocking",
                "title",
                f"标题当前 {len(normalized_title)} 字，至少需要 {title_minimum} 字。",
                "补充一个准确、完整且不夸大的标题。",
            )
        )
    if title_maximum is not None and len(normalized_title) > int(title_maximum):
        issues.append(
            _issue(
                "title_over_safe_limit",
                "blocking",
                "title",
                f"标题当前 {len(normalized_title)} 字，超过 {title_maximum} 字安全线。",
                "使用预检生成的短标题，或手动缩短后再次检查。",
            )
        )

    content_limit = policy["content"].get("safe_limit")
    if content_limit is not None and len(normalized_content) > int(content_limit):
        issues.append(
            _issue(
                "content_over_safe_limit",
                "blocking",
                "content",
                f"正文当前 {len(normalized_content)} 字，超过 {content_limit} 字安全线。",
                "使用平台自动短版，保留结论、边界和可核验来源。",
            )
        )

    if _UNFINISHED_MARKER_PATTERN.search(normalized_title + "\n" + normalized_content):
        issues.append(
            _issue(
                "unfinished_placeholder",
                "blocking",
                "content",
                "内容仍包含作者、日期、来源或证据的待补充占位符。",
                "删除占位字段；只有已知且已核验的作者、日期或来源才能写入正文。",
            )
        )
    if _LITERAL_MARKDOWN_PATTERN.search(normalized_content):
        issues.append(
            _issue(
                "literal_markdown_syntax",
                "blocking",
                "content",
                "平台副本仍包含会被编辑器当作普通文字显示的 Markdown 标记。",
                "恢复自动平台副本，或删除标题井号、星号、分隔线和链接括号后再次预检。",
            )
        )
    if _UNVERIFIED_SOURCE_PATTERN.search(normalized_content):
        issues.append(
            _issue(
                "unverified_source_marker",
                "blocking",
                "content",
                "正文把未核验或待补充的信息写成了来源。",
                "删除该来源，或替换为当前可公开访问并能支持对应事实的链接。",
            )
        )
    if _NO_PUBLIC_EVIDENCE_PATTERN.search(normalized_content):
        issues.append(
            _issue(
                "public_evidence_unavailable",
                "warning",
                "content",
                "正文明确说明当前缺少可核验的公开证据。",
                "保留该事实边界，不要把待补内容伪装成来源；发布前可补充真实公开链接。",
            )
        )
    if _APPROVAL_GUARANTEE_PATTERN.search(normalized_content):
        issues.append(
            _issue(
                "approval_guarantee_claim",
                "blocking",
                "content",
                "正文包含保证或包过平台审核的表述，但动态审核无法由软件保证。",
                "改成可核验的能力和适用条件，并明确最终结果以平台审核为准。",
            )
        )
    if _ABSOLUTE_CLAIM_PATTERN.search(normalized_title + "\n" + normalized_content):
        issues.append(
            _issue(
                "absolute_claim_needs_evidence",
                "warning",
                "content",
                "检测到第一、唯一、永久免费或保证收录等绝对化表达。",
                "删除绝对化表述，或提供能直接支持该表述的公开证据和适用范围。",
            )
        )
    if tag_preview["dropped_count"]:
        maximum = policy["tags"]["max_count"]
        if maximum == 0:
            tag_message = (
                f"原稿有 {len(tag_preview['original'])} 个标签；"
                "当前文章适配器不向该平台提交标签字段。"
            )
            tag_suggestion = "标签仍保留在原稿记录中，不要为此在正文末尾机械堆叠关键词。"
        else:
            tag_message = (
                f"原稿有 {len(tag_preview['original'])} 个标签；"
                f"当前适配器只尝试前 {maximum} 个。"
            )
            tag_suggestion = (
                "把最相关、最可能匹配的平台话题放在前面；"
                "未匹配时停止，不循环换词。"
            )
        issues.append(
            _issue(
                "tags_trimmed_for_adapter",
                "warning",
                "tags",
                tag_message,
                tag_suggestion,
            )
        )
    if tag_preview["truncated_count"]:
        maximum_length = policy["tags"].get("max_length")
        issues.append(
            _issue(
                "tags_truncated_for_adapter",
                "warning",
                "tags",
                (
                    f"有 {tag_preview['truncated_count']} 个标签超过产品侧 "
                    f"{maximum_length} 字安全线，适配副本已截短；"
                    "该安全线不是平台官方配额。"
                ),
                "发布前查看标签适配审计，确认截短后的标签仍准确表达主题。",
            )
        )

    if policy["format"] == "conservative_enterprise_education_article":
        has_external_url = bool(_PUBLIC_URL_PATTERN.search(normalized_content))
        has_contact = bool(_SOHU_CONTACT_PATTERN.search(normalized_content))
        has_promotional_cta = bool(_PROMOTIONAL_CTA_PATTERN.search(normalized_content))
        has_pure_redirect_url = bool(
            _SOHU_PURE_REDIRECT_URL_LINE_PATTERN.search(normalized_content)
        )
        if has_external_url:
            issues.append(
                _issue(
                    "sohu_external_url_review_risk",
                    "warning",
                    "content",
                    "搜狐号副本包含站外 URL，可能被平台结合上下文判断为导流。",
                    "仅保留确实支持正文事实的来源页；不要写官网、购买、咨询或了解更多入口。",
                )
            )
        if has_contact:
            issues.append(
                _issue(
                    "sohu_contact_information",
                    "blocking",
                    "content",
                    "搜狐号副本包含邮箱、手机号、微信、QQ 或其他联系方式。",
                    "从平台副本中删除联系方式；原稿和客户资料不会因此被改写。",
                )
            )
        if has_promotional_cta:
            issues.append(
                _issue(
                    "sohu_promotional_call_to_action",
                    "blocking",
                    "content",
                    "搜狐号副本包含扫码、私信、访问官网、限时或立即购买等强促销引导。",
                    "改成中性企业科普：说明问题、机制、适用场景、边界和可核验来源。",
                )
            )
        if has_pure_redirect_url or (
            has_external_url and (has_contact or has_promotional_cta)
        ):
            issues.append(
                _issue(
                    "sohu_high_confidence_external_redirect",
                    "blocking",
                    "content",
                    "搜狐号副本存在纯站外链接，或站外 URL 与联系方式、促销引导同时出现。",
                    "自动实发已阻断；删除纯导流入口，只保留与文章事实直接相关的中性说明。",
                )
            )
        if (
            _SOHU_SEO_QUESTION_TITLE_PATTERN.search(normalized_title)
            and _SOHU_BRAND_MARKETING_PATTERN.search(
                normalized_title + "\n" + normalized_content
            )
        ):
            issues.append(
                _issue(
                    "sohu_seo_question_brand_marketing_combo",
                    "warning",
                    "title",
                    "标题呈明显搜索问句或指南结构，同时正文含品牌、产品或服务营销语义。",
                    "改成具体的信息型标题，并让正文以判断标准、机制、证据和限制为主；不要堆叠同义问句。",
                )
            )
        paragraph_count = len(
            [item for item in re.split(r"\n\s*\n|\n", normalized_content) if item.strip()]
        )
        if paragraph_count < 3:
            issues.append(
                _issue(
                    "sohu_thin_article_structure",
                    "warning",
                    "content",
                    "搜狐号副本少于三个有效段落，企业科普信息结构偏薄。",
                    "按直接结论、机制或步骤、适用边界与来源组织至少三个自然段。",
                )
            )
    blocking_count = sum(issue["severity"] == "blocking" for issue in issues)
    warning_count = sum(issue["severity"] == "warning" for issue in issues)
    return {
        "policy_version": PLATFORM_PUBLISH_POLICY_VERSION,
        "passed": blocking_count == 0,
        "blocking_count": blocking_count,
        "warning_count": warning_count,
        "issues": issues,
        "tag_preview": tag_preview,
        "disclaimer": policy["review"]["disclaimer"],
    }


def _issue(
    code: str,
    severity: str,
    field: str,
    message: str,
    suggestion: str,
) -> dict[str, str]:
    return {
        "code": code,
        "severity": severity,
        "field": field,
        "message": message,
        "suggestion": suggestion,
    }


__all__ = [
    "PLATFORM_PUBLISH_POLICY_VERSION",
    "adapt_tags_for_platform",
    "evaluate_platform_offline_preflight",
    "get_platform_publish_policy",
    "list_platform_publish_policies",
    "normalize_policy_tags",
    "sanitize_sohu_publish_copy",
]
