"""Synthetic research/drafts verify behavior; no real platform publication."""
import copy
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock, patch

from db.createTable import initialize_database
from services import geo_content_research as research, geo_content_store as store
from services.article_service import get_article
from services.desktop_geo_jobs import DesktopGeoJobs
from services.geo_test_service import build_report
from services.project_service import create_project, delete_project, ProjectHasArticlesError
from tests.test_geo_tests import plan, answer


def report():
    value = plan()
    state = {'run_id': 'synthetic-baseline', 'status': 'completed', 'execution': {'client_mode': 'consumer', 'requested_model_label': 'synthetic-model'}, 'cases': {}}
    for case in value['cases']:
        result = answer(case['question'], environment={'client_mode': 'consumer', 'model_label': 'synthetic-model'}, timestamp='2026-10-01T00:00:00Z')
        result['citations'] = [{'title': '合成选型资料', 'url': None}]
        result['source_extraction_status'] = 'partial'
        state['cases'][case['id']] = {'status': 'completed', 'result': result}
    return build_report(value, state)


def facts():
    return [{'text': '这是合成事实，仅用于验证文章写作链路', 'source_url': 'https://example.org/about', 'confirmed': True}]


def analysis():
    return json.dumps({'diagnosis': '合成编辑任务，非曝光结论', 'briefs': [{'topic': '如何选择模型接入方案', 'case_ids': ['q01'],
        'why': '基于合成采集问题', 'outline': ['适用范围', '公开依据'], 'missing_evidence': ['需要客户核对能力边界'], 'source_ids': ['s001'], 'priority': 'high'}]}, ensure_ascii=False)


def draft():
    return json.dumps({'title': '合成稿：模型接入方案的选择', 'summary': '仅用于流程测试的合成文章',
        'content': '据公开资料自述，这是合成事实。选择服务时应核对使用范围与明确限制。' * 12 + '\n资料来源：https://example.org/about',
        'tags': ['合成测试'], 'used_fact_ids': ['f01']}, ensure_ascii=False)


class ResearchFixture:
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.database = initialize_database(self.root / 'database.db')
        self.baseline = report()
        self.cycle = store.create_cycle(self.database, self.baseline, 'synthetic-report')

    def prepare_facts(self):
        self.cycle = store.save_facts(self.database, self.cycle['id'], facts(), self.cycle['revision'])
        return self.cycle


