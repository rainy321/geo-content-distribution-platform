import sqlite3
import tempfile
import unittest
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from db.createTable import initialize_database
from sau_worker import (
    WorkerSettings,
    check_worker_readiness,
    main,
    run_worker_once,
)
from services.publish_job_service import create_publish_job, get_publish_job


class WorkerEntrypointTests(unittest.TestCase):
    def test_real_execution_requires_every_worker_safety_gate(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = WorkerSettings(
                database_path=root / "database.db",
                cookies_directory=root / "cookies",
                media_root=root / "media",
                interval_seconds=15,
                demo_mode=False,
                allow_real_publishing=True,
                scheduled_real_publishing_enabled=True,
                real_publishing_authorized=True,
                publishing_runtime_ready=True,
            )

        self.assertTrue(settings.allows_real_execution)
        for field in (
            "allow_real_publishing",
            "scheduled_real_publishing_enabled",
            "real_publishing_authorized",
            "publishing_runtime_ready",
        ):
            with self.subTest(field=field):
                self.assertFalse(
                    replace(settings, **{field: False}).allows_real_execution
                )
        self.assertFalse(replace(settings, demo_mode=True).allows_real_execution)

    def test_environment_settings_keep_demo_and_real_execution_separate(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with patch.dict(
                "os.environ",
                {
                    "DATABASE_PATH": str(root / "data" / "database.db"),
                    "COOKIES_DIRECTORY": str(root / "cookies"),
                    "MEDIA_ROOT": str(root / "media"),
                    "PUBLISH_SCHEDULER_INTERVAL_SECONDS": "2",
                    "DEMO_MODE": "true",
                    "ALLOW_REAL_PUBLISHING": "true",
                    "SCHEDULED_REAL_PUBLISHING_ENABLED": "true",
                    "GEO_REAL_PUBLISHING_AUTHORIZED": "true",
                    "GEO_PUBLISHING_RUNTIME_READY": "true",
                    "ENABLE_BILIBILI_RUNTIME": "true",
                },
                clear=True,
            ):
                settings = WorkerSettings.from_environment()

        self.assertEqual(settings.interval_seconds, 5)
        self.assertTrue(settings.demo_mode)
        self.assertFalse(settings.allows_real_execution)
        self.assertTrue(settings.scheduled_real_publishing_enabled)
        self.assertTrue(settings.real_publishing_authorized)
        self.assertTrue(settings.publishing_runtime_ready)
        self.assertTrue(settings.enable_bilibili_runtime)

    @patch("sau_worker.run_publish_scheduler_tick")
    @patch("sau_worker.create_real_publisher_factory")
    def test_once_reuses_scheduler_tick_without_real_factory_in_demo(
        self,
        mock_factory,
        mock_tick,
    ):
        mock_tick.return_value = {"promoted_count": 0, "jobs": []}
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = WorkerSettings(
                database_path=root / "data" / "database.db",
                cookies_directory=root / "cookies",
                media_root=root / "media",
                interval_seconds=15,
                demo_mode=True,
                allow_real_publishing=False,
            )

            result = run_worker_once(settings)

            self.assertTrue(settings.database_path.is_file())
            self.assertTrue(settings.cookies_directory.is_dir())
            self.assertTrue(settings.media_root.is_dir())

        mock_factory.assert_not_called()
        mock_tick.assert_called_once_with(
            settings.database_path,
            publisher_factory=None,
            allow_real=False,
            media_root=settings.media_root,
        )
        self.assertEqual(result["promoted_count"], 0)

    def test_check_reports_safe_worker_without_browser_requirement(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = WorkerSettings(
                database_path=root / "data" / "database.db",
                cookies_directory=root / "cookies",
                media_root=root / "media",
                interval_seconds=15,
                demo_mode=True,
                allow_real_publishing=False,
            )

            result = check_worker_readiness(settings)

        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["mode"], "safe")
        self.assertEqual(result["checks"]["database"], "ok")
        self.assertEqual(
            result["checks"]["browser_runtime"],
            "not_required",
        )

    @patch("sau_worker._browser_runtime_status", return_value="unavailable")
    def test_check_blocks_real_worker_when_browser_is_missing(
        self,
        _mock_browser_status,
    ):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = WorkerSettings(
                database_path=root / "data" / "database.db",
                cookies_directory=root / "cookies",
                media_root=root / "media",
                interval_seconds=15,
                demo_mode=False,
                allow_real_publishing=True,
                scheduled_real_publishing_enabled=True,
                real_publishing_authorized=True,
                publishing_runtime_ready=True,
            )

            result = check_worker_readiness(settings)

        self.assertEqual(result["status"], "blocked")
        self.assertIn("browser_runtime:unavailable", result["issues"])
        self.assertIn("accounts:none_connected", result["issues"])

    @patch("sau_worker.create_real_publisher_factory")
    def test_old_real_auto_task_needs_action_without_loading_publisher(
        self,
        mock_factory,
    ):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = WorkerSettings(
                database_path=root / "data" / "database.db",
                cookies_directory=root / "cookies",
                media_root=root / "media",
                interval_seconds=15,
                demo_mode=False,
                allow_real_publishing=True,
                scheduled_real_publishing_enabled=False,
                real_publishing_authorized=True,
                publishing_runtime_ready=True,
            )
            initialize_database(settings.database_path)
            with closing(sqlite3.connect(settings.database_path)) as conn:
                with conn:
                    project_id = conn.execute(
                        "INSERT INTO projects (name) VALUES (?)",
                        ("旧自动任务回归",),
                    ).lastrowid
                    article_id = conn.execute(
                        """
                        INSERT INTO articles (project_id, title, content, status)
                        VALUES (?, ?, ?, ?)
                        """,
                        (project_id, "旧任务", "不得自动发布", "ready"),
                    ).lastrowid
            old_job = create_publish_job(
                settings.database_path,
                article_id=article_id,
                platform="zhihu",
                publish_at=datetime.now(timezone.utc) - timedelta(minutes=1),
                auto_execute=True,
            )

            result = run_worker_once(settings)
            latest = get_publish_job(settings.database_path, old_job["id"])

        self.assertFalse(settings.allows_real_execution)
        mock_factory.assert_not_called()
        self.assertEqual(result["attempted_real_count"], 0)
        self.assertEqual(result["blocked_real_count"], 1)
        self.assertEqual(latest["status"], "need_action")
        self.assertIn("未访问平台", latest["message"])

    @patch("sau_worker.WorkerSettings.from_environment")
    @patch("sau_worker.check_worker_readiness")
    def test_check_cli_returns_nonzero_when_blocked(
        self,
        mock_check,
        mock_settings,
    ):
        mock_settings.return_value = object()
        mock_check.return_value = {
            "status": "blocked",
            "mode": "real",
            "checks": {},
            "issues": ["browser_runtime:unavailable"],
        }

        with patch("builtins.print") as mock_print:
            exit_code = main(["--check"])

        self.assertEqual(exit_code, 1)
        mock_print.assert_called_once()


if __name__ == "__main__":
    unittest.main()
