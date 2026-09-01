import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from db.createTable import initialize_database
from sau_backend import app


class ProjectsApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "database.db"
        initialize_database(self.db_path)
        self.original_database_path = app.config["DATABASE_PATH"]
        self.original_testing = app.testing
        app.config["DATABASE_PATH"] = self.db_path
        app.testing = True
        self.client = app.test_client()

    def tearDown(self):
        app.config["DATABASE_PATH"] = self.original_database_path
        app.testing = self.original_testing
        self.temp_dir.cleanup()

    def test_creates_reads_and_lists_project(self):
        response = self.client.post(
            "/api/projects",
            json={
                "name": " XX科技 ",
                "website": " https://example.test ",
                "product": "企业 AI Agent",
                "industry": "人工智能",
                "description": "面向企业提供智能服务。",
                "keywords": [" AI Agent ", "AI Agent", "企业智能体"],
                "competitors": ["品牌A", "品牌A", "品牌B"],
            },
        )

        self.assertEqual(response.status_code, 201)
        project = response.get_json()["data"]
        self.assertEqual(project["name"], "XX科技")
        self.assertEqual(project["website"], "https://example.test")
        self.assertEqual(project["keywords"], ["AI Agent", "企业智能体"])
        self.assertEqual(project["competitors"], ["品牌A", "品牌B"])

        detail = self.client.get(f"/api/projects/{project['id']}")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.get_json()["data"]["article_count"], 0)

        listing = self.client.get("/api/projects")
        self.assertEqual(listing.status_code, 200)
        self.assertEqual([item["id"] for item in listing.get_json()["data"]], [project["id"]])

    def test_updates_only_supplied_fields(self):
        project = self._create_project()

        response = self.client.put(
            f"/api/projects/{project['id']}",
            json={
                "product": "企业智能体平台",
                "keywords": ["企业智能体", "大模型应用"],
            },
        )

        self.assertEqual(response.status_code, 200)
        updated = response.get_json()["data"]
        self.assertEqual(updated["name"], "XX科技")
        self.assertEqual(updated["website"], "https://example.test")
        self.assertEqual(updated["product"], "企业智能体平台")
        self.assertEqual(updated["keywords"], ["企业智能体", "大模型应用"])

    def test_list_includes_article_count(self):
        project = self._create_project()
        with closing(sqlite3.connect(self.db_path)) as conn:
            with conn:
                conn.execute(
                    """
                    INSERT INTO articles (project_id, title, content)
                    VALUES (?, ?, ?)
                    """,
                    (project["id"], "测试文章", "正文"),
                )

        listing = self.client.get("/api/projects").get_json()["data"]
        self.assertEqual(listing[0]["article_count"], 1)

        updated = self.client.put(
            f"/api/projects/{project['id']}",
            json={"description": "更新后的品牌介绍"},
        ).get_json()["data"]
        self.assertEqual(updated["article_count"], 1)

    def test_deletes_empty_project(self):
        project = self._create_project()

        response = self.client.delete(f"/api/projects/{project['id']}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["data"], {"id": project["id"]})
        self.assertEqual(self.client.get(f"/api/projects/{project['id']}").status_code, 404)

    def test_refuses_to_delete_project_with_articles(self):
        project = self._create_project()
        with closing(sqlite3.connect(self.db_path)) as conn:
            with conn:
                article_id = conn.execute(
                    """
                    INSERT INTO articles (project_id, title, content)
                    VALUES (?, ?, ?)
                    """,
                    (project["id"], "测试文章", "正文"),
                ).lastrowid

        response = self.client.delete(f"/api/projects/{project['id']}")

        self.assertEqual(response.status_code, 409)
        with closing(sqlite3.connect(self.db_path)) as conn:
            project_count = conn.execute(
                "SELECT COUNT(*) FROM projects WHERE id = ?",
                (project["id"],),
            ).fetchone()[0]
            article_count = conn.execute(
                "SELECT COUNT(*) FROM articles WHERE id = ?",
                (article_id,),
            ).fetchone()[0]
        self.assertEqual((project_count, article_count), (1, 1))

    def test_validates_fields_and_unknown_projects(self):
        invalid_requests = [
            self.client.post("/api/projects", json={}),
            self.client.post(
                "/api/projects",
                json={"name": "XX科技", "keywords": "AI Agent"},
            ),
            self.client.put("/api/projects/999999", json={"name": "新名称"}),
            self.client.put("/api/projects/1", json={}),
            self.client.delete("/api/projects/999999"),
        ]

        self.assertEqual(
            [response.status_code for response in invalid_requests],
            [400, 400, 404, 400, 404],
        )

    def _create_project(self):
        response = self.client.post(
            "/api/projects",
            json={
                "name": "XX科技",
                "website": "https://example.test",
                "product": "企业 AI Agent",
                "keywords": ["AI Agent"],
            },
        )
        self.assertEqual(response.status_code, 201)
        return response.get_json()["data"]


if __name__ == "__main__":
    unittest.main()
