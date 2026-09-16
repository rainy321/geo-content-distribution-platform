from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from services.publish_preflight_service import validate_platform_publish_variant
from services.publisher_adapter import PublishContent, PublisherAdapter, PublishResult
from services.weibo_official_connector import (
    WEIBO_OFFICIAL_CONNECTOR_REQUIRED_MESSAGE,
    WEIBO_PRODUCT_TEXT_SAFE_LIMIT,
    WeiboAuthorizationError,
    WeiboCatalogError,
    WeiboConnectorError,
    WeiboConnectorUnavailableError,
    WeiboOfficialConnector,
    WeiboPublishUncertainError,
    build_weibo_status_text,
    is_weibo_public_status_url,
)


class WeiboPublisherAdapter(PublisherAdapter):
    """Publish through the official CLI; browser automation is not available."""

    platform = "weibo"

    def __init__(
        self,
        account_file: str | Path,
        *,
        connector: WeiboOfficialConnector | None = None,
    ):
        self.account_file = Path(account_file).expanduser().resolve()
        self.connector = connector or WeiboOfficialConnector()

    def login(self) -> bool:
        return self.check_login()

    def check_login(self) -> bool:
        try:
            self.connector.verify_marker(self.account_file)
        except WeiboConnectorError:
            return False
        return True

    def publish(self, content: PublishContent) -> PublishResult:
        variant = self._validate(content)
        try:
            self.connector.verify_marker(self.account_file)
        except (WeiboConnectorUnavailableError, WeiboAuthorizationError) as exc:
            message = str(exc).strip() or WEIBO_OFFICIAL_CONNECTOR_REQUIRED_MESSAGE
            return PublishResult(False, self.platform, "need_action", message=message)

        if len(content.images) > 1:
            return PublishResult(
                False,
                self.platform,
                "need_action",
                message="微博官方连接器首版仅允许一张图片，请减少图片或人工发布",
            )
        selected_tags = variant["offline_preflight"]["tag_preview"]["selected"]
        post_text = build_weibo_status_text(
            variant["title"],
            variant["publish_content"],
            selected_tags,
        )
        if len(post_text) > WEIBO_PRODUCT_TEXT_SAFE_LIMIT:
            return PublishResult(
                False,
                self.platform,
                "failed",
                message=(
                    f"微博标题、正文与话题合计超过产品侧 "
                    f"{WEIBO_PRODUCT_TEXT_SAFE_LIMIT} 字保守安全线；"
                    "该安全线不是微博官方配额"
                ),
            )
        try:
            outcome = self.connector.publish_status(
                post_text,
                image_path=(content.images[0] if content.images else ""),
            )
        except WeiboCatalogError as exc:
            return PublishResult(False, self.platform, "need_action", message=str(exc))
        except WeiboPublishUncertainError as exc:
            return PublishResult(False, self.platform, "processing", message=str(exc))
        except WeiboConnectorUnavailableError:
            return PublishResult(
                False,
                self.platform,
                "need_action",
                message=WEIBO_OFFICIAL_CONNECTOR_REQUIRED_MESSAGE,
            )
        except WeiboConnectorError as exc:
            return PublishResult(False, self.platform, "need_action", message=str(exc))
        except Exception as exc:
            return PublishResult(
                False,
                self.platform,
                "processing",
                message=(
                    f"微博官方发布链路异常：{exc.__class__.__name__}；"
                    "平台最终状态未知，请先人工核验，系统不会自动重试"
                ),
            )

        url = str(outcome.get("url") or "").strip()
        if outcome.get("status") == "success" and is_weibo_public_status_url(url):
            return PublishResult(
                True,
                self.platform,
                "success",
                url=url,
                message="微博官方连接器已返回可核验公开作品链接",
                published_at=datetime.now(timezone.utc).isoformat(),
            )
        return PublishResult(
            False,
            self.platform,
            "processing",
            message=str(outcome.get("message") or "").strip()
            or "微博已提交但没有可核验公开链接，请人工核验；系统不会自动重试",
        )

    def schedule(
        self,
        content: PublishContent,
        publish_at: datetime | None = None,
    ) -> PublishResult:
        self._validate(content)
        scheduled_for = publish_at or content.publish_at
        if scheduled_for is None:
            raise ValueError("定时发布必须提供 publish_at")
        return PublishResult(
            True,
            self.platform,
            "scheduled",
            message=f"任务已排期至 {scheduled_for.isoformat()}，等待系统调度",
        )

    def _validate(self, content: PublishContent) -> dict[str, Any]:
        if not isinstance(content, PublishContent):
            raise TypeError("content 必须是 PublishContent")
        if content.platform != self.platform:
            raise ValueError("内容平台与微博适配器不一致")
        if content.video:
            raise ValueError("微博官方连接器首版不支持视频发布")
        return validate_platform_publish_variant(
            platform=self.platform,
            title=content.title,
            content=content.content,
            tags=content.tags,
        )

__all__ = ["WeiboPublisherAdapter"]
