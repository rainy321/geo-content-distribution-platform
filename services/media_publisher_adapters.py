from __future__ import annotations

import asyncio
import inspect
import re
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from services.publisher_adapter import (
    PUBLISH_RESULT_STATUSES,
    PublishContent,
    PublisherAdapter,
    PublishResult,
    classify_publisher_error,
)


class MediaPublisherAdapter(PublisherAdapter):
    """Guarded bridge from the Web publish contract to legacy media uploaders."""

    def __init__(
        self,
        platform: str,
        platform_name: str,
        account_file: str | Path,
        *,
        requires_images: bool = False,
        requires_video: bool = False,
        timeout_seconds: float = 300,
        login_checker: Callable[..., Any],
        publish_runner: Callable[..., Any],
    ):
        if timeout_seconds <= 0:
            raise ValueError("超时时间必须大于 0")
        self.platform = str(platform).strip().lower()
        self.platform_name = str(platform_name).strip()
        self.account_file = str(account_file)
        self.requires_images = bool(requires_images)
        self.requires_video = bool(requires_video)
        self.timeout_seconds = float(timeout_seconds)
        self._login_checker = login_checker
        self._publish_runner = publish_runner

    def login(self) -> bool:
        # Interactive login is intentionally owned by the media-account page.
        return self.check_login()

    def check_login(self) -> bool:
        return bool(
            _run_with_timeout(
                self._login_checker,
                self.account_file,
                timeout_seconds=self.timeout_seconds,
            )
        )

    def publish(self, content: PublishContent) -> PublishResult:
        self._validate_content(content)
        try:
            if not self.check_login():
                return PublishResult(
                    False,
                    self.platform,
                    "need_action",
                    message=f"{self.platform_name}登录状态无效，需要重新登录后重试",
                )
        except (TimeoutError, asyncio.TimeoutError):
            return PublishResult(
                False,
                self.platform,
                "failed",
                message=f"{self.platform_name}登录状态检查超时，请稍后重试",
            )
        except Exception as exc:
            return self._exception_result(exc, prefix="登录状态检查失败")

        try:
            raw_result = _run_with_timeout(
                self._publish_runner,
                content,
                self.account_file,
                timeout_seconds=self.timeout_seconds,
            )
        except (TimeoutError, asyncio.TimeoutError):
            return PublishResult(
                False,
                self.platform,
                "processing",
                message=(
                    f"{self.platform_name}发布操作超时，平台最终状态未知；"
                    "请先人工核对，确认后再重新授权；系统不会自动重试"
                ),
            )
        except Exception as exc:
            detail = str(exc).strip() or exc.__class__.__name__
            return PublishResult(
                False,
                self.platform,
                "processing",
                message=(
                    f"{self.platform_name}发布过程异常：{detail}；平台最终状态未知；"
                    "请先人工核对，确认未提交后再重新授权；系统不会自动重试"
                ),
            )
        return self._normalize_result(raw_result)

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
            True,
            self.platform,
            "scheduled",
            message=f"任务已排期至 {scheduled_for.isoformat()}，等待系统调度",
        )

    def _validate_content(self, content: PublishContent) -> None:
        if not isinstance(content, PublishContent):
            raise TypeError("content 必须是 PublishContent")
        if content.platform != self.platform:
            raise ValueError(
                f"内容平台 {content.platform} 与适配器 {self.platform} 不一致"
            )
        from services.publish_preflight_service import validate_platform_publish_variant

        validate_platform_publish_variant(
            platform=self.platform,
            title=content.title,
            content=content.content,
            tags=content.tags,
        )
        if self.requires_images and not content.images:
            raise ValueError(f"{self.platform_name}图文发布至少需要一张图片")
        if self.requires_video and not content.video:
            raise ValueError(f"{self.platform_name}发布必须提供视频文件")

    def _normalize_result(self, raw_result: Any) -> PublishResult:
        if isinstance(raw_result, PublishResult):
            if raw_result.platform != self.platform:
                return PublishResult(
                    False,
                    self.platform,
                    "failed",
                    message=f"{self.platform_name}发布器返回了错误的平台标识",
                )
            if raw_result.status == "success" and not _is_public_http_url(
                raw_result.url
            ):
                return self._processing_without_public_url(raw_result.message)
            return raw_result
        if isinstance(raw_result, Mapping):
            status = str(raw_result.get("status") or "processing").strip().lower()
            if status not in PUBLISH_RESULT_STATUSES:
                status = "failed"
            url = str(raw_result.get("url") or raw_result.get("result_url") or "").strip()
            message = str(raw_result.get("message") or "").strip()
            if status == "success" and not _is_public_http_url(url):
                return self._processing_without_public_url(message)
            return PublishResult(
                status == "success",
                self.platform,
                status,
                url=url,
                message=message,
            )
        # Legacy uploaders can verify navigation but do not consistently return
        # a public URL. Keep the job processing instead of claiming success.
        return PublishResult(
            False,
            self.platform,
            "processing",
            message=(
                f"已执行{self.platform_name}提交链路，但平台未返回可核验公开链接；"
                "请在平台后台核对"
            ),
        )

    def _processing_without_public_url(self, message: str = "") -> PublishResult:
        detail = str(message or "").strip()
        if detail and "公开链接" not in detail:
            detail = f"{detail}；平台未返回可核验公开链接，请在平台后台核对"
        if not detail:
            detail = (
                f"已执行{self.platform_name}提交链路，但平台未返回可核验公开链接；"
                "请在平台后台核对"
            )
        return PublishResult(False, self.platform, "processing", message=detail)

    def _exception_result(self, exc: Exception, *, prefix: str) -> PublishResult:
        detail = str(exc).strip() or exc.__class__.__name__
        return PublishResult(
            False,
            self.platform,
            classify_publisher_error(detail),
            message=f"{self.platform_name}{prefix}：{detail}",
        )


