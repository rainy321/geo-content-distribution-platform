from __future__ import annotations

import re
from typing import Any, Iterable

from services.ai_visibility_service import (
    VisibilityAnalysisError,
    normalize_citation_url,
)
from services.content_hygiene_service import sanitize_generated_content
from services.platform_capability_service import normalize_platform_key
from services.platform_publish_policy_service import (
    adapt_tags_for_platform,
    evaluate_platform_offline_preflight,
    get_platform_publish_policy,
    sanitize_sohu_publish_copy,
)


PUBLISH_CONTENT_POLICY_VERSION = "2026-09-16.1"


class PublishContentLimitError(ValueError):
    """Raised before a publisher is loaded when a platform copy is unsafe."""


_CONTENT_POLICIES: dict[str, dict[str, Any]] = {
    "zhihu": {
        "content_mode": "article",
        "hard_limit": None,
        "default_safe_limit": 3000,
        "fixed_reserve": 0,
        "tags_share_content_limit": False,
        "label": "文章正文",
        "counting_note": "3000 字是产品侧保守安全线，不是知乎官方上限",
        "basis": "not_verified",
        "safe_limit_basis": "product_conservative_guardrail",
    },
    "toutiao": {
        "content_mode": "article",
        "hard_limit": None,
        "default_safe_limit": 3000,
        "fixed_reserve": 0,
        "tags_share_content_limit": False,
        "label": "文章正文",
        "counting_note": "3000 字是产品侧保守安全线，不是今日头条官方上限",
        "basis": "not_verified",
        "safe_limit_basis": "product_conservative_guardrail",
    },
    "baijiahao": {
        "content_mode": "image_article",
        "hard_limit": None,
        "default_safe_limit": 3000,
        "fixed_reserve": 0,
        "tags_share_content_limit": False,
        "label": "文章正文",
        "counting_note": "3000 字是产品侧保守安全线，不是百家号官方上限",
        "basis": "not_verified",
        "safe_limit_basis": "product_conservative_guardrail",
    },
    "sohu": {
        "content_mode": "article",
        "hard_limit": None,
        "default_safe_limit": 3000,
        "fixed_reserve": 0,
        "tags_share_content_limit": False,
        "label": "文章正文",
        "counting_note": "3000 字是产品侧保守安全线，不是搜狐号官方上限",
        "basis": "not_verified",
        "safe_limit_basis": "product_conservative_guardrail",
    },
    "xiaohongshu": {
        "content_mode": "image_note",
        "hard_limit": 1000,
        "default_safe_limit": 800,
        "fixed_reserve": 30,
        "tags_share_content_limit": True,
        "label": "笔记正文",
        "counting_note": (
            "按小红书编辑器截图显示的 1000 字上限预检；"
            "另为话题、换行和平台计数差异预留余量"
        ),
        "basis": "user_editor_screenshot_2026_09_10",
        "safe_limit_basis": "geo_conservative_headroom",
    },
    "douyin": {
        "content_mode": "image_note",
        "hard_limit": 1000,
        "default_safe_limit": 800,
        "fixed_reserve": 24,
        "tags_share_content_limit": True,
        "label": "作品描述",
        "counting_note": "按 Unicode 字符预估；另为话题、换行和平台计数差异预留余量",
        "basis": "existing_repository_policy",
        "safe_limit_basis": "existing_repository_policy",
    },
    "kuaishou": {
        "content_mode": "image_note",
        "hard_limit": 500,
        "default_safe_limit": 380,
        "fixed_reserve": 18,
        "tags_share_content_limit": True,
        "label": "作品描述",
        "counting_note": "按 Unicode 字符预估；另为话题、换行和平台计数差异预留余量",
        "basis": "existing_repository_policy",
        "safe_limit_basis": "existing_repository_policy",
    },
    "bilibili": {
        "content_mode": "video_description",
        "hard_limit": 2000,
        "default_safe_limit": 1800,
        "fixed_reserve": 0,
        "tags_share_content_limit": False,
        "label": "视频简介",
        "counting_note": "按 Unicode 字符计数；标题和标签由独立字段提交",
        "basis": "existing_repository_policy",
        "safe_limit_basis": "existing_repository_policy",
    },
    "channels": {
        "content_mode": "video_description",
        "hard_limit": None,
        "default_safe_limit": 800,
        "fixed_reserve": 0,
        "tags_share_content_limit": False,
        "label": "视频简介",
        "counting_note": "800 字是产品侧保守安全线，不是视频号官方上限",
        "basis": "not_verified",
        "safe_limit_basis": "product_conservative_guardrail",
    },
    "tiktok": {
        "content_mode": "video_description",
        "hard_limit": None,
        "default_safe_limit": 800,
        "fixed_reserve": 0,
        "tags_share_content_limit": False,
        "label": "视频简介",
        "counting_note": "800 字是产品侧保守安全线，不是 TikTok 官方上限",
        "basis": "not_verified",
        "safe_limit_basis": "product_conservative_guardrail",
    },
    "weibo": {
        "content_mode": "social_post",
        "hard_limit": None,
        "default_safe_limit": 620,
        "fixed_reserve": 0,
        "tags_share_content_limit": False,
        "label": "微博正文",
        "counting_note": "620 字是产品侧保守安全线，不是微博官方上限；标题、正文和最多五个截短话题合计预留在 800 字产品总预算内",
        "basis": "not_verified",
        "safe_limit_basis": "product_conservative_guardrail",
    },
}


