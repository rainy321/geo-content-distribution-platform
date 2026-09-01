from __future__ import annotations

import asyncio
import inspect
import re
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PUBLISH_RESULT_STATUSES = frozenset(
    {"queued", "processing", "success", "failed", "need_action", "scheduled"}
)
_NEED_ACTION_MARKERS = (
    "cookie",
    "login",
    "登录",
    "登陆",
    "扫码",
    "二维码",
    "验证码",
    "滑块",
    "人工",
    "风控",
    "安全验证",
    "确认身份",
    "页面结构",
)


@dataclass(frozen=True)
class PublishContent:
    """Platform-neutral article input passed to a publisher adapter."""

    platform: str
    title: str
    content: str
    images: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    publish_at: datetime | None = None

    def __post_init__(self) -> None:
        platform = str(self.platform or "").strip().lower()
        title = str(self.title or "").strip()
        content = str(self.content or "").strip()
        if not platform:
            raise ValueError("发布平台不能为空")
        if not title:
            raise ValueError("文章标题不能为空")
        if not content:
            raise ValueError("文章正文不能为空")
        if self.publish_at is not None and not isinstance(self.publish_at, datetime):
            raise ValueError("publish_at 必须是 datetime")

        object.__setattr__(self, "platform", platform)
        object.__setattr__(self, "title", title)
        object.__setattr__(self, "content", content)
        object.__setattr__(self, "images", _normalize_string_tuple(self.images))
        object.__setattr__(self, "tags", _normalize_string_tuple(self.tags))


@dataclass(frozen=True)
class PublishResult:
    """A truthful, serializable result returned by every publisher adapter."""

    success: bool
    platform: str
    status: str
    url: str = ""
    message: str = ""
    published_at: str = ""
    demo: bool = False

    def __post_init__(self) -> None:
        status = str(self.status or "").strip().lower()
        if status not in PUBLISH_RESULT_STATUSES:
            raise ValueError("不支持的发布结果状态")
        object.__setattr__(self, "platform", str(self.platform or "").strip().lower())
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "url", str(self.url or "").strip())
        object.__setattr__(self, "message", str(self.message or "").strip())
        object.__setattr__(self, "published_at", str(self.published_at or "").strip())
        object.__setattr__(self, "success", bool(self.success))
        object.__setattr__(self, "demo", bool(self.demo))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class PublisherAdapter(ABC):
    """Synchronous boundary around a platform's possibly asynchronous uploader."""

    platform: str

    @abstractmethod
    def login(self) -> bool:
        """Open or execute the platform-specific login flow."""

    @abstractmethod
    def check_login(self) -> bool:
        """Return whether the stored platform session is currently usable."""

    @abstractmethod
    def publish(self, content: PublishContent) -> PublishResult:
        """Attempt immediate publication without leaking platform exceptions."""

    @abstractmethod
    def schedule(
        self,
        content: PublishContent,
        publish_at: datetime | None = None,
    ) -> PublishResult:
        """Accept content for later execution by the application's scheduler."""


class DemoPublisher(PublisherAdapter):
    """A deterministic publisher that never opens or contacts a real platform."""

    def __init__(self, platform: str):
        normalized = str(platform or "").strip().lower()
        if not normalized:
            raise ValueError("发布平台不能为空")
        self.platform = normalized

    def login(self) -> bool:
        return True

    def check_login(self) -> bool:
        return True

    def publish(self, content: PublishContent) -> PublishResult:
        self._validate_content(content)
        return PublishResult(
            success=True,
            platform=self.platform,
            status="success",
            message="演示发布已完成；未访问真实平台，也未产生真实内容链接",
            published_at=_utc_now_iso(),
            demo=True,
        )

    def schedule(
        self,
        content: PublishContent,
        publish_at: datetime | None = None,
    ) -> PublishResult:
        self._validate_content(content)
        scheduled_for = publish_at or content.publish_at
        if scheduled_for is None:
            raise ValueError("定时发布必须提供 publish_at")
        return PublishResult(
            success=True,
            platform=self.platform,
            status="scheduled",
            message=(
                f"演示任务已排期至 {scheduled_for.isoformat()}；"
                "未访问真实平台"
            ),
            demo=True,
        )

    def _validate_content(self, content: PublishContent) -> None:
        _validate_content_platform(content, self.platform)


