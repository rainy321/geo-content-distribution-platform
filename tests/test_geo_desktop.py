import os
import sqlite3
import socket
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock, patch

from geo_desktop import (
    APP_DIRECTORY_NAME,
    APP_VERSION,
    PUBLISHING_PROFILE_AUTHORIZED_REAL,
    PUBLISHING_PROFILE_SAFE,
    DesktopApi,
    DesktopPaths,
    SingleInstanceMutex,
    _create_startup_backup,
    _start_publish_scheduler,
    apply_desktop_environment,
    inspect_bilibili_runtime,
    inspect_publishing_runtime,
    load_build_info,
    publishing_profile_allows_real,
    reserve_loopback_socket,
    resolve_browser_executable,
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
                f"""{{
  "schema_version": 1,
  "version": "{APP_VERSION}",
  "source_commit": "abc123",
  "source_branch": "codex/test",
  "built_at_utc": "2026-09-09T00:00:00Z",
  "source_dirty": false
}}
""",
                encoding="utf-8",
            )

            result = load_build_info(directory)

            self.assertEqual(result["source_commit"], "abc123")
            self.assertFalse(result["source_dirty"])
            self.assertEqual(PUBLISHING_PROFILE_SAFE, result["publishing_profile"])

    def test_reads_authorized_real_build_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            metadata = Path(directory) / "desktop-build-info.json"
            metadata.write_text(
                f"""{{
  "schema_version": 1,
  "version": "{APP_VERSION}",
  "source_commit": "def456",
  "source_branch": "codex/windows-desktop-real-publish",
  "built_at_utc": "2026-09-09T01:00:00Z",
  "source_dirty": false,
  "publishing_profile": "authorized_real"
}}
""",
                encoding="utf-8",
            )

            result = load_build_info(directory)

            self.assertEqual(
                PUBLISHING_PROFILE_AUTHORIZED_REAL,
                result["publishing_profile"],
            )
            self.assertTrue(
                publishing_profile_allows_real(result["publishing_profile"])
            )

    def test_source_run_ignores_working_release_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            working_metadata = Path(directory) / ".build" / "desktop-build-info.json"
            working_metadata.parent.mkdir()
            working_metadata.write_text(
                f"""{{
  "schema_version": 1,
  "version": "{APP_VERSION}",
  "publishing_profile": "authorized_real"
}}
""",
                encoding="utf-8",
            )

            result = load_build_info(directory)

            self.assertEqual("development", result["source_commit"])
            self.assertEqual(PUBLISHING_PROFILE_SAFE, result["publishing_profile"])

    def test_rejects_unknown_publishing_profile_in_build_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            metadata = Path(directory) / "desktop-build-info.json"
            metadata.write_text(
                f"""{{
  "schema_version": 1,
  "version": "{APP_VERSION}",
  "publishing_profile": "unlocked_by_environment"
}}
""",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(RuntimeError, "发布配置"):
                load_build_info(directory)

    def test_real_publishing_profile_match_is_exact(self):
        self.assertTrue(
            publishing_profile_allows_real(PUBLISHING_PROFILE_AUTHORIZED_REAL)
        )
        self.assertFalse(publishing_profile_allows_real(PUBLISHING_PROFILE_SAFE))
        self.assertFalse(publishing_profile_allows_real("AUTHORIZED_REAL"))
        self.assertFalse(publishing_profile_allows_real("authorized_real_extra"))

    def test_resolves_explicit_chrome_executable(self):
        with tempfile.TemporaryDirectory() as directory:
            chrome = Path(directory) / "chrome.exe"
            chrome.write_bytes(b"synthetic-browser-placeholder")

            result = resolve_browser_executable({"LOCAL_CHROME_PATH": str(chrome)})

            self.assertEqual(chrome.resolve(), result)

    def test_invalid_explicit_browser_path_fails_closed(self):
        with patch("geo_desktop.shutil.which", return_value=r"C:\fallback\chrome.exe"):
            result = resolve_browser_executable(
                {"LOCAL_CHROME_PATH": r"C:\missing\chrome.exe"}
            )

        self.assertIsNone(result)

    def test_discovers_chrome_in_standard_program_files_layout(self):
        with tempfile.TemporaryDirectory() as directory:
            program_files = Path(directory)
            chrome = program_files / "Google" / "Chrome" / "Application" / "chrome.exe"
            chrome.parent.mkdir(parents=True)
            chrome.write_bytes(b"synthetic-browser-placeholder")

            with patch("geo_desktop.shutil.which", return_value=None):
                result = resolve_browser_executable(
                    {
                        "ProgramFiles": str(program_files),
                        "ProgramFiles(x86)": "",
                        "LOCALAPPDATA": "",
                    }
                )

            self.assertEqual(chrome.resolve(), result)

    def test_does_not_claim_edge_only_install_as_supported(self):
        with tempfile.TemporaryDirectory() as directory:
            program_files = Path(directory)
            edge = program_files / "Microsoft" / "Edge" / "Application" / "msedge.exe"
            edge.parent.mkdir(parents=True)
            edge.write_bytes(b"synthetic-browser-placeholder")

            with patch("geo_desktop.shutil.which", return_value=None):
                result = resolve_browser_executable(
                    {
                        "ProgramFiles": str(program_files),
                        "ProgramFiles(x86)": "",
                        "LOCALAPPDATA": "",
                    }
                )

            self.assertIsNone(result)

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
            self.assertEqual("false", environ["ALLOW_LEGACY_DIRECT_PUBLISHING"])
            self.assertEqual("false", environ["ENABLE_BILIBILI_RUNTIME"])
            self.assertEqual("false", environ["GEO_REAL_PUBLISHING_AUTHORIZED"])
            self.assertEqual("false", environ["GEO_PUBLISHING_RUNTIME_READY"])
            self.assertEqual(PUBLISHING_PROFILE_SAFE, environ["GEO_DESKTOP_PUBLISHING_PROFILE"])
            self.assertEqual("true", environ["LOCAL_CHROME_HEADLESS"])
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

    def test_authorized_profile_records_intent_but_keeps_gate_closed_until_probe(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            frontend = root / "frontend"
            frontend.mkdir()
            (frontend / "index.html").write_text("<html></html>", encoding="utf-8")
            browser = root / "chrome.exe"
            browser.write_bytes(b"synthetic-browser-placeholder")
            environ = {
                "ALLOW_REAL_PUBLISHING": "true",
                "ALLOW_LEGACY_DIRECT_PUBLISHING": "true",
                "ENABLE_BILIBILI_RUNTIME": "true",
            }

            with patch(
                "geo_desktop.resolve_browser_executable",
                return_value=browser.resolve(),
            ):
                apply_desktop_environment(
                    DesktopPaths.from_root(root / "runtime"),
                    frontend,
                    43211,
                    environ=environ,
                    publishing_profile=PUBLISHING_PROFILE_AUTHORIZED_REAL,
                )

            self.assertEqual("true", environ["GEO_REAL_PUBLISHING_AUTHORIZED"])
            self.assertEqual("false", environ["GEO_PUBLISHING_RUNTIME_READY"])
            self.assertEqual("false", environ["ALLOW_REAL_PUBLISHING"])
            self.assertEqual("false", environ["ALLOW_LEGACY_DIRECT_PUBLISHING"])
            self.assertEqual("false", environ["ENABLE_BILIBILI_RUNTIME"])
            self.assertEqual(
                PUBLISHING_PROFILE_AUTHORIZED_REAL,
                environ["GEO_DESKTOP_PUBLISHING_PROFILE"],
            )
            self.assertEqual(str(browser.resolve()), environ["LOCAL_CHROME_PATH"])
            self.assertEqual("true", environ["GEO_PUBLISHING_BROWSER_READY"])
            self.assertEqual("false", environ["LOCAL_CHROME_HEADLESS"])

    def test_rejects_unknown_environment_publishing_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "发布配置"):
                apply_desktop_environment(
                    DesktopPaths.from_root(directory),
                    directory,
                    43212,
                    environ={},
                    publishing_profile="untrusted",
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
    def test_safe_status_never_reports_real_publishing_enabled(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(
                os.environ,
                {
                    "ALLOW_REAL_PUBLISHING": "true",
                    "GEO_PUBLISHING_RUNTIME_READY": "true",
                },
                clear=False,
            ):
                status = DesktopApi(
                    DesktopPaths.from_root(directory),
                    build_info={"publishing_profile": PUBLISHING_PROFILE_SAFE},
                ).get_runtime_status()
            self.assertTrue(status["desktop"])
            self.assertFalse(status["real_publishing_authorized"])
            self.assertFalse(status["real_publishing_enabled"])
            self.assertFalse(status["scheduled_real_publishing_enabled"])

    def test_authorized_status_requires_both_runtime_gates(self):
        build_info = {
            "schema_version": 1,
            "version": APP_VERSION,
            "source_commit": "abc123",
            "source_branch": "codex/test",
            "built_at_utc": "2026-09-09T01:00:00Z",
            "source_dirty": False,
            "publishing_profile": PUBLISHING_PROFILE_AUTHORIZED_REAL,
        }
        runtime = {
            "ready": True,
            "enabled": True,
            "browser_ready": True,
            "browser_executable": r"C:\Program Files\Google\Chrome\chrome.exe",
            "missing_modules": [],
            "browser_probe": "not_run",
            "browser_probe_error": "",
        }
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(
                os.environ,
                {
                    "ALLOW_REAL_PUBLISHING": "true",
                    "GEO_PUBLISHING_RUNTIME_READY": "true",
                },
                clear=False,
            ), patch(
                "geo_desktop.inspect_publishing_runtime",
                return_value=dict(runtime),
            ):
                status = DesktopApi(
                    DesktopPaths.from_root(directory),
                    build_info=build_info,
                ).get_runtime_status()

            self.assertTrue(status["real_publishing_authorized"])
            self.assertTrue(status["real_publishing_enabled"])
            self.assertFalse(status["scheduled_real_publishing_enabled"])
            self.assertTrue(status["publishing_runtime"]["ready"])

    def test_authorized_status_stays_disabled_when_probe_gate_is_closed(self):
        build_info = {
            "publishing_profile": PUBLISHING_PROFILE_AUTHORIZED_REAL,
        }
        runtime = {
            "ready": True,
            "enabled": True,
            "browser_ready": True,
            "browser_executable": r"C:\Program Files\Google\Chrome\chrome.exe",
            "missing_modules": [],
            "browser_probe": "not_run",
            "browser_probe_error": "",
        }
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(
                os.environ,
                {
                    "ALLOW_REAL_PUBLISHING": "true",
                    "GEO_PUBLISHING_RUNTIME_READY": "false",
                },
                clear=False,
            ), patch(
                "geo_desktop.inspect_publishing_runtime",
                return_value=dict(runtime),
            ):
                status = DesktopApi(
                    DesktopPaths.from_root(directory),
                    build_info=build_info,
                ).get_runtime_status()

            self.assertTrue(status["real_publishing_authorized"])
            self.assertFalse(status["real_publishing_enabled"])
            self.assertFalse(status["publishing_runtime"]["ready"])

    def test_status_preserves_failed_startup_probe_diagnostic(self):
        build_info = {
            "publishing_profile": PUBLISHING_PROFILE_AUTHORIZED_REAL,
        }
        startup_probe = {
            "ready": False,
            "enabled": True,
            "browser_ready": True,
            "browser_executable": r"C:\Program Files\Google\Chrome\chrome.exe",
            "missing_modules": [],
            "module_errors": {},
            "browser_probe": "failed",
            "browser_probe_error": "synthetic probe failure",
            "reason": "浏览器自动化本地探针失败",
            "error": "synthetic probe failure",
        }
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {
                "ALLOW_REAL_PUBLISHING": "false",
                "GEO_PUBLISHING_RUNTIME_READY": "false",
            },
            clear=False,
        ), patch(
            "geo_desktop.inspect_publishing_runtime",
            side_effect=AssertionError("startup result should be reused"),
        ):
            status = DesktopApi(
                DesktopPaths.from_root(directory),
                build_info=build_info,
                publishing_runtime=startup_probe,
            ).get_runtime_status()

        self.assertFalse(status["publishing_runtime"]["ready"])
        self.assertEqual(
            status["publishing_runtime"]["reason"],
            "浏览器自动化本地探针失败",
        )
        self.assertEqual(
            status["publishing_runtime"]["error"],
            "synthetic probe failure",
        )

    def test_runtime_inspection_fails_closed_for_non_import_errors(self):
        with patch(
            "geo_desktop.resolve_browser_executable",
            return_value=Path(r"C:\synthetic\chrome.exe"),
        ), patch(
            "geo_desktop.importlib.import_module",
            side_effect=RuntimeError("synthetic initialization failure"),
        ):
            status = inspect_publishing_runtime(enabled=True)

        self.assertFalse(status["ready"])
        self.assertTrue(status["missing_modules"])
        self.assertEqual(
            status["module_errors"][status["missing_modules"][0]],
            "RuntimeError",
        )

    def test_bilibili_runtime_inspection_is_offline_and_fail_closed(self):
        expected = {
            "ready": True,
            "path": r"C:\fixed\biliup.exe",
            "version": "1.2.4",
            "sha256": "a" * 64,
            "bytes": 123,
            "packaged": False,
            "source": "external_compatibility_runtime",
            "reason": "ready",
        }
        with patch(
            "uploader.bilibili_uploader.runtime.inspect_desktop_biliup_runtime",
            return_value=dict(expected),
        ) as inspect_runtime:
            result = inspect_bilibili_runtime(enabled=True, probe=True)

        self.assertTrue(result["ready"])
        self.assertTrue(result["enabled"])
        self.assertFalse(result["packaged"])
        inspect_runtime.assert_called_once_with(probe=True)

    def test_desktop_scheduler_never_loads_or_enables_real_publishers(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = DesktopPaths.from_root(directory)
            scheduler = Mock(name="safe_scheduler")
            with patch(
                "services.publish_scheduler_runtime.create_publish_scheduler",
                return_value=scheduler,
            ) as create_scheduler:
                result = _start_publish_scheduler(paths)

        self.assertIs(result, scheduler)
        create_scheduler.assert_called_once_with(
            paths.database,
            interval_seconds=15,
            publisher_factory=None,
            allow_real=False,
            media_root=paths.media,
        )
        scheduler.start.assert_called_once_with()

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


class DesktopPackagingContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.project_root = Path(__file__).resolve().parents[1]

    def test_spec_collects_both_browser_drivers_only_for_authorized_profile(self):
        spec = (self.project_root / "desktop" / "GEO-Desktop.spec").read_text(
            encoding="utf-8"
        )

        self.assertIn('publishing_profile == "authorized_real"', spec)
        self.assertIn('for package_name in ("playwright", "patchright")', spec)
        self.assertIn("collect_all(", spec)
        self.assertIn('f"{package_name}\\\\driver\\\\node.exe"', spec)
        self.assertIn('f"{package_name}\\\\driver\\\\package\\\\cli.js"', spec)
        self.assertIn(
            'excludes += ["cv2", "numpy", "patchright", "playwright"]',
            spec,
        )

    def test_build_script_keeps_profiles_distinct_and_probes_without_browser_cache(self):
        build_script = (
            self.project_root / "scripts" / "build_windows_desktop.ps1"
        ).read_text(encoding="utf-8")

        self.assertTrue(build_script.startswith("#requires -Version 7.0"))
        self.assertIn('[ValidateSet("safe", "authorized_real")]', build_script)
        self.assertIn("uv sync --extra desktop --extra web --frozen", build_script)
        self.assertIn(
            "Pruning browser publishing dependencies before safe packaging",
            build_script,
        )
        self.assertIn("uv run --no-sync pyinstaller", build_script)
        self.assertIn("function Assert-NativeSuccess", build_script)
        self.assertIn('-not $_.StartsWith("?? output/")', build_script)
        self.assertIn("-not $_.StartsWith('?? \"output/')", build_script)
        self.assertIn('Operation "Running Python regression tests"', build_script)
        self.assertIn('Operation "Running frontend regression tests"', build_script)
        self.assertIn("tests.test_geo_article_compiler_service", build_script)
        self.assertIn("tests.test_geo_article_compiler_api", build_script)
        self.assertIn("tests.test_ai_service", build_script)
        self.assertIn("tests.test_content_generation_service", build_script)
        self.assertIn("tests.test_content_generation_api_v1", build_script)
        self.assertIn("tests.test_articles_api", build_script)
        self.assertIn("tests.test_articles_optimize_api", build_script)
        self.assertIn("tests.test_geo_score_service", build_script)
        self.assertIn("tests.test_geo_score_api", build_script)
        self.assertIn('Operation "Packaging the Windows executable"', build_script)
        self.assertIn("$env:PLAYWRIGHT_BROWSERS_PATH = $isolatedBrowserCache", build_script)
        self.assertLess(
            build_script.index("Pruning browser publishing dependencies"),
            build_script.index("uv run --no-sync pyinstaller"),
        )
        self.assertIn(
            "$report.publishing_runtime_probe.browser_probe -ne \"passed\"",
            build_script,
        )
        self.assertIn(
            "$report.health.scheduled_real_publishing_enabled -ne $false",
            build_script,
        )
        self.assertIn(
            "scheduled_real_publishing_enabled = [bool]$report.health.scheduled_real_publishing_enabled",
            build_script,
        )
        self.assertIn("internal_authorized_real_publish", build_script)
        self.assertIn("THIRD-PARTY-NOTICES.txt", build_script)
        self.assertIn(
            'ai_cost_ledger_version = "ai-cost-ledger-1.1.0"',
            build_script,
        )
        self.assertIn(
            'article_compiler_version = "geo-article-compiler-2.0.0"',
            build_script,
        )
        self.assertIn(
            'ai_citeability_version = "ai-citeability-rules-2.0.0"',
            build_script,
        )
        self.assertIn('docs\\GEO算法融合说明.md', build_script)

    def test_windowed_build_does_not_require_a_console_stream(self):
        logging_source = (self.project_root / "utils" / "log.py").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            'if sys.stdout is not None and hasattr(sys.stdout, "write"):',
            logging_source,
        )
        with tempfile.TemporaryDirectory() as directory:
            environment = dict(os.environ)
            environment["LOGS_DIRECTORY"] = str(Path(directory) / "logs")
            result = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    "import sys; sys.stdout = None; import utils.log",
                ],
                cwd=self.project_root,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(0, result.returncode, result.stderr)

if __name__ == "__main__":
    unittest.main()
