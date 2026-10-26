import unittest
from unittest.mock import AsyncMock, Mock

from services.zhihu_publish_flow import click_exact_publish_and_observe


class _CandidateList:
    def __init__(self, candidates):
        self.candidates = candidates

    async def count(self):
        return len(self.candidates)

    def nth(self, index):
        return self.candidates[index]


class _DialogList:
    async def count(self):
        return 0


def _response(url, payload, *, status=200, method='POST'):
    response = Mock()
    response.url = url
    response.status = status
    response.request.method = method
    response.json = AsyncMock(return_value=payload)
    return response


def _page(responses=(), *, url='https://zhuanlan.zhihu.com/p/2098005862069564953/edit'):
    callbacks = {}
    button = Mock()
    button.is_visible = AsyncMock(return_value=True)
    button.is_enabled = AsyncMock(return_value=True)

    async def clicked(**_kwargs):
        for response in responses:
            callbacks['response'](response)

    button.click = AsyncMock(side_effect=clicked)
    page = Mock()
    page.url = url
    page.on.side_effect = lambda name, callback: callbacks.update({name: callback})
    page.get_by_role.return_value = _CandidateList([button])
    page.locator.return_value = _DialogList()
    page.wait_for_timeout = AsyncMock()
    return page, button


class ZhihuPublishFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_requires_one_exact_publish_button(self):
        page = Mock()
        page.get_by_role.return_value = _CandidateList([])
        page.on = Mock()
        page.remove_listener = Mock()

        with self.assertRaisesRegex(RuntimeError, "精确发布按钮数量异常"):
            await click_exact_publish_and_observe(page)

        page.get_by_role.assert_called_once_with(
            "button", name="发布", exact=True
        )

    async def test_exact_click_without_submission_is_not_reported_as_success(self):
        button = Mock()
        button.is_visible = AsyncMock(return_value=True)
        button.is_enabled = AsyncMock(return_value=True)
        button.click = AsyncMock()

        page = Mock()
        page.url = "https://zhuanlan.zhihu.com/write"
        page.get_by_role.return_value = _CandidateList([button])
        page.locator.return_value = _DialogList()
        page.wait_for_timeout = AsyncMock()
        page.on = Mock()
        page.remove_listener = Mock()

        result = await click_exact_publish_and_observe(page)
        self.assertFalse(result['success'])
        self.assertEqual(result['status'], 'processing')
        self.assertIn('尚未观察到明确提交结果', result['message'])

        button.click.assert_awaited_once_with(timeout=5_000)

    async def test_navigation_to_article_is_success_not_context_failure(self):
        button = Mock()
        button.is_visible = AsyncMock(return_value=True)
        button.is_enabled = AsyncMock(return_value=True)
        button.click = AsyncMock()

        page = Mock()
        page.url = "https://zhuanlan.zhihu.com/p/2078070222595035345"
        page.get_by_role.return_value = _CandidateList([button])
        page.wait_for_timeout = AsyncMock(
            side_effect=RuntimeError(
                "Execution context was destroyed, most likely because of a navigation"
            )
        )
        page.wait_for_load_state = AsyncMock()
        page.on = Mock()
        page.remove_listener = Mock()

        result = await click_exact_publish_and_observe(page)

        self.assertTrue(result["success"])
        self.assertEqual(result["status"], "success")
        self.assertEqual(
            result["url"],
            "https://zhuanlan.zhihu.com/p/2078070222595035345",
        )

    async def test_autosaved_real_markdown_draft_and_other_noncanonical_urls_are_not_public(self):
        for url in (
                'https://zhuanlan.zhihu.com/p/2098005862069564953/edit',
                'https://zhuanlan.zhihu.com/p/123abc',
                'https://zhuanlan.zhihu.com/p/123/anything',
                'https://zhuanlan.zhihu.com/p/123?next=publish',
                'https://zhuanlan.zhihu.com/p/123#anything',
                'https://zhuanlan.zhihu.com.evil.invalid/p/123',
                'https://zhuanlan.zhihu.com@evil.invalid/p/123',
                'https://zhuanlan.zhihu.com:8443/p/123',
                'https://evil.invalid/?article=https://zhuanlan.zhihu.com/p/123'):
            with self.subTest(url=url):
                page, button = _page(url=url)
                result = await click_exact_publish_and_observe(page)
                self.assertFalse(result['success'])
                self.assertEqual(result['status'], 'processing')
                self.assertFalse(result.get('url'))
                button.click.assert_awaited_once_with(timeout=5_000)

    async def test_autosave_success_and_explicit_draft_states_never_publish(self):
        canonical = 'https://zhuanlan.zhihu.com/p/2098005862069564953'
        cases = [
            ('https://www.zhihu.com/api/v4/articles/1', {'status': 'success', 'url': canonical}),
            ('https://www.zhihu.com/api/v4/articles/1', {'status': 'success', 'state': 'draft', 'url': canonical}),
            ('https://www.zhihu.com/api/v4/articles/1/publish', {'state': 'draft', 'url': canonical}),
            ('https://www.zhihu.com/api/v4/articles/1/publish', {'publish_status': 'autosaved', 'url': canonical}),
            ('https://www.zhihu.com/api/v4/articles/1/publish', {'data': {'state': 'saved', 'url': canonical}}),
            ('https://www.zhihu.com/api/v4/articles/1/publish', {'is_draft': True, 'url': canonical}),
            ('https://www.zhihu.com/api/v4/articles/1/publish', {'published': False, 'url': canonical}),
            ('https://www.zhihu.com/api/v4/articles/1/publish', {'success': False, 'url': canonical}),
            ('https://www.zhihu.com/api/v4/articles/1/publish', {'error': {'code': 'verify'}, 'url': canonical}),
        ]
        for endpoint, payload in cases:
            with self.subTest(payload=payload):
                page, button = _page([_response(endpoint, payload)])
                result = await click_exact_publish_and_observe(page)
                self.assertEqual(result['status'], 'processing')
                self.assertFalse(result['success'])
                self.assertFalse(result.get('url'))
                button.click.assert_awaited_once_with(timeout=5_000)

    async def test_only_exact_trusted_publish_endpoint_or_explicit_published_state_supplies_url(self):
        canonical = 'https://zhuanlan.zhihu.com/p/2098005862069564953'
        for endpoint in ('https://www.zhihu.com/api/v4/articles/1/unpublish',
                         'https://www.zhihu.com/api/v4/articles/1/publish_preview',
                         'https://www.zhihu.com/api/v4/articles/1/publish/preview',
                         'https://www.zhihu.com/api/v4/articles/1?next=publish',
                         'https://evil.invalid/articles/1/publish',
                         'https://www.zhihu.com.evil.invalid/articles/1/publish'):
            with self.subTest(endpoint=endpoint):
                page, _ = _page([_response(endpoint, {'status': 'success', 'url': canonical})])
                result = await click_exact_publish_and_observe(page)
                self.assertFalse(result['success'])
                self.assertEqual(result['status'], 'processing')
        for endpoint, payload in (
                ('https://www.zhihu.com/api/v4/articles/1/publish', {'data': {'url': canonical}}),
                ('https://www.zhihu.com/api/v4/articles/1', {'data': {'state': 'published', 'url': canonical}})):
            with self.subTest(endpoint=endpoint, payload=payload):
                page, button = _page([_response(endpoint, payload)])
                result = await click_exact_publish_and_observe(page)
                self.assertTrue(result['success'])
                self.assertEqual(result['url'], canonical)
                button.click.assert_awaited_once_with(timeout=5_000)

    async def test_submitted_or_review_pending_does_not_establish_publication(self):
        canonical = 'https://zhuanlan.zhihu.com/p/2098005862069564953'
        for state in ('submitted', 'pending_review', 'under_review', 'processing'):
            with self.subTest(state=state):
                page, _ = _page([_response('https://www.zhihu.com/api/v4/articles/1/publish',
                                         {'state': state, 'url': canonical})])
                result = await click_exact_publish_and_observe(page)
                self.assertFalse(result['success'])
                self.assertEqual(result['status'], 'processing')

    async def test_save_url_is_not_combined_with_later_publish_acceptance(self):
        responses = [
            _response('https://www.zhihu.com/api/v4/articles/1',
                      {'state': 'draft', 'url': 'https://zhuanlan.zhihu.com/p/2098005862069564953'}),
            _response('https://www.zhihu.com/api/v4/articles/1/publish', {'status': 'submitted'}),
        ]
        page, button = _page(responses)
        result = await click_exact_publish_and_observe(page)
        self.assertFalse(result['success'])
        self.assertEqual(result['status'], 'processing')
        self.assertFalse(result.get('url'))
        button.click.assert_awaited_once_with(timeout=5_000)

    async def test_reference_or_draft_url_in_success_payload_is_not_current_public_article(self):
        for payload in (
                {'state': 'published', 'content': 'https://zhuanlan.zhihu.com/p/123'},
                {'state': 'published', 'references': [{'url': 'https://zhuanlan.zhihu.com/p/123'}]},
                {'state': 'published', 'data': {'url': 'https://zhuanlan.zhihu.com/p/2098005862069564953/edit'}}):
            with self.subTest(payload=payload):
                page, _ = _page([_response('https://www.zhihu.com/api/v4/articles/1/publish', payload)])
                result = await click_exact_publish_and_observe(page)
                self.assertFalse(result['success'])
                self.assertEqual(result['status'], 'processing')

    async def test_http_error_or_read_only_response_cannot_supply_publication_proof(self):
        for status, method in ((500, 'POST'), (403, 'POST'), (200, 'GET')):
            with self.subTest(status=status, method=method):
                page, _ = _page([_response('https://www.zhihu.com/api/v4/articles/1/publish',
                    {'state': 'published', 'url': 'https://zhuanlan.zhihu.com/p/123'},
                    status=status, method=method)])
                result = await click_exact_publish_and_observe(page)
                self.assertFalse(result['success'])
                self.assertEqual(result['status'], 'processing')

    async def test_pending_security_confirmation_stays_processing_without_another_publish_click(self):
        page, button = _page()
        dialog = Mock()
        dialog.get_by_role.return_value = _CandidateList([])
        dialogs = Mock()
        dialogs.count = AsyncMock(return_value=1)
        dialogs.last = dialog
        page.locator.return_value = dialogs
        result = await click_exact_publish_and_observe(page)
        self.assertFalse(result['success'])
        self.assertEqual(result['status'], 'processing')
        self.assertIn('待确认', result['message'])
        button.click.assert_awaited_once_with(timeout=5_000)


if __name__ == "__main__":
    unittest.main()