class ZhihuPublisherAdapter(PublisherAdapter):
    """Timeout-protected adapter for the existing Zhihu Playwright uploader."""

    platform = "zhihu"

    def __init__(
        self,
        account_file: str | Path,
        *,
        timeout_seconds: float = 120,
        login_timeout_seconds: float = 240,
        login_checker: Callable[..., Any] | None = None,
        login_runner: Callable[..., Any] | None = None,
        publish_runner: Callable[..., Any] | None = None,
        publication_checker: Callable[..., Any] | None = None,
    ):
        if timeout_seconds <= 0 or login_timeout_seconds <= 0:
            raise ValueError("超时时间必须大于 0")
        self.account_file = str(account_file)
        self.timeout_seconds = float(timeout_seconds)
        self.login_timeout_seconds = float(login_timeout_seconds)
        self._login_checker = login_checker or _default_zhihu_login_checker
        self._login_runner = login_runner or _default_zhihu_login_runner
        self._publish_runner = publish_runner or _default_zhihu_publish_runner
        self._publication_checker = (
            publication_checker or _default_zhihu_publication_checker
        )

    def login(self) -> bool:
        return bool(
            _run_with_timeout(
                self._login_runner,
                self.account_file,
                timeout_seconds=self.login_timeout_seconds,
            )
        )

    def check_login(self) -> bool:
        return bool(
            _run_with_timeout(
                self._login_checker,
                self.account_file,
                timeout_seconds=self.timeout_seconds,
            )
        )

    def publish(self, content: PublishContent) -> PublishResult:
        _validate_content_platform(content, self.platform)
        try:
            logged_in = self.check_login()
        except (TimeoutError, asyncio.TimeoutError):
            return self._failed("知乎登录状态检查超时，请稍后重试")
        except Exception as exc:
            return self._exception_result(exc, prefix="知乎登录状态检查失败")

        if not logged_in:
            return PublishResult(
                success=False,
                platform=self.platform,
                status="need_action",
                message="知乎登录状态无效，需要重新登录后重试",
            )

        try:
            existing = self._find_publication(content.title, attempts=1)
        except (TimeoutError, asyncio.TimeoutError):
            return self._failed("知乎公开文章幂等检查超时，已阻止本次发布")
        except Exception as exc:
            return self._exception_result(exc, prefix="知乎公开文章幂等检查失败")
        if existing is not None:
            return self._verified_publication_result(
                existing,
                message="知乎已存在同标题公开文章，已跳过重复发布",
            )

        try:
            raw_result = _run_with_timeout(
                self._publish_runner,
                content,
                self.account_file,
                timeout_seconds=self.timeout_seconds,
            )
        except (TimeoutError, asyncio.TimeoutError):
            result = self._failed(
                "知乎发布操作超时；平台最终状态未知，请先人工核对再重试"
            )
        except Exception as exc:
            result = self._exception_result(exc, prefix="知乎发布失败")
        else:
            result = self._normalize_publish_result(raw_result)

        if result.status == "success" and result.url:
            return result
        try:
            verified = self._find_publication(content.title, attempts=3)
        except Exception as exc:
            detail = str(exc).strip() or exc.__class__.__name__
            return PublishResult(
                success=result.success,
                platform=result.platform,
                status=result.status,
                url=result.url,
                message=(
                    f"{result.message}；发布后公开文章核验失败：{detail}"
                ).strip("；"),
                published_at=result.published_at,
            )
        if verified is not None:
            return self._verified_publication_result(
                verified,
                message="知乎公开文章 API 已核验发布成功",
            )
        return result

    def schedule(
        self,
        content: PublishContent,
        publish_at: datetime | None = None,
    ) -> PublishResult:
        _validate_content_platform(content, self.platform)
        scheduled_for = publish_at or content.publish_at
        if scheduled_for is None:
            raise ValueError("定时发布必须提供 publish_at")
        return PublishResult(
            success=True,
            platform=self.platform,
            status="scheduled",
            message=f"任务已排期至 {scheduled_for.isoformat()}，等待系统调度",
        )

    def _normalize_publish_result(self, raw_result: Any) -> PublishResult:
        if isinstance(raw_result, PublishResult):
            if raw_result.platform != self.platform:
                return self._failed("知乎发布器返回了错误的平台标识")
            return raw_result

        if isinstance(raw_result, Mapping):
            url = str(raw_result.get("url") or raw_result.get("result_url") or "").strip()
            raw_status = str(raw_result.get("status") or "").strip().lower()
            if not raw_status:
                raw_status = "success" if raw_result.get("success") is True or url else "processing"
            if raw_status not in PUBLISH_RESULT_STATUSES:
                return self._failed(f"知乎发布器返回了未知状态：{raw_status}")
            success = raw_status in {"success", "scheduled"}
            published_at = str(raw_result.get("published_at") or "").strip()
            if raw_status == "success" and not published_at:
                published_at = _utc_now_iso()
            return PublishResult(
                success=success,
                platform=self.platform,
                status=raw_status,
                url=url,
                message=str(raw_result.get("message") or "").strip(),
                published_at=published_at,
            )

        if isinstance(raw_result, str) and raw_result.strip():
            return PublishResult(
                success=True,
                platform=self.platform,
                status="success",
                url=raw_result.strip(),
                message="知乎发布成功",
                published_at=_utc_now_iso(),
            )

        return PublishResult(
            success=False,
            platform=self.platform,
            status="processing",
            message="已执行知乎发布点击，但平台未返回最终状态或内容链接",
        )

    def _find_publication(
        self,
        title: str,
        *,
        attempts: int,
    ) -> dict[str, Any] | None:
        raw_result = _run_with_timeout(
            self._publication_checker,
            title,
            self.account_file,
            attempts,
            timeout_seconds=self.timeout_seconds,
        )
        if raw_result is None:
            return None
        if not isinstance(raw_result, Mapping):
            raise TypeError("知乎公开文章核验器必须返回映射或 None")
        url = str(raw_result.get("url") or "").strip()
        if not url.startswith(("https://", "http://")):
            raise ValueError("知乎公开文章核验结果缺少有效链接")
        return dict(raw_result)

    def _verified_publication_result(
        self,
        publication: Mapping[str, Any],
        *,
        message: str,
    ) -> PublishResult:
        url = str(publication.get("url") or "").strip()
        if url.startswith("http://"):
            url = "https://" + url.removeprefix("http://")
        return PublishResult(
            success=True,
            platform=self.platform,
            status="success",
            url=url,
            message=message,
            published_at=_utc_now_iso(),
        )

    def _exception_result(self, exc: Exception, *, prefix: str) -> PublishResult:
        detail = str(exc).strip() or exc.__class__.__name__
        status = classify_publisher_error(detail)
        return PublishResult(
            success=False,
            platform=self.platform,
            status=status,
            message=f"{prefix}：{detail}",
        )

    def _failed(self, message: str) -> PublishResult:
        return PublishResult(
            success=False,
            platform=self.platform,
            status="failed",
            message=message,
        )


