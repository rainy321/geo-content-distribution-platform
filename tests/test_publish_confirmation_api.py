import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock, patch

from db.createTable import initialize_database
from sau_backend import app
from services.publish_job_executor import execute_publish_job
from services.publish_job_service import create_publish_job
from services.publisher_adapter import PublishResult
from tests.test_publish_job_executor import StubPublisher


class PublishConfirmationApiTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.database = self.root / 'synthetic.db'
        initialize_database(self.database)
        self.original_config = {key:app.config.get(key) for key in (
            'DATABASE_PATH','COOKIES_DIRECTORY','MEDIA_ROOT','DEMO_MODE','ALLOW_REAL_PUBLISHING','DESKTOP_MODE','TESTING')}
        app.config.update(DATABASE_PATH=self.database,COOKIES_DIRECTORY=self.root/'cookies',
                          MEDIA_ROOT=self.root/'media',DEMO_MODE=False,ALLOW_REAL_PUBLISHING=True,
                          DESKTOP_MODE=False,TESTING=True)
        with closing(sqlite3.connect(self.database)) as conn, conn:
            project = conn.execute("INSERT INTO projects(name) VALUES('synthetic confirmation API')").lastrowid
            self.article = conn.execute("INSERT INTO articles(project_id,title,content,status) VALUES(?,'same title','reviewed body','ready')", (project,)).lastrowid
            self.account = conn.execute("INSERT INTO user_info(type,filePath,userName,status) VALUES(9,'PRIVATE_SESSION_PATH.json','synthetic account',1)").lastrowid
        self.client = app.test_client()

    def tearDown(self):
        app.config.update(self.original_config)
        self.directory.cleanup()

    def _review(self):
        return self.client.get(f'/api/articles/{self.article}').json['data']['review_fingerprint']

    def _create_payload(self, **kwargs):
        return {'article_id':self.article,'platform':'zhihu','account_id':self.account,
                'expected_review_fingerprint':self._review(),**kwargs}

    def _create(self):
        response = self.client.post('/api/publish',json=self._create_payload())
        self.assertEqual(response.status_code,201,response.json)
        return response.json['data']

    def _rows(self):
        with closing(sqlite3.connect(self.database)) as conn:
            return {table:conn.execute(f'SELECT * FROM {table}').fetchall()
                    for table in ('articles','publish_jobs','file_records','geo_distribution_events')}

    def test_missing_and_malformed_create_review_are_400_before_cover_or_task_side_effects(self):
        for value in (None,'','bad','A'*64,False,['0'*64]):
            with self.subTest(value=repr(value)), patch('sau_backend.ensure_article_cover') as cover:
                payload = {'article_id':self.article,'platform':'baijiahao','auto_image':True}
                if value is not None:
                    payload['expected_review_fingerprint'] = value
                before = self._rows()
                response = self.client.post('/api/publish',json=payload)
                self.assertEqual(response.status_code,400,response.json)
                cover.assert_not_called()
                self.assertEqual(self._rows(),before)

    def test_wrong_and_stale_create_review_are_409_without_changing_server_article_or_assets(self):
        stale = self._review()
        with closing(sqlite3.connect(self.database)) as conn, conn:
            conn.execute("UPDATE articles SET content='new server body' WHERE id=?",(self.article,))
        for review in ('0'*64,stale):
            before = self._rows()
            with patch('sau_backend.ensure_article_cover') as cover:
                response = self.client.post('/api/publish',json={
                    'article_id':self.article,'platform':'baijiahao','auto_image':True,
                    'expected_review_fingerprint':review})
            self.assertEqual(response.status_code,409,response.json)
            cover.assert_not_called()
            self.assertEqual(self._rows(),before)

    def test_create_api_forwards_review_for_final_transaction_check_after_precheck_race(self):
        payload = self._create_payload()
        changed = []
        def edit_after_api_precheck(*args, **kwargs):
            self.assertEqual(kwargs['expected_review_fingerprint'],payload['expected_review_fingerprint'])
            with closing(sqlite3.connect(self.database)) as conn, conn:
                conn.execute("UPDATE articles SET content='changed between API and transaction' WHERE id=?",(self.article,))
            changed.append(self._rows())
            return create_publish_job(*args,**kwargs)
        with patch('sau_backend.create_publish_job',side_effect=edit_after_api_precheck):
            response = self.client.post('/api/publish',json=payload)
        self.assertEqual(response.status_code,409,response.json)
        self.assertEqual(self._rows(),changed[0])

    def test_public_job_list_and_detail_expose_same_safe_snapshot_without_raw_authorization(self):
        created = self._create()
        detail = self.client.get(f'/api/publish/jobs/{created["job_id"]}').json['data']
        listing = self.client.get('/api/publish/jobs').json['data']['items'][0]
        for job in (created,detail,listing):
            self.assertRegex(job['confirmation_snapshot'],'^[0-9a-f]{64}$')
            self.assertEqual(job['confirmation_snapshot'],created['confirmation_snapshot'])
            self.assertNotIn('authorization_fingerprint',job)
            self.assertNotIn('PRIVATE_SESSION_PATH',str(job))
        self.assertTrue(created['authorization_bound'])

    def test_missing_and_malformed_execute_snapshot_are_400_without_factory_or_task_mutation(self):
        job = self._create()
        for value in (None,'','bad','A'*64,False,['0'*64]):
            with self.subTest(value=repr(value)), patch('sau_backend.create_real_publisher_factory') as factory:
                payload = {'confirm':True}
                if value is not None:
                    payload['expected_confirmation_snapshot'] = value
                before = self._rows()
                response = self.client.post(f'/api/publish/jobs/{job["job_id"]}/execute-real',json=payload)
                self.assertEqual(response.status_code,400,response.json)
                factory.assert_not_called()
                self.assertEqual(self._rows(),before)

    def test_wrong_execute_snapshot_is_409_without_publisher_call_or_row_mutation(self):
        job = self._create()
        factory = Mock(side_effect=AssertionError('must not instantiate publisher on stale confirmation'))
        before = self._rows()
        with patch('sau_backend.create_real_publisher_factory',return_value=factory):
            response = self.client.post(f'/api/publish/jobs/{job["job_id"]}/execute-real',json={
                'confirm':True,'expected_confirmation_snapshot':'0'*64})
        self.assertEqual(response.status_code,409,response.json)
        factory.assert_not_called()
        self.assertEqual(self._rows(),before)

    def test_execute_rejects_non_object_json_without_task_or_publisher_side_effects(self):
        job = self._create()
        for payload in ([1], 'not an object', 1, True):
            with self.subTest(payload=payload), patch('sau_backend.create_real_publisher_factory') as factory:
                before = self._rows()
                response = self.client.post(f'/api/publish/jobs/{job["job_id"]}/execute-real',json=payload)
                self.assertEqual(response.status_code,400,response.json)
                factory.assert_not_called()
                self.assertEqual(self._rows(),before)

    def test_execute_revalidates_snapshot_after_server_changes_between_route_and_claim(self):
        job = self._create()
        factory = Mock(side_effect=AssertionError('must not instantiate publisher on stale confirmation'))
        changed = []
        def edit_after_route_read(*args, **kwargs):
            self.assertEqual(kwargs['expected_confirmation_snapshot'],job['confirmation_snapshot'])
            with closing(sqlite3.connect(self.database)) as conn, conn:
                conn.execute("UPDATE publish_jobs SET video='changed.mp4' WHERE id=?",(job['job_id'],))
            changed.append(self._rows())
            return execute_publish_job(*args,**kwargs)
        with patch('sau_backend.create_real_publisher_factory',return_value=factory), \
                patch('sau_backend.execute_publish_job',side_effect=edit_after_route_read):
            response = self.client.post(f'/api/publish/jobs/{job["job_id"]}/execute-real',json={
                'confirm':True,'expected_confirmation_snapshot':job['confirmation_snapshot']})
        self.assertEqual(response.status_code,409,response.json)
        factory.assert_not_called()
        self.assertEqual(self._rows(),changed[0])

    def test_replaying_old_snapshot_cannot_submit_another_attempt(self):
        job = self._create()
        publisher = StubPublisher('zhihu',PublishResult(False,'zhihu','processing',message='synthetic final state unknown'))
        factory = Mock(return_value=publisher)
        payload = {'confirm':True,'expected_confirmation_snapshot':job['confirmation_snapshot']}
        with patch('sau_backend.create_real_publisher_factory',return_value=factory):
            first = self.client.post(f'/api/publish/jobs/{job["job_id"]}/execute-real',json=payload)
            before = self._rows()
            replay = self.client.post(f'/api/publish/jobs/{job["job_id"]}/execute-real',json=payload)
        self.assertEqual(first.status_code,200,first.json)
        self.assertEqual(first.json['data']['status'],'processing')
        self.assertEqual(replay.status_code,409,replay.json)
        self.assertEqual(factory.call_count,1)
        self.assertEqual(len(publisher.calls),1)
        self.assertEqual(self._rows(),before)


if __name__ == '__main__':
    unittest.main()