class TestResearchLoop(ResearchFixture, unittest.TestCase):
    def test_cycle_list_identity_keeps_the_stored_baseline_instead_of_current_project(self):
        with closing(sqlite3.connect(self.database)) as connection, connection:
            stored = json.loads(connection.execute('SELECT baseline FROM geo_content_cycles WHERE id=?', (self.cycle['id'],)).fetchone()[0])
            stored['profile'].update(website='https://example.org/original-product', entity_type='product')
            connection.execute('UPDATE geo_content_cycles SET baseline=? WHERE id=?', (json.dumps(stored), self.cycle['id']))
            connection.execute('UPDATE projects SET website=?,entity_type=? WHERE id=?',
                               ('https://example.org/current-company', 'company', self.cycle['project_id']))
        item = next(row for row in store.list_cycles(self.database) if row['id'] == self.cycle['id'])
        self.assertEqual(item['website'], 'https://example.org/original-product')
        self.assertEqual(item['entity_type'], 'product')
        self.assertEqual(item['draft_count'], 0)
        self.assertNotIn('baseline', item)
        with closing(sqlite3.connect(self.database)) as connection, connection:
            stored['profile'].pop('website')
            stored['profile'].pop('entity_type')
            connection.execute('UPDATE geo_content_cycles SET baseline=? WHERE id=?', (json.dumps(stored), self.cycle['id']))
        legacy = next(row for row in store.list_cycles(self.database) if row['id'] == self.cycle['id'])
        self.assertEqual((legacy['website'], legacy['entity_type']), ('', ''))

    def test_later_cited_community_remains_available_to_editorial_planning(self):
        baseline = copy.deepcopy(self.baseline)
        baseline['cases'][0]['citations'] = [
            {'title': f'公开技术资料 {index}', 'url': f'https://docs.example.org/{index}'}
            for index in range(24)
        ] + [{'title': '后续引用的社区实操文章', 'url': 'https://blog.csdn.net/example/article/details/123'}]
        observations = research.research_observations(baseline)
        community = next(source for source in observations['sources'] if source['domain'] == 'blog.csdn.net')
        self.assertIn(community['id'], observations['briefs'][0]['source_ids'])

    def test_report_produces_traceable_work_and_unknown_sources_stay_unknown(self):
        self.assertEqual(self.cycle['analysis']['method'], 'rule_observations_not_ai')
        self.assertEqual(self.cycle['test_plan']['plan_sha256'], plan()['plan_sha256'])
        self.assertTrue(any(source['status'] == 'url_unknown' for source in self.cycle['observations']['sources']))
        self.assertIn('不可判断', self.cycle['observations']['cases'][0]['finding'])
        self.assertEqual(self.cycle['facts'], [])
        self.assertEqual(store.create_cycle(self.database, self.baseline, 'same-report')['id'], self.cycle['id'])
        self.assertEqual(len(store.list_cycles(self.database)), 1)

    def test_cannot_mix_customers_or_delete_project_with_research(self):
        other = create_project(self.database, name='其他合成客户', website='', product='', industry='工业', description='维护服务', keywords=[], competitors=[])
        with self.assertRaisesRegex(ValueError, '客户'):
            store.create_cycle(self.database, self.baseline, 'synthetic-report', other['id'])
        with self.assertRaises(ProjectHasArticlesError):
            delete_project(self.database, self.cycle['project_id'])

    def test_report_question_tampering_rejected(self):
        changed = copy.deepcopy(self.baseline)
        changed['cases'][0]['question'] = '被修改的不同测试问题'
        with self.assertRaisesRegex(ValueError, '变更'):
            store.create_cycle(self.database, changed, 'changed')

    def test_no_content_ideas_from_failed_answers(self):
        changed = copy.deepcopy(self.baseline)
        for case in changed['cases']:
            case['assessment']['answer_evaluable'] = False
        with self.assertRaisesRegex(ValueError, '有效回答'):
            store.create_cycle(self.database, changed, 'failed')

    def test_only_confirmed_facts_can_generate_drafts(self):
        with self.assertRaisesRegex(ValueError, '核对'):
            research.draft_prompt(self.cycle, 'b01', 'zhihu')
        with self.assertRaisesRegex(ValueError, '确认'):
            store.save_facts(self.database, self.cycle['id'], [{**facts()[0], 'confirmed': False}], self.cycle['revision'])
        self.prepare_facts()
        prompt = research.draft_prompt(self.cycle, 'b01', 'xiaohongshu')
        self.assertIn('合成事实', prompt)
        self.assertIn('不能承诺 AI 收录', prompt)
        self.assertNotIn(self.baseline['cases'][0]['response'], prompt)

    def test_invalid_fact_urls_and_platforms_are_validation_errors(self):
        for value in [123, 'file:///C:/private', 'https://user:pass@example.org/']:
            with self.assertRaises(ValueError):
                research.normalize_facts([{**facts()[0], 'source_url': value}])
        for platform in [{}, 'unimplemented-platform']:
            with self.assertRaises(ValueError):
                research.draft_input(self.cycle, 'b01', platform)

    def test_ai_plan_must_reference_real_questions_and_observed_sources(self):
        payload = json.loads(analysis())
        payload['briefs'][0]['source_ids'] = ['invented-source']
        with self.assertRaisesRegex(ValueError, '来源'):
            store.save_analysis(self.database, self.cycle['id'], json.dumps(payload), {'method': 'fixture_not_real_ai'}, self.cycle['revision'])
        updated = store.save_analysis(self.database, self.cycle['id'], analysis(), {'method': 'fixture_not_real_ai'}, self.cycle['revision'])
        self.assertEqual(updated['analysis']['briefs'][0]['case_ids'], ['q01'])

    def test_stale_ai_results_cannot_overwrite_changed_facts(self):
        old_revision = self.cycle['revision']
        self.prepare_facts()
        with self.assertRaisesRegex(ValueError, '变更'):
            store.save_analysis(self.database, self.cycle['id'], analysis(), {'method': 'fixture_not_real_ai'}, old_revision)

    def test_draft_saved_to_normal_content_library_with_lineage_and_no_duplicate(self):
        self.prepare_facts()
        first = store.save_draft(self.database, self.cycle['id'], 'b01', 'zhihu', draft(), {'method': 'fixture_not_real_ai'}, self.cycle['revision'])
        second = store.save_draft(self.database, self.cycle['id'], 'b01', 'zhihu', draft(), {'method': 'fixture_not_real_ai'}, self.cycle['revision'])
        self.assertEqual(first['article_id'], second['article_id'])
        article = get_article(self.database, first['article_id'])
        self.assertEqual(article['status'], 'draft')
        self.assertEqual(article['project_id'], self.cycle['project_id'])
        lineage = store.article_research(self.database, article['id'])
        self.assertEqual(lineage['provenance']['baseline_run_id'], self.baseline['run_id'])
        self.assertEqual(lineage['provenance']['used_fact_ids'], ['f01'])
        self.assertEqual(store.get_cycle(self.database, self.cycle['id'])['drafts'][0]['jobs'], [])

    def test_unsupported_citations_not_accepted_in_article(self):
        self.prepare_facts()
        payload = json.loads(draft())
        payload['content'] += '\nhttps://invented.example.org/fake-proof'
        with self.assertRaisesRegex(ValueError, '网址'):
            store.save_draft(self.database, self.cycle['id'], 'b01', 'zhihu', json.dumps(payload), {'method': 'fixture_not_real_ai'}, self.cycle['revision'])

    def test_article_and_lineage_are_atomic(self):
        self.prepare_facts()
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute("CREATE TRIGGER synthetic_link_failure BEFORE INSERT ON geo_content_drafts BEGIN SELECT RAISE(ABORT,'synthetic failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            store.save_draft(self.database, self.cycle['id'], 'b01', 'zhihu', draft(), {'method': 'fixture_not_real_ai'}, self.cycle['revision'])
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM articles').fetchone()[0], 0)

    def test_comparison_does_not_multiply_small_samples_or_mix_models(self):
        after = copy.deepcopy(self.baseline)
        after['run_id'] = 'synthetic-retest'
        for case in after['cases']:
            case['collected_at'] = '2026-10-02T00:00:00Z'
        after['cases'][0]['assessment']['brand_mentioned'] = False
        after['cases'][1]['assessment']['answer_evaluable'] = False
        compared = research.compare_reports(self.baseline, after, ['https://new.example.org/post'])
        self.assertTrue(compared['conditions_match'])
        self.assertEqual(compared['cohorts'][0]['paired_answers'], 1)
        self.assertEqual(compared['cohorts'][0]['before_brand_rate'], 1)
        self.assertEqual(compared['cohorts'][0]['after_brand_rate'], 0)
        self.assertIsNone(compared['publication_citations'][0]['observed_in_citations'])
        self.assertIsNone(compared['cohorts'][0]['official_citation_before'])
        after['cases'][0]['model_label'] = 'different-model'
        compared = research.compare_reports(self.baseline, after)
        self.assertFalse(compared['conditions_match'])
        self.assertTrue(all(row['change'] is None for row in compared['cohorts']))

    def test_publications_only_include_successful_non_demo_urls(self):
        self.prepare_facts()
        created = store.save_draft(self.database, self.cycle['id'], 'b01', 'zhihu', draft(), {'method': 'fixture_not_real_ai'}, self.cycle['revision'])
        with closing(sqlite3.connect(self.database)) as connection, connection:
            for status, demo, url in [('failed', 0, 'https://example.org/failure'), ('success', 1, 'https://example.org/demo'), ('success', 0, 'https://example.org/real-result')]:
                connection.execute('INSERT INTO publish_jobs(article_id,platform,status,demo,result_url) VALUES(?,?,?,?,?)', (created['article_id'], 'zhihu', status, demo, url))
        self.assertEqual(store.get_cycle(self.database, self.cycle['id'])['publications'], ['https://example.org/real-result'])

    def test_older_collection_is_not_a_post_baseline_retest(self):
        after = copy.deepcopy(self.baseline)
        after['run_id'] = 'synthetic-older-run'
        for case in after['cases']:
            case['collected_at'] = '2026-09-30T00:00:00Z'
        compared = research.compare_reports(self.baseline, after)
        self.assertFalse(compared['conditions_match'])
        self.assertIn('复测采集时间未晚于基线', compared['reasons'])

    def test_industrial_client_uses_its_own_questions_and_owner_facts(self):
        from services.geo_test_service import parse_ai_plan
        profile = {'name':'合成工业客户','industry':'工业设备维护','description':'数控设备维护服务'}
        questions = {'cases':[
            {'category':'discovery','question':'企业选择设备维护服务时应核对哪些现场能力？','intent':'采购需求'},
            {'category':'discovery','question':'预防性维护的验收清单应包含哪些指标？','intent':'维护方案'},
            {'category':'brand_identity','question':'合成工业客户的设备维护服务有哪些公开说明？','intent':'品牌核验'}]}
        value = parse_ai_plan(json.dumps(questions), profile, 3, provenance={'method':'fixture_not_real_ai'})
        state = {'run_id':'synthetic-industrial','status':'completed','cases':{case['id']:{'status':'completed','result':answer(case['question'])} for case in value['cases']}}
        created = store.create_cycle(self.database, build_report(value, state), 'synthetic-industrial')
        created = store.save_facts(self.database, created['id'], [{'text':'合成客户说明提供设备巡检服务','source_url':'','confirmed':True}], created['revision'])
        prompt = research.draft_prompt(created,'b01','owned_web')
        self.assertIn('设备维护', prompt)
        self.assertIn('owner_statement', prompt)
        self.assertNotIn('懂AI', prompt)

    def test_native_research_stores_evidence_and_same_input_never_resubmits(self):
        self.prepare_facts()
        calls = []
        async def collect(config, **_kwargs):
            calls.append(config.question)
            self.assertEqual(config.submission_format, 'verbatim_fenced')
            return ({'question': config.question, 'question_submitted': True, 'answer_completed': True,
                     'response': draft(), 'environment': {'model_label': 'synthetic-model'}}, self.root/'synthetic-evidence.json')
        bridge = DesktopGeoJobs(self.root)
        with patch('services.desktop_geo_jobs.collect_doubao_sources', collect):
            for _ in range(2):
                response = bridge.generate_research(self.cycle['id'], 'draft', 'b01', 'zhihu', {})
                self.assertNotIn('error', response)
                bridge.worker.join(timeout=5)
                self.assertEqual(bridge.get_task()['status'], 'completed')
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(store.get_cycle(self.database, self.cycle['id'])['drafts']), 1)
        self.assertEqual(DesktopGeoJobs(self.root).get_task()['cycle_id'], self.cycle['id'])

    def test_native_pre_submission_failure_is_explicit_and_not_a_missing_answer(self):
        for reason, attempted in (('editor_readback_mismatch_not_submitted', False),
                                  ('submission_fresh_user_context_not_confirmed', False),
                                  ('answer_timeout', True),
                                  ('editor_readback_mismatch_not_submitted', True)):
            async def collect(config, **_kwargs):
                return ({'question': config.question, 'question_submitted': False, 'answer_completed': False,
                         'question_submission_attempted': attempted, 'failure_reason': reason}, self.root/'synthetic-failure.json')
            bridge = DesktopGeoJobs(self.root)
            with self.subTest(reason=reason, attempted=attempted), \
                 patch('services.desktop_geo_jobs.collect_doubao_sources', collect):
                response = bridge.generate_research(self.cycle['id'], 'analysis', None, None, {})
                self.assertNotIn('error', response)
                bridge.worker.join(timeout=5)
                task = bridge.get_task()
                self.assertEqual(task['status'], 'failed')
                if attempted is False:
                    self.assertIn('发送前核对未通过，问题尚未发送，证据已保存', task['message'])
                    self.assertNotIn('未返回完整内容', task['message'])
                else:
                    self.assertNotIn('尚未发送', task['message'])
                    self.assertIn(reason, task['message'])

    def test_native_retest_uses_exact_baseline_and_associates_report(self):
        async def collect(value, config, progress):
            self.assertEqual(value['plan_sha256'], self.cycle['test_plan']['plan_sha256'])
            self.assertEqual(config.collector.requested_model_label, 'synthetic-model')
            result = copy.deepcopy(self.baseline)
            result['run_id'] = 'synthetic-new-run'
            for case in result['cases']:
                case['collected_at'] = '2026-10-02T00:00:00Z'
            return result
        bridge = DesktopGeoJobs(self.root)
        with patch('services.desktop_geo_jobs.run_test_plan', collect):
            bridge.start_research_retest(self.cycle['id'])
            bridge.worker.join(timeout=5)
        self.assertEqual(bridge.get_task()['status'], 'completed')
        self.assertEqual(len(store.get_cycle(self.database, self.cycle['id'])['retests']), 1)