class ToutiaoPublisherAdapter(PublisherAdapter):
    """Timeout-protected adapter around OmniPost's Toutiao article uploader."""

    platform = "toutiao"

    def __init__(
        self,
        account_file: str | Path,
        *,
        timeout_seconds: float = 120,
        login_timeout_seconds: float = 240,
        login_checker: Callable[..., Any] | None = None,
        login_runner: Callable[..., Any] | None = None,
        publish_runner: Callable[..., Any] | None = None,
    ):
        if timeout_seconds <= 0 or login_timeout_seconds <= 0:
            raise ValueError("超时时间必须大于 0")
        self.account_file = str(account_file)
        self.timeout_seconds = float(timeout_seconds)
        self.login_timeout_seconds = float(login_timeout_seconds)
        self._login_checker = login_checker or _default_toutiao_login_checker
        self._login_runner = login_runner or _default_toutiao_login_runner
        self._publish_runner = publish_runner or _default_toutiao_publish_runner

    def login(self) -> bool:
        return bool(
            _run_with_timeout(
                self._login_runner,
                self.account_file,
                timeout_seconds=self.login_timeout_seconds,
            )
        )

    def check_login(self) -> bool:
        return bool(
            _run_with_timeout(
                self._login_checker,
                self.account_file,
                timeout_seconds=self.timeout_seconds,
            )
        )

    def publish(self, content: PublishContent) -> PublishResult:
        _validate_content_platform(content, self.platform)
        try:
            logged_in = self.check_login()
        except (TimeoutError, asyncio.TimeoutError):
            return self._failed("今日头条登录状态检查超时，请稍后重试")
        except Exception as exc:
            return self._exception_result(exc, prefix="今日头条登录状态检查失败")

        if not logged_in:
            return PublishResult(
                success=False,
                platform=self.platform,
                status="need_action",
                message="今日头条登录状态无效，需要重新登录后重试",
            )

        try:
            raw_result = _run_with_timeout(
                self._publish_runner,
                content,
                self.account_file,
                timeout_seconds=self.timeout_seconds,
            )
        except (TimeoutError, asyncio.TimeoutError):
            return PublishResult(
                success=False,
                platform=self.platform,
                status="processing",
                message=(
                    "今日头条发布操作超时，平台最终状态未知；"
                    "请先到平台后台核对，系统不会自动重试"
                ),
            )
        except Exception as exc:
            return self._exception_result(exc, prefix="今日头条发布失败")
        return self._normalize_publish_result(raw_result)

    def schedule(
        self,
        content: PublishContent,
        publish_at: datetime | None = None,
    ) -> PublishResult:
        _validate_content_platform(content, self.platform)
        scheduled_for = publish_at or content.publish_at
        if scheduled_for is None:
            raise ValueError("定时发布必须提供 publish_at")
        return PublishResult(
            success=True,
            platform=self.platform,
            status="scheduled",
            message=f"任务已排期至 {scheduled_for.isoformat()}，等待系统调度",
        )

    def _normalize_publish_result(self, raw_result: Any) -> PublishResult:
        if isinstance(raw_result, PublishResult):
            if raw_result.platform != self.platform:
                return self._failed("今日头条发布器返回了错误的平台标识")
            if raw_result.status == "success" and not _is_public_toutiao_url(
                raw_result.url
            ):
                return PublishResult(
                    success=False,
                    platform=self.platform,
                    status="processing",
                    message="今日头条未返回可核验的公开文章链接，请到平台后台确认",
                )
            return raw_result
        if isinstance(raw_result, Mapping):
            raw_status = str(raw_result.get("status") or "processing").strip().lower()
            if raw_status not in PUBLISH_RESULT_STATUSES:
                return self._failed(f"今日头条发布器返回了未知状态：{raw_status}")
            url = str(raw_result.get("url") or raw_result.get("result_url") or "").strip()
            if raw_status == "success" and not _is_public_toutiao_url(url):
                raw_status = "processing"
            success = raw_status == "success"
            return PublishResult(
                success=success,
                platform=self.platform,
                status=raw_status,
                url=url,
                message=str(raw_result.get("message") or "").strip(),
                published_at=_utc_now_iso() if success else "",
            )
        return PublishResult(
            success=False,
            platform=self.platform,
            status="processing",
            message="已执行今日头条发布流程，但未取得可核验的平台结果",
        )

    def _exception_result(self, exc: Exception, *, prefix: str) -> PublishResult:
        detail = str(exc).strip() or exc.__class__.__name__
        return PublishResult(
            success=False,
            platform=self.platform,
            status=classify_publisher_error(detail),
            message=f"{prefix}：{detail}",
        )

    def _failed(self, message: str) -> PublishResult:
        return PublishResult(
            success=False,
            platform=self.platform,
            status="failed",
            message=message,
        )


