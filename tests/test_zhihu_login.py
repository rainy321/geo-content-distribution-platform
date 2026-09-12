import json
import inspect
import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing
from pathlib import Path
from queue import Queue
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from db.createTable import initialize_database
from myUtils.login import _queue_zhihu_login_error, zhihu_cookie_gen
from uploader.zhihu_uploader.main import (
    ZHIHU_LOGIN_TIMEOUT_MS,
    ZhihuLoginControlError,
    _goto_zhihu_page,
    _has_authenticated_session,
    _is_login_url,
    _is_trusted_zhihu_url,
    _wait_until_logged_in,
)


class _FakeResponse:
    def __init__(self, *, ok=True, payload=None):
        self.ok = ok
        self.payload = payload if payload is not None else {}
        self.disposed = False

    async def json(self):
        return self.payload

    async def dispose(self):
        self.disposed = True


class _FakeRequest:
    def __init__(self, response):
        self.response = response
        self.calls = []

    async def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


class _FakeContext:
    def __init__(self, *, cookies=None, response=None):
        self._cookies = list(cookies or [])
        self.cookie_calls = 0
        self.request = _FakeRequest(response or _FakeResponse(ok=False))

    async def cookies(self):
        self.cookie_calls += 1
        return self._cookies


class _FakeLocator:
    def __init__(self, count):
        self._count = count

    async def count(self):
        return self._count


class _FakePage:
    def __init__(
        self,
        urls,
        *,
        goto_error=None,
        ready_state="complete",
        body_count=1,
        control_count=1,
        closed=False,
        clock=None,
    ):
        self.urls = list(urls)
        self.index = 0
        self.goto_error = goto_error
        self.ready_state = ready_state
        self.body_count = body_count
        self.control_count = control_count
        self.closed = closed
        self.clock = clock
        self.brought_to_front = False

    @property
    def url(self):
        return self.urls[self.index]

    async def goto(self, *_args, **_kwargs):
        if self.goto_error:
            raise self.goto_error

    def is_closed(self):
        return self.closed

    async def evaluate(self, _expression):
        return self.ready_state

    def locator(self, selector):
        return _FakeLocator(
            self.body_count if selector == "body" else self.control_count
        )

    async def bring_to_front(self):
        self.brought_to_front = True

    async def wait_for_timeout(self, milliseconds):
        if self.clock is not None:
            self.clock.now += milliseconds / 1000
        if self.index < len(self.urls) - 1:
            self.index += 1


class _FakeClock:
    def __init__(self):
        self.now = 0.0

    def time(self):
        return self.now


def _protocol_queue():
    status_queue = Queue()
    status_queue.protocol_version = 2
    return status_queue