_TITLE_POLICIES: dict[str, dict[str, Any]] = {
    # These limits mirror the fields used by the bundled uploaders.  Keeping
    # them here makes any shortening visible before a job is persisted.
    "zhihu": {"hard_limit": 100, "safe_limit": 100, "min_length": 1},
    # The current Douyin image-note publisher UI exposes a 20-character title
    # field.  Keep two characters of product-side headroom for platform-side
    # counting differences instead of letting a 20/20 snapshot reach submit.
    "douyin": {"hard_limit": 20, "safe_limit": 18, "min_length": 1},
    "toutiao": {"hard_limit": 30, "safe_limit": 30, "min_length": 1},
    "sohu": {"hard_limit": 72, "safe_limit": 72, "min_length": 5},
    "baijiahao": {"hard_limit": 64, "safe_limit": 64, "min_length": 2},
    "xiaohongshu": {"hard_limit": 20, "safe_limit": 20, "min_length": 1},
    # The remaining values are product-side conservative guardrails. Their
    # platform hard limits have not been verified in this repository.
    "kuaishou": {
        "hard_limit": None,
        "safe_limit": 20,
        "min_length": 1,
        "basis": "not_verified",
        "safe_limit_basis": "product_conservative_guardrail",
    },
    "bilibili": {
        "hard_limit": None,
        "safe_limit": 80,
        "min_length": 1,
        "basis": "not_verified",
        "safe_limit_basis": "product_conservative_guardrail",
    },
    "channels": {
        "hard_limit": None,
        "safe_limit": 30,
        "min_length": 1,
        "basis": "not_verified",
        "safe_limit_basis": "product_conservative_guardrail",
    },
    "tiktok": {
        "hard_limit": None,
        "safe_limit": 80,
        "min_length": 1,
        "basis": "not_verified",
        "safe_limit_basis": "product_conservative_guardrail",
    },
    "weibo": {
        "hard_limit": None,
        "safe_limit": 80,
        "min_length": 1,
        "basis": "not_verified",
        "safe_limit_basis": "product_conservative_guardrail",
    },
}

_PLAIN_TEXT_CONTENT_PLATFORMS = frozenset(
    {
        "zhihu",
        "toutiao",
        "baijiahao",
        "sohu",
        "xiaohongshu",
        "douyin",
        "kuaishou",
        "bilibili",
        "channels",
        "tiktok",
        "weibo",
    }
)

_GEO_PRESERVATION_PATTERN = re.compile(
    r"(?:https?://|参考(?:资料|来源)?|来源|证据|适用(?:条件|范围)|"
    r"限制|边界|风险|注意事项|常见问题|FAQ|问[:：]|答[:：])",
    flags=re.IGNORECASE,
)
_OUTBOUND_PUBLIC_URL_PATTERN = re.compile(
    r"(?:https?://|www\.)[^\s<>\]\[()（），。；：！？、“”‘’]+",
    re.IGNORECASE,
)


