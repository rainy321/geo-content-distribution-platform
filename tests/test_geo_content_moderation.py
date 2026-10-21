"""Synthetic operator moderation observations; no real accounts or network."""
import copy
import json
import sqlite3
import unittest
import uuid
from contextlib import closing
from unittest.mock import patch

from services import geo_content_store as store, geo_destination_service as destinations
from services.article_service import update_article
from services.project_service import create_project
from tests import test_geo_content_feedback as feedback
from tests.test_geo_content_loop import ResearchFixture, draft
from tests.test_geo_destinations import config


class TestModerationObservation(ResearchFixture, unittest.TestCase):
    prepare_article = feedback.TestPublicationFeedback.prepare_article
    payload = feedback.TestPublicationFeedback.payload
    retest = feedback.TestPublicationFeedback.retest

    def observation(self, state='rejected', **changes):
        article_id = changes.get('article_id', self.article_id)
        current = store.get_cycle(self.database, self.cycle['id'])
        article = next(item for item in current['drafts'] if item['article_id'] == article_id)
        return {'observation_id': uuid.uuid4().hex, 'article_id': article_id, 'target_id': self.target['id'],
                'state': state, 'confirmed': True, 'expected_article_sha256': article['article_sha256'],
                'note': '合成运营观察，未访问真实平台', **changes}

    def observe(self, state='rejected', **changes):
        return store.save_moderation_observation(self.database, self.cycle['id'], self.observation(state, **changes))

    def count_observations(self):
        with closing(sqlite3.connect(self.database)) as connection:
            return connection.execute("SELECT COUNT(*) FROM geo_distribution_events WHERE kind='moderation'").fetchone()[0]

    def test_pending_and_rejected_block_reporting_without_network_or_success(self):
        self.prepare_article()
        initial = destinations.get_target(self.database, self.cycle['project_id'], self.target['id'])
        for state in ('pending', 'rejected'):
            with self.subTest(state=state), patch('requests.get', side_effect=AssertionError('No verification request')):
                observed = self.observe(state)
                self.assertEqual(observed['moderation_observations'][0]['state'], state)
                self.assertEqual(observed['moderation_observations'][0]['source'], 'operator_reported')
                self.assertEqual(observed['publications'], [])
                with self.assertRaisesRegex(ValueError, '待审核或审核未通过'):
                    store.save_reported_publication(self.database, self.cycle['id'], self.payload())
        latest = store.get_cycle(self.database, self.cycle['id'])
        self.assertEqual(latest['drafts'][0]['status'], 'ready')
        self.assertEqual(latest['drafts'][0]['jobs'], [])
        target = destinations.get_target(self.database, self.cycle['project_id'], self.target['id'])
        self.assertEqual((target['state'], target['blocked_reason']), (initial['state'], initial['blocked_reason']))

    def test_unknown_or_free_text_receipt_cannot_erase_pending_or_rejection(self):
        self.prepare_article()
        for state in ('pending', 'rejected'):
            self.observe(state)
            self.observe('unknown', note='只看到提交成功，尚未核对审核与正文')
            destinations.record_event(self.database, self.target['id'], 'submit', 'success', '已提交', self.article_id)
            with self.subTest(state=state), self.assertRaisesRegex(ValueError, '待审核或审核未通过'):
                store.save_reported_publication(self.database, self.cycle['id'], self.payload())

    def test_login_or_submit_failure_text_is_not_moderation(self):
        self.prepare_article()
        for kind, state in (('login', 'need_action'), ('submit', 'need_action'), ('moderation', 'need_action')):
            destinations.record_event(self.database, self.target['id'], kind, state, '审核未通过（仅合成自由文本）', self.article_id)
        self.observe('unknown')
        result = store.save_reported_publication(self.database, self.cycle['id'], self.payload())
        self.assertEqual(len(result['reported_publications']), 1)
        self.assertEqual(result['blocked_publications'], [])

    def test_later_rejection_withholds_existing_owner_record_and_feedback_but_preserves_history(self):
        self.prepare_article()
        original = store.save_reported_publication(self.database, self.cycle['id'], self.payload())['reported_publications'][0]
        store.attach_retest(self.database, self.cycle['id'], self.retest())
        self.observe('rejected')
        result = store.get_cycle(self.database, self.cycle['id'])
        self.assertEqual(result['reported_publications'], [])
        self.assertEqual(result['publications'], [])
        self.assertEqual(result['blocked_publications'][0]['id'], original['id'])
        self.assertEqual(result['blocked_publications'][0]['moderation_state'], 'rejected')
        self.assertEqual(result['retests'][0]['comparison']['feedback_stage'], 'collection_only')
        self.assertFalse(result['retests'][0]['comparison']['publication_comparison_eligible'])
        self.assertEqual(result['retests'][0]['comparison']['publication_evidence'], [])
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM geo_content_publications').fetchone()[0], 1)

    def test_later_pending_observation_filters_publisher_success_without_rewriting_job(self):
        self.prepare_article()
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute('''INSERT INTO publish_jobs(article_id,platform,status,demo,result_url,finished_at)
                VALUES(?,?,?,?,?,?)''', (self.article_id, 'zhihu', 'success', 0, self.payload()['url'], '2026-10-03 04:00:00'))
        self.observe('pending')
        result = store.attach_retest(self.database, self.cycle['id'], self.retest())
        self.assertEqual(result['publications'], [])
        self.assertEqual(result['drafts'][0]['jobs'][0]['status'], 'success')
        self.assertEqual(result['blocked_publications'][0]['evidence_level'], 'publisher_reported_success')
        self.assertEqual(result['retests'][0]['comparison']['feedback_stage'], 'collection_only')

    def test_exact_public_content_observation_can_supersede_hold_but_does_not_create_publication(self):
        self.prepare_article()
        self.observe('rejected')
        result = self.observe('publicly_visible', url=self.payload()['url'], content_confirmed=True)
        self.assertEqual(result['publications'], [])
        self.assertEqual(result['reported_publications'], [])
        self.assertEqual(result['drafts'][0]['jobs'], [])
        reported = store.save_reported_publication(self.database, self.cycle['id'], self.payload())
        self.assertEqual(reported['reported_publications'][0]['evidence_level'], 'owner_reported')
        self.assertEqual(reported['blocked_publications'], [])
        self.assertEqual(reported['moderation_observations'][0]['source'], 'operator_reported')

    def test_public_observation_is_bound_to_exact_link_not_all_urls_for_article(self):
        self.prepare_article()
        self.observe('rejected')
        self.observe('publicly_visible', url=self.payload()['url'], content_confirmed=True)
        with self.assertRaisesRegex(ValueError, '待审核或审核未通过'):
            store.save_reported_publication(self.database, self.cycle['id'], self.payload(url='https://niche.example.org/blog/different-post'))
        result = store.save_reported_publication(self.database, self.cycle['id'], self.payload())
        self.assertEqual(len(result['reported_publications']), 1)

    def test_changed_article_cannot_use_stale_public_content_confirmation(self):
        self.prepare_article()
        self.observe('rejected')
        public = self.observation('publicly_visible', url=self.payload()['url'], content_confirmed=True)
        store.save_moderation_observation(self.database, self.cycle['id'], public)
        store.save_reported_publication(self.database, self.cycle['id'], self.payload())
        before = store.get_cycle(self.database, self.cycle['id'])['drafts'][0]
        update_article(self.database, self.article_id, {'content': before['content'] + '\n合成修订'})
        result = store.get_cycle(self.database, self.cycle['id'])
        self.assertEqual(result['reported_publications'], [])
        self.assertNotEqual(result['drafts'][0]['article_sha256'], before['article_sha256'])
        with self.assertRaisesRegex(ValueError, '内容已变更'):
            store.save_moderation_observation(self.database, self.cycle['id'], {**public, 'observation_id': uuid.uuid4().hex})
        # Retrying the identical old observation is idempotent, not a new
        # confirmation of the changed article or an escape from the hold.
        store.save_moderation_observation(self.database, self.cycle['id'], public)
        self.assertEqual(self.count_observations(), 2)
        with self.assertRaisesRegex(ValueError, '待审核或审核未通过'):
            store.save_reported_publication(self.database, self.cycle['id'], self.payload())

    def test_same_timestamp_uses_insertion_order_and_replay_does_not_move_an_old_observation(self):
        self.prepare_article()
        pending = self.observation('pending')
        public = self.observation('publicly_visible', url=self.payload()['url'], content_confirmed=True)
        with patch.object(store, '_now', return_value='2026-10-04T00:00:00+00:00'):
            store.save_moderation_observation(self.database, self.cycle['id'], pending)
            store.save_moderation_observation(self.database, self.cycle['id'], public)
            result = store.save_moderation_observation(self.database, self.cycle['id'], pending)
        self.assertEqual([o['id'] for o in result['moderation_observations']], [public['observation_id'], pending['observation_id']])
        self.assertEqual(self.count_observations(), 2)
        self.assertEqual(len(store.save_reported_publication(self.database, self.cycle['id'], self.payload())['reported_publications']), 1)

    def test_idempotency_key_conflict_never_overwrites_original(self):
        self.prepare_article()
        value = self.observation('rejected')
        first = store.save_moderation_observation(self.database, self.cycle['id'], value)
        replay = store.save_moderation_observation(self.database, self.cycle['id'], copy.deepcopy(value))
        self.assertEqual(first['updated_at'], replay['updated_at'])
        self.assertEqual(self.count_observations(), 1)
        with self.assertRaisesRegex(ValueError, '已有不同记录'):
            store.save_moderation_observation(self.database, self.cycle['id'], {**value, 'state': 'publicly_visible',
                'url': self.payload()['url'], 'content_confirmed': True})
        self.assertEqual(self.count_observations(), 1)
        self.assertEqual(store.get_cycle(self.database, self.cycle['id'])['moderation_observations'][0]['state'], 'rejected')

    def test_rejection_of_another_article_does_not_block_this_article(self):
        self.prepare_article()
        self.observe('rejected')
        cycle = store.get_cycle(self.database, self.cycle['id'])
        cycle = store.save_facts(self.database, cycle['id'], cycle['facts'] + [
            {'text': '第二篇合成稿的独立补充资料', 'source_url': '', 'confirmed': True}], cycle['revision'])
        second = store.save_draft(self.database, cycle['id'], 'b01', self.target['key'], draft(),
                                  {'method': 'synthetic'}, cycle['revision'])['article_id']
        self.assertNotEqual(second, self.article_id)
        update_article(self.database, second, {'status': 'ready'})
        result = store.save_reported_publication(self.database, cycle['id'], self.payload(article_id=second))
        self.assertEqual(result['reported_publications'][0]['article_id'], second)

    def test_other_target_or_customer_or_cycle_cannot_receive_this_article_observation(self):
        self.prepare_article()
        second_target = destinations.save_target(self.database, self.cycle['project_id'], config(name='第二合成目标'))
        other_project = create_project(self.database, name='另一个合成客户', website='', product='', industry='', description='', keywords=[], competitors=[])
        other_target = destinations.save_target(self.database, other_project['id'], config())
        for target in (second_target, other_target):
            with self.subTest(target=target['id']), self.assertRaisesRegex(ValueError, '本轮客户'):
                self.observe('rejected', target_id=target['id'])
        another_cycle = store.create_cycle(self.database, self.retest(), 'synthetic-next-cycle')
        with self.assertRaisesRegex(ValueError, '本轮客户'):
            store.save_moderation_observation(self.database, another_cycle['id'], self.observation())
        self.assertEqual(self.count_observations(), 0)

    def test_another_articles_registered_link_is_not_accepted_as_this_articles_public_content(self):
        self.prepare_article()
        store.save_reported_publication(self.database, self.cycle['id'], self.payload())
        cycle = store.get_cycle(self.database, self.cycle['id'])
        cycle = store.save_facts(self.database, cycle['id'], cycle['facts'] + [
            {'text': '另一个合成版本的资料', 'source_url': '', 'confirmed': True}], cycle['revision'])
        second = store.save_draft(self.database, cycle['id'], 'b01', self.target['key'], draft(),
                                  {'method': 'synthetic'}, cycle['revision'])['article_id']
        with self.assertRaisesRegex(ValueError, '已经关联其他文章'):
            self.observe('publicly_visible', article_id=second, url=self.payload()['url'], content_confirmed=True)
        self.assertEqual(self.count_observations(), 0)

    def test_bad_public_confirmation_or_payload_is_rejected_without_record(self):
        self.prepare_article()
        base = self.observation('publicly_visible', url=self.payload()['url'], content_confirmed=True)
        invalid = [
            {'confirmed': False}, {'observation_id': 'not-an-id'}, {'article_id': True}, {'state': []},
            {'state': 'success'}, {'expected_article_sha256': 'bad'}, {'note': 'x' * 401},
            {'url': ''}, {'content_confirmed': False}, {'url': 'https://niche.example.org/'},
            {'url': 'https://niche.example.org/editor'}, {'url': 'https://evil.example.org/post'},
            {'url': 'https://127.0.0.1/post'}, {'url': False},
        ]
        for change in invalid:
            with self.subTest(change=change), self.assertRaises(ValueError):
                store.save_moderation_observation(self.database, self.cycle['id'], {**base, **change})
        self.assertEqual(self.count_observations(), 0)

    def test_invalid_public_event_metadata_cannot_clear_known_rejection(self):
        self.prepare_article()
        self.observe('rejected')
        for metadata in ('invalid-json', json.dumps({'schema_version': 1, 'source': 'operator_reported', 'url': self.payload()['url'],
                'article_sha256': 'not-a-hash', 'content_confirmed': True, 'note': []})):
            with closing(sqlite3.connect(self.database)) as connection, connection:
                connection.execute('INSERT INTO geo_distribution_events VALUES(?,?,?,?,?,?,?)',
                    (uuid.uuid4().hex, self.target['id'], self.article_id, 'moderation', 'publicly_visible', metadata, '9999-01-01'))
        with self.assertRaisesRegex(ValueError, '待审核或审核未通过'):
            store.save_reported_publication(self.database, self.cycle['id'], self.payload())


if __name__ == '__main__':
    unittest.main()