class SohuPublisherAdapter(PublisherAdapter):
    """Timeout-protected adapter around OmniPost's Sohu article uploader."""

    platform = "sohu"

    def __init__(
        self,
        account_file: str | Path,
        *,
        timeout_seconds: float = 120,
        login_timeout_seconds: float = 240,
        login_checker: Callable[..., Any] | None = None,
        login_runner: Callable[..., Any] | None = None,
        publish_runner: Callable[..., Any] | None = None,
    ):
        if timeout_seconds <= 0 or login_timeout_seconds <= 0:
            raise ValueError("超时时间必须大于 0")
        self.account_file = str(account_file)
        self.timeout_seconds = float(timeout_seconds)
        self.login_timeout_seconds = float(login_timeout_seconds)
        self._login_checker = login_checker or _default_sohu_login_checker
        self._login_runner = login_runner or _default_sohu_login_runner
        self._publish_runner = publish_runner or _default_sohu_publish_runner

    def login(self) -> bool:
        return bool(
            _run_with_timeout(
                self._login_runner,
                self.account_file,
                timeout_seconds=self.login_timeout_seconds,
            )
        )

    def check_login(self) -> bool:
        return bool(
            _run_with_timeout(
                self._login_checker,
                self.account_file,
                timeout_seconds=self.timeout_seconds,
            )
        )

    def publish(self, content: PublishContent) -> PublishResult:
        _validate_content_platform(content, self.platform)
        try:
            logged_in = self.check_login()
        except (TimeoutError, asyncio.TimeoutError):
            return self._failed("搜狐号登录状态检查超时，请稍后重试")
        except Exception as exc:
            return self._exception_result(exc, prefix="搜狐号登录状态检查失败")

        if not logged_in:
            return PublishResult(
                success=False,
                platform=self.platform,
                status="need_action",
                message="搜狐号登录状态无效，需要重新登录后重试",
            )

        try:
            raw_result = _run_with_timeout(
                self._publish_runner,
                content,
                self.account_file,
                timeout_seconds=self.timeout_seconds,
            )
        except (TimeoutError, asyncio.TimeoutError):
            return PublishResult(
                success=False,
                platform=self.platform,
                status="processing",
                message=(
                    "搜狐号发布操作超时，平台最终状态未知；"
                    "请先到平台后台核对，系统不会自动重试"
                ),
            )
        except Exception as exc:
            return self._exception_result(exc, prefix="搜狐号发布失败")
        return self._normalize_publish_result(raw_result)

    def schedule(
        self,
        content: PublishContent,
        publish_at: datetime | None = None,
    ) -> PublishResult:
        _validate_content_platform(content, self.platform)
        scheduled_for = publish_at or content.publish_at
        if scheduled_for is None:
            raise ValueError("定时发布必须提供 publish_at")
        return PublishResult(
            success=True,
            platform=self.platform,
            status="scheduled",
            message=f"任务已排期至 {scheduled_for.isoformat()}，等待系统调度",
        )

    def _normalize_publish_result(self, raw_result: Any) -> PublishResult:
        if isinstance(raw_result, PublishResult):
            if raw_result.platform != self.platform:
                return self._failed("搜狐号发布器返回了错误的平台标识")
            if raw_result.status == "success" and not _is_public_sohu_url(
                raw_result.url
            ):
                return PublishResult(
                    success=False,
                    platform=self.platform,
                    status="processing",
                    message="搜狐号未返回可核验的公开文章链接，请到平台后台确认",
                )
            return raw_result
        if isinstance(raw_result, Mapping):
            raw_status = str(raw_result.get("status") or "processing").strip().lower()
            if raw_status not in PUBLISH_RESULT_STATUSES:
                return self._failed(f"搜狐号发布器返回了未知状态：{raw_status}")
            url = str(raw_result.get("url") or raw_result.get("result_url") or "").strip()
            if raw_status == "success" and not _is_public_sohu_url(url):
                raw_status = "processing"
            success = raw_status == "success"
            return PublishResult(
                success=success,
                platform=self.platform,
                status=raw_status,
                url=url,
                message=str(raw_result.get("message") or "").strip(),
                published_at=_utc_now_iso() if success else "",
            )
        return PublishResult(
            success=False,
            platform=self.platform,
            status="processing",
            message="已执行搜狐号发布流程，但未取得可核验的平台结果",
        )

    def _exception_result(self, exc: Exception, *, prefix: str) -> PublishResult:
        detail = str(exc).strip() or exc.__class__.__name__
        return PublishResult(
            success=False,
            platform=self.platform,
            status=classify_publisher_error(detail),
            message=f"{prefix}：{detail}",
        )

    def _failed(self, message: str) -> PublishResult:
        return PublishResult(
            success=False,
            platform=self.platform,
            status="failed",
            message=message,
        )


