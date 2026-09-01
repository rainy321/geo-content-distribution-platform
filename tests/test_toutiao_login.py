import unittest

from myUtils.login import wait_for_toutiao_creator_backend


class _FakePage:
    def __init__(self, urls):
        self._urls = list(urls)
        self._index = 0
        self.wait_calls = []

    @property
    def url(self):
        return self._urls[self._index]

    async def wait_for_timeout(self, milliseconds):
        self.wait_calls.append(milliseconds)
        if self._index < len(self._urls) - 1:
            self._index += 1


class ToutiaoLoginWaiterTests(unittest.IsolatedAsyncioTestCase):
    async def test_detects_spa_transition_without_wait_for_url(self):
        page = _FakePage(
            [
                "https://mp.toutiao.com/auth/page/login",
                "https://mp.toutiao.com/profile_v4/index",
            ]
        )

        result = await wait_for_toutiao_creator_backend(
            page,
            timeout_seconds=1,
            poll_interval_seconds=0.01,
        )

        self.assertTrue(result)
        self.assertEqual(len(page.wait_calls), 1)

    async def test_rejects_login_url_that_merely_contains_profile_text(self):
        page = _FakePage(
            ["https://mp.toutiao.com/auth/page/login?next=/profile_v4/index"]
        )

        result = await wait_for_toutiao_creator_backend(
            page,
            timeout_seconds=0,
        )

        self.assertFalse(result)
        self.assertEqual(page.wait_calls, [])

    async def test_validates_wait_configuration(self):
        page = _FakePage(["https://mp.toutiao.com/auth/page/login"])

        with self.assertRaisesRegex(ValueError, "等待时间"):
            await wait_for_toutiao_creator_backend(
                page,
                timeout_seconds=-1,
            )


if __name__ == "__main__":
    unittest.main()
