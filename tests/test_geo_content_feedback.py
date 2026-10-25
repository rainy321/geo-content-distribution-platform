"""Owner-reported publication feedback uses synthetic data, never network posts."""
import copy
import json
import sqlite3
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from db.createTable import initialize_database
from services import geo_content_research as research, geo_content_store as store
from services import geo_destination_service as destinations
from services.article_service import update_article
from services.geo_test_service import plan_fingerprint
from services.geo_feedback_summary import collection_summary
from services.project_service import create_project
from tests.test_geo_content_loop import ResearchFixture, draft, facts
from tests.test_geo_destinations import config


class TestPublicationFeedback(ResearchFixture, unittest.TestCase):
    def prepare_article(self, **changes):
        self.target = destinations.save_target(self.database, self.cycle['project_id'], config(**changes))
        self.cycle = store.get_cycle(self.database, self.cycle['id'])
        self.prepare_facts()
        result = store.save_draft(self.database, self.cycle['id'], 'b01', self.target['key'], draft(), {'method': 'synthetic'}, self.cycle['revision'])
        self.article_id = result['article_id']
        update_article(self.database, self.article_id, {'status': 'ready'})

    def payload(self, **changes):
        return {'article_id': self.article_id, 'target_id': self.target['id'],
                'url': 'https://niche.example.org/blog/test-post',
                'published_at': '2026-10-03T12:00:00+08:00', 'confirmed': True, **changes}

    def retest(self, collected_at='2026-10-04T00:00:00Z'):
        value = copy.deepcopy(self.baseline)
        value['run_id'] = 'synthetic-feedback-retest'
        for case in value['cases']:
            case['collected_at'] = collected_at
        return value

    def test_stopped_empty_retest_exposes_collection_failure_without_changing_report(self):
        after = self.retest()
        after.update(status='stopped', stop_reason='browser_error:TimeoutError')
        for index, case in enumerate(after['cases']):
            case.update(status='failed' if index == 0 else 'pending', response=None,
                        collected_at=None, model_label=None, client_mode=None,
                        failure_reason='browser_error:TimeoutError' if index == 0 else None,
                        result_path='C:/private/account/evidence/result.json')
            case['assessment'].update(answer_evaluable=False)
            for key in ('failure_stage', 'question_submission_attempted', 'question_submitted'):
                case.pop(key, None)
        original = copy.deepcopy(after)
        loaded = store.attach_retest(self.database, self.cycle['id'], after)['retests'][0]
        summary = loaded['collection']
        self.assertEqual(summary['status'], 'stopped')
        self.assertFalse(summary['has_evaluable_answers'])
        self.assertEqual(summary['stop_reason'], 'browser_error:TimeoutError')
        self.assertEqual(summary['counts'], {'planned': len(after['cases']), 'evaluable': 0,
                         'failed': 1, 'pending': len(after['cases']) - 1, 'excluded_private': 0, 'incomplete': 0})
        self.assertIsNone(summary['issues'][0]['failure_stage'])
        self.assertIsNone(summary['issues'][0]['question_submitted'])
        self.assertNotIn('private/account', json.dumps(summary))
        self.assertFalse(loaded['comparison']['conditions_match'])
        self.assertTrue(all(row['change'] is None for row in loaded['comparison']['cohorts']))
        self.assertEqual(after, original)
        with closing(sqlite3.connect(self.database)) as connection:
            stored_before = connection.execute('SELECT report FROM geo_content_retests WHERE cycle_id=?', (self.cycle['id'],)).fetchone()[0]
        store.get_cycle(self.database, self.cycle['id'])
        with closing(sqlite3.connect(self.database)) as connection:
            stored_after = connection.execute('SELECT report FROM geo_content_retests WHERE cycle_id=?', (self.cycle['id'],)).fetchone()[0]
        self.assertEqual(stored_before, stored_after)
        self.assertEqual(json.loads(stored_after), original)

    def test_feedback_collection_summary_uses_current_private_isolation_and_keeps_other_answers(self):
        after = self.retest()
        after['execution']['client_mode'] = 'work'
        for case in after['cases']:
            case['client_mode'] = 'work'
        after['cases'][0]['response'] = '内部资料已显示合成客户的项目情况。'
        before = copy.deepcopy(after)
        summary = collection_summary(after)
        self.assertEqual(summary['counts']['evaluable'], len(after['cases']) - 1)
        self.assertEqual(summary['counts']['excluded_private'], 1)
        self.assertEqual(summary['counts']['failed'], 0)
        self.assertTrue(summary['has_evaluable_answers'])
        self.assertEqual(summary['issues'][0]['failure_reason'], 'private_account_context')
        self.assertNotIn('合成客户的项目情况', json.dumps(summary, ensure_ascii=False))
        self.assertEqual(after, before)

    def test_feedback_summary_only_exposes_typed_phase_and_submission_evidence(self):
        after = self.retest()
        after['status'] = 'stopped'
        after['stop_reason'] = 'C:/private/profile/TimeoutError'
        for case in after['cases']:
            case.update(status='failed', failure_reason='C:/private/profile/TimeoutError',
                        failure_stage='navigation', question_submission_attempted=False, question_submitted=False)
        summary = collection_summary(after)
        self.assertIsNone(summary['stop_reason'])
        self.assertEqual(summary['issues'][0]['failure_stage'], 'navigation')
        self.assertFalse(summary['issues'][0]['question_submission_attempted'])
        self.assertFalse(summary['issues'][0]['question_submitted'])
        self.assertIsNone(summary['issues'][0]['failure_reason'])
        after['cases'][0]['failure_stage'] = 'submission_control'
        self.assertEqual(collection_summary(after)['issues'][0]['failure_stage'], 'submission_control')
        after['cases'][0].update(failure_stage='C:/private/profile', question_submission_attempted=0, question_submitted='false')
        issue = collection_summary(after)['issues'][0]
        self.assertIsNone(issue['failure_stage'])
        self.assertIsNone(issue['question_submission_attempted'])
        self.assertIsNone(issue['question_submitted'])

    def test_same_project_facts_are_candidates_and_require_new_confirmation(self):
        self.prepare_facts()
        next_report = self.retest()
        created = store.create_cycle(self.database, next_report, 'next-round')
        self.assertEqual(created['facts'], [])
        candidates = created['fact_candidates']
        self.assertEqual(candidates['source_cycle_id'], self.cycle['id'])
        self.assertFalse(candidates['facts'][0]['confirmed'])
        with self.assertRaisesRegex(ValueError, '确认'):
            store.save_facts(self.database, created['id'], candidates['facts'], created['revision'])
        confirmed = [{**fact, 'confirmed': True} for fact in candidates['facts']]
        saved = store.save_facts(self.database, created['id'], confirmed, created['revision'])
        self.assertEqual(saved['facts'][0]['text'], facts()[0]['text'])
        self.assertIsNone(store.get_cycle(self.database, self.cycle['id'])['fact_candidates'])

    def test_facts_do_not_leak_to_another_project(self):
        self.prepare_facts()
        changed = copy.deepcopy(self.baseline)
        changed['profile']['name'] = '其他合成品牌'
        plan = research.plan_from_report(self.baseline)
        plan['profile'] = changed['profile']
        changed['plan_sha256'] = plan_fingerprint(plan)
        changed['run_id'] = 'another-customer'
        other = store.create_cycle(self.database, changed, 'another')
        self.assertIsNone(other['fact_candidates'])

    def test_old_selected_brief_cannot_reuse_answers_excluded_by_current_rules(self):
        self.prepare_facts()
        cycle = store.get_cycle(self.database, self.cycle['id'])
        original = copy.deepcopy(cycle)
        for change in (
            {'response': '已委托专门调研代理，后台进行，完成后整理给你。'},
            {'response': '算力紧张，办公能力得晚点才能用了。'},
            {'status': 'need_action'},
            {'assessment': {**cycle['baseline']['cases'][0]['assessment'], 'answer_evaluable': False}},
        ):
            damaged = copy.deepcopy(cycle)
            damaged['baseline']['cases'][0].update(change)
            before = copy.deepcopy(damaged)
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, '重新采集并分析'):
                research.draft_input(damaged, 'b01', 'owned_web')
            self.assertEqual(damaged, before)
        # An excluded unrelated case does not invalidate this verified brief.
        unrelated = copy.deepcopy(cycle)
        unrelated['baseline']['cases'][1]['response'] = '已委托专门调研代理，后台进行，完成后整理给你。'
        self.assertEqual(research.draft_input(unrelated, 'b01', 'owned_web')['brief']['id'], 'b01')
        self.assertEqual(cycle, original)

    def test_old_brief_missing_or_malformed_case_references_never_become_valid(self):
        self.prepare_facts()
        cycle = store.get_cycle(self.database, self.cycle['id'])
        for identifiers in ([], 'q01', [None], ['missing-case'], ['q01', 'q01']):
            damaged = copy.deepcopy(cycle)
            damaged['analysis']['briefs'][0]['case_ids'] = identifiers
            with self.subTest(identifiers=identifiers), self.assertRaisesRegex(ValueError, '不存在或格式无效'):
                research.draft_input(damaged, 'b01', 'owned_web')

    def test_reported_link_is_persistent_lineage_without_adapter_success(self):
        self.prepare_article()
        for _ in range(2):
            saved = store.save_reported_publication(self.database, self.cycle['id'], self.payload(note='合成用户声明，未访问平台'))
        initialize_database(self.database)
        loaded = store.get_cycle(self.database, self.cycle['id'])
        self.assertEqual(loaded['publications'], [])
        self.assertEqual(len(loaded['reported_publications']), 1)
        evidence = loaded['reported_publications'][0]
        self.assertEqual(evidence['evidence_level'], 'owner_reported')
        self.assertEqual(evidence['baseline_run_id'], self.baseline['run_id'])
        self.assertEqual(evidence['report_digest'], self.cycle['report_digest'])
        self.assertEqual(evidence['plan_sha256'], self.baseline['plan_sha256'])
        self.assertEqual(evidence['published_at'], '2026-10-03T04:00:00+00:00')
        self.assertEqual(len(evidence['article_sha256']), 64)
        self.assertEqual(loaded['drafts'][0]['status'], 'ready')
        self.assertEqual(loaded['drafts'][0]['jobs'], [])
        self.assertEqual(saved['reported_publications'][0]['id'], evidence['id'])

    def test_publication_range_is_separate_from_observed_source_range(self):
        self.prepare_article(source_scope='https://www.zhihu.com/question/',
                             publication_scope='https://zhuanlan.zhihu.com/p/')
        result = store.save_reported_publication(self.database, self.cycle['id'], self.payload(url='https://zhuanlan.zhihu.com/p/123456'))
        self.assertEqual(result['reported_publications'][0]['url'], 'https://zhuanlan.zhihu.com/p/123456')
        with self.assertRaisesRegex(ValueError, '公开网址范围'):
            store.save_reported_publication(self.database, self.cycle['id'], self.payload(url='https://www.zhihu.com/question/123'))

    def test_owner_report_validation_blocks_wrong_scope_unreviewed_and_invalid_time(self):
        self.prepare_article()
        invalid = [
            {'confirmed': False}, {'article_id': True}, {'target_id': 'missing'},
            {'url': 'https://niche.example.org.evil.example.org/blog/post'},
            {'url': 'https://niche.example.org/editor'},
            {'url': 'https://127.0.0.1/blog/post'}, {'url': 'https://name:pass@niche.example.org/blog/post'},
            {'published_at': '2026-10-03T04:00:00'}, {'published_at': '2026-09-30T00:00:00Z'},
            {'published_at': (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()},
        ]
        for change in invalid:
            with self.subTest(change=change), self.assertRaises(ValueError):
                store.save_reported_publication(self.database, self.cycle['id'], self.payload(**change))
        update_article(self.database, self.article_id, {'status': 'draft'})
        with self.assertRaisesRegex(ValueError, '就绪'):
            store.save_reported_publication(self.database, self.cycle['id'], self.payload())
        self.assertEqual(store.get_cycle(self.database, self.cycle['id'])['reported_publications'], [])

    def test_article_from_another_cycle_cannot_be_reported_under_this_cycle(self):
        self.prepare_article()
        later = store.create_cycle(self.database, self.retest(), 'next-round')
        with self.assertRaisesRegex(ValueError, '本轮客户'):
            store.save_reported_publication(self.database, later['id'], self.payload())

    def test_feedback_distinguishes_after_publication_before_publication_and_unknown_time(self):
        self.prepare_article()
        store.save_reported_publication(self.database, self.cycle['id'], self.payload())
        after = self.retest()
        after['cases'][0]['citations'] = [{'url': self.payload()['url'], 'title': '合成发布文章'}]
        result = store.attach_retest(self.database, self.cycle['id'], after)
        comparison = result['retests'][0]['comparison']
        self.assertEqual(comparison['feedback_stage'], 'post_publication_observation')
        self.assertTrue(comparison['publication_comparison_eligible'])
        self.assertEqual(comparison['effectiveness_conclusion'], 'insufficient_evidence')
        self.assertEqual(comparison['publication_evidence'][0]['evidence_level'], 'owner_reported')
        self.assertTrue(comparison['publication_evidence'][0]['observed_in_citations'])
        self.assertIn('未独立核验', comparison['publication_context'])
        earlier = self.retest('2026-10-02T00:00:00Z')
        result = store.attach_retest(self.database, self.cycle['id'], earlier)
        comparison = result['retests'][0]['comparison']
        self.assertTrue(comparison['conditions_match'])
        self.assertFalse(comparison['publication_comparison_eligible'])
        self.assertEqual(comparison['feedback_stage'], 'retest_precedes_publication')
        unknown = self.retest()
        unknown['cases'][0]['collected_at'] = None
        comparison = store.attach_retest(self.database, self.cycle['id'], unknown)['retests'][0]['comparison']
        self.assertFalse(comparison['publication_comparison_eligible'])
        self.assertEqual(comparison['feedback_stage'], 'publication_time_unknown')

    def test_adapter_completion_without_time_is_not_post_publication_feedback(self):
        self.prepare_article()
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute('INSERT INTO publish_jobs(article_id,platform,status,demo,result_url) VALUES(?,?,?,?,?)',
                               (self.article_id, 'zhihu', 'success', 0, 'https://zhuanlan.zhihu.com/p/123'))
        comparison = store.attach_retest(self.database, self.cycle['id'], self.retest())['retests'][0]['comparison']
        self.assertEqual(comparison['feedback_stage'], 'publication_time_unknown')
        self.assertEqual(comparison['publication_evidence'][0]['evidence_level'], 'publisher_reported_success')

    def test_other_customer_report_cannot_be_attached(self):
        changed = self.retest()
        changed['profile']['name'] = '其他合成客户'
        plan = research.plan_from_report(self.baseline)
        plan['profile'] = changed['profile']
        changed['plan_sha256'] = plan_fingerprint(plan)
        with self.assertRaisesRegex(ValueError, '其他客户'):
            store.attach_retest(self.database, self.cycle['id'], changed)

    def test_platform_publication_time_precedes_later_reconciliation_and_minute_precision_is_preserved(self):
        self.prepare_article()
        with closing(sqlite3.connect(self.database)) as connection,connection:
            connection.execute('''INSERT INTO publish_jobs(article_id,platform,status,demo,result_url,finished_at,
                platform_published_at,platform_published_precision,result_checked_at) VALUES(?,?,?,?,?,?,?,?,?)''',
                (self.article_id,'toutiao','success',0,'https://www.toutiao.com/article/123/',
                 '2026-10-04 00:00:00','2026-10-03T12:00:00+08:00','minute','2026-10-04T00:00:00Z'))
        after=self.retest('2026-10-03T04:05:00Z')
        result=store.attach_retest(self.database,self.cycle['id'],after)
        comparison=result['retests'][0]['comparison']
        self.assertEqual(comparison['feedback_stage'],'post_publication_observation')
        evidence=comparison['publication_evidence'][0]
        self.assertEqual(evidence['published_at'],'2026-10-03T12:00:00+08:00')
        self.assertEqual(evidence['result_checked_at'],'2026-10-04T00:00:00Z')
        self.assertEqual(evidence['published_at_basis'],'platform_visible_public_time')
        inside_minute=research.publication_feedback({'conditions_match':True,'publication_citations':[]},
            self.retest('2026-10-03T04:00:30Z'),[evidence])
        self.assertEqual(inside_minute['feedback_stage'],'publication_time_unknown')
        self.assertIsNone(inside_minute['publication_evidence'][0]['all_answers_after_publication'])
        after_minute=research.publication_feedback({'conditions_match':True,'publication_citations':[]},
            self.retest('2026-10-03T04:01:00Z'),[evidence])
        self.assertEqual(after_minute['feedback_stage'],'post_publication_observation')
        with closing(sqlite3.connect(self.database)) as connection,connection:
            connection.execute('UPDATE publish_jobs SET platform_published_at=NULL,platform_published_precision=NULL WHERE article_id=?',(self.article_id,))
        legacy=store.get_cycle(self.database,self.cycle['id'])['retests'][0]['comparison']
        self.assertEqual(legacy['feedback_stage'],'retest_precedes_publication')
        self.assertEqual(legacy['publication_evidence'][0]['published_at_basis'],'publisher_completion_time')

    def test_toutiao_tracking_queries_and_item_urls_compare_only_same_strict_article_identity(self):
        target='https://www.toutiao.com/article/123456/?enter_from=management&wid=456'
        for observed,expected in (
            ('https://www.toutiao.com/article/123456/',True),
            ('https://www.toutiao.com/item/123456/?source=search',True),
            ('https://www.toutiao.com:443/article/123456/',True),
            ('https://www.toutiao.com/article/999999/',None),
            ('https://www.toutiao.com.evil.example/article/123456/',None),
            ('https://user:pass@www.toutiao.com/article/123456/',None),
            ('https://www.toutiao.com:444/article/123456/',None),
        ):
            with self.subTest(observed=observed):
                after=self.retest();after['cases'][0]['citations']=[{'url':observed}]
                value=research.compare_reports(self.baseline,after,[target])
                self.assertEqual(value['publication_citations'][0],{'url':target,'observed_in_citations':expected})
                self.assertEqual(after['cases'][0]['citations'][0]['url'],observed)
        after=self.retest();after['cases'][0]['citations']=[{'url':'https://other.example/article?id=2'}]
        self.assertIsNone(research.compare_reports(self.baseline,after,['https://other.example/article?id=1'])['publication_citations'][0]['observed_in_citations'])

    def test_draft_prompt_omits_operational_config_but_lineage_has_local_fingerprint(self):
        self.prepare_article(login_url='https://niche.example.org/login?session=private-login-marker',
                             editor_url='https://niche.example.org/editor?draft=private-editor-marker',
                             title_selector='input[name=private-title-field]',
                             body_selector='textarea[name=private-body-field]',
                             rules_url='https://niche.example.org/rules?private=rules-query-marker')
        cycle = store.get_cycle(self.database, self.cycle['id'])
        inputs = research.draft_input(cycle, 'b01', self.target['key'])
        prompt = research.draft_prompt(cycle, 'b01', self.target['key'])
        for value in ('private-login-marker', 'private-editor-marker', 'private-title-field',
                      'private-body-field', 'rules-query-marker', 'login_url', 'editor_url',
                      'title_selector', 'source_scope', 'interval_hours', self.target['id'],
                      inputs['destination_config_sha256']):
            with self.subTest(value=value):
                self.assertNotIn(value, prompt)
        self.assertIn('面向工程师', prompt)
        self.assertIn('https://niche.example.org/rules', prompt)
        self.assertEqual(inputs['destination_config_sha256'], research.fingerprint(self.target['config']))
        lineage = store.article_research(self.database, self.article_id)['provenance']
        self.assertEqual(lineage['destination_config_sha256'], inputs['destination_config_sha256'])
        self.assertEqual(lineage['destination']['id'], self.target['id'])
        self.assertNotIn('editor_url', lineage['destination'])

    def test_operational_change_invalidates_older_ai_output_even_with_unchanged_revision(self):
        self.prepare_article()
        cycle = store.get_cycle(self.database, self.cycle['id'])
        before = research.draft_input(cycle, 'b01', self.target['key'])
        changed = {**self.target['config'], 'login_url': 'https://niche.example.org/new-login'}
        # Simulate an integration writing config without incrementing revision;
        # the local full-config fingerprint must still reject an expired result.
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute('UPDATE geo_distribution_targets SET config=? WHERE id=?', (json.dumps(changed), self.target['id']))
        refreshed = store.get_cycle(self.database, self.cycle['id'])
        after = research.draft_input(refreshed, 'b01', self.target['key'])
        self.assertEqual(before['destination'], after['destination'])
        self.assertNotEqual(before['destination_config_sha256'], after['destination_config_sha256'])
        with self.assertRaisesRegex(ValueError, '变化'):
            store.save_draft(self.database, self.cycle['id'], 'b01', self.target['key'], draft(),
                {'method': 'synthetic', 'input_sha256': research.fingerprint(before)}, cycle['revision'])

    def test_api_records_owner_statement_without_network_or_job(self):
        self.prepare_article()
        from sau_backend import app
        with patch.dict(app.config, DATABASE_PATH=self.database, ACCESS_CONTROL_ENABLED=False, TESTING=True):
            client = app.test_client()
            path = '/api/geo-content/cycles/' + self.cycle['id'] + '/publications'
            with patch('requests.get', side_effect=AssertionError('Owner reporting does not access network')):
                result = client.post(path, json=self.payload())
            self.assertEqual(result.status_code, 200, result.json)
            self.assertEqual(result.json['data']['publications'], [])
            self.assertEqual(result.json['data']['reported_publications'][0]['evidence_level'], 'owner_reported')
            self.assertEqual(client.post(path, json={}).status_code, 400)
            self.assertEqual(client.post('/api/geo-content/cycles/missing/publications', json=self.payload()).status_code, 404)
            with patch.dict(app.config, ACCESS_CONTROL_ENABLED=True):
                self.assertEqual(client.post(path, json=self.payload()).status_code, 401)


if __name__ == '__main__':
    unittest.main()
