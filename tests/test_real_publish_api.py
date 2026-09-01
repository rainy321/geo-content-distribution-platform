import sqlite3
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
            for key in ("DATABASE_PATH", "DEMO_MODE", "ALLOW_REAL_PUBLISHING")
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

    def test_requires_all_feature_and_request_confirmation_gates(self):
        job = self._create_job()

        app.config["ALLOW_REAL_PUBLISHING"] = False
        disabled = self.client.post(
            f"/api/publish/jobs/{job['id']}/execute-real",
            json={"confirm": True},
        )
        app.config["ALLOW_REAL_PUBLISHING"] = True
        app.config["DEMO_MODE"] = True
        demo_mode = self.client.post(
            f"/api/publish/jobs/{job['id']}/execute-real",
            json={"confirm": True},
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

    def test_rejects_demo_real_job_without_execution(self):
        demo_job = self._create_job(demo=True)

        with patch("sau_backend.execute_publish_job") as execute:
            demo_response = self.client.post(
                f"/api/publish/jobs/{demo_job['id']}/execute-real",
                json={"confirm": True},
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
                json={"confirm": True},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["data"]["status"], "success")
        create_factory.assert_called_once()
        execute.assert_called_once_with(
            self.db_path,
            job["id"],
            publisher_factory=factory,
            media_root=app.config["MEDIA_ROOT"],
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
                json={"confirm": True},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["data"]["status"], "processing")
        execute.assert_called_once_with(
            self.db_path,
            job["id"],
            publisher_factory=factory,
            media_root=app.config["MEDIA_ROOT"],
        )

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
