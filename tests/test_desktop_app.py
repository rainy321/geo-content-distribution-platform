import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from desktop_app import create_desktop_server, migrate_workspace


class TestDesktopMigration(unittest.TestCase):
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
