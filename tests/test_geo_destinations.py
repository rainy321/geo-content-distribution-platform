"""Distribution destinations and account-state handling, with synthetic fixtures."""
import asyncio
import copy
import json
import sqlite3
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

from services import geo_destination_service as destinations, geo_content_store as store, geo_content_research as research
from services.desktop_geo_jobs import DesktopGeoJobs
from tests.test_geo_content_loop import ResearchFixture, draft, facts


def config(**changes):
    return {'name': '合成小众技术社区', 'home_url': 'https://niche.example.org/',
            'login_url': 'https://niche.example.org/login', 'editor_url': 'https://niche.example.org/editor',
            'source_scope': 'https://niche.example.org/blog/', 'style': '面向工程师：问题、条件、步骤、验证与局限。',
            'title_selector': 'input[name=title]', 'body_selector': 'textarea[name=body]', **changes}


class TestSourceOpportunities(unittest.TestCase):
    def test_aliyun_article_is_a_login_export_candidate_with_no_assumed_publish_permission(self):
        observed = {'cases': [{'id': 'q01', 'usable': True}], 'sources': [
            {'id': 's1', 'case_id': 'q01', 'url': 'https://developer.aliyun.com/article/1768112', 'title': '合成社区文章'}]}
        rows = destinations.opportunities(observed, [])
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row['id'], 'aliyun_community')
        self.assertEqual(row['surface'], 'community_candidate')
        self.assertEqual(row['source_ids'], ['s1'])
        normalized = destinations.normalize_config(row['candidate'])
        self.assertEqual(normalized['home_url'], 'https://developer.aliyun.com/')
        self.assertEqual(normalized['source_scope'], 'https://developer.aliyun.com/article/')
        self.assertEqual(normalized['publication_scope'], 'https://developer.aliyun.com/article/')
        self.assertEqual(normalized['login_url'], 'https://developer.aliyun.com/article/new')
        self.assertEqual(normalized['editor_url'], normalized['login_url'])
        self.assertEqual(normalized['rules_url'], 'https://developer.aliyun.com/article/1639804')
        self.assertEqual(normalized['channel'], '')
        self.assertEqual(normalized['title_selector'], '')
        self.assertEqual(normalized['body_selector'], '')
        from services.geo_target_publish_guard import builtin_publisher
        self.assertFalse(builtin_publisher(normalized)['available'])

    def test_aliyun_help_docs_and_nonarticle_or_similar_hosts_do_not_become_community_candidates(self):
        observed = {'cases': [{'id': 'q01', 'usable': True}], 'sources': [
            {'id': 's1', 'case_id': 'q01', 'url': 'https://help.aliyun.com/zh/model-studio/overview', 'title': '合成厂商文档'},
            {'id': 's2', 'case_id': 'q01', 'url': 'https://developer.aliyun.com/topic/example', 'title': '合成专题'},
            {'id': 's3', 'case_id': 'q01', 'url': 'https://developer.aliyun.com/articlex/123', 'title': '路径相似'},
            {'id': 's4', 'case_id': 'q01', 'url': 'https://developer.aliyun.com.evil.example.org/article/123', 'title': '域名相似'},
            {'id': 's5', 'case_id': 'q01', 'url': 'https://www.developer.aliyun.com/article/123', 'title': '未核验子域'},
            {'id': 's6', 'case_id': 'q01', 'url': 'https://help.aliyun.com/article/123', 'title': '文档域文章形路径'}]}
        rows = destinations.opportunities(observed, [])
        docs = next(row for row in rows if row['domain'] == 'help.aliyun.com')
        self.assertEqual(docs['surface'], 'vendor_documentation')
        self.assertIsNone(docs['candidate'])
        self.assertFalse(any(row['surface'] == 'community_candidate' for row in rows))
        self.assertTrue(all(row['surface'] == 'unknown' for row in rows if row is not docs))

    def test_vendor_documents_are_not_open_communities_and_host_spoofing_fails(self):
        observed = {'cases': [{'id': 'q01', 'usable': True}], 'sources': [
            {'id': 's1', 'case_id': 'q01', 'url': 'https://cloud.tencent.com/document/product/1/2', 'title': '文档'},
            {'id': 's2', 'case_id': 'q01', 'url': 'https://cloud.tencent.com/developer/article/123', 'title': '社区'},
            {'id': 's3', 'case_id': 'q01', 'url': 'https://cloud.tencent.com.evil.example.org/developer/article/1', 'title': '伪相似域名'}]}
        rows = destinations.opportunities(observed, [])
        self.assertEqual(next(row for row in rows if row['id'].endswith(':docs'))['surface'], 'vendor_documentation')
        community = next(row for row in rows if row['id'] == 'tencent_community')
        self.assertEqual(community['candidate']['name'], '腾讯云开发者社区')
        self.assertEqual(next(row for row in rows if 'evil' in row['id'])['surface'], 'unknown')
        target = {'id': 't1', 'key': 'target_t1', 'config': {'name': '社区', 'source_scope': community['candidate']['source_scope']}}
        rows = destinations.opportunities(observed, [target])
        self.assertFalse(next(row for row in rows if row['surface'] == 'vendor_documentation')['matches'])
        self.assertTrue(next(row for row in rows if row['id'] == 'tencent_community')['matches'])

    def test_unknown_urls_title_leads_failed_answers_and_duplicate_sources(self):
        observed = {'cases': [{'id': 'q1', 'usable': True}, {'id': 'q2', 'usable': False}], 'sources': [
            {'id': 's1', 'case_id': 'q1', 'url': None, 'title': '腾讯云开发者社区文章'},
            {'id': 's2', 'case_id': 'q1', 'url': 'https://www.zhihu.com/question/1', 'title': '问题'},
            {'id': 's3', 'case_id': 'q1', 'url': 'https://www.zhihu.com/question/1#reference', 'title': '问题'},
            {'id': 's4', 'case_id': 'q2', 'url': 'https://blog.csdn.net/user/article/1', 'title': '无效回答'}]}
        rows = destinations.opportunities(observed, [])
        unknown = next(row for row in rows if row['surface'] == 'url_unknown')
        self.assertIsNone(unknown['candidate'])
        self.assertEqual(unknown['title_leads'][0]['basis'], 'title_only_unverified')
        self.assertEqual(next(row for row in rows if row['id'] == 'zhihu')['observed_pairs'], 1)
        self.assertNotIn('csdn', [row['id'] for row in rows])


