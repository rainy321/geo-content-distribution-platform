"""Windows desktop host for the GEO content platform.

The executable keeps the existing Vue/Flask product boundary intact: Vue is
served from the bundled build, Flask owns business rules and persistence, and
the desktop host only manages lifecycle, local paths and the native window.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import importlib
import json
import logging
import os
import shutil
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Mapping, MutableMapping, Sequence


APP_VERSION = "1.2.8"
BUILD_INFO_SCHEMA_VERSION = 1
APP_TITLE = "GEO 智能内容分发平台"
APP_DIRECTORY_NAME = "GEOContentPlatform"
PUBLISHING_PROFILE_SAFE = "safe"
PUBLISHING_PROFILE_AUTHORIZED_REAL = "authorized_real"
PUBLISHING_PROFILES = frozenset(
    {PUBLISHING_PROFILE_SAFE, PUBLISHING_PROFILE_AUTHORIZED_REAL}
)
LOOPBACK_HOST = "127.0.0.1"
STARTUP_TIMEOUT_SECONDS = 30.0
_WINDOWS_ALREADY_EXISTS = 183


@dataclass(frozen=True)
class DesktopPaths:
    """Persistent paths owned by one Windows user."""

    root: Path
    database: Path
    media: Path
    cookies: Path
    auth_config: Path
    ai_config: Path
    backups: Path
    logs: Path
    webview: Path

    @classmethod
    def from_root(cls, root: str | Path) -> "DesktopPaths":
        resolved_root = Path(root).expanduser().resolve()
        return cls(
            root=resolved_root,
            database=resolved_root / "data" / "geo.db",
            media=resolved_root / "media",
            cookies=resolved_root / "accounts",
            auth_config=resolved_root / "config" / "auth.json",
            ai_config=resolved_root / "config" / "ai.json",
            backups=resolved_root / "backups",
            logs=resolved_root / "logs",
            webview=resolved_root / "webview",
        )

    def create(self) -> None:
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self.media.mkdir(parents=True, exist_ok=True)
        self.cookies.mkdir(parents=True, exist_ok=True)
        self.auth_config.parent.mkdir(parents=True, exist_ok=True)
        self.backups.mkdir(parents=True, exist_ok=True)
        self.logs.mkdir(parents=True, exist_ok=True)
        self.webview.mkdir(parents=True, exist_ok=True)


def resolve_data_root(environ: Mapping[str, str] | None = None) -> Path:
    """Resolve a stable per-user data directory without touching the repo."""

    values = os.environ if environ is None else environ
    configured = str(values.get("GEO_DESKTOP_DATA_DIR", "")).strip()
    if configured:
        return Path(configured).expanduser().resolve()

    local_app_data = str(values.get("LOCALAPPDATA", "")).strip()
    if local_app_data:
        return (Path(local_app_data) / APP_DIRECTORY_NAME).resolve()

    return (Path.home() / ".geo-content-platform").resolve()


def resolve_desktop_ai_runtime(
    paths: DesktopPaths,
    environ: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Return the configured public provider/model without exposing secrets.

    ``CONTENT_ENGINE`` names the legacy adapter route (normally ``qwen``), not
    the OpenAI-compatible provider selected by the user. Desktop diagnostics
    must read unified AI metadata rather than present the route as a provider.
    """

    values = os.environ if environ is None else environ
    provider = str(values.get("AI_PROVIDER", "")).strip().lower()
    model = str(values.get("AI_MODEL", "")).strip()
    try:
        from services.unified_ai_config_service import (
            UnifiedAIConfigError,
            UnifiedAIConfigService,
        )

        status = UnifiedAIConfigService(paths.ai_config).status()
        if status.configured:
            provider = str(status.provider or provider).strip().lower()
            model = str(status.model or model).strip()
    except (UnifiedAIConfigError, OSError, ValueError):
        # Diagnostic metadata must never prevent the desktop shell starting.
        pass
    return {
        "provider": provider or "unconfigured",
        "model": model or "unknown",
    }


def resolve_resource_root() -> Path:
    """Return the PyInstaller extraction root or the source checkout root."""

    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root:
        return Path(frozen_root).resolve()
    return Path(__file__).resolve().parent