class ZhihuLoginSignalTests(unittest.IsolatedAsyncioTestCase):
    def test_manual_login_default_is_ten_minutes(self):
        timeout_default = inspect.signature(
            _wait_until_logged_in
        ).parameters["timeout_ms"].default

        self.assertEqual(ZHIHU_LOGIN_TIMEOUT_MS, 600_000)
        self.assertEqual(timeout_default, ZHIHU_LOGIN_TIMEOUT_MS)

    def test_url_classification_rejects_lookalikes_and_query_false_positives(self):
        self.assertTrue(_is_login_url("https://www.zhihu.com/signin?next=%2F"))
        self.assertTrue(_is_login_url("https://passport.zhihu.com/"))
        self.assertFalse(
            _is_login_url("https://www.zhihu.com/creator?next=/signin")
        )
        self.assertTrue(_is_trusted_zhihu_url("https://zhuanlan.zhihu.com/write"))
        self.assertFalse(_is_trusted_zhihu_url("https://zhihu.com.example/write"))

    async def test_auth_cookie_is_a_strong_signal_without_identity_request(self):
        context = _FakeContext(
            cookies=[
                {
                    "name": "z_c0",
                    "value": "authenticated",
                    "domain": ".zhihu.com",
                    "expires": -1,
                }
            ]
        )
        page = _FakePage(["https://www.zhihu.com/signin"])

        result = await _has_authenticated_session(
            page,
            context,
            probe_identity=True,
        )

        self.assertTrue(result)
        self.assertEqual(context.request.calls, [])

    async def test_auth_cookie_rejects_empty_value_untrusted_domain_and_expiry(self):
        page = _FakePage(["https://www.zhihu.com/creator"])
        invalid_cookies = (
            {
                "name": "z_c0",
                "value": "",
                "domain": ".zhihu.com",
                "expires": -1,
            },
            {
                "name": "z_c0",
                "value": "authenticated",
                "domain": ".zhihu.com.example",
                "expires": -1,
            },
            {
                "name": "z_c0",
                "value": "authenticated",
                "domain": ".zhihu.com",
                "expires": 1,
            },
        )

        for cookie in invalid_cookies:
            with self.subTest(cookie=cookie):
                context = _FakeContext(cookies=[cookie])
                self.assertFalse(
                    await _has_authenticated_session(page, context)
                )

    async def test_auth_cookie_is_not_read_until_page_host_is_trusted(self):
        context = _FakeContext(
            cookies=[
                {
                    "name": "z_c0",
                    "value": "authenticated",
                    "domain": ".zhihu.com",
                    "expires": -1,
                }
            ]
        )
        page = _FakePage(["https://attacker.example/creator"])

        self.assertFalse(await _has_authenticated_session(page, context))
        self.assertEqual(context.cookie_calls, 0)

    async def test_identity_endpoint_requires_account_token(self):
        valid_response = _FakeResponse(payload={"url_token": "verified-member"})
        context = _FakeContext(response=valid_response)
        page = _FakePage(["https://www.zhihu.com/creator"])

        valid = await _has_authenticated_session(
            page,
            context,
            probe_identity=True,
        )

        self.assertTrue(valid)
        self.assertTrue(valid_response.disposed)

        guest_context = _FakeContext(response=_FakeResponse(payload={"name": ""}))
        self.assertFalse(
            await _has_authenticated_session(
                page,
                guest_context,
                probe_identity=True,
            )
        )

    async def test_identity_endpoint_is_not_called_for_untrusted_host(self):
        context = _FakeContext(
            response=_FakeResponse(payload={"url_token": "should-not-count"})
        )
        page = _FakePage(["https://zhihu.com.example/creator"])

        result = await _has_authenticated_session(
            page,
            context,
            probe_identity=True,
        )

        self.assertFalse(result)
        self.assertEqual(context.request.calls, [])

    async def test_navigation_timeout_continues_for_interactive_trusted_page(self):
        page = _FakePage(
            ["https://www.zhihu.com/signin"],
            goto_error=PlaywrightTimeoutError("timed out"),
            ready_state="loading",
            control_count=2,
        )

        completed = await _goto_zhihu_page(page, "https://www.zhihu.com/signin")

        self.assertFalse(completed)
        self.assertTrue(page.brought_to_front)

    async def test_navigation_timeout_still_fails_for_untrusted_or_blank_page(self):
        page = _FakePage(
            ["about:blank"],
            goto_error=PlaywrightTimeoutError("timed out"),
        )

        with self.assertRaises(PlaywrightTimeoutError):
            await _goto_zhihu_page(page, "https://www.zhihu.com/signin")

    async def test_login_wait_uses_identity_after_leaving_login_page(self):
        context = _FakeContext(
            response=_FakeResponse(payload={"url_token": "verified-member"})
        )
        page = _FakePage(
            [
                "https://www.zhihu.com/signin",
                "https://www.zhihu.com/creator",
            ]
        )

        result = await _wait_until_logged_in(page, context, timeout_ms=2000)

        self.assertTrue(result)
        self.assertEqual(len(context.request.calls), 1)

    async def test_slow_identity_probe_cannot_extend_wall_clock_deadline(self):
        clock = _FakeClock()
        context = _FakeContext()
        page = _FakePage(
            [
                "https://www.zhihu.com/signin",
                "https://www.zhihu.com/creator",
            ],
            clock=clock,
        )
        probe_timeouts = []

        async def authenticate(
            _page,
            _context,
            *,
            probe_identity=False,
            identity_timeout_ms=10_000,
        ):
            if probe_identity:
                probe_timeouts.append(identity_timeout_ms)
                clock.now += identity_timeout_ms / 1000
            return False

        with patch(
            "uploader.zhihu_uploader.main.asyncio.get_running_loop",
            return_value=clock,
        ), patch(
            "uploader.zhihu_uploader.main._has_authenticated_session",
            new=AsyncMock(side_effect=authenticate),
        ):
            result = await _wait_until_logged_in(
                page,
                context,
                timeout_ms=2000,
            )

        self.assertFalse(result)
        self.assertEqual(clock.now, 2.0)
        self.assertGreaterEqual(len(probe_timeouts), 1)
        self.assertLessEqual(max(probe_timeouts), 1000)

    async def test_login_wait_honors_operator_cancellation(self):
        context = _FakeContext()
        page = _FakePage(["https://www.zhihu.com/signin"])
        cancel_event = threading.Event()
        cancel_event.set()

        with self.assertRaises(ZhihuLoginControlError) as raised:
            await _wait_until_logged_in(
                page,
                context,
                cancel_event=cancel_event,
            )

        self.assertEqual(raised.exception.login_error_code, "operator_cancelled")

    async def test_login_wait_reports_closed_browser_without_waiting(self):
        context = _FakeContext()
        page = _FakePage(["https://www.zhihu.com/signin"], closed=True)

        with self.assertRaises(ZhihuLoginControlError) as raised:
            await _wait_until_logged_in(page, context)

        self.assertEqual(raised.exception.login_error_code, "browser_closed")


