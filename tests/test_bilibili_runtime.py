import hashlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from uploader.bilibili_uploader.runtime import (
    BILIUP_OUTPUT_TAIL_BYTES_PER_STREAM,
    TRUSTED_DESKTOP_BILIUP_BINARIES,
    _OwnedBiliupProcess,
    _WindowsKillJob,
    _register_biliup_process,
    _run_owned_biliup_executable,
    build_biliup_runtime_path,
    ensure_biliup_binary,
    inspect_desktop_biliup_runtime,
    run_biliup_command,
    spawn_biliup_process,
    terminate_all_biliup_processes,
)


class BiliupRuntimeTests(unittest.TestCase):
    def test_build_biliup_runtime_path_returns_platform_path(self):
        path = build_biliup_runtime_path("Windows")
        self.assertTrue(str(path).endswith("biliup.exe"))

    def test_desktop_probe_accepts_only_fixed_digest_size_and_version(self):
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / "biliup.exe"
            binary.write_bytes(b"synthetic-fixed-runtime")
            digest = hashlib.sha256(binary.read_bytes()).hexdigest()
            metadata = {
                "version": "9.8.7",
                "bytes": binary.stat().st_size,
                "source_archive": "synthetic.zip",
                "source_archive_sha256": "0" * 64,
            }
            completed = Mock(
                returncode=0,
                stdout="biliup-cli 9.8.7\n",
                stderr="",
            )
            with patch.dict(
                TRUSTED_DESKTOP_BILIUP_BINARIES,
                {digest: metadata},
                clear=True,
            ), patch(
                "uploader.bilibili_uploader.runtime._run_owned_biliup_executable",
                return_value=completed,
            ) as run:
                report = inspect_desktop_biliup_runtime(binary)

        self.assertTrue(report["ready"])
        self.assertEqual(report["sha256"], digest)
        self.assertEqual(report["version"], "9.8.7")
        run.assert_called_once()

    def test_desktop_probe_rejects_unknown_digest_without_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / "biliup.exe"
            binary.write_bytes(b"unreviewed-runtime")
            with patch(
                "uploader.bilibili_uploader.runtime._run_owned_biliup_executable"
            ) as run:
                report = inspect_desktop_biliup_runtime(binary)

        self.assertFalse(report["ready"])
        self.assertIn("哈希", report["reason"])
        run.assert_not_called()

    def test_desktop_ensure_never_downloads_and_pins_startup_digest(self):
        report = {
            "ready": True,
            "path": "C:/fixed/biliup.exe",
            "sha256": "a" * 64,
            "reason": "ready",
        }
        with patch.dict(
            os.environ,
            {
                "GEO_DESKTOP_MODE": "true",
                "GEO_BILIUP_BINARY": report["path"],
                "GEO_BILIUP_SHA256": report["sha256"],
            },
            clear=False,
        ), patch(
            "uploader.bilibili_uploader.runtime.inspect_desktop_biliup_runtime",
            return_value=report,
        ), patch(
            "uploader.bilibili_uploader.runtime.fetch_latest_release"
        ) as fetch:
            result = ensure_biliup_binary(force_check=True)

        self.assertEqual(result, Path(report["path"]))
        fetch.assert_not_called()

    @patch("uploader.bilibili_uploader.runtime.fetch_latest_release")
    def test_ensure_biliup_binary_downloads_when_missing(self, mock_release):
        mock_release.return_value = {
            "tag_name": "v1.0.0",
            "asset_url": "https://example.invalid/biliup.exe",
            "asset_name": "biliup.exe",
        }
        with patch("uploader.bilibili_uploader.runtime.read_local_biliup_version", return_value=None):
            with patch("pathlib.Path.exists", return_value=False):
                with patch("uploader.bilibili_uploader.runtime.download_biliup_asset") as mock_download:
                    with patch("uploader.bilibili_uploader.runtime.write_local_biliup_version") as mock_write_version:
                        ensure_biliup_binary(force_check=True)
        mock_download.assert_called_once()
        mock_write_version.assert_called_once_with("v1.0.0")

    @patch("uploader.bilibili_uploader.runtime.fetch_latest_release")
    def test_ensure_biliup_binary_reuses_local_when_up_to_date(self, mock_release):
        mock_release.return_value = {
            "tag_name": "v1.0.0",
            "asset_url": "https://example.invalid/biliup.exe",
            "asset_name": "biliup.exe",
        }
        with patch("uploader.bilibili_uploader.runtime.read_local_biliup_version", return_value="v1.0.0"):
            with patch("pathlib.Path.exists", return_value=True):
                with patch("uploader.bilibili_uploader.runtime.download_biliup_asset") as mock_download:
                    with patch("uploader.bilibili_uploader.runtime.write_local_biliup_version") as mock_write_version:
                        ensure_biliup_binary(force_check=True)
        mock_download.assert_not_called()
        mock_write_version.assert_not_called()

    @patch("uploader.bilibili_uploader.runtime._run_owned_biliup_executable")
    @patch("uploader.bilibili_uploader.runtime.ensure_biliup_binary")
    def test_run_biliup_command_returns_completed_process(self, mock_ensure_binary, mock_run):
        mock_ensure_binary.return_value = build_biliup_runtime_path("Windows")
        mock_run.return_value = Mock(returncode=0, stdout="ok", stderr="")
        result = run_biliup_command(["login"])
        self.assertEqual(result.returncode, 0)
        mock_run.assert_called_once_with(
            mock_ensure_binary.return_value,
            ["login"],
            interactive=False,
            timeout_seconds=None,
        )

    @patch("uploader.bilibili_uploader.runtime._run_owned_biliup_executable")
    @patch("uploader.bilibili_uploader.runtime.ensure_biliup_binary")
    def test_run_biliup_command_login_uses_interactive_stdio(self, mock_ensure_binary, mock_run):
        mock_ensure_binary.return_value = Path("C:/mock/biliup.exe")
        mock_run.return_value = Mock(returncode=0, stdout="", stderr="")
        run_biliup_command(["login"], interactive=True)
        mock_run.assert_called_once_with(
            mock_ensure_binary.return_value,
            ["login"],
            interactive=True,
            timeout_seconds=None,
        )

    @patch("uploader.bilibili_uploader.runtime._run_owned_biliup_executable")
    @patch("uploader.bilibili_uploader.runtime.ensure_biliup_binary")
    def test_run_biliup_command_applies_owned_timeout(self, mock_ensure_binary, mock_run):
        mock_ensure_binary.return_value = Path("C:/mock/biliup.exe")
        mock_run.return_value = Mock(returncode=0, stdout="ok", stderr="")

        run_biliup_command(["renew"], timeout_seconds=37)

        mock_run.assert_called_once_with(
            mock_ensure_binary.return_value,
            ["renew"],
            interactive=False,
            timeout_seconds=37,
        )

    def test_windows_job_object_is_configured_assigned_and_closed(self):
        kernel32 = Mock()
        kernel32.CreateJobObjectW.return_value = 1234
        kernel32.SetInformationJobObject.return_value = 1
        kernel32.AssignProcessToJobObject.return_value = 1
        process = Mock(_handle=5678)

        with patch("ctypes.WinDLL", return_value=kernel32):
            job = _WindowsKillJob.create()
        job.assign(process)
        job.close()

        kernel32.CreateJobObjectW.assert_called_once_with(None, None)
        kernel32.SetInformationJobObject.assert_called_once()
        limit_pointer = kernel32.SetInformationJobObject.call_args.args[2]
        self.assertEqual(
            limit_pointer._obj.BasicLimitInformation.LimitFlags,
            _WindowsKillJob.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
        )
        kernel32.AssignProcessToJobObject.assert_called_once()
        kernel32.CloseHandle.assert_called_once_with(1234)

    @patch("uploader.bilibili_uploader.runtime._create_windows_kill_job")
    @patch("uploader.bilibili_uploader.runtime.subprocess.Popen")
    def test_spawn_registers_popen_in_windows_kill_job(self, popen, create_job):
        process = Mock(pid=31415)
        popen.return_value = process
        job = Mock()
        create_job.return_value = job

        result = spawn_biliup_process(
            ["renew"],
            binary_path=Path("C:/fixed/biliup.exe"),
        )
        try:
            self.assertIs(result, process)
            create_job.assert_called_once_with(process)
        finally:
            terminate_all_biliup_processes()
        job.terminate.assert_called_once_with()
        job.close.assert_called_once_with()

    @patch("uploader.bilibili_uploader.runtime.subprocess.run")
    def test_windows_fallback_targets_only_owned_root_process_tree(self, run):
        process = Mock(pid=4242)
        process.poll.side_effect = [None, None, 0]
        process.wait.return_value = 1
        _register_biliup_process(_OwnedBiliupProcess(process=process))

        terminate_all_biliup_processes()

        self.assertEqual(
            run.call_args.args[0],
            ["taskkill", "/PID", "4242", "/T", "/F"],
        )
        process.terminate.assert_called_once_with()

    @patch("uploader.bilibili_uploader.runtime._create_windows_kill_job")
    @patch("uploader.bilibili_uploader.runtime.subprocess.Popen")
    def test_normal_completion_closes_job_and_culls_stray_descendants(
        self, popen, create_job
    ):
        process = Mock(pid=16180, returncode=0)
        process.stdout = io.BytesIO(b"ok")
        process.stderr = io.BytesIO(b"")
        process.wait.return_value = 0
        popen.return_value = process
        job = Mock()
        create_job.return_value = job

        result = _run_owned_biliup_executable(
            "C:/fixed/biliup.exe",
            ["renew"],
            timeout_seconds=10,
        )

        self.assertEqual(result.returncode, 0)
        job.terminate.assert_not_called()
        job.close.assert_called_once_with()

    def test_owned_timeout_terminates_registered_process_tree(self):
        process = Mock(pid=27182, returncode=None)
        process.stdout = io.BytesIO(b"")
        process.stderr = io.BytesIO(b"")
        process.wait.side_effect = [
            subprocess.TimeoutExpired(["biliup.exe", "upload"], 3),
            1,
        ]
        job = Mock()
        _register_biliup_process(
            _OwnedBiliupProcess(process=process, windows_job=job)
        )

        with patch(
            "uploader.bilibili_uploader.runtime.spawn_biliup_process",
            return_value=process,
        ):
            with self.assertRaises(subprocess.TimeoutExpired):
                _run_owned_biliup_executable(
                    "C:/fixed/biliup.exe",
                    ["upload"],
                    timeout_seconds=3,
                )

        job.terminate.assert_called_once_with()
        job.close.assert_called_once_with()

    def test_noninteractive_long_output_is_drained_but_only_tail_is_retained(self):
        prefix_secret = "COOKIE_SHOULD_HAVE_BEEN_DROPPED"
        script = (
            "import sys; "
            f"sys.stdout.write('{prefix_secret}' + 'x' * 100000 + ' BV1xJt66fEyq'); "
            "sys.stderr.write('y' * 100000 + ' stderr-tail')"
        )

        result = _run_owned_biliup_executable(
            sys.executable,
            ["-c", script],
            timeout_seconds=15,
        )

        self.assertEqual(result.returncode, 0)
        self.assertLessEqual(
            len(result.stdout.encode("utf-8")),
            BILIUP_OUTPUT_TAIL_BYTES_PER_STREAM,
        )
        self.assertLessEqual(
            len(result.stderr.encode("utf-8")),
            BILIUP_OUTPUT_TAIL_BYTES_PER_STREAM,
        )
        self.assertNotIn(prefix_secret, result.stdout)
        self.assertIn("BV1xJt66fEyq", result.stdout)
        self.assertTrue(result.stderr.endswith("stderr-tail"))
