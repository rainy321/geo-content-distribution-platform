"""Synthetic baseline discovery paths; no client data, network or provider calls."""
import copy
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from db.createTable import initialize_database
from services import geo_content_store as store
from services.geo_content_research import plan_from_report
from services.geo_discovery_funnel import discovery_funnel
from services.geo_feedback_diagnostics import diagnose_cycle
from services.geo_test_service import build_report, parse_ai_plan


def synthetic_report(*, guided_sources=None, guided_citations=None):
    profile = {'entity_type': 'product', 'name': '合成数据门', 'industry': '合成软件',
               'description': '仅供离线测试，不代表真实产品。', 'website': 'https://example.org/products/gate/'}
    cases = [
        {'category': 'discovery', 'question': '团队怎样选择数据服务接入工具？', 'intent': '自然需求'},
        {'category': 'brand_identity', 'question': '合成数据门是什么，由谁运营？', 'intent': '品牌核验'},
        {'category': 'website_guided', 'question': '请看 https://example.org/products/gate/ 核对功能。', 'intent': '官网核验'},
    ]
    plan = parse_ai_plan(json.dumps({'cases': cases}, ensure_ascii=False), profile, 3,
                         provenance={'method': 'synthetic_offline_only'})
    state = {'run_id': 'synthetic-funnel-run', 'status': 'completed',
             'execution': {'client_mode': 'consumer', 'requested_model_label': 'synthetic-model'}, 'cases': {}}
    answers = ('应比较接入范围与管理需求。', '未找到合成数据门的运营主体与官网。',
               '给定页面介绍合成数据门提供接入与用量管理，不能代表自然检索发现。')
    for index, case in enumerate(plan['cases']):
        result = {'question': case['question'], 'question_submitted': True, 'question_submission_attempted': True,
                  'answer_completed': True, 'response': answers[index], 'collection_status': 'success',
                  'source_extraction_status': 'captured' if index < 2 else 'partial',
                  'source_absence_verified': index < 2,
                  'displayed_sources': [] if index < 2 else guided_sources,
                  'citations': [] if index < 2 else guided_citations,
                  'environment': {'client_mode': 'consumer', 'model_label': 'synthetic-model'},
                  'timestamp': '2026-10-01T00:00:00Z'}
        state['cases'][case['id']] = {'status': 'completed', 'result': result}
    return build_report(plan, state)


def cycle_fixture(**kwargs):
    report = synthetic_report(**kwargs)
    return {'id': 'synthetic-funnel-cycle', 'project_id': 7, 'baseline_source': 'native_report',
            'baseline': report, 'profile': report['profile'], 'test_plan': plan_from_report(report),
            'facts': [{'id': 'f01', 'confirmed': True, 'text': '离线产品资料，仅用于测试。',
                       'source_url': 'https://example.org/products/gate/'}]}


