import unittest
from unittest.mock import AsyncMock, Mock

from uploader.toutiao_uploader.main import TouTiaoArticle


class ToutiaoArticleCoverTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.publisher = TouTiaoArticle(
            title="测试标题",
            body="测试正文",
            tags=[],
            publish_date=0,
            account_file="unused.json",
            cover_path=None,
        )

    async def test_no_cover_explicitly_selects_no_cover_mode(self):
        page = Mock()
        self.publisher._select_article_cover_mode = AsyncMock(return_value=True)

        await self.publisher.handle_cover(page)

        self.publisher._select_article_cover_mode.assert_awaited_once_with(page, "无封面")

    async def test_no_cover_fails_before_publish_when_mode_cannot_be_selected(self):
        page = Mock()
        self.publisher._select_article_cover_mode = AsyncMock(return_value=False)

        with self.assertRaisesRegex(RuntimeError, "未能选择今日头条「无封面」模式"):
            await self.publisher.handle_cover(page)


if __name__ == "__main__":
    unittest.main()