class BaijiahaoPublisherAdapter(PublisherAdapter):
    """Guarded adapter around OmniPost's Baijiahao article uploader."""

    platform = "baijiahao"

    def __init__(
        self,
        account_file: str | Path,
        *,
        timeout_seconds: float = 120,
        login_timeout_seconds: float = 240,
        login_checker: Callable[..., Any] | None = None,
        login_runner: Callable[..., Any] | None = None,
        publish_runner: Callable[..., Any] | None = None,
    ):
        if timeout_seconds <= 0 or login_timeout_seconds <= 0:
            raise ValueError("超时时间必须大于 0")
        self.account_file = str(account_file)
        self.timeout_seconds = float(timeout_seconds)
        self.login_timeout_seconds = float(login_timeout_seconds)
        self._login_checker = login_checker or _default_baijiahao_login_checker
        self._login_runner = login_runner or _default_baijiahao_login_runner
        self._publish_runner = publish_runner or _default_baijiahao_publish_runner

    def login(self) -> bool:
        return bool(
            _run_with_timeout(
                self._login_runner,
                self.account_file,
                timeout_seconds=self.login_timeout_seconds,
            )
        )

    def check_login(self) -> bool:
        return bool(
            _run_with_timeout(
                self._login_checker,
                self.account_file,
                timeout_seconds=self.timeout_seconds,
            )
        )

    def publish(self, content: PublishContent) -> PublishResult:
        _validate_content_platform(content, self.platform)
        if not content.images:
            return PublishResult(
                success=False,
                platform=self.platform,
                status="need_action",
                message="百家号图文必须提供一张展示封面，请先人工选择图片",
            )
        try:
            logged_in = self.check_login()
        except (TimeoutError, asyncio.TimeoutError):
            return self._failed("百家号登录状态检查超时，请稍后重试")
        except Exception as exc:
            return self._exception_result(exc, prefix="百家号登录状态检查失败")
        if not logged_in:
            return PublishResult(
                success=False,
                platform=self.platform,
                status="need_action",
                message="百家号登录状态无效，需要重新登录后重试",
            )
        try:
            raw_result = _run_with_timeout(
                self._publish_runner,
                content,
                self.account_file,
                timeout_seconds=self.timeout_seconds,
            )
        except (TimeoutError, asyncio.TimeoutError):
            return PublishResult(
                success=False,
                platform=self.platform,
                status="processing",
                message="百家号发布操作超时，平台最终状态未知；请先人工核对，系统不会自动重试",
            )
        except Exception as exc:
            return self._exception_result(exc, prefix="百家号发布失败")
        return self._normalize_publish_result(raw_result)

    def schedule(
        self,
        content: PublishContent,
        publish_at: datetime | None = None,
    ) -> PublishResult:
        _validate_content_platform(content, self.platform)
        scheduled_for = publish_at or content.publish_at
        if scheduled_for is None:
            raise ValueError("定时发布必须提供 publish_at")
        return PublishResult(
            success=True,
            platform=self.platform,
            status="scheduled",
            message=f"任务已排期至 {scheduled_for.isoformat()}，等待系统调度",
        )

    def _normalize_publish_result(self, raw_result: Any) -> PublishResult:
        return _normalize_guarded_public_result(
            raw_result,
            platform=self.platform,
            platform_name="百家号",
            public_url_checker=_is_public_baijiahao_url,
        )

    def _exception_result(self, exc: Exception, *, prefix: str) -> PublishResult:
        detail = str(exc).strip() or exc.__class__.__name__
        return PublishResult(
            success=False,
            platform=self.platform,
            status=classify_publisher_error(detail),
            message=f"{prefix}：{detail}",
        )

    def _failed(self, message: str) -> PublishResult:
        return PublishResult(False, self.platform, "failed", message=message)