def load_build_info(resource_root: str | Path | None = None) -> dict[str, Any]:
    """Load non-sensitive provenance embedded by the release build.

    Source runs intentionally fall back to an explicit development marker;
    packaged releases must contain ``desktop-build-info.json`` and the build
    smoke test verifies its contents.
    """

    root = (
        Path(resource_root).expanduser().resolve()
        if resource_root is not None
        else resolve_resource_root()
    )
    # The build script writes working metadata under ``.build`` for the spec,
    # then embeds it at the extraction root.  Source runs must never inherit
    # that authorized profile merely because a release was built nearby.
    candidates = (root / "desktop-build-info.json",)
    for candidate in candidates:
        if not candidate.is_file():
            continue
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"桌面构建信息无法读取：{candidate}") from exc
        if not isinstance(payload, dict):
            raise RuntimeError(f"桌面构建信息格式无效：{candidate}")
        if payload.get("schema_version") != BUILD_INFO_SCHEMA_VERSION:
            raise RuntimeError(f"桌面构建信息版本不受支持：{candidate}")
        if payload.get("version") != APP_VERSION:
            raise RuntimeError("桌面程序版本与构建信息不一致")
        publishing_profile = str(
            payload.get("publishing_profile") or PUBLISHING_PROFILE_SAFE
        ).strip()
        if publishing_profile not in PUBLISHING_PROFILES:
            raise RuntimeError("桌面构建信息包含不受支持的发布配置")
        return {
            "schema_version": BUILD_INFO_SCHEMA_VERSION,
            "version": APP_VERSION,
            "source_commit": str(payload.get("source_commit") or "unknown"),
            "source_branch": str(payload.get("source_branch") or "unknown"),
            "built_at_utc": str(payload.get("built_at_utc") or "unknown"),
            "source_dirty": bool(payload.get("source_dirty", False)),
            "publishing_profile": publishing_profile,
        }
    return {
        "schema_version": BUILD_INFO_SCHEMA_VERSION,
        "version": APP_VERSION,
        "source_commit": "development",
        "source_branch": "development",
        "built_at_utc": "development",
        "source_dirty": True,
        "publishing_profile": PUBLISHING_PROFILE_SAFE,
    }


def publishing_profile_allows_real(publishing_profile: str) -> bool:
    """Return whether an immutable desktop build may enable real publishing."""

    return str(publishing_profile).strip() == PUBLISHING_PROFILE_AUTHORIZED_REAL


def resolve_browser_executable(
    environ: Mapping[str, str] | None = None,
) -> Path | None:
    """Locate a supported system Chromium browser without downloading anything."""

    values = os.environ if environ is None else environ
    explicit = str(values.get("LOCAL_CHROME_PATH", "")).strip()
    if explicit:
        candidate = Path(explicit).expanduser()
        return candidate.resolve() if candidate.is_file() else None

    candidates: list[Path] = []
    for environment_name, relative_path in (
        ("ProgramFiles", "Google/Chrome/Application/chrome.exe"),
        ("ProgramFiles(x86)", "Google/Chrome/Application/chrome.exe"),
        ("LOCALAPPDATA", "Google/Chrome/Application/chrome.exe"),
    ):
        base = str(values.get(environment_name, "")).strip()
        if base:
            candidates.append(Path(base) / relative_path)
    # The inherited uploaders use Playwright/Patchright's Chrome channel in a
    # few platform-specific paths.  Do not claim Edge is universally supported
    # until those upstream flows are fully browser-agnostic.
    for executable_name in ("chrome.exe", "chrome"):
        resolved = shutil.which(executable_name)
        if resolved:
            candidates.append(Path(resolved))

    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    return None


def resolve_frontend_dir(resource_root: str | Path) -> Path:
    """Find a complete Vue build in frozen and source layouts."""

    root = Path(resource_root).resolve()
    candidates = (root / "frontend", root / "sau_frontend" / "dist")
    for candidate in candidates:
        if (candidate / "index.html").is_file():
            return candidate
    raise FileNotFoundError(
        "未找到桌面前端构建。请先运行 npm run build，或重新构建 EXE。"
    )


def reserve_loopback_socket() -> socket.socket:
    """Bind an ephemeral loopback socket and retain ownership until Waitress."""

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        listener.bind((LOOPBACK_HOST, 0))
    except Exception:
        listener.close()
        raise
    return listener


def reserve_loopback_port() -> int:
    """Return a transient port number for diagnostics and compatibility tests."""

    with reserve_loopback_socket() as listener:
        return int(listener.getsockname()[1])


