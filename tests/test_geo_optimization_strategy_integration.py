"""Isolated synthetic DB and mocked providers: question actions reach real generation paths."""
import copy
import json
import os
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch

from db.createTable import initialize_database
from services import geo_content_research as research, geo_content_store as store
from services.desktop_geo_jobs import DesktopGeoJobs
from services.geo_test_service import build_report, parse_ai_plan


def synthetic_report():
    profile = {'name': '合成流程甲公司', 'entity_type': 'company', 'industry': '合成软件服务',
               'description': '仅用于离线流程验证', 'website': 'https://example.org'}
    cases = [
        {'category': 'discovery', 'question': '团队怎样选择软件说明服务？', 'intent': '自然需求'},
        {'category': 'discovery', 'question': '软件说明服务如何比较适用范围？', 'intent': '自然选型'},
        {'category': 'brand_identity', 'question': '合成流程甲公司提供什么公开业务？', 'intent': '品牌核对'},
        {'category': 'website_guided', 'question': '请查看 https://example.org 核对公开服务说明。', 'intent': '官网核对'},
    ]
    plan = parse_ai_plan(json.dumps({'cases': cases}, ensure_ascii=False), profile, 4, provenance={'method': 'synthetic_only'})
    state = {'run_id': 'offline-strategy-baseline', 'status': 'completed', 'cases': {},
             'execution': {'client_mode': 'consumer', 'requested_model_label': 'synthetic-model'}}
    for case in plan['cases']:
        state['cases'][case['id']] = {'status': 'completed', 'result': {
            'question': case['question'], 'question_submitted': True, 'question_submission_attempted': True,
            'answer_completed': True, 'response': '合成流程甲公司仅提供软件测试说明；这不是现实客户证据。',
            'collection_status': 'success', 'source_extraction_status': 'captured',
            'citations': [{'title': '合成公开文档', 'url': 'https://docs.example.org/selection'}], 'displayed_sources': [],
            'visible_source_count': 0, 'visible_citation_count': 1,
            'environment': {'client_mode': 'consumer', 'model_label': 'synthetic-model'},
            'timestamp': '2026-10-01T00:00:00Z',
        }}
    return build_report(plan, state)


def synthetic_facts():
    return [{'text': '合成流程甲公司说明提供软件测试资料，仅用于离线验证。',
             'source_url': 'https://example.org/about', 'confirmed': True}]


def synthetic_draft():
    return json.dumps({'title': '合成软件说明服务的适用范围', 'summary': '仅供离线生成链路测试。',
        'content': ('据公开资料自述，合成流程甲公司提供软件测试资料。选择时应核对适用范围，不能将合成说明视为现实交付案例。\n\n' * 20)
                   + '参考资料：https://example.org/about',
        'tags': ['合成流程'], 'used_fact_ids': ['f01']}, ensure_ascii=False)


class StrategyIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.database = initialize_database(self.root / 'database.db')
        self.cycle = store.create_cycle(self.database, synthetic_report(), 'synthetic-strategy-report')
        self.cycle = store.save_facts(self.database, self.cycle['id'], synthetic_facts(), self.cycle['revision'])

    def rows(self):
        with closing(sqlite3.connect(self.database)) as connection:
            return {table: connection.execute('SELECT * FROM ' + table + ' ORDER BY rowid').fetchall()
                    for table in ('geo_content_cycles', 'geo_content_drafts', 'articles')}

    def test_get_is_read_only_and_full_vs_minimal_cycle_inputs_have_identical_action_hash(self):
        before = self.rows()
        with closing(store._connect(self.database)) as connection:
            minimal = store._cycle(connection, self.cycle['id'])
        with patch('services.geo_feedback_diagnostics.diagnose_cycle', return_value={'planning_feedback': {'needs_update': True}}):
            full = store.get_cycle(self.database, self.cycle['id'])
        self.assertTrue(full['optimization_plan']['needs_refresh'])
        first = research.draft_input(minimal, 'b01', 'zhihu')
        second = research.draft_input(full, 'b01', 'zhihu')
        self.assertEqual(first['action_plan'], second['action_plan'])
        self.assertNotIn('needs_refresh', first['action_plan'])
        self.assertEqual(research.fingerprint(first), research.fingerprint(second))
        self.assertEqual(before, self.rows())

    def test_saved_brand_action_does_not_cover_natural_questions_or_claim_success(self):
        brief = {'case_ids': ['q03'], 'source_ids': [], 'topic': '主体与公开业务说明',
                 'why': '本次合成回答仍需对照已确认业务。', 'outline': ['主体与业务'], 'missing_evidence': ['公开业务说明'],
                 'priority': 'medium', 'optimization': {'gap': '本题需要核对业务解释。', 'change': '主体和已确认业务在同段说明。'}}
        current = store.save_analysis(self.database, self.cycle['id'],
            json.dumps({'diagnosis': '离线合成行动', 'briefs': [brief]}, ensure_ascii=False),
            {'method': 'synthetic_only'}, self.cycle['revision'])
        plan = current['optimization_plan']
        self.assertEqual(plan['actions'][0]['gap'], brief['optimization']['gap'])
        self.assertEqual(plan['actions'][0]['change_summary'], brief['optimization']['change'])
        self.assertEqual(plan['actions'][0]['goals'], [{'key': 'identity_accuracy', 'label': '品牌与业务识别'}])
        self.assertEqual(plan['coverage']['questions_with_actions'], 1)
        self.assertIn('q01', plan['coverage']['uncovered_case_ids']); self.assertIn('q02', plan['coverage']['uncovered_case_ids'])
        self.assertNotIn('success_rate', plan); self.assertNotIn('effect', plan)

    def test_new_draft_keeps_exact_action_snapshot_and_same_input_is_idempotent(self):
        inputs = research.draft_input(self.cycle, 'b01', 'zhihu')
        expected = copy.deepcopy(inputs['action_plan'])
        provenance = {'method': 'synthetic_only', 'input_sha256': research.fingerprint(inputs),
                      'optimization_action': {'untrusted_caller_override': True}}
        first = store.save_draft(self.database, self.cycle['id'], 'b01', 'zhihu', synthetic_draft(), provenance, self.cycle['revision'])
        second = store.save_draft(self.database, self.cycle['id'], 'b01', 'zhihu', synthetic_draft(), provenance, self.cycle['revision'])
        self.assertEqual(first['article_id'], second['article_id']); self.assertTrue(second['already_saved'])
        current = store.get_cycle(self.database, self.cycle['id'])
        draft = next(row for row in current['drafts'] if row['article_id'] == first['article_id'])
        self.assertEqual(draft['provenance']['optimization_action'], expected)
        before = self.rows()
        with patch('services.geo_optimization_strategy.BASE_REQUIREMENTS', ('未来引擎的不同要求，仅用于证明旧快照不变。',)):
            changed = store.get_cycle(self.database, self.cycle['id'])
            self.assertNotEqual(changed['optimization_plan']['actions'][0]['writing_requirements'], expected['writing_requirements'])
            self.assertEqual(changed['drafts'][0]['provenance']['optimization_action'], expected)
        self.assertEqual(before, self.rows())

    def test_legacy_draft_provenance_is_not_backfilled_or_rewritten_by_get(self):
        created = store.save_draft(self.database, self.cycle['id'], 'b01', 'zhihu', synthetic_draft(),
                                   {'method': 'synthetic_legacy_fixture'}, self.cycle['revision'])
        with closing(sqlite3.connect(self.database)) as connection, connection:
            row = connection.execute('SELECT provenance FROM geo_content_drafts WHERE article_id=?', (created['article_id'],)).fetchone()
            prior = json.loads(row[0]); prior.pop('optimization_action')
            connection.execute('UPDATE geo_content_drafts SET provenance=? WHERE article_id=?', (json.dumps(prior), created['article_id']))
        before = self.rows(); viewed = store.get_cycle(self.database, self.cycle['id'])
        self.assertNotIn('optimization_action', viewed['drafts'][0]['provenance'])
        self.assertTrue(viewed['optimization_plan']['actions'])
        self.assertEqual(before, self.rows())

    def app_client(self):
        with patch.dict(os.environ, {'DATABASE_PATH': str(self.database), 'COOKIES_DIRECTORY': str(self.root/'cookies'),
                                   'MEDIA_ROOT': str(self.root/'media'), 'DEMO_MODE': 'false', 'SEED_DEMO_DATA': 'false',
                                   'RUN_PUBLISH_SCHEDULER': 'false'}):
            from sau_backend import app
        configuration = patch.dict(app.config, DATABASE_PATH=self.database, ACCESS_CONTROL_ENABLED=False,
                                   TESTING=True, AI_RATE_LIMIT_PER_MINUTE=0)
        configuration.start(); self.addCleanup(configuration.stop)
        return app.test_client()

    def test_api_generation_uses_action_prompt_and_matches_persisted_input_hash(self):
        client = self.app_client()
        expected_inputs = research.draft_input(self.cycle, 'b01', 'zhihu')
        expected_prompt = research.draft_prompt(self.cycle, 'b01', 'zhihu')
        response = Mock(status_code=200)
        response.json.return_value = {'choices': [{'message': {'content': synthetic_draft()}}]}
        request = {'kind': 'draft', 'brief_id': 'b01', 'platform': 'zhihu',
                   'ai_config': {'base_url': 'https://api.example.org/v1', 'api_key': 'synthetic-only-key', 'model': 'synthetic-model'}}
        with patch('requests.post', return_value=response) as provider:
            for _ in range(2):
                result = client.post('/api/geo-content/cycles/'+self.cycle['id']+'/generate', json=request)
                self.assertEqual(result.status_code, 200)
        self.assertEqual(provider.call_count, 1)
        sent = provider.call_args.kwargs['json']['messages'][1]['content']
        self.assertEqual(sent, expected_prompt)
        self.assertIn('action_plan.writing_requirements', sent)
        draft = store.get_cycle(self.database, self.cycle['id'])['drafts'][0]
        self.assertEqual(draft['provenance']['input_sha256'], research.fingerprint(expected_inputs))
        self.assertEqual(draft['provenance']['optimization_action'], expected_inputs['action_plan'])

    def test_native_generation_matches_same_action_prompt_hash_without_cas_rejection(self):
        expected_inputs = research.draft_input(self.cycle, 'b01', 'zhihu')
        expected_prompt = research.draft_prompt(self.cycle, 'b01', 'zhihu'); calls = []

        async def collect(config, **_kwargs):
            calls.append(config.question)
            return {'question': config.question, 'question_submitted': True, 'answer_completed': True,
                    'response': synthetic_draft(), 'environment': {'model_label': 'synthetic-model'}}, self.root/'synthetic-evidence.json'

        bridge = DesktopGeoJobs(self.root); self.addCleanup(bridge.shutdown)
        with patch('services.desktop_geo_jobs.collect_doubao_sources', collect):
            for _ in range(2):
                result = bridge.generate_research(self.cycle['id'], 'draft', 'b01', 'zhihu', {})
                self.assertNotIn('error', result)
                bridge.worker.join(timeout=5)
                self.assertFalse(bridge.worker.is_alive())
                self.assertEqual(bridge.get_task()['status'], 'completed')
        self.assertEqual(calls, [expected_prompt])
        draft = store.get_cycle(self.database, self.cycle['id'])['drafts'][0]
        self.assertEqual(draft['provenance']['input_sha256'], research.fingerprint(expected_inputs))
        self.assertEqual(draft['provenance']['optimization_action'], expected_inputs['action_plan'])


if __name__ == '__main__':
    unittest.main()
