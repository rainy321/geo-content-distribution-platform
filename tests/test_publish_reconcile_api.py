import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from db.createTable import initialize_database
from sau_backend import app
from services.publish_job_service import (
    create_publish_job,
    get_publish_job,
    mark_publish_job_awaiting_reconciliation,
    transition_publish_job,
)


class PublishReconcileApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "publish-reconcile.db"
        initialize_database(self.db_path)
        with closing(sqlite3.connect(self.db_path)) as conn:
            with conn:
                project_id = conn.execute(
                    "INSERT INTO projects (name) VALUES ('人工对账测试')"
                ).lastrowid
                self.article_id = conn.execute(
                    """
                    INSERT INTO articles (project_id, title, content, status)
                    VALUES (?, '合成对账文章', '不发送到任何平台', 'ready')
                    """,
                    (project_id,),
                ).lastrowid
                self.account_id = conn.execute(
                    """
                    INSERT INTO user_info (type, filePath, userName, status)
                    VALUES (9, 'synthetic.json', 'synthetic-account', 1)
                    """
                ).lastrowid
        self.original_database = app.config.get("DATABASE_PATH")
        app.config.update(DATABASE_PATH=self.db_path, TESTING=True)
        self.client = app.test_client()

    def tearDown(self):
        app.config["DATABASE_PATH"] = self.original_database
        self.temp_dir.cleanup()

    def _job(self, status="awaiting_reconcile"):
        # Each synthetic case represents an independently prepared dispatch.
        with closing(sqlite3.connect(self.db_path)) as conn:
            with conn:
                conn.execute(
                    "UPDATE articles SET status = 'ready' WHERE id = ?",
                    (self.article_id,),
                )
        job = create_publish_job(
            self.db_path,
            article_id=self.article_id,
            platform="zhihu",
            account_id=self.account_id,
        )
        if status != "queued":
            job = transition_publish_job(
                self.db_path,
                job["id"],
                "processing",
                message="平台结果待确认",
                expected_state_version=job["state_version"],
            )
        if status == "awaiting_reconcile":
            job = mark_publish_job_awaiting_reconciliation(
                self.db_path,
                job["id"],
                message="平台提交结果待确认",
                expected_state_version=job["state_version"],
            )
        elif status == "need_action":
            job = transition_publish_job(
                self.db_path,
                job["id"],
                "need_action",
                message="需要人工验证后核对后台",
                expected_state_version=job["state_version"],
            )
        return job

    @staticmethod
    def _payload(job, *, decision="published", **overrides):
        return {
            "confirm": True,
            "platform_checked": True,
            "job_id": job["id"],
            "state_version": job["state_version"],
            "decision": decision,
            "execution_stopped_confirmed": decision == "not_published",
            "verification_note": "已在平台内容管理页按精确标题核验",
            "result_url": "",
            **overrides,
        }

    def test_requires_explicit_platform_check_before_mutation(self):
        job = self._job()
        payload = self._payload(job)
        payload["platform_checked"] = False

        response = self.client.post(
            f"/api/publish/jobs/{job['id']}/reconcile", json=payload
        )

        self.assertEqual(response.status_code, 400)
        current = get_publish_job(self.db_path, job["id"])
        self.assertEqual(current["status"], "need_action")
        self.assertTrue(current["reconciliation_required"])

    def test_reconciles_finished_ambiguous_attempt_without_calling_publisher(self):
        job = self._job()
        result_url = "https://www.zhihu.com/p/123456"
        with patch("sau_backend.create_real_publisher_factory") as factory, patch(
            "sau_backend.execute_publish_job"
        ) as execute:
            response = self.client.post(
                f"/api/publish/jobs/{job['id']}/reconcile",
                json=self._payload(job, result_url=result_url),
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()["data"]
        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["url"], result_url)
        self.assertIn("人工核验已发布", payload["message"])
        factory.assert_not_called()
        execute.assert_not_called()

    def test_rejects_non_ambiguous_need_action(self):
        job = self._job("need_action")

        response = self.client.post(
            f"/api/publish/jobs/{job['id']}/reconcile",
            json=self._payload(job),
        )

        self.assertEqual(response.status_code, 409)
        current = get_publish_job(self.db_path, job["id"])
        self.assertEqual(current["status"], "need_action")
        self.assertFalse(current["reconciliation_required"])

    def test_rejects_live_processing_attempt_and_retry(self):
        job = self._job("processing")

        reconciled = self.client.post(
            f"/api/publish/jobs/{job['id']}/reconcile",
            json=self._payload(job),
        )
        retried = self.client.post(f"/api/publish/jobs/{job['id']}/retry")

        self.assertEqual(reconciled.status_code, 409)
        self.assertEqual(retried.status_code, 409)
        self.assertEqual(get_publish_job(self.db_path, job["id"])["status"], "processing")

    def test_reconciles_confirmed_not_published_as_failed_then_allows_retry(self):
        job = self._job()

        response = self.client.post(
            f"/api/publish/jobs/{job['id']}/reconcile",
            json=self._payload(
                job,
                decision="not_published",
                verification_note="平台全部状态与草稿箱均无此标题",
            ),
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()["data"]
        self.assertEqual(payload["status"], "failed")
        self.assertIn("人工核验未发布", payload["message"])
        retried = self.client.post(f"/api/publish/jobs/{job['id']}/retry")
        self.assertEqual(retried.status_code, 200)
        self.assertEqual(retried.get_json()["data"]["status"], "queued")

    def test_not_published_requires_stopped_process_and_cooldown_confirmation(self):
        job = self._job()

        response = self.client.post(
            f"/api/publish/jobs/{job['id']}/reconcile",
            json=self._payload(
                job,
                decision="not_published",
                execution_stopped_confirmed=False,
            ),
        )

        self.assertEqual(response.status_code, 400)
        current = get_publish_job(self.db_path, job["id"])
        self.assertEqual(current["status"], "need_action")
        self.assertTrue(current["reconciliation_required"])

    def test_rejects_stale_or_non_ambiguous_reconciliation(self):
        stale = self._job()
        stale_payload = self._payload(stale, state_version=stale["state_version"] - 1)
        stale_response = self.client.post(
            f"/api/publish/jobs/{stale['id']}/reconcile", json=stale_payload
        )
        queued = self._job("queued")
        queued_response = self.client.post(
            f"/api/publish/jobs/{queued['id']}/reconcile",
            json=self._payload(queued),
        )

        self.assertEqual(stale_response.status_code, 409)
        self.assertEqual(queued_response.status_code, 409)
        self.assertEqual(get_publish_job(self.db_path, stale["id"])["status"], "need_action")
        self.assertEqual(get_publish_job(self.db_path, queued["id"])["status"], "queued")

    def test_validates_url_and_disallows_url_for_not_published(self):
        invalid_url_job = self._job()
        invalid_url = self.client.post(
            f"/api/publish/jobs/{invalid_url_job['id']}/reconcile",
            json=self._payload(invalid_url_job, result_url="javascript:alert(1)"),
        )
        not_published_job = self._job()
        impossible = self.client.post(
            f"/api/publish/jobs/{not_published_job['id']}/reconcile",
            json=self._payload(
                not_published_job,
                decision="not_published",
                result_url="https://example.com/should-not-exist",
            ),
        )

        self.assertEqual(invalid_url.status_code, 400)
        self.assertEqual(impossible.status_code, 400)


if __name__ == "__main__":
    unittest.main()
