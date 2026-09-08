import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from db.createTable import initialize_database
from sau_backend import app
from services.content_engine_adapters import MockContentEngineAdapter
from services.content_generation_service import ContentGenerationService


def _mock_generation_result(sequence: int) -> dict:
    return {
        "title": f"企业 AI Agent 主链路验收 {sequence}",
        "summary": f"第 {sequence} 次离线生成验收摘要。",
        "content": (
            f"XX科技第 {sequence} 次验证企业 AI Agent 内容生产主链路。\n\n"
            "## 实施步骤\n"
            "2026 年先核验品牌事实，再生成结构化内容，并保留运行回执。\n\n"
            "## FAQ\n"
            "问：如何确认内容可用？\n"
            "答：检查本地 GEO 分数、运行记录与文章状态。\n\n"
            "## 总结\n"
            "通过文章列表确认待发布内容可见。来源：https://example.test/acceptance"
        ),
        "tags": ["AI Agent", "主链路验收"],
        "faq": [
            {
                "question": "如何确认内容可用？",
                "answer": "检查本地 GEO 分数、运行记录与文章状态。",
            }
        ],
        "sources": [
            {
                "title": "离线验收来源",
                "url": "https://example.test/acceptance",
            }
        ],
        "engine": "mock",
        "engine_version": "three-run-test",
        "usage": None,
        "provider_geo_score": 90 - sequence,
    }


class ThreeRunMainFlowAcceptanceTests(unittest.TestCase):
    """Exercise the sellable content workflow without creating publish jobs."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "three-run-acceptance.db"
        initialize_database(self.db_path)
        with closing(sqlite3.connect(self.db_path)) as conn:
            with conn:
                self.project_id = conn.execute(
                    """
                    INSERT INTO projects (
                        name, website, product, industry, description,
                        keywords, competitors
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "XX科技",
                        "https://example.test",
                        "企业 AI Agent",
                        "人工智能",
                        "帮助企业建设可核验的智能内容工作流。",
                        json.dumps(["AI Agent", "企业智能体"], ensure_ascii=False),
                        "[]",
                    ),
                ).lastrowid

        self.original_config = {
            key: app.config.get(key)
            for key in (
                "DATABASE_PATH",
                "TESTING",
                "ACCESS_CONTROL_ENABLED",
                "AI_RATE_LIMIT_PER_MINUTE",
                "ALLOW_REAL_PUBLISHING",
                "RUN_PUBLISH_SCHEDULER",
            )
        }
        app.config.update(
            DATABASE_PATH=self.db_path,
            TESTING=True,
            ACCESS_CONTROL_ENABLED=False,
            AI_RATE_LIMIT_PER_MINUTE=0,
            ALLOW_REAL_PUBLISHING=False,
            RUN_PUBLISH_SCHEDULER=False,
        )
        self.client = app.test_client()

    def tearDown(self):
        app.config.update(self.original_config)
        self.temp_dir.cleanup()

    def test_three_consecutive_generate_score_save_ready_and_list_visibility(self):
        adapter = MockContentEngineAdapter(
            [_mock_generation_result(sequence) for sequence in range(1, 4)],
            version="three-run-test",
        )
        service = ContentGenerationService(self.db_path, adapter)
        saved_ids = []
        run_ids = []

        with (
            patch("sau_backend._create_content_generation_service", return_value=service),
            patch("sau_backend.create_publish_job") as create_publish_job,
            patch("sau_backend.execute_publish_job") as execute_publish_job,
            patch("sau_backend.create_real_publisher_factory") as create_publisher,
        ):
            for sequence in range(1, 4):
                with self.subTest(run=sequence):
                    generation_response = self.client.post(
                        "/api/articles/generate",
                        headers={
                            "X-Request-ID": f"three-run-request-{sequence}",
                            "Idempotency-Key": f"three-run-idempotency-{sequence}",
                        },
                        json={
                            "project_id": self.project_id,
                            "topic": f"企业 AI Agent 主链路验收 {sequence}",
                            "keywords": ["AI Agent", "企业智能体"],
                            "length": 1000,
                            "content_type": "行业科普",
                            "target_platform": "知乎",
                        },
                    )
                    self.assertEqual(generation_response.status_code, 200)
                    generated = generation_response.get_json()["data"]

                    self.assertIsInstance(generated["geo_score"], int)
                    self.assertGreaterEqual(generated["geo_score"], 0)
                    self.assertLessEqual(generated["geo_score"], 100)
                    self.assertIn("dimensions", generated["geo_analysis"])
                    self.assertIn("suggestions", generated["geo_analysis"])
                    self.assertEqual(generated["generation"]["engine"], "mock")
                    run_id = generated["generation"]["run_id"]
                    self.assertIsInstance(run_id, int)
                    run_ids.append(run_id)

                    save_response = self.client.post(
                        "/api/articles",
                        json={
                            "project_id": self.project_id,
                            "generation_run_id": run_id,
                            "title": generated["title"],
                            "summary": generated["summary"],
                            "content": generated["content"],
                            "tags": generated["tags"],
                            "status": "ready",
                        },
                    )
                    self.assertEqual(save_response.status_code, 201)
                    saved = save_response.get_json()["data"]
                    self.assertEqual(saved["status"], "ready")
                    self.assertEqual(saved["generation_run_id"], run_id)
                    self.assertEqual(saved["generation"]["run_id"], run_id)
                    self.assertEqual(saved["geo_score"], generated["geo_score"])
                    saved_ids.append(saved["id"])

                    list_response = self.client.get(
                        f"/api/articles?project_id={self.project_id}&status=ready"
                    )
                    self.assertEqual(list_response.status_code, 200)
                    listed = list_response.get_json()["data"]
                    visible_ids = {item["id"] for item in listed["items"]}
                    self.assertIn(saved["id"], visible_ids)
                    self.assertEqual(listed["pagination"]["total"], sequence)

            create_publish_job.assert_not_called()
            execute_publish_job.assert_not_called()
            create_publisher.assert_not_called()

        self.assertEqual(len(adapter.calls), 3)
        self.assertEqual(len(set(run_ids)), 3)
        self.assertEqual(len(set(saved_ids)), 3)

        with closing(sqlite3.connect(self.db_path)) as conn:
            publish_job_count = conn.execute("SELECT COUNT(*) FROM publish_jobs").fetchone()[0]
            bindings = conn.execute(
                """
                SELECT a.id, a.status, r.id, r.article_id
                FROM articles AS a
                JOIN content_generation_runs AS r ON r.article_id = a.id
                ORDER BY a.id
                """
            ).fetchall()

        self.assertEqual(publish_job_count, 0)
        self.assertEqual(
            [(row[0], row[1], row[2], row[3]) for row in bindings],
            [
                (article_id, "ready", run_id, article_id)
                for article_id, run_id in zip(saved_ids, run_ids)
            ],
        )


if __name__ == "__main__":
    unittest.main()
