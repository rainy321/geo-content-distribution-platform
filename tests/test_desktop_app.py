import json
import os
import sqlite3
import tempfile
import asyncio
import threading
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from desktop_app import configure_environment, create_desktop_server, desktop_urls, migrate_workspace, shutdown_desktop_runtime, _paged_history_ready
from desktop_app import main
from services.desktop_geo_jobs import DesktopGeoJobs


class TestRunningDesktopInstallSmoke(unittest.TestCase):
    def test_running_instance_cannot_reuse_an_old_success_receipt_for_an_upgrade_smoke(self):
        with tempfile.TemporaryDirectory() as temporary:
            data=Path(temporary);(data/'logs').mkdir()
            receipt=data/'logs/smoke-test.json';receipt.write_text('{"passed":true,"process_id":123}',encoding='utf-8')
            close=Mock();instance=SimpleNamespace(acquired=False,close=close)
            with patch('desktop_app.SingleInstance',return_value=instance),patch('desktop_app.activate_existing_window') as activate:
                self.assertEqual(main(['--data-dir',str(data),'--smoke-test']),2)
                activate.assert_not_called();close.assert_called_once()
            self.assertEqual(receipt.read_text(encoding='utf-8'),'{"passed":true,"process_id":123}')

    def test_regular_second_launch_still_activates_the_existing_software(self):
        with tempfile.TemporaryDirectory() as temporary:
            instance=SimpleNamespace(acquired=False,close=Mock())
            with patch('desktop_app.SingleInstance',return_value=instance),patch('desktop_app.activate_existing_window') as activate:
                self.assertEqual(main(['--data-dir',temporary]),0)
                activate.assert_called_once();instance.close.assert_called_once()


class TestPagedHistorySmoke(unittest.TestCase):
    def test_paged_history_preserves_total_without_rendering_every_record(self):
        self.assertTrue(_paged_history_ready({'history_rows': 5, 'history_total': 62}, 62))
        self.assertTrue(_paged_history_ready({'history_rows': 20, 'history_total': 62}, 62))
        self.assertTrue(_paged_history_ready({'history_rows': 0, 'history_total': 0}, 0))
        self.assertTrue(_paged_history_ready({'history_rows': 3, 'history_total': 3}, 3))

    def test_missing_records_and_missing_loaded_total_still_fail(self):
        for observed in ({'history_rows': 5, 'history_total': 61}, {'history_rows': 4, 'history_total': 62},
                         {'history_rows': 63, 'history_total': 62}, {'history_rows': 5},
                         {'history_rows': True, 'history_total': 62}):
            self.assertFalse(_paged_history_ready(observed, 62))


class TestDesktopShutdown(unittest.TestCase):
    def test_main_shutdown_persists_cancelled_task_before_stopping_backend(self):
        with tempfile.TemporaryDirectory() as directory:
            jobs = DesktopGeoJobs(Path(directory))
            entered, browser_closed = threading.Event(), threading.Event()
            async def waiting_for_manual_verification():
                entered.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    browser_closed.set()
            jobs._start('plan', waiting_for_manual_verification)
            self.assertTrue(entered.wait(timeout=3))
            observations = []
            server = SimpleNamespace(shutdown=lambda: observations.append((jobs.get_task()['status'], browser_closed.is_set())),
                                     server_close=Mock())
            thread = SimpleNamespace(is_alive=lambda: True, join=Mock())
            shutdown_desktop_runtime(jobs, server, thread)
            self.assertFalse(jobs.worker.is_alive())
            self.assertEqual(observations, [('stopped', True)])
            server.server_close.assert_called_once()
            thread.join.assert_called_once_with(timeout=3)
            restored = DesktopGeoJobs(Path(directory))
            self.assertEqual(restored.get_task()['status'], 'stopped')

    def test_native_shutdown_exception_does_not_skip_backend_cleanup(self):
        jobs = SimpleNamespace(get_task=lambda: {'status': 'running'}, shutdown=Mock(side_effect=RuntimeError('synthetic cleanup failure')))
        server = SimpleNamespace(shutdown=Mock(), server_close=Mock())
        thread = SimpleNamespace(is_alive=lambda: True, join=Mock())
        with self.assertLogs(level='ERROR') as output:
            shutdown_desktop_runtime(jobs, server, thread)
        self.assertIn('saved task requires review', output.output[0])
        server.shutdown.assert_called_once()
        server.server_close.assert_called_once()
        thread.join.assert_called_once_with(timeout=3)


