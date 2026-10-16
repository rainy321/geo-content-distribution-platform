"""Windows desktop entry point for the GEO workspace."""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import logging
import os
import shutil
import socket
import sqlite3
import sys
import threading
import time
from contextlib import closing
from pathlib import Path


WINDOW_TITLE = "GEO AI 搜索工作台"


def default_data_directory() -> Path:
    # AppData can be redirected when launched by a packaged host. Keep the
    # desktop's durable state under the user's profile for every launch method.
    return Path.home() / ".geo-workbench"


def migrate_legacy_desktop_data(destination: Path) -> None:
    candidates = [Path(os.getenv("LOCALAPPDATA") or Path.home()) / "Tongyuye/GEO_AI_Workbench",
                  Path.home() / "AppData/Local/Tongyuye/GEO_AI_Workbench"]
    candidates.extend((Path.home() / "AppData/Local/Packages").glob(
        "OpenAI.Codex_*/LocalCache/Local/Tongyuye/GEO_AI_Workbench"))
    destination.mkdir(parents=True, exist_ok=True)
    imported = []
    for source in dict.fromkeys(path.resolve() for path in candidates):
        if source == destination or not source.is_dir():
            continue
        identifier = hashlib.sha256(str(source).encode()).hexdigest()[:16]
        backup = destination / "recovery" / identifier
        source_db = source / "database.db"
        if source_db.is_file():
            backup.mkdir(parents=True, exist_ok=True)
            for target_db in (backup / "database.db", destination / "database.db"):
                if not target_db.exists():
                    with closing(sqlite3.connect(source_db.as_uri() + "?mode=ro", uri=True)) as connection:
                        with closing(sqlite3.connect(target_db)) as target:
                            connection.backup(target)
        for name in ("geo-tests", "desktop-jobs", "doubao", "media", "cookies"):
            source_tree = source / name
            if not source_tree.is_dir():
                continue
            for file in source_tree.rglob("*"):
                if not file.is_file() or file.is_symlink() or file.suffix.lower() == ".lock":
                    continue
                try:
                    relative = file.resolve().relative_to(source)
                    target = destination / relative
                    if not target.exists():
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(file, target)
                    elif file.read_bytes() != target.read_bytes():
                        conflict = backup / relative
                        if not conflict.exists():
                            conflict.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(file, conflict)
                except (OSError, ValueError):
                    logging.warning("Legacy desktop file retained at source: %s", file)
        for name in ("workspace-state.json", "desktop-port.json"):
            if (source / name).is_file() and not (destination / name).exists():
                shutil.copy2(source / name, destination / name)
        imported.append(str(source))
    if imported:
        (destination / "legacy-data-import.json").write_text(
            json.dumps({"sources": imported, "originals_preserved": True}, ensure_ascii=False, indent=2), encoding="utf-8")


