from __future__ import annotations

from copy import deepcopy
import re
from typing import Any, Iterable

from services.platform_capability_service import (
    get_platform_capability,
    normalize_platform_key,
)


PLATFORM_PUBLISH_POLICY_VERSION = "2026-09-10.2"


# This matrix intentionally distinguishes a verified field constraint from a
# product-side recommendation.  A missing limit means "not verified in this
# repository", not "unlimited".  Do not turn unknown fields into invented
# platform quotas.
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
            "safe_limit": None,
            "basis": "not_verified",
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
            "safe_limit": None,
            "basis": "not_verified",
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
            "safe_limit": None,
            "basis": "not_verified",
        },
        "tags": {
            "submission_mode": "topic_picker",
            "max_count": 5,
            "basis": "repository_adapter_behavior",
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
            "safe_limit": None,
            "basis": "not_verified",
        },
        "tags": {
            "submission_mode": "topic_picker_and_body_hashtags",
            "max_count": 10,
            "basis": "repository_adapter_behavior",
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
            "safe_limit": 900,
            "fixed_reserve": 30,
            "tags_share_content_limit": True,
            "basis": "user_editor_screenshot_2026_09_10",
            "safe_limit_basis": "geo_conservative_headroom",
        },
        "tags": {
            "submission_mode": "topic_picker",
            "max_count": None,
            "basis": "not_verified",
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
            "safe_limit": 920,
            "fixed_reserve": 24,
            "tags_share_content_limit": True,
            "basis": "existing_repository_policy",
        },
        "tags": {
            "submission_mode": "literal_hashtags",
            "max_count": None,
            "basis": "not_verified",
        },
    },
    "kuaishou": {
        "format": "plain_image_note",
        "title": {
            "min_length": 1,
            "hard_limit": None,
            "safe_limit": None,
            "basis": "not_verified",
        },
        "content": {
            "hard_limit": 500,
            "safe_limit": 450,
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
            "safe_limit": None,
            "basis": "not_verified",
        },
        "content": {
            "hard_limit": 2000,
            "safe_limit": 2000,
            "fixed_reserve": 0,
            "tags_share_content_limit": False,
            "basis": "existing_repository_policy",
        },
        "tags": {
            "submission_mode": "comma_separated_cli_field",
            "max_count": None,
            "basis": "not_verified",
        },
    },
    "channels": {
        "format": "plain_video_description",
        "title": {
            "min_length": 1,
            "hard_limit": None,
            "safe_limit": None,
            "basis": "not_verified",
        },
        "content": {
            "hard_limit": None,
            "safe_limit": None,
            "basis": "not_verified",
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
            "safe_limit": None,
            "basis": "not_verified",
        },
        "content": {
            "hard_limit": None,
            "safe_limit": None,
            "basis": "not_verified",
        },
        "tags": {
            "submission_mode": "literal_hashtags",
            "max_count": None,
            "basis": "not_verified",
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
    r"立即(?:购买|下单|咨询)|限时(?:抢购|优惠)|免费领取",
    flags=re.IGNORECASE,
)
_PUBLIC_URL_PATTERN = re.compile(r"https?://[^\s<>]+", flags=re.IGNORECASE)
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


def normalize_policy_tags(tags: Iterable[Any] | None) -> list[str]:
    if tags is None or isinstance(tags, (str, bytes)):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for raw in tags:
        tag = re.sub(r"\s+", " ", str(raw or "").replace("\u200b", " "))
        tag = re.sub(r"^#+", "", tag).strip()
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
    original = normalize_policy_tags(tags)
    maximum = policy["tags"]["max_count"]
    selected = original if maximum is None else original[: int(maximum)]
    return {
        "original": original,
        "selected": selected,
        "adapted": selected != original,
        "dropped_count": len(original) - len(selected),
        "policy": policy["tags"],
    }


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
    if tag_preview["adapted"]:
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

    if policy["format"] == "conservative_enterprise_education_article":
        if _PROMOTIONAL_CTA_PATTERN.search(normalized_content):
            issues.append(
                _issue(
                    "sohu_promotional_call_to_action",
                    "warning",
                    "content",
                    "搜狐号副本包含扫码、私信、限时或立即购买等强促销引导。",
                    "改成中性企业科普：说明问题、机制、适用场景、边界和可核验来源。",
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
        if not _PUBLIC_URL_PATTERN.search(normalized_content):
            issues.append(
                _issue(
                    "sohu_no_public_source_url",
                    "warning",
                    "content",
                    "搜狐号副本没有可公开核验的来源链接。",
                    "若文章包含产品或事实性主张，请补充能直接支持该主张的公开页面。",
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
]
