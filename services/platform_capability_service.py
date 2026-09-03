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
SUPPORTED_PUBLISH_PLATFORMS = frozenset(PLATFORM_BY_KEY)


def list_platform_capabilities() -> list[dict[str, Any]]:
    return [dict(item) for item in PLATFORM_CAPABILITIES]


def get_platform_capability(platform: str) -> dict[str, Any] | None:
    normalized = str(platform or "").strip().lower()
    capability = PLATFORM_BY_KEY.get(normalized)
    return dict(capability) if capability is not None else None
