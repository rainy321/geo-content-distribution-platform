import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from db.createTable import initialize_database
from sau_backend import app
from services.desktop_backup_service import create_verified_database_backup
from services.local_auth_service import LocalAuthService
from services.request_guard import FixedWindowRateLimiter


class DesktopProductFlowTests(unittest.TestCase):
    """Exercise the sellable local workflow with synthetic data only."""

    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.database_path = self.root / "data" / "geo.db"
        self.auth_path = self.root / "config" / "auth.json"
        self.backup_path = self.root / "backups"
        initialize_database(self.database_path)
        self.local_auth = LocalAuthService(self.auth_path, iterations=100_000)
        self.original_config = {
            key: app.config.get(key)
            for key in (
                "ACCESS_CONTROL_ENABLED",
                "ACCESS_PASSWORD",
                "ALLOW_REAL_PUBLISHING",
                "AUTH_LOGIN_ATTEMPTS_PER_MINUTE",
                "AUTH_RATE_LIMITER",
                "DATABASE_PATH",
                "DEMO_MODE",
                "DESKTOP_MODE",
                "ENABLE_BILIBILI_RUNTIME",
                "LOCAL_AUTH_SERVICE",
                "SECRET_KEY",
                "SESSION_COOKIE_SECURE",
                "TESTING",
            )
        }
        app.config.update(
            ACCESS_CONTROL_ENABLED=True,
            ACCESS_PASSWORD="",
            ALLOW_REAL_PUBLISHING=False,
            AUTH_LOGIN_ATTEMPTS_PER_MINUTE=10,
            AUTH_RATE_LIMITER=FixedWindowRateLimiter(),
            DATABASE_PATH=self.database_path,
            DEMO_MODE=False,
            DESKTOP_MODE=True,
            ENABLE_BILIBILI_RUNTIME=False,
            LOCAL_AUTH_SERVICE=self.local_auth,
            SECRET_KEY=self.local_auth.get_session_secret(),
            SESSION_COOKIE_SECURE=False,
            TESTING=True,
        )
        self.client = app.test_client()

    def tearDown(self):
        app.config.update(self.original_config)
        self.temporary_directory.cleanup()

    def test_first_run_analysis_history_password_and_backup(self):
        status = self.client.get("/api/auth/status")
        blocked = self.client.get("/api/projects")
        self.assertEqual(status.status_code, 200)
        self.assertTrue(status.get_json()["data"]["setup_required"])
        self.assertEqual(blocked.status_code, 401)

        setup = self.client.post(
            "/api/auth/setup",
            json={"password": "Synthetic-Desktop-2026!"},
        )
        self.assertEqual(setup.status_code, 200)

        created = self.client.post(
            "/api/projects",
            json={
                "name": "合成测试品牌",
                "website": "https://synthetic.example",
                "product": "合成可见度工具",
                "industry": "测试行业",
                "description": "只用于本机自动化验收，不对应任何客户。",
                "keywords": ["合成关键词"],
                "competitors": ["虚构竞品"],
            },
        )
        self.assertEqual(created.status_code, 201)
        project_id = created.get_json()["data"]["id"]

        analyzed = self.client.post(
            "/api/geo/visibility/analyze",
            json={
                "project_id": project_id,
                "prompt": "合成测试：有哪些虚构的内容可见度工具？",
                "responses": [
                    {
                        "text": (
                            "推荐合成测试品牌的合成可见度工具，详情见 "
                            "https://synthetic.example/guide。"
                        ),
                        "provider": "synthetic-fixture",
                        "model": "offline-v1",
                    },
                    {
                        "text": "虚构竞品排名第一，合成测试品牌排名第二。",
                        "provider": "synthetic-fixture",
                        "model": "offline-v1",
                    },
                ],
                "persist": True,
            },
        )
        self.assertEqual(analyzed.status_code, 200)
        result = analyzed.get_json()["data"]
        self.assertEqual(result["summary"]["sample_count"], 2)
        self.assertFalse(result["methodology"]["external_model_used"])
        self.assertIsInstance(result["run_id"], int)

        history = self.client.get(
            "/api/geo/visibility/runs",
            query_string={"project_id": project_id, "limit": 10},
        )
        detail = self.client.get(
            f"/api/geo/visibility/runs/{result['run_id']}"
        )
        self.assertEqual(history.status_code, 200)
        self.assertEqual(len(history.get_json()["data"]), 1)
        self.assertEqual(detail.status_code, 200)
        saved = detail.get_json()["data"]
        self.assertEqual(saved["result"]["project"]["name"], "合成测试品牌")
        self.assertEqual(
            saved["input_samples"][0]["provider"],
            "synthetic-fixture",
        )

        # The desktop hard gate must reject legacy publishing before payload
        # validation, imports, worker threads, or any platform I/O can occur.
        publish = self.client.post("/postVideo", json={"dryRun": False})
        self.assertEqual(publish.status_code, 403)
        self.assertIn("ALLOW_REAL_PUBLISHING", publish.get_json()["msg"])

        changed = self.client.post(
            "/api/auth/change-password",
            json={
                "current_password": "Synthetic-Desktop-2026!",
                "new_password": "Synthetic-Desktop-Next-2026!",
            },
        )
        self.assertEqual(changed.status_code, 200)
        self.assertEqual(self.client.post("/api/auth/logout").status_code, 200)
        self.assertEqual(
            self.client.post(
                "/api/auth/login",
                json={"password": "Synthetic-Desktop-2026!"},
            ).status_code,
            401,
        )
        self.assertEqual(
            self.client.post(
                "/api/auth/login",
                json={"password": "Synthetic-Desktop-Next-2026!"},
            ).status_code,
            200,
        )

        backup = create_verified_database_backup(
            self.database_path,
            self.backup_path,
        )
        self.assertEqual(backup.integrity_check, "ok")
        with closing(sqlite3.connect(backup.path)) as connection:
            counts = connection.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM projects),
                    (SELECT COUNT(*) FROM ai_visibility_runs)
                """
            ).fetchone()
        self.assertEqual(counts, (1, 1))
        persisted_auth = self.auth_path.read_text(encoding="utf-8")
        self.assertNotIn("Synthetic-Desktop", persisted_auth)
        self.assertIsInstance(json.loads(persisted_auth)["password"], dict)


if __name__ == "__main__":
    unittest.main()
