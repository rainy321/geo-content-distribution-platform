"""Windows desktop entry point for the GEO workspace."""
from __future__ import annotations

import argparse
import ctypes
import faulthandler
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
from urllib.parse import urlsplit


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


def _migrate_workspace_media_sessions(workspace: Path, destination: Path) -> dict:
    """Import only matching local account files, never whole browser profiles.

    The desktop database may already contain newer accounts. Requiring the same
    account id, platform and relative file path prevents an unrelated source
    account from being connected to an existing desktop account. Imported
    sessions still require a live login check; historical status is not proof.
    """
    summary = {"media_session_files_copied": 0, "media_session_files_preserved": 0,
               "media_session_files_skipped": 0, "media_sessions_require_check": False}
    source_db, target_db = workspace / "db/database.db", destination / "database.db"
    source_root, target_root = (workspace / "cookiesFile").resolve(), (destination / "cookies").resolve()
    if not source_db.is_file() or not target_db.is_file() or not source_root.is_dir():
        return summary
    try:
        with closing(sqlite3.connect(source_db.as_uri() + "?mode=ro", uri=True)) as source:
            source_accounts = set(source.execute("SELECT id, type, filePath FROM user_info"))
        with closing(sqlite3.connect(target_db.as_uri() + "?mode=ro", uri=True)) as target:
            accounts = target.execute("SELECT id, type, filePath FROM user_info").fetchall()
    except sqlite3.OperationalError:
        # Older databases may not contain media accounts yet.
        return summary
    target_root.mkdir(parents=True, exist_ok=True)
    imported = []
    for account_id, platform, raw_path in accounts:
        if (account_id, platform, raw_path) not in source_accounts:
            continue
        relative = Path(str(raw_path or ""))
        source_file, target_file = (source_root / relative).resolve(), (target_root / relative).resolve()
        try:
            if not str(raw_path or "").strip() or relative.is_absolute():
                raise ValueError("Account file must be relative")
            source_file.relative_to(source_root)
            target_file.relative_to(target_root)
            if (source_root / relative).is_symlink() or not source_file.is_file():
                raise ValueError("Account file is unavailable")
        except (OSError, ValueError):
            summary["media_session_files_skipped"] += 1
            continue
        if target_file.exists():
            summary["media_session_files_preserved"] += 1
            continue
        target_file.parent.mkdir(parents=True, exist_ok=True)
        created = False
        try:
            # Exclusive creation preserves a newer login even if another local
            # instance creates its session between the existence check and copy.
            with source_file.open("rb") as source, target_file.open("xb") as target:
                created = True
                shutil.copyfileobj(source, target)
            imported.append((account_id, platform, raw_path))
            summary["media_session_files_copied"] += 1
        except FileExistsError:
            summary["media_session_files_preserved"] += 1
        except OSError:
            if created:
                target_file.unlink(missing_ok=True)
            summary["media_session_files_skipped"] += 1
    if imported:
        with closing(sqlite3.connect(target_db)) as target, target:
            target.executemany(
                "UPDATE user_info SET status = 0, last_checked_at = NULL WHERE id = ? AND type = ? AND filePath = ?",
                imported,
            )
        summary["media_sessions_require_check"] = True
    return summary


def migrate_workspace(workspace: Path, destination: Path) -> dict:
    """Import this user's local data; never bundle it or replace desktop data."""
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
    media_sessions = _migrate_workspace_media_sessions(workspace, destination)
    summary = {"source": str(workspace), "database_copied": database_copied,
               "report_files_copied": copied, "browser_sessions_copied": False,
               "browser_profiles_copied": False, **media_sessions}
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
    # Local desktop publishing still requires an explicit confirmation and a
    # verified bound account. Preserve an administrator's explicit disable.
    os.environ.setdefault("ALLOW_REAL_PUBLISHING", "true")
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


def desktop_urls(port: int, data: Path) -> tuple[str, str]:
    """Open the guided workflow; keep report selection on its dedicated route."""
    from services.geo_test_reports import list_saved_reports

    base = f"http://127.0.0.1:{port}/#/"
    legacy = base + "geo-tests?desktop=1"
    selected = next((item for item in list_saved_reports(data / "geo-tests")
                     if item["status"] == "completed"), None)
    if selected:
        legacy += "&report=" + selected["id"]
    return base + "journey?desktop=1", legacy