_VERIFICATION_POLICIES: dict[str, dict[str, Any]] = {
    "zhihu": {
        "methods": ("扫码确认", "短信验证", "安全验证"),
        "notice": "知乎可能要求扫码、短信或安全验证，请保持知乎 App 和绑定手机可用。",
    },
    "toutiao": {
        "methods": ("短信验证", "扫码确认", "安全验证"),
        "notice": "今日头条可能要求短信、扫码或安全验证，请保持绑定手机可用。",
    },
    "baijiahao": {
        "methods": ("滑块验证", "短信验证", "扫码确认"),
        "notice": "百家号可能在最终提交前要求滑块、短信或扫码验证。",
    },
    "sohu": {
        "methods": ("短信验证", "扫码确认", "安全验证"),
        "notice": "搜狐号可能要求短信、扫码或安全验证，请保持绑定手机可用。",
    },
    "xiaohongshu": {
        "methods": ("扫码确认", "短信验证", "安全验证"),
        "notice": "小红书可能要求 App 扫码、短信或安全验证，请保持小红书 App 可用。",
    },
    "douyin": {
        "methods": ("短信验证码", "扫码确认", "滑块验证"),
        "notice": "抖音可能在最终发布时要求短信验证码、扫码或滑块验证，请保持绑定手机和抖音 App 可用。",
    },
    "kuaishou": {
        "methods": ("短信验证码", "扫码确认", "安全验证"),
        "notice": "快手可能在最终发布时要求短信验证码、扫码或安全验证，请保持绑定手机和快手 App 可用。",
    },
    "bilibili": {
        "methods": ("扫码确认", "短信验证", "安全验证"),
        "notice": "Bilibili 可能要求扫码、短信或安全验证，请保持哔哩哔哩 App 和绑定手机可用。",
    },
    "channels": {
        "methods": ("管理员扫码", "安全确认"),
        "notice": "视频号可能要求管理员微信扫码或安全确认，请保持对应管理员微信可用。",
    },
    "tiktok": {
        "methods": ("邮件验证码", "短信验证", "安全验证"),
        "notice": "TikTok 可能要求邮件、短信或安全验证，请保持绑定邮箱和手机可用。",
    },
    "weibo": {
        "methods": ("官方 OAuth 浏览器授权", "设备码授权", "微博安全验证"),
        "notice": "微博仅通过官方 CLI OAuth 授权；可能要求浏览器、设备码或微博安全验证，不会绕过验证。",
    },
}


PLATFORM_FREQUENCY_GUIDANCE_DEFAULTS: dict[str, dict[str, int]] = {
    "zhihu": {"daily_recommended_max": 3, "cooldown_minutes": 60},
    "toutiao": {"daily_recommended_max": 5, "cooldown_minutes": 30},
    "baijiahao": {"daily_recommended_max": 3, "cooldown_minutes": 60},
    "sohu": {"daily_recommended_max": 3, "cooldown_minutes": 60},
    "xiaohongshu": {"daily_recommended_max": 3, "cooldown_minutes": 120},
    "douyin": {"daily_recommended_max": 3, "cooldown_minutes": 120},
    "kuaishou": {"daily_recommended_max": 3, "cooldown_minutes": 120},
    "bilibili": {"daily_recommended_max": 2, "cooldown_minutes": 180},
    "channels": {"daily_recommended_max": 3, "cooldown_minutes": 120},
    "tiktok": {"daily_recommended_max": 3, "cooldown_minutes": 120},
    "weibo": {"daily_recommended_max": 5, "cooldown_minutes": 30},
}


def get_publish_content_policy(
    platform: str,
    *,
    tags: Iterable[Any] | None = None,
) -> dict[str, Any] | None:
    """Return the effective platform limit after reserving tag space."""

    normalized_platform = _normalize_platform(platform)
    configured = _CONTENT_POLICIES.get(normalized_platform)
    if configured is None:
        return None

    tag_preview = adapt_tags_for_platform(normalized_platform, tags)
    normalized_tags = tag_preview["selected"]
    tags_share_content_limit = bool(configured.get("tags_share_content_limit"))
    tag_characters = (
        sum(character_count(tag) + 2 for tag in normalized_tags)
        if tags_share_content_limit
        else 0
    )
    configured_hard_limit = configured.get("hard_limit")
    hard_limit = (
        int(configured_hard_limit) if configured_hard_limit is not None else None
    )
    fixed_reserve = int(configured["fixed_reserve"])
    reserved_characters = fixed_reserve + tag_characters
    safe_limit = int(configured["default_safe_limit"])
    if hard_limit is not None:
        safe_limit = min(
            safe_limit,
            hard_limit - reserved_characters,
        )
    if safe_limit < 80:
        raise PublishContentLimitError(
            f"{normalized_platform} 话题占用过多，无法为正文保留安全空间；"
            "请先减少或缩短文章标签"
        )
    return {
        "version": PUBLISH_CONTENT_POLICY_VERSION,
        "field": "content",
        "content_mode": configured["content_mode"],
        "label": configured["label"],
        "hard_limit": hard_limit,
        "safe_limit": safe_limit,
        "fixed_reserve": fixed_reserve,
        "tag_characters": tag_characters,
        "tag_count_used": len(normalized_tags),
        "tag_count_dropped": int(tag_preview["dropped_count"]),
        "tag_count_truncated": int(tag_preview["truncated_count"]),
        "tag_adaptation_audit": tag_preview["audit"],
        "reserved_characters": reserved_characters,
        "tags_share_content_limit": tags_share_content_limit,
        "counting_note": configured["counting_note"],
        "basis": configured.get("basis", "existing_repository_policy"),
        "safe_limit_basis": configured.get(
            "safe_limit_basis", "geo_conservative_headroom"
        ),
        "guaranteed_pass": False,
    }


