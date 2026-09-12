import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from db.createTable import initialize_database
from sau_backend import app
from services.media_account_risk_service import record_media_account_risk
from services.publish_job_service import (
    create_publish_job,
    mark_publish_job_awaiting_reconciliation,
    transition_publish_job,
)
from services.publish_review_monitor_service import (
    get_publish_review_overview,
    run_publish_review_monitor_tick,
)


class PublishReviewMonitorApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.db_path = self.root / "monitor-api.db"
        initialize_database(self.db_path)
        with closing(sqlite3.connect(self.db_path)) as conn:
            with conn:
                project_id = conn.execute(
                    "INSERT INTO projects (name) VALUES ('监测接口项目')"
                ).lastrowid
                article_id = conn.execute(
                    """
                    INSERT INTO articles (project_id, title, content, status)
                    VALUES (?, '监测接口精确标题', '不访问真实平台。', 'ready')
                    """,
                    (project_id,),
                ).lastrowid
                self.account_id = conn.execute(
                    """
                    INSERT INTO user_info (type, filePath, userName, status)
                    VALUES (9, 'test.json', '接口测试账号', 1)
                    """
                ).lastrowid
        job = create_publish_job(
            self.db_path,
            article_id=article_id,
            platform="zhihu",
            account_id=self.account_id,
        )
        job = transition_publish_job(
            self.db_path,
            job["id"],
            "processing",
            message="平台提交中",
            expected_state_version=job["state_version"],
        )
        self.job = mark_publish_job_awaiting_reconciliation(
            self.db_path,
            job["id"],
            message="平台处理中/审核中",
            expected_state_version=job["state_version"],
        )
        self.original_database = app.config.get("DATABASE_PATH")
        app.config.update(DATABASE_PATH=self.db_path, TESTING=True)
        self.client = app.test_client()

    def tearDown(self):
        app.config["DATABASE_PATH"] = self.original_database
        self.temp_dir.cleanup()

    def test_overview_is_local_only_and_job_payload_contains_monitor(self):
        with patch("sau_backend.create_real_publisher_factory") as factory, patch(
            "sau_backend.execute_publish_job"
        ) as execute:
            response = self.client.get(
                "/api/publish/review-monitor?unread_only=true&alert_limit=10"
            )
            jobs = self.client.get("/api/publish/jobs")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["data"]["summary"]["watching"], 1)
        payload = jobs.get_json()["data"]["items"][0]
        self.assertEqual(payload["review_monitor"]["state"], "watching")
        self.assertIsNone(payload["account_risk"])
        factory.assert_not_called()
        execute.assert_not_called()

    def test_global_risk_alert_and_job_badge_are_returned(self):
        risk = record_media_account_risk(
            self.db_path,
            self.account_id,
            source="publish",
            error_text="账号已被永久封禁",
        )

        overview = self.client.get(
            "/api/publish/review-monitor?unread_only=true"
        )
        jobs = self.client.get("/api/publish/jobs")

        self.assertEqual(overview.status_code, 200)
        alert = overview.get_json()["data"]["alerts"][0]
        self.assertEqual(alert["event_key"], f"account-risk:{risk['id']}")
        self.assertEqual(alert["severity"], "critical")
        job = jobs.get_json()["data"]["items"][0]
        self.assertEqual(job["account_risk"]["category"], "permanent_ban")

    def test_acknowledge_requires_explicit_confirmation(self):
        run_publish_review_monitor_tick(
            self.db_path,
            verifier=lambda _snapshot: {
                "state": "published",
                "result_url": "https://zhuanlan.zhihu.com/p/123456789",
            },
        )
        alert = get_publish_review_overview(
            self.db_path,
            unread_only=True,
        )["alerts"][0]

        refused = self.client.post(
            f"/api/publish/review-alerts/{alert['id']}/acknowledge",
            json={},
        )
        accepted = self.client.post(
            f"/api/publish/review-alerts/{alert['id']}/acknowledge",
            json={"confirm_seen": True},
        )

        self.assertEqual(refused.status_code, 400)
        self.assertEqual(accepted.status_code, 200)
        self.assertTrue(accepted.get_json()["data"]["acknowledged"])

    def test_rearm_is_local_only_and_requires_confirmation(self):
        run_publish_review_monitor_tick(
            self.db_path,
            verifier=lambda _snapshot: {"state": "unsupported"},
        )
        refused = self.client.post(
            f"/api/publish/jobs/{self.job['id']}/review-monitor/rearm",
            json={},
        )
        with patch("sau_backend.create_real_publisher_factory") as factory, patch(
            "sau_backend.execute_publish_job"
        ) as execute:
            accepted = self.client.post(
                f"/api/publish/jobs/{self.job['id']}/review-monitor/rearm",
                json={"confirm_read_only": True},
            )

        self.assertEqual(refused.status_code, 400)
        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(accepted.get_json()["data"]["state"], "watching")
        factory.assert_not_called()
        execute.assert_not_called()


if __name__ == "__main__":
    unittest.main()
