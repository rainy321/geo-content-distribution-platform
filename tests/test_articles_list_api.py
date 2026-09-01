import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from db.createTable import initialize_database
from sau_backend import app


class ArticlesListApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "database.db"
        initialize_database(self.db_path)
        with closing(sqlite3.connect(self.db_path)) as conn:
            with conn:
                self.project_a = conn.execute(
                    "INSERT INTO projects (name) VALUES (?)",
                    ("品牌A",),
                ).lastrowid
                self.project_b = conn.execute(
                    "INSERT INTO projects (name) VALUES (?)",
                    ("品牌B",),
                ).lastrowid
                self.article_ids = [
                    self._insert_article(conn, self.project_a, "文章一", "draft"),
                    self._insert_article(conn, self.project_a, "文章二", "ready"),
                    self._insert_article(conn, self.project_b, "文章三", "ready"),
                ]

        self.original_database_path = app.config["DATABASE_PATH"]
        self.original_testing = app.testing
        app.config["DATABASE_PATH"] = self.db_path
        app.testing = True
        self.client = app.test_client()

    def tearDown(self):
        app.config["DATABASE_PATH"] = self.original_database_path
        app.testing = self.original_testing
        self.temp_dir.cleanup()

    def test_lists_articles_in_reverse_creation_order(self):
        response = self.client.get("/api/articles")

        self.assertEqual(response.status_code, 200)
        data = response.get_json()["data"]
        self.assertEqual(
            [item["id"] for item in data["items"]],
            list(reversed(self.article_ids)),
        )
        self.assertEqual(
            data["pagination"],
            {"page": 1, "page_size": 20, "total": 3, "total_pages": 1},
        )
        self.assertEqual(data["items"][0]["project_name"], "品牌B")
        self.assertEqual(data["items"][0]["tags"], ["GEO", "内容"])
        self.assertNotIn("content", data["items"][0])
        self.assertNotIn("geo_analysis", data["items"][0])

    def test_paginates_without_changing_total(self):
        first_page = self.client.get("/api/articles?page=1&page_size=2").get_json()[
            "data"
        ]
        second_page = self.client.get("/api/articles?page=2&page_size=2").get_json()[
            "data"
        ]

        self.assertEqual(
            [item["id"] for item in first_page["items"]],
            [self.article_ids[2], self.article_ids[1]],
        )
        self.assertEqual(
            [item["id"] for item in second_page["items"]],
            [self.article_ids[0]],
        )
        self.assertEqual(second_page["pagination"]["total"], 3)
        self.assertEqual(second_page["pagination"]["total_pages"], 2)

    def test_filters_by_project_and_status_together(self):
        response = self.client.get(
            f"/api/articles?project_id={self.project_a}&status=ready"
        )

        self.assertEqual(response.status_code, 200)
        data = response.get_json()["data"]
        self.assertEqual([item["title"] for item in data["items"]], ["文章二"])
        self.assertEqual(data["pagination"]["total"], 1)

    def test_returns_empty_page_for_unknown_project(self):
        response = self.client.get("/api/articles?project_id=999999")

        self.assertEqual(response.status_code, 200)
        data = response.get_json()["data"]
        self.assertEqual(data["items"], [])
        self.assertEqual(data["pagination"]["total"], 0)
        self.assertEqual(data["pagination"]["total_pages"], 0)

    def test_rejects_invalid_pagination_and_filters(self):
        requests = [
            "/api/articles?page=0",
            "/api/articles?page=abc",
            "/api/articles?page_size=101",
            "/api/articles?project_id=none",
            "/api/articles?status=unknown",
        ]

        for url in requests:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 400)

    @staticmethod
    def _insert_article(conn, project_id, title, status):
        return conn.execute(
            """
            INSERT INTO articles (
                project_id, title, summary, content, tags, status
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                project_id,
                title,
                f"{title}摘要",
                f"{title}正文",
                json.dumps(["GEO", "内容"], ensure_ascii=False),
                status,
            ),
        ).lastrowid


if __name__ == "__main__":
    unittest.main()