class TestResearchAPI(ResearchFixture, unittest.TestCase):
    # Keep API tests separate from providers; fixture HTTP data is synthetic.
    def setUp(self):
        super().setUp()
        from sau_backend import app
        self.app = app
        self.configuration = patch.dict(app.config, DATABASE_PATH=self.database, ACCESS_CONTROL_ENABLED=False, TESTING=True, AI_RATE_LIMIT_PER_MINUTE=0)
        self.configuration.start()
        self.addCleanup(self.configuration.stop)
        self.client = app.test_client()

    def test_api_generation_uses_report_evidence_and_preserves_editorial_draft(self):
        identifier = self.cycle['id']
        response = self.client.put('/api/geo-content/cycles/'+identifier, json={'facts': facts(), 'revision': self.cycle['revision']})
        self.assertEqual(response.status_code, 200)
        response = Mock(status_code=200)
        response.json.return_value = {'choices': [{'message': {'content': draft()}}]}
        request = {'kind': 'draft', 'brief_id': 'b01', 'platform': 'zhihu', 'ai_config': {'base_url': 'https://api.example.org/v1', 'api_key': 'synthetic-session-key', 'model': 'synthetic-model'}}
        with patch('requests.post', return_value=response) as provider:
            for _ in range(2):
                result = self.client.post('/api/geo-content/cycles/'+identifier+'/generate', json=request)
                self.assertEqual(result.status_code, 200, result.json)
            self.assertEqual(provider.call_count, 1)
            sent = provider.call_args.kwargs['json']['messages'][1]['content']
            self.assertIn(self.cycle['analysis']['briefs'][0]['topic'], sent)
            self.assertIn('合成事实', sent)
            self.assertNotIn('synthetic-session-key', result.get_data(as_text=True))
        article_id = result.json['data']['article_id']
        self.assertIsNotNone(self.client.get('/api/geo-content/articles/'+str(article_id)).json['data'])
        self.assertEqual(self.client.get('/api/articles/'+str(article_id)).json['data']['status'], 'draft')

    def test_api_access_control_validation_and_persistent_cycle(self):
        result = self.client.post('/api/geo-content/cycles', json={'report': self.baseline})
        self.assertEqual(result.status_code, 201)
        self.assertEqual(result.json['data']['id'], self.cycle['id'])
        self.assertEqual(len(self.client.get('/api/geo-content/cycles').json['data']), 1)
        self.assertEqual(self.client.get('/api/geo-content/cycles/missing').status_code, 404)
        self.assertEqual(self.client.post('/api/geo-content/cycles/'+self.cycle['id']+'/generate', json={'kind':'draft','brief_id':'b01','platform':'zhihu'}).status_code, 400)
        with patch.dict(self.app.config, ACCESS_CONTROL_ENABLED=True):
            self.assertEqual(self.client.get('/api/geo-content/cycles').status_code, 401)


if __name__ == '__main__':
    unittest.main()