class XiaohongshuPublisherAdapter(PublisherAdapter):
    """Guarded adapter for image-backed Xiaohongshu notes."""

    platform = "xiaohongshu"

    def __init__(
        self,
        account_file: str | Path,
        *,
        timeout_seconds: float = 120,
        login_timeout_seconds: float = 300,
        login_checker: Callable[..., Any] | None = None,
        login_runner: Callable[..., Any] | None = None,
        publish_runner: Callable[..., Any] | None = None,
    ):
        if timeout_seconds <= 0 or login_timeout_seconds <= 0:
            raise ValueError("超时时间必须大于 0")
        self.account_file = str(account_file)
        self.timeout_seconds = float(timeout_seconds)
        self.login_timeout_seconds = float(login_timeout_seconds)
        self._login_checker = login_checker or _default_xiaohongshu_login_checker
        self._login_runner = login_runner or _default_xiaohongshu_login_runner
        self._publish_runner = publish_runner or _default_xiaohongshu_publish_runner

    def login(self) -> bool:
        return bool(
            _run_with_timeout(
                self._login_runner,
                self.account_file,
                timeout_seconds=self.login_timeout_seconds,
            )
        )

    def check_login(self) -> bool:
        return bool(
            _run_with_timeout(
                self._login_checker,
                self.account_file,
                timeout_seconds=self.timeout_seconds,
            )
        )

    def publish(self, content: PublishContent) -> PublishResult:
        _validate_content_platform(content, self.platform)
        if not content.images:
            return PublishResult(
                success=False,
                platform=self.platform,
                status="need_action",
                message="小红书图文笔记至少需要一张图片，请先人工选择图片",
            )
        try:
            logged_in = self.check_login()
        except (TimeoutError, asyncio.TimeoutError):
            return self._failed("小红书登录状态检查超时，请稍后重试")
        except Exception as exc:
            return self._exception_result(exc, prefix="小红书登录状态检查失败")
        if not logged_in:
            return PublishResult(
                success=False,
                platform=self.platform,
                status="need_action",
                message="小红书登录状态无效，需要重新登录后重试",
            )
        try:
            raw_result = _run_with_timeout(
                self._publish_runner,
                content,
                self.account_file,
                timeout_seconds=self.timeout_seconds,
            )
        except (TimeoutError, asyncio.TimeoutError):
            return PublishResult(
                success=False,
                platform=self.platform,
                status="processing",
                message="小红书发布操作超时，平台最终状态未知；请先人工核对，系统不会自动重试",
            )
        except Exception as exc:
            return self._exception_result(exc, prefix="小红书发布失败")
        return self._normalize_publish_result(raw_result)

    def schedule(
        self,
        content: PublishContent,
        publish_at: datetime | None = None,
    ) -> PublishResult:
        _validate_content_platform(content, self.platform)
        scheduled_for = publish_at or content.publish_at
        if scheduled_for is None:
            raise ValueError("定时发布必须提供 publish_at")
        return PublishResult(
            success=True,
            platform=self.platform,
            status="scheduled",
            message=f"任务已排期至 {scheduled_for.isoformat()}，等待系统调度",
        )

    def _normalize_publish_result(self, raw_result: Any) -> PublishResult:
        return _normalize_guarded_public_result(
            raw_result,
            platform=self.platform,
            platform_name="小红书",
            public_url_checker=_is_public_xiaohongshu_url,
        )

    def _exception_result(self, exc: Exception, *, prefix: str) -> PublishResult:
        detail = str(exc).strip() or exc.__class__.__name__
        status = (
            "processing"
            if "最终状态未知" in detail
            else classify_publisher_error(detail)
        )
        return PublishResult(
            success=False,
            platform=self.platform,
            status=status,
            message=f"{prefix}：{detail}",
        )

    def _failed(self, message: str) -> PublishResult:
        return PublishResult(False, self.platform, "failed", message=message)


async def _default_zhihu_login_checker(account_file: str) -> bool:
    from uploader.zhihu_uploader.main import cookie_auth

    return await cookie_auth(account_file)


async def _default_zhihu_login_runner(account_file: str) -> bool:
    from uploader.zhihu_uploader.main import zhihu_setup

    return await zhihu_setup(account_file, handle=True)


async def _default_zhihu_publication_checker(
    title: str,
    account_file: str,
    attempts: int,
) -> dict[str, Any] | None:
    from services.zhihu_publication_verifier import find_public_zhihu_article

    return await find_public_zhihu_article(
        title,
        account_file,
        attempts=attempts,
    )


async def _default_zhihu_publish_runner(
    content: PublishContent,
    account_file: str,
) -> Any:
    from uploader.zhihu_uploader.main import ZhiHuArticle
    from services.zhihu_publish_flow import click_exact_publish_and_observe

    publisher = ZhiHuArticle(
        title=content.title,
        body=content.content,
        tags=list(content.tags),
        publish_date=0,
        account_file=account_file,
        dry_run=False,
        cover_path=content.images[0] if content.images else None,
        creation_statement="包含 AI 辅助创作 作者对内容负责",
    )
    observed_result: dict[str, Any] = {}

    async def click_publish(page: Any) -> None:
        observed_result.update(await click_exact_publish_and_observe(page))

    # Keep the legacy uploader untouched while correcting its fuzzy publish
    # selector at the service-adapter boundary.
    publisher.click_publish = click_publish
    await publisher.main()
    if not observed_result:
        raise RuntimeError("知乎发布流程结束，但未产生可核验的提交结果")
    return observed_result


