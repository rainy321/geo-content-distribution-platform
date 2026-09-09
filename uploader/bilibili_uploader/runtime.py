from __future__ import annotations

import hashlib
import os
import platform
import shutil
import signal
import stat
import subprocess
import tarfile
import tempfile
import threading
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests


GITHUB_RELEASE_API = "https://api.github.com/repos/biliup/biliup/releases/latest"
DESKTOP_BILIUP_BINARY_ENV = "GEO_BILIUP_BINARY"
DESKTOP_BILIUP_SHA256_ENV = "GEO_BILIUP_SHA256"

# Desktop builds never execute an arbitrary or freshly downloaded binary.  A
# compatibility runtime is accepted only after its extracted executable digest,
# byte length and version probe all match this review list.  The binary remains
# outside the GEO distribution so its upstream rights and update cadence stay
# explicit rather than being silently folded into our one-file EXE.
TRUSTED_DESKTOP_BILIUP_BINARIES = {
    "5500912978355e7a64dbbe86ebd7ade2cd4ec8bbc91e16998e4aea7359d0fcdd": {
        "version": "1.2.4",
        "bytes": 33_895_936,
        "source_archive": "biliupR-v1.2.4-x86_64-windows.zip",
        "source_archive_sha256": (
            "cb5af47aeaffd63719c94fa354a4d1404dd8437b6cc215513ec4e6054177c93e"
        ),
    },
}

# Non-interactive commands can be noisy (upload progress in particular).  Pipes
# must still be drained continuously to prevent a child-process deadlock, but
# retaining the complete stream would let an unbounded CLI log consume desktop
# memory.  Keep only a small tail from each stream: it is enough to recognize a
# final BV id while bounding a completed command to at most 128 KiB of output.
BILIUP_OUTPUT_TAIL_BYTES_PER_STREAM = 64 * 1024
BILIUP_OUTPUT_READ_CHUNK_BYTES = 8 * 1024


# Every biliup process launched by the desktop is registered here.  On Windows
# each process is assigned to its own Job Object with KILL_ON_JOB_CLOSE, so an
# ordinary window close and an abrupt host-process exit both tear down the
# complete helper process tree.  The registry is also drained explicitly by
# ``geo_desktop.run`` so tests and controlled shutdowns do not depend on OS
# handle cleanup timing.
_ACTIVE_PROCESS_LOCK = threading.RLock()
_ACTIVE_PROCESSES: dict[int, "_OwnedBiliupProcess"] = {}


class _WindowsKillJob:
    """Small owner for a Windows Job Object configured to kill on close."""

    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
    JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS = 9

    def __init__(self, kernel32: Any, handle: Any) -> None:
        self._kernel32 = kernel32
        self._handle = handle
        self._closed = False

    @classmethod
    def create(cls) -> "_WindowsKillJob":
        import ctypes
        from ctypes import wintypes

        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_ulonglong),
                ("WriteOperationCount", ctypes.c_ulonglong),
                ("OtherOperationCount", ctypes.c_ulonglong),
                ("ReadTransferCount", ctypes.c_ulonglong),
                ("WriteTransferCount", ctypes.c_ulonglong),
                ("OtherTransferCount", ctypes.c_ulonglong),
            ]

        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            raise OSError(ctypes.get_last_error(), "CreateJobObjectW failed")

        job = cls(kernel32, handle)
        limits = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        limits.BasicLimitInformation.LimitFlags = (
            cls.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        )
        configured = kernel32.SetInformationJobObject(
            handle,
            cls.JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS,
            ctypes.byref(limits),
            ctypes.sizeof(limits),
        )
        if not configured:
            error_code = ctypes.get_last_error()
            job.close()
            raise OSError(error_code, "SetInformationJobObject failed")
        return job

    def assign(self, process: subprocess.Popen[Any]) -> None:
        import ctypes

        process_handle = getattr(process, "_handle", None)
        if process_handle is None:
            raise OSError("Popen did not expose a Windows process handle")
        assigned = self._kernel32.AssignProcessToJobObject(
            self._handle,
            ctypes.c_void_p(int(process_handle)),
        )
        if not assigned:
            raise OSError(ctypes.get_last_error(), "AssignProcessToJobObject failed")

    def terminate(self, exit_code: int = 1) -> None:
        if self._closed:
            return
        self._kernel32.TerminateJobObject(self._handle, int(exit_code))

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._kernel32.CloseHandle(self._handle)


