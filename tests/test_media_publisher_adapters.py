import asyncio
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from services.media_publisher_adapters import (
    BilibiliPublisherAdapter,
    DouyinPublisherAdapter,
    _bilibili_publish_runner,
)
from services.publisher_adapter import PublishContent


class MediaPublisherAdapterTests(unittest.TestCase):
    def test_image_note_adapter_requires_image_and_never_claims_unknown_success(self):
        calls = []

        async def publish_runner(content, account_file):
            calls.append((content.title, account_file))

        adapter = DouyinPublisherAdapter(
            "account.json",
            login_checker=lambda _account: True,
            publish_runner=publish_runner,
        )
        with self.assertRaisesRegex(ValueError, "至少需要一张图片"):
            adapter.publish(PublishContent("douyin", "标题", "正文"))

        result = adapter.publish(
            PublishContent("douyin", "标题", "正文", images=("cover.png",))
        )
        self.assertEqual(result.status, "processing")
        self.assertFalse(result.success)
        self.assertEqual(calls, [("标题", "account.json")])

    def test_publish_timeout_stays_processing_until_reconciled(self):
        async def slow_publish(*_args):
            await asyncio.sleep(0.2)

        adapter = DouyinPublisherAdapter(
            "account.json",
            login_checker=lambda _account: True,
            publish_runner=slow_publish,
            timeout_seconds=0.05,
        )

        result = adapter.publish(
            PublishContent("douyin", "标题", "正文", images=("cover.png",))
        )

        self.assertEqual(result.status, "processing")
        self.assertFalse(result.success)
        self.assertIn("最终状态未知", result.message)
        self.assertIn("不会自动重试", result.message)

    def test_success_without_public_url_remains_processing(self):
        adapter = DouyinPublisherAdapter(
            "account.json",
            login_checker=lambda _account: True,
            publish_runner=lambda *_args: {
                "status": "success",
                "message": "平台已接受提交",
            },
        )

        result = adapter.publish(
            PublishContent("douyin", "标题", "正文", images=("cover.png",))
        )

        self.assertEqual(result.status, "processing")
        self.assertFalse(result.success)
        self.assertIn("公开链接", result.message)

    def test_success_with_http_url_is_preserved(self):
        adapter = DouyinPublisherAdapter(
            "account.json",
            login_checker=lambda _account: True,
            publish_runner=lambda *_args: {
                "status": "success",
                "url": "https://example.com/post/123",
                "message": "平台已公开",
            },
        )

        result = adapter.publish(
            PublishContent("douyin", "标题", "正文", images=("cover.png",))
        )

        self.assertEqual(result.status, "success")
        self.assertTrue(result.success)
        self.assertEqual(result.url, "https://example.com/post/123")

    def test_video_adapter_requires_video_and_invalid_login_needs_action(self):
        adapter = BilibiliPublisherAdapter(
            "account.json",
            login_checker=lambda _account: False,
            publish_runner=lambda *_args: None,
        )
        with self.assertRaisesRegex(ValueError, "必须提供视频"):
            adapter.publish(PublishContent("bilibili", "标题", "正文"))

        result = adapter.publish(
            PublishContent("bilibili", "标题", "正文", video="demo.mp4")
        )
        self.assertEqual(result.status, "need_action")

    def test_bilibili_rejects_over_limit_snapshot_before_login_or_publish(self):
        calls = []
        adapter = BilibiliPublisherAdapter(
            "account.json",
            login_checker=lambda _account: calls.append("login"),
            publish_runner=lambda *_args: calls.append("publish"),
        )

        with self.assertRaisesRegex(ValueError, "当前 2001 字"):
            adapter.publish(
                PublishContent(
                    "bilibili",
                    "标题",
                    "字" * 2001,
                    video="demo.mp4",
                )
            )
        self.assertEqual(calls, [])

    @patch("uploader.bilibili_uploader.runtime.run_biliup_command")
    def test_bilibili_runner_promotes_strict_bv_output_to_public_success(self, run):
        run.return_value = Mock(
            returncode=0,
            stdout="upload accepted: BV1xJt66fEyq",
            stderr="",
        )

        description = "简介" * 1000
        result = _bilibili_publish_runner(
            PublishContent("bilibili", "标题", description, video="demo.mp4"),
            "account.json",
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual(
            result["url"],
            "https://www.bilibili.com/video/BV1xJt66fEyq",
        )
        _, kwargs = run.call_args
        self.assertEqual(kwargs["timeout_seconds"], 7200)
        arguments = run.call_args.args[0]
        self.assertEqual(arguments[arguments.index("--desc") + 1], description)

    @patch("uploader.bilibili_uploader.runtime.run_biliup_command")
    def test_bilibili_runner_without_bv_requires_reconciliation(self, run):
        run.return_value = Mock(returncode=0, stdout="upload accepted", stderr="")

        result = _bilibili_publish_runner(
            PublishContent("bilibili", "标题", "正文", video="demo.mp4"),
            "account.json",
        )

        self.assertEqual(result["status"], "processing")
        self.assertIn("核对", result["message"])

    @patch("uploader.bilibili_uploader.runtime.run_biliup_command")
    def test_bilibili_cli_failure_never_leaks_raw_output_to_publish_result(self, run):
        secret = "SESSDATA=private-cookie-token"
        local_path = r"C:\\Users\\operator\\account.json"
        run.return_value = Mock(
            returncode=9,
            stdout=f"upload request token={secret}",
            stderr=f"failed reading {local_path}",
        )
        adapter = BilibiliPublisherAdapter(
            local_path,
            login_checker=lambda _account: True,
        )

        result = adapter.publish(
            PublishContent("bilibili", "标题", "正文", video="demo.mp4")
        )

        self.assertEqual(result.status, "processing")
        self.assertFalse(result.success)
        self.assertIn("BILIUP_EXIT_9", result.message)
        self.assertNotIn(secret, result.message)
        self.assertNotIn(local_path, result.message)
        self.assertLess(len(result.message), 220)

    @patch("uploader.bilibili_uploader.runtime.run_biliup_command")
    def test_bilibili_subprocess_exception_never_leaks_command_arguments(self, run):
        secret_body = "正文-secret-api-token"
        local_path = r"C:\\Users\\operator\\account.json"
        run.side_effect = subprocess.TimeoutExpired(
            ["biliup", "-u", local_path, "upload", "demo.mp4", "--desc", secret_body],
            7200,
            output="SESSDATA=private-cookie",
        )
        adapter = BilibiliPublisherAdapter(
            local_path,
            login_checker=lambda _account: True,
        )

        result = adapter.publish(
            PublishContent("bilibili", "标题", secret_body, video="demo.mp4")
        )

        self.assertEqual(result.status, "processing")
        self.assertIn("BILIUP_RUNTIME_ERROR", result.message)
        self.assertNotIn(secret_body, result.message)
        self.assertNotIn(local_path, result.message)
        self.assertNotIn("SESSDATA", result.message)
        self.assertLess(len(result.message), 220)

    @patch("uploader.bilibili_uploader.runtime.run_biliup_command")
    def test_bilibili_login_probe_exception_never_leaks_account_path(self, run):
        local_path = r"C:\\Users\\operator\\private-account.json"
        run.side_effect = OSError(f"cannot execute helper for {local_path}")
        adapter = BilibiliPublisherAdapter(local_path)

        result = adapter.publish(
            PublishContent("bilibili", "标题", "正文", video="demo.mp4")
        )

        self.assertFalse(result.success)
        self.assertIn("BILIUP_LOGIN_CHECK_FAILED", result.message)
        self.assertNotIn(local_path, result.message)
        self.assertLess(len(result.message), 220)

    def test_schedule_is_local_and_does_not_call_platform(self):
        called = []
        adapter = BilibiliPublisherAdapter(
            "account.json",
            login_checker=lambda _account: called.append("login"),
            publish_runner=lambda *_args: called.append("publish"),
        )
        result = adapter.schedule(
            PublishContent("bilibili", "标题", "正文", video="demo.mp4"),
            datetime(2026, 9, 4, tzinfo=timezone.utc),
        )
        self.assertEqual(result.status, "scheduled")
        self.assertEqual(called, [])


if __name__ == "__main__":
    unittest.main()
