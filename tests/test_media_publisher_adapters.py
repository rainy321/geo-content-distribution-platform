import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from services.media_publisher_adapters import (
    BilibiliPublisherAdapter,
    DouyinPublisherAdapter,
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