def get_publish_title_policy(platform: str) -> dict[str, Any] | None:
    normalized_platform = _normalize_platform(platform)
    configured = _TITLE_POLICIES.get(normalized_platform)
    if configured is None:
        return None
    platform_title_policy = get_platform_publish_policy(normalized_platform)["title"]
    configured_hard_limit = configured.get("hard_limit")
    hard_limit = (
        int(configured_hard_limit) if configured_hard_limit is not None else None
    )
    return {
        "version": PUBLISH_CONTENT_POLICY_VERSION,
        "field": "title",
        "label": "发布标题",
        "hard_limit": hard_limit,
        "safe_limit": int(configured["safe_limit"]),
        "min_length": int(configured["min_length"]),
        "counting_note": (
            "按 Unicode 字符计数；超限时在创建任务前生成并展示短标题；"
            + (
                "该安全线是产品侧保守值，不是平台官方上限"
                if hard_limit is None
                else "限制来自仓库内发布器校验"
            )
        ),
        "basis": platform_title_policy.get("basis", "not_verified"),
        "safe_limit_basis": platform_title_policy.get(
            "safe_limit_basis",
            platform_title_policy.get("basis", "not_verified"),
        ),
    }


def get_platform_verification_policy(platform: str) -> dict[str, Any]:
    normalized_platform = _normalize_platform(platform)
    configured = _VERIFICATION_POLICIES.get(normalized_platform)
    if configured is None:
        return {
            "likely": True,
            "methods": ["账号安全验证"],
            "notice": "平台可能要求账号安全验证，请保持绑定设备可用。",
            "action": "软件会停止自动操作并将任务标记为待人工确认，不会绕过或自动重试验证。",
        }
    return {
        "likely": True,
        "methods": list(configured["methods"]),
        "notice": configured["notice"],
        "action": "出现验证时请在已打开的平台窗口内本人完成；软件会保留任务并转为待人工确认，不会绕过或自动重试。",
    }


def get_platform_frequency_guidance(platform: str) -> dict[str, Any]:
    """Return product-side conservative defaults, never an official quota."""

    normalized_platform = _normalize_platform(platform)
    configured = PLATFORM_FREQUENCY_GUIDANCE_DEFAULTS.get(
        normalized_platform,
        {"daily_recommended_max": 3, "cooldown_minutes": 120},
    )
    return {
        "daily_recommended_max": configured["daily_recommended_max"],
        "cooldown_minutes": configured["cooldown_minutes"],
        "enforcement": "warning",
        "source": "geo_conservative_default",
        "disclaimer": (
            "这是 GEO 软件的保守操作建议，不是平台官方配额或通过保证；"
            "平台会按账号状态、内容和行为动态风控，账号运营方需自行承担"
            "限流、处罚或封禁风险。"
        ),
        "risk_notice": (
            "新账号、短时间批量发送、重复内容或平台要求验证时，应降低频率；"
            "可能出现验证码、限流、审核延迟或账号处罚。"
        ),
    }


