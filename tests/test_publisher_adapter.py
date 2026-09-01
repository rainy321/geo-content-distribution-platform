import asyncio
import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from services.publisher_adapter import (
    DemoPublisher,
    PublishContent,
    PublishResult,
    ToutiaoPublisherAdapter,
    ZhihuPublisherAdapter,
    _default_toutiao_publish_runner,
    _default_zhihu_publish_runner,
)


class PublishContentTests(unittest.TestCase):
    def test_normalizes_content_and_rejects_missing_fields(self):
        content = PublishContent(
            platform=" ZHIHU ",
            title="  标题  ",
            content="  正文  ",
            images=[" cover.png ", ""],
            tags="AI",
        )

        self.assertEqual(content.platform, "zhihu")
        self.assertEqual(content.title, "标题")
        self.assertEqual(content.content, "正文")
        self.assertEqual(content.images, ("cover.png",))
        self.assertEqual(content.tags, ("AI",))
        with self.assertRaisesRegex(ValueError, "标题"):
            PublishContent(platform="zhihu", title=" ", content="正文")


class DemoPublisherTests(unittest.TestCase):
    def setUp(self):
        self.content = PublishContent(
            platform="zhihu",
            title="AI Agent 指南",
            content="正文",
        )
        self.publisher = DemoPublisher("zhihu")

    def test_publish_is_clearly_marked_as_demo(self):
        result = self.publisher.publish(self.content)

        self.assertTrue(result.success)
        self.assertEqual(result.status, "success")
        self.assertTrue(result.demo)
        self.assertIn("未访问真实平台", result.message)
        self.assertEqual(result.url, "")
        self.assertEqual(result.to_dict()["platform"], "zhihu")

    def test_schedule_is_demo_only_and_requires_a_time(self):
        publish_at = datetime(2026, 9, 2, 9, 30, tzinfo=timezone.utc)

        result = self.publisher.schedule(self.content, publish_at)

        self.assertEqual(result.status, "scheduled")
        self.assertTrue(result.demo)
        self.assertIn(publish_at.isoformat(), result.message)
        with self.assertRaisesRegex(ValueError, "publish_at"):
            self.publisher.schedule(self.content)