async def _default_toutiao_login_checker(account_file: str) -> bool:
    from uploader.toutiao_uploader.main import cookie_auth

    return await cookie_auth(account_file)


async def _default_toutiao_login_runner(account_file: str) -> bool:
    from uploader.toutiao_uploader.main import toutiao_setup

    return await toutiao_setup(account_file, handle=True)


async def _default_toutiao_publish_runner(
    content: PublishContent,
    account_file: str,
) -> Any:
    from services.toutiao_publish_flow import click_exact_publish_and_observe
    from uploader.toutiao_uploader.main import TouTiaoArticle

    publisher = TouTiaoArticle(
        title=content.title,
        body=content.content,
        tags=list(content.tags),
        publish_date=0,
        account_file=account_file,
        dry_run=False,
        cover_path=content.images[0] if content.images else None,
        work_statements=["引用AI"],
    )
    observed_result: dict[str, Any] = {}

    async def click_publish(page: Any) -> None:
        observed_result.update(await click_exact_publish_and_observe(page))

    publisher.publish = click_publish
    await publisher.main()
    if not observed_result:
        raise RuntimeError("今日头条发布流程结束，但未产生可核验的提交结果")
    return observed_result


async def _default_sohu_login_checker(account_file: str) -> bool:
    from uploader.sohu_uploader.main import cookie_auth

    return await cookie_auth(account_file)


async def _default_sohu_login_runner(account_file: str) -> bool:
    from uploader.sohu_uploader.main import sohu_setup

    return await sohu_setup(account_file, handle=True)


async def _default_sohu_publish_runner(
    content: PublishContent,
    account_file: str,
) -> Any:
    from services.sohu_publish_flow import click_exact_publish_and_observe
    from uploader.sohu_uploader.main import SoHuArticle

    publisher = SoHuArticle(
        title=content.title,
        body=content.content,
        tags=list(content.tags),
        publish_date=0,
        account_file=account_file,
        dry_run=False,
        cover_paths=list(content.images),
        info_source="包含AI创作内容",
    )
    observed_result: dict[str, Any] = {}

    async def click_publish(page: Any) -> None:
        observed_result.update(await click_exact_publish_and_observe(page))

    publisher.publish = click_publish
    await publisher.main()
    if not observed_result:
        raise RuntimeError("搜狐号发布流程结束，但未产生可核验的提交结果")
    return observed_result


async def _default_baijiahao_login_checker(account_file: str) -> bool:
    from uploader.baijiahao_uploader.main import cookie_auth

    return await cookie_auth(account_file)


async def _default_baijiahao_login_runner(account_file: str) -> bool:
    from uploader.baijiahao_uploader.main import baijiahao_setup

    return await baijiahao_setup(account_file, handle=True)


async def _default_baijiahao_publish_runner(
    content: PublishContent,
    account_file: str,
) -> Any:
    from services.baijiahao_publish_flow import click_exact_publish_and_observe
    from uploader.baijiahao_uploader.main import BaiJiaHaoArticle

    publisher = BaiJiaHaoArticle(
        title=content.title,
        body=_markdown_to_platform_text(content.content),
        tags=list(content.tags),
        publish_date=0,
        account_file=account_file,
        dry_run=False,
        cover_path=content.images[0],
        ai_generated=True,
    )
    observed_result: dict[str, Any] = {}

    async def click_publish(page: Any) -> None:
        observed_result.update(await click_exact_publish_and_observe(page))

    publisher.publish = click_publish
    await publisher.main()
    if not observed_result:
        raise RuntimeError("百家号发布流程结束，但未产生可核验的提交结果")
    return observed_result


async def _default_xiaohongshu_login_checker(account_file: str) -> bool:
    from uploader.xiaohongshu_uploader.main import cookie_auth

    return await cookie_auth(account_file)


async def _default_xiaohongshu_login_runner(account_file: str) -> bool:
    from uploader.xiaohongshu_uploader.main import xiaohongshu_setup

    return await xiaohongshu_setup(account_file, handle=True, headless=False)


async def _default_xiaohongshu_publish_runner(
    content: PublishContent,
    account_file: str,
) -> Any:
    from services.xiaohongshu_publish_flow import click_exact_publish_and_observe
    from uploader.xiaohongshu_uploader.main import XiaoHongShuNote

    observed_result: dict[str, Any] = {}

    async def observe_publish(page: Any, *, scheduled: bool = False) -> None:
        observed_result.update(
            await click_exact_publish_and_observe(page, scheduled=scheduled)
        )

    note_text = _markdown_to_platform_text(content.content, strip_hashes=True)
    publisher = XiaoHongShuNote(
        image_paths=list(content.images),
        note=note_text,
        tags=list(content.tags),
        publish_date=0,
        account_file=account_file,
        title=content.title,
        desc=note_text,
        headless=False,
        dry_run=False,
        ai_generated=True,
        publish_callback=observe_publish,
    )
    await publisher.main()
    if not observed_result:
        raise RuntimeError("小红书发布流程结束，但未产生可核验的提交结果")
    return observed_result


