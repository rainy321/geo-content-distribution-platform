from __future__ import annotations

import hmac
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from services.article_service import get_article
from services.geo_article_compiler_service import (
    GEOArticleCompilerError,
)
from services.project_service import ProjectNotFoundError, get_project
from services.publish_job_service import (
    ConcurrentPublishJobUpdateError,
    PUBLISH_AUTHORIZATION_MISMATCH_MESSAGE,
    build_publish_confirmation_fingerprint,
    claim_publish_job,
    mark_publish_job_awaiting_reconciliation,
    publish_authorization_matches,
    transition_publish_job,
)
from services.publisher_adapter import (
    classify_publisher_error,
    DemoPublisher,
    PublishContent,
    PublisherAdapter,
    PublishResult,
)
from services.publish_preflight_service import (
    PublishContentLimitError,
    evaluate_publish_citeability,
    validate_platform_publish_variant,
)


PublisherFactory = Callable[[dict[str, Any]], PublisherAdapter]
DEFAULT_MEDIA_ROOT = Path(__file__).resolve().parent.parent / "videoFile"


class PublisherNotConfiguredError(RuntimeError):
    """Raised when a real publish job has no explicitly configured adapter."""


class PublishConfirmationMismatchError(RuntimeError):
    """Raised after a claimed job no longer matches interactive confirmation."""

    def __init__(self, message: str, *, job: dict[str, Any]):
        super().__init__(message)
        self.job = job


class PublishCiteabilityGateError(RuntimeError):
    """Raised when the exact real-publication copy is not safe to release."""


def execute_publish_job(
    database_path: str | Path,
    job_id: int,
    *,
    publisher_factory: PublisherFactory | None = None,
    media_root: str | Path = DEFAULT_MEDIA_ROOT,
    require_authorization_match: bool = False,
    expected_state_version: int | None = None,
    expected_confirmation_fingerprint: str | None = None,
) -> dict[str, Any]:
    """Claim and execute exactly one queued job.

    Demo jobs are always forced through DemoPublisher. Real jobs require an
    explicit factory so calling this function can never contact a platform by
    accident.
    """

    if (
        expected_confirmation_fingerprint is not None
        and expected_state_version is None
    ):
        raise ValueError(
            "二次确认指纹校验必须同时提供 expected_state_version"
        )

    job = claim_publish_job(
        database_path,
        job_id,
        expected_state_version=expected_state_version,
    )
    try:
        article = get_article(database_path, job["article_id"])
        if expected_confirmation_fingerprint is not None:
            confirmed_job = {
                **job,
                # Claim increments state_version.  The browser confirmation was
                # rendered for the immediately preceding queued snapshot.
                "state_version": expected_state_version,
            }
            current_fingerprint = build_publish_confirmation_fingerprint(
                job=confirmed_job,
                article=article,
            )
            if not hmac.compare_digest(
                expected_confirmation_fingerprint,
                current_fingerprint,
            ):
                mismatch_message = (
                    "领取任务后检测到文章内容或发布参数已变化；"
                    "未加载发布器、未访问平台，请重试并重新确认"
                )
                blocked_job = transition_publish_job(
                    database_path,
                    job_id,
                    "need_action",
                    message=mismatch_message,
                    expected_state_version=job["state_version"],
                )
                raise PublishConfirmationMismatchError(
                    mismatch_message,
                    job=blocked_job,
                )
        if require_authorization_match and not publish_authorization_matches(
            job,
            article,
        ):
            return transition_publish_job(
                database_path,
                job_id,
                "need_action",
                message=PUBLISH_AUTHORIZATION_MISMATCH_MESSAGE,
                expected_state_version=job["state_version"],
            )
        content = _build_publish_content(job, article, media_root=media_root)
        if not job["demo"]:
            _enforce_citeability_gate(
                database_path,
                article=article,
                title=content.title,
                content=content.content,
            )
        publisher = _select_publisher(job, publisher_factory)
        result = publisher.publish(content)
        if not isinstance(result, PublishResult):
            raise TypeError("发布器必须返回 PublishResult")
        return _persist_result(database_path, job, result)
    except PublishConfirmationMismatchError:
        raise
    except ConcurrentPublishJobUpdateError:
        # A human action, reconciliation, or a later retry already changed the
        # attempt. Never translate that conflict into a retryable failure.
        raise
    except PublisherNotConfiguredError as exc:
        return transition_publish_job(
            database_path,
            job_id,
            "need_action",
            message=str(exc),
            expected_state_version=job["state_version"],
        )
    except PublishContentLimitError as exc:
        return transition_publish_job(
            database_path,
            job_id,
            "need_action",
            message=(
                f"发布前置校验未通过：{exc}；未加载发布器、未访问平台，"
                "请重新创建任务并编辑平台短版"
            ),
            expected_state_version=job["state_version"],
        )
    except PublishCiteabilityGateError as exc:
        return transition_publish_job(
            database_path,
            job_id,
            "need_action",
            message=(
                f"AI 可引用性发布门禁未通过：{exc}；"
                "未加载发布器、未访问平台，请返回内容库完成优化后重试"
            ),
            expected_state_version=job["state_version"],
        )
    except Exception as exc:
        detail = str(exc).strip() or exc.__class__.__name__
        return transition_publish_job(
            database_path,
            job_id,
            classify_publisher_error(detail),
            message=f"发布执行失败：{detail}",
            expected_state_version=job["state_version"],
        )