class TestDesktopMigration(unittest.TestCase):
    def test_fresh_desktop_starts_on_journey_without_assuming_saved_report(self):
        with tempfile.TemporaryDirectory() as directory:
            entry, legacy = desktop_urls(5421, Path(directory))
        self.assertEqual(entry, 'http://127.0.0.1:5421/#/journey?desktop=1')
        self.assertEqual(legacy, 'http://127.0.0.1:5421/#/geo-tests?desktop=1')

    def test_previous_report_is_selected_only_when_explicitly_opening_legacy_screen(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, status in [('complete', 'completed'), ('newer-incomplete', 'stopped')]:
                report = root / 'geo-tests' / name / 'report.json'
                report.parent.mkdir(parents=True)
                report.write_text(json.dumps({'schema_version': '1.0', 'kind': 'geo_search_test_report',
                    'profile': {'name': '合成入口验收品牌'}, 'cases': [], 'status': status,
                    'updated_at': '2026-10-19T10:00:00+08:00' if status == 'completed' else '2026-10-20T10:00:00+08:00'}), encoding='utf-8')
            entry, legacy = desktop_urls(5422, root)
            from services.geo_test_reports import read_saved_report
            selected = read_saved_report(root / 'geo-tests', legacy.split('&report=')[1])
        self.assertNotIn('report=', entry)
        self.assertIn('/#/journey?', entry)
        self.assertEqual(selected['status'], 'completed')

    def test_desktop_enables_confirmed_publishing_without_enabling_scheduler(self):
        original_directory = Path.cwd()
        try:
            with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
                configure_environment(Path(directory))
                self.assertEqual(os.environ['ALLOW_REAL_PUBLISHING'], 'true')
                self.assertEqual(os.environ['RUN_PUBLISH_SCHEDULER'], 'false')
                os.chdir(original_directory)
        finally:
            os.chdir(original_directory)

    def test_desktop_preserves_explicit_real_publishing_disable(self):
        original_directory = Path.cwd()
        try:
            with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'ALLOW_REAL_PUBLISHING': 'false'}, clear=True):
                configure_environment(Path(directory))
                self.assertEqual(os.environ['ALLOW_REAL_PUBLISHING'], 'false')
                os.chdir(original_directory)
        finally:
            os.chdir(original_directory)

    @staticmethod
    def _accounts_database(path, accounts):
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute('CREATE TABLE user_info(id INTEGER PRIMARY KEY, type INTEGER, filePath TEXT, status INTEGER, last_checked_at TEXT)')
            connection.executemany('INSERT INTO user_info VALUES (?, ?, ?, ?, ?)', accounts)

    def test_workspace_import_recovers_only_referenced_media_sessions_and_requires_live_check(self):
        with tempfile.TemporaryDirectory() as directory:
            source, target = Path(directory) / 'source', Path(directory) / 'desktop'
            self._accounts_database(source / 'db/database.db', [(1, 7, 'account.json', 1, '2026-09-01 05:52:13')])
            sessions = source / 'cookiesFile'
            sessions.mkdir()
            (sessions / 'account.json').write_text('{"cookies": [], "origins": []}', encoding='utf-8')
            (sessions / 'unreferenced.json').write_text('do not import other accounts', encoding='utf-8')
            result = migrate_workspace(source, target)
            self.assertEqual(result['media_session_files_copied'], 1)
            self.assertTrue(result['media_sessions_require_check'])
            self.assertEqual((target / 'cookies/account.json').read_bytes(), (sessions / 'account.json').read_bytes())
            self.assertFalse((target / 'cookies/unreferenced.json').exists())
            self.assertTrue((sessions / 'account.json').exists())
            with closing(sqlite3.connect(target / 'database.db')) as connection:
                self.assertEqual(connection.execute('SELECT status, last_checked_at FROM user_info').fetchone(), (0, None))

    def test_repeat_import_keeps_newer_session_and_verified_status(self):
        with tempfile.TemporaryDirectory() as directory:
            source, target = Path(directory) / 'source', Path(directory) / 'desktop'
            self._accounts_database(source / 'db/database.db', [(1, 7, 'account.json', 1, None)])
            sessions = source / 'cookiesFile'
            sessions.mkdir()
            (sessions / 'account.json').write_text('old local session', encoding='utf-8')
            migrate_workspace(source, target)
            (target / 'cookies/account.json').write_text('new desktop login', encoding='utf-8')
            with closing(sqlite3.connect(target / 'database.db')) as connection, connection:
                connection.execute("UPDATE user_info SET status=1, last_checked_at='2026-10-19 10:00:00'")
            result = migrate_workspace(source, target)
            self.assertEqual(result['media_session_files_copied'], 0)
            self.assertEqual(result['media_session_files_preserved'], 1)
            self.assertFalse(result['media_sessions_require_check'])
            self.assertEqual((target / 'cookies/account.json').read_text(), 'new desktop login')
            with closing(sqlite3.connect(target / 'database.db')) as connection:
                self.assertEqual(connection.execute('SELECT status, last_checked_at FROM user_info').fetchone(), (1, '2026-10-19 10:00:00'))

    def test_import_does_not_bind_sessions_to_different_desktop_accounts(self):
        with tempfile.TemporaryDirectory() as directory:
            source, target = Path(directory) / 'source', Path(directory) / 'desktop'
            self._accounts_database(source / 'db/database.db', [(1, 7, 'account.json', 1, None)])
            self._accounts_database(target / 'database.db', [(1, 8, 'account.json', 1, None)])
            sessions = source / 'cookiesFile'
            sessions.mkdir()
            (sessions / 'account.json').write_text('session for different platform', encoding='utf-8')
            result = migrate_workspace(source, target)
            self.assertEqual(result['media_session_files_copied'], 0)
            self.assertFalse((target / 'cookies/account.json').exists())

    def test_import_rejects_paths_outside_either_cookie_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            source, target = Path(directory) / 'source', Path(directory) / 'desktop'
            outside = Path(directory) / 'outside.json'
            outside.write_text('unrelated private data', encoding='utf-8')
            self._accounts_database(source / 'db/database.db', [
                (1, 7, '../outside.json', 1, None), (2, 8, str(outside), 1, None)])
            (source / 'cookiesFile').mkdir()
            (source / 'outside.json').write_text('outside source cookie root', encoding='utf-8')
            result = migrate_workspace(source, target)
            self.assertEqual(result['media_session_files_copied'], 0)
            self.assertEqual(result['media_session_files_skipped'], 2)
            self.assertFalse((target / 'outside.json').exists())
            self.assertEqual(outside.read_text(), 'unrelated private data')

    def test_separate_data_directories_cannot_share_the_same_server_port(self):
        with tempfile.TemporaryDirectory() as directory:
            first, second = Path(directory) / 'first', Path(directory) / 'second'
            first.mkdir()
            second.mkdir()
            def app(environ, start_response):
                start_response('200 OK', [('Content-Type', 'text/plain')])
                return [b'local desktop test']
            one = create_desktop_server(app, first)
            self.addCleanup(one.server_close)
            (second / 'desktop-port.json').write_text(json.dumps({'port': one.server_port}), encoding='utf-8')
            two = create_desktop_server(app, second)
            self.addCleanup(two.server_close)
            self.assertNotEqual(one.server_port, two.server_port)

    def test_database_snapshot_and_report_copy_exclude_browser_sessions(self):
        with tempfile.TemporaryDirectory() as directory:
            source, target = Path(directory) / 'source', Path(directory) / 'desktop'
            (source / 'db').mkdir(parents=True)
            with closing(sqlite3.connect(source / 'db/database.db')) as connection, connection:
                connection.execute('CREATE TABLE projects(name TEXT)')
                connection.execute("INSERT INTO projects VALUES ('合成桌面验收客户')")
            report = source / 'runtime/geo-tests/client/run/report.json'
            report.parent.mkdir(parents=True)
            report.write_text('{"fixture":true}', encoding='utf-8')
            cookies = source / 'runtime/doubao/browser-profile/Cookies'
            cookies.parent.mkdir(parents=True)
            cookies.write_text('synthetic cookie must remain outside the desktop import')
            result = migrate_workspace(source, target)
            self.assertTrue(result['database_copied'])
            self.assertEqual(result['report_files_copied'], 1)
            self.assertFalse((target / 'runtime/doubao').exists())
            self.assertFalse(result['browser_sessions_copied'])
            with closing(sqlite3.connect(target / 'database.db')) as connection:
                self.assertEqual(connection.execute('SELECT name FROM projects').fetchone()[0], '合成桌面验收客户')

    def test_reimport_preserves_existing_desktop_database_and_report(self):
        with tempfile.TemporaryDirectory() as directory:
            source, target = Path(directory) / 'source', Path(directory) / 'desktop'
            report = source / 'runtime/geo-tests/client/run/report.json'
            report.parent.mkdir(parents=True)
            report.write_text('{"source":true}', encoding='utf-8')
            migrate_workspace(source, target)
            imported = target / 'geo-tests/client/run/report.json'
            imported.write_text('{"edited_on_desktop":true}', encoding='utf-8')
            result = migrate_workspace(source, target)
            self.assertEqual(result['report_files_copied'], 0)
            self.assertEqual(json.loads(imported.read_text()), {'edited_on_desktop': True})