class DiscoveryFunnelTests(unittest.TestCase):
    def stages(self, result):
        return {row['key']: row for row in result['stages']}

    def test_real_failure_shape_keeps_guided_unknown_and_negative_brand_mentions_out_of_success(self):
        cycle = cycle_fixture()
        self.assertTrue(cycle['baseline']['cases'][1]['assessment']['brand_mentioned'])
        result = discovery_funnel(cycle)
        self.assertTrue(result['supported'])
        self.assertTrue(result['comparability']['matched'])
        self.assertEqual([row['state'] for row in result['stages']], ['not_observed', 'not_observed', 'unknown'])
        self.assertEqual([row['key'] for row in result['priorities']], ['retrieval_discovery', 'scenario_coverage'])
        self.assertIn('给网址题来源尚未确认', result['priorities'][0]['reason'])
        guided = self.stages(result)['website_guided']
        self.assertEqual(guided['unknown_case_ids'], ['q03'])
        self.assertIsNone(guided['cases'][0]['signal_value'])
        self.assertIsNone(guided['cases'][0]['official_citation_present'])
        for key in ('success_rate', 'conversion_rate', 'business_recognized', 'fact_accuracy'):
            self.assertNotIn('"' + key + '"', json.dumps(result))

    def test_explicit_guided_source_is_not_natural_discovery_or_proof_of_effect(self):
        source = {'title': '合成产品资料', 'url': 'https://example.org/products/gate/'}
        result = discovery_funnel(cycle_fixture(guided_sources=[source], guided_citations=[source]))
        self.assertEqual([row['state'] for row in result['stages']], ['not_observed', 'not_observed', 'observed'])
        self.assertIn('不能替代不带网址的品牌检索', result['priorities'][0]['reason'])
        self.assertIn('scenario_coverage', [row['key'] for row in result['priorities']])

    def test_unknown_or_present_body_citation_never_becomes_total_official_absence(self):
        for value in (None, True):
            with self.subTest(citation=value):
                cycle = cycle_fixture()
                cycle['baseline']['cases'][1]['assessment']['official_citation_present'] = value
                result = discovery_funnel(cycle)
                self.assertEqual(self.stages(result)['brand_search']['state'], 'not_observed')
                self.assertNotIn('retrieval_discovery', [row['key'] for row in result['priorities']])

    def test_missing_and_excluded_questions_remain_unknown_not_zero(self):
        for change in ('missing', 'excluded', 'private', 'question_mismatch', 'source_unknown'):
            with self.subTest(change=change):
                cycle = cycle_fixture()
                row = cycle['baseline']['cases'][1]
                if change == 'missing': cycle['baseline']['cases'].pop(1)
                elif change == 'excluded': row['assessment']['answer_evaluable'] = False
                elif change == 'private': row['response'] = '根据你之前的聊天记录，找到这份资料。'
                elif change == 'question_mismatch': row['question'] += '另一个问题'
                else: row['assessment']['official_source_present'] = None
                result = discovery_funnel(cycle)
                brand = self.stages(result)['brand_search']
                self.assertEqual(brand['state'], 'unknown')
                self.assertEqual(brand['unknown_case_ids'], ['q02'])
                self.assertNotIn('retrieval_discovery', [row['key'] for row in result['priorities']])

    def test_different_or_unknown_observed_modes_models_never_generate_cross_question_priorities(self):
        for field, value in (('client_mode', 'work'), ('client_mode', None), ('client_mode', []),
                             ('model_label', 'different-model'), ('model_label', None), ('model_label', {})):
            with self.subTest(field=field, value=value):
                cycle = cycle_fixture()
                cycle['baseline']['cases'][2][field] = value
                result = discovery_funnel(cycle)
                self.assertTrue(result['supported'])
                self.assertFalse(result['comparability']['matched'])
                self.assertEqual(result['priorities'], [])
                if not isinstance(value, str):
                    self.assertIsNone(self.stages(result)['website_guided']['cases'][0][field])

    def test_mixed_models_within_category_show_each_case_without_a_combined_stage_result(self):
        cycle = cycle_fixture()
        cases = [cycle['test_plan']['cases'][0],
                 {'category': 'discovery', 'question': '数据管理工具如何比较适用范围？', 'intent': '自然选型'},
                 *cycle['test_plan']['cases'][1:]]
        plan = parse_ai_plan(json.dumps({'cases': cases}, ensure_ascii=False), cycle['profile'], 4,
                             provenance={'method': 'synthetic_offline_only'})
        originals = {row['category']: row for row in cycle['baseline']['cases']}
        cycle['baseline']['cases'] = [{**copy.deepcopy(originals[row['category']]), **row} for row in plan['cases']]
        cycle['baseline']['cases'][1]['model_label'] = 'different-model'
        cycle['baseline']['plan_sha256'] = plan['plan_sha256']
        cycle['test_plan'] = plan
        result = discovery_funnel(cycle)
        natural = self.stages(result)['natural_discovery']
        self.assertEqual(natural['state'], 'unknown')
        self.assertFalse(natural['conditions_match'])
        self.assertEqual([row['signal_value'] for row in natural['cases']], [False, False])
        self.assertEqual(result['priorities'], [])

    def test_external_probes_and_later_retests_cannot_override_native_baseline(self):
        cycle = cycle_fixture()
        original = discovery_funnel(cycle)
        cycle['probes'] = [{'source': 'user_reported_external', 'after': {'official_source_present': True}},
                           {'source': 'native_collector', 'after': {'official_source_present': True}}]
        cycle['retests'] = [{'report': synthetic_report(guided_sources=[{'url': 'https://example.org/products/gate/'}])}]
        self.assertEqual(discovery_funnel(cycle), original)
        cycle['baseline_source'] = 'user_reported_external'
        external = discovery_funnel(cycle)
        self.assertFalse(external['supported'])
        self.assertEqual(external['priorities'], [])
        self.assertTrue(all(row['state'] == 'unknown' for row in external['stages']))

    def test_plan_entity_legacy_and_duplicate_boundaries_keep_unsupported_state(self):
        for change in ('plan', 'entity', 'legacy', 'duplicate', 'invalid_id', 'question_changed'):
            with self.subTest(change=change):
                cycle = cycle_fixture()
                if change == 'plan': cycle['test_plan']['plan_sha256'] = '0' * 64
                elif change == 'entity': cycle['profile'] = {**cycle['profile'], 'website': 'https://other.example.org/'}
                elif change == 'legacy': cycle['profile'] = {key: value for key, value in cycle['profile'].items() if key != 'entity_type'}
                elif change == 'duplicate': cycle['test_plan']['cases'].append(copy.deepcopy(cycle['test_plan']['cases'][0]))
                elif change == 'invalid_id': cycle['test_plan']['cases'][0]['id'] = ['invalid']
                else: cycle['test_plan']['cases'][0]['question'] += '被篡改'
                result = discovery_funnel(cycle)
                self.assertFalse(result['supported'])
                self.assertEqual(result['priorities'], [])

    def test_identity_priority_requires_missing_confirmed_public_material(self):
        for facts, expected in (([], True), ([{'confirmed': True, 'text': '仅运营方说明'}], True),
                                ([{'confirmed': False, 'source_url': 'https://example.org/'}], True),
                                ([{'confirmed': True, 'source_url': 'https://example.org/products/gate/'}], False)):
            with self.subTest(facts=facts):
                cycle = cycle_fixture(); cycle['facts'] = facts
                priorities = discovery_funnel(cycle)['priorities']
                self.assertEqual(any(row['key'] == 'identity_evidence' for row in priorities), expected)

    def test_projection_is_immutable_and_has_no_SQL_HTTP_DNS_AI_or_customer_text_output(self):
        cycle = cycle_fixture(); before = copy.deepcopy(cycle)
        with patch('sqlite3.connect', side_effect=AssertionError('No SQL')), \
             patch('socket.getaddrinfo', side_effect=AssertionError('No DNS')), \
             patch('requests.sessions.Session.request', side_effect=AssertionError('No HTTP')), \
             patch('services.ai_service.generate_geo_content', side_effect=AssertionError('No AI')):
            result = discovery_funnel(cycle)
        self.assertEqual(before, cycle)
        self.assertEqual(result, discovery_funnel(cycle))
        self.assertNotIn(cycle['baseline']['cases'][1]['response'], json.dumps(result, ensure_ascii=False))

    def test_diagnostics_actions_are_bound_to_frozen_case_and_fixed_navigation(self):
        cycle = cycle_fixture()
        result = diagnose_cycle(cycle)
        self.assertEqual(result['discovery_funnel'], discovery_funnel(cycle))
        discovery = [row for row in result['attention_items'] if row['kind'].startswith('discovery_')]
        self.assertEqual({row['kind'] for row in discovery}, {'discovery_retrieval_discovery', 'discovery_scenario_coverage'})
        actions = [row for row in result['actions'] if row['label'] in ('优先补品牌检索发现路径', '补自然需求的场景内容')]
        self.assertEqual({row['key'] for row in actions}, {'review_webmaster', 'review_drafts'})
        self.assertEqual({row['target']['case_id'] for row in actions}, {'q01', 'q02'})


class DiscoveryFunnelStoreTests(unittest.TestCase):
    def test_real_store_read_exposes_new_diagnosis_without_writing_customer_records(self):
        with tempfile.TemporaryDirectory() as directory:
            database = initialize_database(Path(directory) / 'database.db')
            report = synthetic_report()
            cycle = store.create_cycle(database, report, 'synthetic-funnel-reference')
            before = database.read_bytes()
            current = store.get_cycle(database, cycle['id'])
            self.assertEqual(before, database.read_bytes())
            self.assertEqual(current['discovery_funnel'], current['feedback_diagnostics']['discovery_funnel'])
            self.assertEqual(current['discovery_funnel']['run_id'], report['run_id'])
            self.assertEqual([row['state'] for row in current['discovery_funnel']['stages']],
                             ['not_observed', 'not_observed', 'unknown'])
            with closing(sqlite3.connect(database)) as connection:
                for table in ('geo_content_drafts', 'geo_content_retests', 'geo_research_probes', 'publish_jobs'):
                    self.assertEqual(connection.execute('SELECT count(*) FROM ' + table).fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
