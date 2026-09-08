import unittest
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
        self.original_testing = app.testing
        self.original_bilibili_runtime = app.config["ENABLE_BILIBILI_RUNTIME"]
        app.config["TESTING"] = True
        app.config["ENABLE_BILIBILI_RUNTIME"] = False
        self.client = app.test_client()
        with active_queues_lock:
            active_queues.clear()

    def tearDown(self):
        with active_queues_lock:
            active_queues.clear()
        app.config["TESTING"] = self.original_testing
        app.config["ENABLE_BILIBILI_RUNTIME"] = self.original_bilibili_runtime

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

    def test_bilibili_login_is_blocked_before_thread_and_queue_registration(self):
        with patch("sau_backend.threading.Thread") as thread:
            response = self.client.get(
                "/login",
                query_string={"type": 6, "id": "bilibili-account"},
            )

        self.assertEqual(response.status_code, 403)
        self.assertIn("ENABLE_BILIBILI_RUNTIME", response.get_json()["msg"])
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


if __name__ == "__main__":
    unittest.main()
