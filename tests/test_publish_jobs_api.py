import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from db.createTable import initialize_database
from sau_backend import app
from services.publish_job_service import transition_publish_job


class PublishJobsApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "database.db"
        initialize_database(self.db_path)
        with closing(sqlite3.connect(self.db_path)) as conn:
            with conn:
                project_id = conn.execute(
                    "INSERT INTO projects (name) VALUES (?)",
                    ("XX科技",),
                ).lastrowid
                self.article_id = conn.execute(
                    """
                    INSERT INTO articles (project_id, title, content, tags, status)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (project_id, "AI Agent 指南", "文章正文", '["AI"]', "ready"),
                ).lastrowid

        self.original_database_path = app.config["DATABASE_PATH"]
        self.original_demo_mode = app.config["DEMO_MODE"]
        self.original_testing = app.testing
        app.config["DATABASE_PATH"] = self.db_path
        app.config["DEMO_MODE"] = True
        app.testing = True
        self.client = app.test_client()

    def tearDown(self):
        app.config["DATABASE_PATH"] = self.original_database_path
        app.config["DEMO_MODE"] = self.original_demo_mode
        app.testing = self.original_testing
        self.temp_dir.cleanup()

    def test_creates_and_reads_demo_job_from_server_configuration(self):
        response = self.client.post(
            "/api/publish",
            json={"article_id": self.article_id, "platform": " ZHIHU ", "demo": False},
        )

        self.assertEqual(response.status_code, 201)
        job = response.get_json()["data"]
        self.assertEqual(job["status"], "queued")
        self.assertEqual(job["platform"], "zhihu")
        self.assertTrue(job["demo"])
        self.assertNotIn("id", job)
        self.assertIn("job_id", job)

        detail_response = self.client.get(f"/api/publish/jobs/{job['job_id']}")
        self.assertEqual(detail_response.status_code, 200)
        self.assertEqual(detail_response.get_json()["data"], job)

    def test_executes_demo_job_through_full_state_flow(self):
        job = self._create_job()

        response = self.client.post(f"/api/publish/jobs/{job['job_id']}/execute")

        self.assertEqual(response.status_code, 200)
        result = response.get_json()["data"]
        self.assertEqual(result["status"], "success")
        self.assertTrue(result["demo"])
        self.assertIn("未访问真实平台", result["message"])
        self.assertTrue(result["started_at"])
        self.assertTrue(result["finished_at"])

    def test_lists_jobs_with_filters_and_pagination(self):
        zhihu = self._create_job(platform="zhihu")
        self._create_job(platform="toutiao")

        response = self.client.get(
            f"/api/publish/jobs?article_id={self.article_id}"
            "&platform=zhihu&status=queued&page=1&page_size=1"
        )

        self.assertEqual(response.status_code, 200)
        result = response.get_json()["data"]
        self.assertEqual(result["pagination"]["total"], 1)
        self.assertEqual(result["items"][0]["job_id"], zhihu["job_id"])
        self.assertEqual(result["items"][0]["article_title"], "AI Agent 指南")

    def test_retries_failed_job(self):
        job = self._create_job()
        transition_publish_job(self.db_path, job["job_id"], "processing")
        transition_publish_job(
            self.db_path,
            job["job_id"],
            "failed",
            message="网络异常",
        )

        response = self.client.post(f"/api/publish/jobs/{job['job_id']}/retry")

        self.assertEqual(response.status_code, 200)
        retried = response.get_json()["data"]
        self.assertEqual(retried["status"], "queued")
        self.assertEqual(retried["message"], "")
        self.assertIsNone(retried["started_at"])

    def test_real_job_cannot_be_executed_by_safe_demo_endpoint(self):
        app.config["DEMO_MODE"] = False
        job = self._create_job()

        response = self.client.post(f"/api/publish/jobs/{job['job_id']}/execute")

        self.assertEqual(response.status_code, 409)
        self.assertIn("真实平台执行尚未开放", response.get_json()["msg"])
        detail = self.client.get(f"/api/publish/jobs/{job['job_id']}").get_json()["data"]
        self.assertEqual(detail["status"], "queued")
        self.assertFalse(detail["demo"])

    def test_scheduled_job_waits_instead_of_executing_early(self):
        response = self.client.post(
            "/api/publish",
            json={
                "article_id": self.article_id,
                "platform": "zhihu",
                "publish_at": "2026-09-02T09:30:00+08:00",
            },
        )
        job = response.get_json()["data"]

        execute_response = self.client.post(
            f"/api/publish/jobs/{job['job_id']}/execute"
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(job["status"], "scheduled")
        self.assertEqual(execute_response.status_code, 409)

    def test_validates_create_and_list_inputs(self):
        invalid_json = self.client.post(
            "/api/publish",
            data="not-json",
            content_type="application/json",
        )
        invalid_article = self.client.post(
            "/api/publish",
            json={"article_id": "abc", "platform": "zhihu"},
        )
        missing_article = self.client.post(
            "/api/publish",
            json={"article_id": 999999, "platform": "zhihu"},
        )
        invalid_platform = self.client.post(
            "/api/publish",
            json={"article_id": self.article_id, "platform": "unknown"},
        )
        invalid_time = self.client.post(
            "/api/publish",
            json={
                "article_id": self.article_id,
                "platform": "zhihu",
                "publish_at": "tomorrow",
            },
        )
        invalid_list = self.client.get("/api/publish/jobs?status=unknown")

        self.assertEqual(invalid_json.status_code, 400)
        self.assertEqual(invalid_article.status_code, 400)
        self.assertEqual(missing_article.status_code, 404)
        self.assertEqual(invalid_platform.status_code, 400)
        self.assertEqual(invalid_time.status_code, 400)
        self.assertEqual(invalid_list.status_code, 400)

    def test_unknown_and_non_retryable_jobs_return_domain_errors(self):
        job = self._create_job()

        missing_detail = self.client.get("/api/publish/jobs/999999")
        missing_retry = self.client.post("/api/publish/jobs/999999/retry")
        missing_execute = self.client.post("/api/publish/jobs/999999/execute")
        queued_retry = self.client.post(f"/api/publish/jobs/{job['job_id']}/retry")

        self.assertEqual(missing_detail.status_code, 404)
        self.assertEqual(missing_retry.status_code, 404)
        self.assertEqual(missing_execute.status_code, 404)
        self.assertEqual(queued_retry.status_code, 409)

    def _create_job(self, *, platform="zhihu"):
        response = self.client.post(
            "/api/publish",
            json={"article_id": self.article_id, "platform": platform},
        )
        self.assertEqual(response.status_code, 201)
        return response.get_json()["data"]


if __name__ == "__main__":
    unittest.main()