def shutdown_desktop_runtime(jobs, server, thread) -> None:
    """Finish native work before the interpreter discards daemon threads.

    pywebview dispatches ``closed`` callbacks asynchronously, so cleanup must
    run synchronously after its UI loop returns rather than depend on that
    callback finishing before process exit.
    """
    if jobs:
        try:
            current = jobs.get_task()
            logging.info("Desktop collector shutdown requested: id=%s kind=%s status=%s",
                         current.get('id'), current.get('kind'), current.get('status'))
            jobs.shutdown()
            current = jobs.get_task()
            logging.info("Desktop collector shutdown finished: id=%s status=%s",
                         current.get('id'), current.get('status'))
        except Exception:
            logging.exception('Desktop collector shutdown failed; saved task requires review')
    if server:
        try:
            if thread and thread.is_alive():
                server.shutdown()
        finally:
            server.server_close()
    if thread:
        thread.join(timeout=3)


def _paged_history_ready(observed, expected):
    rows = observed.get('history_rows')
    return (type(rows) is int and type(observed.get('history_total')) is int
            and observed['history_total'] == expected and min(expected, 5) <= rows <= expected)


def run_smoke_check(window, data: Path, url: str) -> None:
    """Exercise the actual embedded window; no provider requests or fixtures."""
    status = {"passed": False, "embedded_window": True, "url": url, "mock_api": False,
              "process_id": os.getpid(), "started_at_unix": time.time()}
    try:
        _entry_url, legacy_url = desktop_urls(urlsplit(url).port, data)
        status["legacy_report_url"] = legacy_url
        journey_observed = {}
        for _ in range(60):
            try:
                journey_observed = window.evaluate_js("""JSON.stringify({
                    heading: document.querySelector('[data-testid="geo-journey"] h1')?.textContent,
                    steps: [...document.querySelectorAll('[data-testid="geo-journey"] .journey-steps > button')].map(e => e.textContent.trim()),
                    primary_action: !!document.querySelector('[data-testid="journey-primary"]'),
                    native_bridge: ['open_login','plan_with_doubao','start_run','generate_research','get_task','open_destination'].every(name => typeof window.pywebview?.api?.[name] === 'function'),
                    automatic_web_bridge: ['plan_automatic','start_automatic_run'].every(name => typeof window.pywebview?.api?.[name] === 'function'),
                    destination_recheck_bridge: typeof window.pywebview?.api?.check_destination_session === 'function'
                })""")
                journey_observed = json.loads(journey_observed) if isinstance(journey_observed, str) else journey_observed
                steps = journey_observed.get('steps') or []
                if (str(journey_observed.get('heading') or '').strip() and len(steps) == 5
                        and all(label in actual for label, actual in zip(('品牌', '搜索基线', '内容', '发布', '反馈'), steps))
                        and journey_observed.get('primary_action') and journey_observed.get('native_bridge')
                        and journey_observed.get('destination_recheck_bridge') and journey_observed.get('automatic_web_bridge')):
                    status['journey'] = journey_observed
                    status['journey_verified'] = True
                    break
            except Exception:
                pass
            time.sleep(0.5)
        if not status.get('journey_verified'):
            status['last_journey_observed'] = journey_observed
            raise RuntimeError('桌面主流程未加载五个步骤或原生桥接')

        # Verify historical reports after the entry workflow. Selecting a past
        # report belongs to this explicit navigation, not a new user's start.
        window.evaluate_js('window.location.hash = ' + json.dumps(urlsplit(legacy_url).fragment))
        from services.desktop_task_store import DesktopTaskStore
        store = DesktopTaskStore(data)
        expected_history = len(store.records())
        workspace = store.workspace()
        observed = {}
        require_report = '&report=' in legacy_url
        legacy_ready = False
        for _ in range(60):
            try:
                observed = window.evaluate_js("""JSON.stringify({
                    heading: document.querySelector('h1')?.textContent,
                    report: [...document.querySelectorAll('h2')].find(e => e.textContent.includes('采集报告'))?.textContent,
                    rates: [...document.querySelectorAll('.cohorts strong')].map(e => e.textContent),
                    native_bridge: ['start_run','plan_automatic','start_automatic_run'].every(name => typeof window.pywebview?.api?.[name] === 'function')
                    ,history_rows: document.querySelectorAll('[data-testid="desktop-history"] .el-table__row').length
                    ,history_total: (() => { const count = document.querySelector('[data-testid="desktop-history"]')?.getAttribute('data-history-count'); return count !== null && count !== undefined ? Number(count) : null })()
                    ,draft_company: document.querySelector('input[data-testid="company-input"], [data-testid="company-input"] input')?.value
                    ,plan_heading: [...document.querySelectorAll('h2')].find(e => e.textContent.includes('测试计划'))?.textContent
                })""")
                observed = json.loads(observed) if isinstance(observed, str) else observed
                restored_company = "company" not in workspace or observed.get("draft_company") == workspace["company"]
                restored_plan = not workspace.get("plan") or observed.get("plan_heading") == workspace["plan"]["profile"]["name"] + " · 测试计划"
                if observed and observed.get("heading") == "AI 搜索测试" and (not require_report or observed.get("report")) and observed.get("native_bridge") and _paged_history_ready(observed, expected_history) and restored_company and restored_plan:
                    status.update(**observed)
                    status["workspace_restored"] = True
                    status["legacy_report_verified"] = bool(observed.get("report"))
                    status["legacy_report_required"] = require_report
                    legacy_ready = True
                    break
            except Exception:
                pass
            time.sleep(0.5)
        if not legacy_ready:
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
                    await page.locator('[data-testid="geo-journey"] .journey-steps > button').last.wait_for()
                    if await page.locator('[data-testid="geo-journey"] .journey-steps > button').count() != 5:
                        raise RuntimeError('实际浏览器未显示五步主流程')
                    await page.screenshot(path=str(data / 'logs/content-preview.png'))
                    await page.goto(legacy_url)
                    await page.get_by_role('heading', name='AI 搜索测试', exact=True).wait_for()
                    await page.screenshot(path=str(data / 'logs/legacy-report-preview.png'))
                    return ready
                finally:
                    await browser.close()
        import asyncio
        status["collector_engine_ready"] = asyncio.run(check_browser_engine())
        # Media login uses a fresh headed browser, whereas the collector uses
        # persistent contexts. Exercise both installed runtime paths: one can
        # fail even when the other succeeds. This check never opens a platform.
        async def check_media_login_browser():
            from myUtils.login import get_browser_options
            from utils.base_social_media import set_init_script
            async with async_playwright() as playwright:
                options = {**get_browser_options(), 'headless': False}
                browser = await playwright.chromium.launch(**options)
                try:
                    context = await browser.new_context()
                    context = await set_init_script(context)
                    page = await context.new_page()
                    await page.set_content('<title>Synthetic media-login browser check</title>')
                    if await page.title() != 'Synthetic media-login browser check':
                        return False
                    await page.goto(url)
                    await page.locator('[data-testid="geo-journey"] .journey-steps > button').last.wait_for()
                    return await page.locator('[data-testid="geo-journey"] .journey-steps > button').count() == 5
                finally:
                    await browser.close()
        status['media_login_browser_ready'] = asyncio.run(check_media_login_browser())
        if not status['media_login_browser_ready']:
            raise RuntimeError('媒体登录浏览器未就绪')
        # The read-only Zhihu publication precheck uses a separate headless
        # launcher. Exercise its exact options in the packaged runtime too.
        async def check_zhihu_preflight_browser():
            from services.zhihu_publication_verifier import _launch_options
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(**_launch_options())
                try:
                    context = await browser.new_context()
                    try:
                        page = await context.new_page()
                        await page.goto(url)
                        await page.locator('[data-testid="geo-journey"] .journey-steps > button').last.wait_for()
                        return await page.locator('[data-testid="geo-journey"] .journey-steps > button').count() == 5
                    finally:
                        await context.close()
                finally:
                    await browser.close()
        status['zhihu_preflight_browser_ready'] = asyncio.run(check_zhihu_preflight_browser())
        if not status['zhihu_preflight_browser_ready']:
            raise RuntimeError('知乎发文前检查浏览器未就绪')
        async def check_article_browser_options():
            from uploader.baijiahao_uploader.main import _build_launch_kwargs as baijiahao_options
            from uploader.xiaohongshu_uploader.main import _build_launch_kwargs as xiaohongshu_options
            observations = {}
            async with async_playwright() as playwright:
                for platform, options in (('baijiahao', baijiahao_options), ('xiaohongshu', xiaohongshu_options)):
                    browser = await playwright.chromium.launch(**options(headless=True))
                    try:
                        context = await browser.new_context()
                        try:
                            page = await context.new_page()
                            await page.goto(url)
                            await page.locator('[data-testid="geo-journey"] .journey-steps > button').last.wait_for()
                            observations[platform] = await page.locator('[data-testid="geo-journey"] .journey-steps > button').count() == 5
                        finally:
                            await context.close()
                    finally:
                        await browser.close()
            return observations
        status['article_browser_options_ready'] = asyncio.run(check_article_browser_options())
        if not all(status['article_browser_options_ready'].values()):
            raise RuntimeError('文章浏览器启动配置未就绪')
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
        status['passed'] = True
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
        if args.smoke_test:
            instance.close()
            return 2
        activate_existing_window()
        instance.close()
        return 0
    server, thread, jobs = None, None, None
    log = None
    try:
        (data / "logs").mkdir(exist_ok=True)
        log = (data / "logs/desktop.log").open("a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stderr = log
        logging.basicConfig(stream=log, level=logging.INFO,
                            format='%(asctime)s %(levelname)s %(threadName)s %(message)s')
        # Native Windows / CLR faults may bypass Python exception handling.
        # Keep this file open through interpreter exit for a useful traceback.
        faulthandler.enable(file=log, all_threads=True)
        logging.info('Desktop startup pid=%s', os.getpid())
        if not args.data_dir or data == default_data_directory().resolve():
            migrate_legacy_desktop_data(data)
        if args.import_workspace:
            migrate_workspace(args.import_workspace, data)
        configure_environment(data)
        from services.desktop_browser_runtime import initialize_browser_runtime
        runtime = initialize_browser_runtime()
        logging.info("Collector runtime initialized: %s", runtime)
        from sau_backend import app
        from services.desktop_geo_jobs import DesktopGeoJobs
        import webview
        from webview.menu import Menu, MenuAction, MenuSeparator

        app.config["GEO_TEST_REPORTS_DIRECTORY"] = data / "geo-tests"
        app.config["DESKTOP_MODE"] = True
        server = create_desktop_server(app, data)
        url, _legacy_url = desktop_urls(server.server_port, data)
        thread = threading.Thread(target=server.serve_forever, daemon=True, name="GEO desktop backend")
        thread.start()
        webview.settings["ALLOW_DOWNLOADS"] = True
        webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True
        jobs = DesktopGeoJobs(data)
        window = webview.create_window(WINDOW_TITLE, url=url, width=1380, height=920,
                                       min_size=(920, 640), text_select=True, js_api=jobs)
        window.events.closed += lambda: logging.info('Desktop window closed')
        menu = [Menu("软件", [MenuAction("打开数据文件夹", lambda: os.startfile(data)),
                            MenuSeparator(), MenuAction("退出", window.destroy)])]
        icon = Path(__file__).resolve().parent / "desktop/app.ico"
        start_options = {"gui": "edgechromium", "private_mode": False,
                         "storage_path": str(data / "webview"), "menu": menu,
                         "localization": {"global.quitConfirmation": "确定关闭软件？", "global.ok": "确定", "global.cancel": "取消"}}
        if icon.is_file():
            start_options["icon"] = str(icon)
        if args.smoke_test:
            logging.info('Desktop UI loop entering smoke check')
            webview.start(lambda: run_smoke_check(window, data, url), **start_options)
        else:
            logging.info('Desktop UI loop entering')
            webview.start(**start_options)
        logging.info('Desktop UI loop returned')
        if args.smoke_test:
            return 0 if json.loads((data / "logs/smoke-test.json").read_text(encoding="utf-8"))["passed"] else 1
        return 0
    except Exception:
        logging.exception("GEO desktop startup failed")
        if os.name == "nt" and not args.smoke_test:
            ctypes.windll.user32.MessageBoxW(None, f"软件未能启动，请查看日志：\n{data / 'logs/desktop.log'}", WINDOW_TITLE, 0x10)
        return 1
    finally:
        shutdown_desktop_runtime(jobs, server, thread)
        instance.close()
        if log:
            logging.info('Desktop main exiting pid=%s', os.getpid())
            log.flush()


if __name__ == "__main__":
    raise SystemExit(main())
