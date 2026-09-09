import os
import sqlite3
import socket
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock

from geo_desktop import (
    APP_DIRECTORY_NAME,
    DesktopApi,
    DesktopPaths,
    SingleInstanceMutex,
    _create_startup_backup,
    apply_desktop_environment,
    load_build_info,
    reserve_loopback_socket,
    resolve_data_root,
    resolve_frontend_dir,
)


class DesktopPathsTests(unittest.TestCase):
    def test_uses_explicit_data_directory_before_local_app_data(self):
        with tempfile.TemporaryDirectory() as explicit:
            result = resolve_data_root(
                {
                    "GEO_DESKTOP_DATA_DIR": explicit,
                    "LOCALAPPDATA": r"C:\ignored",
                }
            )
            self.assertEqual(Path(explicit).resolve(), result)

    def test_uses_local_app_data_for_normal_windows_install(self):
        result = resolve_data_root({"LOCALAPPDATA": r"C:\Users\Demo\AppData\Local"})
        self.assertEqual(APP_DIRECTORY_NAME, result.name)

    def test_creates_isolated_runtime_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = DesktopPaths.from_root(directory)
            paths.create()
            self.assertTrue(paths.database.parent.is_dir())
            self.assertTrue(paths.media.is_dir())
            self.assertTrue(paths.cookies.is_dir())
            self.assertTrue(paths.auth_config.parent.is_dir())
            self.assertTrue(paths.backups.is_dir())
            self.assertTrue(paths.logs.is_dir())
            self.assertTrue(paths.webview.is_dir())


class DesktopEnvironmentTests(unittest.TestCase):
    def test_zero_byte_existing_database_stops_before_initialization(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = DesktopPaths.from_root(directory)
            paths.create()
            paths.database.touch()

            with self.assertRaisesRegex(RuntimeError, "0 字节"):
                _create_startup_backup(paths, Mock())

    def test_reads_and_validates_generated_build_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            metadata = Path(directory) / "desktop-build-info.json"
            metadata.write_text(
                """{
  "schema_version": 1,
  "version": "1.0.0",
  "source_commit": "abc123",
  "source_branch": "codex/test",
  "built_at_utc": "2026-09-09T00:00:00Z",
  "source_dirty": false
}
""",
                encoding="utf-8",
            )

            result = load_build_info(directory)

            self.assertEqual(result["source_commit"], "abc123")
            self.assertFalse(result["source_dirty"])

    def test_reserved_port_stays_owned_until_server_handoff(self):
        listener = reserve_loopback_socket()
        port = listener.getsockname()[1]
        competitor = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            with self.assertRaises(OSError):
                competitor.bind(("127.0.0.1", port))
        finally:
            competitor.close()
            listener.close()

    def test_forces_real_publishing_safety_flags_off(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            frontend = root / "frontend"
            frontend.mkdir()
            (frontend / "index.html").write_text("<html></html>", encoding="utf-8")
            environ = {
                "ALLOW_REAL_PUBLISHING": "true",
                "ENABLE_BILIBILI_RUNTIME": "true",
                "APP_ACCESS_PASSWORD": "must-not-leak-into-desktop",
            }

            apply_desktop_environment(
                DesktopPaths.from_root(root / "runtime"),
                frontend,
                43210,
                environ=environ,
            )

            self.assertEqual("false", environ["ALLOW_REAL_PUBLISHING"])
            self.assertEqual("false", environ["ENABLE_BILIBILI_RUNTIME"])
            self.assertEqual("false", environ["DEMO_MODE"])
            self.assertEqual("127.0.0.1", environ["SERVER_HOST"])
            self.assertEqual("43210", environ["SERVER_PORT"])
            self.assertEqual("true", environ["GEO_DESKTOP_MODE"])
            self.assertNotIn("APP_ACCESS_PASSWORD", environ)
            self.assertTrue(environ["APP_SESSION_SECRET"])
            self.assertEqual(
                str((root / "runtime" / "config" / "auth.json").resolve()),
                environ["GEO_DESKTOP_AUTH_PATH"],
            )

    def test_rejects_invalid_port(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                apply_desktop_environment(
                    DesktopPaths.from_root(directory),
                    directory,
                    0,
                    environ={},
                )

    def test_resolves_frozen_frontend_layout(self):
        with tempfile.TemporaryDirectory() as directory:
            frontend = Path(directory) / "frontend"
            frontend.mkdir()
            (frontend / "index.html").write_text("ready", encoding="utf-8")
            self.assertEqual(frontend, resolve_frontend_dir(directory))


class DesktopApiTests(unittest.TestCase):
    def test_status_never_reports_real_publishing_enabled(self):
        with tempfile.TemporaryDirectory() as directory:
            previous = os.environ.get("ALLOW_REAL_PUBLISHING")
            os.environ["ALLOW_REAL_PUBLISHING"] = "true"
            try:
                status = DesktopApi(DesktopPaths.from_root(directory)).get_runtime_status()
            finally:
                if previous is None:
                    os.environ.pop("ALLOW_REAL_PUBLISHING", None)
                else:
                    os.environ["ALLOW_REAL_PUBLISHING"] = previous
            self.assertTrue(status["desktop"])
            self.assertFalse(status["real_publishing_enabled"])

    def test_mutex_name_is_stable_and_scoped_to_data_root(self):
        first = SingleInstanceMutex(Path("one"))
        same = SingleInstanceMutex(Path("one"))
        other = SingleInstanceMutex(Path("two"))
        self.assertEqual(first.name, same.name)
        self.assertNotEqual(first.name, other.name)

    def test_native_api_creates_verified_database_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = DesktopPaths.from_root(directory)
            paths.create()
            with closing(sqlite3.connect(paths.database)) as connection:
                connection.execute("CREATE TABLE sample (value TEXT)")
                connection.execute("INSERT INTO sample VALUES ('local-only')")
                connection.commit()

            result = DesktopApi(paths).create_database_backup()

            self.assertTrue(result["ok"])
            backup = Path(result["backup"]["path"])
            self.assertTrue(backup.is_file())
            with closing(sqlite3.connect(backup)) as connection:
                value = connection.execute("SELECT value FROM sample").fetchone()[0]
            self.assertEqual(value, "local-only")


if __name__ == "__main__":
    unittest.main()
