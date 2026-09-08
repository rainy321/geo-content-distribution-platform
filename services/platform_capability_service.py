from __future__ import annotations

from typing import Any


PLATFORM_CAPABILITIES = (
    {
        "key": "zhihu",
        "name": "知乎",
        "account_type": 9,
        "priority": "P0",
        "content_mode": "article",
        "requires_images": False,
        "requires_video": False,
        "supports_schedule": True,
    },
    {
        "key": "toutiao",
        "name": "今日头条",
        "account_type": 7,
        "priority": "P0",
        "content_mode": "article",
        "requires_images": False,
        "requires_video": False,
        "supports_schedule": True,
    },
    {
        "key": "baijiahao",
        "name": "百家号",
        "account_type": 5,
        "priority": "P0",
        "content_mode": "image_article",
        "requires_images": True,
        "requires_video": False,
        "supports_schedule": True,
    },
    {
        "key": "sohu",
        "name": "搜狐号",
        "account_type": 8,
        "priority": "P0",
        "content_mode": "article",
        "requires_images": False,
        "requires_video": False,
        "supports_schedule": True,
    },
    {
        "key": "xiaohongshu",
        "name": "小红书",
        "account_type": 1,
        "priority": "P1",
        "content_mode": "image_note",
        "requires_images": True,
        "requires_video": False,
        "supports_schedule": True,
    },
    {
        "key": "douyin",
        "name": "抖音",
        "account_type": 3,
        "priority": "P2",
        "content_mode": "image_note",
        "requires_images": True,
        "requires_video": False,
        "supports_schedule": True,
    },
    {
        "key": "kuaishou",
        "name": "快手",
        "account_type": 4,
        "priority": "P2",
        "content_mode": "image_note",
        "requires_images": True,
        "requires_video": False,
        "supports_schedule": True,
    },
    {
        "key": "bilibili",
        "name": "Bilibili",
        "account_type": 6,
        "priority": "P2",
        "content_mode": "video",
        "requires_images": False,
        "requires_video": True,
        "supports_schedule": True,
    },
    {
        "key": "channels",
        "name": "视频号",
        "account_type": 2,
        "priority": "P2",
        "content_mode": "video",
        "requires_images": False,
        "requires_video": True,
        "supports_schedule": True,
    },
    {
        "key": "tiktok",
        "name": "TikTok",
        "account_type": 10,
        "priority": "P2",
        "content_mode": "video",
        "requires_images": False,
        "requires_video": True,
        "supports_schedule": True,
    },
)

PLATFORM_BY_KEY = {item["key"]: item for item in PLATFORM_CAPABILITIES}
PLATFORM_BY_ACCOUNT_TYPE = {
    item["account_type"]: item for item in PLATFORM_CAPABILITIES
}
BILIBILI_PLATFORM_KEY = "bilibili"
BILIBILI_ACCOUNT_TYPE = PLATFORM_BY_KEY[BILIBILI_PLATFORM_KEY]["account_type"]
BILIBILI_RUNTIME_DISABLED_MESSAGE = (
    "Bilibili 运行时默认关闭；完成固定版本、SHA-256 校验、安全解包和商业授权后，"
    "才可设置 ENABLE_BILIBILI_RUNTIME=true"
)
SUPPORTED_PUBLISH_PLATFORMS = frozenset(PLATFORM_BY_KEY)
PLATFORM_KEY_ALIASES = {
    **{item["key"].casefold(): item["key"] for item in PLATFORM_CAPABILITIES},
    **{item["name"].casefold(): item["key"] for item in PLATFORM_CAPABILITIES},
    "b站": "bilibili",
    "哔哩哔哩": "bilibili",
    "头条": "toutiao",
    "搜狐": "sohu",
    "微信视频号": "channels",
}


def list_platform_capabilities() -> list[dict[str, Any]]:
    return [dict(item) for item in PLATFORM_CAPABILITIES]


def get_platform_capability(platform: str) -> dict[str, Any] | None:
    normalized = normalize_platform_key(platform)
    capability = PLATFORM_BY_KEY.get(normalized)
    return dict(capability) if capability is not None else None


def normalize_platform_key(platform: str) -> str:
    """Return the stable internal key for a key or user-facing platform name."""

    normalized = str(platform or "").strip().casefold()
    return PLATFORM_KEY_ALIASES.get(normalized, "")
