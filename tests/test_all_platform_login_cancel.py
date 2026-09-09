import json
import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing
from pathlib import Path
from queue import Queue
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from db.createTable import initialize_database
from myUtils.login import (
    _wait_for_interactive_login,
    baijiahao_cookie_gen,
    bilibili_cookie_gen,
    douyin_cookie_gen,
    get_ks_cookie,
    get_tencent_cookie,
    sohu_cookie_gen,
    tiktok_cookie_gen,
    toutiao_cookie_gen,
    xiaohongshu_cookie_gen,
    zhihu_cookie_gen,
)
import uploader.xiaohongshu_uploader.main as xhs_login_module


class _ProtocolQueue(Queue):
    protocol_version = 2

    def __init__(self):
        super().__init__()
        self.cancel_event = threading.Event()


class _PlaywrightManager:
    def __init__(self, playwright):
        self.playwright = playwright

    async def __aenter__(self):
        return self.playwright

    async def __aexit__(self, _exc_type, _exc, _traceback):
        return False


class _Locator:
    @property
    def first(self):
        return self

    def get_by_role(self, *_args, **_kwargs):
        return self

    async def click(self):
        return None

    async def get_attribute(self, _name):
        return "data:image/png;base64,c3ludGhldGlj"


class _Page:
    def __init__(self):
        self.url = "https://synthetic.invalid/login"
        self.closed = False
        self.wait_calls = 0
        self._locator = _Locator()

    async def goto(self, url, **_kwargs):
        self.url = str(url)

    def get_by_role(self, *_args, **_kwargs):
        return self._locator

    def get_by_text(self, *_args, **_kwargs):
        return self._locator

    def frame_locator(self, *_args, **_kwargs):
        return self._locator

    async def wait_for_timeout(self, _milliseconds):
        self.wait_calls += 1

    async def close(self):
        self.closed = True


class _Context:
    def __init__(self, page):
        self.page = page
        self.closed = False
        self.storage_state = AsyncMock()
        self.cookies = AsyncMock(return_value=[])

    async def new_page(self):
        return self.page

    async def close(self):
        self.closed = True


class _Browser:
    def __init__(self, context):
        self.context = context
        self.closed = False

    async def new_context(self):
        return self.context

    async def close(self):
        self.closed = True


def _playwright_manager(browser):
    launch = AsyncMock(return_value=browser)
    playwright = SimpleNamespace(chromium=SimpleNamespace(launch=launch))
    return _PlaywrightManager(playwright)


def _queued_values(status_queue):
    values = []
    while not status_queue.empty():
        values.append(status_queue.get_nowait())
    return values


def _assert_operator_cancelled(test_case, status_queue):
    values = _queued_values(status_queue)
    structured = [
        json.loads(value)
        for value in values
        if isinstance(value, str) and value.startswith("{")
    ]
    test_case.assertTrue(
        any(item.get("code") == "operator_cancelled" for item in structured),
        values,
    )
    test_case.assertEqual(values[-1], "500")


class AllPlatformLoginCancelTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.database_path = self.root / "geo.db"
        self.cookies_directory = self.root / "cookies"
        initialize_database(self.database_path)

    async def asyncTearDown(self):
        self.temp_dir.cleanup()

    def assert_no_accounts(self):
        with closing(sqlite3.connect(self.database_path)) as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM user_info"
            ).fetchone()[0]
        self.assertEqual(count, 0)

    async def test_interactive_wait_honors_cancel_between_polls(self):
        cancel_event = threading.Event()
        page = _Page()
        context = _Context(page)

        async def cancel_during_wait(_milliseconds):
            page.wait_calls += 1
            cancel_event.set()

        page.wait_for_timeout = cancel_during_wait
        result = await _wait_for_interactive_login(
            page,
            context,
            original_url=page.url,
            timeout_seconds=600,
            cancel_event=cancel_event,
        )

        self.assertFalse(result)
        self.assertEqual(page.wait_calls, 1)
        self.assertEqual(context.cookies.await_count, 1)

    async def test_qr_browser_platforms_cancel_before_credential_write(self):
        cases = (
            ("douyin", douyin_cookie_gen),
            ("weixin_channels", get_tencent_cookie),
            ("kuaishou", get_ks_cookie),
        )
        for platform, login in cases:
            with self.subTest(platform=platform):
                status_queue = _ProtocolQueue()
                page = _Page()
                context = _Context(page)
                browser = _Browser(context)

                async def authenticate_then_cancel(*_args, **kwargs):
                    self.assertIs(kwargs["cancel_event"], status_queue.cancel_event)
                    status_queue.cancel_event.set()
                    return True

                with patch(
                    "myUtils.login.async_playwright",
                    return_value=_playwright_manager(browser),
                ), patch(
                    "myUtils.login.set_init_script",
                    new=AsyncMock(return_value=context),
                ), patch(
                    "myUtils.login._wait_for_interactive_login",
                    new=AsyncMock(side_effect=authenticate_then_cancel),
                ), patch(
                    "myUtils.login.check_cookie",
                    new=AsyncMock(),
                ) as check_cookie:
                    result = await login(
                        "synthetic-account",
                        status_queue,
                        database_path=self.database_path,
                        cookies_directory=self.cookies_directory,
                    )

                self.assertIsNone(result)
                context.storage_state.assert_not_awaited()
                check_cookie.assert_not_awaited()
                self.assertTrue(page.closed)
                self.assertTrue(context.closed)
                self.assertTrue(browser.closed)
                self.assert_no_accounts()
                _assert_operator_cancelled(self, status_queue)

    async def test_xiaohongshu_cancel_after_lower_login_removes_new_credential(self):
        status_queue = _ProtocolQueue()
        created_path = None

        async def finish_lower_login_then_cancel(
            account_file, *, headless, cancel_event
        ):
            nonlocal created_path
            self.assertFalse(headless)
            self.assertIs(cancel_event, status_queue.cancel_event)
            created_path = Path(account_file)
            created_path.write_text('{"synthetic":true}', encoding="utf-8")
            status_queue.cancel_event.set()
            return {"success": True, "message": "ok"}

        with patch(
            "uploader.xiaohongshu_uploader.main.xiaohongshu_cookie_gen",
            new=AsyncMock(side_effect=finish_lower_login_then_cancel),
        ):
            result = await xiaohongshu_cookie_gen(
                "synthetic-xhs",
                status_queue,
                database_path=self.database_path,
                cookies_directory=self.cookies_directory,
            )

        self.assertIsNone(result)
        self.assertIsNotNone(created_path)
        self.assertFalse(created_path.exists())
        self.assert_no_accounts()
        _assert_operator_cancelled(self, status_queue)

    async def test_xiaohongshu_lower_wait_honors_cancel_and_closes_browser(self):
        cancel_event = threading.Event()
        page = _Page()
        context = _Context(page)
        browser = _Browser(context)
        qrcode_path = self.root / "synthetic-xhs-qrcode.png"

        async def cancel_during_poll(_seconds):
            cancel_event.set()

        with patch(
            "uploader.xiaohongshu_uploader.main.async_playwright",
            return_value=_playwright_manager(browser),
        ), patch(
            "uploader.xiaohongshu_uploader.main.set_init_script",
            new=AsyncMock(return_value=context),
        ), patch(
            "uploader.xiaohongshu_uploader.main._save_xhs_qrcode",
            new=AsyncMock(
                return_value={
                    "image_path": str(qrcode_path),
                    "image_data_url": "data:image/png;base64,c3ludGhldGlj",
                }
            ),
        ), patch(
            "uploader.xiaohongshu_uploader.main._is_xhs_login_completed",
            new=AsyncMock(return_value=False),
        ), patch(
            "uploader.xiaohongshu_uploader.main.asyncio.sleep",
            new=AsyncMock(side_effect=cancel_during_poll),
        ):
            result = await xhs_login_module.xiaohongshu_cookie_gen(
                self.cookies_directory / "synthetic-xhs.json",
                headless=False,
                cancel_event=cancel_event,
            )

        self.assertEqual(result["status"], "operator_cancelled")
        self.assertTrue(context.closed)
        self.assertTrue(browser.closed)

    async def test_manual_browser_database_failure_removes_unregistered_cookie(self):
        cases = (
            ("baijiahao", baijiahao_cookie_gen, "myUtils.login._wait_for_url_condition"),
            ("toutiao", toutiao_cookie_gen, "myUtils.login.wait_for_toutiao_creator_backend"),
            ("sohu", sohu_cookie_gen, "myUtils.login._wait_for_url_condition"),
        )
        with closing(sqlite3.connect(self.database_path)) as connection:
            connection.execute(
                """
                CREATE TRIGGER reject_synthetic_login
                BEFORE INSERT ON user_info
                BEGIN
                    SELECT RAISE(ABORT, 'synthetic login insert failure');
                END
                """
            )
            connection.commit()

        for platform, login, waiter_patch in cases:
            with self.subTest(platform=platform):
                status_queue = _ProtocolQueue()
                page = _Page()
                context = _Context(page)
                browser = _Browser(context)

                async def write_cookie(*, path):
                    Path(path).write_text('{"synthetic":true}', encoding="utf-8")

                context.storage_state = AsyncMock(side_effect=write_cookie)
                with patch(
                    "myUtils.login.async_playwright",
                    return_value=_playwright_manager(browser),
                ), patch(
                    "myUtils.login.set_init_script",
                    new=AsyncMock(return_value=context),
                ), patch(
                    waiter_patch,
                    new=AsyncMock(return_value=True),
                ), patch(
                    "myUtils.login.check_cookie",
                    new=AsyncMock(return_value=True),
                ):
                    result = await login(
                        f"synthetic-{platform}",
                        status_queue,
                        database_path=self.database_path,
                        cookies_directory=self.cookies_directory,
                    )

                self.assertIsNone(result)
                self.assertEqual(list(self.cookies_directory.glob("*.json")), [])
                self.assertTrue(page.closed)
                self.assertTrue(context.closed)
                self.assertTrue(browser.closed)
                self.assert_no_accounts()

    async def test_manual_browser_cancel_after_insert_rolls_back_and_removes_cookie(self):
        cases = (
            ("baijiahao", baijiahao_cookie_gen, "myUtils.login._wait_for_url_condition"),
            ("toutiao", toutiao_cookie_gen, "myUtils.login.wait_for_toutiao_creator_backend"),
            ("sohu", sohu_cookie_gen, "myUtils.login._wait_for_url_condition"),
        )
        real_connect = sqlite3.connect

        class CancelAfterInsertConnection:
            def __init__(self, connection, cancel_event):
                self.connection = connection
                self.cancel_event = cancel_event

            def execute(self, statement, parameters=()):
                cursor = self.connection.execute(statement, parameters)
                if "INSERT INTO user_info" in statement:
                    self.cancel_event.set()
                return cursor

            def commit(self):
                return self.connection.commit()

            def rollback(self):
                return self.connection.rollback()

            def close(self):
                return self.connection.close()

        for platform, login, waiter_patch in cases:
            with self.subTest(platform=platform):
                status_queue = _ProtocolQueue()
                page = _Page()
                context = _Context(page)
                browser = _Browser(context)

                async def write_cookie(*, path):
                    Path(path).write_text('{"synthetic":true}', encoding="utf-8")

                context.storage_state = AsyncMock(side_effect=write_cookie)

                def connect_and_cancel_after_insert(*args, **kwargs):
                    return CancelAfterInsertConnection(
                        real_connect(*args, **kwargs),
                        status_queue.cancel_event,
                    )

                with patch(
                    "myUtils.login.async_playwright",
                    return_value=_playwright_manager(browser),
                ), patch(
                    "myUtils.login.set_init_script",
                    new=AsyncMock(return_value=context),
                ), patch(
                    waiter_patch,
                    new=AsyncMock(return_value=True),
                ), patch(
                    "myUtils.login.check_cookie",
                    new=AsyncMock(return_value=True),
                ), patch(
                    "myUtils.login.sqlite3.connect",
                    side_effect=connect_and_cancel_after_insert,
                ):
                    result = await login(
                        f"synthetic-{platform}",
                        status_queue,
                        database_path=self.database_path,
                        cookies_directory=self.cookies_directory,
                    )

                self.assertIsNone(result)
                self.assertEqual(list(self.cookies_directory.glob("*.json")), [])
                self.assertTrue(page.closed)
                self.assertTrue(context.closed)
                self.assertTrue(browser.closed)
                self.assert_no_accounts()
                _assert_operator_cancelled(self, status_queue)

    async def test_tiktok_cancel_during_poll_closes_owned_browser(self):
        status_queue = _ProtocolQueue()
        page = _Page()
        context = _Context(page)
        browser = _Browser(context)

        async def cookies_then_cancel():
            status_queue.cancel_event.set()
            return [{"name": "sessionid", "value": "synthetic"}]

        context.cookies = AsyncMock(side_effect=cookies_then_cancel)
        with patch(
            "myUtils.login.async_playwright",
            return_value=_playwright_manager(browser),
        ), patch(
            "myUtils.login.set_init_script",
            new=AsyncMock(return_value=context),
        ), patch(
            "myUtils.login.check_cookie",
            new=AsyncMock(),
        ) as check_cookie:
            result = await tiktok_cookie_gen(
                "synthetic-tiktok",
                status_queue,
                database_path=self.database_path,
                cookies_directory=self.cookies_directory,
            )

        self.assertIsNone(result)
        context.storage_state.assert_not_awaited()
        check_cookie.assert_not_awaited()
        self.assertTrue(page.closed)
        self.assertTrue(context.closed)
        self.assertTrue(browser.closed)
        self.assert_no_accounts()
        self.assertEqual(list(self.cookies_directory.glob("*.json")), [])
        _assert_operator_cancelled(self, status_queue)

    async def test_zhihu_cancel_after_storage_write_removes_credential(self):
        status_queue = _ProtocolQueue()
        page = _Page()
        context = _Context(page)
        browser = _Browser(context)
        created_path = None

        async def save_then_cancel(*, path):
            nonlocal created_path
            created_path = Path(path)
            created_path.write_text('{"synthetic":true}', encoding="utf-8")
            status_queue.cancel_event.set()

        context.storage_state = AsyncMock(side_effect=save_then_cancel)
        with patch(
            "myUtils.login.async_playwright",
            return_value=_playwright_manager(browser),
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
                "synthetic-zhihu",
                status_queue,
                database_path=self.database_path,
                cookies_directory=self.cookies_directory,
            )

        self.assertIsNone(result)
        self.assertIsNotNone(created_path)
        self.assertFalse(created_path.exists())
        self.assertTrue(page.closed)
        self.assertTrue(context.closed)
        self.assertTrue(browser.closed)
        self.assert_no_accounts()
        _assert_operator_cancelled(self, status_queue)

    async def test_bilibili_cancel_after_validation_removes_file_and_stops_only_child(self):
        status_queue = _ProtocolQueue()
        login_process = Mock()
        renew_process = Mock()
        process_states = {
            id(login_process): True,
            id(renew_process): True,
        }
        for child in (login_process, renew_process):
            child.poll.side_effect = (
                lambda owned=child: None if process_states[id(owned)] else -15
            )
            child.terminate.side_effect = (
                lambda owned=child: process_states.__setitem__(id(owned), False)
            )
            child.wait.side_effect = (
                lambda timeout, owned=child: process_states.__setitem__(
                    id(owned), False
                ) or -15
            )
        spawned_file = None
        popen_calls = 0

        def fake_spawn(arguments, **_kwargs):
            nonlocal spawned_file, popen_calls
            popen_calls += 1
            user_flag_index = arguments.index("-u")
            spawned_file = Path(arguments[user_flag_index + 1])
            return login_process if popen_calls == 1 else renew_process

        sleep_calls = 0
        async def create_synthetic_credential(_seconds):
            nonlocal sleep_calls
            sleep_calls += 1
            if sleep_calls == 1:
                spawned_file.write_text('{"synthetic":true}', encoding="utf-8")
            else:
                status_queue.cancel_event.set()

        with patch(
            "uploader.bilibili_uploader.runtime.ensure_biliup_binary",
            return_value=self.root / "synthetic-biliup.exe",
        ), patch(
            "uploader.bilibili_uploader.runtime.spawn_biliup_process",
            side_effect=fake_spawn,
        ) as spawn, patch(
            "myUtils.login.asyncio.sleep",
            new=AsyncMock(side_effect=create_synthetic_credential),
        ):
            result = await bilibili_cookie_gen(
                "synthetic-bilibili",
                status_queue,
                database_path=self.database_path,
                cookies_directory=self.cookies_directory,
            )

        self.assertIsNone(result)
        self.assertEqual(spawn.call_count, 2)
        login_process.terminate.assert_called_once_with()
        login_process.wait.assert_called_once_with(timeout=2)
        login_process.kill.assert_not_called()
        renew_process.terminate.assert_called_once_with()
        renew_process.wait.assert_called_once_with(timeout=2)
        renew_process.kill.assert_not_called()
        self.assertIsNotNone(spawned_file)
        self.assertFalse(spawned_file.exists())
        self.assert_no_accounts()
        _assert_operator_cancelled(self, status_queue)


if __name__ == "__main__":
    unittest.main()
