"""Single-case research probes use synthetic records and mocked collection only."""
import copy
import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from db.createTable import initialize_database
from services import geo_content_store as store
from services import geo_research_probe_service as probes
from services.desktop_geo_jobs import DesktopGeoJobs
from services.geo_test_runner import write_json
from services.geo_test_service import build_report, parse_ai_plan, plan_fingerprint


def synthetic_result(question, **changes):
    return {'question': question, 'response': '合成树科技的公开业务资料仍需核对。',
        'question_submitted': True, 'question_submission_attempted': True, 'answer_completed': True,
        'collection_status': 'success', 'source_extraction_status': 'partial',
        'citations': [{'title': '合成资料', 'url': None}], 'displayed_sources': [],
        'visible_citation_count': None, 'visible_source_count': None,
        'environment': {'client_mode': 'work', 'model_label': '测试模型'},
        'timestamp': '2026-01-02T00:00:00+00:00', **changes}


class ProbeFixture:
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.database = self.root / 'database.db'
        initialize_database(self.database)
        profile = {'name': '合成树科技有限公司', 'company': '合成树科技有限公司',
            'aliases': ['合成树科技'], 'entity_type': 'company', 'industry': '软件服务',
            'description': '合成测试资料', 'website': 'https://synthetic.example.org', 'official_urls': []}
        cases = [
            {'category': 'discovery', 'question': '企业如何选择软件服务商，请列出公开候选？', 'intent': '行业需求'},
            {'category': 'brand_identity', 'question': '合成树科技做什么业务，有哪些公开资料？', 'intent': '身份核验'},
            {'category': 'website_guided', 'question': '请核对 https://synthetic.example.org 的业务介绍。', 'intent': '官网核验'}]
        self.plan = parse_ai_plan(json.dumps({'cases': cases}), profile, 3, provenance={'method': 'synthetic'})
        state = {'run_id': 'synthetic-original-round', 'status': 'completed',
            'execution': {'client_mode': 'work', 'requested_model_label': None, 'timeout_seconds': 180},
            'cases': {case['id']: {'status': 'success', 'result': synthetic_result(case['question'])}
                      for case in self.plan['cases']}}
        self.baseline = build_report(self.plan, state)
        self.cycle = store.create_cycle(self.database, self.baseline, 'synthetic-report-reference')
        self.question = self.plan['cases'][1]['question']
        self.result = synthetic_result(self.question, response='合成树科技介绍其官网地址 synthetic.example.org 和软件服务。')
        with sqlite3.connect(self.database) as conn:
            self.original_baseline = conn.execute('SELECT baseline FROM geo_content_cycles').fetchone()[0]

    def native(self, **changes):
        return {'case_id': 'q02', 'run_id': 'a' * 32, 'created_at': '2026-01-02T00:00:00Z',
                'collector_result': self.result, **changes}

    def prototype(self):
        return {'kind': 'private_same_question_probe', 'run_id': 'b' * 32,
            'source_cycle_id': self.cycle['id'], 'source_plan_sha256': self.plan['plan_sha256'],
            'source_baseline_sha256': hashlib.sha256(self.original_baseline.encode()).hexdigest(),
            'profile': self.plan['profile'], 'case': self.plan['cases'][1],
            'selection': {'selected_count': 1, 'selected_case_ids': ['q02']},
            'execution': {'client_mode': 'work', 'requested_model_label': None, 'timeout_seconds': 180,
                          'manual_wait_seconds': None, 'normal_profile_lock': True, 'collector_invocation_limit': 1},
            'collector_result': self.result, 'source_files_unchanged': True, 'source_baseline_unchanged': True}

    def assert_original_unchanged(self):
        with sqlite3.connect(self.database) as conn:
            self.assertEqual(conn.execute('SELECT baseline FROM geo_content_cycles').fetchone()[0], self.original_baseline)
            self.assertEqual(conn.execute('SELECT count(*) FROM geo_content_retests').fetchone()[0], 0)