def build_platform_publish_variant(
    *,
    platform: str,
    title: str,
    content: str,
    tags: Iterable[Any] | None = None,
    publish_title: str | None = None,
    publish_content: str | None = None,
    required_phrases: Iterable[Any] | None = None,
) -> dict[str, Any]:
    """Build a visible, immutable platform copy without changing the article."""

    normalized_platform = _normalize_platform(platform)
    original_content = _normalize_line_endings(content).strip()
    if not original_content:
        raise PublishContentLimitError("原稿正文不能为空")
    original_title = str(title or "").strip()
    if not original_title:
        raise PublishContentLimitError("原稿标题不能为空")

    policy = get_publish_content_policy(normalized_platform, tags=tags)
    title_policy = get_publish_title_policy(normalized_platform)
    if policy is None or title_policy is None:  # pragma: no cover - matrix invariant
        raise PublishContentLimitError(
            f"{normalized_platform} 缺少发布标题或正文安全策略"
        )
    title_manually_edited = publish_title is not None
    if title_manually_edited:
        actual_title = str(publish_title or "").strip()
    elif title_policy is not None:
        actual_title = _shorten_title_to_limit(
            original_title,
            title_policy["safe_limit"],
        )
    else:
        actual_title = original_title
    if not actual_title:
        raise PublishContentLimitError("平台发布标题不能为空")

    manually_edited = publish_content is not None
    hygiene = sanitize_generated_content(original_content)
    automatic_source_content = hygiene.content
    platform_cleanup = {
        "applied": False,
        "changed": False,
        "removed_redirect_lines": 0,
        "removed_contact_clauses": 0,
        "removed_cta_clauses": 0,
        "removed_hashtag_tails": 0,
        "removed_duplicate_sentences": 0,
    }
    if manually_edited:
        actual_content = _normalize_line_endings(publish_content).strip()
        if not actual_content:
            raise PublishContentLimitError("平台发布副本不能为空")
    elif policy is not None or normalized_platform in _PLAIN_TEXT_CONTENT_PLATFORMS:
        compact_content = _format_platform_text(
            automatic_source_content,
            platform=normalized_platform,
        )
        if normalized_platform == "sohu":
            sanitized = sanitize_sohu_publish_copy(compact_content)
            compact_content = sanitized["content"]
            platform_cleanup = sanitized["audit"]
        actual_content = (
            _shorten_to_limit(
                compact_content,
                policy["safe_limit"],
                required_phrases=required_phrases,
            )
            if policy is not None
            else compact_content
        )
    else:
        actual_content = automatic_source_content
    if not actual_content.strip():
        raise PublishContentLimitError(
            "平台发布副本清理后为空；请在原稿中补充中性事实、机制或适用边界"
        )

    actual_length = character_count(actual_content)
    original_length = character_count(original_content)
    title_length = character_count(actual_title)
    original_title_length = character_count(original_title)
    if title_policy is not None:
        if title_length < title_policy["min_length"]:
            raise PublishContentLimitError(
                f"{normalized_platform} 发布标题至少需要 "
                f"{title_policy['min_length']} 字"
            )
        if title_length > title_policy["safe_limit"]:
            raise PublishContentLimitError(
                f"{normalized_platform} 发布标题当前 {title_length} 字，"
                f"请缩短至 {title_policy['safe_limit']} 字以内"
            )
    if actual_length > policy["safe_limit"]:
        if policy["hard_limit"] is None:
            limit_context = (
                f"超过产品侧 {policy['safe_limit']} 字保守安全线"
                "（不是平台官方上限）"
            )
        else:
            limit_context = (
                f"为避免超过平台 {policy['hard_limit']} 字上限"
                "（含话题和计数余量）"
            )
        raise PublishContentLimitError(
            f"{normalized_platform} 平台副本当前 {actual_length} 字，"
            f"{limit_context}，请缩短至 {policy['safe_limit']} 字以内"
        )

    adapted = actual_content != original_content
    if manually_edited:
        adaptation_reason = "用户编辑的平台副本"
    elif platform_cleanup["changed"]:
        adaptation_reason = (
            "搜狐自动平台副本已移除纯导流 URL、联系方式、强促销引导、"
            "尾部标签或完全重复句；原稿保持不变"
        )
    elif hygiene.changed and adapted:
        adaptation_reason = (
            "已移除未完成的作者、日期或证据占位标记；"
            "平台账号和文章记录仍保留正式作者与时间信息"
        )
    elif adapted:
        adaptation_reason = (
            f"已移除平台编辑器不需要的 Markdown 标记，并自动缩至 "
            f"{policy['safe_limit']} 字安全线内"
        )
    elif adapted and normalized_platform in _PLAIN_TEXT_CONTENT_PLATFORMS:
        adaptation_reason = "已在创建任务前移除平台编辑器不需要的 Markdown 标记"
    else:
        adaptation_reason = "原稿已符合平台安全限制，无需缩减"

    platform_policy = get_platform_publish_policy(normalized_platform)
    offline_preflight = evaluate_platform_offline_preflight(
        platform=normalized_platform,
        title=actual_title,
        content=actual_content,
        tags=tags,
    )
    submission_preview = None
    if normalized_platform == "weibo":
        from services.weibo_official_connector import (
            WEIBO_PRODUCT_TEXT_SAFE_LIMIT,
            build_weibo_status_text,
        )

        selected_tags = offline_preflight["tag_preview"]["selected"]
        submission_text = build_weibo_status_text(
            actual_title,
            actual_content,
            selected_tags,
        )
        submission_length = character_count(submission_text)
        if submission_length > WEIBO_PRODUCT_TEXT_SAFE_LIMIT:
            raise PublishContentLimitError(
                f"微博标题、正文与话题合计 {submission_length} 字，超过产品侧 "
                f"{WEIBO_PRODUCT_TEXT_SAFE_LIMIT} 字保守总预算；该预算不是微博官方配额"
            )
        submission_preview = {
            "text_length": submission_length,
            "safe_limit": WEIBO_PRODUCT_TEXT_SAFE_LIMIT,
            "basis": "product_conservative_guardrail",
        }

    return {
        "platform": normalized_platform,
        "policy_version": PUBLISH_CONTENT_POLICY_VERSION,
        "title": actual_title,
        "original_title": original_title,
        "title_length": title_length,
        "original_title_length": original_title_length,
        "title_adapted": actual_title != original_title,
        "title_manually_edited": title_manually_edited,
        "title_policy": title_policy,
        "original_content": original_content,
        "publish_content": actual_content,
        "original_length": original_length,
        "publish_length": actual_length,
        "adapted": adapted,
        "manually_edited": manually_edited,
        "adaptation_reason": adaptation_reason,
        "placeholder_cleanup": {
            "applied": bool(hygiene.changed and not manually_edited),
            "removed_lines": hygiene.removed_lines if not manually_edited else 0,
            "replaced_markers": hygiene.replaced_markers if not manually_edited else 0,
        },
        "platform_cleanup": platform_cleanup,
        "content_policy": policy,
        "platform_policy": platform_policy,
        "offline_preflight": offline_preflight,
        "verification": get_platform_verification_policy(normalized_platform),
        "submission_preview": submission_preview,
    }