@dataclass
class _OwnedBiliupProcess:
    process: subprocess.Popen[Any]
    windows_job: _WindowsKillJob | None = None
    posix_process_group: int | None = None


class _BoundedOutputTail:
    """Thread-safe byte tail used while a pipe is continuously drained."""

    def __init__(self, limit: int = BILIUP_OUTPUT_TAIL_BYTES_PER_STREAM) -> None:
        if limit <= 0:
            raise ValueError("output tail limit must be positive")
        self.limit = int(limit)
        self._buffer = bytearray()
        self._lock = threading.Lock()

    def append(self, chunk: bytes) -> None:
        if not chunk:
            return
        with self._lock:
            if len(chunk) >= self.limit:
                self._buffer[:] = chunk[-self.limit :]
                return
            overflow = len(self._buffer) + len(chunk) - self.limit
            if overflow > 0:
                del self._buffer[:overflow]
            self._buffer.extend(chunk)

    def decode(self) -> str:
        with self._lock:
            payload = bytes(self._buffer)
        return payload.decode("utf-8", errors="replace")


def _drain_output_pipe(pipe: Any, tail: _BoundedOutputTail) -> None:
    """Drain a binary subprocess pipe until EOF without retaining full output."""

    try:
        while True:
            chunk = pipe.read(BILIUP_OUTPUT_READ_CHUNK_BYTES)
            if not chunk:
                return
            if isinstance(chunk, str):
                chunk = chunk.encode("utf-8", errors="replace")
            tail.append(bytes(chunk))
    except (BrokenPipeError, OSError, ValueError):
        # Termination/desktop shutdown may close the pipe while it is being read.
        return


def _start_output_drains(
    process: subprocess.Popen[Any],
) -> tuple[_BoundedOutputTail, _BoundedOutputTail, list[threading.Thread]]:
    stdout_tail = _BoundedOutputTail()
    stderr_tail = _BoundedOutputTail()
    threads: list[threading.Thread] = []
    for name, pipe, tail in (
        ("stdout", process.stdout, stdout_tail),
        ("stderr", process.stderr, stderr_tail),
    ):
        if pipe is None:
            continue
        thread = threading.Thread(
            target=_drain_output_pipe,
            args=(pipe, tail),
            name=f"biliup-{name}-drain",
            daemon=True,
        )
        thread.start()
        threads.append(thread)
    return stdout_tail, stderr_tail, threads


def _finish_output_drains(
    process: subprocess.Popen[Any], threads: list[threading.Thread]
) -> None:
    """Give EOF readers time to finish, then close pipes to bound shutdown."""

    deadline = time.monotonic() + 2.0
    for thread in threads:
        thread.join(max(0.0, deadline - time.monotonic()))
    # Closing the handles makes a lingering reader return without leaving a
    # daemon thread attached to the completed invocation.
    for pipe in (process.stdout, process.stderr):
        try:
            if pipe is not None:
                pipe.close()
        except Exception:
            pass
    if any(thread.is_alive() for thread in threads):
        for thread in threads:
            thread.join(0.2)


def _register_biliup_process(record: _OwnedBiliupProcess) -> None:
    with _ACTIVE_PROCESS_LOCK:
        _ACTIVE_PROCESSES[id(record.process)] = record


def _pop_biliup_process(process: subprocess.Popen[Any]) -> _OwnedBiliupProcess | None:
    with _ACTIVE_PROCESS_LOCK:
        return _ACTIVE_PROCESSES.pop(id(process), None)


def _create_windows_kill_job(
    process: subprocess.Popen[Any],
) -> _WindowsKillJob | None:
    """Attach ``process`` to KILL_ON_JOB_CLOSE, or fail into exact-PID cleanup."""

    job: _WindowsKillJob | None = None
    try:
        job = _WindowsKillJob.create()
        job.assign(process)
        return job
    except Exception:
        try:
            if job is not None:
                job.close()
        except Exception:
            pass
        return None


