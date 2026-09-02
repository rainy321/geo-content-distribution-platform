from __future__ import annotations

import argparse
import json
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from conf import BASE_DIR
from db.createTable import initialize_database
from services.publish_scheduler_runtime import create_publish_scheduler
from services.publish_scheduler_service import run_publish_scheduler_tick
from services.real_publisher_factory import create_real_publisher_factory


def _environment_flag(name: str, default: bool = False) -> bool:
    fallback = "true" if default else "false"
    return str(os.getenv(name, fallback)).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _environment_interval() -> int:
    try:
        value = int(os.getenv("PUBLISH_SCHEDULER_INTERVAL_SECONDS", "15"))
    except ValueError:
        value = 15
    return max(5, value)


@dataclass(frozen=True)
class WorkerSettings:
    database_path: Path
    cookies_directory: Path
    media_root: Path
    interval_seconds: int
    demo_mode: bool
    allow_real_publishing: bool

    @property
    def allows_real_execution(self) -> bool:
        return self.allow_real_publishing and not self.demo_mode

    @classmethod
    def from_environment(cls) -> "WorkerSettings":
        return cls(
            database_path=Path(
                os.getenv("DATABASE_PATH", BASE_DIR / "db" / "database.db")
            ).expanduser().resolve(),
            cookies_directory=Path(
                os.getenv("COOKIES_DIRECTORY", BASE_DIR / "cookiesFile")
            ).expanduser().resolve(),
            media_root=Path(
                os.getenv("MEDIA_ROOT", BASE_DIR / "videoFile")
            ).expanduser().resolve(),
            interval_seconds=_environment_interval(),
            demo_mode=_environment_flag("DEMO_MODE"),
            allow_real_publishing=_environment_flag("ALLOW_REAL_PUBLISHING"),
        )


def _prepare_worker(settings: WorkerSettings) -> None:
    settings.database_path.parent.mkdir(parents=True, exist_ok=True)
    settings.cookies_directory.mkdir(parents=True, exist_ok=True)
    settings.media_root.mkdir(parents=True, exist_ok=True)
    initialize_database(settings.database_path)


def _publisher_factory(settings: WorkerSettings):
    if not settings.allows_real_execution:
        return None
    return create_real_publisher_factory(
        settings.database_path,
        cookies_directory=settings.cookies_directory,
    )


def run_worker_once(settings: WorkerSettings) -> dict:
    """Process one due-task batch; useful for smoke checks and cron hosts."""

    _prepare_worker(settings)
    factory = _publisher_factory(settings)
    return run_publish_scheduler_tick(
        settings.database_path,
        publisher_factory=factory,
        allow_real=settings.allows_real_execution,
        media_root=settings.media_root,
    )


def run_worker_forever(settings: WorkerSettings) -> None:
    """Run the existing scheduler in a dedicated long-lived worker process."""

    _prepare_worker(settings)
    factory = _publisher_factory(settings)
    scheduler = create_publish_scheduler(
        settings.database_path,
        interval_seconds=settings.interval_seconds,
        publisher_factory=factory,
        allow_real=settings.allows_real_execution,
        media_root=settings.media_root,
    )
    scheduler.start()
    stop_event = threading.Event()
    try:
        stop_event.wait()
    except KeyboardInterrupt:
        pass
    finally:
        scheduler.shutdown(wait=False)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sau-worker",
        description="运行 GEO 发布任务 Worker",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="只扫描并处理一批到期任务，然后退出",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = WorkerSettings.from_environment()
    if args.once:
        result = run_worker_once(settings)
        print(json.dumps(result, ensure_ascii=False, default=str))
        return 0
    run_worker_forever(settings)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