def apply_desktop_environment(
    paths: DesktopPaths,
    frontend_dir: str | Path,
    port: int,
    *,
    environ: MutableMapping[str, str] | None = None,
    publishing_profile: str = PUBLISHING_PROFILE_SAFE,
) -> MutableMapping[str, str]:
    """Apply the desktop storage boundary before importing ``sau_backend``.

    Publishing flags are derived only from immutable build provenance, rather
    than inherited shell variables.  This keeps the safe build locked while an
    explicitly produced authorized build can expose the complete workflow.
    """

    if not 1 <= int(port) <= 65535:
        raise ValueError("桌面服务端口必须在 1 到 65535 之间")

    target = os.environ if environ is None else environ
    normalized_profile = str(publishing_profile).strip()
    if normalized_profile not in PUBLISHING_PROFILES:
        raise ValueError("不受支持的桌面发布配置")
    real_publishing_enabled = publishing_profile_allows_real(normalized_profile)
    browser_executable = resolve_browser_executable(target)
    paths.create()
    from services.local_auth_service import LocalAuthService

    auth_service = LocalAuthService(paths.auth_config)
    session_secret = auth_service.get_session_secret()
    # A machine-wide shell variable must never override the per-user desktop
    # credential store.  The browser deployment keeps its existing env-password
    # path because GEO_DESKTOP_MODE is only assigned by this native host.
    target.pop("APP_ACCESS_PASSWORD", None)
    target.update(
        {
            "DATABASE_PATH": str(paths.database),
            "MEDIA_ROOT": str(paths.media),
            "COOKIES_DIRECTORY": str(paths.cookies),
            "LOGS_DIRECTORY": str(paths.logs),
            "FRONTEND_BUILD_DIR": str(Path(frontend_dir).resolve()),
            "SERVER_HOST": LOOPBACK_HOST,
            "SERVER_PORT": str(port),
            "SESSION_COOKIE_SECURE": "false",
            "APP_SESSION_SECRET": session_secret,
            "GEO_DESKTOP_MODE": "true",
            "GEO_DESKTOP_AUTH_PATH": str(paths.auth_config),
            "GEO_UNIFIED_AI_CONFIG_PATH": str(paths.ai_config),
            "DEMO_MODE": "false",
            "SEED_DEMO_DATA": "false",
            # The immutable profile grants intent.  The actual gate stays
            # closed until the packaged browser engines pass a local probe in
            # ``run`` immediately before Flask is imported.
            "GEO_REAL_PUBLISHING_AUTHORIZED": (
                "true" if real_publishing_enabled else "false"
            ),
            "GEO_PUBLISHING_RUNTIME_READY": "false",
            "ALLOW_REAL_PUBLISHING": "false",
            "ALLOW_LEGACY_DIRECT_PUBLISHING": "false",
            "ENABLE_BILIBILI_RUNTIME": "false",
            "RUN_PUBLISH_SCHEDULER": "true",
            "GEO_DESKTOP_PUBLISHING_PROFILE": normalized_profile,
            "GEO_PUBLISHING_BROWSER_READY": (
                "true" if browser_executable is not None else "false"
            ),
            "LOCAL_CHROME_HEADLESS": (
                "false" if real_publishing_enabled else "true"
            ),
        }
    )
    # Never inherit an executable path or digest from a parent shell.  The
    # desktop host repopulates both only after its own fixed-version probe.
    target.pop("GEO_BILIUP_BINARY", None)
    target.pop("GEO_BILIUP_SHA256", None)
    if browser_executable is not None:
        target["LOCAL_CHROME_PATH"] = str(browser_executable)
    else:
        target.pop("LOCAL_CHROME_PATH", None)
    return target


def configure_logging(paths: DesktopPaths) -> logging.Logger:
    logger = logging.getLogger("geo.desktop")
    logger.setLevel(logging.INFO)
    if logger.handlers:
        return logger

    handler = RotatingFileHandler(
        paths.logs / "desktop.log",
        maxBytes=2 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    )
    logger.addHandler(handler)
    return logger


class SingleInstanceMutex:
    """A named Windows mutex scoped to one data directory."""

    def __init__(self, data_root: str | Path):
        identity = str(Path(data_root).resolve()).casefold().encode("utf-8")
        suffix = hashlib.sha256(identity).hexdigest()[:16]
        self.name = f"Local\\GEOContentPlatform-{suffix}"
        self._handle: int | None = None

    def acquire(self) -> bool:
        if os.name != "nt":
            return True
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.CreateMutexW(None, False, self.name)
        if not handle:
            raise OSError("无法创建桌面单实例锁")
        self._handle = handle
        return kernel32.GetLastError() != _WINDOWS_ALREADY_EXISTS

    def release(self) -> None:
        if self._handle is not None and os.name == "nt":
            ctypes.windll.kernel32.CloseHandle(self._handle)
        self._handle = None


class DesktopServer:
    """Lifecycle wrapper around Waitress's stoppable WSGI server."""

    def __init__(self, application: Any, listener: socket.socket):
        from waitress.server import create_server

        self._server = create_server(
            application,
            sockets=[listener],
            threads=8,
            channel_timeout=180,
            ident="GEO Desktop",
        )
        self._thread = threading.Thread(
            target=self._server.run,
            name="geo-desktop-http",
            daemon=True,
        )

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._server.close()
        self._server.task_dispatcher.shutdown(cancel_pending=True, timeout=5)
        self._thread.join(timeout=6)