class ZhihuPublisherAdapterTests(unittest.TestCase):
    def setUp(self):
        self.content = PublishContent(
            platform="zhihu",
            title="AI Agent 指南",
            content="正文",
            images=("cover.png",),
            tags=("AI", "Agent"),
        )

    def _adapter(self, **overrides):
        options = {
            "login_checker": lambda _account_file: True,
            "login_runner": lambda _account_file: True,
            "publish_runner": lambda _content, _account_file: None,
            "publication_checker": lambda _title, _account_file, _attempts: None,
            "timeout_seconds": 0.2,
            "login_timeout_seconds": 0.2,
        }
        options.update(overrides)
        return ZhihuPublisherAdapter("account.json", **options)

    def test_invalid_login_requires_action_without_running_publisher(self):
        calls = []
        adapter = self._adapter(
            login_checker=lambda _account_file: False,
            publish_runner=lambda *_args: calls.append("published"),
        )

        result = adapter.publish(self.content)

        self.assertFalse(result.success)
        self.assertEqual(result.status, "need_action")
        self.assertEqual(calls, [])

    def test_explicit_url_or_result_mapping_is_success(self):
        captured = []

        async def publish_runner(content, account_file):
            captured.append((content.title, account_file))
            return {"status": "success", "url": "https://zhuanlan.zhihu.com/p/123"}

        result = self._adapter(publish_runner=publish_runner).publish(self.content)

        self.assertTrue(result.success)
        self.assertEqual(result.status, "success")
        self.assertEqual(result.url, "https://zhuanlan.zhihu.com/p/123")
        self.assertTrue(result.published_at)
        self.assertEqual(captured, [("AI Agent 指南", "account.json")])

    def test_missing_platform_confirmation_remains_processing(self):
        result = self._adapter().publish(self.content)

        self.assertFalse(result.success)
        self.assertEqual(result.status, "processing")
        self.assertIn("未返回最终状态", result.message)

    def test_existing_public_article_skips_duplicate_publish(self):
        calls = []
        adapter = self._adapter(
            publication_checker=lambda *_args: {
                "id": "123",
                "url": "http://zhuanlan.zhihu.com/p/123",
            },
            publish_runner=lambda *_args: calls.append("published"),
        )

        result = adapter.publish(self.content)

        self.assertTrue(result.success)
        self.assertEqual(result.status, "success")
        self.assertEqual(result.url, "https://zhuanlan.zhihu.com/p/123")
        self.assertEqual(calls, [])
        self.assertIn("跳过重复发布", result.message)

    def test_post_publish_public_check_reconciles_processing_result(self):
        checks = []

        def publication_checker(_title, _account_file, attempts):
            checks.append(attempts)
            if attempts == 3:
                return {
                    "id": "456",
                    "url": "https://zhuanlan.zhihu.com/p/456",
                }
            return None

        result = self._adapter(
            publication_checker=publication_checker,
        ).publish(self.content)

        self.assertTrue(result.success)
        self.assertEqual(result.status, "success")
        self.assertEqual(result.url, "https://zhuanlan.zhihu.com/p/456")
        self.assertEqual(checks, [1, 3])

    def test_failed_idempotency_check_blocks_publish(self):
        calls = []

        def fail_check(*_args):
            raise RuntimeError("知乎公开文章查询失败")

        result = self._adapter(
            publication_checker=fail_check,
            publish_runner=lambda *_args: calls.append("published"),
        ).publish(self.content)

        self.assertFalse(result.success)
        self.assertEqual(result.status, "failed")
        self.assertEqual(calls, [])
        self.assertIn("幂等检查失败", result.message)

    def test_manual_intervention_errors_are_classified(self):
        def raise_expired_cookie(*_args):
            raise RuntimeError("cookie 已失效，请重新登录")

        result = self._adapter(publish_runner=raise_expired_cookie).publish(self.content)

        self.assertEqual(result.status, "need_action")
        self.assertIn("cookie 已失效", result.message)

    def test_ordinary_errors_are_failed(self):
        def raise_network_error(*_args):
            raise RuntimeError("连接被重置")

        result = self._adapter(publish_runner=raise_network_error).publish(self.content)

        self.assertEqual(result.status, "failed")
        self.assertIn("连接被重置", result.message)

    def test_timeout_is_failed_without_leaking_exception(self):
        async def slow_publish(*_args):
            await asyncio.sleep(0.1)

        result = self._adapter(
            publish_runner=slow_publish,
            timeout_seconds=0.01,
        ).publish(self.content)

        self.assertEqual(result.status, "failed")
        self.assertIn("超时", result.message)

    def test_schedule_does_not_check_login_or_publish(self):
        calls = []
        publish_at = datetime(2026, 9, 2, 9, 30, tzinfo=timezone.utc)
        adapter = self._adapter(
            login_checker=lambda _account_file: calls.append("checked"),
            publish_runner=lambda *_args: calls.append("published"),
        )

        result = adapter.schedule(self.content, publish_at)

        self.assertTrue(result.success)
        self.assertEqual(result.status, "scheduled")
        self.assertEqual(calls, [])

    def test_rejects_content_for_another_platform(self):
        content = PublishContent(platform="toutiao", title="标题", content="正文")

        with self.assertRaisesRegex(ValueError, "平台"):
            self._adapter().publish(content)

    def test_login_methods_support_async_and_sync_runners(self):
        async def login_checker(_account_file):
            return True

        adapter = self._adapter(
            login_checker=login_checker,
            login_runner=lambda _account_file: True,
        )

        self.assertTrue(adapter.check_login())
        self.assertTrue(adapter.login())

    def test_publish_result_rejects_unknown_status(self):
        with self.assertRaisesRegex(ValueError, "状态"):
            PublishResult(success=False, platform="zhihu", status="unknown")

    @patch("uploader.zhihu_uploader.main.ZhiHuArticle")
    def test_default_runner_uses_supported_ai_creation_statement(self, article_class):
        article = article_class.return_value
        observed = {
            "status": "processing",
            "success": False,
            "message": "已提交，等待平台核验",
        }

        async def run_main():
            await article.click_publish(object())

        article.main = AsyncMock(side_effect=run_main)

        with patch(
            "services.zhihu_publish_flow.click_exact_publish_and_observe",
            new=AsyncMock(return_value=observed),
        ):
            result = asyncio.run(
                _default_zhihu_publish_runner(self.content, "account.json")
            )

        self.assertEqual(
            article_class.call_args.kwargs["creation_statement"],
            "包含 AI 辅助创作 作者对内容负责",
        )
        article.main.assert_awaited_once_with()
        self.assertTrue(callable(article.click_publish))
        self.assertEqual(result, observed)