def _run_with_timeout(
    runner: Callable[..., Any],
    *args: Any,
    timeout_seconds: float,
) -> Any:
    async def invoke() -> Any:
        value = runner(*args)
        if inspect.isawaitable(value):
            return await value
        return value

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(asyncio.wait_for(invoke(), timeout=timeout_seconds))
    raise RuntimeError("同步发布适配器不能在运行中的事件循环内调用")


def _validate_content_platform(content: PublishContent, platform: str) -> None:
    if not isinstance(content, PublishContent):
        raise TypeError("content 必须是 PublishContent")
    if content.platform != platform:
        raise ValueError(f"内容平台 {content.platform} 与适配器 {platform} 不一致")


def _normalize_string_tuple(values: Sequence[Any] | str | None) -> tuple[str, ...]:
    if values is None:
        return ()
    source = (values,) if isinstance(values, str) else values
    return tuple(str(item).strip() for item in source if str(item).strip())


def _is_public_toutiao_url(value: str) -> bool:
    normalized = str(value or "").strip().lower()
    return normalized.startswith(("https://", "http://")) and (
        "toutiao.com/article/" in normalized or "toutiao.com/item/" in normalized
    )


def _is_public_sohu_url(value: str) -> bool:
    normalized = str(value or "").strip().lower()
    if not normalized.startswith(("https://", "http://")):
        return False
    return bool(
        re.fullmatch(r"https?://(?:www\.)?sohu\.com/a/\d+_\d+/?", normalized)
    )


def _is_public_baijiahao_url(value: str) -> bool:
    normalized = str(value or "").strip().lower()
    return bool(
        re.match(r"https?://baijiahao\.baidu\.com/s\?id=\d+", normalized)
    )


def _is_public_xiaohongshu_url(value: str) -> bool:
    normalized = str(value or "").strip().lower()
    return bool(
        re.match(
            r"https?://(?:www\.)?xiaohongshu\.com/(?:explore|discovery/item)/[a-f0-9]+",
            normalized,
        )
    )


def _markdown_to_platform_text(value: str, *, strip_hashes: bool = False) -> str:
    """Convert stored Markdown into conservative plain text for browser editors."""

    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"^[ \t]{0,3}#{1,6}[ \t]*", "", text, flags=re.MULTILINE)
    text = re.sub(r"^[ \t]{0,3}>[ \t]?", "", text, flags=re.MULTILINE)
    text = re.sub(r"^[ \t]{0,3}[-*+][ \t]+", "• ", text, flags=re.MULTILINE)
    text = re.sub(
        r"^[ \t]{0,3}([-*_])(?:[ \t]*\1){2,}[ \t]*$",
        "",
        text,
        flags=re.MULTILINE,
    )
    text = re.sub(r"\*\*([^*\n]+)\*\*", r"\1", text)
    text = re.sub(r"__([^_\n]+)__", r"\1", text)
    text = re.sub(r"~~([^~\n]+)~~", r"\1", text)
    text = re.sub(r"`{1,3}([^`\n]+)`{1,3}", r"\1", text)
    if strip_hashes:
        text = text.replace("#", "")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _normalize_guarded_public_result(
    raw_result: Any,
    *,
    platform: str,
    platform_name: str,
    public_url_checker: Callable[[str], bool],
) -> PublishResult:
    if isinstance(raw_result, PublishResult):
        if raw_result.platform != platform:
            return PublishResult(
                False,
                platform,
                "failed",
                message=f"{platform_name}发布器返回了错误的平台标识",
            )
        if raw_result.status == "success" and not public_url_checker(raw_result.url):
            return PublishResult(
                False,
                platform,
                "processing",
                message=f"{platform_name}未返回可核验的公开内容链接，请到平台后台确认",
            )
        return raw_result
    if isinstance(raw_result, Mapping):
        raw_status = str(raw_result.get("status") or "processing").strip().lower()
        if raw_status not in PUBLISH_RESULT_STATUSES:
            return PublishResult(
                False,
                platform,
                "failed",
                message=f"{platform_name}发布器返回了未知状态：{raw_status}",
            )
        url = str(raw_result.get("url") or raw_result.get("result_url") or "").strip()
        if raw_status == "success" and not public_url_checker(url):
            raw_status = "processing"
        success = raw_status == "success"
        return PublishResult(
            success=success,
            platform=platform,
            status=raw_status,
            url=url,
            message=str(raw_result.get("message") or "").strip(),
            published_at=_utc_now_iso() if success else "",
        )
    return PublishResult(
        False,
        platform,
        "processing",
        message=f"已执行{platform_name}发布流程，但未取得可核验的平台结果",
    )


def classify_publisher_error(error: Exception | str) -> str:
    """Map platform safety/login failures to the shared task status."""

    normalized = str(error).lower()
    return (
        "need_action"
        if any(marker in normalized for marker in _NEED_ACTION_MARKERS)
        else "failed"
    )


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
