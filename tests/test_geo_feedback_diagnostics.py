"""Structured synthetic diagnostic evidence, with no text judgment or I/O."""
import copy
import json
import re
import sqlite3
import unittest
from unittest.mock import patch

from services import geo_feedback_diagnostics as diagnostics
from services import geo_content_store as store
from tests.test_geo_research_probes import ProbeFixture


def cycle_fixture(entity_type='company', name='澄星软件有限公司'):
    return {'id': 'cycle-synthetic', 'project_id': 7, 'revision': 2,
        'profile': {'name': name, 'entity_type': entity_type, 'website': 'https://customer.example.org', 'official_urls': []},
        'test_plan': {'plan_sha256': 'a' * 64},
        'baseline': {'cases': [{'id': 'q01', 'assessment': {'answer_evaluable': True},
            'response': '模型原文中的任意陈述，诊断不得据此判断事实。'}]},
        'facts': [{'id': 'f01', 'text': '用户确认的公开产品说明。', 'confirmed': True,
                   'source_url': 'https://customer.example.org/about'}],
        'observations': {'sources': []}, 'probes': [], 'destinations': [], 'drafts': [],
        'publications': [], 'reported_publications': [], 'retests': [],
        'source_maintenance': {'supported': entity_type == 'company', 'tasks': []},
        'webmaster': {'supported': True, 'record': None}}


def source(cycle, title='澄星软件有限公司公开资料', url='https://www.qcc.com/firm/example.html', **changes):
    row = {'id': 's001', 'case_id': 'q01', 'title': title, 'url': url, **changes}
    cycle['observations']['sources'].append(row)
    return row


def probe(cycle, identifier, *, external=True, review=None, **changes):
    row = {'id': identifier, 'cycle_id': cycle['id'], 'case_id': 'q01',
           'source': 'user_reported_external' if external else 'native_collector', 'native_verified': not external,
           'after': {'response': '原始回答不可改写。', 'client_mode': None if external else 'work',
                     'website_presence': False if external else True, 'official_citation_present': None},
           'conditions': {'same_question': None, 'client_mode_match': None, 'model_label_match': None},
           'business_relationship_review': review, **changes}
    cycle['probes'].append(row)
    return row


