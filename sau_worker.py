from __future__ import annotations

import argparse
import json
import os
import sqlite3
import threading
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from conf import BASE_DIR
from db.createTable import initialize_database
from services.publish_scheduler_runtime import create_publish_scheduler
from services.publish_scheduler_service import run_publish_scheduler_tick
from services.real_publisher_factory import create_real_publisher_factory
from services.platform_capability_service import PLATFORM_CAPABILITIES


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
    enable_bilibili_runtime: bool = False

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
            enable_bilibili_runtime=_environment_flag("ENABLE_BILIBILI_RUNTIME"),
        )


def _prepare_worker(settings: WorkerSettings) -> None:
    settings.database_path.parent.mkdir(parents=True, exist_ok=True)
    settings.cookies_directory.mkdir(parents=True, exist_ok=True)
    settings.media_root.mkdir(parents=True, exist_ok=True)
    initialize_database(settings.database_path)


def _browser_runtime_status() -> str:
    configured_chrome = str(os.getenv("LOCAL_CHROME_PATH", "")).strip()
    if configured_chrome:
        return "ok" if Path(configured_chrome).is_file() else "unavailable"
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            executable = Path(playwright.chromium.executable_path)
        return "ok" if executable.is_file() else "unavailable"
    except (ImportError, OSError, RuntimeError):
        return "unavailable"


def _connected_account_count(settings: WorkerSettings) -> int:
    supported_types = tuple(item["account_type"] for item in PLATFORM_CAPABILITIES)
    placeholders = ", ".join("?" for _ in supported_types)
    with closing(sqlite3.connect(settings.database_path)) as conn:
        rows = conn.execute(
            f"""
            SELECT filePath
            FROM user_info
            WHERE status = 1 AND type IN ({placeholders})
            """,
            supported_types,
        ).fetchall()
    count = 0
    cookies_root = settings.cookies_directory.resolve()
    for (file_path,) in rows:
        raw_value = str(file_path or "").strip()
        if not raw_value:
            continue
        candidate = (cookies_root / raw_value).resolve()
        try:
            candidate.relative_to(cookies_root)
        except ValueError:
            continue
        if candidate.is_file():
            count += 1
    return count


def check_worker_readiness(settings: WorkerSettings) -> dict:
    """Validate the worker contract without opening any publishing website."""

    checks = {
        "database": "unavailable",
        "cookies_directory": "unavailable",
        "media_root": "unavailable",
        "browser_runtime": "not_required",
        "connected_accounts": 0,
    }
    issues: list[str] = []
    try:
        _prepare_worker(settings)
        with closing(sqlite3.connect(settings.database_path)) as conn:
            conn.execute("SELECT 1").fetchone()
        checks["database"] = "ok"
        checks["cookies_directory"] = (
            "ok" if settings.cookies_directory.is_dir() else "unavailable"
        )
        checks["media_root"] = (
            "ok" if settings.media_root.is_dir() else "unavailable"
        )
        checks["connected_accounts"] = _connected_account_count(settings)
    except (OSError, sqlite3.Error) as exc:
        issues.append(f"storage:{type(exc).__name__}")

    if settings.allows_real_execution:
        checks["browser_runtime"] = _browser_runtime_status()
        if checks["browser_runtime"] != "ok":
            issues.append("browser_runtime:unavailable")
        if os.getenv("VERCEL"):
            issues.append("host:serverless_not_supported")
        if checks["connected_accounts"] == 0:
            issues.append("accounts:none_connected")

    for name in ("database", "cookies_directory", "media_root"):
        if checks[name] != "ok" and not any(
            issue.startswith("storage:") for issue in issues
        ):
            issues.append(f"{name}:unavailable")

    blocking_issues = [
        issue for issue in issues if issue != "accounts:none_connected"
    ]
    return {
        "status": "ready" if not blocking_issues else "blocked",
        "mode": "real" if settings.allows_real_execution else "safe",
        "checks": checks,
        "issues": issues,
    }


def _publisher_factory(settings: WorkerSettings):
    if not settings.allows_real_execution:
        return None
    return create_real_publisher_factory(
        settings.database_path,
        cookies_directory=settings.cookies_directory,
        enable_bilibili_runtime=settings.enable_bilibili_runtime,
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
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--once",
        action="store_true",
        help="只扫描并处理一批到期任务，然后退出",
    )
    mode.add_argument(
        "--check",
        action="store_true",
        help="只检查数据库、目录、账号与浏览器运行时，不访问发布平台",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = WorkerSettings.from_environment()
    if args.check:
        result = check_worker_readiness(settings)
        print(json.dumps(result, ensure_ascii=False, default=str))
        return 0 if result["status"] == "ready" else 1
    if args.once:
        result = run_worker_once(settings)
        print(json.dumps(result, ensure_ascii=False, default=str))
        return 0
    run_worker_forever(settings)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