def validate_platform_publish_variant(
    *,
    platform: str,
    title: str,
    content: str,
    tags: Iterable[Any] | None = None,
) -> dict[str, Any]:
    """Final execution gate for an already-created platform copy."""

    result = build_platform_publish_variant(
        platform=platform,
        title=title,
        content=content,
        tags=tags,
        publish_title=title,
        publish_content=content,
    )
    assert_platform_publish_variant_ready(result)
    return result


def assert_platform_publish_variant_ready(variant: dict[str, Any]) -> None:
    """Reject only deterministic blockers; warnings never predict moderation."""

    preflight = variant.get("offline_preflight")
    if not isinstance(preflight, dict) or preflight.get("passed") is True:
        return
    issues = preflight.get("issues")
    blocking = [
        item
        for item in issues or ()
        if isinstance(item, dict) and item.get("severity") == "blocking"
    ]
    if not blocking:
        return
    summary = "；".join(str(item.get("message") or "").strip() for item in blocking[:3])
    raise PublishContentLimitError(f"平台离线预检未通过：{summary}")


def evaluate_publish_citeability(
    *,
    title: str,
    content: str,
    brand: str,
    product: str = "",
    website: str = "",
    keywords: Iterable[Any] | None = None,
    target_length: int | None = None,
    sources: Iterable[Any] | None = None,
) -> dict[str, Any]:
    """Score only fields that the current publisher actually sends.

    Article summaries are intentionally absent because they are not part of the
    outbound title/body contract. Immutable generation-receipt sources may
    validate a risky claim only while that source's canonical public URL is
    still visible in the exact outbound body. A platform-shortened copy that
    drops the URL therefore cannot inherit the source claim.
    """

    # Imported lazily because the article compiler reuses this module's pure
    # platform-limit policies while constructing generation instructions.
    from services.geo_article_compiler_service import score_ai_citeability

    outbound_content = str(content or "").strip()
    outbound_visible_length = len(re.sub(r"\s+", "", outbound_content))
    outbound_target_length = None
    if target_length is not None:
        if isinstance(target_length, bool) or not isinstance(target_length, int):
            raise ValueError("target_length 必须是整数")
        outbound_target_length = max(200, min(3000, outbound_visible_length))
    matched_sources, source_filter = filter_sources_for_outbound_copy(
        sources,
        outbound_content,
    )
    result = score_ai_citeability(
        title=str(title or "").strip(),
        summary="",
        content=outbound_content,
        brand=str(brand or "").strip(),
        product=str(product or "").strip(),
        website=str(website or "").strip(),
        keywords=_normalize_tags(keywords),
        sources=matched_sources,
        target_length=outbound_target_length,
    )
    if (
        outbound_target_length is not None
        and result["length_profile"].get("source") == "explicit"
    ):
        result["length_profile"]["source"] = "outbound_copy"
    result["outbound_source_filter"] = source_filter
    return result