class DouyinPublisherAdapter(MediaPublisherAdapter):
    def __init__(self, account_file: str | Path, **overrides: Any):
        super().__init__(
            "douyin",
            "抖音",
            account_file,
            requires_images=True,
            login_checker=overrides.pop("login_checker", _douyin_login_checker),
            publish_runner=overrides.pop("publish_runner", _douyin_publish_runner),
            **overrides,
        )


class KuaishouPublisherAdapter(MediaPublisherAdapter):
    def __init__(self, account_file: str | Path, **overrides: Any):
        super().__init__(
            "kuaishou",
            "快手",
            account_file,
            requires_images=True,
            login_checker=overrides.pop("login_checker", _kuaishou_login_checker),
            publish_runner=overrides.pop("publish_runner", _kuaishou_publish_runner),
            **overrides,
        )


class BilibiliPublisherAdapter(MediaPublisherAdapter):
    def __init__(self, account_file: str | Path, **overrides: Any):
        super().__init__(
            "bilibili",
            "Bilibili",
            account_file,
            requires_video=True,
            login_checker=overrides.pop("login_checker", _bilibili_login_checker),
            publish_runner=overrides.pop("publish_runner", _bilibili_publish_runner),
            timeout_seconds=overrides.pop("timeout_seconds", 7200),
            **overrides,
        )


class ChannelsPublisherAdapter(MediaPublisherAdapter):
    def __init__(self, account_file: str | Path, **overrides: Any):
        super().__init__(
            "channels",
            "视频号",
            account_file,
            requires_video=True,
            login_checker=overrides.pop("login_checker", _channels_login_checker),
            publish_runner=overrides.pop("publish_runner", _channels_publish_runner),
            **overrides,
        )


class TiktokPublisherAdapter(MediaPublisherAdapter):
    def __init__(self, account_file: str | Path, **overrides: Any):
        super().__init__(
            "tiktok",
            "TikTok",
            account_file,
            requires_video=True,
            login_checker=overrides.pop("login_checker", _tiktok_login_checker),
            publish_runner=overrides.pop("publish_runner", _tiktok_publish_runner),
            **overrides,
        )


async def _douyin_login_checker(account_file: str) -> bool:
    from myUtils.auth import cookie_auth_douyin

    return await cookie_auth_douyin(account_file)


async def _douyin_publish_runner(content: PublishContent, account_file: str) -> Any:
    from uploader.douyin_uploader.main import DouYinNote

    publisher = DouYinNote(
        image_paths=list(content.images),
        note=content.content,
        tags=list(content.tags),
        publish_date=0,
        account_file=account_file,
        title=content.title,
        headless=False,
        ai_generated=True,
    )
    try:
        result = await publisher.douyin_upload_note()
    except asyncio.CancelledError:
        return _media_interrupted_result(
            publisher,
            platform_name="抖音",
            submit_attribute="submit_click_attempted",
            error=None,
        )
    except Exception as exc:
        return _media_interrupted_result(
            publisher,
            platform_name="抖音",
            submit_attribute="submit_click_attempted",
            error=exc,
        )
    return result or {
        "status": "processing",
        "message": (
            "抖音已完成一次提交链路，但未取得可核验的公开链接；"
            "请先在作品管理中核对，系统不会自动重试"
        ),
    }


