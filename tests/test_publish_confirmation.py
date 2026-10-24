import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from threading import Barrier
from unittest.mock import Mock, patch

from db.createTable import initialize_database
from services.article_service import get_article
from services.publish_confirmation import article_review_fingerprint, publish_confirmation_snapshot
from services.publish_job_executor import execute_publish_job
from services.publish_job_service import (PublishConfirmationMismatchError, claim_publish_job,
    create_publish_job, get_publish_job, retry_publish_job, transition_publish_job)
from tests.test_publish_job_executor import StubPublisher
from services.publisher_adapter import PublishResult


class ConfirmationDigestTests(unittest.TestCase):
    def test_article_digest_is_stable_for_database_json_and_detail_dto(self):
        article = {'id':4,'project_id':2,'title':'合成标题','content':'exact body\n',
                   'tags':'["AI", " AI ", "Agent"]','status':'ready'}
        dto = {**article,'tags':['AI','Agent'],'geo_score':80,'summary':'not published'}
        self.assertEqual(article_review_fingerprint(article), article_review_fingerprint(dto))
        self.assertRegex(article_review_fingerprint(dto), '^[0-9a-f]{64}$')

    def test_article_payload_and_review_identity_changes_invalidate_digest(self):
        article = {'id':4,'project_id':2,'title':'title','content':'body','tags':['AI'],'status':'ready'}
        original = article_review_fingerprint(article)
        for field,value in (('id',5),('project_id',3),('title','title '),('content','body '),
                            ('tags',['Agent']),('status','draft')):
            with self.subTest(field=field):
                self.assertNotEqual(article_review_fingerprint({**article,field:value}), original)

    def test_job_digest_canonicalizes_stored_assets_and_bools(self):
        raw = {'id':1,'article_id':2,'platform':'zhihu','status':'queued','images':'["cover.png"]',
               'demo':0,'auto_execute':1,'authorization_fingerprint':'PRIVATE_AUTH_BINDING'}
        dto = {**raw,'images':['cover.png'],'demo':False,'auto_execute':True,'confirmation_snapshot':'ignored'}
        token = publish_confirmation_snapshot(raw)
        self.assertEqual(token, publish_confirmation_snapshot(dto))
        self.assertRegex(token, '^[0-9a-f]{64}$')
        self.assertNotIn('PRIVATE_AUTH_BINDING', token)
        for field,value in (('status','processing'),('images',['other.png']),('started_at','new attempt'),
                            ('account_name','other account'),('authorization_fingerprint','changed')):
            with self.subTest(field=field):
                self.assertNotEqual(publish_confirmation_snapshot({**raw,field:value}), token)


class ConfirmationTransactionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.database = Path(self.directory.name) / 'synthetic.db'
        initialize_database(self.database)
        with closing(sqlite3.connect(self.database)) as conn, conn:
            self.project = conn.execute("INSERT INTO projects(name) VALUES('synthetic confirmation')").lastrowid
            self.other_project = conn.execute("INSERT INTO projects(name) VALUES('synthetic other')").lastrowid
            self.article = conn.execute("INSERT INTO articles(project_id,title,content,tags,status) VALUES(?,'same title','original body','[\"AI\"]','ready')", (self.project,)).lastrowid
            self.other_article = conn.execute("INSERT INTO articles(project_id,title,content,status) VALUES(?,'other title','other body','ready')", (self.project,)).lastrowid
            self.account = conn.execute("INSERT INTO user_info(type,filePath,userName,status) VALUES(9,'synthetic.json','synthetic account',1)").lastrowid
            self.other_account = conn.execute("INSERT INTO user_info(type,filePath,userName,status) VALUES(9,'synthetic-other.json','other account',1)").lastrowid

    def tearDown(self):
        self.directory.cleanup()

    def _create(self, **kwargs):
        return create_publish_job(self.database, article_id=self.article,
                                  platform='zhihu', account_id=self.account, **kwargs)

    def _rows(self):
        with closing(sqlite3.connect(self.database)) as conn:
            return {table:conn.execute(f'SELECT * FROM {table}').fetchall()
                    for table in ('articles','publish_jobs','geo_distribution_events')}

    def test_create_conflict_rejects_server_invisible_article_edit_inside_transaction(self):
        review = article_review_fingerprint(get_article(self.database, self.article))
        with closing(sqlite3.connect(self.database)) as conn, conn:
            conn.execute("UPDATE articles SET content='changed body but same ready status' WHERE id=?", (self.article,))
        before = self._rows()
        from services import publish_job_service as service
        original_fetch = service._fetch_authorization_article
        def read_in_transaction(conn, *args, **kwargs):
            self.assertTrue(conn.in_transaction)
            return original_fetch(conn, *args, **kwargs)
        with patch.object(service, '_fetch_authorization_article', side_effect=read_in_transaction), \
                patch.object(service, '_resolve_publish_account_id', side_effect=AssertionError('mismatch must stop before binding or quota')):
            with self.assertRaises(PublishConfirmationMismatchError):
                self._create(expected_review_fingerprint=review)
        self.assertEqual(self._rows(), before)

    def test_current_review_allows_create_but_digest_is_not_permission_for_draft(self):
        review = article_review_fingerprint(get_article(self.database, self.article))
        job = self._create(expected_review_fingerprint=review)
        self.assertRegex(job['confirmation_snapshot'], '^[0-9a-f]{64}$')
        self.assertEqual(job['confirmation_snapshot'], get_publish_job(self.database,job['id'])['confirmation_snapshot'])
        with closing(sqlite3.connect(self.database)) as conn, conn:
            conn.execute("UPDATE articles SET status='draft' WHERE id=?", (self.other_article,))
        draft = get_article(self.database, self.other_article)
        with self.assertRaisesRegex(ValueError, '审稿'):
            create_publish_job(self.database, article_id=self.other_article, platform='zhihu',
                               expected_review_fingerprint=article_review_fingerprint(draft))

    def test_claim_conflict_checks_all_critical_job_fields_before_time_or_target_io(self):
        changes = {'images':'["cover.png"]','video':'other.mp4','publish_at':'2026-11-01T12:00:00Z',
                   'auto_execute':1,'authorization_fingerprint':'f'*64,'started_at':'2026-10-01 00:00:00',
                   'finished_at':'2026-10-01 01:00:00','account_id':self.other_account,
                   'article_id':self.other_article,'platform':'toutiao','status':'need_action'}
        from services import publish_job_service as service
        for field,value in changes.items():
            with self.subTest(field=field):
                job = self._create()
                with closing(sqlite3.connect(self.database)) as conn, conn:
                    conn.execute(f'UPDATE publish_jobs SET {field}=? WHERE id=?', (value,job['id']))
                before = self._rows()
                original_fetch = service._fetch_job
                def read_in_transaction(conn, *args):
                    self.assertTrue(conn.in_transaction)
                    return original_fetch(conn, *args)
                with patch.object(service,'_fetch_job',side_effect=read_in_transaction), \
                        patch.object(service,'_new_attempt_time',side_effect=AssertionError('no new attempt')), \
                        patch.object(service,'_fetch_authorization_article',side_effect=AssertionError('no target or article IO')):
                    with self.assertRaises(PublishConfirmationMismatchError):
                        claim_publish_job(self.database, job['id'], expected_confirmation_snapshot=job['confirmation_snapshot'])
                self.assertEqual(self._rows(), before)

    def test_re_reviewed_same_title_payload_cannot_use_old_execute_confirmation(self):
        original = self._create()
        transition_publish_job(self.database, original['id'], 'need_action', message='explicitly stopped before execution')
        with closing(sqlite3.connect(self.database)) as conn, conn:
            conn.execute("UPDATE articles SET content='new reviewed body' WHERE id=?", (self.article,))
        refreshed = retry_publish_job(self.database, original['id'])
        self.assertNotEqual(refreshed['confirmation_snapshot'], original['confirmation_snapshot'])
        before = self._rows()
        factory = Mock(side_effect=AssertionError('must never reach publisher'))
        with self.assertRaises(PublishConfirmationMismatchError):
            execute_publish_job(self.database, original['id'], publisher_factory=factory,
                                expected_confirmation_snapshot=original['confirmation_snapshot'])
        factory.assert_not_called()
        self.assertEqual(self._rows(), before)

    def test_invalid_provided_tokens_fail_closed_while_none_keeps_internal_compatibility(self):
        job = self._create()
        before = self._rows()
        for token in ('', '0'*64, '非'*64, [], False):
            with self.subTest(token=repr(token)), self.assertRaises(PublishConfirmationMismatchError):
                claim_publish_job(self.database,job['id'],expected_confirmation_snapshot=token)
        self.assertEqual(self._rows(), before)
        self.assertEqual(claim_publish_job(self.database,job['id'])['status'], 'processing')

    def test_matching_confirmation_still_checks_review_before_any_publisher(self):
        job = self._create()
        with closing(sqlite3.connect(self.database)) as conn, conn:
            conn.execute("UPDATE articles SET content='changed without new authorization' WHERE id=?", (self.article,))
        factory = Mock(side_effect=AssertionError('digest is not authorization'))
        result = execute_publish_job(self.database,job['id'],publisher_factory=factory,
                                    expected_confirmation_snapshot=job['confirmation_snapshot'])
        factory.assert_not_called()
        self.assertEqual(result['status'], 'need_action')
        self.assertIsNone(result['started_at'])

    def test_racing_same_confirmation_can_claim_and_call_publisher_only_once(self):
        job = self._create()
        barrier = Barrier(2)
        publisher = StubPublisher('zhihu',PublishResult(False,'zhihu','processing',message='synthetic pending result'))
        def execute_once():
            barrier.wait()
            try:
                return execute_publish_job(self.database,job['id'],publisher_factory=lambda _:publisher,
                                           expected_confirmation_snapshot=job['confirmation_snapshot'])['status']
            except PublishConfirmationMismatchError:
                return 'stale_confirmation'
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _:execute_once(), range(2)))
        self.assertCountEqual(results,['processing','stale_confirmation'])
        self.assertEqual(len(publisher.calls), 1)
