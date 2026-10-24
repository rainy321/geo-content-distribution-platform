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

    def test_list_review_fingerprint_matches_full_detail_without_returning_body(self):
        listing = self.client.get('/api/articles').json['data']['items']
        for item in listing:
            with self.subTest(article=item['id']):
                detail = self.client.get(f'/api/articles/{item["id"]}').json['data']
                self.assertRegex(item['review_fingerprint'], '^[0-9a-f]{64}$')
                self.assertEqual(item['review_fingerprint'], detail['review_fingerprint'])
                self.assertNotIn('content', item)
                self.assertNotIn('geo_analysis', item)
                self.assertTrue(detail['content'])

    def test_same_title_and_status_body_edit_changes_both_list_and_detail_fingerprint(self):
        article = self.article_ids[1]
        previous = self.client.get(f'/api/articles/{article}').json['data']
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.execute('UPDATE articles SET content=? WHERE id=?', ('server changed only body',article))
        current = self.client.get(f'/api/articles/{article}').json['data']
        item = next(value for value in self.client.get('/api/articles').json['data']['items'] if value['id']==article)
        self.assertEqual(current['title'], previous['title'])
        self.assertEqual(current['status'], previous['status'])
        self.assertNotEqual(current['review_fingerprint'], previous['review_fingerprint'])
        self.assertEqual(item['review_fingerprint'], current['review_fingerprint'])
        self.assertNotIn('content', item)

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
