from __future__ import annotations

from datetime import datetime
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler

from services.publish_job_executor import PublisherFactory
from services.publish_scheduler_service import run_publish_scheduler_tick


def create_publish_scheduler(
    database_path: str | Path,
    *,
    interval_seconds: int = 15,
    publisher_factory: PublisherFactory | None = None,
    allow_real: bool = False,
    media_root: str | Path | None = None,
) -> BackgroundScheduler:
    """Build, but do not start, the application's lightweight scheduler."""

    if interval_seconds < 5:
        raise ValueError("调度间隔不能少于 5 秒")
    scheduler = BackgroundScheduler(timezone=datetime.now().astimezone().tzinfo)
    job_kwargs = {"database_path": str(database_path)}
    if publisher_factory is not None:
        job_kwargs["publisher_factory"] = publisher_factory
    if allow_real:
        job_kwargs["allow_real"] = True
    if media_root is not None:
        job_kwargs["media_root"] = str(media_root)
    scheduler.add_job(
        run_publish_scheduler_tick,
        trigger="interval",
        seconds=interval_seconds,
        kwargs=job_kwargs,
        id="publish-jobs-due-tick",
        name="检查到期发布任务",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=max(30, interval_seconds * 2),
        replace_existing=True,
    )
    return scheduler
