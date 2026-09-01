import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from db.createTable import initialize_database
from sau_backend import app


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
