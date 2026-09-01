import unittest
from unittest.mock import AsyncMock, Mock

from uploader.sohu_uploader.main import SoHuArticle


class SohuArticleBlockingStateTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.article = SoHuArticle(
            title="GEO 内容生成测试说明",
            body="正文",
            tags=[],
            publish_date=0,
            account_file="account.json",
            dry_run=True,
        )

    async def test_exact_unverified_message_is_a_hard_publish_block(self):
        page = Mock()
        page.inner_text = AsyncMock(return_value="未实名暂无法发布文章 去实名认证")

        result = await self.article._detect_blocking_state(page)

        self.assertIn("禁止发布文章", result)
        self.assertIn("人工完成实名认证", result)

    async def test_generic_verification_banner_is_not_a_hard_block(self):
        page = Mock()
        page.inner_text = AsyncMock(return_value="实名认证")
        page.evaluate = AsyncMock(return_value=False)

        result = await self.article._detect_blocking_state(page)

        self.assertEqual(result, "")


if __name__ == "__main__":
    unittest.main()
