import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from db.createTable import initialize_database
from sau_backend import app
from services.content_engine_contract import (
    ContentGenerationRequest,
    ContentGenerationResult,
)
from services.content_generation_run_service import begin_or_replay, mark_succeeded


class ArticlesApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "database.db"
        initialize_database(self.db_path)
        with closing(sqlite3.connect(self.db_path)) as conn:
            with conn:
                self.project_id = conn.execute(
                    """
                    INSERT INTO projects (name, keywords)
                    VALUES (?, ?)
                    """,
                    (
                        "XX科技",
                        json.dumps(["AI Agent", "企业智能体"], ensure_ascii=False),
                    ),
                ).lastrowid

        self.original_database_path = app.config["DATABASE_PATH"]
        self.original_testing = app.testing
        app.config["DATABASE_PATH"] = self.db_path
        app.testing = True
        self.client = app.test_client()

    def tearDown(self):
        app.config["DATABASE_PATH"] = self.original_database_path
        app.testing = self.original_testing
        self.temp_dir.cleanup()

    def test_saves_and_reads_article_with_server_side_score(self):
        response = self.client.post(
            "/api/articles",
            json={
                "project_id": self.project_id,
                "title": "AI Agent 入门",
                "summary": "文章摘要",
                "content": "XX科技介绍 AI Agent。",
                "tags": [" AI Agent ", "AI Agent", "企业智能体"],
                "geo_score": 100,
            },
        )

        self.assertEqual(response.status_code, 201)
        article = response.get_json()["data"]
        self.assertEqual(article["project_id"], self.project_id)
        self.assertEqual(article["tags"], ["AI Agent", "企业智能体"])
        self.assertNotEqual(article["geo_score"], 100)
        self.assertIn("dimensions", article["geo_analysis"])
        self.assertEqual(article["status"], "draft")

        get_response = self.client.get(f"/api/articles/{article['id']}")
        self.assertEqual(get_response.status_code, 200)
        self.assertEqual(get_response.get_json()["data"], article)

    def test_edits_article_and_recalculates_score(self):
        article = self._create_article()
        original_score = article["geo_score"]
        long_paragraph = "企业可以按照实际流程推进实施，并持续检查文章中的事实信息。" * 14
        improved_content = f"""XX科技提供企业 AI Agent 相关服务。2026 年总结了 3 项方法。

## 实施方法
{long_paragraph}

### 实践建议
{long_paragraph}

## FAQ
问：企业智能体适合谁？
答：适合有明确业务需求的企业。

## 总结
建议结合自身情况实施。数据来源：https://example.test/report
"""

        response = self.client.put(
            f"/api/articles/{article['id']}",
            json={
                "title": "企业 AI Agent 落地指南",
                "content": improved_content,
                "tags": ["AI Agent", "企业智能体"],
                "status": "ready",
            },
        )

        self.assertEqual(response.status_code, 200)
        updated = response.get_json()["data"]
        self.assertEqual(updated["status"], "ready")
        self.assertEqual(updated["geo_score"], 100)
        self.assertGreater(updated["geo_score"], original_score)

        with closing(sqlite3.connect(self.db_path)) as conn:
            stored = conn.execute(
                "SELECT title, tags, geo_score, status FROM articles WHERE id = ?",
                (article["id"],),
            ).fetchone()
        self.assertEqual(stored[0], "企业 AI Agent 落地指南")
        self.assertEqual(json.loads(stored[1]), ["AI Agent", "企业智能体"])
        self.assertEqual(stored[2:], (100, "ready"))

    def test_rejects_unknown_project_and_article(self):
        create_response = self.client.post(
            "/api/articles",
            json={
                "project_id": 999999,
                "title": "标题",
                "content": "正文",
            },
        )
        get_response = self.client.get("/api/articles/999999")
        update_response = self.client.put(
            "/api/articles/999999",
            json={"title": "新标题"},
        )

        self.assertEqual(create_response.status_code, 404)
        self.assertEqual(get_response.status_code, 404)
        self.assertEqual(update_response.status_code, 404)

    def test_validates_article_fields_and_status(self):
        missing_content = self.client.post(
            "/api/articles",
            json={"project_id": self.project_id, "title": "标题"},
        )
        invalid_tags = self.client.post(
            "/api/articles",
            json={
                "project_id": self.project_id,
                "title": "标题",
                "content": "正文",
                "tags": "AI Agent",
            },
        )
        invalid_status = self.client.post(
            "/api/articles",
            json={
                "project_id": self.project_id,
                "title": "标题",
                "content": "正文",
                "status": "unknown",
            },
        )

        self.assertEqual(missing_content.status_code, 400)
        self.assertEqual(invalid_tags.status_code, 400)
        self.assertEqual(invalid_status.status_code, 400)

    def test_rejects_project_change_and_empty_update(self):
        article = self._create_article()

        project_change = self.client.put(
            f"/api/articles/{article['id']}",
            json={"project_id": self.project_id},
        )
        empty_update = self.client.put(
            f"/api/articles/{article['id']}",
            json={},
        )

        self.assertEqual(project_change.status_code, 400)
        self.assertEqual(empty_update.status_code, 400)

    def test_operator_cannot_force_internal_publishing_status(self):
        article = self._create_article()

        response = self.client.put(
            f"/api/articles/{article['id']}",
            json={"status": "published"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("草稿或待发布", response.get_json()["msg"])

    def test_generation_run_binds_once_and_keeps_snapshot_scoring_context(self):
        request = ContentGenerationRequest.create(
            project_context={
                "id": self.project_id,
                "name": "XX科技",
                "keywords": ["AI Agent", "企业智能体"],
            },
            topic="快照评分",
            keywords=["独立关键词"],
            length=600,
            content_type="行业科普",
            idempotency_key="article-save-snapshot-test",
        )
        run = begin_or_replay(self.db_path, request, configured_engine="mock")
        result = ContentGenerationResult.from_payload(
            {
                "title": "独立关键词实践",
                "content": "XX科技围绕独立关键词提供说明。",
                "summary": "摘要",
                "tags": ["独立关键词"],
            },
            default_engine="mock",
            default_engine_version="test",
            request_id=request.request_id,
            elapsed_ms=1,
        )
        mark_succeeded(
            self.db_path,
            run.run_id,
            result,
            local_score=30,
            local_analysis={"dimensions": {}, "suggestions": []},
        )

        first = self.client.post(
            "/api/articles",
            json={
                "project_id": self.project_id,
                "generation_run_id": run.run_id,
                "title": result.title,
                "summary": result.summary,
                "content": result.content,
                "tags": list(result.tags),
            },
        )
        self.assertEqual(first.status_code, 201)
        article = first.get_json()["data"]
        self.assertEqual(article["generation_run_id"], run.run_id)
        self.assertEqual(article["generation"]["engine"], "mock")
        self.assertEqual(article["generation"]["usage"], None)
        score_before = article["geo_score"]

        detail = self.client.get(f"/api/articles/{article['id']}")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(
            detail.get_json()["data"]["generation"]["run_id"],
            run.run_id,
        )

        with closing(sqlite3.connect(self.db_path)) as conn:
            with conn:
                conn.execute(
                    "UPDATE projects SET keywords = ? WHERE id = ?",
                    (json.dumps(["完全不同"], ensure_ascii=False), self.project_id),
                )

        updated = self.client.put(
            f"/api/articles/{article['id']}",
            json={"summary": "更新后的摘要"},
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.get_json()["data"]["geo_score"], score_before)

        duplicate = self.client.post(
            "/api/articles",
            json={
                "project_id": self.project_id,
                "generation_run_id": run.run_id,
                "title": "重复保存",
                "content": "重复内容",
            },
        )
        self.assertEqual(duplicate.status_code, 409)
        self.assertIn("已经保存", duplicate.get_json()["msg"])

    def _create_article(self):
        response = self.client.post(
            "/api/articles",
            json={
                "project_id": self.project_id,
                "title": "初始标题",
                "content": "XX科技的简短正文。",
            },
        )
        self.assertEqual(response.status_code, 201)
        return response.get_json()["data"]


if __name__ == "__main__":
    unittest.main()
