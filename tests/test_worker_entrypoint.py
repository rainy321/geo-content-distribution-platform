import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sau_worker import (
    WorkerSettings,
    check_worker_readiness,
    main,
    run_worker_once,
)


class WorkerEntrypointTests(unittest.TestCase):
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
                },
                clear=True,
            ):
                settings = WorkerSettings.from_environment()

        self.assertEqual(settings.interval_seconds, 5)
        self.assertTrue(settings.demo_mode)
        self.assertFalse(settings.allows_real_execution)

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
            )

            result = check_worker_readiness(settings)

        self.assertEqual(result["status"], "blocked")
        self.assertIn("browser_runtime:unavailable", result["issues"])
        self.assertIn("accounts:none_connected", result["issues"])

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
