import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from db.createTable import initialize_database
from sau_backend import app
from services.geo_site_audit_service import (
    GEOSiteAuditConflictError,
    GEOSiteAuditError,
    GEOSiteAuditTimeoutError,
)
from services.request_guard import FixedWindowRateLimiter


class GEOSiteAuditApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "site-audit-api.db"
        initialize_database(self.database_path)
        with closing(sqlite3.connect(self.database_path)) as conn:
            with conn:
                cursor = conn.execute(
                    """
                    INSERT INTO projects (
                        name, website, product, industry, description,
                        keywords, competitors
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "API 示例品牌",
                        "https://brand.example/",
                        "示例产品",
                        "软件",
                        "仅用于 API 测试",
                        "[]",
                        json.dumps([], ensure_ascii=False),
                    ),
                )
                self.project_id = int(cursor.lastrowid)
        self.original_config = {
            "DATABASE_PATH": app.config.get("DATABASE_PATH"),
            "ACCESS_CONTROL_ENABLED": app.config.get("ACCESS_CONTROL_ENABLED"),
            "SITE_AUDIT_RATE_LIMIT_PER_MINUTE": app.config.get(
                "SITE_AUDIT_RATE_LIMIT_PER_MINUTE"
            ),
            "AI_RATE_LIMITER": app.config.get("AI_RATE_LIMITER"),
            "TESTING": app.config.get("TESTING"),
        }
        app.config.update(
            DATABASE_PATH=self.database_path,
            ACCESS_CONTROL_ENABLED=False,
            SITE_AUDIT_RATE_LIMIT_PER_MINUTE=0,
            TESTING=True,
        )
        self.client = app.test_client()

    def tearDown(self):
        app.config.update(self.original_config)
        self.temp_dir.cleanup()

    def payload(self, **overrides):
        value = {
            "project_id": self.project_id,
            "confirm_external_fetch": True,
            "persist": True,
        }
        value.update(overrides)
        return value

    def headers(self, **overrides):
        value = {
            "X-Request-ID": "api-site-audit-request-1",
            "Idempotency-Key": "api-site-audit-key-1",
        }
        value.update(overrides)
        return value

    @patch("sau_backend.run_geo_site_audit")
    def test_post_success_forwards_explicit_fetch_and_identity_contract(self, run_audit):
        expected = {
            "id": 7,
            "project_id": self.project_id,
            "status": "ready",
            "score": 96,
            "audit_version": "geo-site-audit-1.0.0",
        }
        run_audit.return_value = expected

        response = self.client.post(
            "/api/geo/site-audits",
            json=self.payload(),
            headers=self.headers(),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"code": 200, "msg": "站点技术审计完成", "data": expected})
        run_audit.assert_called_once_with(
            self.database_path,
            project_id=self.project_id,
            url=None,
            persist=True,
            confirm_external_fetch=True,
            request_id="api-site-audit-request-1",
            idempotency_key="api-site-audit-key-1",
            allow_proxy_fake_ip=False,
        )

    def test_post_rejects_invalid_body_missing_confirmation_and_missing_identity(self):
        invalid_body = self.client.post(
            "/api/geo/site-audits",
            data="not-json",
            content_type="text/plain",
            headers=self.headers(),
        )
        missing_confirmation = self.client.post(
            "/api/geo/site-audits",
            json={"project_id": self.project_id},
            headers=self.headers(),
        )
        missing_request_id = self.client.post(
            "/api/geo/site-audits",
            json=self.payload(),
            headers={"Idempotency-Key": "api-site-audit-key-1"},
        )
        missing_idempotency_key = self.client.post(
            "/api/geo/site-audits",
            json=self.payload(),
            headers={"X-Request-ID": "api-site-audit-request-1"},
        )

        self.assertEqual(invalid_body.status_code, 400)
        self.assertEqual(missing_confirmation.status_code, 400)
        self.assertIn("必须明确确认", missing_confirmation.get_json()["msg"])
        self.assertEqual(missing_request_id.status_code, 400)
        self.assertIn("request_id", missing_request_id.get_json()["msg"])
        self.assertEqual(missing_idempotency_key.status_code, 400)
        self.assertIn("idempotency_key", missing_idempotency_key.get_json()["msg"])

    @patch("sau_backend.run_geo_site_audit")
    def test_post_maps_not_found_validation_and_conflict_statuses(self, run_audit):
        run_audit.side_effect = GEOSiteAuditConflictError(
            "request_id 或 Idempotency-Key 已用于其他站点审计请求"
        )
        conflict = self.client.post(
            "/api/geo/site-audits",
            json=self.payload(),
            headers=self.headers(),
        )
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.get_json()["code"], 409)

        run_audit.side_effect = GEOSiteAuditError("品牌项目不存在")
        missing = self.client.post(
            "/api/geo/site-audits",
            json=self.payload(project_id=999999),
            headers=self.headers(
                **{
                    "X-Request-ID": "missing-project-request",
                    "Idempotency-Key": "missing-project-key",
                }
            ),
        )
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(missing.get_json()["code"], 404)

        run_audit.side_effect = GEOSiteAuditError("站点 URL 格式无效")
        invalid = self.client.post(
            "/api/geo/site-audits",
            json=self.payload(url="file:///etc/passwd"),
            headers=self.headers(
                **{
                    "X-Request-ID": "invalid-url-request",
                    "Idempotency-Key": "invalid-url-key",
                }
            ),
        )
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(invalid.get_json()["code"], 400)

        run_audit.side_effect = GEOSiteAuditTimeoutError("站点审计 DNS 解析超时")
        timed_out = self.client.post(
            "/api/geo/site-audits",
            json=self.payload(),
            headers=self.headers(
                **{
                    "X-Request-ID": "timeout-request",
                    "Idempotency-Key": "timeout-key",
                }
            ),
        )
        self.assertEqual(timed_out.status_code, 504)
        self.assertEqual(timed_out.get_json()["code"], 504)

    def test_history_endpoints_return_200_400_and_404_contracts(self):
        empty = self.client.get(
            "/api/geo/site-audits/runs",
            query_string={"project_id": self.project_id},
        )
        invalid_limit = self.client.get(
            "/api/geo/site-audits/runs",
            query_string={"project_id": self.project_id, "limit": 0},
        )
        missing_project = self.client.get(
            "/api/geo/site-audits/runs",
            query_string={"project_id": 999999},
        )
        missing_run = self.client.get("/api/geo/site-audits/runs/999999")

        self.assertEqual(empty.status_code, 200)
        self.assertEqual(empty.get_json()["data"], [])
        self.assertEqual(invalid_limit.status_code, 400)
        self.assertEqual(missing_project.status_code, 404)
        self.assertEqual(missing_run.status_code, 404)

    @patch("sau_backend.run_geo_site_audit")
    def test_site_audit_has_independent_429_rate_limit(self, run_audit):
        run_audit.return_value = {"status": "ready", "score": 100}
        app.config.update(
            SITE_AUDIT_RATE_LIMIT_PER_MINUTE=1,
            AI_RATE_LIMITER=FixedWindowRateLimiter(),
        )

        first = self.client.post(
            "/api/geo/site-audits",
            json=self.payload(),
            headers=self.headers(),
        )
        second = self.client.post(
            "/api/geo/site-audits",
            json=self.payload(),
            headers=self.headers(
                **{
                    "X-Request-ID": "api-site-audit-request-2",
                    "Idempotency-Key": "api-site-audit-key-2",
                }
            ),
        )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 429)
        self.assertEqual(second.get_json()["code"], 429)
        self.assertEqual(second.headers["X-RateLimit-Limit"], "1")
        self.assertIn("Retry-After", second.headers)
        self.assertEqual(run_audit.call_count, 1)

    @patch("sau_backend.run_geo_site_audit")
    def test_existing_operator_gate_returns_401_before_service(self, run_audit):
        app.config["ACCESS_CONTROL_ENABLED"] = True

        response = self.client.post(
            "/api/geo/site-audits",
            json=self.payload(),
            headers=self.headers(),
        )

        self.assertEqual(response.status_code, 401)
        self.assertTrue(response.get_json()["data"]["authentication_required"])
        run_audit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
