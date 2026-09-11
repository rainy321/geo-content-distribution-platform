from __future__ import annotations

import unittest

from services.platform_page_risk_service import (
    PlatformPageAccountRiskError,
    detect_visible_platform_account_risk,
    raise_if_visible_platform_account_risk,
)


class SyntheticNode:
    def __init__(self, text="", *, visible=True):
        self.text = text
        self.visible = visible

    async def is_visible(self):
        return self.visible

    async def inner_text(self):
        return self.text


class SyntheticLocator:
    def __init__(self, nodes=None):
        self.nodes = list(nodes or [])

    async def count(self):
        return len(self.nodes)

    def nth(self, index):
        return self.nodes[index]


class SyntheticPage:
    def __init__(self, *, title="", body="", locators=None):
        self._title = title
        self._body = body
        self._locators = dict(locators or {})

    async def title(self):
        return self._title

    def locator(self, selector):
        return SyntheticLocator(self._locators.get(selector, []))

    async def inner_text(self, selector):
        if selector != "body":
            raise AssertionError("only body is supported by this synthetic page")
        return self._body


class PlatformPageRiskServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_full_page_ban_is_extracted_when_editor_is_absent(self):
        page = SyntheticPage(
            title="快手创作者服务平台",
            body="账号已被封禁，该账号因违规操作已受到限制。",
        )

        risk = await detect_visible_platform_account_risk(page, platform_name="快手")

        self.assertEqual(risk["category"], "permanent_ban")
        self.assertEqual(risk["source"], "full_page_body")

    async def test_article_copy_cannot_be_mistaken_for_account_ban(self):
        page = SyntheticPage(
            body="本文解释账号已被封禁时如何申诉。",
            locators={
                'textarea, [contenteditable="true"], input[type="text"]': [
                    SyntheticNode("本文解释账号已被封禁时如何申诉。")
                ]
            },
        )

        risk = await detect_visible_platform_account_risk(page, platform_name="知乎")

        self.assertIsNone(risk)

    async def test_visible_verification_dialog_raises_actionable_error(self):
        page = SyntheticPage(
            locators={
                '[role="dialog"]': [SyntheticNode("请接收短信验证码完成本人验证")],
                'textarea, [contenteditable="true"], input[type="text"]': [
                    SyntheticNode("article editor")
                ],
            }
        )

        with self.assertRaises(PlatformPageAccountRiskError) as context:
            await raise_if_visible_platform_account_risk(
                page,
                platform_name="小红书",
            )

        self.assertIn("verification_required", str(context.exception))
        self.assertIn("账号本人完成验证", str(context.exception))


if __name__ == "__main__":
    unittest.main()
