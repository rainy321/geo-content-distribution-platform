import sqlite3
import json
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock, patch

from db.createTable import initialize_database
from sau_backend import app
from services.publish_job_service import create_publish_job, get_publish_job


class RealPublishApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "real-publish.db"
        initialize_database(self.db_path)
        with closing(sqlite3.connect(self.db_path)) as conn:
            with conn:
                project_id = conn.execute(
                    "INSERT INTO projects (name) VALUES ('真实发布测试')"
                ).lastrowid
                self.article_id = conn.execute(
                    """
                    INSERT INTO articles (project_id, title, content, status)
                    VALUES (?, '待发布文章', '正文', 'ready')
                    """,
                    (project_id,),
                ).lastrowid

        self.original_config = {
            key: app.config.get(key)
            for key in ("DATABASE_PATH", "DEMO_MODE", "ALLOW_REAL_PUBLISHING", "TESTING")
        }
        app.config.update(
            DATABASE_PATH=self.db_path,
            DEMO_MODE=False,
            ALLOW_REAL_PUBLISHING=True,
            TESTING=True,
        )
        self.client = app.test_client()

    def tearDown(self):
        app.config.update(self.original_config)
        self.temp_dir.cleanup()

    def _create_job(self, platform="zhihu", *, demo=False):
        return create_publish_job(
            self.db_path,
            article_id=self.article_id,
            platform=platform,
            demo=demo,
        )

    def _execute_payload(self, job):
        current = self.client.get(f"/api/publish/jobs/{job['id']}").json['data']
        return {'confirm': True, 'expected_confirmation_snapshot': current['confirmation_snapshot']}

    def _review_fingerprint(self):
        return self.client.get(f'/api/articles/{self.article_id}').json['data']['review_fingerprint']

    def test_requires_all_feature_and_request_confirmation_gates(self):
        job = self._create_job()

        app.config["ALLOW_REAL_PUBLISHING"] = False
        disabled = self.client.post(
            f"/api/publish/jobs/{job['id']}/execute-real",
            json=self._execute_payload(job),
        )
        app.config["ALLOW_REAL_PUBLISHING"] = True
        app.config["DEMO_MODE"] = True
        demo_mode = self.client.post(
            f"/api/publish/jobs/{job['id']}/execute-real",
            json=self._execute_payload(job),
        )
        app.config["DEMO_MODE"] = False
        unconfirmed = self.client.post(
            f"/api/publish/jobs/{job['id']}/execute-real",
            json={},
        )

        self.assertEqual(disabled.status_code, 403)
        self.assertEqual(demo_mode.status_code, 403)
        self.assertEqual(unconfirmed.status_code, 400)
        self.assertEqual(get_publish_job(self.db_path, job["id"])["status"], "queued")

    def test_real_api_rejects_unreviewed_draft_and_stops_changed_queued_task(self):
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.execute("UPDATE articles SET status='draft' WHERE id=?", (self.article_id,))
        response = self.client.post('/api/publish', json={'article_id': self.article_id, 'platform': 'zhihu',
            'expected_review_fingerprint': self._review_fingerprint()})
        self.assertEqual(response.status_code, 400)
        self.assertIn('审稿', response.json['msg'])
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.execute("UPDATE articles SET status='ready' WHERE id=?", (self.article_id,))
        job = self._create_job()
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.execute("UPDATE articles SET content='尚未重新确认的正文' WHERE id=?", (self.article_id,))
        publisher_factory = Mock()
        with patch('sau_backend.create_real_publisher_factory', return_value=publisher_factory):
            result = self.client.post(f"/api/publish/jobs/{job['id']}/execute-real", json=self._execute_payload(job))
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json['data']['status'], 'need_action')
        self.assertIn('重新审稿', result.json['data']['message'])
        publisher_factory.assert_not_called()

    def test_rejects_demo_real_job_without_execution(self):
        demo_job = self._create_job(demo=True)

        with patch("sau_backend.execute_publish_job") as execute:
            demo_response = self.client.post(
                f"/api/publish/jobs/{demo_job['id']}/execute-real",
                json=self._execute_payload(demo_job),
            )

        self.assertEqual(demo_response.status_code, 409)
        execute.assert_not_called()

    def test_executes_confirmed_zhihu_job_with_explicit_factory(self):
        job = self._create_job()
        completed = {
            **job,
            "status": "success",
            "message": "知乎发布成功",
            "result_url": "https://www.zhihu.com/p/123",
            "started_at": "2026-08-31 14:00:00",
            "finished_at": "2026-08-31 14:01:00",
        }
        factory = Mock(name="real_publisher_factory")

        with patch(
            "sau_backend.create_real_publisher_factory",
            return_value=factory,
        ) as create_factory, patch(
            "sau_backend.execute_publish_job",
            return_value=completed,
        ) as execute:
            response = self.client.post(
                f"/api/publish/jobs/{job['id']}/execute-real",
                json=self._execute_payload(job),
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["data"]["status"], "success")
        create_factory.assert_called_once()
        execute.assert_called_once_with(
            self.db_path,
            job["id"],
            publisher_factory=factory,
            media_root=app.config["MEDIA_ROOT"],
            expected_confirmation_snapshot=job['confirmation_snapshot'],
        )

    def test_executes_confirmed_toutiao_job_with_explicit_factory(self):
        job = self._create_job(platform="toutiao")
        processing = {
            **job,
            "status": "processing",
            "message": "今日头条已接受文章提交，等待平台审核",
            "started_at": "2026-09-01 10:00:00",
        }
        factory = Mock(name="real_publisher_factory")

        with patch(
            "sau_backend.create_real_publisher_factory",
            return_value=factory,
        ), patch(
            "sau_backend.execute_publish_job",
            return_value=processing,
        ) as execute:
            response = self.client.post(
                f"/api/publish/jobs/{job['id']}/execute-real",
                json=self._execute_payload(job),
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["data"]["status"], "processing")
        execute.assert_called_once_with(
            self.db_path,
            job["id"],
            publisher_factory=factory,
            media_root=app.config["MEDIA_ROOT"],
            expected_confirmation_snapshot=job['confirmation_snapshot'],
        )

    def test_desktop_toutiao_reports_manual_progress_through_existing_job(self):
        from services.publish_job_service import claim_publish_job
        job = self._create_job(platform='toutiao')
        def execute(_database, identifier, **_kwargs):
            claim_publish_job(self.db_path, identifier)
            create_factory.call_args.kwargs['publish_progress']('请在当前发布窗口完成正常验证')
            create_factory.call_args.kwargs['submission_recorder']({'submission_phase': 'initial_click_attempted', 'submission_attempted': True})
            return get_publish_job(self.db_path, identifier)
        with patch.dict(app.config, {'DESKTOP_MODE': True}), \
                patch('sau_backend.create_real_publisher_factory', return_value=Mock()) as create_factory, \
                patch('sau_backend.execute_publish_job', side_effect=execute):
            response = self.client.post(f"/api/publish/jobs/{job['id']}/execute-real", json=self._execute_payload(job))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(create_factory.call_args.kwargs['toutiao_interactive'])
        self.assertEqual(response.json['data']['status'], 'processing')
        self.assertIn('当前发布窗口', get_publish_job(self.db_path, job['id'])['message'])
        evidence = json.loads((self.db_path.parent / 'publish-evidence' / f"job-{job['id']}.json").read_text(encoding='utf-8'))
        self.assertEqual(evidence['submission_phase'], 'initial_click_attempted')
        self.assertTrue(evidence['submission_attempted'])
        self.assertEqual(evidence['article_id'], job['article_id'])
        self.assertEqual(evidence['account_id'], job['account_id'])
        self.assertEqual(evidence['authorization_fingerprint'], job['authorization_fingerprint'])
        self.assertTrue(evidence['started_at'])
        history = list((self.db_path.parent / 'publish-evidence' / 'history' / f"job-{job['id']}" / 'phases').glob('*.json'))
        self.assertEqual(len(history), 1)
        self.assertEqual(json.loads(history[0].read_text(encoding='utf-8')), evidence)

    def test_job_payload_exposes_real_action_only_when_all_server_gates_allow_it(self):
        job = self._create_job()

        allowed = self.client.get(f"/api/publish/jobs/{job['id']}").get_json()["data"]
        app.config["ALLOW_REAL_PUBLISHING"] = False
        blocked = self.client.get(f"/api/publish/jobs/{job['id']}").get_json()["data"]

        self.assertTrue(allowed["can_execute_real"])
        self.assertFalse(blocked["can_execute_real"])

    def test_toutiao_job_payload_exposes_real_action(self):
        job = self._create_job(platform="toutiao")

        payload = self.client.get(
            f"/api/publish/jobs/{job['id']}"
        ).get_json()["data"]

        self.assertTrue(payload["can_execute_real"])

    def test_public_api_rejects_unattended_real_scheduled_execution(self):
        payload = {
            "article_id": self.article_id,
            "platform": "zhihu",
            "publish_at": "2026-09-03T09:30:00+08:00",
            "auto_execute": True,
            "expected_review_fingerprint": self._review_fingerprint(),
        }
        app.config["ALLOW_REAL_PUBLISHING"] = False
        blocked = self.client.post("/api/publish", json=payload)
        app.config["ALLOW_REAL_PUBLISHING"] = True
        created = self.client.post("/api/publish", json=payload)
        immediate = self.client.post(
            "/api/publish",
            json={
                "article_id": self.article_id,
                "platform": "zhihu",
                "auto_execute": True,
                "expected_review_fingerprint": self._review_fingerprint(),
            },
        )
        malformed = self.client.post(
            "/api/publish",
            json={
                "article_id": self.article_id,
                "platform": "zhihu",
                "publish_at": "2026-09-03T09:30:00+08:00",
                "auto_execute": "yes",
                "expected_review_fingerprint": self._review_fingerprint(),
            },
        )

        self.assertEqual(blocked.status_code, 409)
        self.assertEqual(created.status_code, 409)
        self.assertEqual(
            created.get_json()["data"]["reason_code"],
            "auto_execute_not_allowed",
        )
        self.assertEqual(immediate.status_code, 400)
        self.assertEqual(malformed.status_code, 400)

    def test_sohu_job_payload_exposes_real_action(self):
        job = self._create_job(platform="sohu")

        payload = self.client.get(
            f"/api/publish/jobs/{job['id']}"
        ).get_json()["data"]

        self.assertTrue(payload["can_execute_real"])

    def test_baijiahao_and_xiaohongshu_payloads_expose_real_action(self):
        for platform in ("baijiahao", "xiaohongshu"):
            with self.subTest(platform=platform):
                job = self._create_job(platform=platform)
                payload = self.client.get(
                    f"/api/publish/jobs/{job['id']}"
                ).get_json()["data"]
                self.assertTrue(payload["can_execute_real"])


if __name__ == "__main__":
    unittest.main()