def migrate_workspace(workspace: Path, destination: Path) -> dict:
    """Copy local data once; never bundle accounts or overwrite desktop data."""
    workspace, destination = workspace.resolve(), destination.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    copied = 0
    source_db, target_db = workspace / "db/database.db", destination / "database.db"
    database_copied = False
    if source_db.is_file() and not target_db.exists():
        with closing(sqlite3.connect(source_db.as_uri() + "?mode=ro", uri=True)) as source:
            with closing(sqlite3.connect(target_db)) as target:
                source.backup(target)
        database_copied = True
    source_reports = workspace / "runtime/geo-tests"
    if source_reports.is_dir():
        for source in source_reports.rglob("*"):
            if not source.is_file() or source.is_symlink():
                continue
            relative = source.relative_to(source_reports)
            if (relative.parts[0] in {"local-app", "ui-verification"}
                    or source.suffix.lower() not in {".json", ".png", ".html", ".txt"}):
                continue
            try:
                source.resolve().relative_to(source_reports.resolve())
            except ValueError:
                continue
            target = destination / "geo-tests" / relative
            if target.exists():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            copied += 1
    summary = {"source": str(workspace), "database_copied": database_copied,
               "report_files_copied": copied, "browser_sessions_copied": False}
    (destination / "workspace-import.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def configure_environment(data: Path) -> None:
    for name in ("media", "cookies", "logs", "webview", "geo-tests"):
        (data / name).mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("DATABASE_PATH", str(data / "database.db"))
    os.environ.setdefault("MEDIA_ROOT", str(data / "media"))
    os.environ.setdefault("COOKIES_DIRECTORY", str(data / "cookies"))
    os.environ["RUN_PUBLISH_SCHEDULER"] = "false"
    os.environ["SEED_DEMO_DATA"] = "false"
    os.environ.setdefault("DEMO_MODE", "false")
    os.chdir(data)


def create_desktop_server(app, data: Path):
    from werkzeug.serving import ThreadedWSGIServer

    class DesktopServer(ThreadedWSGIServer):
        # Windows SO_REUSEADDR can bind an already occupied port and route
        # requests to the wrong desktop instance. Always bind exclusively.
        allow_reuse_address = False

        def server_bind(self):
            if os.name == "nt":
                self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            super().server_bind()

    preference = data / "desktop-port.json"
    try:
        preferred = int(json.loads(preference.read_text(encoding="utf-8"))["port"])
    except (OSError, ValueError, KeyError, TypeError):
        preferred = 5418
    if not 5418 <= preferred <= 5430:
        preferred = 5418
    # Bind directly rather than checking and later reopening a port.
    for port in dict.fromkeys([preferred, *range(5418, 5431)]):
        try:
            server = DesktopServer("127.0.0.1", port, app)
            preference.write_text(json.dumps({"port": port}), encoding="utf-8")
            return server
        except (OSError, SystemExit):
            continue
    raise RuntimeError("桌面软件的本机端口暂时不可用")


class SingleInstance:
    def __init__(self, data: Path):
        self.handle = None
        self.acquired = True
        if os.name == "nt":
            from ctypes import wintypes

            self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            self.kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
            self.kernel.CreateMutexW.restype = wintypes.HANDLE
            self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            name = "Local\\Tongyuye.GEO.Workbench." + hashlib.sha256(str(data).casefold().encode()).hexdigest()[:16]
            self.handle = self.kernel.CreateMutexW(None, False, name)
            if not self.handle:
                raise OSError("无法创建软件实例锁")
            self.acquired = ctypes.get_last_error() != 183

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


def activate_existing_window() -> None:
    if os.name != "nt":
        return
    from ctypes import wintypes

    user = ctypes.windll.user32
    user.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    user.FindWindowW.restype = wintypes.HWND
    user.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user.SetForegroundWindow.argtypes = [wintypes.HWND]
    handle = user.FindWindowW(None, WINDOW_TITLE)
    if handle:
        user.ShowWindow(handle, 9)
        user.SetForegroundWindow(handle)


def run_smoke_check(window, data: Path, url: str) -> None:
    """Exercise the actual embedded window; no provider requests or fixtures."""
    status = {"passed": False, "embedded_window": True, "url": url, "mock_api": False}
    try:
        from services.desktop_task_store import DesktopTaskStore
        store = DesktopTaskStore(data)
        expected_history = len(store.records())
        workspace = store.workspace()
        for _ in range(60):
            try:
                observed = window.evaluate_js("""JSON.stringify({
                    heading: document.querySelector('h1')?.textContent,
                    report: [...document.querySelectorAll('h2')].find(e => e.textContent.includes('采集报告'))?.textContent,
                    rates: [...document.querySelectorAll('.cohorts strong')].map(e => e.textContent),
                    native_bridge: typeof window.pywebview?.api?.start_run === 'function'
                    ,history_rows: document.querySelectorAll('[data-testid="desktop-history"] .el-table__row').length
                    ,draft_company: document.querySelector('input[data-testid="company-input"], [data-testid="company-input"] input')?.value
                    ,plan_heading: [...document.querySelectorAll('h2')].find(e => e.textContent.includes('测试计划'))?.textContent
                })""")
                observed = json.loads(observed) if isinstance(observed, str) else observed
                restored_company = "company" not in workspace or observed.get("draft_company") == workspace["company"]
                restored_plan = not workspace.get("plan") or observed.get("plan_heading") == workspace["plan"]["profile"]["name"] + " · 测试计划"
                if observed and observed.get("heading") == "AI 搜索测试" and observed.get("report") and observed.get("native_bridge") and observed.get("history_rows", 0) >= expected_history and restored_company and restored_plan:
                    status.update(passed=True, **observed)
                    status["workspace_restored"] = True
                    break
            except Exception:
                pass
            time.sleep(0.5)
        if not status["passed"]:
            status["last_observed"] = observed
            raise RuntimeError("桌面窗口未加载实际报告")
        from services.desktop_geo_jobs import edge_executable, chrome_executable
        from services.browser_runtime import launch_context
        from playwright.async_api import async_playwright

        async def check_browser_engine():
            async with async_playwright() as playwright:
                browser = await launch_context(playwright, data / 'logs/browser-engine-diagnostic',
                    {'headless': True, 'executable_path': edge_executable()}, chrome_executable())
                try:
                    page = await browser.new_page()
                    await page.set_viewport_size({"width": 1380, "height": 880})
                    await page.set_content('<title>Synthetic desktop browser-engine check</title>')
                    ready = await page.title() == 'Synthetic desktop browser-engine check'
                    await page.goto(url)
                    await page.get_by_role('heading', name='AI 搜索测试', exact=True).wait_for()
                    await page.screenshot(path=str(data / 'logs/content-preview.png'))
                    return ready
                finally:
                    await browser.close()
        import asyncio
        status["collector_engine_ready"] = asyncio.run(check_browser_engine())
        # Exercise the same worker/login path that failed for the user. The
        # browser is real; only the URL and wait budget use a local QA page.
        import services.desktop_geo_jobs as native_jobs
        import services.doubao_source_collector as collector
        original_url, original_login = collector.DOUBAO_URL, native_jobs.open_doubao_session
        diagnostic = native_jobs.DesktopGeoJobs(data / 'logs/runtime-diagnostic')
        async def short_login(config, budget):
            return await original_login(config, 1)
        try:
            collector.DOUBAO_URL = url
            native_jobs.open_doubao_session = short_login
            diagnostic.open_login({'client_mode': 'consumer'})
            diagnostic.worker.join(timeout=25)
            status['native_login_path_with_local_page'] = diagnostic.get_task()['status'] == 'completed'
            if not status['native_login_path_with_local_page']:
                raise RuntimeError('原生登录任务回归验证失败')
        finally:
            diagnostic.shutdown()
            collector.DOUBAO_URL, native_jobs.open_doubao_session = original_url, original_login
        status["content_preview_method"] = "headless_preview_of_actual_local_server"
        if not status["collector_engine_ready"]:
            raise RuntimeError("采集浏览器引擎未就绪")
    except Exception as error:
        status.update(passed=False, error=type(error).__name__)
        logging.exception("Desktop smoke check failed")
    finally:
        (data / "logs/smoke-test.json").write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
        window.destroy()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=WINDOW_TITLE)
    parser.add_argument("--data-dir", type=Path, default=default_data_directory())
    parser.add_argument("--import-workspace", type=Path)
    parser.add_argument("--smoke-test", action="store_true")
    args = parser.parse_args(argv)
    data = args.data_dir.expanduser().resolve()
    data.mkdir(parents=True, exist_ok=True)
    instance = SingleInstance(data)
    if not instance.acquired:
        activate_existing_window()
        instance.close()
        return 0
    server, thread = None, None
    log = None
    try:
        (data / "logs").mkdir(exist_ok=True)
        log = (data / "logs/desktop.log").open("a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stderr = log
        logging.basicConfig(stream=log, level=logging.INFO)
        if not args.data_dir or data == default_data_directory().resolve():
            migrate_legacy_desktop_data(data)
        if args.import_workspace:
            migrate_workspace(args.import_workspace, data)
        configure_environment(data)
        from services.desktop_browser_runtime import initialize_browser_runtime
        runtime = initialize_browser_runtime()
        logging.info("Collector runtime initialized: %s", runtime)
        from sau_backend import app
        from services.geo_test_reports import list_saved_reports
        from services.desktop_geo_jobs import DesktopGeoJobs
        import webview
        from webview.menu import Menu, MenuAction, MenuSeparator

        app.config["GEO_TEST_REPORTS_DIRECTORY"] = data / "geo-tests"
        app.config["DESKTOP_MODE"] = True
        server = create_desktop_server(app, data)
        url = f"http://127.0.0.1:{server.server_port}/#/geo-tests?desktop=1"
        reports = list_saved_reports(data / "geo-tests")
        selected = next((item for item in reports if item["status"] == "completed"), None)
        if selected:
            url += "&report=" + selected["id"]
        thread = threading.Thread(target=server.serve_forever, daemon=True, name="GEO desktop backend")
        thread.start()
        webview.settings["ALLOW_DOWNLOADS"] = True
        webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True
        jobs = DesktopGeoJobs(data)
        window = webview.create_window(WINDOW_TITLE, url=url, width=1380, height=920,
                                       min_size=(920, 640), text_select=True, js_api=jobs)
        window.events.closed += jobs.shutdown
        menu = [Menu("软件", [MenuAction("打开数据文件夹", lambda: os.startfile(data)),
                            MenuSeparator(), MenuAction("退出", window.destroy)])]
        icon = Path(__file__).resolve().parent / "desktop/app.ico"
        start_options = {"gui": "edgechromium", "private_mode": False,
                         "storage_path": str(data / "webview"), "menu": menu,
                         "localization": {"global.quitConfirmation": "确定关闭软件？", "global.ok": "确定", "global.cancel": "取消"}}
        if icon.is_file():
            start_options["icon"] = str(icon)
        if args.smoke_test:
            webview.start(lambda: run_smoke_check(window, data, url), **start_options)
        else:
            webview.start(**start_options)
        if args.smoke_test:
            return 0 if json.loads((data / "logs/smoke-test.json").read_text(encoding="utf-8"))["passed"] else 1
        return 0
    except Exception:
        logging.exception("GEO desktop startup failed")
        if os.name == "nt" and not args.smoke_test:
            ctypes.windll.user32.MessageBoxW(None, f"软件未能启动，请查看日志：\n{data / 'logs/desktop.log'}", WINDOW_TITLE, 0x10)
        return 1
    finally:
        if server:
            if thread and thread.is_alive():
                server.shutdown()
            server.server_close()
        if thread:
            thread.join(timeout=3)
        instance.close()
        if log:
            log.flush()


if __name__ == "__main__":
    raise SystemExit(main())