class DesktopApi:
    """Small, non-sensitive native bridge exposed to the Vue shell."""

    def __init__(
        self,
        paths: DesktopPaths,
        *,
        build_info: Mapping[str, Any] | None = None,
        publishing_runtime: Mapping[str, Any] | None = None,
    ):
        self.paths = paths
        self.build_info = dict(build_info or load_build_info())
        self._publishing_runtime = (
            dict(publishing_runtime) if publishing_runtime is not None else None
        )

    def get_runtime_status(self) -> dict[str, Any]:
        from services.local_auth_service import LocalAuthService

        auth_status = LocalAuthService(self.paths.auth_config).status()
        ai_runtime = resolve_desktop_ai_runtime(self.paths)
        publishing_profile = str(
            self.build_info.get("publishing_profile") or PUBLISHING_PROFILE_SAFE
        )
        real_publishing_authorized = publishing_profile_allows_real(
            publishing_profile
        )
        publishing_runtime = (
            dict(self._publishing_runtime)
            if self._publishing_runtime is not None
            else inspect_publishing_runtime(
                enabled=real_publishing_authorized,
                probe_browser=False,
            )
        )
        real_publishing_enabled = (
            real_publishing_authorized
            and str(os.getenv("ALLOW_REAL_PUBLISHING", "false")).lower()
            == "true"
            and str(os.getenv("GEO_PUBLISHING_RUNTIME_READY", "false")).lower()
            == "true"
        )
        publishing_runtime["ready"] = bool(
            publishing_runtime["ready"] and real_publishing_enabled
        )
        bilibili_runtime = dict(
            publishing_runtime.get("bilibili_runtime")
            or inspect_bilibili_runtime(
                enabled=real_publishing_authorized,
                probe=False,
            )
        )
        bilibili_runtime_enabled = bool(
            real_publishing_enabled
            and bilibili_runtime.get("ready")
            and str(os.getenv("ENABLE_BILIBILI_RUNTIME", "false")).lower()
            == "true"
        )
        bilibili_runtime["ready"] = bilibili_runtime_enabled
        publishing_runtime["bilibili_runtime"] = bilibili_runtime
        return {
            "desktop": True,
            "version": APP_VERSION,
            "data_directory": str(self.paths.root),
            "content_engine": ai_runtime["provider"],
            "content_engine_model": ai_runtime["model"],
            "fallback_engine": str(
                os.getenv("CONTENT_ENGINE_FALLBACK", "disabled")
            ),
            "publishing_profile": publishing_profile,
            "real_publishing_authorized": real_publishing_authorized,
            "real_publishing_enabled": real_publishing_enabled,
            "scheduled_real_publishing_enabled": False,
            "bilibili_runtime_enabled": bilibili_runtime_enabled,
            "publishing_runtime": publishing_runtime,
            "authentication_required": True,
            "setup_required": auth_status.setup_required,
            "backup_directory": str(self.paths.backups),
            "build": dict(self.build_info),
        }

    def open_data_directory(self) -> dict[str, Any]:
        try:
            if os.name == "nt":
                os.startfile(str(self.paths.root))  # type: ignore[attr-defined]
            else:
                return {
                    "ok": False,
                    "message": "此构建仅支持在 Windows 中打开数据目录",
                }
        except OSError as exc:
            return {"ok": False, "message": str(exc)}
        return {"ok": True, "message": "已打开数据目录"}

    def create_database_backup(self) -> dict[str, Any]:
        from services.desktop_backup_service import (
            DesktopBackupError,
            create_verified_database_backup,
        )

        try:
            result = create_verified_database_backup(
                self.paths.database,
                self.paths.backups,
            )
        except DesktopBackupError as exc:
            return {"ok": False, "message": str(exc)}
        return {
            "ok": True,
            "message": "数据库备份已创建并通过完整性检查",
            "backup": result.to_dict(),
        }