async def _kuaishou_login_checker(account_file: str) -> bool:
    from myUtils.auth import cookie_auth_ks

    return await cookie_auth_ks(account_file)


async def _kuaishou_publish_runner(content: PublishContent, account_file: str) -> Any:
    from uploader.ks_uploader.main import KSNote

    publisher = KSNote(
        image_paths=list(content.images),
        note=content.content,
        tags=list(content.tags),
        publish_date=0,
        account_file=account_file,
        title=content.title,
        headless=False,
        ai_generated=True,
    )
    try:
        result = await publisher.main()
    except asyncio.CancelledError:
        return _media_interrupted_result(
            publisher,
            platform_name="快手",
            submit_attribute="submit_click_attempted",
            error=None,
        )
    except Exception as exc:
        return _media_interrupted_result(
            publisher,
            platform_name="快手",
            submit_attribute="submit_click_attempted",
            error=exc,
        )
    return result or {
        "status": "processing",
        "message": (
            "快手已完成一次提交链路，但未取得可核验的公开链接；"
            "请先在作品管理中核对，系统不会自动重试"
        ),
    }


def _media_interrupted_result(
    publisher: Any,
    *,
    platform_name: str,
    submit_attribute: str,
    error: Exception | None,
) -> dict[str, str]:
    """Preserve the one-click boundary and expose actionable account failures."""

    from services.media_account_risk_service import (
        classify_media_account_risk,
        sanitize_risk_evidence,
    )

    detail = sanitize_risk_evidence(error or "", limit=180)
    risk = classify_media_account_risk(detail)
    if risk and risk["category"] != "network_error":
        return {
            "status": "need_action",
            "message": (
                f"{platform_name}{risk['label']}[{risk['category']}]："
                f"{risk.get('evidence') or risk['reason']}；"
                "任务已暂停，请按平台提示处理并先核对作品管理，系统不会自动重试"
            ),
        }

    if bool(getattr(publisher, submit_attribute, False)):
        return {
            "status": "processing",
            "message": (
                f"{platform_name}已触发一次发布操作，但平台最终结果未知；"
                "请先在作品管理中核对，系统不会自动重试"
            ),
        }

    if risk and risk["category"] == "network_error":
        problem = f"网络或浏览器连接异常：{risk.get('evidence') or risk['reason']}"
    elif detail:
        problem = detail
    else:
        problem = "发布流程在点击前被中止"
    return {
        "status": "failed",
        "message": (
            f"{platform_name}在点击发布前失败，尚未提交：{problem}；"
            "确认平台后台没有新作品后可安全重试"
        ),
    }


def _bilibili_login_checker(account_file: str) -> bool:
    from uploader.bilibili_uploader.runtime import run_biliup_command

    try:
        return run_biliup_command(
            ["-u", account_file, "renew"],
            timeout_seconds=60,
        ).returncode == 0
    except Exception:
        # Exceptions such as TimeoutExpired can embed the full command, including
        # the account-file path.  Never let that representation reach the API.
        raise RuntimeError(
            "Bilibili 登录状态检查不可用（错误码 BILIUP_LOGIN_CHECK_FAILED）"
        ) from None


def _bilibili_publish_runner(content: PublishContent, account_file: str) -> dict[str, Any]:
    from uploader.bilibili_uploader.runtime import run_biliup_command

    arguments = [
        "-u",
        account_file,
        "upload",
        content.video,
        "--title",
        content.title,
        "--desc",
        content.content,
        "--tid",
        "21",
        "--copyright",
        "1",
    ]
    if content.tags:
        arguments.extend(["--tag", ",".join(content.tags)])
    arguments.extend(
        ["--extra-fields", '{"creation_statement":{"id":1}}']
    )
    try:
        result = run_biliup_command(arguments, timeout_seconds=7200)
    except Exception:
        # A subprocess exception may embed title/body/path/cookie arguments.
        # Preserve the truthful unknown-state workflow with a stable safe code.
        raise RuntimeError(
            "Bilibili 上传命令异常（错误码 BILIUP_RUNTIME_ERROR）"
        ) from None
    if result.returncode != 0:
        # CLI output may contain local paths, account-file locations, cookies or
        # request details.  It is intentionally not propagated to PublishResult,
        # SQLite or the DOM.  The bounded tail remains internal to the invocation.
        normalized_code = str(int(result.returncode)).replace("-", "N")
        raise RuntimeError(f"Bilibili 上传命令失败（错误码 BILIUP_EXIT_{normalized_code}）")
    output = f"{result.stdout or ''}\n{result.stderr or ''}"
    bvid_match = re.search(r"(?<![0-9A-Za-z])(BV[0-9A-Za-z]{10})(?![0-9A-Za-z])", output)
    if bvid_match:
        return {
            "status": "success",
            "url": f"https://www.bilibili.com/video/{bvid_match.group(1)}",
            "message": "Bilibili 已返回可核验的 BV 号",
        }
    return {
        "status": "processing",
        "message": (
            "Bilibili 已接受上传，但未返回可核验 BV 链接；"
            "请在稿件管理中核对后完成任务"
        ),
    }