def filter_sources_for_outbound_copy(
    sources: Iterable[Any] | None,
    outbound_content: str,
) -> tuple[list[Any], dict[str, Any]]:
    """Keep structured claims only for URLs still visible in the sent body.

    Publication and diagnostics share this pure local filter so the same exact
    copy cannot receive contradictory evidence decisions.  It does not fetch
    a URL or infer that a public-looking URL is truthful.
    """

    source_items = (
        list(sources)
        if sources is not None and not isinstance(sources, (str, bytes))
        else []
    )
    outbound_urls: set[str] = set()
    for match in _OUTBOUND_PUBLIC_URL_PATTERN.finditer(str(outbound_content or "")):
        raw_url = match.group(0).rstrip(".,;:!?，。；：！？'\"")
        try:
            outbound_urls.add(normalize_citation_url(raw_url)["canonical_url"])
        except (VisibilityAnalysisError, KeyError, TypeError, ValueError):
            continue

    matched: list[Any] = []
    for item in source_items:
        if isinstance(item, dict):
            raw_url = item.get("url") or item.get("canonical_url")
        else:
            raw_url = item
        if not isinstance(raw_url, str) or not raw_url.strip():
            continue
        try:
            canonical_url = normalize_citation_url(raw_url)["canonical_url"]
        except (VisibilityAnalysisError, KeyError, TypeError, ValueError):
            continue
        if canonical_url in outbound_urls:
            matched.append(item)

    return matched, {
        "provided_count": len(source_items),
        "matched_count": len(matched),
        "visible_url_count": len(outbound_urls),
        "policy": "structured_claim_requires_url_in_exact_outbound_copy",
    }


def character_count(value: Any) -> int:
    return len(str(value or ""))


def _normalize_platform(platform: str) -> str:
    normalized = normalize_platform_key(platform)
    if not normalized:
        raise PublishContentLimitError("暂不支持该发布平台")
    return normalized


def _normalize_tags(tags: Iterable[Any] | None) -> list[str]:
    if tags is None or isinstance(tags, (str, bytes)):
        return []
    result: list[str] = []
    for item in tags:
        normalized = str(item or "").strip().lstrip("#").strip()
        if normalized:
            result.append(normalized)
    return result


def _normalize_line_endings(value: Any) -> str:
    return str(value or "").replace("\r\n", "\n").replace("\r", "\n")