class TestDestinationState(ResearchFixture, unittest.TestCase):
    def target(self, **changes):
        self.target_value = destinations.save_target(self.database, self.cycle['project_id'], config(**changes))
        self.cycle = store.get_cycle(self.database, self.cycle['id'])
        return self.target_value

    def article(self, ready=True):
        self.cycle = store.save_facts(self.database, self.cycle['id'], facts(), self.cycle['revision'])
        result = store.save_draft(self.database, self.cycle['id'], 'b01', self.target_value['key'], draft(), {'method': 'synthetic_fixture'}, self.cycle['revision'])
        if ready:
            with closing(sqlite3.connect(self.database)) as connection, connection:
                connection.execute("UPDATE articles SET status='ready' WHERE id=?", (result['article_id'],))
        return result['article_id']

    def test_configuration_is_project_scoped_persistent_and_never_claims_auto_publish(self):
        target = self.target()
        self.assertIn(target['key'], self.cycle['channels'])
        self.assertFalse(target['auto_publish_verified'])
        with self.assertRaisesRegex(ValueError, '当前客户'):
            destinations.get_target(self.database, self.cycle['project_id'] + 999, target['id'])
        with self.assertRaisesRegex(ValueError, '跨客户'):
            destinations.save_target(self.database, self.cycle['project_id'] + 999, config(), target['id'], 1)
        self.assertEqual(destinations.list_targets(self.database, self.cycle['project_id'])[0]['id'], target['id'])

    def test_invalid_urls_limits_and_executable_selectors_are_rejected(self):
        for url in ('http://example.org/', 'https://user:password@example.org/', 'https://127.0.0.1/', 'https://localhost/', 'https://example.org:8000/', 'file:///tmp', 'https://x.local/'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                destinations.normalize_config(config(login_url=url))
        for change in ({'daily_limit': 0}, {'interval_hours': True}, {'title_selector': 'xpath=//input'}, {'body_selector': ''}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                destinations.normalize_config(config(**change))

    def test_platform_style_and_versions_enter_generation_and_lineage(self):
        self.target()
        article_id = self.article()
        prompt = research.draft_prompt(self.cycle, 'b01', self.target_value['key'])
        self.assertIn('面向工程师', prompt)
        lineage = store.article_research(self.database, article_id)['provenance']
        self.assertEqual(lineage['destination']['id'], self.target_value['id'])
        self.assertEqual(lineage['distribution_basis']['source_ids'], [])
        original = self.cycle
        destinations.save_target(self.database, self.cycle['project_id'], config(style='面向研究者写方法与局限'), self.target_value['id'], 1)
        with self.assertRaisesRegex(ValueError, '已变更'):
            store.save_draft(self.database, original['id'], 'b01', self.target_value['key'], draft(), {'method': 'synthetic'}, original['revision'])

    def test_custom_platform_length_rejects_instead_of_silently_truncating(self):
        self.target(body_limit=100)
        with self.assertRaisesRegex(ValueError, '长度限制'):
            self.article()

    def test_only_reviewed_articles_for_the_exact_customer_target_can_be_filled(self):
        self.target()
        article_id = self.article(ready=False)
        with self.assertRaisesRegex(ValueError, '就绪'):
            destinations.reserve_fill(self.database, self.cycle['project_id'], self.target_value['id'], article_id)
        self.assertEqual(destinations.get_target(self.database, self.cycle['project_id'], self.target_value['id'])['events'], [])

    def test_reservation_prevents_duplicates_and_login_does_not_clear_block(self):
        self.target()
        article_id = self.article()
        _, _, event = destinations.reserve_fill(self.database, self.cycle['project_id'], self.target_value['id'], article_id)
        with self.assertRaises(ValueError):
            destinations.reserve_fill(self.database, self.cycle['project_id'], self.target_value['id'], article_id)
        destinations.record_event(self.database, self.target_value['id'], 'fill', 'need_action', '请检查验证页面', article_id, event)
        destinations.record_event(self.database, self.target_value['id'], 'login', 'session_saved', '会话保存，未验证')
        target = destinations.get_target(self.database, self.cycle['project_id'], self.target_value['id'])
        self.assertTrue(target['blocked_reason'])
        with self.assertRaises(ValueError):
            destinations.review_block(self.database, self.cycle['project_id'], target['id'], False)
        destinations.review_block(self.database, self.cycle['project_id'], target['id'], True)
        with self.assertRaisesRegex(ValueError, '24 小时'):
            destinations.reserve_fill(self.database, self.cycle['project_id'], target['id'], article_id)

    def test_rolling_quota_and_interval_survive_manual_review(self):
        self.target(daily_limit=2)
        article_id = self.article()
        _, _, event = destinations.reserve_fill(self.database, self.cycle['project_id'], self.target_value['id'], article_id)
        destinations.record_event(self.database, self.target_value['id'], 'fill', 'need_action', '合成中断', article_id, event)
        destinations.review_block(self.database, self.cycle['project_id'], self.target_value['id'], True)
        with self.assertRaisesRegex(ValueError, '间隔'):
            destinations.reserve_fill(self.database, self.cycle['project_id'], self.target_value['id'], article_id)
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute('UPDATE geo_distribution_events SET created_at=? WHERE kind=\'fill\'', ((datetime.now(timezone.utc) - timedelta(hours=25)).isoformat(),))
        destinations.reserve_fill(self.database, self.cycle['project_id'], self.target_value['id'], article_id)

    def test_native_login_saves_separate_profile_and_does_not_validate_permissions(self):
        self.target()
        captured = []
        async def session(target, profile, executable, **kwargs):
            captured.append(profile)
            return {'state': 'session_saved', 'message': '会话保存，不代表投稿权限验证'}
        bridge = DesktopGeoJobs(self.root)
        with patch('services.geo_destination_browser.open_session', session):
            bridge.open_destination(self.cycle['id'], self.target_value['id'])
            bridge.worker.join(timeout=5)
        self.assertEqual(bridge.get_task()['status'], 'completed')
        self.assertIn(self.target_value['id'], str(captured[0]))
        self.assertNotIn('doubao', str(captured[0]))
        target = destinations.get_target(self.database, self.cycle['project_id'], self.target_value['id'])
        self.assertEqual(target['state'], 'session_saved')
        self.assertFalse(target['auto_publish_verified'])

    def test_interrupted_native_fill_is_durable_and_never_replayed(self):
        self.target()
        article_id = self.article()
        destinations.reserve_fill(self.database, self.cycle['project_id'], self.target_value['id'], article_id)
        bridge = DesktopGeoJobs(self.root)
        bridge.store.save({'id': 'synthetic-interrupted', 'kind': 'destination_fill', 'status': 'running',
                           'target_id': self.target_value['id'], 'created_at': datetime.now(timezone.utc).isoformat()})
        restored = DesktopGeoJobs(self.root)
        self.assertEqual(restored.get_task()['status'], 'needs_review')
        target = destinations.get_target(self.database, self.cycle['project_id'], self.target_value['id'])
        self.assertEqual(target['state'], 'need_action')
        self.assertIsNone(restored.worker)

    def test_native_fill_failure_becomes_block_and_no_second_browser_attempt(self):
        self.target()
        article_id = self.article()
        async def fail(*args, **kwargs):
            kwargs['progress']({'state': 'need_action', 'message': '配置失效或验证，需检查'})
            return {'state': 'need_action', 'message': '配置失效或验证，需检查'}
        bridge = DesktopGeoJobs(self.root)
        with patch('services.geo_destination_browser.open_session', fail) as session:
            bridge.open_destination(self.cycle['id'], self.target_value['id'], article_id)
            bridge.worker.join(timeout=5)
        target = destinations.get_target(self.database, self.cycle['project_id'], self.target_value['id'])
        self.assertEqual(target['state'], 'need_action')
        self.assertEqual(len([row for row in target['events'] if row['kind'] == 'fill']), 1)
        self.assertEqual(store.get_cycle(self.database, self.cycle['id'])['publications'], [])


class TestEditorFilling(unittest.IsolatedAsyncioTestCase):
    async def test_normal_user_window_close_preserves_session_saved_result(self):
        from tempfile import TemporaryDirectory
        from pathlib import Path
        from types import SimpleNamespace
        from unittest.mock import Mock
        from services.geo_destination_browser import open_session
        page = SimpleNamespace(goto=AsyncMock(return_value=SimpleNamespace(status=200)))
        context = SimpleNamespace(pages=[page], on=lambda _, callback: callback(),
            close=AsyncMock(side_effect=RuntimeError('Target page, context or browser has been closed')))
        factory = AsyncMock()
        with TemporaryDirectory() as root, patch('playwright.async_api.async_playwright', return_value=factory), patch('services.browser_runtime.launch_context', AsyncMock(return_value=context)):
            result = await open_session({'config': config()}, Path(root) / 'profile', 'synthetic-browser', wait_seconds=.01)
        self.assertEqual(result['state'], 'session_saved')

    async def test_browser_fallback_only_retries_launch_with_a_separate_profile(self):
        from pathlib import Path
        from types import SimpleNamespace
        from tempfile import TemporaryDirectory
        from services.browser_runtime import launch_context
        context = object()
        chromium = SimpleNamespace(launch_persistent_context=AsyncMock(side_effect=[RuntimeError('synthetic launch failure'), context]))
        with TemporaryDirectory() as root:
            value = await launch_context(SimpleNamespace(chromium=chromium), Path(root) / 'profile', {'executable_path': 'edge', 'headless': True}, 'chrome')
        self.assertIs(value, context)
        first, second = chromium.launch_persistent_context.call_args_list
        self.assertNotEqual(first.args[0], second.args[0])
        self.assertTrue(second.args[0].endswith('-chrome'))

    async def test_no_fill_on_redirect_warning_or_ambiguous_selector(self):
        from services.geo_destination_browser import fill_editor
        page = AsyncMock()
        page.url = 'https://login.other.example.org/'
        with self.assertRaisesRegex(ValueError, '跳到'):
            await fill_editor(page, config(), {'title': '标题', 'content': '正文'})
        page.url = config()['editor_url']
        body = AsyncMock()
        body.inner_text.return_value = '操作过于频繁'
        page.locator = lambda _: body
        with self.assertRaisesRegex(ValueError, '验证、限制'):
            await fill_editor(page, config(), {'title': '标题', 'content': '正文'})
        body.inner_text.return_value = '编辑器'
        body.count.return_value = 2
        with self.assertRaisesRegex(ValueError, '不唯一'):
            await fill_editor(page, config(), {'title': '标题', 'content': '正文'})
        body.fill.assert_not_called()


class TestDestinationAPI(ResearchFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        from sau_backend import app
        self.configuration = patch.dict(app.config, DATABASE_PATH=self.database, ACCESS_CONTROL_ENABLED=False, TESTING=True)
        self.configuration.start()
        self.addCleanup(self.configuration.stop)
        self.client = app.test_client()

    def test_save_update_and_read_events_use_cycle_customer_and_validate_requests(self):
        endpoint = '/api/geo-content/cycles/' + self.cycle['id'] + '/destinations'
        response = self.client.post(endpoint, json={'config': config()})
        self.assertEqual(response.status_code, 200)
        target = response.json['data']['target']
        self.assertEqual(len(response.json['data']['cycle']['destinations']), 1)
        response = self.client.get(endpoint + '/' + target['id'])
        self.assertEqual(response.json['data']['target']['events'], [])
        response = self.client.put(endpoint + '/' + target['id'], json={'config': config(style='为技术负责人写核对清单'), 'revision': 1})
        self.assertEqual(response.json['data']['target']['revision'], 2)
        self.assertEqual(self.client.put(endpoint + '/' + target['id'], json={'config': config(), 'revision': 1}).status_code, 400)
        self.assertEqual(self.client.post(endpoint, json={'config': config(login_url='file:///secret')}).status_code, 400)
        self.assertEqual(self.client.get(endpoint.replace(self.cycle['id'], 'missing') + '/' + target['id']).status_code, 404)
        self.assertEqual(self.client.post(endpoint + '/' + target['id'] + '/review', json={'confirmed': False}).status_code, 400)


if __name__ == '__main__':
    unittest.main()