def spawn_biliup_process(
    arguments: list[str],
    *,
    binary_path: str | Path | None = None,
    **popen_options: Any,
) -> subprocess.Popen[Any]:
    """Start one tracked biliup process without performing platform actions itself."""

    resolved_binary = (
        Path(binary_path)
        if binary_path is not None
        else ensure_biliup_binary(force_check=False)
    )
    command = [str(resolved_binary), *arguments]
    options = dict(popen_options)
    if os.name != "nt":
        # A private POSIX process group is the closest equivalent fallback and
        # lets cancellation terminate descendants without process-name scans.
        options.setdefault("start_new_session", True)
    process = subprocess.Popen(command, **options)
    windows_job = _create_windows_kill_job(process) if os.name == "nt" else None
    process_group = process.pid if os.name != "nt" else None
    _register_biliup_process(
        _OwnedBiliupProcess(
            process=process,
            windows_job=windows_job,
            posix_process_group=process_group,
        )
    )
    return process


def _terminate_process_fallback(record: _OwnedBiliupProcess) -> None:
    process = record.process
    if os.name == "nt":
        # ``taskkill /T`` is used only if Job Object assignment was unavailable.
        # It targets the still-owned root PID, never a broad executable name.
        if process.poll() is None:
            options: dict[str, Any] = {
                "check": False,
                "stdout": subprocess.DEVNULL,
                "stderr": subprocess.DEVNULL,
                "timeout": 10,
            }
            creation_flag = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            if creation_flag:
                options["creationflags"] = creation_flag
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    **options,
                )
            except Exception:
                pass
    elif record.posix_process_group:
        try:
            os.killpg(record.posix_process_group, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            pass

    try:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=2)
    except Exception:
        try:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=2)
        except Exception:
            pass


def terminate_biliup_process(process: subprocess.Popen[Any] | None) -> None:
    """Terminate exactly one tracked biliup process tree and release ownership."""

    if process is None:
        return
    record = _pop_biliup_process(process)
    if record is None:
        # The caller may be racing a global desktop shutdown.  Never fall back
        # to a name-based kill when ownership has already been released.
        return
    try:
        if record.windows_job is not None:
            record.windows_job.terminate()
        else:
            _terminate_process_fallback(record)
        try:
            process.wait(timeout=2)
        except Exception:
            _terminate_process_fallback(record)
    finally:
        if record.windows_job is not None:
            record.windows_job.close()


def release_biliup_process(process: subprocess.Popen[Any] | None) -> None:
    """Release a completed root; closing its Job also kills stray descendants."""

    if process is None:
        return
    record = _pop_biliup_process(process)
    if record is not None and record.windows_job is not None:
        record.windows_job.close()


def terminate_all_biliup_processes() -> int:
    """Drain all active helpers during desktop shutdown; return owned count."""

    with _ACTIVE_PROCESS_LOCK:
        processes = [record.process for record in _ACTIVE_PROCESSES.values()]
    for process in processes:
        terminate_biliup_process(process)
    return len(processes)


def _run_owned_biliup_executable(
    binary_path: str | Path,
    arguments: list[str],
    *,
    interactive: bool = False,
    timeout_seconds: float | None = None,
) -> subprocess.CompletedProcess[str]:
    """``subprocess.run`` semantics backed by a contained, tracked process."""

    popen_options: dict[str, Any] = {}
    if not interactive:
        popen_options.update(
            {
                "stdout": subprocess.PIPE,
                "stderr": subprocess.PIPE,
            }
        )
        if os.name == "nt":
            popen_options["creationflags"] = getattr(
                subprocess, "CREATE_NO_WINDOW", 0
            )

    process = spawn_biliup_process(
        arguments,
        binary_path=binary_path,
        **popen_options,
    )
    command = [str(binary_path), *arguments]
    stdout_tail: _BoundedOutputTail | None = None
    stderr_tail: _BoundedOutputTail | None = None
    drain_threads: list[threading.Thread] = []
    if not interactive:
        stdout_tail, stderr_tail, drain_threads = _start_output_drains(process)
    try:
        returncode = process.wait(timeout=timeout_seconds)
        # Closing the Windows Job Object after the owned root exits also closes
        # inherited pipe handles held by any stray descendants.
        release_biliup_process(process)
        _finish_output_drains(process, drain_threads)
        return subprocess.CompletedProcess(
            command,
            int(returncode),
            stdout_tail.decode() if stdout_tail is not None else None,
            stderr_tail.decode() if stderr_tail is not None else None,
        )
    except BaseException:
        # Includes timeout, cancellation and host-thread interruption.  The Job
        # Object path kills descendants before the original exception escapes.
        terminate_biliup_process(process)
        _finish_output_drains(process, drain_threads)
        raise
    finally:
        release_biliup_process(process)


