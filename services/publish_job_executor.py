from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from services.article_service import get_article
from services.publish_job_service import (
    PUBLISH_AUTHORIZATION_MISMATCH_MESSAGE,
    claim_publish_job,
    publish_authorization_matches,
    transition_publish_job,
    update_publish_job_progress,
)
from services.publisher_adapter import (
    classify_publisher_error,
    DemoPublisher,
    PublishContent,
    PublisherAdapter,
    PublishResult,
)


PublisherFactory = Callable[[dict[str, Any]], PublisherAdapter]
DEFAULT_MEDIA_ROOT = Path(__file__).resolve().parent.parent / "videoFile"


class PublisherNotConfiguredError(RuntimeError):
    """Raised when a real publish job has no explicitly configured adapter."""


def execute_publish_job(
    database_path: str | Path,
    job_id: int,
    *,
    publisher_factory: PublisherFactory | None = None,
    media_root: str | Path = DEFAULT_MEDIA_ROOT,
    require_authorization_match: bool = False,
) -> dict[str, Any]:
    """Claim and execute exactly one queued job.

    Demo jobs are always forced through DemoPublisher. Real jobs require an
    explicit factory so calling this function can never contact a platform by
    accident.
    """

    job = claim_publish_job(database_path, job_id)
    try:
        article = get_article(database_path, job["article_id"])
        if require_authorization_match and not publish_authorization_matches(
            job,
            article,
        ):
            return transition_publish_job(
                database_path,
                job_id,
                "need_action",
                message=PUBLISH_AUTHORIZATION_MISMATCH_MESSAGE,
            )
        content = _build_publish_content(job, article, media_root=media_root)
        publisher = _select_publisher(job, publisher_factory)
        result = publisher.publish(content)
        if not isinstance(result, PublishResult):
            raise TypeError("发布器必须返回 PublishResult")
        return _persist_result(database_path, job, result)
    except PublisherNotConfiguredError as exc:
        return transition_publish_job(
            database_path,
            job_id,
            "need_action",
            message=str(exc),
        )
    except Exception as exc:
        detail = str(exc).strip() or exc.__class__.__name__
        return transition_publish_job(
            database_path,
            job_id,
            classify_publisher_error(detail),
            message=f"发布执行失败：{detail}",
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


def _build_publish_content(
    job: dict[str, Any],
    article: dict[str, Any],
    *,
    media_root: str | Path = DEFAULT_MEDIA_ROOT,
) -> PublishContent:
    publish_at = job.get("publish_at")
    if isinstance(publish_at, str) and publish_at.strip():
        publish_at = datetime.fromisoformat(publish_at.strip())
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

    return PublishContent(
        platform=job["platform"],
        title=article["title"],
        content=article["content"],
        images=tuple(image_paths),
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
        )
    if result.status == "processing":
        return update_publish_job_progress(
            database_path,
            job["id"],
            message=result.message,
            result_url=result.url,
        )
    raise ValueError(f"即时发布不能返回 {result.status} 状态")
