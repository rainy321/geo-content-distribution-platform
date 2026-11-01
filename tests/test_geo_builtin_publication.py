"""Synthetic builtin owner reports; no customer DB, sessions or platform access."""
import copy
import sqlite3
import unittest
import uuid
from contextlib import closing
from unittest.mock import patch

from db.createTable import initialize_database
from services import geo_content_store as store, geo_destination_service as destinations
from services.article_service import get_article, update_article
from services.publish_job_service import create_publish_job, get_publish_job
from tests import test_geo_content_feedback as feedback
from tests.test_geo_content_loop import ResearchFixture, draft
from tests.test_geo_destinations import config


class TestBuiltinPublication(ResearchFixture, unittest.TestCase):
    retest = feedback.TestPublicationFeedback.retest

    def setUp(self):
        super().setUp()
        self.prepare_facts()
        self.article_id = store.save_draft(self.database, self.cycle['id'], 'b01', 'zhihu', draft(),
                                          {'method': 'synthetic'}, self.cycle['revision'])['article_id']
        update_article(self.database, self.article_id, {'status': 'ready'})
        with closing(sqlite3.connect(self.database)) as connection, connection:
            self.account_id = connection.execute('''INSERT INTO user_info(type,filePath,userName,status)
                VALUES(9,'synthetic-only.json','synthetic account',1)''').lastrowid
        self.job_id = create_publish_job(self.database, article_id=self.article_id, platform='zhihu',
                                         account_id=self.account_id, require_account=True)['id']
        self.change_job(status='need_action', started_at='2026-10-03T10:00+08:00',
                        message='合成导入核对失败，本次未点击发布', result_url='')

    def change_job(self, **changes):
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute('UPDATE publish_jobs SET ' + ','.join(key + '=?' for key in changes)
                               + ' WHERE id=?', (*changes.values(), self.job_id))

    def payload(self, **changes):
        article = get_article(self.database, self.article_id)
        job = get_publish_job(self.database, self.job_id)
        return {'kind': 'builtin_job', 'article_id': self.article_id, 'job_id': self.job_id,
                'account_id': self.account_id, 'expected_review_fingerprint': article['review_fingerprint'],
                'expected_confirmation_snapshot': job['confirmation_snapshot'],
                'url': 'https://zhuanlan.zhihu.com/p/2100208460726116757',
                'published_at': '2026-10-03T12:00+08:00', 'published_at_precision': 'minute',
                'confirmed': True, 'note': '合成作者会话观察，未独立匿名核验；平台秒数未知', **changes}

    def save(self, **changes):
        return store.save_reported_publication(self.database, self.cycle['id'], self.payload(**changes))

    def rows(self, table):
        with closing(sqlite3.connect(self.database)) as connection:
            return connection.execute('SELECT * FROM ' + table + ' ORDER BY rowid').fetchall()

    def test_normal_api_records_minute_owner_evidence_without_rewriting_job_or_article(self):
        from sau_backend import app
        payload = self.payload()
        unchanged = {table: self.rows(table) for table in ('publish_jobs', 'articles', 'geo_content_publications',
                                                          'geo_distribution_targets', 'geo_distribution_events')}
        with patch.dict(app.config, DATABASE_PATH=self.database, ACCESS_CONTROL_ENABLED=False, TESTING=True):
            client = app.test_client()
            endpoint = '/api/geo-content/cycles/' + self.cycle['id'] + '/publications'
            with patch('requests.get', side_effect=AssertionError('No platform requests')):
                first = client.post(endpoint, json=payload)
                second = client.post(endpoint, json=copy.deepcopy(payload))
            self.assertEqual(first.status_code, 200, first.json)
            self.assertEqual(second.status_code, 200, second.json)
            self.assertEqual(first.json['data']['updated_at'], second.json['data']['updated_at'])
            evidence = first.json['data']['reported_publications'][0]
            self.assertEqual(first.json['data']['reported_publications'], second.json['data']['reported_publications'])
            self.assertEqual(first.json['data']['publications'], [])
            self.assertEqual(evidence['kind'], 'builtin_job')
            self.assertEqual(evidence['job_id'], self.job_id)
            self.assertEqual(evidence['account_id'], self.account_id)
            self.assertIsNone(evidence['target_id'])
            self.assertEqual(evidence['review_fingerprint'], payload['expected_review_fingerprint'])
            self.assertEqual(evidence['url'], payload['url'])
            self.assertEqual(evidence['published_at'], '2026-10-03T04:00+00:00')
            self.assertEqual(evidence['published_at_precision'], 'minute')
            self.assertEqual(evidence['published_at_basis'], 'operator_reported_platform_time')
            self.assertEqual(evidence['evidence_level'], 'owner_reported')
            self.assertEqual(evidence['baseline_run_id'], self.baseline['run_id'])
            self.assertEqual(evidence['plan_sha256'], self.baseline['plan_sha256'])
            self.assertEqual(evidence['note'], payload['note'])
            self.assertEqual(client.post(endpoint, json={**payload, 'confirmed': False}).status_code, 400)
            self.assertEqual(client.post(endpoint.replace(self.cycle['id'], 'missing'), json=payload).status_code, 404)
            with patch.dict(app.config, ACCESS_CONTROL_ENABLED=True):
                self.assertEqual(client.post(endpoint, json=payload).status_code, 401)
        self.assertEqual(len(self.rows('geo_builtin_publications')), 1)
        for table, before in unchanged.items():
            self.assertEqual(self.rows(table), before, table)

    def test_unsafe_urls_wrong_types_timestamps_and_precision_are_rejected(self):
        invalid = [{'confirmed': False}, {'article_id': True}, {'job_id': 1.0}, {'account_id': False},
                   {'job_id': 2 ** 63}, {'expected_review_fingerprint': 'A' * 64},
                   {'expected_confirmation_snapshot': False}, {'target_id': 'fabricated'},
                   {'platform': 'zhihu'}, {'evidence_level': 'publisher_reported_success'},
                   {'published_at_precision': 'second'}, {'published_at': '2026-10-03T12:00'},
                   {'published_at': '2026-10-03T12:00:01+08:00'},
                   {'published_at': '2026-10-03T12:00:00.001+08:00'},
                   {'published_at': '2026-10-03T12:00+08:00:30'},
                   {'published_at': '2026-09-30T12:00+08:00'},
                   {'published_at': '2099-10-03T12:00+08:00'}, {'note': 'x' * 1001}]
        urls = [True, 2100208460726116757, 2.1002084607261168e18, [], None,
                'https://zhuanlan.zhihu.com/p/2100208460726116757/edit',
                'https://zhuanlan.zhihu.com/', 'https://www.zhihu.com/signin',
                'https://www.zhihu.com/people/account', 'https://zhuanlan.zhihu.com/p/1?next=/edit',
                'https://zhuanlan.zhihu.com/p/1#fragment', 'https://user@zhuanlan.zhihu.com/p/1',
                'https://zhuanlan.zhihu.com.evil.example/p/1', 'http://zhuanlan.zhihu.com/p/1',
                'https://zhuanlan.zhihu.com:443/p/1', 'https://zhuanlan.zhihu.com/p/1/',
                'https://zhuanlan.zhihu.com/p/1\n', 'https://zhuanlan.zhihu.com/p/1.0',
                'https://zhuanlan.zhihu.com/p/1e19', 'https://zhuanlan.zhihu.com/p/%31']
        for changes in invalid + [{'url': url} for url in urls]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.save(**changes)
        self.assertEqual(self.rows('geo_builtin_publications'), [])

    def test_unattempted_running_demo_and_wrong_job_bindings_are_rejected(self):
        baseline = self.rows('publish_jobs')[0]
        for changes in ({'status': 'queued'}, {'status': 'processing'}, {'status': 'scheduled'},
                        {'status': 'success'}, {'demo': 1}, {'started_at': None},
                        {'started_at': 'not-a-time'}, {'started_at': '2099-01-01T00:00Z'},
                        {'platform': 'toutiao'}, {'authorization_fingerprint': ''},
                        {'account_id': None}):
            with self.subTest(changes=changes):
                self.change_job(**changes)
                with self.assertRaises(ValueError):
                    self.save()
                with closing(sqlite3.connect(self.database)) as connection, connection:
                    columns = [row[1] for row in connection.execute('PRAGMA table_info(publish_jobs)')]
                    connection.execute('UPDATE publish_jobs SET ' + ','.join(key + '=?' for key in columns)
                                       + ' WHERE id=?', (*baseline, self.job_id))
        self.assertEqual(self.rows('geo_builtin_publications'), [])

    def test_changed_article_source_review_task_or_account_cannot_use_old_snapshots(self):
        payload = self.payload()
        update_article(self.database, self.article_id, {'content': get_article(self.database, self.article_id)['content'] + '\n修订'})
        with self.assertRaisesRegex(ValueError, '已变更'):
            store.save_reported_publication(self.database, self.cycle['id'], payload)
        with self.assertRaisesRegex(ValueError, '原任务授权'):
            self.save()  # Even a refreshed review cannot repurpose old authorization.
        self.assertEqual(self.rows('geo_builtin_publications'), [])

    def test_changed_sources_or_stale_task_snapshot_are_rejected(self):
        payload = self.payload()
        self.change_job(message='new task observation')
        with self.assertRaisesRegex(ValueError, '任务或绑定账号已变更'):
            store.save_reported_publication(self.database, self.cycle['id'], payload)
        self.cycle = store.save_facts(self.database, self.cycle['id'], self.cycle['facts'] + [
            {'text': '新增合成来源', 'source_url': '', 'confirmed': True}], self.cycle['revision'])
        with self.assertRaisesRegex(ValueError, '来源或事实资料已变更'):
            self.save()

    def test_other_cycles_customers_platforms_draft_and_accounts_are_rejected(self):
        other_cycle = store.create_cycle(self.database, self.retest(), 'synthetic-other-cycle')
        with self.assertRaisesRegex(ValueError, '本轮客户'):
            store.save_reported_publication(self.database, other_cycle['id'], self.payload())
        with self.assertRaisesRegex(ValueError, '账号不匹配'):
            self.save(account_id=self.account_id + 100)
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute('UPDATE user_info SET type=7 WHERE id=?', (self.account_id,))
        with self.assertRaisesRegex(ValueError, '账号不匹配'):
            self.save()
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute('UPDATE user_info SET type=9 WHERE id=?', (self.account_id,))
            other_project = connection.execute("INSERT INTO projects(name) VALUES('other synthetic customer')").lastrowid
            connection.execute('UPDATE articles SET project_id=? WHERE id=?', (other_project, self.article_id))
        with self.assertRaisesRegex(ValueError, '本轮客户'):
            self.save()
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute('UPDATE articles SET project_id=? WHERE id=?', (self.cycle['project_id'], self.article_id))
            connection.execute("UPDATE geo_content_drafts SET platform='toutiao' WHERE article_id=?", (self.article_id,))
        with self.assertRaisesRegex(ValueError, '本轮客户'):
            self.save()
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute("UPDATE geo_content_drafts SET platform='zhihu' WHERE article_id=?", (self.article_id,))
        update_article(self.database, self.article_id, {'status': 'draft'})
        with self.assertRaisesRegex(ValueError, '标记就绪'):
            self.save()

    def test_same_binding_is_idempotent_but_changed_url_time_note_are_conflicts(self):
        first = self.save()['reported_publications'][0]
        self.assertEqual(self.save(published_at='2026-10-03T04:00Z')['reported_publications'][0]['id'], first['id'])
        for changes in ({'url': 'https://zhuanlan.zhihu.com/p/2100208460726116758'},
                        {'published_at': '2026-10-03T12:01+08:00'}, {'note': 'changed observation'}):
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, '已有不同登记'):
                self.save(**changes)
        self.assertEqual(len(self.rows('geo_builtin_publications')), 1)

    def test_failed_task_and_already_published_article_keep_owner_reported_semantics(self):
        self.change_job(status='failed')
        update_article(self.database, self.article_id, {'status': 'published'})
        cycle = self.save()
        self.assertEqual(cycle['publications'], [])
        self.assertEqual(cycle['reported_publications'][0]['evidence_level'], 'owner_reported')
        self.assertEqual(cycle['drafts'][0]['jobs'][0]['status'], 'failed')
        self.assertEqual(cycle['drafts'][0]['status'], 'published')

    def test_pending_rejected_and_later_holds_are_never_bypassed(self):
        target = destinations.save_target(self.database, self.cycle['project_id'], config())
        first = self.save()['reported_publications'][0]
        for state in ('pending', 'rejected'):
            destinations.record_event(self.database, target['id'], 'moderation', state,
                                      '合成历史审核记录', self.article_id)
            with self.subTest(state=state), self.assertRaisesRegex(ValueError, '不能用人工登记跳过'):
                self.save()
            cycle = store.get_cycle(self.database, self.cycle['id'])
            self.assertEqual(cycle['reported_publications'], [])
            self.assertEqual(cycle['blocked_publications'][0]['id'], first['id'])
            comparison = store.attach_retest(self.database, self.cycle['id'], self.retest())['retests'][0]['comparison']
            self.assertEqual(comparison['publication_evidence'], [])
        self.assertEqual(len(self.rows('geo_builtin_publications')), 1)

    def test_other_job_active_task_and_other_article_bindings_are_rejected(self):
        original_job = self.job_id
        other = create_publish_job(self.database, article_id=self.article_id, platform='zhihu',
                                   account_id=self.account_id, require_account=True)['id']
        with self.assertRaisesRegex(ValueError, '还有排队'):
            self.save()
        self.job_id = other
        self.change_job(status='need_action', started_at='2026-10-03T10:01+08:00')
        self.job_id = original_job
        self.save()
        self.job_id = other
        with self.assertRaisesRegex(ValueError, '已有不同登记'):
            self.save()
        with closing(sqlite3.connect(self.database)) as connection, connection:
            unrelated = connection.execute('''INSERT INTO articles(project_id,title,content,status)
                VALUES(?,'other synthetic article','unrelated body','ready')''', (self.cycle['project_id'],)).lastrowid
        self.change_job(article_id=unrelated)
        with self.assertRaisesRegex(ValueError, '任务、稿件'):
            self.save(url='https://zhuanlan.zhihu.com/p/2100208460726116758')

    def test_existing_target_owner_record_and_adapter_success_url_cannot_be_rebound(self):
        target = destinations.save_target(self.database, self.cycle['project_id'],
                                           config(publication_scope='https://zhuanlan.zhihu.com/p/'))
        cycle = store.get_cycle(self.database, self.cycle['id'])
        article = store.save_draft(self.database, cycle['id'], 'b01', target['key'], draft(),
                                    {'method': 'synthetic'}, cycle['revision'])['article_id']
        update_article(self.database, article, {'status': 'ready'})
        url = self.payload()['url']
        store.save_reported_publication(self.database, cycle['id'], {
            'article_id': article, 'target_id': target['id'], 'url': url,
            'published_at': '2026-10-03T12:00+08:00', 'confirmed': True})
        with self.assertRaisesRegex(ValueError, '已有关联记录'):
            self.save()
        another_url = 'https://zhuanlan.zhihu.com/p/2100208460726116758'
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute('''INSERT INTO publish_jobs(article_id,platform,status,demo,result_url,finished_at)
                VALUES(?,'zhihu','success',0,?,'2026-10-03 04:00:00')''', (article, another_url))
        with self.assertRaisesRegex(ValueError, '已有关联记录'):
            self.save(url=another_url)
        self.assertEqual(self.rows('geo_builtin_publications'), [])

    def test_builtin_link_cannot_later_be_reused_for_target_registration_or_public_observation(self):
        self.save()
        target = destinations.save_target(self.database, self.cycle['project_id'],
                                           config(publication_scope='https://zhuanlan.zhihu.com/p/'))
        cycle = store.get_cycle(self.database, self.cycle['id'])
        article_id = store.save_draft(self.database, cycle['id'], 'b01', target['key'], draft(),
                                       {'method': 'synthetic'}, cycle['revision'])['article_id']
        update_article(self.database, article_id, {'status': 'ready'})
        with self.assertRaisesRegex(ValueError, '已经关联内置渠道'):
            store.save_reported_publication(self.database, cycle['id'], {
                'article_id': article_id, 'target_id': target['id'], 'url': self.payload()['url'],
                'published_at': '2026-10-03T12:00+08:00', 'confirmed': True})
        current = next(item for item in store.get_cycle(self.database, cycle['id'])['drafts']
                       if item['article_id'] == article_id)
        with self.assertRaisesRegex(ValueError, '已经关联其他文章'):
            store.save_moderation_observation(self.database, cycle['id'], {
                'observation_id': uuid.uuid4().hex, 'article_id': article_id, 'target_id': target['id'],
                'url': self.payload()['url'], 'state': 'publicly_visible', 'content_confirmed': True,
                'expected_article_sha256': current['article_sha256'], 'confirmed': True})
        self.assertEqual(self.rows('geo_content_publications'), [])
        self.assertEqual(len(self.rows('geo_builtin_publications')), 1)

    def test_same_minute_retest_stays_unknown_and_later_retest_is_owner_context(self):
        self.save()
        for collected_at, expected in (('2026-10-03T04:00:00Z', 'retest_precedes_publication'),
                                       ('2026-10-03T04:00:59.999Z', 'publication_time_unknown'),
                                       ('2026-10-03T04:01:00Z', 'post_publication_observation')):
            with self.subTest(collected_at=collected_at):
                cycle = store.attach_retest(self.database, self.cycle['id'], self.retest(collected_at))
                comparison = cycle['retests'][0]['comparison']
                self.assertEqual(comparison['feedback_stage'], expected)
                self.assertEqual(comparison['publication_evidence'][0]['evidence_level'], 'owner_reported')
                self.assertIn('未独立核验', comparison['publication_context'])
                self.assertEqual(comparison['effectiveness_conclusion'], 'insufficient_evidence')

    def test_upgrade_adds_only_the_builtin_table_and_keeps_existing_schema_and_rows(self):
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute('DROP TABLE geo_builtin_publications')
            schema = connection.execute("SELECT type,name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name").fetchall()
            tables = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
            rows = {table: connection.execute('SELECT * FROM ' + table).fetchall() for table in tables}
        initialize_database(self.database)
        with closing(sqlite3.connect(self.database)) as connection:
            after = connection.execute("SELECT type,name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' AND name!='geo_builtin_publications' ORDER BY type,name").fetchall()
            self.assertEqual(after, schema)
            for table, before in rows.items():
                self.assertEqual(connection.execute('SELECT * FROM ' + table).fetchall(), before, table)
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM geo_builtin_publications').fetchone()[0], 0)
            self.assertEqual(connection.execute('PRAGMA foreign_key_check').fetchall(), [])