class ProbeServiceTests(ProbeFixture, unittest.TestCase):
    def test_single_selected_answer_has_three_state_address_observation_without_rate(self):
        saved = probes.attach_probe(self.database, self.cycle['id'], self.native(), source='native_collector', native_task_id='synthetic-task')
        self.assertFalse(saved['before']['website_presence'])
        self.assertTrue(saved['after']['website_presence'])
        self.assertIsNone(saved['after']['official_citation_present'])
        self.assertEqual(saved['after']['citations'][0]['url'], None)
        self.assertEqual(saved['selected_count'], 1)
        self.assertEqual(saved['not_selected_case_ids'], ['q01', 'q03'])
        self.assertNotIn('cohorts', saved)
        self.assertNotIn('brand_mention_rate', saved)
        self.assertIsNone(saved['causal_effect'])
        self.assertEqual(store.get_cycle(self.database, self.cycle['id'])['probes'], [saved])
        self.assert_original_unchanged()

    def test_incomplete_capacity_and_account_private_are_unknown_never_zero(self):
        for changes in (
            {'answer_completed': False},
            {'response': '办公算力紧张，请稍后重试。', 'collection_status': 'failed', 'failure_reason': 'service_capacity_unavailable'},
            {'response': '根据内部资料，合成树科技的官网是 synthetic.example.org。'}):
            with self.subTest(changes=changes):
                result = {**self.result, **changes}
                saved = probes.build_probe(self.cycle, 'q02', result, run_id='c' * 32, source='native_collector')
                self.assertFalse(saved['after']['answer_evaluable'])
                self.assertIsNone(saved['after']['website_presence'])
                self.assertEqual(saved['status'], 'incomplete')

    def test_import_exact_bytes_keeps_hash_and_cannot_claim_native_verification(self):
        raw = json.dumps(self.prototype(), ensure_ascii=False, indent=2).encode('utf-8')
        before = bytes(raw)
        saved = probes.attach_probe(self.database, self.cycle['id'], raw, source='research_prototype_import')
        self.assertEqual(saved['source_probe_sha256'], hashlib.sha256(raw).hexdigest())
        self.assertEqual(saved['source'], 'research_prototype_import')
        self.assertFalse(saved['native_verified'])
        self.assertEqual(raw, before)
        self.assert_original_unchanged()

    def test_import_rejects_path_wrong_cycle_question_plan_and_multiple_selection(self):
        for changes in ({'source_cycle_id': 'another-cycle'}, {'source_plan_sha256': '0' * 64},
                        {'case': {**self.plan['cases'][1], 'question': 'changed question'}},
                        {'selection': {'selected_count': 2, 'selected_case_ids': ['q01', 'q02']}},
                        {'execution': {**self.prototype()['execution'], 'requested_model_label': 'other model'}}):
            with self.subTest(changes=changes):
                raw = json.dumps({**self.prototype(), **changes}).encode()
                with self.assertRaises(ValueError):
                    probes.attach_probe(self.database, self.cycle['id'], raw, source='research_prototype_import')
        with self.assertRaises(ValueError):
            probes.attach_probe(self.database, self.cycle['id'], 'C:/arbitrary/result.json', source='research_prototype_import')
        self.assertEqual(store.get_cycle(self.database, self.cycle['id'])['probes'], [])

    def test_guided_question_is_never_a_without_url_acceptance_test(self):
        saved = probes.build_probe(self.cycle, 'q03', synthetic_result(self.plan['cases'][2]['question']),
                                   run_id='d' * 32, source='native_collector')
        self.assertTrue(saved['website_in_question'])
        self.assertFalse(saved['website_test_without_url'])

    def test_literal_domain_presence_neither_counts_lookalike_nor_proves_positive_claim(self):
        saved = probes.build_probe(self.cycle, 'q02', {**self.result,
            'response': 'No synthetic.example.org website was found.'}, run_id='e' * 32, source='native_collector')
        self.assertTrue(saved['after']['website_presence'], 'this records text occurrence, including negation')
        for text in ('synthetic.example.org.evil.test', 'fake-synthetic.example.org', 'evil.synthetic.example.org',
                     'www.synthetic.example.org.evil.test', 'evil-www.synthetic.example.org',
                     'evil.www.synthetic.example.org'):
            self.assertFalse(probes._address_present(text, self.cycle['profile']))

    def test_answer_www_address_is_present_with_either_configured_www_variant(self):
        for website in ('https://synthetic.example.org', 'https://www.synthetic.example.org'):
            cycle = copy.deepcopy(self.cycle)
            cycle['profile']['website'] = website
            saved = probes.build_probe(cycle, 'q02', {**self.result,
                'response': '其官网地址为 https://www.synthetic.example.org/about/ 。'},
                run_id='f' * 32, source='native_collector')
            self.assertTrue(saved['after']['website_presence'])
            self.assertTrue(probes._address_present('synthetic.example.org', cycle['profile']))

    def test_question_with_bare_www_address_is_explicitly_prompted(self):
        cycle = copy.deepcopy(self.cycle)
        question = '合成树科技的网址 www.synthetic.example.org 介绍了什么业务？'
        cycle['test_plan']['cases'][1]['question'] = question
        cycle['test_plan']['plan_sha256'] = plan_fingerprint(cycle['test_plan'])
        cycle['baseline']['cases'][1]['question'] = question
        saved = probes.build_probe(cycle, 'q02', synthetic_result(question),
                                   run_id='1' * 32, source='native_collector')
        self.assertTrue(saved['website_in_question'])
        self.assertFalse(saved['website_test_without_url'])

    def test_import_is_idempotent_but_changed_same_id_does_not_overwrite(self):
        raw = json.dumps(self.prototype()).encode()
        first = probes.attach_probe(self.database, self.cycle['id'], raw, source='research_prototype_import')
        self.assertEqual(probes.attach_probe(self.database, self.cycle['id'], raw, source='research_prototype_import'), first)
        changed = self.prototype()
        changed['collector_result'] = {**self.result, 'response': 'a different answer'}
        with self.assertRaises(ValueError):
            probes.attach_probe(self.database, self.cycle['id'], json.dumps(changed).encode(), source='research_prototype_import')
        self.assertEqual(len(store.get_cycle(self.database, self.cycle['id'])['probes']), 1)

    def test_reading_older_database_without_probe_table_is_read_only(self):
        with sqlite3.connect(self.database) as conn:
            conn.execute('DROP TABLE geo_research_probes')
        before = self.database.read_bytes()
        self.assertEqual(store.get_cycle(self.database, self.cycle['id'])['probes'], [])
        self.assertEqual(before, self.database.read_bytes())


