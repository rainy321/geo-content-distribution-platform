import ast
import inspect
import sys
import unittest
from queue import Queue
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import myUtils.login as login_module
from myUtils.login import (
    _safe_login_log,
    toutiao_cookie_gen,
    wait_for_toutiao_creator_backend,
)


class _StdoutBackedLogger:
    def log(self, _level, message):
        sys.stdout.write(message)


class _EncodingFailingStdout:
    encoding = "gbk"

    def write(self, _value):
        raise UnicodeEncodeError("gbk", "\u2705", 0, 1, "illegal multibyte sequence")

    def flush(self):
        return None


class _RecordingLogger:
    def __init__(self):
        self.messages = []

    def log(self, level, message):
        self.messages.append((level, message))


class _FakePlaywrightContext:
    def __init__(self, playwright):
        self.playwright = playwright

    async def __aenter__(self):
        return self.playwright

    async def __aexit__(self, _exc_type, _exc, _traceback):
        return False


class _FakePage:
    def __init__(self, urls):
        self._urls = list(urls)
        self._index = 0
        self.wait_calls = []

    @property
    def url(self):
        return self._urls[self._index]

    async def wait_for_timeout(self, milliseconds):
        self.wait_calls.append(milliseconds)
        if self._index < len(self._urls) - 1:
            self._index += 1


class ToutiaoLoginWaiterTests(unittest.IsolatedAsyncioTestCase):
    def test_manual_login_default_is_ten_minutes(self):
        waiter_timeout = inspect.signature(
            wait_for_toutiao_creator_backend
        ).parameters["timeout_seconds"].default
        login_timeout = inspect.signature(toutiao_cookie_gen).parameters[
            "timeout_seconds"
        ].default

        self.assertEqual(waiter_timeout, 600)
        self.assertEqual(login_timeout, 600)

    async def test_detects_spa_transition_without_wait_for_url(self):
        page = _FakePage(
            [
                "https://mp.toutiao.com/auth/page/login",
                "https://mp.toutiao.com/profile_v4/index",
            ]
        )

        result = await wait_for_toutiao_creator_backend(
            page,
            timeout_seconds=1,
            poll_interval_seconds=0.01,
        )

        self.assertTrue(result)
        self.assertEqual(len(page.wait_calls), 1)

    async def test_rejects_login_url_that_merely_contains_profile_text(self):
        page = _FakePage(
            ["https://mp.toutiao.com/auth/page/login?next=/profile_v4/index"]
        )

        result = await wait_for_toutiao_creator_backend(
            page,
            timeout_seconds=0,
        )

        self.assertFalse(result)
        self.assertEqual(page.wait_calls, [])

    async def test_validates_wait_configuration(self):
        page = _FakePage(["https://mp.toutiao.com/auth/page/login"])

        with self.assertRaisesRegex(ValueError, "等待时间"):
            await wait_for_toutiao_creator_backend(
                page,
                timeout_seconds=-1,
            )


class LoginDiagnosticSafetyTests(unittest.IsolatedAsyncioTestCase):
    def test_safe_log_tolerates_missing_stdout(self):
        logger = _StdoutBackedLogger()

        with patch("utils.log.login_logger", logger), patch.object(
            sys, "stdout", None
        ):
            _safe_login_log("toutiao", "browser_launch_started")

    def test_safe_log_tolerates_stdout_encoding_failure(self):
        logger = _StdoutBackedLogger()

        with patch("utils.log.login_logger", logger), patch.object(
            sys, "stdout", _EncodingFailingStdout()
        ):
            _safe_login_log("toutiao", "browser_launch_started")

    def test_safe_log_is_ascii_and_does_not_render_exception_or_account_data(self):
        logger = _RecordingLogger()
        error = RuntimeError("token=secret-cookie")

        with patch("utils.log.login_logger", logger):
            _safe_login_log(
                "\u4eca日头条\U0001f680",
                "started account=customer-name",
                level="error",
                error=error,
            )

        self.assertEqual(len(logger.messages), 1)
        level, message = logger.messages[0]
        self.assertEqual(level, "ERROR")
        self.assertTrue(message.isascii())
        self.assertIn("error_type=runtimeerror", message)
        self.assertNotIn("customer-name", message)
        self.assertNotIn("secret-cookie", message)

    def test_login_module_has_no_direct_print_calls(self):
        tree = ast.parse(inspect.getsource(login_module))
        print_calls = [
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "print"
        ]

        self.assertEqual(print_calls, [])

    async def test_toutiao_reaches_browser_launch_when_stdout_encoding_fails(self):
        launch = AsyncMock(side_effect=RuntimeError("controlled launch failure"))
        playwright = SimpleNamespace(chromium=SimpleNamespace(launch=launch))
        status_queue = Queue()

        with patch(
            "myUtils.login.async_playwright",
            return_value=_FakePlaywrightContext(playwright),
        ), patch(
            "utils.log.login_logger", _StdoutBackedLogger()
        ), patch.object(
            sys, "stdout", _EncodingFailingStdout()
        ):
            result = await toutiao_cookie_gen(
                "account-name-must-not-be-logged",
                status_queue,
            )

        self.assertIsNone(result)
        launch.assert_awaited_once()
        self.assertEqual(status_queue.get_nowait(), "MANUAL_LOGIN")
        self.assertEqual(status_queue.get_nowait(), "500")
        self.assertTrue(status_queue.empty())


if __name__ == "__main__":
    unittest.main()
