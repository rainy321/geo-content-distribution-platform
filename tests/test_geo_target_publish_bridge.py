"""Synthetic target lineage reaches built-in adapters only with current evidence."""
import json
import sqlite3
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta, timezone
from threading import Barrier
from unittest.mock import Mock, patch

from services import geo_destination_service as destinations, geo_content_store as store
from services.article_service import get_article
from services.geo_target_publish_guard import article_target_context, configuration_digest
from services.publish_job_service import (create_publish_job, retry_publish_job, InvalidPublishJobTransitionError,
    claim_publish_job, transition_publish_job, record_publish_submission_state)
from services.publish_retry_evidence import LEGACY_BEFORE_FILL_MESSAGE
from services.publish_job_executor import execute_publish_job
from services.publish_confirmation import article_review_fingerprint
from services.publisher_adapter import PublishResult
from tests.test_geo_content_loop import ResearchFixture, draft, facts
from tests.test_geo_destinations import config
from tests.test_publish_job_executor import StubPublisher


class TargetPublishBridgeTests(ResearchFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.config = config(name='合成头条投放目标', home_url='https://www.toutiao.com/',
            login_url='https://mp.toutiao.com/auth/page/login', editor_url='https://mp.toutiao.com/profile_v4/graphic/publish',
            source_scope='https://www.toutiao.com/', publication_scope='https://www.toutiao.com/',
            channel='toutiao', title_limit=30, daily_limit=1, interval_hours=6)
        self.target = destinations.save_target(self.database, self.cycle['project_id'], self.config)
        self.cycle = store.get_cycle(self.database, self.cycle['id'])
        self.cycle = store.save_facts(self.database, self.cycle['id'], facts(), self.cycle['revision'])
        saved = store.save_draft(self.database, self.cycle['id'], 'b01', self.target['key'], draft(), {'method': 'synthetic'}, self.cycle['revision'])
        self.article_id = saved['article_id']
        with closing(sqlite3.connect(self.database)) as conn, conn:
            conn.execute("UPDATE articles SET status='ready' WHERE id=?", (self.article_id,))
            self.account_id = conn.execute("INSERT INTO user_info(type,filePath,userName,status) VALUES(7,'synthetic.json','synthetic',1)").lastrowid

    def job(self):
        return create_publish_job(self.database, article_id=self.article_id, platform='toutiao', account_id=self.account_id)

    def update_config(self, **changes):
        self.target = destinations.save_target(self.database, self.cycle['project_id'], {**self.config, **changes}, self.target['id'], self.target['revision'])
        return self.target

    def test_same_article_and_cycle_reach_base_adapter_without_cloning(self):
        self.assertTrue(self.target['builtin_publisher']['available'])
        self.assertEqual(self.target['config_sha256'], configuration_digest(self.target['config']))
        job = self.job()
        publisher = StubPublisher('toutiao', PublishResult(False, 'toutiao', 'processing', message='synthetic accepted, URL unknown'))
        result = execute_publish_job(self.database, job['id'], publisher_factory=lambda _job: publisher)
        self.assertEqual(result['status'], 'processing')
        self.assertEqual(len(publisher.calls), 1)
        self.assertEqual(publisher.calls[0].platform, 'toutiao')
        with closing(sqlite3.connect(self.database)) as conn:
            self.assertEqual(conn.execute('SELECT cycle_id,platform FROM geo_content_drafts WHERE article_id=?', (self.article_id,)).fetchone(), (self.cycle['id'], self.target['key']))
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM articles').fetchone()[0], 1)

    def test_old_configuration_cannot_create_or_fill_or_be_reauthorized_by_retry(self):
        job = self.job()
        self.update_config(style='新的写作规范')
        with self.assertRaisesRegex(ValueError, '重新生成'):
            self.job()
        with self.assertRaisesRegex(ValueError, '重新生成'):
            destinations.reserve_fill(self.database, self.cycle['project_id'], self.target['id'], self.article_id)
        factory = Mock()
        result = execute_publish_job(self.database, job['id'], publisher_factory=factory)
        self.assertEqual(result['status'], 'need_action')
        factory.assert_not_called()
        with self.assertRaises(InvalidPublishJobTransitionError):
            retry_publish_job(self.database, job['id'])

    def test_configuration_change_without_revision_still_invalidates_sha(self):
        job = self.job()
        with closing(sqlite3.connect(self.database)) as conn, conn:
            current = json.loads(conn.execute('SELECT config FROM geo_distribution_targets').fetchone()[0])
            current['style'] = '模拟配置损坏，但版本号没变'
            conn.execute('UPDATE geo_distribution_targets SET config=?', (json.dumps(current),))
        factory = Mock()
        result = execute_publish_job(self.database, job['id'], publisher_factory=factory)
        self.assertEqual(result['status'], 'need_action')
        factory.assert_not_called()

    def test_wrong_channel_and_host_spoofing_never_fall_back_to_an_adapter(self):
        with self.assertRaisesRegex(ValueError, '渠道'):
            create_publish_job(self.database, article_id=self.article_id, platform='sohu', account_id=self.account_id)
        bad = {**self.config, 'source_scope': 'https://www.toutiao.com.evil.example.org/'}
        self.assertFalse(destinations.save_target(self.database, self.cycle['project_id'], bad)['builtin_publisher']['available'])
        bad['source_scope'] = 'https://www.toutiao.com/'
        bad['publication_scope'] = 'https://cloud.tencent.com/developer/'
        self.assertFalse(destinations.save_target(self.database, self.cycle['project_id'], bad)['builtin_publisher']['available'])

    def test_missing_provenance_disabled_or_cross_project_is_rejected(self):
        with closing(sqlite3.connect(self.database)) as conn, conn:
            conn.row_factory = sqlite3.Row
            original = conn.execute('SELECT provenance FROM geo_content_drafts').fetchone()[0]
            provenance = json.loads(original)
            for value in ([1, 2], {**provenance, 'destination_config_sha256': ''},
                          {**provenance, 'destination': {**provenance['destination'], 'id': 'other'}}):
                conn.execute('UPDATE geo_content_drafts SET provenance=?', (json.dumps(value),))
                with self.assertRaises(ValueError):
                    article_target_context(conn, self.article_id, platform='toutiao', require_builtin=True)
            conn.execute('UPDATE geo_content_drafts SET provenance=?', (original,))
            conn.execute("UPDATE geo_distribution_targets SET state='disabled'")
            with self.assertRaisesRegex(ValueError, '停用'):
                article_target_context(conn, self.article_id, platform='toutiao', require_builtin=True)
            conn.execute("UPDATE geo_distribution_targets SET state='configured'")
            conn.execute('UPDATE geo_content_cycles SET project_id=project_id+999')
            with self.assertRaisesRegex(ValueError, '同客户'):
                article_target_context(conn, self.article_id, platform='toutiao', require_builtin=True)

    def test_length_is_rechecked_after_owner_edits(self):
        with closing(sqlite3.connect(self.database)) as conn, conn:
            conn.execute('UPDATE articles SET title=? WHERE id=?', ('长' * 31, self.article_id))
        with self.assertRaisesRegex(ValueError, '长度'):
            self.job()

    def test_execution_rechecks_after_claim_before_factory(self):
        job = self.job()
        original = get_article
        def changed_after_claim(database, article_id):
            article = original(database, article_id)
            with closing(sqlite3.connect(database)) as conn, conn:
                conn.execute('UPDATE geo_distribution_targets SET revision=revision+1')
            return article
        factory = Mock()
        with patch('services.publish_job_executor.get_article', side_effect=changed_after_claim):
            result = execute_publish_job(self.database, job['id'], publisher_factory=factory)
        self.assertEqual(result['status'], 'need_action')
        factory.assert_not_called()

    def test_assisted_fills_and_builtin_jobs_share_custom_daily_threshold(self):
        stamp = (datetime.now(timezone.utc) - timedelta(hours=7)).isoformat()
        with closing(sqlite3.connect(self.database)) as conn, conn:
            conn.execute("INSERT INTO geo_distribution_events VALUES('fill-old',?,?,'fill','reviewed','synthetic',?)", (self.target['id'], self.article_id, stamp))
        with self.assertRaisesRegex(ValueError, '24 小时'):
            self.job()

    def test_local_publisher_interval_is_kept_when_custom_target_is_looser(self):
        with closing(sqlite3.connect(self.database)) as conn, conn:
            stamp = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime('%Y-%m-%d %H:%M:%S')
            other = conn.execute("INSERT INTO articles(project_id,title,content,status) VALUES(?,'other','synthetic','ready')", (self.cycle['project_id'],)).lastrowid
            conn.execute("INSERT INTO publish_jobs(article_id,platform,account_id,status,demo,created_at,started_at) VALUES(?,'toutiao',?,'failed',0,?,?)", (other, self.account_id, stamp, stamp))
        with self.assertRaisesRegex(ValueError, '3 小时'):
            self.job()

    def test_custom_limits_are_rechecked_before_claim(self):
        job = self.job()
        with closing(sqlite3.connect(self.database)) as conn, conn:
            conn.execute("INSERT INTO geo_distribution_events VALUES('fill-later',?,?,'fill','reviewed','synthetic',?)", (self.target['id'], self.article_id, datetime.now(timezone.utc).isoformat()))
        factory = Mock()
        result = execute_publish_job(self.database, job['id'], publisher_factory=factory)
        self.assertEqual(result['status'], 'need_action')
        factory.assert_not_called()

    def test_concurrent_creates_reserve_only_one_target_slot(self):
        barrier = Barrier(2)
        def create():
            barrier.wait(timeout=3)
            try:
                return self.job()['id']
            except ValueError:
                return None
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: create(), range(2)))
        self.assertEqual(sum(value is not None for value in results), 1)

    def test_running_unknown_result_blocks_target_changes_and_other_fills(self):
        job = self.job()
        publisher = StubPublisher('toutiao', PublishResult(False, 'toutiao', 'processing', message='unknown final state'))
        execute_publish_job(self.database, job['id'], publisher_factory=lambda _job: publisher)
        with self.assertRaisesRegex(ValueError, '正在发布|最终状态未知'):
            self.update_config(style='不能在发布中修改目标')
        with closing(sqlite3.connect(self.database)) as conn, conn:
            conn.execute("UPDATE articles SET status='ready' WHERE id=?", (self.article_id,))
        with self.assertRaisesRegex(ValueError, '处理中|最终状态未知'):
            destinations.reserve_fill(self.database, self.cycle['project_id'], self.target['id'], self.article_id)

    def test_toutiao_catalog_normalizes_current_article_constraints(self):
        preset = next(entry for key, _, _, entry in destinations.CATALOG if key == 'toutiao')
        saved = destinations.normalize_config(preset)
        self.assertEqual(saved['title_limit'], 30)
        self.assertEqual(saved['rules_url'], 'https://baike.toutiao.com/detail/236/238/0')
        self.assertIn('信息增量', saved['style'])
        self.assertIn('不得声明原创', saved['style'])
        self.assertIn('不代表自动合规', saved['style'])

    def test_http_bridge_keeps_original_article_and_requires_final_confirmation(self):
        from sau_backend import app
        publisher = StubPublisher('toutiao', PublishResult(False, 'toutiao', 'processing', message='synthetic result unknown'))
        with patch.dict(app.config, DATABASE_PATH=self.database, DEMO_MODE=False,
                        ALLOW_REAL_PUBLISHING=True, TESTING=True, DESKTOP_MODE=False):
            client = app.test_client()
            response = client.post('/api/publish', json={'article_id': self.article_id,
                'platform': 'toutiao', 'account_id': self.account_id,
                'expected_review_fingerprint': article_review_fingerprint(get_article(self.database,self.article_id))})
            self.assertEqual(response.status_code, 201, response.json)
            job = response.json['data']
            self.assertEqual(job['article_id'], self.article_id)
            factory = Mock(return_value=publisher)
            with patch('sau_backend.create_real_publisher_factory', return_value=factory):
                unconfirmed = client.post(f'/api/publish/jobs/{job["job_id"]}/execute-real', json={})
                self.assertEqual(unconfirmed.status_code, 400)
                factory.assert_not_called()
                current_job = client.get(f'/api/publish/jobs/{job["job_id"]}').json['data']
                confirmed = client.post(f'/api/publish/jobs/{job["job_id"]}/execute-real', json={
                    'confirm': True, 'expected_confirmation_snapshot': current_job['confirmation_snapshot']})
            self.assertEqual(confirmed.status_code, 200, confirmed.json)
            self.assertEqual(confirmed.json['data']['status'], 'processing')
            self.assertEqual(len(publisher.calls), 1)

    def stopped_zero_write(self, *, legacy=False):
        job = claim_publish_job(self.database, self.job()['id'])
        directory = self.database.parent / 'publish-evidence'
        if legacy:
            directory.mkdir(parents=True, exist_ok=True)
            (directory / f'job-{job["id"]}.json').write_text(json.dumps({'job_id':job['id'],
                'platform':'toutiao', 'updated_at':datetime.now(timezone.utc).isoformat(),
                'submission_phase':'stopped_before_submission','submission_attempted':False}), encoding='utf-8')
        else:
            record_publish_submission_state(self.database, job['id'], 'toutiao', {
                'submission_phase':'preparing','submission_attempted':False,'content_write_attempted':False})
            record_publish_submission_state(self.database, job['id'], 'toutiao', {
                'submission_phase':'stopped_before_submission','submission_attempted':False,'content_write_attempted':False})
        return transition_publish_job(self.database, job['id'], 'need_action', message=LEGACY_BEFORE_FILL_MESSAGE)

    def test_proven_zero_write_manual_retry_preserves_history_and_only_exempts_same_job(self):
        job = self.stopped_zero_write()
        journal = self.database.parent / 'publish-evidence' / f'job-{job["id"]}.json'
        original_journal = journal.read_bytes()
        with closing(sqlite3.connect(self.database)) as conn:
            first_claim = conn.execute("SELECT * FROM geo_distribution_events WHERE kind='builtin_publish'").fetchone()
        queued = retry_publish_job(self.database, job['id'])
        self.assertEqual(queued['status'], 'queued')
        self.assertEqual(queued['article_id'], self.article_id)
        self.assertEqual(journal.read_bytes(), original_journal)
        with self.assertRaises(ValueError):
            self.job()  # A new job never receives the old job's exemption.
        publisher = StubPublisher('toutiao', PublishResult(False,'toutiao','processing',message='synthetic unknown result'))
        result = execute_publish_job(self.database, queued['id'], publisher_factory=lambda _:publisher)
        self.assertEqual(result['status'], 'processing')
        self.assertEqual(len(publisher.calls), 1)
        with closing(sqlite3.connect(self.database)) as conn:
            rows = conn.execute("SELECT * FROM geo_distribution_events WHERE kind='builtin_publish' ORDER BY created_at").fetchall()
            review = json.loads(conn.execute("SELECT message FROM geo_distribution_events WHERE kind='builtin_retry_review'").fetchone()[0])
        self.assertEqual(len(rows),2)
        self.assertEqual(rows[0],first_claim)
        self.assertNotEqual(rows[0][0],rows[1][0])
        self.assertEqual((journal.parent / review['journal']).read_bytes(),original_journal)
        self.assertEqual(len(list((journal.parent/'history'/f'job-{job["id"]}'/'phases').glob('*.json'))),2)
        with self.assertRaises(InvalidPublishJobTransitionError):
            retry_publish_job(self.database, job['id'])  # Unknown processing cannot retry.

    def test_legacy_before_fill_proof_allows_one_retry_but_cannot_be_reused_for_a_later_claim(self):
        job = self.stopped_zero_write(legacy=True)
        retry_publish_job(self.database,job['id'])
        claimed = claim_publish_job(self.database,job['id'])
        transition_publish_job(self.database,claimed['id'],'need_action',message=LEGACY_BEFORE_FILL_MESSAGE)
        with self.assertRaises(InvalidPublishJobTransitionError):
            retry_publish_job(self.database,job['id'])  # Stale journal predates latest claim.

    def test_retry_after_any_content_write_is_still_limited(self):
        job = self.stopped_zero_write()
        retry_publish_job(self.database,job['id'])
        claim_publish_job(self.database,job['id'])
        record_publish_submission_state(self.database,job['id'],'toutiao',{
            'submission_phase':'stopped_before_submission','submission_attempted':False,'content_write_attempted':True})
        transition_publish_job(self.database,job['id'],'need_action',message=LEGACY_BEFORE_FILL_MESSAGE)
        with self.assertRaises(InvalidPublishJobTransitionError):
            retry_publish_job(self.database,job['id'])

    def test_ambiguous_or_wrong_attempt_journal_never_relaxes_the_quota(self):
        job = self.stopped_zero_write()
        journal = self.database.parent/'publish-evidence'/f'job-{job["id"]}.json'
        original = json.loads(journal.read_text(encoding='utf-8'))
        changes = ({'content_write_attempted':True}, {'content_write_attempted':None},
            {'submission_attempted':True}, {'submission_phase':'submission_result_unknown'},
            {'updated_at':'2020-01-01T00:00:00+00:00'}, {'job_id':job['id']+1},
            {'authorization_fingerprint':'wrong'}, {'claim_event_id':'publish-job:999'})
        for change in changes:
            with self.subTest(change=change):
                journal.write_text(json.dumps({**original,**change}),encoding='utf-8')
                with self.assertRaises(InvalidPublishJobTransitionError):
                    retry_publish_job(self.database,job['id'])
        with closing(sqlite3.connect(self.database)) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM geo_distribution_events WHERE kind='builtin_retry_review'").fetchone()[0],0)

    def test_archived_proof_is_rechecked_before_claim_and_final_confirmation_is_retained(self):
        from sau_backend import app
        job = self.stopped_zero_write(legacy=True)
        retry_publish_job(self.database,job['id'])
        factory = Mock()
        with patch.dict(app.config,DATABASE_PATH=self.database,DEMO_MODE=False,ALLOW_REAL_PUBLISHING=True,TESTING=True):
            with patch('sau_backend.create_real_publisher_factory',return_value=factory):
                response=app.test_client().post(f'/api/publish/jobs/{job["id"]}/execute-real',json={})
            self.assertEqual(response.status_code,400)
            factory.assert_not_called()
        with closing(sqlite3.connect(self.database)) as conn:
            proof = json.loads(conn.execute("SELECT message FROM geo_distribution_events WHERE kind='builtin_retry_review'").fetchone()[0])
        archived = self.database.parent/'publish-evidence'/proof['journal']
        archived.write_text('{}',encoding='utf-8')
        result = execute_publish_job(self.database,job['id'],publisher_factory=factory)
        self.assertEqual(result['status'],'need_action')
        factory.assert_not_called()