def wait_until_ready(
    base_url: str,
    *,
    timeout_seconds: float = STARTUP_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last_error = ""
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(
                f"{base_url}/api/health",
                timeout=2,
            ) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if response.status == 200 and payload.get("data", {}).get("status") == "ok":
                return payload
            last_error = f"健康检查返回 HTTP {response.status}"
        except (OSError, ValueError, urllib.error.URLError) as exc:
            last_error = str(exc)
        time.sleep(0.15)
    raise TimeoutError(f"桌面服务启动超时：{last_error or '未收到健康响应'}")


def fetch_json_endpoint(base_url: str, path: str) -> dict[str, Any]:
    """Fetch a local JSON endpoint used by packaged smoke tests."""

    with urllib.request.urlopen(f"{base_url}{path}", timeout=3) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if response.status != 200 or not isinstance(payload, dict):
        raise RuntimeError(f"本地端点 {path} 返回无效响应")
    return payload


def inspect_publishing_runtime(
    *,
    enabled: bool,
    probe_browser: bool = False,
) -> dict[str, Any]:
    """Inspect packaged modules and optionally run an offline browser probe."""

    browser_executable = resolve_browser_executable()
    if not enabled:
        return {
            "ready": False,
            "enabled": False,
            "browser_ready": browser_executable is not None,
            "browser_executable": (
                str(browser_executable) if browser_executable is not None else ""
            ),
            "missing_modules": [],
            "module_errors": {},
            "browser_probe": "not_run",
            "browser_probe_error": "",
            "reason": "当前桌面构建未授权真实发布",
            "error": "",
        }

    required_modules = (
        "playwright.async_api",
        "patchright.async_api",
        "cv2",
        "myUtils.login",
        "services.real_publisher_factory",
        "uploader.zhihu_uploader.main",
        "uploader.toutiao_uploader.main",
        "uploader.sohu_uploader.main",
        "uploader.baijiahao_uploader.main",
        "uploader.xiaohongshu_uploader.main",
        "uploader.douyin_uploader.main",
        "uploader.ks_uploader.main",
        "uploader.tencent_uploader.main",
        "uploader.tk_uploader.main_chrome",
    )
    missing_modules: list[str] = []
    module_errors: dict[str, str] = {}
    for module_name in required_modules:
        try:
            importlib.import_module(module_name)
        except Exception as exc:
            missing_modules.append(module_name)
            module_errors[module_name] = exc.__class__.__name__
    browser_ready = browser_executable is not None
    browser_probe = "not_run"
    browser_probe_error = ""
    if browser_ready and not missing_modules and probe_browser:
        try:
            _probe_browser_automation(browser_executable)
            browser_probe = "passed"
        except Exception as exc:
            browser_probe = "failed"
            browser_probe_error = str(exc).strip() or exc.__class__.__name__
    ready = browser_ready and not missing_modules and browser_probe != "failed"
    if not browser_ready:
        reason = "未找到 Google Chrome"
        error = ""
    elif missing_modules:
        reason = "发布运行时组件缺失或无法加载"
        error = ", ".join(missing_modules)
    elif browser_probe == "failed":
        reason = "浏览器自动化本地探针失败"
        error = browser_probe_error
    elif ready:
        reason = "发布运行时已就绪"
        error = ""
    else:
        reason = "发布运行时未就绪"
        error = ""
    return {
        "ready": ready,
        "enabled": True,
        "browser_ready": browser_ready,
        "browser_executable": (
            str(browser_executable) if browser_executable is not None else ""
        ),
        "missing_modules": missing_modules,
        "module_errors": module_errors,
        "browser_probe": browser_probe,
        "browser_probe_error": browser_probe_error,
        "reason": reason,
        "error": error,
    }


def inspect_bilibili_runtime(
    *,
    enabled: bool,
    probe: bool = True,
) -> dict[str, Any]:
    """Inspect the fixed, external Bilibili compatibility runtime offline."""

    if not enabled:
        return {
            "ready": False,
            "enabled": False,
            "path": "",
            "version": "",
            "sha256": "",
            "bytes": 0,
            "packaged": False,
            "source": "external_compatibility_runtime",
            "reason": "当前桌面构建未授权真实发布",
        }
    try:
        from uploader.bilibili_uploader.runtime import (
            inspect_desktop_biliup_runtime,
        )

        report = inspect_desktop_biliup_runtime(probe=probe)
    except Exception as exc:
        return {
            "ready": False,
            "enabled": True,
            "path": "",
            "version": "",
            "sha256": "",
            "bytes": 0,
            "packaged": False,
            "source": "external_compatibility_runtime",
            "reason": "Bilibili 兼容运行时探针失败",
            "error": str(exc).strip() or exc.__class__.__name__,
        }
    report["enabled"] = True
    return report


def _probe_browser_automation(browser_executable: Path | None) -> None:
    """Launch both packaged engines against an offline page and close them."""

    if browser_executable is None or not browser_executable.is_file():
        raise RuntimeError("未找到 Google Chrome")
    probes = (
        ("Playwright", "playwright.sync_api"),
        ("Patchright", "patchright.sync_api"),
    )
    for engine_name, module_name in probes:
        sync_api = importlib.import_module(module_name)
        with sync_api.sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=True,
                executable_path=str(browser_executable),
            )
            try:
                page = browser.new_page()
                page.set_content(f"<title>{engine_name}</title><p>GEO runtime ready</p>")
                if page.title() != engine_name:
                    raise RuntimeError(f"{engine_name} 本地页面探针失败")
            finally:
                browser.close()


def write_health_report(
    report_path: str | Path | None,
    payload: Mapping[str, Any],
) -> None:
    if report_path is None:
        return
    destination = Path(report_path).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def show_native_error(message: str, *, title: str = APP_TITLE) -> None:
    if os.name == "nt":
        ctypes.windll.user32.MessageBoxW(None, message, title, 0x10)
    else:
        print(f"{title}: {message}", file=sys.stderr)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="GEO-Content-Platform")
    parser.add_argument(
        "--data-dir",
        help="覆盖桌面运行数据目录，主要用于隔离测试",
    )
    smoke_mode = parser.add_mutually_exclusive_group()
    smoke_mode.add_argument(
        "--health-check",
        action="store_true",
        help="启动内置服务并完成一次健康检查后退出",
    )
    smoke_mode.add_argument(
        "--ui-smoke",
        action="store_true",
        help="在隐藏 WebView2 中完成合成数据产品流程后退出",
    )
    parser.add_argument(
        "--health-report",
        help="将健康检查 JSON 写入指定路径",
    )
    return parser


def _wait_for_webview_value(
    window: Any,
    expression: str,
    *,
    timeout_seconds: float = 20.0,
) -> Any:
    deadline = time.monotonic() + timeout_seconds
    last_error = ""
    while time.monotonic() < deadline:
        try:
            value = window.evaluate_js(expression)
            if value:
                return value
        except Exception as exc:  # the native bridge may still be attaching
            last_error = str(exc)
        time.sleep(0.15)
    raise TimeoutError(f"WebView2 页面等待超时：{last_error or expression}")


