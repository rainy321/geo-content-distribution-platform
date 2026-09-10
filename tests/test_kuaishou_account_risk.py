import unittest
from unittest.mock import AsyncMock, Mock

from uploader.ks_uploader.main import KSBaseUploader


class KuaishouVisibleAccountRiskTests(unittest.IsolatedAsyncioTestCase):
    def _uploader_and_page(self, body_text: str):
        uploader = KSBaseUploader(publish_date=0, account_file="synthetic.json")
        body = Mock()
        body.inner_text = AsyncMock(return_value=body_text)
        page = Mock()
        page.locator.return_value = body
        return uploader, page

    async def test_permanent_ban_stops_before_form_retry(self):
        uploader, page = self._uploader_and_page(
            "账号已被封禁 该账号因违规操作已受到限制，无法访问创作者中心"
        )

        with self.assertRaisesRegex(RuntimeError, r"permanent_ban.*账号已被封禁"):
            await uploader._raise_if_visible_account_risk(page)

        page.locator.assert_called_once_with("body")
        body = page.locator.return_value
        body.inner_text.assert_awaited_once_with(timeout=1500)

    async def test_verification_can_wait_or_be_promoted_to_action_required(self):
        uploader, page = self._uploader_and_page("请接收短信验证码并完成安全验证")

        await uploader._raise_if_visible_account_risk(page)
        with self.assertRaisesRegex(RuntimeError, r"verification_required"):
            await uploader._raise_if_visible_account_risk(
                page,
                include_verification=True,
            )

    async def test_network_copy_does_not_mislabel_account_as_banned(self):
        uploader, page = self._uploader_and_page("当前网络异常，请稍后重试")

        await uploader._raise_if_visible_account_risk(
            page,
            include_verification=True,
        )


if __name__ == "__main__":
    unittest.main()