class ZhihuWebLoginTests(unittest.IsolatedAsyncioTestCase):
    def _playwright_context(self, launch):
        runtime = SimpleNamespace(chromium=SimpleNamespace(launch=launch))
        manager = AsyncMock()
        manager.__aenter__.return_value = runtime
        manager.__aexit__.return_value = None
        return manager

    async def test_safe_structured_error_precedes_legacy_terminal_token(self):
        status_queue = _protocol_queue()
        _queue_zhihu_login_error(status_queue, "browser_launch_failed")

        payload = json.loads(status_queue.get_nowait())
        self.assertEqual(payload["type"], "error")
        self.assertEqual(payload["code"], "browser_launch_failed")
        self.assertNotIn("Traceback", payload["message"])
        self.assertEqual(status_queue.get_nowait(), "500")

    async def test_legacy_protocol_receives_only_terminal_token(self):
        status_queue = Queue()

        _queue_zhihu_login_error(status_queue, "browser_launch_failed")

        self.assertEqual(status_queue.get_nowait(), "500")
        self.assertTrue(status_queue.empty())

    async def test_browser_launch_failure_is_logged_and_returned_without_details(self):
        status_queue = _protocol_queue()
        launch = AsyncMock(side_effect=RuntimeError("private diagnostic"))
        manager = self._playwright_context(launch)

        with patch("myUtils.login.async_playwright", return_value=manager), patch(
            "utils.log.zhihu_logger"
        ) as logger:
            result = await zhihu_cookie_gen("account-secret", status_queue)

        self.assertIsNone(result)
        events = [status_queue.get_nowait() for _ in range(3)]
        error = json.loads(events[1])
        self.assertEqual(json.loads(events[0])["type"], "stage")
        self.assertEqual(error["code"], "browser_launch_failed")
        self.assertNotIn("private diagnostic", error["message"])
        self.assertNotIn("account-secret", json.dumps(events, ensure_ascii=False))
        self.assertEqual(events[2], "500")
        logger.exception.assert_called_once()

    async def test_verified_live_session_is_saved_without_redundant_second_browser(self):
        with tempfile.TemporaryDirectory() as temp_directory:
            root = Path(temp_directory)
            database_path = root / "geo.db"
            cookies_directory = root / "accounts"
            initialize_database(database_path)

            page = SimpleNamespace()
            context = SimpleNamespace()
            browser = SimpleNamespace()
            browser.new_context = AsyncMock(return_value=context)
            browser.close = AsyncMock()
            context.new_page = AsyncMock(return_value=page)
            context.close = AsyncMock()

            async def save_storage(*, path):
                Path(path).write_text(
                    '{"cookies":[{"name":"z_c0"}]}',
                    encoding="utf-8",
                )

            context.storage_state = AsyncMock(side_effect=save_storage)
            launch = AsyncMock(return_value=browser)
            manager = self._playwright_context(launch)
            status_queue = _protocol_queue()

            login_wait = AsyncMock(return_value=True)
            with patch(
                "myUtils.login.async_playwright", return_value=manager
            ), patch(
                "myUtils.login.set_init_script",
                new=AsyncMock(return_value=context),
            ), patch(
                "uploader.zhihu_uploader.main._goto_zhihu_page",
                new=AsyncMock(return_value=True),
            ), patch(
                "uploader.zhihu_uploader.main._wait_until_logged_in",
                new=login_wait,
            ), patch(
                "myUtils.login.check_cookie",
                new=AsyncMock(side_effect=AssertionError("must not run")),
            ) as redundant_check:
                result = await zhihu_cookie_gen(
                    "知乎主号",
                    status_queue,
                    database_path=database_path,
                    cookies_directory=cookies_directory,
                )

            self.assertTrue(result)
            redundant_check.assert_not_awaited()
            login_wait.assert_awaited_once_with(
                page,
                context,
                timeout_ms=ZHIHU_LOGIN_TIMEOUT_MS,
                cancel_event=None,
            )
            with closing(sqlite3.connect(database_path)) as connection:
                account = connection.execute(
                    "SELECT type, filePath, userName, status FROM user_info"
                ).fetchone()
            self.assertEqual(account[0], 9)
            self.assertEqual(account[2:], ("知乎主号", 1))
            self.assertTrue((cookies_directory / account[1]).is_file())
            events = []
            while not status_queue.empty():
                events.append(status_queue.get_nowait())
            self.assertEqual(json.loads(events[0])["stage"], "opening_browser")
            self.assertEqual(events[1], "MANUAL_LOGIN")
            self.assertEqual(
                json.loads(events[2])["stage"],
                "waiting_for_account",
            )
            self.assertEqual(events[-1], "200")
            self.assertEqual(json.loads(events[-2])["type"], "success")

    async def test_cancel_after_identity_check_prevents_session_write(self):
        page = SimpleNamespace()
        context = SimpleNamespace()
        browser = SimpleNamespace()
        browser.new_context = AsyncMock(return_value=context)
        browser.close = AsyncMock()
        context.new_page = AsyncMock(return_value=page)
        context.close = AsyncMock()
        context.storage_state = AsyncMock()
        launch = AsyncMock(return_value=browser)
        manager = self._playwright_context(launch)
        status_queue = _protocol_queue()
        status_queue.cancel_event = threading.Event()

        async def finish_login_then_cancel(*_args, **_kwargs):
            status_queue.cancel_event.set()
            return True

        with patch(
            "myUtils.login.async_playwright", return_value=manager
        ), patch(
            "myUtils.login.set_init_script",
            new=AsyncMock(return_value=context),
        ), patch(
            "uploader.zhihu_uploader.main._goto_zhihu_page",
            new=AsyncMock(return_value=True),
        ), patch(
            "uploader.zhihu_uploader.main._wait_until_logged_in",
            new=AsyncMock(side_effect=finish_login_then_cancel),
        ):
            result = await zhihu_cookie_gen("cancelled-account", status_queue)

        self.assertIsNone(result)
        context.storage_state.assert_not_awaited()
        events = []
        while not status_queue.empty():
            events.append(status_queue.get_nowait())
        error = next(
            json.loads(event)
            for event in events
            if event.startswith("{") and json.loads(event).get("type") == "error"
        )
        self.assertEqual(error["code"], "operator_cancelled")
        self.assertEqual(events[-1], "500")

    async def test_relogin_updates_existing_row_and_removes_old_cookie(self):
        with tempfile.TemporaryDirectory() as temp_directory:
            root = Path(temp_directory)
            database_path = root / "geo.db"
            cookies_directory = root / "accounts"
            cookies_directory.mkdir()
            initialize_database(database_path)
            old_cookie = cookies_directory / "old-session.json"
            old_cookie.write_text("{}", encoding="utf-8")
            with closing(sqlite3.connect(database_path)) as connection:
                existing_account_id = connection.execute(
                    """
                    INSERT INTO user_info (type, filePath, userName, status)
                    VALUES (?, ?, ?, ?)
                    """,
                    (9, old_cookie.name, "知乎主号", 0),
                ).lastrowid
                connection.commit()

            page = SimpleNamespace()
            context = SimpleNamespace()
            browser = SimpleNamespace()
            browser.new_context = AsyncMock(return_value=context)
            browser.close = AsyncMock()
            context.new_page = AsyncMock(return_value=page)
            context.close = AsyncMock()

            async def save_storage(*, path):
                Path(path).write_text(
                    '{"cookies":[{"name":"z_c0"}]}',
                    encoding="utf-8",
                )

            context.storage_state = AsyncMock(side_effect=save_storage)
            manager = self._playwright_context(AsyncMock(return_value=browser))
            status_queue = _protocol_queue()
            status_queue.existing_account_id = existing_account_id

            with patch(
                "myUtils.login.async_playwright", return_value=manager
            ), patch(
                "myUtils.login.set_init_script",
                new=AsyncMock(return_value=context),
            ), patch(
                "uploader.zhihu_uploader.main._goto_zhihu_page",
                new=AsyncMock(return_value=True),
            ), patch(
                "uploader.zhihu_uploader.main._wait_until_logged_in",
                new=AsyncMock(return_value=True),
            ):
                result = await zhihu_cookie_gen(
                    "知乎主号",
                    status_queue,
                    database_path=database_path,
                    cookies_directory=cookies_directory,
                )

            self.assertTrue(result)
            with closing(sqlite3.connect(database_path)) as connection:
                accounts = connection.execute(
                    """
                    SELECT id, filePath, userName, status, last_checked_at,
                           credential_epoch
                    FROM user_info
                    """
                ).fetchall()
            self.assertEqual(len(accounts), 1)
            self.assertEqual(accounts[0][0], existing_account_id)
            self.assertNotEqual(accounts[0][1], old_cookie.name)
            self.assertEqual(accounts[0][2:4], ("知乎主号", 1))
            self.assertTrue(accounts[0][4])
            self.assertEqual(accounts[0][5], 2)
            self.assertFalse(old_cookie.exists())
            self.assertTrue((cookies_directory / accounts[0][1]).is_file())

    async def test_relogin_update_miss_rolls_back_and_removes_new_cookie(self):
        with tempfile.TemporaryDirectory() as temp_directory:
            root = Path(temp_directory)
            database_path = root / "geo.db"
            cookies_directory = root / "accounts"
            cookies_directory.mkdir()
            initialize_database(database_path)
            old_cookie = cookies_directory / "old-session.json"
            old_cookie.write_text("{}", encoding="utf-8")
            with closing(sqlite3.connect(database_path)) as connection:
                existing_account_id = connection.execute(
                    """
                    INSERT INTO user_info (type, filePath, userName, status)
                    VALUES (?, ?, ?, ?)
                    """,
                    (9, old_cookie.name, "知乎主号", 0),
                ).lastrowid
                # Deterministically simulate a row disappearing between the
                # binding read and conditional update.  The transaction must
                # roll this trigger-side deletion back with the failed update.
                connection.execute(
                    f"""
                    CREATE TRIGGER remove_zhihu_before_relogin
                    BEFORE UPDATE OF filePath ON user_info
                    WHEN OLD.id = {int(existing_account_id)}
                    BEGIN
                        DELETE FROM user_info WHERE id = OLD.id;
                    END
                    """
                )
                connection.commit()

            page = SimpleNamespace()
            context = SimpleNamespace()
            browser = SimpleNamespace()
            browser.new_context = AsyncMock(return_value=context)
            browser.close = AsyncMock()
            context.new_page = AsyncMock(return_value=page)
            context.close = AsyncMock()

            async def save_storage(*, path):
                Path(path).write_text(
                    '{"cookies":[{"name":"z_c0"}]}',
                    encoding="utf-8",
                )

            context.storage_state = AsyncMock(side_effect=save_storage)
            manager = self._playwright_context(AsyncMock(return_value=browser))
            status_queue = _protocol_queue()
            status_queue.existing_account_id = existing_account_id

            with patch(
                "myUtils.login.async_playwright", return_value=manager
            ), patch(
                "myUtils.login.set_init_script",
                new=AsyncMock(return_value=context),
            ), patch(
                "uploader.zhihu_uploader.main._goto_zhihu_page",
                new=AsyncMock(return_value=True),
            ), patch(
                "uploader.zhihu_uploader.main._wait_until_logged_in",
                new=AsyncMock(return_value=True),
            ):
                result = await zhihu_cookie_gen(
                    "知乎主号",
                    status_queue,
                    database_path=database_path,
                    cookies_directory=cookies_directory,
                )

            self.assertIsNone(result)
            with closing(sqlite3.connect(database_path)) as connection:
                account = connection.execute(
                    "SELECT id, filePath, status FROM user_info"
                ).fetchone()
            self.assertEqual(account, (existing_account_id, old_cookie.name, 0))
            self.assertEqual(
                sorted(path.name for path in cookies_directory.glob("*.json")),
                [old_cookie.name],
            )
            events = []
            while not status_queue.empty():
                events.append(status_queue.get_nowait())
            error = next(
                json.loads(event)
                for event in events
                if event.startswith("{")
                and json.loads(event).get("type") == "error"
            )
            self.assertEqual(error["code"], "session_save_failed")
            self.assertEqual(events[-1], "500")

    async def test_database_failure_removes_new_unregistered_cookie_file(self):
        with tempfile.TemporaryDirectory() as temp_directory:
            root = Path(temp_directory)
            database_path = root / "database-directory"
            database_path.mkdir()
            cookies_directory = root / "accounts"

            page = SimpleNamespace()
            context = SimpleNamespace()
            browser = SimpleNamespace()
            browser.new_context = AsyncMock(return_value=context)
            browser.close = AsyncMock()
            context.new_page = AsyncMock(return_value=page)
            context.close = AsyncMock()

            async def save_storage(*, path):
                Path(path).write_text(
                    '{"cookies":[{"name":"z_c0"}]}',
                    encoding="utf-8",
                )

            context.storage_state = AsyncMock(side_effect=save_storage)
            launch = AsyncMock(return_value=browser)
            manager = self._playwright_context(launch)
            status_queue = _protocol_queue()

            with patch(
                "myUtils.login.async_playwright", return_value=manager
            ), patch(
                "myUtils.login.set_init_script",
                new=AsyncMock(return_value=context),
            ), patch(
                "uploader.zhihu_uploader.main._goto_zhihu_page",
                new=AsyncMock(return_value=True),
            ), patch(
                "uploader.zhihu_uploader.main._wait_until_logged_in",
                new=AsyncMock(return_value=True),
            ):
                result = await zhihu_cookie_gen(
                    "database-failure-account",
                    status_queue,
                    database_path=database_path,
                    cookies_directory=cookies_directory,
                )

            self.assertIsNone(result)
            self.assertEqual(list(cookies_directory.glob("*.json")), [])
            events = []
            while not status_queue.empty():
                events.append(status_queue.get_nowait())
            error = next(
                json.loads(event)
                for event in events
                if event.startswith("{")
                and json.loads(event).get("type") == "error"
            )
            self.assertEqual(error["code"], "session_save_failed")
            self.assertEqual(events[-1], "500")


if __name__ == "__main__":
    unittest.main()