class ToutiaoPublisherAdapterTests(unittest.TestCase):
    def setUp(self):
        self.content = PublishContent(
            platform="toutiao",
            title="AI Agent 指南",
            content="正文",
            images=("cover.png",),
            tags=("AI", "Agent"),
        )

    def _adapter(self, **overrides):
        options = {
            "login_checker": lambda _account_file: True,
            "login_runner": lambda _account_file: True,
            "publish_runner": lambda _content, _account_file: None,
            "timeout_seconds": 0.2,
            "login_timeout_seconds": 0.2,
        }
        options.update(overrides)
        return ToutiaoPublisherAdapter("account.json", **options)

    def test_invalid_login_requires_action_without_running_publisher(self):
        calls = []
        adapter = self._adapter(
            login_checker=lambda _account_file: False,
            publish_runner=lambda *_args: calls.append("published"),
        )

        result = adapter.publish(self.content)

        self.assertEqual(result.status, "need_action")
        self.assertEqual(calls, [])

    def test_public_url_is_required_for_success(self):
        result = self._adapter(
            publish_runner=lambda *_args: {
                "status": "success",
                "url": "https://www.toutiao.com/article/123/",
                "message": "已公开",
            }
        ).publish(self.content)

        self.assertTrue(result.success)
        self.assertEqual(result.status, "success")
        self.assertEqual(result.url, "https://www.toutiao.com/article/123/")

    def test_success_without_public_url_remains_processing(self):
        result = self._adapter(
            publish_runner=lambda *_args: {
                "status": "success",
                "message": "点击完成",
            }
        ).publish(self.content)

        self.assertFalse(result.success)
        self.assertEqual(result.status, "processing")

    def test_internal_url_is_not_treated_as_public_success(self):
        result = self._adapter(
            publish_runner=lambda *_args: {
                "status": "success",
                "url": "https://mp.toutiao.com/profile_v4/graphic/articles",
            }
        ).publish(self.content)

        self.assertFalse(result.success)
        self.assertEqual(result.status, "processing")

    def test_timeout_remains_processing_to_prevent_blind_retry(self):
        async def slow_publish(*_args):
            await asyncio.sleep(0.1)

        result = self._adapter(
            publish_runner=slow_publish,
            timeout_seconds=0.01,
        ).publish(self.content)

        self.assertEqual(result.status, "processing")
        self.assertIn("不会自动重试", result.message)

    def test_manual_intervention_errors_are_classified(self):
        def blocked(*_args):
            raise RuntimeError("出现验证码，需要人工确认")

        result = self._adapter(publish_runner=blocked).publish(self.content)

        self.assertEqual(result.status, "need_action")

    def test_schedule_does_not_contact_platform(self):
        calls = []
        publish_at = datetime(2026, 9, 2, 9, 30, tzinfo=timezone.utc)
        adapter = self._adapter(
            login_checker=lambda *_args: calls.append("checked"),
            publish_runner=lambda *_args: calls.append("published"),
        )

        result = adapter.schedule(self.content, publish_at)

        self.assertEqual(result.status, "scheduled")
        self.assertEqual(calls, [])

    @patch("uploader.toutiao_uploader.main.TouTiaoArticle")
    def test_default_runner_declares_ai_assistance_and_observes_click(self, article_class):
        article = article_class.return_value
        observed = {
            "status": "processing",
            "success": False,
            "message": "平台已接受提交",
        }

        async def run_main():
            await article.publish(object())

        article.main = AsyncMock(side_effect=run_main)

        with patch(
            "services.toutiao_publish_flow.click_exact_publish_and_observe",
            new=AsyncMock(return_value=observed),
        ):
            result = asyncio.run(
                _default_toutiao_publish_runner(self.content, "account.json")
            )

        self.assertEqual(article_class.call_args.kwargs["work_statements"], ["引用AI"])
        self.assertEqual(article_class.call_args.kwargs["cover_path"], "cover.png")
        article.main.assert_awaited_once_with()
        self.assertEqual(result, observed)


if __name__ == "__main__":
    unittest.main()
