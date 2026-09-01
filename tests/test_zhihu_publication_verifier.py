import unittest

from services.zhihu_publication_verifier import (
    _normalize_article,
    _safe_get,
)


class ZhihuPublicationVerifierTests(unittest.TestCase):
    def test_normalizes_article_url_to_https(self):
        article = _normalize_article(
            {
                "id": "2078070222595035345",
                "title": "测试标题",
                "url": "http://zhuanlan.zhihu.com/p/2078070222595035345",
                "created": 123,
            }
        )

        self.assertEqual(
            article["url"],
            "https://zhuanlan.zhihu.com/p/2078070222595035345",
        )
        self.assertEqual(article["title"], "测试标题")

    def test_builds_url_when_api_omits_it(self):
        article = _normalize_article({"id": "123", "title": "测试"})

        self.assertEqual(article["url"], "https://zhuanlan.zhihu.com/p/123")


class ZhihuPublicationVerifierAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_network_error_is_sanitized(self):
        class LeakyRequestContext:
            async def get(self, *_args, **_kwargs):
                raise RuntimeError("cookie: private-session-token-must-not-appear")

        with self.assertRaisesRegex(RuntimeError, "网络异常：RuntimeError") as caught:
            await _safe_get(
                LeakyRequestContext(),
                "https://www.zhihu.com/api/v4/me",
                stage="身份接口",
            )

        self.assertNotIn("private-session-token", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