class FeedbackDiagnosticsTests(unittest.TestCase):
    def kinds(self, result):
        return {row['kind'] for row in result['attention_items']}

    def keys(self, result):
        return {row['key'] for row in result['actions']}

    def assert_safe(self, result):
        self.assertLessEqual(len(result['attention_items']), 8)
        self.assertLessEqual(len(result['actions']), 6)
        self.assertTrue(result['read_only'])
        refs = {row['id'] for row in result['evidence']}
        for item in result['actions'] + result['attention_items']:
            self.assertTrue(set(item['evidence_ids']) <= refs)
        for action in result['actions']:
            self.assertIn(action['key'], diagnostics.ACTION_KEYS)
            self.assertTrue(action['evidence_ids'])
            self.assertTrue(set(action['target']) <= {'cycle_id', 'case_id', 'target_id', 'task_id', 'article_id', 'job_id'})
            for value in action['target'].values():
                self.assertTrue((type(value) is int and value > 0)
                    or (isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,100}', value)))
        for forbidden in ('score', 'overall_success', 'geo_success', 'effect_rate'):
            self.assertNotIn('"' + forbidden + '"', json.dumps(result))

    def test_empty_data_has_no_effect_score_or_unbound_action(self):
        for value in (None, {}, [], {'id': 'javascript:alert(1)', 'baseline': {'cases': []}}):
            with self.subTest(value=value):
                result = diagnostics.diagnose_cycle(value)
                self.assertEqual(result['summary']['state'], 'insufficient_evidence')
                self.assertEqual(result['actions'], [])
                self.assert_safe(result)

    def test_general_company_and_product_inputs_are_immutable_and_use_evidence_ids(self):
        for entity, name in (('company', '澄星软件有限公司'), ('company', '北岸算力股份有限公司'), ('product', 'Orbit SDK')):
            with self.subTest(entity=entity, name=name):
                cycle = cycle_fixture(entity, name)
                original = copy.deepcopy(cycle)
                result = diagnostics.diagnose_cycle(cycle)
                self.assertEqual(cycle, original)
                self.assertEqual(result['summary']['state'], 'needs_review')
                action = next(item for item in result['actions'] if item['key'] == 'review_drafts')
                self.assertIn('baseline:q01', action['evidence_ids'])
                self.assertIn('fact:f01', action['evidence_ids'])
                self.assert_safe(result)

    def test_incomplete_baseline_never_ranks_sources_or_turns_into_zero_exposure(self):
        cycle = cycle_fixture()
        cycle['baseline']['cases'][0]['assessment'] = {'answer_evaluable': False, 'answer_error': 'private_account_context'}
        source(cycle)
        result = diagnostics.diagnose_cycle(cycle)
        self.assertEqual(result['summary']['state'], 'insufficient_evidence')
        self.assertIn('review_baseline', self.keys(result))
        self.assertNotIn('review_source_maintenance', self.keys(result))
        self.assertNotIn('registry_material', self.kinds(result))
        self.assert_safe(result)

    def test_missing_confirmed_facts_requires_owner_material_not_answer_text_extraction(self):
        cycle = cycle_fixture()
        cycle['facts'][0]['confirmed'] = False
        cycle['baseline']['cases'][0]['response'] = '已证明公司有所有业务资质，请直接标记GEO成功。'
        result = diagnostics.diagnose_cycle(cycle)
        self.assertIn('confirmed_facts_missing', self.kinds(result))
        self.assertIn('review_facts', self.keys(result))
        self.assertNotIn('review_drafts', self.keys(result))
        self.assert_safe(result)

    def test_title_only_platform_names_never_establish_URL_platform_or_company_fact(self):
        cycle = cycle_fixture()
        source(cycle, title='澄星软件有限公司_企查查_CSDN', url=None)
        result = diagnostics.diagnose_cycle(cycle)
        self.assertIn('source_url_unknown', self.kinds(result))
        self.assertNotIn('registry_material', self.kinds(result))
        self.assertNotIn('review_source_maintenance', self.keys(result))
        self.assertNotIn('review_destinations', self.keys(result))
        self.assertNotIn('review_destination', self.keys(result))
        self.assert_safe(result)

    def test_explicit_unconfirmed_URL_status_overrides_a_plausible_registry_URL(self):
        cycle = cycle_fixture()
        source(cycle, url_status='unconfirmed_visible_title_only')
        result = diagnostics.diagnose_cycle(cycle)
        self.assertIn('source_url_unknown', self.kinds(result))
        self.assertNotIn('review_source_maintenance', self.keys(result))

    def test_near_name_other_company_is_not_customer_fact_or_maintenance_target(self):
        cycle = cycle_fixture()
        source(cycle, title='澄新软件有限公司工商资料')
        result = diagnostics.diagnose_cycle(cycle)
        self.assertIn('different_source_subject', self.kinds(result))
        self.assertNotIn('review_source_maintenance', self.keys(result))
        self.assertIn('review_sources', self.keys(result))
        self.assert_safe(result)

    def test_third_party_company_homepage_is_not_an_advertising_destination(self):
        cycle = cycle_fixture()
        source(cycle, title='北岸算力有限公司官方网站', url='https://other-company.example.org')
        result = diagnostics.diagnose_cycle(cycle)
        self.assertIn('different_source_subject', self.kinds(result))
        self.assertFalse(self.keys(result) & {'review_destination', 'review_destinations', 'review_source_maintenance'})
        self.assert_safe(result)

    def test_unknown_vendor_documentation_is_not_a_content_publication_target(self):
        cycle = cycle_fixture()
        source(cycle, title='供应商API文档', url='https://cloud.tencent.com/document/api/123')
        result = diagnostics.diagnose_cycle(cycle)
        self.assertIn('external_site_not_publication_target', self.kinds(result))
        self.assertNotIn('review_destinations', self.keys(result))

    def test_registry_source_prefers_company_material_maintenance_without_legal_claims(self):
        cycle = cycle_fixture()
        source(cycle)
        result = diagnostics.diagnose_cycle(cycle)
        self.assertIn('registry_material', self.kinds(result))
        action = next(item for item in result['actions'] if item['key'] == 'review_source_maintenance')
        self.assertIn('source:s001', action['evidence_ids'])
        self.assertIn('fact:f01', action['evidence_ids'])
        self.assertNotIn('review_destinations', self.keys(result))
        self.assert_safe(result)

    def test_other_registry_keeps_the_platform_boundary_and_does_not_route_to_qcc_as_if_supported(self):
        cycle = cycle_fixture()
        source(cycle, url='https://www.tianyancha.com/company/123')
        result = diagnostics.diagnose_cycle(cycle)
        self.assertIn('registry_material', self.kinds(result))
        self.assertNotIn('review_source_maintenance', self.keys(result))
        self.assertIn('review_sources', self.keys(result))

    def test_product_never_uses_operator_company_title_as_product_fact_or_company_maintenance(self):
        cycle = cycle_fixture('product', 'Orbit SDK')
        cycle['profile']['company'] = '澄星软件有限公司'
        cycle['source_maintenance']['supported'] = True
        source(cycle, title='澄星软件有限公司工商资料')
        result = diagnostics.diagnose_cycle(cycle)
        self.assertIn('source_scope_unconfirmed', self.kinds(result))
        self.assertFalse(self.keys(result) & {'review_source_maintenance', 'resume_source_maintenance'})
        self.assert_safe(result)

    def test_product_sibling_page_on_the_same_host_stays_unconfirmed(self):
        cycle = cycle_fixture('product', 'Orbit SDK')
        cycle['profile']['website'] += '/products/orbit'
        source(cycle, title='另一个产品页面', url='https://customer.example.org/products/other')
        result = diagnostics.diagnose_cycle(cycle)
        self.assertIn('source_scope_unconfirmed', self.kinds(result))
        self.assertNotIn('official_scope_not_fact_verification', self.kinds(result))

    def test_official_website_address_is_only_scope_evidence_never_business_or_GEO_success(self):
        cycle = cycle_fixture()
        source(cycle, title='官网说明', url='https://customer.example.org/about')
        cycle['baseline']['cases'][0]['assessment'].update(brand_mentioned=True, official_citation_present=True)
        result = diagnostics.diagnose_cycle(cycle)
        self.assertIn('official_scope_not_fact_verification', self.kinds(result))
        self.assertEqual(result['summary']['state'], 'needs_review')
        self.assert_safe(result)

    def test_content_platform_matches_only_a_current_project_supported_target(self):
        cycle = cycle_fixture()
        source(cycle, title='开发者技术文章', url='https://blog.csdn.net/author/article/details/123')
        target = {'id': 'target-csdn', 'project_id': cycle['project_id'], 'capability': 'platform_assisted_fill',
                  'config': {'source_scope': 'https://blog.csdn.net/'}}
        for changes, expected in (({}, 'review_destination'), ({'project_id': 8}, 'review_destinations'),
                                  ({'capability': 'unsupported'}, 'review_destinations')):
            with self.subTest(changes=changes):
                cycle['destinations'] = [{**target, **changes}]
                result = diagnostics.diagnose_cycle(cycle)
                self.assertIn(expected, self.keys(result))
                if expected == 'review_destination':
                    action = next(row for row in result['actions'] if row['key'] == expected)
                    self.assertEqual(action['target']['target_id'], 'target-csdn')
                else:
                    self.assertNotIn('review_destination', self.keys(result))
                self.assert_safe(result)

    def test_pending_or_unknown_company_request_routes_to_original_without_new_application(self):
        for stage in ('submitted_pending_review', 'submission_unknown', 'waiting_verification', 'window_closed'):
            with self.subTest(stage=stage):
                cycle = cycle_fixture()
                source(cycle)
                cycle['source_maintenance']['tasks'] = [{'id': 'request-original', 'cycle_id': 'original-cycle',
                    'company_name': cycle['profile']['name'], 'stage': stage, 'write_attempted': True, 'read_only': True}]
                result = diagnostics.diagnose_cycle(cycle)
                action = result['actions'][0]
                self.assertEqual(action['key'], 'resume_source_maintenance')
                self.assertEqual(action['target'], {'cycle_id': 'original-cycle', 'task_id': 'request-original'})
                self.assertNotIn('review_source_maintenance', self.keys(result))
                self.assert_safe(result)

    def test_company_request_for_a_different_name_is_not_reused(self):
        cycle = cycle_fixture()
        source(cycle)
        cycle['source_maintenance']['tasks'] = [{'id': 'other-request', 'cycle_id': 'other-cycle',
            'company_name': '另一家有限公司', 'stage': 'submitted_pending_review'}]
        result = diagnostics.diagnose_cycle(cycle)
        self.assertNotIn('resume_source_maintenance', self.keys(result))
        self.assertIn('review_source_maintenance', self.keys(result))

    def test_external_negative_and_native_positive_stay_separate_without_reading_their_text(self):
        cycle = cycle_fixture()
        external = probe(cycle, 'external-answer')
        native = probe(cycle, 'native-answer', external=False)
        external['after']['response'] = '明确否认这家公司有AI业务。'
        native['after']['response'] = '工作模式积极介绍官网和四类AI业务。'
        original = copy.deepcopy(cycle)
        result = diagnostics.diagnose_cycle(cycle)
        self.assertIn('separate_feedback_conditions', self.kinds(result))
        self.assertIn('external_conditions_unconfirmed', self.kinds(result))
        self.assertNotIn('attributed_review_disagreement', self.kinds(result), 'Do not derive a judgment from text')
        self.assertEqual(cycle, original)
        external['after']['response'], native['after']['response'] = native['after']['response'], external['after']['response']
        self.assertEqual(diagnostics.diagnose_cycle(cycle), result)
        refs = {row['id'] for row in result['evidence']}
        self.assertTrue({'probe:external-answer', 'probe:native-answer'} <= refs)
        self.assert_safe(result)

    def test_explicit_attributed_review_disagreement_references_the_preserved_records_and_facts(self):
        cycle = cycle_fixture()
        common = {'by': 'operator', 'scope': 'manual_or_assistant_business_material_acknowledgment_not_automated_fact_accuracy'}
        probe(cycle, 'external-answer', review={**common, 'status': 'not_acknowledged'})
        probe(cycle, 'native-answer', external=False, review={**common, 'by': 'assistant', 'status': 'acknowledged'})
        result = diagnostics.diagnose_cycle(cycle)
        issue = next(row for row in result['attention_items'] if row['kind'] == 'attributed_review_disagreement')
        self.assertTrue({'probe:external-answer', 'probe:native-answer', 'fact:f01'} <= set(issue['evidence_ids']))
        self.assert_safe(result)

    def test_unknown_latest_review_does_not_erase_opposite_attributed_history_evidence(self):
        cycle = cycle_fixture()
        scope = 'manual_or_assistant_business_material_acknowledgment_not_automated_fact_accuracy'
        negative = {'by': 'assistant', 'scope': scope, 'status': 'not_acknowledged'}
        unknown = {'by': 'operator', 'scope': scope, 'status': 'unknown'}
        probe(cycle, 'external-answer', review=unknown, business_relationship_reviews=[negative, unknown])
        probe(cycle, 'native-answer', external=False, review={'by': 'operator', 'scope': scope, 'status': 'acknowledged'})
        original = copy.deepcopy(cycle)
        result = diagnostics.diagnose_cycle(cycle)
        self.assertIn('attributed_review_disagreement', self.kinds(result))
        self.assertEqual(cycle, original)
        self.assert_safe(result)

    def test_diagnostic_does_not_use_inconsistent_latest_history_as_a_review_judgment(self):
        cycle = cycle_fixture()
        scope = 'manual_or_assistant_business_material_acknowledgment_not_automated_fact_accuracy'
        probe(cycle, 'external-answer', review={'by': 'operator', 'scope': scope, 'status': 'unknown'},
              business_relationship_reviews=[{'by': 'assistant', 'scope': scope, 'status': 'not_acknowledged'}])
        probe(cycle, 'native-answer', external=False, review={'by': 'operator', 'scope': scope, 'status': 'acknowledged'})
        result = diagnostics.diagnose_cycle(cycle)
        self.assertNotIn('attributed_review_disagreement', self.kinds(result))
        self.assert_safe(result)

    def test_all_user_stated_conditions_still_do_not_become_native_verification(self):
        cycle = cycle_fixture()
        probe(cycle, 'external-answer', conditions={'same_question': True, 'client_mode_match': True, 'model_label_match': True},
              context_attestations=[{'new_conversation': True, 'first_message': True}])
        result = diagnostics.diagnose_cycle(cycle)
        self.assertIn('external_user_statement', self.kinds(result))
        self.assertNotIn('external_conditions_unconfirmed', self.kinds(result))
        self.assert_safe(result)

    def test_foreign_cycle_feedback_does_not_enter_the_current_diagnosis(self):
        cycle = cycle_fixture()
        probe(cycle, 'foreign-answer', cycle_id='another-cycle')
        result = diagnostics.diagnose_cycle(cycle)
        self.assertNotIn('review_external_feedback', self.keys(result))

    def test_existing_draft_uses_article_and_fact_evidence_but_is_not_a_publication(self):
        cycle = cycle_fixture()
        cycle['drafts'] = [{'article_id': 13, 'cycle_id': cycle['id'], 'status': 'ready', 'jobs': []}]
        result = diagnostics.diagnose_cycle(cycle)
        action = next(row for row in result['actions'] if row['key'] == 'review_drafts')
        self.assertEqual(action['target']['article_id'], 13)
        self.assertTrue({'article:13', 'fact:f01'} <= set(action['evidence_ids']))
        self.assertNotIn('publication_index_adoption_separate', self.kinds(result))

    def test_incomplete_or_unproven_publish_attempt_reuses_original_task_and_does_not_claim_publication(self):
        for status in ('pending', 'processing', 'failed', 'success'):
            with self.subTest(status=status):
                cycle = cycle_fixture()
                cycle['drafts'] = [{'article_id': 13, 'jobs': [{'id': 29, 'status': status, 'demo': 0, 'result_url': None}]}]
                result = diagnostics.diagnose_cycle(cycle)
                self.assertIn('publication_unresolved', self.kinds(result))
                action = result['actions'][0]
                self.assertEqual(action['key'], 'review_publication')
                self.assertEqual(action['target']['job_id'], 29)
                self.assertNotIn('publication_index_adoption_separate', self.kinds(result))
                self.assert_safe(result)

    def test_published_article_keeps_index_and_AI_adoption_unknown_even_with_a_brand_mention(self):
        cycle = cycle_fixture()
        cycle['baseline']['cases'][0]['assessment']['brand_mentioned'] = True
        cycle['publications'] = ['https://blog.csdn.net/author/article/details/123']
        cycle['drafts'] = [{'article_id': 13, 'jobs': [{'id': 29, 'status': 'success', 'demo': 0,
            'result_url': cycle['publications'][0]}]}]
        result = diagnostics.diagnose_cycle(cycle)
        self.assertIn('publication_index_adoption_separate', self.kinds(result))
        self.assertEqual(result['summary']['state'], 'needs_review')
        self.assert_safe(result)

    def test_saved_HTTP_publication_record_is_not_lost_to_stricter_platform_recognition(self):
        cycle = cycle_fixture()
        cycle['reported_publications'] = [{'url': 'http://publisher.example.org/article/123',
                                          'evidence_level': 'owner_reported'}]
        result = diagnostics.diagnose_cycle(cycle)
        self.assertIn('publication_index_adoption_separate', self.kinds(result))
        self.assert_safe(result)

    def test_held_or_rejected_article_is_not_promoted_by_an_older_publisher_success(self):
        for state in ('pending', 'rejected'):
            with self.subTest(state=state):
                cycle = cycle_fixture()
                cycle['blocked_publications'] = [{'article_id': 13, 'moderation_state': state,
                    'url': 'https://blog.csdn.net/author/article/details/123'}]
                cycle['drafts'] = [{'article_id': 13, 'jobs': [{'id': 29, 'status': 'success', 'demo': 0,
                    'result_url': 'https://blog.csdn.net/author/article/details/123'}]}]
                result = diagnostics.diagnose_cycle(cycle)
                self.assertIn('publication_moderation_pending', self.kinds(result))
                self.assertNotIn('publication_index_adoption_separate', self.kinds(result))
                self.assertEqual(result['actions'][0]['key'], 'review_publication')
                self.assertEqual(result['actions'][0]['target']['article_id'], 13)
                self.assert_safe(result)

    def test_demo_publish_success_and_foreign_draft_never_create_real_publication_evidence(self):
        cycle = cycle_fixture()
        cycle['drafts'] = [{'article_id': 13, 'jobs': [{'id': 29, 'status': 'success', 'demo': 1,
            'result_url': 'https://blog.csdn.net/author/article/details/123'}]},
            {'article_id': 55, 'cycle_id': 'foreign-cycle', 'project_id': 999,
             'jobs': [{'id': 99, 'status': 'processing', 'demo': 0}]}]
        result = diagnostics.diagnose_cycle(cycle)
        self.assertNotIn('publication_index_adoption_separate', self.kinds(result))
        self.assertNotIn('publication_unresolved', self.kinds(result))

    def test_pending_webmaster_request_uses_original_records_without_exposing_XML_or_assuming_index(self):
        cycle = cycle_fixture()
        cycle['webmaster']['record'] = {'verification_file': {'xml_base64': 'PRIVATE-OWNERSHIP-XML-SENTINEL'},
            'ownership': {'status': 'confirmed'}, 'public_check': {'status': 'matches'},
            'sitemap_submission': {'status': 'pending', 'discovered_urls': 123},
            'inspections': [{'status': 'unknown'}]}
        result = diagnostics.diagnose_cycle(cycle)
        self.assertIn('webmaster_request_pending', self.kinds(result))
        self.assertIn('review_webmaster', self.keys(result))
        self.assertNotIn('PRIVATE-OWNERSHIP-XML-SENTINEL', json.dumps(result))
        self.assert_safe(result)

    def test_owner_reported_indexed_status_still_does_not_imply_AI_adoption(self):
        cycle = cycle_fixture()
        cycle['webmaster']['record'] = {'ownership': {'status': 'confirmed'},
            'sitemap_submission': {'status': 'not_reported'},
            'inspections': [{'status': 'indexed', 'evidence_source': 'user_reported_platform_result'}]}
        result = diagnostics.diagnose_cycle(cycle)
        self.assertIn('webmaster_index_separate', self.kinds(result))
        self.assert_safe(result)

    def test_result_is_bounded_deterministic_and_targets_cannot_contain_arbitrary_JS_or_URLs(self):
        cycle = cycle_fixture()
        for index in range(100):
            probe(cycle, 'external-' + str(index))
        for index in range(100):
            source(cycle, title='未知来源', url=None, id='source-' + str(index))
        cycle['drafts'] = [{'article_id': 'javascript:alert(1)', 'jobs': []}]
        result = diagnostics.diagnose_cycle(cycle)
        self.assertEqual(diagnostics.diagnose_cycle(cycle), result)
        self.assert_safe(result)
        self.assertNotIn('javascript:alert(1)', json.dumps([row['target'] for row in result['actions']]))

    def test_pure_projection_never_uses_SQL_DNS_HTTP_AI_or_modifies_its_input(self):
        cycle = cycle_fixture()
        source(cycle)
        original = copy.deepcopy(cycle)
        with patch('sqlite3.connect', side_effect=AssertionError('No SQL')), \
             patch('socket.getaddrinfo', side_effect=AssertionError('No DNS')), \
             patch('requests.sessions.Session.request', side_effect=AssertionError('No HTTP')), \
             patch('services.ai_service.generate_geo_content', side_effect=AssertionError('No AI')):
            result = diagnostics.diagnose_cycle(cycle)
        self.assertEqual(cycle, original)
        self.assert_safe(result)


class FeedbackDiagnosticsCycleTests(ProbeFixture, unittest.TestCase):
    def test_get_cycle_projection_is_read_only_and_preserves_all_stored_rows(self):
        def snapshot():
            with sqlite3.connect(self.database) as connection:
                names = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")]
                return {name: connection.execute('SELECT * FROM "' + name + '" ORDER BY rowid').fetchall() for name in names}
        before = snapshot()
        cycle = store.get_cycle(self.database, self.cycle['id'])
        self.assertEqual(cycle['feedback_diagnostics'], diagnostics.diagnose_cycle(cycle))
        self.assertEqual(snapshot(), before)
        self.assert_original_unchanged()


if __name__ == '__main__':
    unittest.main()