def _select_publisher(
    job: dict[str, Any],
    publisher_factory: PublisherFactory | None,
) -> PublisherAdapter:
    if job["demo"]:
        return DemoPublisher(job["platform"])
    if publisher_factory is None:
        raise PublisherNotConfiguredError(
            f"{job['platform']} 真实发布器尚未配置，需要先连接媒体账号"
        )
    publisher = publisher_factory(job)
    if not isinstance(publisher, PublisherAdapter):
        raise TypeError("publisher_factory 必须返回 PublisherAdapter")
    if publisher.platform != job["platform"]:
        raise ValueError(
            f"任务平台 {job['platform']} 与发布器 {publisher.platform} 不一致"
        )
    return publisher


def _enforce_citeability_gate(
    database_path: str | Path,
    *,
    article: dict[str, Any],
    title: str,
    content: str,
) -> dict[str, Any]:
    """Score the exact outbound copy and reject failed real publications.

    Re-scoring here intentionally covers legacy articles without a persisted
    ``geo_analysis.citeability`` payload and platform-specific title/body
    overrides.  It is deterministic and local: no model or platform is called.
    """

    try:
        project = get_project(database_path, int(article["project_id"]))
        result = evaluate_publish_citeability(
            title=title,
            content=content,
            brand=str(project.get("name") or "").strip(),
            product=str(project.get("product") or "").strip(),
            website=str(project.get("website") or "").strip(),
            keywords=(
                project.get("keywords")
                if isinstance(project.get("keywords"), list)
                else []
            ),
        )
    except (GEOArticleCompilerError, ProjectNotFoundError, KeyError, TypeError, ValueError) as exc:
        raise PublishCiteabilityGateError(
            f"无法校验当前发布副本（{str(exc).strip() or exc.__class__.__name__}）"
        ) from exc

    if result.get("passed") is True:
        return result

    actions: list[str] = []
    for issue in result.get("blocking_issues") or ():
        if not isinstance(issue, dict):
            continue
        text = str(issue.get("action") or issue.get("message") or "").strip()
        if text and text not in actions:
            actions.append(text)
    for suggestion in result.get("suggestions") or ():
        text = str(suggestion or "").strip()
        if text and text not in actions:
            actions.append(text)
    action_text = "；".join(actions[:3]) or "补齐标准实体、直接答案、结构与可核验来源"
    score = result.get("score", 0)
    pass_score = result.get("pass_score", 70)
    raise PublishCiteabilityGateError(
        f"当前 {score}/100，要求至少 {pass_score}/100。处理建议：{action_text}"
    )


def _build_publish_content(
    job: dict[str, Any],
    article: dict[str, Any],
    *,
    media_root: str | Path = DEFAULT_MEDIA_ROOT,
) -> PublishContent:
    publish_at = job.get("publish_at")
    if isinstance(publish_at, str) and publish_at.strip():
        publish_at = datetime.fromisoformat(publish_at.strip())
    effective_title = str(job.get("publish_title") or article["title"]).strip()
    effective_content = str(
        job.get("publish_content") or article["content"]
    ).strip()
    validate_platform_publish_variant(
        platform=job["platform"],
        title=effective_title,
        content=effective_content,
        tags=article.get("tags") or (),
    )

    resolved_media_root = Path(media_root).expanduser().resolve()
    image_paths: list[str] = []
    for filename in job.get("images") or ():
        image_path = (resolved_media_root / filename).resolve()
        try:
            image_path.relative_to(resolved_media_root)
        except ValueError as exc:
            raise ValueError("发布图片必须位于素材目录内") from exc
        if not image_path.is_file():
            raise FileNotFoundError(f"发布图片不存在：{filename}")
        image_paths.append(str(image_path))

    video_path = ""
    video_filename = str(job.get("video") or "").strip()
    if video_filename:
        resolved_video_path = (resolved_media_root / video_filename).resolve()
        try:
            resolved_video_path.relative_to(resolved_media_root)
        except ValueError as exc:
            raise ValueError("发布视频必须位于素材目录内") from exc
        if not resolved_video_path.is_file():
            raise FileNotFoundError(f"发布视频不存在：{video_filename}")
        video_path = str(resolved_video_path)

    return PublishContent(
        platform=job["platform"],
        title=effective_title,
        content=effective_content,
        images=tuple(image_paths),
        video=video_path,
        tags=tuple(article.get("tags") or ()),
        publish_at=publish_at,
    )


def _persist_result(
    database_path: str | Path,
    job: dict[str, Any],
    result: PublishResult,
) -> dict[str, Any]:
    if result.platform != job["platform"]:
        raise ValueError(
            f"任务平台 {job['platform']} 与发布结果 {result.platform} 不一致"
        )
    if bool(result.demo) != bool(job["demo"]):
        raise ValueError("发布结果的 Demo 标记与任务不一致")

    expected_success = result.status == "success"
    if result.success != expected_success:
        raise ValueError("发布结果的 success 与 status 不一致")

    if result.status in {"success", "failed", "need_action"}:
        return transition_publish_job(
            database_path,
            job["id"],
            result.status,
            message=result.message,
            result_url=result.url,
            expected_state_version=job["state_version"],
        )
    if result.status == "processing":
        return mark_publish_job_awaiting_reconciliation(
            database_path,
            job["id"],
            message=result.message,
            result_url=result.url,
            expected_state_version=job["state_version"],
        )
    raise ValueError(f"即时发布不能返回 {result.status} 状态")