class DesktopProbeTests(ProbeFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.jobs = DesktopGeoJobs(self.root)
        self.addCleanup(self.jobs.shutdown)

    def launch(self, collect, case_id='q02'):
        with patch('services.desktop_geo_jobs.collect_doubao_sources', collect), \
             patch('services.desktop_geo_jobs.edge_executable', return_value='ordinary-edge'), \
             patch('services.desktop_geo_jobs.chrome_executable', return_value='ordinary-chrome'):
            ack = self.jobs.start_research_probe(self.cycle['id'], case_id)
            if 'error' not in ack:
                self.jobs.worker.join(timeout=5)
                self.assertFalse(self.jobs.worker.is_alive())
        return ack

    def test_native_bridge_sends_only_unchanged_case_once_and_persists_independent_task(self):
        calls = []
        async def collect(config, *, progress):
            calls.append(config)
            self.assertTrue((config.output_root.parent / 'single_probe_intent.json').is_file())
            result = {**self.result, 'question': config.question}
            path = config.output_root / 'synthetic-result' / 'result.json'
            write_json(path, result)
            return result, path
        ack = self.launch(collect)
        self.assertEqual(len(calls), 1)
        config = calls[0]
        self.assertEqual(config.question, self.question)
        self.assertEqual(config.profile_dir, self.root / 'doubao/browser-profile')
        self.assertEqual((config.client_mode, config.requested_model_label, config.timeout_seconds,
                          config.manual_wait_seconds, config.headed), ('work', None, 180, None, True))
        self.assertEqual((config.executable_path, config.fallback_executable_path), ('ordinary-edge', 'ordinary-chrome'))
        task = self.jobs.get_task(ack['id'])
        self.assertEqual(task['kind'], 'probe')
        self.assertEqual(task['status'], 'completed')
        self.assertNotIn('report', task['result'])
        self.assertTrue(task['result']['probe']['native_verified'])
        self.assertTrue((Path(task['run_dir']) / 'single_case_probe.json').is_file())
        self.assertEqual(len(store.get_cycle(self.database, self.cycle['id'])['probes']), 1)
        self.assert_original_unchanged()

    def test_unknown_case_or_cycle_never_collects(self):
        async def collect(*args, **kwargs):
            self.fail('invalid selection must never collect')
        self.assertIn('error', self.launch(collect, '../arbitrary-path'))
        self.assertIn('error', self.jobs.start_research_probe('not-a-cycle', 'q02'))

    def test_active_task_cannot_start_another_probe(self):
        self.jobs.job = {'status': 'running', 'id': 'synthetic-active'}
        async def collect(*args, **kwargs):
            self.fail('must not collect while busy')
        self.assertIn('error', self.launch(collect))

    def test_exception_does_not_retry_or_borrow_old_answer(self):
        calls = []
        async def collect(config, *, progress):
            calls.append(config.question)
            raise TimeoutError('submission may have occurred')
        ack = self.launch(collect)
        task = self.jobs.get_task(ack['id'])
        self.assertEqual(calls, [self.question])
        record = task['result']['probe']
        self.assertEqual(record['status'], 'needs_review')
        self.assertEqual(record['after']['response'], '')
        self.assertIsNone(record['after']['website_presence'])
        self.assert_original_unchanged()

    def test_foreign_result_path_cannot_be_read_as_native_evidence(self):
        outside = self.root / 'foreign-result.json'
        write_json(outside, self.result)
        original = outside.read_bytes()
        async def collect(config, *, progress):
            return self.result, outside
        ack = self.launch(collect)
        record = self.jobs.get_task(ack['id'])['result']['probe']
        self.assertEqual(record['status'], 'needs_review')
        self.assertIsNone(record['evidence'])
        self.assertEqual(outside.read_bytes(), original)


class ExternalObservationTests(ProbeFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.cycle = store.save_facts(self.database, self.cycle['id'], [
            {'text': '合成树科技官网公开介绍软件开发服务。',
             'source_url': 'https://synthetic.example.org/about', 'confirmed': True}], self.cycle['revision'])
        self.raw = '备案域名为 synthetic.example.org。\r\n仅见工商记录，未找到公开软件业务资料。\n'
        self.review = {'status': 'not_acknowledged', 'by': 'assistant',
            'quote': '仅见工商记录，未找到公开软件业务资料。', 'reference_fact_ids': ['f01'],
            'delivery_capability': 'unknown', 'note': '仅核对这段回答是否采用已确认的公开业务资料。'}

    def external(self, metadata=None, raw=None):
        return probes.attach_external_observation(self.database, self.cycle['id'], 'q02',
            self.raw if raw is None else raw, {} if metadata is None else metadata)

    def test_domain_text_present_does_not_override_external_business_negative_or_unknown_conditions(self):
        record = self.external({'business_relationship_review': self.review})
        self.assertEqual(record['source'], 'user_reported_external')
        self.assertFalse(record['native_verified'])
        self.assertEqual(record['after']['response'], self.raw)
        self.assertEqual(record['response_sha256'], hashlib.sha256(self.raw.encode('utf-8')).hexdigest())
        self.assertTrue(record['after']['website_presence'])
        self.assertIsNone(record['after']['answer_evaluable'])
        self.assertEqual(record['question_role'], 'cycle_anchor_only')
        self.assertIsNone(record['observed_question'])
        self.assertTrue(all(value is None for value in record['conditions'].values()))
        self.assertIsNone(record['website_test_without_url'])
        self.assertEqual(record['business_relationship_review']['status'], 'not_acknowledged')
        self.assertEqual(record['business_relationship_review']['delivery_capability'], 'unknown')
        self.assertIsNone(record['after']['official_citation_present'])
        self.assertNotIn('source_files_unchanged', record)
        self.assertIsNone(record['execution'])
        self.assertEqual(record['selected_count'], 1)
        self.assert_original_unchanged()

    def test_review_rejects_nonliteral_quote_unapproved_fact_and_delivery_claim(self):
        for changes in ({'quote': 'The model never said this'}, {'reference_fact_ids': ['f99']},
                        {'delivery_capability': 'proven'}, {'by': 'automatic_fact_judge'}):
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError):
                    self.external({'business_relationship_review': {**self.review, **changes}})
        self.assertEqual(store.get_cycle(self.database, self.cycle['id'])['probes'], [])

    def test_explicit_external_question_is_compared_without_fabricating_missing_model_or_mode(self):
        record = self.external({'question': '另一个不同的问题', 'answer_complete': True})
        self.assertFalse(record['conditions']['same_question'])
        self.assertIsNone(record['conditions']['model_label_match'])
        self.assertIsNone(record['conditions']['client_mode_match'])
        same = self.external({'question': self.question, 'client_mode': 'work', 'model_label': '测试模型',
                              'answer_complete': True})
        self.assertTrue(all(value is True for value in same['conditions'].values()))
        self.assertEqual(same['after']['completeness_basis'], 'user_statement')
        self.assertFalse(same['native_verified'])

    def test_external_private_or_explicitly_incomplete_text_keeps_address_unknown(self):
        for metadata, raw in (({'answer_complete': False}, self.raw),
                              ({}, '根据内部资料，官网为 synthetic.example.org。')):
            record = self.external(metadata, raw)
            self.assertFalse(record['after']['answer_evaluable'])
            self.assertIsNone(record['after']['website_presence'])
            with self.assertRaises(ValueError):
                probes.review_probe_business(self.database, self.cycle['id'], record['id'],
                    {**self.review, 'quote': raw})

    def test_review_existing_native_record_only_adds_attributed_review_and_preserves_answer(self):
        saved = probes.attach_probe(self.database, self.cycle['id'], self.native(), source='native_collector')
        self.assertIsNone(saved['business_relationship_review'])
        review = {**self.review, 'status': 'acknowledged', 'by': 'operator', 'quote': self.result['response']}
        revised = probes.review_probe_business(self.database, self.cycle['id'], saved['id'], review)
        self.assertEqual(saved['after'], revised['after'])
        self.assertEqual(revised['business_relationship_review']['by'], 'operator')
        self.assertEqual(revised['business_relationship_review']['reference_facts'][0]['id'], 'f01')
        self.assertIsNone(revised['causal_effect'])
        self.assert_original_unchanged()

    def test_reimport_is_idempotent_and_does_not_silently_overwrite_review(self):
        first = self.external({'business_relationship_review': self.review})
        repeated = self.external()
        self.assertEqual(repeated, first)
        self.assertEqual(len(store.get_cycle(self.database, self.cycle['id'])['probes']), 1)
        with self.assertRaises(ValueError):
            self.external({'native_verified': True})
        with self.assertRaises(ValueError):
            probes.review_probe_business(self.database, self.cycle['id'], 'not-this-cycle-probe', self.review)
        self.assert_original_unchanged()


if __name__ == '__main__':
    unittest.main()
