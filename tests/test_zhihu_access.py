"""Offline regression for the exact JSON restriction observed on Zhihu.

All browser and HTTP objects are doubles. No platform request is made.
"""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock, patch

from services import zhihu_publication_verifier as verifier
from services.publisher_adapter import PublishContent, ZhihuPublisherAdapter, classify_publisher_error
from services.zhihu_access import (
    KNOWN_MESSAGE, ZhihuAccessRestrictedError, assert_page_access,
    assert_payload_access, is_access_restricted,
)
from services.zhihu_markdown_editor import import_markdown_body
from uploader.zhihu_uploader import main as uploader


# Same JSON shape and human message as the observed response. The private
# account/request correlation suffix is replaced with a synthetic value.
RESTRICTED = {'error': {'code': 40362, 'message': KNOWN_MESSAGE + '。synthetic-private-trace'}}


def page_double(text=None, *, url=uploader.ZHIHU_WRITE_URL):
    body = SimpleNamespace(inner_text=AsyncMock(return_value=text or json.dumps(RESTRICTED)))
    page = SimpleNamespace(url=url, locator=Mock(return_value=body), goto=AsyncMock(),
                           wait_for_timeout=AsyncMock())
    return page, body


def runtime_double(page=None, request=None):
    context = SimpleNamespace(new_page=AsyncMock(return_value=page), request=request, close=AsyncMock())
    browser = SimpleNamespace(new_context=AsyncMock(return_value=context), close=AsyncMock())
    runtime = MagicMock()
    runtime.__aenter__.return_value = SimpleNamespace(chromium=SimpleNamespace(launch=AsyncMock(return_value=browser)))
    return runtime, browser, context


class ExplicitRestrictionTests(unittest.TestCase):
    def test_observed_json_code_and_exact_message_are_recognized(self):
        for payload in (RESTRICTED, {'error': {'code': '40362'}},
                        {'error': {'message': KNOWN_MESSAGE + '。请稍后查看'}}):
            with self.subTest(payload=payload):
                self.assertTrue(is_access_restricted(payload))
                with self.assertRaises(ZhihuAccessRestrictedError) as caught:
                    assert_payload_access(payload)
                self.assertNotIn('synthetic-private-trace', str(caught.exception))

    def test_generic_403_other_errors_or_article_text_are_not_this_restriction(self):
        for payload in ({'error': {'code': 403, 'message': 'Forbidden'}},
                        {'error': {'code': 401, 'message': 'Unauthorized'}},
                        {'error': {'code': 100, 'message': '其他业务错误'}},
                        {'title': KNOWN_MESSAGE, 'data': []},
                        {'data': [{'error': RESTRICTED['error']}]},
                        {'error': {'code': True}}, None, []):
            with self.subTest(payload=payload):
                self.assertFalse(is_access_restricted(payload))
                assert_payload_access(payload)
        self.assertEqual(classify_publisher_error(ZhihuAccessRestrictedError()), 'need_action')


class CurrentPageRestrictionTests(unittest.IsolatedAsyncioTestCase):
    async def test_current_json_page_is_read_only_and_stops_immediately(self):
        page, body = page_double()
        with self.assertRaises(ZhihuAccessRestrictedError):
            await assert_page_access(page)
        page.locator.assert_called_once_with('body')
        body.inner_text.assert_awaited_once()
        page.goto.assert_not_awaited()
        page.wait_for_timeout.assert_not_awaited()

    async def test_unsupported_page_or_untrusted_url_is_not_misclassified(self):
        for text, url in (('<main>' + KNOWN_MESSAGE + '</main>', uploader.ZHIHU_WRITE_URL),
                          ('not json', uploader.ZHIHU_WRITE_URL),
                          (json.dumps(RESTRICTED), 'https://zhihu.com.example.invalid/write'),
                          (json.dumps(RESTRICTED), 'https://user@www.zhihu.com/write'),
                          (json.dumps(RESTRICTED), 'https://www.zhihu.com:8443/write')):
            with self.subTest(text=text, url=url):
                page, _ = page_double(text, url=url)
                await assert_page_access(page)

    async def test_editor_wait_and_import_stop_before_fields_or_import_actions(self):
        page, _ = page_double()
        article = uploader.ZhiHuArticle('synthetic title', 'body', [], 0, 'unused.json')
        with self.assertRaises(ZhihuAccessRestrictedError):
            await article._wait_for_editor_ready(page)
        self.assertEqual(page.locator.call_args_list[0].args, ('body',))
        self.assertEqual(page.locator.call_count, 1)
        page.locator.reset_mock()
        page.get_by_role = Mock()
        editor = SimpleNamespace(inner_text=AsyncMock())
        with self.assertRaises(ZhihuAccessRestrictedError):
            await import_markdown_body(page, editor, markdown='body', expected_title='synthetic title', manifest=[])
        page.get_by_role.assert_not_called()
        editor.inner_text.assert_not_awaited()

    async def test_cookie_check_never_accepts_restricted_writer_by_domain_fallback(self):
        page, _ = page_double()
        runtime, browser, _ = runtime_double(page=page)
        with patch.object(uploader, 'async_playwright', return_value=runtime), \
                patch.object(uploader, '_build_launch_kwargs', return_value={}):
            with self.assertRaises(ZhihuAccessRestrictedError):
                await uploader.cookie_auth('synthetic-session.json')
        page.goto.assert_awaited_once()
        page.wait_for_timeout.assert_not_awaited()
        browser.close.assert_awaited_once()

    async def test_upload_restriction_preserves_pre_publish_boundary_and_cleanup(self):
        page, _ = page_double()
        runtime, browser, context = runtime_double(page=page)
        playwright = await runtime.__aenter__()
        article = uploader.ZhiHuArticle('synthetic title', 'body', [], 0, 'unused.json')
        article.fill_title, article.fill_body = AsyncMock(), AsyncMock()
        article.click_publish = AsyncMock()
        with self.assertRaises(ZhihuAccessRestrictedError):
            await article.upload(playwright)
        article.fill_title.assert_not_awaited()
        article.fill_body.assert_not_awaited()
        article.click_publish.assert_not_awaited()
        context.close.assert_awaited_once()
        browser.close.assert_awaited_once()


class PublicationRestrictionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.account = Path(self.directory.name) / 'synthetic-session.json'
        self.account.write_text('{}', encoding='utf-8')

    async def _query(self, responses, expected_exception, message=None):
        request = SimpleNamespace(get=AsyncMock(side_effect=responses))
        runtime, browser, context = runtime_double(request=request)
        with patch.object(verifier, 'async_playwright', return_value=runtime), \
                patch.object(verifier, '_launch_options', return_value={}):
            with self.assertRaises(expected_exception) as caught:
                await verifier.find_public_zhihu_article('synthetic title', self.account, 3, interval_seconds=0)
        self.assertNotIn('synthetic-private-trace', str(caught.exception))
        if message:
            self.assertIn(message, str(caught.exception))
        context.close.assert_awaited_once()
        browser.close.assert_awaited_once()
        self.assertEqual(request.get.await_count, len(responses))
        return caught.exception

    @staticmethod
    def response(payload, status=200):
        return SimpleNamespace(ok=status == 200, status=status, json=AsyncMock(return_value=payload))

    async def test_identity_explicit_40362_in_200_or_403_is_restricted_without_article_query(self):
        for status in (200, 403):
            with self.subTest(status=status):
                await self._query([self.response(RESTRICTED, status)], ZhihuAccessRestrictedError)

    async def test_article_error_40362_stops_polling_instead_of_returning_no_match(self):
        await self._query([self.response({'url_token': 'synthetic-member'}),
                           self.response(RESTRICTED)], ZhihuAccessRestrictedError)

    async def test_other_business_error_missing_or_invalid_list_stops_as_unknown(self):
        for payload in ({'error': {'code': 999, 'message': '其他业务错误'}},
                        {'success': False}, {}, {'data': None}, {'data': {}}):
            with self.subTest(payload=payload):
                error = await self._query([self.response({'url_token': 'synthetic-member'}),
                                           self.response(payload)], RuntimeError, '未知')
                self.assertNotIsInstance(error, ZhihuAccessRestrictedError)
                self.assertNotIn('登录状态无效', str(error))

    async def test_plain_http403_does_not_claim_explicit_40362_or_expired_login(self):
        error = await self._query([self.response({'error': {'code': 403}}, 403)], RuntimeError, 'HTTP 403')
        self.assertNotIsInstance(error, ZhihuAccessRestrictedError)
        self.assertNotIn('登录状态无效', str(error))

    async def test_identity_business_error_or_missing_token_is_unknown_not_expired(self):
        for payload in ({'error': {'code': 999}}, {}):
            error = await self._query([self.response(payload)], RuntimeError)
            self.assertNotIsInstance(error, ZhihuAccessRestrictedError)
            self.assertNotIn('登录状态无效', str(error))


class PublisherRestrictionTests(unittest.TestCase):
    def setUp(self):
        self.content = PublishContent(platform='zhihu', title='synthetic title', content='body')

    def test_restriction_during_login_idempotency_or_writer_never_probes_or_republishes(self):
        for stage in ('login', 'idempotency', 'writer'):
            with self.subTest(stage=stage):
                login, query, publish = Mock(return_value=True), Mock(return_value=None), Mock(return_value=None)
                {'login': login, 'idempotency': query, 'writer': publish}[stage].side_effect = \
                    ZhihuAccessRestrictedError('synthetic-private-trace')
                adapter = ZhihuPublisherAdapter('unused.json', login_checker=login,
                    publication_checker=query, publish_runner=publish, timeout_seconds=1)
                result = adapter.publish(self.content)
                self.assertFalse(result.success)
                self.assertEqual(result.status, 'need_action')
                self.assertEqual(result.url, '')
                for text in ('暂时限制', '日常浏览器', '不要', '未点击发布', '记录已保留'):
                    self.assertIn(text, result.message)
                self.assertNotIn('synthetic-private-trace', result.message)
                self.assertEqual(query.call_count, 0 if stage == 'login' else 1)
                self.assertEqual(publish.call_count, 1 if stage == 'writer' else 0)

    def test_restriction_after_one_publish_keeps_outcome_unknown_without_pre_click_claim(self):
        query = Mock(side_effect=[None, ZhihuAccessRestrictedError('synthetic-private-trace')])
        publish = Mock(return_value=None)
        adapter = ZhihuPublisherAdapter('unused.json', login_checker=lambda _: True,
            publication_checker=query, publish_runner=publish, timeout_seconds=1)
        result = adapter.publish(self.content)
        self.assertEqual(result.status, 'need_action')
        self.assertIn('提交结果仍未知', result.message)
        self.assertIn('不要重复发布', result.message)
        self.assertIn('记录已保留', result.message)
        self.assertNotIn('未点击发布', result.message)
        self.assertNotIn('synthetic-private-trace', result.message)
        self.assertEqual(query.call_count, 2)
        publish.assert_called_once()


if __name__ == '__main__':
    unittest.main()