def _markdown_to_note_text(value: str) -> str:
    text = _normalize_line_endings(value)
    text = re.sub(r"!\[([^\]]*)\]\([^\)]*\)", r"\1", text)
    text = re.sub(r"^\s*```[^\n]*\n?", "", text, flags=re.MULTILINE)
    text = re.sub(
        r"^\s{0,3}(?:[-*_]\s*){3,}$",
        "",
        text,
        flags=re.MULTILINE,
    )
    text = re.sub(r"^\s{0,3}#{1,6}\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*>\s?", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*[-*+]\s+", "• ", text, flags=re.MULTILINE)
    # A public evidence link is part of the outbound GEO signal. Plain-text
    # editors do not need Markdown syntax, but dropping the URL would remove
    # the visible evidence that readers and models can verify.
    def public_link(match: re.Match[str]) -> str:
        label = match.group(1).strip()
        url = match.group(2).strip()
        # Models commonly emit [https://host](https://host). Repeating the
        # same URL with a full-width colon creates one malformed apparent URL
        # for the citeability scanner and for downstream plain-text editors.
        if label.rstrip("/").casefold() == url.rstrip("/").casefold():
            return url
        return f"{label}：{url}"

    text = re.sub(
        r"\[([^\]]+)\]\((https?://[^\s\)]+)\)",
        public_link,
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", text)
    text = re.sub(r"\*\*([^*\n]+)\*\*", r"\1", text)
    text = re.sub(r"__([^_\n]+)__", r"\1", text)
    text = re.sub(r"~~([^~\n]+)~~", r"\1", text)
    text = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"\1", text)
    text = text.replace("`", "")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _format_platform_text(value: str, *, platform: str) -> str:
    text = _markdown_to_note_text(value)
    if platform != "sohu":
        return text

    # Sohu receives an intentionally conservative enterprise-education copy:
    # ordinary paragraphs, no decorative divider residue, and no fake footer.
    # Semantic claims are not rewritten here; the explainable risk checks below
    # flag anything that needs a human decision instead of inventing facts.
    text = re.sub(r"^\s*[—–_=]{3,}\s*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _shorten_to_limit(
    value: str,
    limit: int,
    *,
    required_phrases: Iterable[Any] | None = None,
) -> str:
    if character_count(value) <= limit:
        return value
    if limit < 2:
        raise PublishContentLimitError("平台安全字数不足，无法生成发布副本")

    pieces = [
        piece
        for raw_piece in re.split(r"(?<=[。！？!?；;])|\n+", value)
        if (piece := raw_piece.strip())
    ]
    preserved_indexes = [
        index
        for index, piece in enumerate(pieces)
        if _GEO_PRESERVATION_PATTERN.search(piece)
    ]
    phrase_items = (
        [required_phrases]
        if isinstance(required_phrases, (str, bytes))
        else list(required_phrases or ())
    )
    normalized_required_phrases = [
        phrase
        for item in phrase_items
        if (phrase := str(item or "").strip())
    ]

    # A real publish gate evaluates only the exact outbound title/body.  The
    # generic GEO-tail reservation below used to spend its whole budget on
    # limitation/source sentences and could silently drop the one middle
    # sentence containing the canonical brand.  Preserve a source sentence
    # carrying each caller-supplied invariant before filling the optional GEO
    # budget.  We never inject a phrase absent from the source, and manual
    # platform copies bypass this shortening path entirely.
    required_indexes: list[int] = []
    for phrase in normalized_required_phrases:
        folded_phrase = phrase.casefold()
        matches = [
            index
            for index, piece in enumerate(pieces)
            if folded_phrase in piece.casefold()
        ]
        if not matches:
            continue
        selected_index = min(
            matches,
            key=lambda index: (character_count(pieces[index]), index),
        )
        if selected_index not in required_indexes:
            required_indexes.append(selected_index)

    preserved_by_index: dict[int, str] = {}
    preserve_budget = max(80, min(220, limit // 3))
    preserved_used = 0
    maximum_preserve_budget = max(0, limit - character_count("\n…\n") - 1)
    for index in sorted(required_indexes):
        piece = pieces[index]
        separator_cost = 1 if preserved_by_index else 0
        piece_length = character_count(piece)
        if piece_length + separator_cost > maximum_preserve_budget - preserved_used:
            continue
        preserved_by_index[index] = piece
        preserved_used += piece_length + separator_cost

    for index in reversed(preserved_indexes):
        if index in preserved_by_index:
            continue
        piece = pieces[index]
        separator_cost = 1 if preserved_by_index else 0
        piece_length = character_count(piece)
        if piece_length + separator_cost > preserve_budget - preserved_used:
            continue
        preserved_by_index[index] = piece
        preserved_used += piece_length + separator_cost
        if len(preserved_by_index) >= 3:
            break

    preserved = [preserved_by_index[index] for index in sorted(preserved_by_index)]

    bridge = "\n…\n" if preserved else ""
    tail = "\n".join(preserved)
    head_limit = limit - character_count(bridge) - character_count(tail)
    if not preserved:
        head_limit = limit - 1
    selected_by_index: dict[int, str] = {}
    used = 0
    for index, piece in enumerate(pieces):
        if index in preserved_by_index:
            continue
        separator = "\n" if selected_by_index else ""
        addition = f"{separator}{piece}"
        if used + character_count(addition) <= head_limit:
            selected_by_index[index] = piece
            used += character_count(addition)
            continue
        remaining = head_limit - used - character_count(separator)
        if remaining > 0 and not selected_by_index:
            selected_by_index[index] = piece[:remaining].rstrip()
        break

    shortened = "\n".join(selected_by_index.values()).rstrip(" ，,。；;：:")
    if not shortened:
        shortened = value[: max(1, head_limit)].rstrip()
    if preserved:
        # Keep retained source fragments in their original reading order.  In
        # particular, a canonical brand already present in the opening answer
        # must not be moved to a synthetic footer merely because it is also a
        # required phrase.
        retained_by_index = {**selected_by_index, **preserved_by_index}
        retained_indexes = sorted(retained_by_index)
        ordered_parts: list[str] = []
        omission_marked = False
        previous_index: int | None = None
        for index in retained_indexes:
            if previous_index is not None:
                if index > previous_index + 1 and not omission_marked:
                    ordered_parts.append("\n…\n")
                    omission_marked = True
                else:
                    ordered_parts.append("\n")
            ordered_parts.append(retained_by_index[index])
            previous_index = index
        if (
            retained_indexes
            and retained_indexes[-1] < len(pieces) - 1
            and not omission_marked
        ):
            ordered_parts.append("…")
        return "".join(ordered_parts)[:limit].rstrip()
    return f"{shortened}…"


def _shorten_title_to_limit(value: str, limit: int) -> str:
    normalized = " ".join(str(value or "").split())
    if character_count(normalized) <= limit:
        return normalized
    return normalized[:limit].rstrip()
