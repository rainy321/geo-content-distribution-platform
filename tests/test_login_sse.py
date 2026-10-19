import unittest
import sqlite3
import tempfile
from pathlib import Path
from queue import Queue
from unittest.mock import patch

from sau_backend import (
    active_queues,
    active_queues_lock,
    app,
    sse_stream,
)


class LoginSseTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.original_database = app.config['DATABASE_PATH']
        app.config['DATABASE_PATH'] = Path(self.directory.name) / 'accounts.db'
        with sqlite3.connect(app.config['DATABASE_PATH']) as conn:
            conn.execute('CREATE TABLE user_info(id INTEGER PRIMARY KEY, type INTEGER, userName TEXT)')
            conn.execute("INSERT INTO user_info VALUES (7,7,'saved remark')")
        self.original_testing = app.testing
        app.config["TESTING"] = True
        self.client = app.test_client()
        with active_queues_lock:
            active_queues.clear()

    def tearDown(self):
        with active_queues_lock:
            active_queues.clear()
        app.config["TESTING"] = self.original_testing
        app.config['DATABASE_PATH'] = self.original_database
        self.directory.cleanup()

    def test_stream_stops_and_cleans_after_terminal_event(self):
        status_queue = Queue()
        status_queue.put("MANUAL_LOGIN")
        status_queue.put("200")
        cleanup_calls = []

        events = list(
            sse_stream(
                status_queue,
                on_close=lambda: cleanup_calls.append("closed"),
            )
        )

        self.assertEqual(events, ["data: MANUAL_LOGIN\n\n", "data: 200\n\n"])
        self.assertEqual(cleanup_calls, ["closed"])

    def test_stream_cleans_when_consumer_disconnects(self):
        status_queue = Queue()
        status_queue.put("MANUAL_LOGIN")
        cleanup_calls = []
        stream = sse_stream(
            status_queue,
            on_close=lambda: cleanup_calls.append("closed"),
        )

        self.assertEqual(next(stream), "data: MANUAL_LOGIN\n\n")
        stream.close()

        self.assertEqual(cleanup_calls, ["closed"])

    def test_login_validates_platform_and_account_name_before_starting_thread(self):
        invalid_platform = self.client.get("/login", query_string={"type": 99, "id": "a"})
        missing_account = self.client.get("/login", query_string={"type": 1})

        self.assertEqual(invalid_platform.status_code, 400)
        self.assertEqual(missing_account.status_code, 400)
        with active_queues_lock:
            self.assertEqual(active_queues, {})

    def test_duplicate_platform_account_session_is_rejected_without_overwrite(self):
        session_key = ("1", "same-account")
        existing_queue = Queue()
        with active_queues_lock:
            active_queues[session_key] = existing_queue

        response = self.client.get(
            "/login",
            query_string={"type": 1, "id": "same-account"},
        )

        self.assertEqual(response.status_code, 409)
        with active_queues_lock:
            self.assertIs(active_queues[session_key], existing_queue)

    def test_login_route_cleans_registered_session_after_terminal_event(self):
        class ImmediateTerminalThread:
            def __init__(self, *, target, args, daemon):
                self.status_queue = args[2]

            def start(self):
                self.status_queue.put("200")

        with patch("sau_backend.threading.Thread", ImmediateTerminalThread):
            response = self.client.get(
                "/login",
                query_string={"type": 1, "id": "finished-account"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_data(as_text=True), "data: 200\n\n")
        with active_queues_lock:
            self.assertEqual(active_queues, {})

    def test_reconnect_validates_id_and_platform_before_starting_browser(self):
        with patch('sau_backend.threading.Thread') as thread:
            for value in ('', '0', '-1', 'bad', '9' * 40):
                response = self.client.get('/login', query_string={'type': 7, 'account_id': value})
                self.assertEqual(response.status_code, 400)
            missing = self.client.get('/login', query_string={'type': 7, 'account_id': 999})
            wrong_platform = self.client.get('/login', query_string={'type': 8, 'account_id': 7})
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(wrong_platform.status_code, 409)
        thread.assert_not_called()

    def test_reconnect_uses_existing_row_and_remark_when_name_omitted(self):
        captured = []
        class ImmediateTerminalThread:
            def __init__(self, *, target, args, daemon):
                captured.append(args)
                self.status_queue = args[2]
            def start(self):
                self.status_queue.put('200')
        with patch('sau_backend.threading.Thread', ImmediateTerminalThread):
            response = self.client.get('/login', query_string={'type': 7, 'account_id': 7})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_data(as_text=True), 'data: 200\n\n')
        self.assertEqual(captured[0][:2], ('7', 'saved remark'))
        self.assertEqual(captured[0][3], 7)

    def test_reconnect_duplicate_guard_uses_account_id_even_when_remark_changes(self):
        with active_queues_lock:
            active_queues[('7', 'account:7')] = Queue()
        with patch('sau_backend.threading.Thread') as thread:
            response = self.client.get('/login', query_string={'type': 7, 'account_id': 7, 'id': 'changed remark'})
        self.assertEqual(response.status_code, 409)
        thread.assert_not_called()


if __name__ == "__main__":
    unittest.main()
