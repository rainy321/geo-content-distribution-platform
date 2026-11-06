"""Actual backend entrypoints reject directory articles before any AI/write."""
import copy
import json
import sqlite3
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

from services import geo_content_research as research, geo_content_store as store
from services import geo_destination_service as destinations
from tests.test_geo_content_loop import ResearchFixture, draft
from tests.test_geo_destinations import config


SCOPES = ('https://www.apisou.com', 'https://www.apisou.com/',
          'https://www.apiznz.com', 'https://www.apiznz.com/',
          'https://www.apiznz.com/site/lists', 'https://www.apiznz.com/site/lists/',
          'https://grokcode.cn/api-transit', 'https://grokcode.cn/api-transit/')


class DirectoryArticleGateTests(ResearchFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        from sau_backend import app
        self.configuration = patch.dict(app.config, DATABASE_PATH=self.database,
            ACCESS_CONTROL_ENABLED=False, TESTING=True, AI_RATE_LIMIT_PER_MINUTE=0)
        self.configuration.start(); self.addCleanup(self.configuration.stop)
        self.client = app.test_client()

    def target(self, scope):
        origin = 'https://' + urlsplit(scope).hostname + '/'
        target = destinations.save_target(self.database, self.cycle['project_id'], config(
            name='合成目标', home_url=origin, login_url=origin, editor_url=origin, source_scope=scope))
        # Also exercise valid legacy root scopes that predate trailing-slash
        # normalization, rather than testing only freshly normalized configs.
        value = {**target['config'], 'source_scope': scope}
        with sqlite3.connect(self.database) as connection:
            connection.execute('UPDATE geo_distribution_targets SET config=? WHERE id=?',
                               (json.dumps(value), target['id']))
        return destinations.get_target(self.database, self.cycle['project_id'], target['id'])

    def tables(self):
        with sqlite3.connect(self.database) as connection:
            names = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            return {name: connection.execute('SELECT * FROM "' + name + '" ORDER BY rowid').fetchall() for name in names}

    def options(self, target):
        return {'kind': 'draft', 'brief_id': 'b01', 'platform': target['key'],
                'ai_config': {'base_url': 'https://api.example.org/v1',
                              'api_key': 'synthetic-session-key', 'model': 'synthetic-model'}}

    def test_directory_gate_precedes_fact_question_and_publication_consumption(self):
        for scope in SCOPES:
            with self.subTest(scope=scope):
                target = {'key': 'target_synthetic', 'config': {'source_scope': scope}}
                cycle = {'destinations': [target]}
                with self.assertRaisesRegex(ValueError, '收录目录.*独立申请'):
                    research.draft_input(cycle, 'missing-brief', target['key'])

    def test_actual_generate_api_rejects_every_known_scope_without_ai_or_any_table_change(self):
        self.prepare_facts()
        previous = store.save_draft(self.database, self.cycle['id'], 'b01', 'owned_web', draft(),
                                    {'method': 'synthetic'}, self.cycle['revision'])
        for scope in SCOPES:
            with self.subTest(scope=scope):
                target = self.target(scope)
                before = self.tables()
                with patch('services.ai_service._request_chat_completion') as provider:
                    result = self.client.post(f'/api/geo-content/cycles/{self.cycle["id"]}/generate',
                                              json=self.options(target))
                self.assertEqual(result.status_code, 400, result.json)
                self.assertIn('收录目录', result.json['msg'])
                provider.assert_not_called()
                self.assertEqual(self.tables(), before)
                self.assertIsNotNone(store.article_research(self.database, previous['article_id']))

    def test_direct_existing_and_save_paths_cannot_bypass_the_directory_gate(self):
        self.prepare_facts()
        target = self.target('https://www.apisou.com')
        current = store.get_cycle(self.database, self.cycle['id'])
        before = self.tables()
        with self.assertRaisesRegex(ValueError, '收录目录'):
            store.existing_draft(self.database, current, 'b01', target['key'])
        with self.assertRaisesRegex(ValueError, '收录目录'):
            store.save_draft(self.database, current['id'], 'b01', target['key'], draft(),
                             {'method': 'synthetic'}, current['revision'])
        self.assertEqual(self.tables(), before)

    def test_ordinary_configured_article_generation_and_dedup_are_unchanged(self):
        self.prepare_facts()
        target = self.target('https://niche.example.org/blog/')
        with patch('services.ai_service._request_chat_completion', return_value=draft()) as provider:
            first = self.client.post(f'/api/geo-content/cycles/{self.cycle["id"]}/generate', json=self.options(target))
            second = self.client.post(f'/api/geo-content/cycles/{self.cycle["id"]}/generate', json=self.options(target))
        self.assertEqual(first.status_code, 200, first.json)
        self.assertEqual(second.status_code, 200, second.json)
        self.assertEqual(provider.call_count, 1)
        self.assertEqual(first.json['data']['article_id'], second.json['data']['article_id'])
        lineage = store.article_research(self.database, first.json['data']['article_id'])['provenance']
        self.assertEqual(lineage['destination']['id'], target['id'])

    def test_grok_root_is_not_misclassified_as_its_api_transit_directory(self):
        self.prepare_facts()
        target = self.target('https://grokcode.cn/')
        current = store.get_cycle(self.database, self.cycle['id'])
        snapshot = copy.deepcopy(current)
        value = research.draft_input(current, 'b01', target['key'])
        self.assertEqual(value['destination']['id'], target['id'])
        self.assertEqual(current, snapshot)
