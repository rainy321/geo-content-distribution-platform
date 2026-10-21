"""Intent checks use temporary customer records; no accounts or live platforms."""
import asyncio
import copy
import json
import sqlite3
import unittest
import uuid
from contextlib import closing
from unittest.mock import AsyncMock, Mock, patch

from services import geo_content_research as research, geo_content_store as store
from services import geo_destination_service as destinations
from services.article_service import update_article
from services.desktop_geo_jobs import DesktopGeoJobs
from services.geo_destination_browser import fill_editor, open_session
from services.geo_publication_suitability import normalize_publication_intent, publication_gate, publication_policy
from services.geo_target_publish_guard import article_target_context, builtin_publisher, configuration_digest
from services.publish_job_service import create_publish_job
from tests.test_geo_content_loop import ResearchFixture, draft, facts
from tests.test_geo_destinations import config


class PublicationSuitabilityTests(unittest.TestCase):
    def test_new_and_legacy_configs_default_to_research_without_implied_review(self):
        value = config()
        value.pop('publication_use')
        value.pop('rule_review')
        normalized = destinations.normalize_config(value)
        self.assertEqual(normalized['publication_use'], 'research_only')
        self.assertEqual(normalized['rule_review'], {'state': 'unknown', 'operator_confirmed': False, 'note': ''})
        self.assertEqual(publication_gate(value)['code'], 'research_only')
        for review in ({}, {'state': 'suitable'}, {'state': 'suitable', 'operator_confirmed': 'true'},
                       {'state': 'unknown', 'operator_confirmed': True}):
            self.assertEqual(publication_gate({**value, 'publication_use': 'editorial', 'rule_review': review})['code'], 'review_unknown')

    def test_explicit_review_is_an_operator_statement_and_unsuitable_cannot_start(self):
        gate = publication_gate(config())
        self.assertTrue(gate['allowed'])
        self.assertIn('运营方', gate['reason'])
        self.assertIn('不是平台自动核验', gate['reason'])
        gate = publication_gate(config(rule_review={'state': 'unsuitable', 'operator_confirmed': True, 'note': '合成不适用原因'}))
        self.assertFalse(gate['allowed'])
        self.assertEqual(gate['code'], 'review_unsuitable')
        for state, enabled in [('disabled', True), ('archived', True), ('deleted', True), ('configured', False)]:
            with self.subTest(state=state, enabled=enabled):
                self.assertEqual(publication_gate(config(enabled=enabled), state)['code'], 'inactive')

    def test_invalid_intent_fields_fail_before_saving(self):
        for changes in ({'publication_use': 'advertising'}, {'publication_use': []}, {'rule_review': []},
                        {'rule_review': {'state': 'approved'}}, {'rule_review': {'operator_confirmed': 'true'}},
                        {'rule_review': {'note': 'x' * 401}}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                normalize_publication_intent(changes)

    def test_fixed_community_policy_cannot_be_overridden_by_labels_or_input_metadata(self):
        for key in ('tencent_community', 'aliyun_community', 'csdn', 'cnblogs'):
            candidate = copy.deepcopy(next(row[3] for row in destinations.CATALOG if row[0] == key))
            value = {**config(), **candidate, 'name': '自定义商业平台', 'publication_use': 'commercial',
                     'publication_policy': {'commercial_restricted': False},
                     'publication_gate': {'allowed': True}}
            normalized = destinations.normalize_config(value)
            self.assertNotIn('publication_policy', normalized)
            self.assertNotIn('publication_gate', normalized)
            gate = publication_gate(normalized)
            self.assertEqual(gate['code'], 'platform_restricted')
            self.assertTrue(gate['matched_policy'])
            self.assertEqual(gate['rules_url'], publication_policy(value)['rules_url'])
            if key == 'cnblogs':
                self.assertEqual(publication_gate({**normalized, 'publication_use': 'editorial'})['code'], 'platform_restricted')
                self.assertIn('本软件 AI 自动化', gate['reason'])
            else:
                self.assertTrue(publication_gate({**normalized, 'publication_use': 'editorial'})['allowed'])

    def test_policy_routes_are_precise_and_separate_csdn_commercial_products(self):
        for url in ('https://cloud.tencent.com/developer/article/1', 'https://cloud.tencent.com/developer',
                    'https://cloud.tencent.com/%64eveloper/article/1'):
            self.assertEqual(publication_policy({'editor_url': url})['key'], 'tencent_community')
        for url in ('https://cloud.tencent.com/document/product/1', 'https://cloud.tencent.com/developer-other/article/1',
                    'https://cloud.tencent.com.evil.example.org/developer/article/1',
                    'https://editor.csdn.net/commercial-cooperation',
                    'https://cloud.tencent.com/foo/../developer/article/1'):
            self.assertIsNone(publication_policy({'editor_url': url}))
        mixed = config(editor_url='https://i.cnblogs.com/', source_scope='https://cloud.tencent.com/developer/')
        self.assertTrue(publication_policy(mixed)['automation_restricted'])
        self.assertFalse(publication_gate(mixed)['allowed'])
        login_only = config(login_url='https://account.cnblogs.com/signin')
        self.assertEqual(publication_gate(login_only)['code'], 'platform_restricted')

    def test_ambiguous_urls_cannot_grant_platform_identity_or_bypass_action_gate(self):
        values = ['https://evil.example\\@cloud.tencent.com/developer/article/1',
                  'https://cloud.tencent.com/foo/../developer/article/1',
                  'https://cloud.tencent.com/foo/%2e%2e/developer/article/1',
                  'http://cloud.tencent.com/developer/article/1',
                  'https://cloud.tencent.com:444/developer/article/1',
                  'https://[invalid/developer/article/1']
        original = copy.deepcopy(values)
        target = {'id': 'synthetic', 'key': 'target_synthetic', 'config': config(source_scope='https://cloud.tencent.com/developer/')}
        for url in values:
            with self.subTest(url=url):
                observed = {'cases': [{'id': 'q1', 'usable': True}], 'sources': [
                    {'id': 's1', 'case_id': 'q1', 'url': url, 'title': '原始来源证据'}]}
                row = destinations.opportunities(observed, [target])[0]
                self.assertIsNone(row['candidate'])
                self.assertEqual(row['matches'], [])
                self.assertEqual(row['surface'], 'unknown')
                self.assertEqual(row['examples'][0]['url'], url)
                self.assertIsNone(publication_policy({'editor_url': url}))
                self.assertEqual(publication_gate(config(editor_url=url))['code'], 'review_unknown')
        self.assertEqual(values, original)

    def test_candidate_metadata_is_fixed_and_cannot_change_the_policy_registry(self):
        observed = {'cases': [{'id': 'q1', 'usable': True}], 'sources': [
            {'id': 's1', 'case_id': 'q1', 'url': 'https://www.cnblogs.com/sample/p/1', 'title': '合成技术来源'}]}
        candidate = destinations.opportunities(observed, [])[0]['candidate']
        policy = candidate['publication_policy']
        self.assertTrue(policy['automation_restricted'])
        policy['automation_restricted'] = False
        self.assertTrue(publication_policy(candidate)['automation_restricted'])
        self.assertFalse(publication_gate(destinations.normalize_config(candidate))['allowed'])


class PublicationSuitabilityIntegrationTests(ResearchFixture, unittest.TestCase):
    def ready_article(self, **changes):
        self.target = destinations.save_target(self.database, self.cycle['project_id'], config(**changes))
        self.cycle = store.get_cycle(self.database, self.cycle['id'])
        self.cycle = store.save_facts(self.database, self.cycle['id'], facts(), self.cycle['revision'])
        saved = store.save_draft(self.database, self.cycle['id'], 'b01', self.target['key'], draft(), {'method': 'synthetic'}, self.cycle['revision'])
        self.article_id = saved['article_id']
        update_article(self.database, self.article_id, {'status': 'ready'})
        return self.target

    def test_all_target_entries_share_the_same_gate_before_writing_or_opening(self):
        target = self.ready_article()
        target = destinations.save_target(self.database, self.cycle['project_id'],
            config(publication_use='editorial', rule_review={'state': 'unknown', 'operator_confirmed': False}), target['id'], target['revision'])
        current = store.get_cycle(self.database, self.cycle['id'])
        reason = target['publication_gate']['reason']
        for action in (lambda: research.draft_input(current, 'b01', target['key']),
                       lambda: research.draft_prompt(current, 'b01', target['key']),
                       lambda: store.save_draft(self.database, current['id'], 'b01', target['key'], draft(), {}, current['revision']),
                       lambda: destinations.reserve_fill(self.database, current['project_id'], target['id'], self.article_id)):
            with self.assertRaises(ValueError) as error:
                action()
            self.assertEqual(str(error.exception), reason)
        jobs = DesktopGeoJobs(self.root)
        self.addCleanup(jobs.shutdown)
        with patch.object(jobs, '_start', Mock()) as start:
            self.assertEqual(jobs.open_destination(current['id'], target['id'])['error'], reason)
            self.assertEqual(jobs.open_destination(current['id'], target['id'], self.article_id)['error'], reason)
            self.assertEqual(jobs.generate_research(current['id'], 'draft', 'b01', target['key'], {})['error'], reason)
        start.assert_not_called()
        profile = self.root / 'not-created-unreviewed'
        with patch('services.browser_runtime.launch_context', AsyncMock()) as launch:
            with self.assertRaises(ValueError) as error:
                asyncio.run(open_session(target, profile, None))
            self.assertEqual(str(error.exception), reason)
            launch.assert_not_awaited()
        self.assertFalse(profile.exists())
        with self.assertRaises(ValueError) as error:
            asyncio.run(fill_editor(None, target['config'], {}))
        self.assertEqual(str(error.exception), reason)
        self.assertEqual(destinations.get_target(self.database, current['project_id'], target['id'])['events'], [])

    def test_legacy_read_keeps_config_digest_database_and_old_moderation_available(self):
        target = self.ready_article()
        with closing(sqlite3.connect(self.database)) as connection, connection:
            legacy = json.loads(connection.execute('SELECT config FROM geo_distribution_targets WHERE id=?', (target['id'],)).fetchone()[0])
            legacy.pop('publication_use')
            legacy.pop('rule_review')
            connection.execute('UPDATE geo_distribution_targets SET config=? WHERE id=?', (json.dumps(legacy), target['id']))
        before = (self.database.read_bytes(), self.database.stat().st_mtime_ns)
        current = store.get_cycle(self.database, self.cycle['id'])
        loaded = current['destinations'][0]
        self.assertEqual(loaded['config'], legacy)
        self.assertEqual(loaded['config_sha256'], configuration_digest(legacy))
        self.assertEqual(loaded['publication_gate']['code'], 'research_only')
        self.assertEqual((self.database.read_bytes(), self.database.stat().st_mtime_ns), before)
        article = current['drafts'][0]
        saved = store.save_moderation_observation(self.database, current['id'], {
            'observation_id': uuid.uuid4().hex, 'article_id': self.article_id, 'target_id': target['id'],
            'state': 'rejected', 'confirmed': True, 'expected_article_sha256': article['article_sha256']})
        self.assertEqual(saved['moderation_observations'][0]['state'], 'rejected')
        self.assertEqual(saved['drafts'][0]['content'], article['content'])
        self.assertEqual(saved['drafts'][0]['provenance'], article['provenance'])
        self.assertEqual(research.draft_input(current, 'b01', 'owned_web')['platform'], 'owned_web')

    def test_builtin_target_creation_and_worker_context_require_suitability(self):
        target = self.ready_article(home_url='https://www.toutiao.com/', login_url='https://mp.toutiao.com/',
            editor_url='https://mp.toutiao.com/', source_scope='https://www.toutiao.com/', channel='toutiao', title_limit=30)
        self.assertTrue(builtin_publisher(target['config'])['available'])
        changed = destinations.save_target(self.database, self.cycle['project_id'],
            {**target['config'], 'publication_use': 'research_only'}, target['id'], target['revision'])
        self.assertFalse(changed['builtin_publisher']['available'])
        reason = changed['publication_gate']['reason']
        with closing(store._connect(self.database)) as connection:
            with self.assertRaises(ValueError) as error:
                article_target_context(connection, self.article_id, platform='toutiao', require_builtin=True)
            self.assertEqual(str(error.exception), reason)
        with self.assertRaises(ValueError) as error:
            create_publish_job(self.database, article_id=self.article_id, platform='toutiao')
        self.assertEqual(str(error.exception), reason)

    def test_http_generation_rejects_before_any_ai_request(self):
        target = self.ready_article()
        destinations.save_target(self.database, self.cycle['project_id'], config(publication_use='research_only'), target['id'], target['revision'])
        from sau_backend import app
        with patch.dict(app.config, DATABASE_PATH=self.database, ACCESS_CONTROL_ENABLED=False, TESTING=True):
            with patch('services.ai_service._request_chat_completion') as call:
                result = app.test_client().post('/api/geo-content/cycles/' + self.cycle['id'] + '/generate',
                    json={'kind': 'draft', 'brief_id': 'b01', 'platform': target['key']})
        self.assertEqual(result.status_code, 400)
        self.assertIn('仅用于来源研究', result.json['msg'])
        call.assert_not_called()

    def test_new_research_only_target_can_be_saved_without_any_login_or_review(self):
        from sau_backend import app
        raw = config()
        raw.pop('publication_use')
        raw.pop('rule_review')
        with patch.dict(app.config, DATABASE_PATH=self.database, ACCESS_CONTROL_ENABLED=False, TESTING=True):
            result = app.test_client().post('/api/geo-content/cycles/' + self.cycle['id'] + '/destinations',
                json={'config': raw})
        self.assertEqual(result.status_code, 200)
        target = result.json['data']['target']
        self.assertEqual(target['config']['publication_use'], 'research_only')
        self.assertEqual(target['publication_gate']['code'], 'research_only')
        self.assertIsNone(target['publication_policy'])
        self.assertEqual(target['events'], [])


if __name__ == '__main__':
    unittest.main()
