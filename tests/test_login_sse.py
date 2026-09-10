import asyncio
import http.client
import json
import socket
import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing
from pathlib import Path
from queue import Queue
from unittest.mock import patch

from db.createTable import initialize_database
from sau_backend import (
    LOGIN_EVENT_MAX_COUNT,
    LOGIN_EVENT_MAX_ITEM_BYTES,
    LOGIN_EVENT_MAX_TOTAL_BYTES,
    LOGIN_COMPAT_QUEUE_MAX_COUNT,
    LOGIN_MAX_ACTIVE_STREAMS,
    LOGIN_SESSION_REPLAY_TTL_SECONDS,
    LoginStatusQueue,
    _expire_recent_login_queue,
    _finish_login_session,
    _run_login_coroutine,
    active_account_checks,
    active_login_stream_transports,
    active_queues,
    active_queues_lock,
    app,
    recent_login_queues,
    run_async_function,
    sse_stream,
)


class LoginSseTests(unittest.TestCase):
    def setUp(self):
        self.temp_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_directory.name) / "geo.db"
        initialize_database(self.database_path)
        self.original_testing = app.testing
        self.original_bilibili_runtime = app.config["ENABLE_BILIBILI_RUNTIME"]
        self.original_real_publishing = app.config["ALLOW_REAL_PUBLISHING"]
        self.original_runtime_ready = app.config["PUBLISHING_RUNTIME_READY"]
        self.original_database_path = app.config["DATABASE_PATH"]
        app.config["TESTING"] = True
        app.config["ENABLE_BILIBILI_RUNTIME"] = False
        app.config["ALLOW_REAL_PUBLISHING"] = True
        app.config["PUBLISHING_RUNTIME_READY"] = True
        app.config["DATABASE_PATH"] = self.database_path
        self.client = app.test_client()
        with active_queues_lock:
            active_queues.clear()
            active_account_checks.clear()
            recent_login_queues.clear()
            active_login_stream_transports.clear()

    def tearDown(self):
        with active_queues_lock:
            active_queues.clear()
            active_account_checks.clear()
            recent_login_queues.clear()
            active_login_stream_transports.clear()
        app.config["TESTING"] = self.original_testing
        app.config["ENABLE_BILIBILI_RUNTIME"] = self.original_bilibili_runtime
        app.config["ALLOW_REAL_PUBLISHING"] = self.original_real_publishing
        app.config["PUBLISHING_RUNTIME_READY"] = self.original_runtime_ready
        app.config["DATABASE_PATH"] = self.original_database_path
        self.temp_directory.cleanup()

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

    def test_protocol_two_stream_replays_events_after_cursor(self):
        status_queue = LoginStatusQueue(
            protocol_version=2,
            client_session_id="session_1234567890abcdef",
        )
        status_queue.put("opening")
        status_queue.put("MANUAL_LOGIN")
        status_queue.put("200")

        events = list(sse_stream(status_queue, after_event_id=1))

        self.assertEqual(
            events,
            ["id: 2\ndata: MANUAL_LOGIN\n\n", "id: 3\ndata: 200\n\n"],
        )

    def test_protocol_two_event_journal_has_count_byte_and_item_limits(self):
        status_queue = LoginStatusQueue(
            protocol_version=2,
            client_session_id="bounded_1234567890abcdef",
        )
        for index in range(LOGIN_EVENT_MAX_COUNT + 20):
            status_queue.put(f"log-{index}")

        self.assertLessEqual(len(status_queue._event_log), LOGIN_EVENT_MAX_COUNT)
        self.assertLessEqual(status_queue.qsize(), LOGIN_COMPAT_QUEUE_MAX_COUNT)

        chunk = "x" * (LOGIN_EVENT_MAX_ITEM_BYTES - 1024)
        for index in range(8):
            status_queue.put(f"{index}:{chunk}")
        self.assertLessEqual(
            status_queue._event_log_bytes,
            LOGIN_EVENT_MAX_TOTAL_BYTES,
        )

        status_queue.put("x" * (LOGIN_EVENT_MAX_ITEM_BYTES + 1))
        bounded_payload = json.loads(status_queue._event_log[-1][1])
        self.assertEqual(bounded_payload["code"], "event_payload_too_large")

    def test_completed_protocol_two_replay_expires_without_new_request(self):
        session_key = ("9", "expiring-account")
        session_id = "expiry_1234567890abcdef"
        replay_key = (*session_key, session_id)
        status_queue = LoginStatusQueue(
            protocol_version=2,
            client_session_id=session_id,
        )
        with active_queues_lock:
            active_queues[session_key] = status_queue
        status_queue.put("500")
        with patch("sau_backend._schedule_recent_login_queue_expiry"):
            _finish_login_session(*session_key, status_queue)

        with active_queues_lock:
            self.assertIs(recent_login_queues[replay_key], status_queue)
        _expire_recent_login_queue(
            replay_key,
            status_queue,
            now=status_queue.finished_at + LOGIN_SESSION_REPLAY_TTL_SECONDS + 1,
        )
        with active_queues_lock:
            self.assertNotIn(replay_key, recent_login_queues)

    def test_protocol_two_transport_close_does_not_cancel_active_login(self):
        session_key = ("9", "resilient-account")
        status_queue = LoginStatusQueue(
            protocol_version=2,
            client_session_id="session_1234567890abcdef",
        )
        status_queue.put("MANUAL_LOGIN")
        with active_queues_lock:
            active_queues[session_key] = status_queue

        response = self.client.get(
            "/login",
            query_string={
                "type": 9,
                "id": "resilient-account",
                "protocol": 2,
                "session_id": "session_1234567890abcdef",
            },
            buffered=False,
        )
        self.assertEqual(next(response.response), b"id: 1\ndata: MANUAL_LOGIN\n\n")
        response.close()

        self.assertFalse(status_queue.cancel_event.is_set())
        with active_queues_lock:
            self.assertIs(active_queues[session_key], status_queue)

    def test_protocol_two_reconnect_replaces_old_transport_without_duplicate_worker(self):
        session_key = ("9", "transport-account")
        session_id = "transport_1234567890abcdef"
        status_queue = LoginStatusQueue(
            protocol_version=2,
            client_session_id=session_id,
        )
        status_queue.put("MANUAL_LOGIN")
        with active_queues_lock:
            active_queues[session_key] = status_queue

        with patch("sau_backend.threading.Thread") as thread:
            first = self.client.get(
                "/login",
                query_string={
                    "type": 9,
                    "id": session_key[1],
                    "protocol": 2,
                    "session_id": session_id,
                },
                buffered=False,
            )
            self.assertEqual(
                next(first.response), b"id: 1\ndata: MANUAL_LOGIN\n\n"
            )
            with active_queues_lock:
                first_token = active_login_stream_transports[status_queue]

            with patch.object(
                status_queue,
                "wake_stream_waiters",
                wraps=status_queue.wake_stream_waiters,
            ) as wake_old_transport:
                second = self.client.get(
                    "/login",
                    query_string={
                        "type": 9,
                        "id": session_key[1],
                        "protocol": 2,
                        "session_id": session_id,
                    },
                    buffered=False,
                )
            wake_old_transport.assert_called_once_with()
            self.assertEqual(
                next(second.response), b"id: 1\ndata: MANUAL_LOGIN\n\n"
            )
            with active_queues_lock:
                second_token = active_login_stream_transports[status_queue]

        self.assertIsNot(first_token, second_token)
        thread.assert_not_called()
        with self.assertRaises(StopIteration):
            next(first.response)
        first.close()
        with active_queues_lock:
            self.assertIs(
                active_login_stream_transports[status_queue], second_token
            )
        second.close()
        with active_queues_lock:
            self.assertNotIn(status_queue, active_login_stream_transports)
        self.assertFalse(status_queue.cancel_event.is_set())

    def test_login_stream_limit_preserves_waitress_capacity(self):
        with active_queues_lock:
            for index in range(LOGIN_MAX_ACTIVE_STREAMS):
                queue = LoginStatusQueue(
                    protocol_version=2,
                    client_session_id=f"occupied_{index:02d}_1234567890abcdef",
                )
                active_login_stream_transports[queue] = object()

        with patch("sau_backend.threading.Thread") as thread:
            response = self.client.get(
                "/login",
                query_string={
                    "type": 9,
                    "id": "over-capacity",
                    "protocol": 2,
                    "session_id": "capacity_1234567890abcdef",
                },
            )

        self.assertEqual(response.status_code, 503)
        self.assertIn("登录", response.get_json()["msg"])
        thread.assert_not_called()
        with active_queues_lock:
            self.assertNotIn(("9", "over-capacity"), active_queues)

    def test_protocol_two_reconnect_reuses_worker_and_replays_terminal_result(self):
        session_key = ("9", "replay-account")
        session_id = "session_1234567890abcdef"
        status_queue = LoginStatusQueue(
            protocol_version=2,
            client_session_id=session_id,
        )
        status_queue.put("MANUAL_LOGIN")
        status_queue.put("200")
        _finish_login_session(*session_key, status_queue)

        with patch("sau_backend.threading.Thread") as thread:
            response = self.client.get(
                "/login",
                query_string={
                    "type": 9,
                    "id": "replay-account",
                    "protocol": 2,
                    "session_id": session_id,
                    "last_event_id": 1,
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_data(as_text=True), "id: 2\ndata: 200\n\n")
        thread.assert_not_called()

    def test_protocol_two_reconnect_rejects_different_session_owner(self):
        session_key = ("9", "owned-account")
        status_queue = LoginStatusQueue(
            protocol_version=2,
            client_session_id="session_1234567890abcdef",
        )
        with active_queues_lock:
            active_queues[session_key] = status_queue

        response = self.client.get(
            "/login",
            query_string={
                "type": 9,
                "id": "owned-account",
                "protocol": 2,
                "session_id": "different_1234567890abcdef",
            },
        )

        self.assertEqual(response.status_code, 409)
        self.assertFalse(status_queue.cancel_event.is_set())

        cancel_response = self.client.post(
            "/api/media-login/cancel",
            json={
                "type": 9,
                "id": "owned-account",
                "session_id": "different_1234567890abcdef",
            },
        )
        self.assertEqual(cancel_response.status_code, 409)
        self.assertFalse(status_queue.cancel_event.is_set())

    def test_bilibili_cancel_copy_names_its_owned_scan_window_not_chrome(self):
        status_queue = LoginStatusQueue(
            protocol_version=2,
            client_session_id="bilibili_1234567890abcdef",
        )
        with active_queues_lock:
            active_queues[("6", "bilibili-account")] = status_queue

        with patch.object(
            status_queue.finished_event,
            "wait",
            return_value=False,
        ):
            response = self.client.post(
                "/api/media-login/cancel",
                json={
                    "type": 6,
                    "id": "bilibili-account",
                    "session_id": "bilibili_1234567890abcdef",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn("Bilibili 扫码窗口", response.get_json()["msg"])
        self.assertNotIn("Chrome", response.get_json()["msg"])

    def test_login_validates_platform_and_account_name_before_starting_thread(self):
        invalid_platform = self.client.get("/login", query_string={"type": 99, "id": "a"})
        missing_account = self.client.get("/login", query_string={"type": 1})
        invalid_protocol = self.client.get(
            "/login",
            query_string={"type": 1, "id": "a", "protocol": 99},
        )

        self.assertEqual(invalid_platform.status_code, 400)
        self.assertEqual(missing_account.status_code, 400)
        self.assertEqual(invalid_protocol.status_code, 400)
        with active_queues_lock:
            self.assertEqual(active_queues, {})

    def test_bilibili_login_is_blocked_before_thread_and_queue_registration(self):
        with patch("sau_backend.threading.Thread") as thread:
            response = self.client.get(
                "/login",
                query_string={"type": 6, "id": "bilibili-account"},
            )

        self.assertEqual(response.status_code, 403)
        self.assertIn("Bilibili", response.get_json()["msg"])
        thread.assert_not_called()
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
                self.login_type = args[0]
                self.account_id = args[1]
                self.status_queue = args[2]

            def start(self):
                self.status_queue.put("200")
                _finish_login_session(
                    self.login_type,
                    self.account_id,
                    self.status_queue,
                )

        with patch("sau_backend.threading.Thread", ImmediateTerminalThread):
            response = self.client.get(
                "/login",
                query_string={"type": 1, "id": "finished-account"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_data(as_text=True), "data: 200\n\n")
        with active_queues_lock:
            self.assertEqual(active_queues, {})

    def test_login_first_event_streams_through_real_waitress_http_stack(self):
        """Guard against WSGI hop-by-hop headers rejected by Waitress."""

        from waitress.server import create_server

        worker_finished = threading.Event()

        def synthetic_login(login_type, account_id, status_queue):
            try:
                status_queue.put("MANUAL_LOGIN")
                status_queue.put("500")
            finally:
                _finish_login_session(login_type, account_id, status_queue)
                worker_finished.set()

        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        host, port = listener.getsockname()
        server = create_server(
            app,
            sockets=[listener],
            threads=2,
            ident="GEO Login SSE Test",
        )
        server_thread = threading.Thread(target=server.run, daemon=True)
        connection = http.client.HTTPConnection(host, port, timeout=5)

        try:
            with patch("sau_backend.run_async_function", synthetic_login):
                server_thread.start()
                connection.request(
                    "GET",
                    "/login?type=9&id=waitress-test&protocol=2&session_id=waitress_1234567890abcdef",
                )
                response = connection.getresponse()

                self.assertEqual(response.status, 200)
                self.assertEqual(
                    response.getheader("Content-Type"),
                    "text/event-stream",
                )
                self.assertEqual(
                    response.getheader("Cache-Control"),
                    "private, no-store",
                )
                self.assertEqual(response.readline(), b"id: 1\n")
                self.assertEqual(response.readline(), b"data: MANUAL_LOGIN\n")
                self.assertEqual(response.readline(), b"\n")
                self.assertEqual(response.read(), b"id: 2\ndata: 500\n\n")
                self.assertTrue(worker_finished.wait(timeout=2))
        finally:
            connection.close()
            server.close()
            server.task_dispatcher.shutdown(cancel_pending=True, timeout=5)
            server_thread.join(timeout=5)

        self.assertFalse(server_thread.is_alive())
        with active_queues_lock:
            self.assertEqual(active_queues, {})

    def test_existing_account_binding_mismatch_is_rejected_before_thread(self):
        with closing(sqlite3.connect(self.database_path)) as connection:
            account_id = connection.execute(
                """
                INSERT INTO user_info (type, filePath, userName, status)
                VALUES (?, ?, ?, ?)
                """,
                (9, "old.json", "知乎主号", 1),
            ).lastrowid
            connection.commit()

        for query in (
            {
                "type": 9,
                "id": "不同备注",
                "existing_account_id": account_id,
            },
            {
                "type": 8,
                "id": "知乎主号",
                "existing_account_id": account_id,
            },
            {"type": 9, "id": "知乎主号", "existing_account_id": 99999},
        ):
            with self.subTest(query=query), patch(
                "sau_backend.threading.Thread"
            ) as thread:
                response = self.client.get("/login", query_string=query)
            self.assertEqual(response.status_code, 409)
            thread.assert_not_called()

    def test_kuaishou_existing_account_id_is_rejected_before_thread(self):
        with closing(sqlite3.connect(self.database_path)) as connection:
            account_id = connection.execute(
                """
                INSERT INTO user_info (type, filePath, userName, status)
                VALUES (?, ?, ?, ?)
                """,
                (4, "old-kuaishou.json", "快手主号", 0),
            ).lastrowid
            connection.commit()

        with patch("sau_backend.threading.Thread") as thread:
            response = self.client.get(
                "/login",
                query_string={
                    "type": 4,
                    "id": "快手主号",
                    "existing_account_id": account_id,
                },
            )

        self.assertEqual(response.status_code, 409)
        self.assertIn("先退出并解绑旧账号", response.get_json()["msg"])
        thread.assert_not_called()
        with active_queues_lock:
            self.assertEqual(active_queues, {})

    def test_disconnected_or_checking_existing_account_cannot_start_relogin(self):
        with closing(sqlite3.connect(self.database_path)) as connection:
            disconnected_id = connection.execute(
                """
                INSERT INTO user_info (type, filePath, userName, status)
                VALUES (9, '', '已解绑账号', -2)
                """
            ).lastrowid
            checking_id = connection.execute(
                """
                INSERT INTO user_info (type, filePath, userName, status)
                VALUES (9, 'checking.json', '检测中账号', 1)
                """
            ).lastrowid
            connection.commit()
        with active_queues_lock:
            active_account_checks.add(checking_id)

        for query in (
            {
                "type": 9,
                "id": "已解绑账号",
                "existing_account_id": disconnected_id,
            },
            {
                "type": 9,
                "id": "检测中账号",
                "existing_account_id": checking_id,
            },
        ):
            with self.subTest(query=query), patch(
                "sau_backend.threading.Thread"
            ) as thread:
                response = self.client.get("/login", query_string=query)
            self.assertEqual(response.status_code, 409)
            thread.assert_not_called()

    def test_protocol_and_matching_existing_account_are_bound_to_session(self):
        with closing(sqlite3.connect(self.database_path)) as connection:
            account_id = connection.execute(
                """
                INSERT INTO user_info (type, filePath, userName, status)
                VALUES (?, ?, ?, ?)
                """,
                (9, "old.json", "知乎主号", 1),
            ).lastrowid
            connection.commit()
        captured = {}

        class ImmediateTerminalThread:
            def __init__(self, *, target, args, daemon):
                captured["queue"] = args[2]
                self.args = args

            def start(self):
                self.args[2].put("200")
                _finish_login_session(*self.args)

        with patch(
            "sau_backend.threading.Thread", ImmediateTerminalThread
        ), patch("sau_backend._schedule_recent_login_queue_expiry"):
            response = self.client.get(
                "/login",
                query_string={
                    "type": 9,
                    "id": "知乎主号",
                    "protocol": 2,
                    "session_id": "existing_1234567890abcdef",
                    "existing_account_id": account_id,
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(captured["queue"].protocol_version, 2)
        self.assertEqual(captured["queue"].existing_account_id, account_id)

    def test_protocol_two_requires_owner_session_id(self):
        with patch("sau_backend.threading.Thread") as thread:
            response = self.client.get(
                "/login",
                query_string={"type": 9, "id": "owned-login", "protocol": 2},
            )

        self.assertEqual(response.status_code, 400)
        thread.assert_not_called()

    def test_protocol_two_active_session_cannot_be_cancelled_without_owner_id(self):
        session_key = ("9", "owned-cancel")
        status_queue = LoginStatusQueue(
            protocol_version=2,
            client_session_id="cancel_1234567890abcdef",
        )
        with active_queues_lock:
            active_queues[session_key] = status_queue

        response = self.client.post(
            "/api/media-login/cancel",
            json={"type": 9, "id": "owned-cancel"},
        )

        self.assertEqual(response.status_code, 409)
        self.assertFalse(status_queue.cancel_event.is_set())

    def test_protocol_two_cancel_never_removes_active_before_worker_transfer(self):
        session_key = ("9", "owned-finish-race")
        status_queue = LoginStatusQueue(
            protocol_version=2,
            client_session_id="cancel_race_1234567890abcdef",
        )
        with active_queues_lock:
            active_queues[session_key] = status_queue

        def report_finished_without_registry_transfer(*, timeout):
            self.assertEqual(timeout, 3.0)
            status_queue.put("500")
            status_queue.mark_finished()
            return True

        with patch.object(
            status_queue.finished_event,
            "wait",
            side_effect=report_finished_without_registry_transfer,
        ):
            response = self.client.post(
                "/api/media-login/cancel",
                json={
                    "type": 9,
                    "id": session_key[1],
                    "session_id": status_queue.client_session_id,
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["data"]["active"])
        with active_queues_lock:
            self.assertIs(active_queues[session_key], status_queue)
            self.assertNotIn(
                (*session_key, status_queue.client_session_id),
                recent_login_queues,
            )

        with patch("sau_backend._schedule_recent_login_queue_expiry"):
            _finish_login_session(*session_key, status_queue)
        with active_queues_lock:
            self.assertNotIn(session_key, active_queues)
            self.assertIs(
                recent_login_queues[
                    (*session_key, status_queue.client_session_id)
                ],
                status_queue,
            )

    def test_cancel_endpoint_signals_worker_without_removing_active_session(self):
        session_key = ("9", "zhihu-account")
        status_queue = LoginStatusQueue()
        with active_queues_lock:
            active_queues[session_key] = status_queue

        with patch.object(
            status_queue.finished_event,
            "wait",
            return_value=False,
        ) as wait_for_finish:
            response = self.client.post(
                "/api/media-login/cancel",
                json={"type": 9, "id": "zhihu-account"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["data"]["cancel_requested"])
        self.assertTrue(response.get_json()["data"]["active"])
        self.assertEqual(response.get_json()["data"]["outcome"], "pending")
        wait_for_finish.assert_called_once_with(timeout=3.0)
        self.assertTrue(status_queue.cancel_event.is_set())
        with active_queues_lock:
            self.assertIs(active_queues[session_key], status_queue)

        _finish_login_session(*session_key, status_queue)
        self.assertTrue(status_queue.finished_event.is_set())
        with active_queues_lock:
            self.assertEqual(active_queues, {})

    def test_cancel_endpoint_removes_session_after_bounded_worker_finish(self):
        session_key = ("9", "finishing-account")
        status_queue = LoginStatusQueue()
        with active_queues_lock:
            active_queues[session_key] = status_queue

        def finish_after_cancel(*, timeout):
            self.assertEqual(timeout, 3.0)
            status_queue.put("500")
            status_queue.mark_finished()
            return True

        with patch.object(
            status_queue.finished_event,
            "wait",
            side_effect=finish_after_cancel,
        ):
            response = self.client.post(
                "/api/media-login/cancel",
                json={"type": 9, "id": "finishing-account"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.get_json()["data"],
            {
                "active": False,
                "cancel_requested": True,
                "outcome": "cancelled",
            },
        )
        with active_queues_lock:
            self.assertEqual(active_queues, {})

    def test_cancel_endpoint_is_idempotent_when_session_already_finished(self):
        response = self.client.post(
            "/api/media-login/cancel",
            json={"type": 9, "id": "missing-account"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.get_json()["data"],
            {
                "active": False,
                "cancel_requested": False,
                "outcome": "unknown",
            },
        )

    def test_cancel_endpoint_reports_when_success_won_the_race(self):
        session_key = ("9", "completed-account")
        status_queue = LoginStatusQueue()
        status_queue.put("200")
        status_queue.mark_finished()
        with active_queues_lock:
            active_queues[session_key] = status_queue

        response = self.client.post(
            "/api/media-login/cancel",
            json={"type": 9, "id": "completed-account"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["data"]["outcome"], "success")
        self.assertFalse(response.get_json()["data"]["active"])

    def test_thread_start_failure_marks_finished_and_removes_registered_session(self):
        captured = {}

        class StartFailureThread:
            def __init__(self, *, target, args, daemon):
                captured["queue"] = args[2]

            def start(self):
                raise RuntimeError("synthetic start failure")

        with patch("sau_backend.threading.Thread", StartFailureThread), patch(
            "sau_backend.login_logger"
        ) as logger:
            response = self.client.get(
                "/login",
                query_string={"type": 9, "id": "start-failure-account"},
            )

        self.assertEqual(response.status_code, 500)
        status_queue = captured["queue"]
        self.assertTrue(status_queue.finished_event.is_set())
        self.assertEqual(status_queue.outcome, "failed")
        with active_queues_lock:
            self.assertEqual(active_queues, {})
        logger.exception.assert_called_once()

    def test_login_helper_import_failure_emits_safe_error_and_cleans_registry(self):
        session_key = ("9", "private-account-name")
        status_queue = LoginStatusQueue(protocol_version=2)
        with active_queues_lock:
            active_queues[session_key] = status_queue

        with patch(
            "sau_backend._load_login_helpers",
            side_effect=ImportError("private package path"),
        ), patch("sau_backend.login_logger") as logger:
            run_async_function(*session_key, status_queue)

        stage_error = json.loads(status_queue.get_nowait())
        self.assertEqual(stage_error["type"], "error")
        self.assertEqual(stage_error["code"], "unexpected_error")
        self.assertNotIn("private package path", stage_error["message"])
        self.assertNotIn("private-account-name", json.dumps(stage_error))
        self.assertEqual(status_queue.get_nowait(), "500")
        self.assertTrue(status_queue.finished_event.is_set())
        with active_queues_lock:
            self.assertEqual(active_queues, {})
        logger.exception.assert_called_once()

    def test_login_event_loop_closes_when_helper_raises(self):
        created_loops = []
        original_new_event_loop = asyncio.new_event_loop

        async def failing_helper():
            raise RuntimeError("synthetic helper failure")

        def create_recording_loop():
            loop = original_new_event_loop()
            created_loops.append(loop)
            return loop

        with patch(
            "sau_backend.asyncio.new_event_loop",
            side_effect=create_recording_loop,
        ):
            with self.assertRaisesRegex(RuntimeError, "synthetic helper failure"):
                _run_login_coroutine(failing_helper())

        self.assertEqual(len(created_loops), 1)
        self.assertTrue(created_loops[0].is_closed())


if __name__ == "__main__":
    unittest.main()