def run_native_ui_smoke(
    base_url: str,
    paths: DesktopPaths,
    build_info: Mapping[str, Any],
    publishing_runtime: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Exercise the packaged WebView and native bridge with synthetic data.

    This is deliberately local-only.  It configures the first-run password,
    creates a fictional project, runs the deterministic visibility endpoint,
    persists and reopens its route, then invokes the native SQLite backup
    bridge.  It never calls a model or a publishing platform.
    """

    import webview

    outcome: dict[str, Any] = {"ok": False}
    window = webview.create_window(
        f"{APP_TITLE} - UI Smoke",
        base_url,
        js_api=DesktopApi(
            paths,
            build_info=build_info,
            publishing_runtime=publishing_runtime,
        ),
        width=1200,
        height=760,
        min_size=(900, 640),
        hidden=True,
        background_color="#f7f7f5",
        text_select=True,
    )
    if window is None:
        raise RuntimeError("无法创建 WebView2 验收窗口")

    setup_payload = json.dumps(
        {"password": "Synthetic-UI-Smoke-2026!"},
        ensure_ascii=False,
    )
    project_payload = json.dumps(
        {
            "name": "合成桌面验收品牌",
            "website": "https://desktop-smoke.example",
            "product": "合成可见度产品",
            "industry": "合成测试",
            "description": "只用于隔离桌面成品验收，不对应任何客户。",
            "keywords": ["合成验收"],
            "competitors": ["虚构竞品"],
        },
        ensure_ascii=False,
    )

    def execute_smoke() -> None:
        try:
            _wait_for_webview_value(
                window,
                "document.readyState === 'complete' && "
                "document.body && document.body.innerText.includes('设置管理员口令')",
            )
            _wait_for_webview_value(
                window,
                "Boolean(window.pywebview && window.pywebview.api && "
                "window.pywebview.api.create_database_backup)",
            )
            flow_script = f"""
                sessionStorage.removeItem('geo-ui-smoke');
                (async () => {{
                  const call = async (path, payload) => {{
                    const response = await fetch(path, {{
                      method: 'POST',
                      credentials: 'include',
                      headers: {{ 'Content-Type': 'application/json' }},
                      body: JSON.stringify(payload)
                    }});
                    const body = await response.json();
                    if (!response.ok) throw new Error(path + ': ' + (body.msg || response.status));
                    return body.data;
                  }};
                  try {{
                    await call('/api/auth/setup', {setup_payload});
                    const project = await call('/api/projects', {project_payload});
                    const analysis = await call('/api/geo/visibility/analyze', {{
                      project_id: project.id,
                      prompt: '合成验收：哪些虚构工具适合内容可见度分析？',
                      responses: [
                        {{
                          text: '推荐合成桌面验收品牌，证据见 https://desktop-smoke.example/guide。',
                          provider: 'packaged-ui-smoke',
                          model: 'offline-fixture-v1'
                        }},
                        {{
                          text: '虚构竞品排名第一，合成桌面验收品牌排名第二。',
                          provider: 'packaged-ui-smoke',
                          model: 'offline-fixture-v1'
                        }}
                      ],
                      persist: true
                    }});
                    const backup = await window.pywebview.api.create_database_backup();
                    if (!backup || !backup.ok) throw new Error(backup?.message || 'native backup failed');
                    sessionStorage.setItem('geo-ui-smoke', JSON.stringify({{
                      ok: true,
                      project_id: project.id,
                      run_id: analysis.run_id,
                      sample_count: analysis.summary.sample_count,
                      external_model_used: analysis.methodology.external_model_used,
                      backup_path: backup.backup.path,
                      backup_integrity: backup.backup.integrity_check
                    }}));
                    window.location.hash = '#/ai-visibility';
                    // The setup request authenticates the HTTP session, while
                    // the already-mounted first-run gate still holds its old
                    // pre-setup state.  Reload once so the real Vue bootstrap
                    // observes that authenticated session and renders the
                    // requested product route inside WebView2.
                    window.location.reload();
                  }} catch (error) {{
                    sessionStorage.setItem('geo-ui-smoke', JSON.stringify({{
                      ok: false,
                      error: String(error && error.message ? error.message : error)
                    }}));
                  }}
                }})();
            """
            window.evaluate_js(flow_script)
            state_text = _wait_for_webview_value(
                window,
                "sessionStorage.getItem('geo-ui-smoke')",
                timeout_seconds=30.0,
            )
            state = json.loads(str(state_text))
            if not state.get("ok"):
                raise RuntimeError(str(state.get("error") or "WebView2 合成流程失败"))
            _wait_for_webview_value(
                window,
                "window.location.hash === '#/ai-visibility' && "
                "document.body && document.body.innerText.includes('AI 可见度')",
                timeout_seconds=20.0,
            )
            backup_path = Path(str(state.get("backup_path", ""))).resolve()
            if not backup_path.is_file() or backup_path.parent != paths.backups:
                raise RuntimeError("WebView2 原生备份没有写入隔离备份目录")
            if state.get("external_model_used") is not False:
                raise RuntimeError("合成可见度验收意外声明使用了外部模型")
            outcome.update(
                {
                    "ok": True,
                    "native_gui": "edgechromium",
                    "first_run_setup": "passed",
                    "route": "#/ai-visibility",
                    "project_id": state.get("project_id"),
                    "run_id": state.get("run_id"),
                    "sample_count": state.get("sample_count"),
                    "history_persisted": bool(state.get("run_id")),
                    "backup_integrity": state.get("backup_integrity"),
                    "synthetic_data_only": True,
                    "external_model_used": False,
                    "real_publishing_attempted": False,
                }
            )
        except Exception as exc:
            diagnostic = ""
            try:
                diagnostic = str(
                    window.evaluate_js(
                        "JSON.stringify({path: window.location.pathname, "
                        "hash: window.location.hash, "
                        "text: document.body ? document.body.innerText.slice(0, 500) : ''})"
                    )
                    or ""
                )
            except Exception:
                pass
            outcome.update(
                {"ok": False, "error": str(exc), "diagnostic": diagnostic}
            )
        finally:
            window.destroy()

    webview.start(
        execute_smoke,
        gui="edgechromium",
        debug=False,
        private_mode=True,
        storage_path=str(paths.webview),
    )
    return outcome


def _start_publish_scheduler(paths: DesktopPaths):
    from services.publish_scheduler_runtime import create_publish_scheduler
    from services.platform_review_verifier import create_platform_review_verifier

    # Scheduled real publishing requires a separate two-phase authorization
    # protocol.  Until that exists, the desktop scheduler may promote due jobs
    # but must never load a real publisher or contact a platform.
    # Review monitoring is a separate read-only path: it may inspect creator
    # status pages in an authorized desktop build, but has no publisher or
    # submit action available to it.
    review_enabled = (
        str(os.getenv("GEO_REAL_PUBLISHING_AUTHORIZED", "false")).lower() == "true"
        and str(os.getenv("GEO_PUBLISHING_RUNTIME_READY", "false")).lower() == "true"
    )
    scheduler = create_publish_scheduler(
        paths.database,
        interval_seconds=15,
        publisher_factory=None,
        allow_real=False,
        media_root=paths.media,
        review_verifier=(
            create_platform_review_verifier(paths.cookies)
            if review_enabled
            else None
        ),
        review_interval_seconds=60,
        review_limit=2,
    )
    scheduler.start()
    return scheduler


def _create_startup_backup(paths: DesktopPaths, logger: logging.Logger) -> None:
    if not paths.database.is_file():
        return
    if paths.database.stat().st_size == 0:
        raise RuntimeError(
            f"本地数据库是 0 字节文件，已停止启动以避免覆盖：{paths.database}。"
            "请从 backups 目录恢复，或在确认无数据后移走该文件。"
        )
    from services.desktop_backup_service import create_verified_database_backup

    result = create_verified_database_backup(paths.database, paths.backups)
    logger.info("startup database backup path=%s bytes=%s", result.path, result.bytes)


def run(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    data_root = Path(args.data_dir).resolve() if args.data_dir else resolve_data_root()
    paths = DesktopPaths.from_root(data_root)
    paths.create()
    logger = configure_logging(paths)
    mutex = SingleInstanceMutex(paths.root)

    if not mutex.acquire():
        show_native_error("GEO 桌面版已在运行，请切换到现有窗口。")
        return 2

    server: DesktopServer | None = None
    scheduler = None
    listener: socket.socket | None = None
    try:
        resource_root = resolve_resource_root()
        build_info = load_build_info(resource_root)
        publishing_profile = str(build_info["publishing_profile"])
        frontend_dir = resolve_frontend_dir(resource_root)
        listener = reserve_loopback_socket()
        port = int(listener.getsockname()[1])
        _create_startup_backup(paths, logger)
        apply_desktop_environment(
            paths,
            frontend_dir,
            port,
            publishing_profile=publishing_profile,
        )
        publishing_runtime = inspect_publishing_runtime(
            enabled=publishing_profile_allows_real(publishing_profile),
            probe_browser=publishing_profile_allows_real(publishing_profile),
        )
        bilibili_runtime = inspect_bilibili_runtime(
            enabled=publishing_profile_allows_real(publishing_profile),
            probe=publishing_profile_allows_real(publishing_profile),
        )
        publishing_runtime["bilibili_runtime"] = bilibili_runtime
        runtime_ready = bool(publishing_runtime["ready"])
        os.environ["GEO_PUBLISHING_RUNTIME_READY"] = (
            "true" if runtime_ready else "false"
        )
        os.environ["ALLOW_REAL_PUBLISHING"] = (
            "true" if runtime_ready else "false"
        )
        bilibili_runtime_ready = bool(runtime_ready and bilibili_runtime["ready"])
        os.environ["ENABLE_BILIBILI_RUNTIME"] = (
            "true" if bilibili_runtime_ready else "false"
        )
        if bilibili_runtime_ready:
            os.environ["GEO_BILIUP_BINARY"] = str(bilibili_runtime["path"])
            os.environ["GEO_BILIUP_SHA256"] = str(bilibili_runtime["sha256"])
        else:
            os.environ.pop("GEO_BILIUP_BINARY", None)
            os.environ.pop("GEO_BILIUP_SHA256", None)
        logger.info(
            "starting version=%s profile=%s publishing_ready=%s bilibili_ready=%s data_root=%s port=%s",
            APP_VERSION,
            publishing_profile,
            runtime_ready,
            bilibili_runtime_ready,
            paths.root,
            port,
        )

        # Import only after the desktop paths and safety flags are frozen.
        from sau_backend import app
        from services.publish_job_service import recover_interrupted_publish_jobs

        recovered_jobs = recover_interrupted_publish_jobs(paths.database)
        if recovered_jobs:
            logger.warning(
                "recovered interrupted publish jobs for manual reconciliation count=%s",
                recovered_jobs,
            )

        server = DesktopServer(app, listener)
        listener = None  # Waitress now owns the reserved socket.
        server.start()
        base_url = f"http://{LOOPBACK_HOST}:{port}"
        health = wait_until_ready(base_url)

        if args.health_check:
            auth = fetch_json_endpoint(base_url, "/api/auth/status")
            native_status = DesktopApi(
                paths,
                build_info=build_info,
                publishing_runtime=publishing_runtime,
            ).get_runtime_status()
            report = {
                "ok": True,
                "version": APP_VERSION,
                "build": build_info,
                "frontend": (frontend_dir / "index.html").is_file(),
                "health": health.get("data", {}),
                "auth": auth.get("data", {}),
                "desktop": native_status,
                "publishing_runtime_probe": publishing_runtime,
                "data_directory": str(paths.root),
            }
            write_health_report(args.health_report, report)
            logger.info("health check passed")
            return 0

        if args.ui_smoke:
            ui_report = run_native_ui_smoke(
                base_url,
                paths,
                build_info,
                publishing_runtime=publishing_runtime,
            )
            report = {
                "ok": bool(ui_report.get("ok")),
                "version": APP_VERSION,
                "build": build_info,
                "health": health.get("data", {}),
                "ui": ui_report,
                "data_directory": str(paths.root),
            }
            write_health_report(args.health_report, report)
            if report["ok"]:
                logger.info("native UI smoke passed")
            else:
                logger.error(
                    "native UI smoke failed error=%s diagnostic=%s",
                    ui_report.get("error"),
                    ui_report.get("diagnostic"),
                )
            # A hidden Python.NET/WebView2 run can retain CLR helper threads.
            # This dedicated smoke path owns only a disposable data directory;
            # the report is fully flushed, so end the process and let Windows
            # release its socket/mutex handles instead of hanging the build.
            logging.shutdown()
            os._exit(0 if report["ok"] else 1)

        scheduler = _start_publish_scheduler(paths)

        import webview

        webview.create_window(
            APP_TITLE,
            base_url,
            js_api=DesktopApi(
                paths,
                build_info=build_info,
                publishing_runtime=publishing_runtime,
            ),
            width=1440,
            height=900,
            min_size=(1080, 700),
            background_color="#f7f7f5",
            text_select=True,
        )
        webview.start(
            gui="edgechromium",
            debug=False,
            private_mode=False,
            storage_path=str(paths.webview),
        )
        return 0
    except Exception as exc:
        logger.exception("desktop startup failed")
        write_health_report(
            args.health_report,
            {"ok": False, "error": str(exc), "data_directory": str(paths.root)},
        )
        if not args.health_check:
            show_native_error(
                f"GEO 桌面版启动失败。\n\n{exc}\n\n日志：{paths.logs / 'desktop.log'}"
            )
        return 1
    finally:
        if scheduler is not None:
            scheduler.shutdown(wait=False)
        try:
            from uploader.bilibili_uploader.runtime import (
                terminate_all_biliup_processes,
            )

            terminated_biliup_processes = terminate_all_biliup_processes()
            if terminated_biliup_processes:
                logger.info(
                    "terminated owned Bilibili process trees count=%s",
                    terminated_biliup_processes,
                )
        except Exception:
            logger.exception("failed to terminate owned Bilibili process trees")
        if server is not None:
            server.stop()
        elif listener is not None:
            listener.close()
        mutex.release()


def main() -> None:
    exit_code = run()
    if os.name == "nt" and bool(getattr(sys, "frozen", False)):
        # Python.NET/WebView2 can leave CLR helper threads alive after the
        # window closes.  ``run`` has already completed its ``finally`` block,
        # so flush logs and end the frozen child process without waiting for
        # those runtime-owned threads.  The PyInstaller parent then exits too.
        logging.shutdown()
        os._exit(exit_code)
    else:
        raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