async def _channels_login_checker(account_file: str) -> bool:
    from myUtils.auth import cookie_auth_tencent

    return await cookie_auth_tencent(account_file)


async def _channels_publish_runner(content: PublishContent, account_file: str) -> Any:
    from uploader.tencent_uploader.main import TencentVideo

    publisher = TencentVideo(
        title=content.title,
        file_path=content.video,
        tags=list(content.tags),
        publish_date=0,
        account_file=account_file,
        desc=content.content,
        thumbnail_path=content.images[0] if content.images else None,
        headless=False,
    )
    try:
        await publisher.main()
    except asyncio.CancelledError:
        return _channels_interrupted_result(publisher, error=None)
    except Exception as exc:
        return _channels_interrupted_result(publisher, error=exc)
    return {
        "status": "processing",
        "message": "视频号已完成一次提交链路，公开链接需在内容管理中核验",
    }


def _channels_interrupted_result(publisher: Any, *, error: Exception | None):
    if bool(getattr(publisher, "submit_click_attempted", False)):
        return {
            "status": "processing",
            "message": (
                "视频号已触发一次发表操作，但最终结果未能确认；"
                "请先在视频号助手核对，系统不会自动重试"
            ),
        }
    status = classify_publisher_error(error) if error is not None else "failed"
    detail = str(error or "").lower()
    if any(marker in detail for marker in ("验证", "登录", "扫码", "captcha")):
        status = "need_action"
    return {
        "status": status,
        "message": (
            "视频号在点击发表前中止，尚未提交；"
            "可在修复登录、素材或页面问题后安全重试"
        ),
    }


async def _tiktok_login_checker(account_file: str) -> bool:
    from uploader.tk_uploader.main_chrome import cookie_auth

    return await cookie_auth(account_file)


async def _tiktok_publish_runner(content: PublishContent, account_file: str):
    from services.platform_publish_policy_service import adapt_tags_for_platform
    from uploader.tk_uploader.main_chrome import TiktokVideo

    tag_preview = adapt_tags_for_platform("tiktok", content.tags)
    publisher = TiktokVideo(
        title=content.title,
        file_path=content.video,
        tags=list(tag_preview["selected"]),
        publish_date=0,
        account_file=account_file,
        thumbnail_path=content.images[0] if content.images else None,
        headless=False,
    )
    try:
        return await publisher.main()
    except asyncio.CancelledError:
        # The synchronous adapter enforces its timeout with wait_for(), which
        # cancels this coroutine. Convert that cancellation while the publisher
        # object still exposes whether Post was reached.
        return _tiktok_interrupted_result(publisher, error=None)
    except Exception as exc:
        return _tiktok_interrupted_result(publisher, error=exc)


def _tiktok_interrupted_result(publisher: Any, *, error: Exception | None):
    if bool(getattr(publisher, "post_click_attempted", False)):
        return {
            "status": "processing",
            "message": (
                "TikTok 已触发一次 Post 点击，但平台结果未能确认；"
                "请先在 TikTok Studio 核对，系统不会自动重试"
            ),
        }
    status = classify_publisher_error(error) if error is not None else "failed"
    if status == "need_action":
        message = (
            "TikTok 在点击 Post 前被登录、验证或页面结构阻断，尚未提交；"
            "请处理平台提示后重试"
        )
    else:
        message = (
            "TikTok 在点击 Post 前中止，尚未提交；"
            "可在修正页面或素材问题后安全重试"
        )
    return {"status": status, "message": message}


def _is_public_http_url(value: str) -> bool:
    normalized = str(value or "").strip().lower()
    return normalized.startswith(("https://", "http://"))


def _run_with_timeout(
    runner: Callable[..., Any],
    *args: Any,
    timeout_seconds: float,
) -> Any:
    async def invoke() -> Any:
        if inspect.iscoroutinefunction(runner):
            return await runner(*args)
        value = await asyncio.to_thread(runner, *args)
        if inspect.isawaitable(value):
            return await value
        return value

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(asyncio.wait_for(invoke(), timeout=timeout_seconds))
    raise RuntimeError("同步发布适配器不能在运行中的事件循环内调用")
