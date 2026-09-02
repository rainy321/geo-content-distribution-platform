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

    def test_legacy_ai_declaration_maps_to_current_sohu_label(self):
        article = SoHuArticle(
            title="GEO 内容生成测试说明",
            body="正文",
            tags=[],
            publish_date=0,
            account_file="account.json",
            info_source="包含AI创作内容",
        )

        self.assertEqual(article.info_source, "含有AI生成内容")

    def test_legacy_no_declaration_maps_to_current_sohu_label(self):
        article = SoHuArticle(
            title="GEO 内容生成测试说明",
            body="正文",
            tags=[],
            publish_date=0,
            account_file="account.json",
            info_source="无特别声明",
        )

        self.assertEqual(article.info_source, "无需声明")

    def test_legacy_citation_maps_to_current_required_declaration(self):
        article = SoHuArticle(
            title="GEO 内容生成测试说明",
            body="正文",
            tags=[],
            publish_date=0,
            account_file="account.json",
            info_source="引用声明",
        )

        self.assertEqual(article.info_source, "内容为转载")


if __name__ == "__main__":
    unittest.main()