def _environment_flag(name: str) -> bool:
    return str(os.getenv(name, "")).strip().lower() in {"1", "true", "yes", "on"}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_obj:
        for chunk in iter(lambda: file_obj.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inspect_desktop_biliup_runtime(
    binary_path: str | Path | None = None,
    *,
    probe: bool = True,
) -> dict:
    """Return a secret-free readiness report for the external Bilibili helper.

    This function performs no download and no platform request.  The optional
    probe only invokes ``biliup --version`` against the local executable.
    """

    candidate = Path(binary_path).expanduser() if binary_path else build_biliup_runtime_path()
    try:
        candidate = candidate.resolve()
    except OSError:
        candidate = candidate.absolute()
    report = {
        "ready": False,
        "path": str(candidate),
        "version": "",
        "sha256": "",
        "bytes": 0,
        "packaged": False,
        "source": "external_compatibility_runtime",
        "reason": "",
    }
    if not candidate.is_file():
        report["reason"] = "未找到本机 Bilibili 兼容运行时"
        return report

    try:
        report["bytes"] = int(candidate.stat().st_size)
        report["sha256"] = _sha256_file(candidate)
    except OSError:
        report["reason"] = "无法读取本机 Bilibili 兼容运行时"
        return report

    metadata = TRUSTED_DESKTOP_BILIUP_BINARIES.get(report["sha256"])
    if metadata is None:
        report["reason"] = "Bilibili 兼容运行时哈希未通过当前版本校验"
        return report
    if report["bytes"] != metadata["bytes"]:
        report["reason"] = "Bilibili 兼容运行时文件大小不匹配"
        return report

    report["version"] = str(metadata["version"])
    report["source_archive"] = str(metadata["source_archive"])
    report["source_archive_sha256"] = str(metadata["source_archive_sha256"])
    if not probe:
        report["ready"] = True
        report["reason"] = "固定版本和 SHA-256 已通过校验"
        return report

    try:
        completed = _run_owned_biliup_executable(
            candidate,
            ["--version"],
            timeout_seconds=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        report["reason"] = "Bilibili 兼容运行时版本探针失败"
        return report
    version_output = f"{completed.stdout or ''}\n{completed.stderr or ''}"
    if completed.returncode != 0 or str(metadata["version"]) not in version_output:
        report["reason"] = "Bilibili 兼容运行时版本与固定清单不匹配"
        return report

    report["ready"] = True
    report["reason"] = "固定版本、SHA-256 与离线版本探针均已通过"
    return report


def get_biliup_runtime_root() -> Path:
    return Path.home() / ".social-auto-upload" / "tools" / "biliup"


def _normalize_system(system_name: str | None = None) -> str:
    system_value = (system_name or platform.system()).strip().lower()
    if system_value == "darwin":
        return "macos"
    return system_value


def _normalize_machine(machine_name: str | None = None) -> str:
    machine_value = (machine_name or platform.machine()).strip().lower()
    aliases = {
        "amd64": "x86_64",
        "x64": "x86_64",
        "arm64": "aarch64",
    }
    return aliases.get(machine_value, machine_value)


def _build_platform_key(system_name: str | None = None, machine_name: str | None = None) -> str:
    return f"{_normalize_system(system_name)}-{_normalize_machine(machine_name)}"


def build_biliup_runtime_path(system_name: str | None = None) -> Path:
    executable_name = "biliup.exe" if _normalize_system(system_name) == "windows" else "biliup"
    return get_biliup_runtime_root() / _build_platform_key(system_name) / executable_name


def _build_biliup_version_path(system_name: str | None = None) -> Path:
    return build_biliup_runtime_path(system_name).with_name("version.txt")


def _select_release_asset(assets: list[dict]) -> dict:
    platform_key = _build_platform_key()
    preferred_patterns = {
        "windows-x86_64": ("x86_64-windows.zip",),
        "linux-x86_64": ("x86_64-linux.tar.xz",),
        "linux-aarch64": ("aarch64-linux.tar.xz",),
        "linux-arm": ("arm-linux.tar.xz",),
        "macos-x86_64": ("x86_64-macos.tar.xz",),
        "macos-aarch64": ("aarch64-macos.tar.xz",),
    }
    patterns = preferred_patterns.get(platform_key)
    if not patterns:
        raise RuntimeError(f"Unsupported biliup platform: {platform_key}")

    for asset in assets:
        asset_name = asset.get("name", "")
        if any(pattern in asset_name for pattern in patterns):
            return {
                "asset_name": asset_name,
                "asset_url": asset.get("browser_download_url", ""),
            }

    raise RuntimeError(f"No matching biliup release asset found for platform: {platform_key}")


def fetch_latest_release() -> dict:
    response = requests.get(
        GITHUB_RELEASE_API,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "social-auto-upload",
        },
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    selected_asset = _select_release_asset(payload.get("assets", []))
    return {
        "tag_name": payload.get("tag_name", ""),
        "asset_name": selected_asset["asset_name"],
        "asset_url": selected_asset["asset_url"],
    }


def read_local_biliup_version() -> str | None:
    version_path = _build_biliup_version_path()
    if not version_path.exists():
        return None
    return version_path.read_text(encoding="utf-8").strip() or None


def write_local_biliup_version(version: str) -> None:
    version_path = _build_biliup_version_path()
    version_path.parent.mkdir(parents=True, exist_ok=True)
    version_path.write_text(version, encoding="utf-8")


def _pick_executable(extract_root: Path) -> Path:
    candidates = []
    for path in extract_root.rglob("*"):
        if not path.is_file():
            continue
        lower_name = path.name.lower()
        if lower_name in {"biliup", "biliup.exe", "biliupr", "biliupr.exe"}:
            candidates.append(path)
    if not candidates:
        raise RuntimeError("Downloaded biliup archive does not contain a runnable executable")
    candidates.sort(key=lambda item: len(str(item)))
    return candidates[0]


def download_biliup_asset(release: dict, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="biliup-download-") as temp_dir:
        temp_root = Path(temp_dir)
        archive_path = temp_root / release["asset_name"]
        with requests.get(release["asset_url"], stream=True, timeout=120) as response:
            response.raise_for_status()
            with archive_path.open("wb") as file_obj:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        file_obj.write(chunk)

        extract_root = temp_root / "extract"
        extract_root.mkdir(parents=True, exist_ok=True)
        if archive_path.suffix.lower() == ".zip":
            with zipfile.ZipFile(archive_path) as zip_file:
                zip_file.extractall(extract_root)
        else:
            with tarfile.open(archive_path, "r:xz") as tar_file:
                tar_file.extractall(extract_root)

        extracted_binary = _pick_executable(extract_root)
        temp_binary = destination.with_suffix(f"{destination.suffix}.tmp")
        shutil.copy2(extracted_binary, temp_binary)
        temp_binary.replace(destination)
        if _normalize_system() != "windows":
            destination.chmod(destination.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return destination


def ensure_biliup_binary(force_check: bool = True) -> Path:
    if _environment_flag("GEO_DESKTOP_MODE"):
        configured_path = str(os.getenv(DESKTOP_BILIUP_BINARY_ENV, "")).strip()
        candidate = Path(configured_path) if configured_path else build_biliup_runtime_path()
        report = inspect_desktop_biliup_runtime(candidate, probe=True)
        expected_sha256 = str(os.getenv(DESKTOP_BILIUP_SHA256_ENV, "")).strip().lower()
        if expected_sha256 and expected_sha256 != report.get("sha256"):
            raise RuntimeError("Bilibili 兼容运行时与桌面启动时固定的 SHA-256 不一致")
        if not report["ready"]:
            raise RuntimeError(str(report["reason"]))
        return Path(report["path"])

    binary_path = build_biliup_runtime_path()
    local_version = read_local_biliup_version()

    # 默认优先复用本地已存在的 biliup，避免每次执行都去请求 GitHub latest release。
    if binary_path.exists() and not force_check:
        return binary_path

    try:
        release = fetch_latest_release()
    except Exception:
        # 如果本地已经有可执行的 biliup，就在 GitHub 限流/网络失败时直接复用本地版本。
        if binary_path.exists():
            return binary_path
        raise

    latest_version = release["tag_name"]
    if binary_path.exists() and local_version == latest_version:
        return binary_path

    download_biliup_asset(release, binary_path)
    write_local_biliup_version(latest_version)
    return binary_path


def run_biliup_command(
    arguments: list[str],
    interactive: bool = False,
    *,
    timeout_seconds: float | None = None,
) -> subprocess.CompletedProcess[str]:
    binary_path = ensure_biliup_binary(force_check=False)
    return _run_owned_biliup_executable(
        binary_path,
        arguments,
        interactive=interactive,
        timeout_seconds=timeout_seconds,
    )
