import tempfile
import unittest
import sqlite3
from contextlib import closing
from pathlib import Path

from db.createTable import initialize_database
from sau_backend import app
from services.article_service import get_article
from services.geo_automation_service import (
    checkpoint_geo_automation_item,
    geo_automation_project_snapshot,
    get_geo_automation_run,
)
from services.project_service import create_project
from services.publish_job_service import claim_publish_job, transition_publish_job


class GEOAutomationApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "database.db"
        initialize_database(self.db_path)
        self.original_config = {
            key: app.config.get(key)
            for key in (
                "DATABASE_PATH",
                "DEMO_MODE",
                "ALLOW_REAL_PUBLISHING",
                "REAL_PUBLISHING_AUTHORIZED",
                "PUBLISHING_RUNTIME_READY",
            )
        }
        app.config.update(
            DATABASE_PATH=self.db_path,
            DEMO_MODE=False,
            ALLOW_REAL_PUBLISHING=False,
            REAL_PUBLISHING_AUTHORIZED=False,
            PUBLISHING_RUNTIME_READY=False,
            TESTING=True,
        )
        self.project = create_project(
            self.db_path,
            name="XX科技",
            website="https://example.test",
            product="企业 AI Agent",
            industry="人工智能",
            description="提供企业智能体平台。",
            keywords=["AI Agent"],
            competitors=[],
        )
        self.client = app.test_client()

    def tearDown(self):
        app.config.update(self.original_config)
        self.temp_dir.cleanup()

    def _payload(self, *, publish_mode="prepare"):
        channels = [{"platform": "zhihu", "account_id": None}]
        config = {
            "name": "一键 GEO API 测试",
            "topics": ["AI Agent 如何落地", "AI Agent 的成本边界"],
            "keywords": ["AI Agent"],
            "optimization_direction": "回答成本、边界与实施风险。",
            "target_length": 1000,
            "content_type": "行业科普",
            "target_platform": "zhihu",
            "channels": channels,
            "publish_mode": publish_mode,
            "optimize": True,
            "continue_on_error": True,
        }
        return {
            "project_id": self.project["id"],
            "config": config,
            "authorization": {
                "confirmed": True,
                "project_id": self.project["id"],
                "topic_count": 2,
                "publish_mode": publish_mode,
                "channels": channels,
                "project_snapshot": geo_automation_project_snapshot(self.project),
            },
        }

    def test_creates_lists_reads_and_checkpoints_run(self):
        created = self.client.post(
            "/api/geo/automation-runs",
            json=self._payload(),
            headers={
                "X-Request-ID": "geo-api-request-1",
                "Idempotency-Key": "geo-api-key-1",
            },
        )
        self.assertEqual(created.status_code, 201)
        run = created.get_json()["data"]
        self.assertEqual(run["status"], "queued")
        self.assertEqual(len(run["items"]), 2)
        self.assertEqual(created.headers["Idempotency-Replayed"], "false")

        replayed = self.client.post(
            "/api/geo/automation-runs",
            json=self._payload(),
            headers={
                "X-Request-ID": "geo-api-request-1",
                "Idempotency-Key": "geo-api-key-1",
            },
        )
        self.assertEqual(replayed.status_code, 200)
        self.assertEqual(replayed.headers["Idempotency-Replayed"], "true")
        self.assertEqual(replayed.get_json()["data"]["id"], run["id"])

        item = run["items"][0]
        checkpoint = self.client.post(
            f"/api/geo/automation-runs/{run['id']}/items/{item['id']}/checkpoint",
            json={
                "expected_state_version": item["state_version"],
                "status": "generating",
                "stage": "generating",
                "message": "正在生成",
                "generation_request_id": "geo-api-generation-1",
            },
        )
        self.assertEqual(checkpoint.status_code, 200)
        self.assertEqual(checkpoint.get_json()["data"]["status"], "running")

        detail = self.client.get(f"/api/geo/automation-runs/{run['id']}")
        listing = self.client.get(
            f"/api/geo/automation-runs?project_id={self.project['id']}"
        )
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(listing.get_json()["data"][0]["id"], run["id"])

    def test_rejects_stale_authorization_snapshot(self):
        payload = self._payload()
        payload["authorization"]["topic_count"] = 1
        response = self.client.post("/api/geo/automation-runs", json=payload)
        self.assertEqual(response.status_code, 409)
        self.assertIn("执行范围已变化", response.get_json()["msg"])

    def test_rejects_project_changed_after_operator_confirmation(self):
        payload = self._payload()
        with app.app_context():
            from services.project_service import update_project

            update_project(
                self.db_path,
                self.project["id"],
                {"description": "确认后由另一窗口修改的项目事实"},
            )
        response = self.client.post("/api/geo/automation-runs", json=payload)
        self.assertEqual(response.status_code, 409)
        self.assertIn("重新核对", response.get_json()["msg"])

    def test_exact_replay_ignores_later_project_and_runtime_drift(self):
        app.config["DEMO_MODE"] = True
        payload = self._payload(publish_mode="demo")
        headers = {
            "X-Request-ID": "geo-api-frozen-replay-request",
            "Idempotency-Key": "geo-api-frozen-replay-key",
        }
        created = self.client.post(
            "/api/geo/automation-runs", json=payload, headers=headers
        )
        self.assertEqual(created.status_code, 201)
        run_id = created.get_json()["data"]["id"]
        with app.app_context():
            from services.project_service import update_project

            update_project(
                self.db_path,
                self.project["id"],
                {"description": "运行已经提交后发生变化"},
            )
        app.config["DEMO_MODE"] = False
        replayed = self.client.post(
            "/api/geo/automation-runs", json=payload, headers=headers
        )
        self.assertEqual(replayed.status_code, 200)
        self.assertEqual(replayed.headers["Idempotency-Replayed"], "true")
        self.assertEqual(replayed.get_json()["data"]["id"], run_id)

    def test_request_and_idempotency_key_cannot_be_rebound_as_aliases(self):
        payload = self._payload()
        first = self.client.post(
            "/api/geo/automation-runs",
            json=payload,
            headers={
                "X-Request-ID": "geo-api-bound-request",
                "Idempotency-Key": "geo-api-bound-key",
            },
        )
        self.assertEqual(first.status_code, 201)
        rebound = self.client.post(
            "/api/geo/automation-runs",
            json=payload,
            headers={
                "X-Request-ID": "geo-api-bound-request",
                "Idempotency-Key": "geo-api-unbound-alias",
            },
        )
        self.assertEqual(rebound.status_code, 409)
        self.assertIn("其他 idempotency_key", rebound.get_json()["msg"])

    def test_publish_api_exact_replay_survives_completed_item_and_runtime_drift(self):
        app.config["DEMO_MODE"] = True
        run = self.client.post(
            "/api/geo/automation-runs",
            json=self._payload(publish_mode="demo"),
            headers={
                "X-Request-ID": "geo-publish-replay-run",
                "Idempotency-Key": "geo-publish-replay-run-key",
            },
        ).get_json()["data"]
        with closing(sqlite3.connect(self.db_path)) as conn:
            with conn:
                article_id = conn.execute(
                    """
                    INSERT INTO articles (project_id, title, content, status)
                    VALUES (?, ?, ?, 'ready')
                    """,
                    (self.project["id"], "AI Agent 成本指南", "正文"),
                ).lastrowid
        for status, stage in (
            ("generating", "generating"),
            ("optimizing", "optimizing"),
            ("preflighting", "preflighting"),
            ("publishing", "publishing"),
        ):
            item = get_geo_automation_run(self.db_path, run["id"])["items"][0]
            run = checkpoint_geo_automation_item(
                self.db_path,
                run_id=run["id"],
                item_id=item["id"],
                expected_state_version=item["state_version"],
                status=status,
                stage=stage,
                article_id=article_id if status != "generating" else None,
                preflight_article_fingerprint=(
                    get_article(self.db_path, article_id)["content_fingerprint"]
                    if status == "publishing"
                    else None
                ),
            )
        item = run["items"][0]
        request_payload = {
            "article_id": article_id,
            "automation_item_id": item["id"],
            "idempotency_key": "geo-publish-replay-job-key",
            "platform": "zhihu",
            "account_id": None,
            "images": [],
            "video": "",
            "auto_image": True,
            "auto_execute": False,
        }
        created = self.client.post(
            "/api/publish",
            json=request_payload,
            headers={"Idempotency-Key": "geo-publish-replay-job-key"},
        )
        self.assertEqual(created.status_code, 201)
        job_id = created.get_json()["data"]["job_id"]
        claimed = claim_publish_job(self.db_path, job_id)
        transition_publish_job(
            self.db_path,
            job_id,
            "success",
            expected_state_version=claimed["state_version"],
        )
        item = get_geo_automation_run(self.db_path, run["id"])["items"][0]
        checkpoint_geo_automation_item(
            self.db_path,
            run_id=run["id"],
            item_id=item["id"],
            expected_state_version=item["state_version"],
            status="completed",
            stage="publishing",
            article_id=article_id,
            publish_jobs=[
                {"platform": "zhihu", "job_id": job_id, "status": "success"}
            ],
        )
        with closing(sqlite3.connect(self.db_path)) as conn:
            with conn:
                conn.executemany(
                    """
                    INSERT INTO publish_jobs (article_id, platform, status)
                    VALUES (?, 'zhihu', 'queued')
                    """,
                    [(article_id,)] * 101,
                )
        unfiltered = self.client.get(
            f"/api/publish/jobs?article_id={article_id}&page_size=100"
        ).get_json()["data"]
        self.assertNotIn(
            job_id,
            [job["job_id"] for job in unfiltered["items"]],
        )
        filtered = self.client.get(
            f"/api/publish/jobs?automation_item_id={item['id']}&page_size=100"
        ).get_json()["data"]
        self.assertEqual(filtered["pagination"]["total"], 1)
        self.assertEqual(filtered["items"][0]["job_id"], job_id)
        self.assertEqual(filtered["items"][0]["automation_item_id"], item["id"])

        app.config.update(
            DEMO_MODE=False,
            ALLOW_REAL_PUBLISHING=False,
            REAL_PUBLISHING_AUTHORIZED=False,
            PUBLISHING_RUNTIME_READY=False,
        )
        replayed = self.client.post(
            "/api/publish",
            json=request_payload,
            headers={"Idempotency-Key": "geo-publish-replay-job-key"},
        )
        self.assertEqual(replayed.status_code, 200)
        self.assertEqual(replayed.headers["Idempotency-Replayed"], "true")
        self.assertEqual(replayed.get_json()["data"]["job_id"], job_id)

    def test_real_mode_fails_closed_before_any_run_is_written(self):
        payload = self._payload(publish_mode="real")
        payload["config"]["channels"][0]["account_id"] = 7
        payload["config"]["channels"][0]["credential_epoch"] = 1
        payload["authorization"]["channels"][0]["account_id"] = 7
        payload["authorization"]["channels"][0]["credential_epoch"] = 1
        response = self.client.post("/api/geo/automation-runs", json=payload)
        self.assertEqual(response.status_code, 403)
        listing = self.client.get("/api/geo/automation-runs").get_json()["data"]
        self.assertEqual(listing, [])

    def test_real_run_freezes_account_epoch_and_replay_ignores_later_relogin(self):
        with closing(sqlite3.connect(self.db_path)) as conn:
            with conn:
                account_id = conn.execute(
                    """
                    INSERT INTO user_info (type, filePath, userName, status)
                    VALUES (9, 'zhihu.json', '知乎账号', 1)
                    """
                ).lastrowid
        app.config.update(
            DEMO_MODE=False,
            ALLOW_REAL_PUBLISHING=True,
            REAL_PUBLISHING_AUTHORIZED=True,
            PUBLISHING_RUNTIME_READY=True,
        )
        payload = self._payload(publish_mode="real")
        channel = {
            "platform": "zhihu",
            "account_id": account_id,
            "credential_epoch": 1,
        }
        payload["config"]["channels"] = [channel]
        payload["authorization"]["channels"] = [channel]
        headers = {
            "X-Request-ID": "geo-api-real-epoch-request",
            "Idempotency-Key": "geo-api-real-epoch-key",
        }
        created = self.client.post(
            "/api/geo/automation-runs",
            json=payload,
            headers=headers,
        )
        self.assertEqual(created.status_code, 201)
        with closing(sqlite3.connect(self.db_path)) as conn:
            with conn:
                conn.execute(
                    "UPDATE user_info SET credential_epoch = 2 WHERE id = ?",
                    (account_id,),
                )
        replayed = self.client.post(
            "/api/geo/automation-runs",
            json=payload,
            headers=headers,
        )
        self.assertEqual(replayed.status_code, 200)
        self.assertEqual(replayed.headers["Idempotency-Replayed"], "true")
        blocked = self.client.post(
            "/api/geo/automation-runs",
            json=payload,
            headers={
                "X-Request-ID": "geo-api-real-epoch-request-2",
                "Idempotency-Key": "geo-api-real-epoch-key-2",
            },
        )
        self.assertEqual(blocked.status_code, 409)
        self.assertEqual(
            blocked.get_json()["data"]["error_code"],
            "automation_account_credential_changed",
        )

    def test_pause_requires_current_run_version(self):
        run = self.client.post(
            "/api/geo/automation-runs",
            json=self._payload(),
        ).get_json()["data"]
        paused = self.client.post(
            f"/api/geo/automation-runs/{run['id']}/pause",
            json={"paused": True, "expected_state_version": run["state_version"]},
        )
        self.assertEqual(paused.status_code, 200)
        self.assertTrue(paused.get_json()["data"]["pause_requested"])
        stale = self.client.post(
            f"/api/geo/automation-runs/{run['id']}/pause",
            json={"paused": False, "expected_state_version": run["state_version"]},
        )
        self.assertEqual(stale.status_code, 409)

    def test_worker_lease_prevents_a_second_page_from_owning_model_stage(self):
        run = self.client.post(
            "/api/geo/automation-runs",
            json=self._payload(),
        ).get_json()["data"]
        item = run["items"][0]
        checkpoint = self.client.post(
            f"/api/geo/automation-runs/{run['id']}/items/{item['id']}/checkpoint",
            json={
                "expected_state_version": item["state_version"],
                "status": "generating",
                "stage": "generating",
                "generation_request_id": "api-leased-generation",
            },
        )
        self.assertEqual(checkpoint.status_code, 200)
        run = checkpoint.get_json()["data"]
        item = run["items"][0]
        first = self.client.post(
            f"/api/geo/automation-runs/{run['id']}/items/{item['id']}/lease",
            json={
                "expected_state_version": item["state_version"],
                "worker_id": "api-worker-a",
                "stage": "generating",
            },
        )
        self.assertEqual(first.status_code, 200)
        second = self.client.post(
            f"/api/geo/automation-runs/{run['id']}/items/{item['id']}/lease",
            json={
                "expected_state_version": item["state_version"],
                "worker_id": "api-worker-b",
                "stage": "generating",
            },
        )
        self.assertEqual(second.status_code, 409)
        self.assertEqual(
            second.get_json()["data"]["error_code"], "automation_item_busy"
        )


if __name__ == "__main__":
    unittest.main()
