import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from db.createTable import initialize_database


class DatabaseInitializationTests(unittest.TestCase):
    def test_creates_expected_tables_at_requested_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "nested" / "database.db"

            result = initialize_database(db_path)

            self.assertEqual(result, db_path.resolve())
            with closing(sqlite3.connect(db_path)) as conn:
                table_names = {
                    row[0]
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                }
            self.assertTrue(
                {
                    "user_info",
                    "file_records",
                    "projects",
                    "articles",
                    "publish_jobs",
                }.issubset(table_names)
            )

    def test_projects_schema_matches_mvp_contract(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "database.db"
            initialize_database(db_path)

            with closing(sqlite3.connect(db_path)) as conn:
                columns = {
                    row[1]: row
                    for row in conn.execute("PRAGMA table_info(projects)")
                }
                conn.execute("INSERT INTO projects (name) VALUES (?)", ("XX科技",))
                project = conn.execute(
                    """
                    SELECT name, website, product, industry, description,
                           keywords, competitors, created_at, updated_at
                    FROM projects
                    """
                ).fetchone()

            self.assertEqual(
                set(columns),
                {
                    "id",
                    "name",
                    "website",
                    "product",
                    "industry",
                    "description",
                    "keywords",
                    "competitors",
                    "created_at",
                    "updated_at",
                },
            )
            self.assertEqual(project[:7], ("XX科技", "", "", "", "", "[]", "[]"))
            self.assertTrue(project[7])
            self.assertTrue(project[8])

    def test_articles_schema_and_defaults_match_mvp_contract(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "database.db"
            initialize_database(db_path)

            with closing(sqlite3.connect(db_path)) as conn:
                columns = {
                    row[1]: row
                    for row in conn.execute("PRAGMA table_info(articles)")
                }
                project_id = conn.execute(
                    "INSERT INTO projects (name) VALUES (?)",
                    ("XX科技",),
                ).lastrowid
                conn.execute(
                    """
                    INSERT INTO articles (project_id, title, content)
                    VALUES (?, ?, ?)
                    """,
                    (project_id, "企业 AI Agent 指南", "正文"),
                )
                article = conn.execute(
                    """
                    SELECT project_id, title, summary, content, tags,
                           geo_score, geo_analysis, status, created_at, updated_at
                    FROM articles
                    """
                ).fetchone()

            self.assertEqual(
                set(columns),
                {
                    "id",
                    "project_id",
                    "title",
                    "summary",
                    "content",
                    "tags",
                    "geo_score",
                    "geo_analysis",
                    "status",
                    "created_at",
                    "updated_at",
                },
            )
            self.assertEqual(
                article[:8],
                (
                    project_id,
                    "企业 AI Agent 指南",
                    "",
                    "正文",
                    "[]",
                    0,
                    "{}",
                    "draft",
                ),
            )
            self.assertTrue(article[8])
            self.assertTrue(article[9])

    def test_articles_reject_unknown_status(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "database.db"
            initialize_database(db_path)

            with closing(sqlite3.connect(db_path)) as conn:
                project_id = conn.execute(
                    "INSERT INTO projects (name) VALUES (?)",
                    ("XX科技",),
                ).lastrowid
                with self.assertRaises(sqlite3.IntegrityError):
                    conn.execute(
                        """
                        INSERT INTO articles (project_id, title, content, status)
                        VALUES (?, ?, ?, ?)
                        """,
                        (project_id, "标题", "正文", "unknown"),
                    )

    def test_publish_jobs_schema_and_defaults_match_mvp_contract(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "database.db"
            initialize_database(db_path)

            with closing(sqlite3.connect(db_path)) as conn:
                columns = {
                    row[1]: row
                    for row in conn.execute("PRAGMA table_info(publish_jobs)")
                }
                project_id = conn.execute(
                    "INSERT INTO projects (name) VALUES (?)",
                    ("XX科技",),
                ).lastrowid
                article_id = conn.execute(
                    """
                    INSERT INTO articles (project_id, title, content)
                    VALUES (?, ?, ?)
                    """,
                    (project_id, "企业 AI Agent 指南", "正文"),
                ).lastrowid
                conn.execute(
                    """
                    INSERT INTO publish_jobs (article_id, platform)
                    VALUES (?, ?)
                    """,
                    (article_id, "zhihu"),
                )
                job = conn.execute(
                    """
                    SELECT article_id, platform, status, message, result_url,
                           images, video, publish_at, authorization_fingerprint,
                           auto_execute, demo,
                           created_at, started_at, finished_at
                    FROM publish_jobs
                    """
                ).fetchone()

            self.assertEqual(
                set(columns),
                {
                    "id",
                    "article_id",
                    "platform",
                    "status",
                    "message",
                    "result_url",
                    "images",
                    "video",
                    "publish_at",
                    "authorization_fingerprint",
                    "auto_execute",
                    "demo",
                    "created_at",
                    "started_at",
                    "finished_at",
                },
            )
            self.assertEqual(
                job[:11],
                (article_id, "zhihu", "queued", "", "", "[]", "", None, "", 0, 0),
            )
            self.assertTrue(job[11])
            self.assertIsNone(job[12])
            self.assertIsNone(job[13])

    def test_reinitialization_adds_assets_to_existing_publish_jobs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "database.db"
            with closing(sqlite3.connect(db_path)) as conn:
                conn.execute(
                    """
                    CREATE TABLE publish_jobs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        article_id INTEGER NOT NULL,
                        platform TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'queued',
                        message TEXT NOT NULL DEFAULT '',
                        result_url TEXT NOT NULL DEFAULT '',
                        publish_at DATETIME,
                        demo INTEGER NOT NULL DEFAULT 0,
                        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        started_at DATETIME,
                        finished_at DATETIME
                    )
                    """
                )
                conn.execute(
                    "INSERT INTO publish_jobs (article_id, platform) VALUES (1, 'zhihu')"
                )
                conn.commit()

            initialize_database(db_path)

            with closing(sqlite3.connect(db_path)) as conn:
                columns = {
                    row[1] for row in conn.execute("PRAGMA table_info(publish_jobs)")
                }
                images, video, fingerprint = conn.execute(
                    """
                    SELECT images, video, authorization_fingerprint
                    FROM publish_jobs
                    WHERE id = 1
                    """
                ).fetchone()
            self.assertIn("images", columns)
            self.assertIn("video", columns)
            self.assertIn("auto_execute", columns)
            self.assertIn("authorization_fingerprint", columns)
            self.assertEqual(images, "[]")
            self.assertEqual(video, "")
            self.assertEqual(fingerprint, "")

    def test_publish_jobs_reject_unknown_status_and_invalid_demo_flag(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "database.db"
            initialize_database(db_path)

            with closing(sqlite3.connect(db_path)) as conn:
                project_id = conn.execute(
                    "INSERT INTO projects (name) VALUES (?)",
                    ("XX科技",),
                ).lastrowid
                article_id = conn.execute(
                    """
                    INSERT INTO articles (project_id, title, content)
                    VALUES (?, ?, ?)
                    """,
                    (project_id, "标题", "正文"),
                ).lastrowid
                with self.assertRaises(sqlite3.IntegrityError):
                    conn.execute(
                        """
                        INSERT INTO publish_jobs (article_id, platform, status)
                        VALUES (?, ?, ?)
                        """,
                        (article_id, "zhihu", "unknown"),
                    )
                with self.assertRaises(sqlite3.IntegrityError):
                    conn.execute(
                        """
                        INSERT INTO publish_jobs (article_id, platform, demo)
                        VALUES (?, ?, ?)
                        """,
                        (article_id, "zhihu", 2),
                    )
                with self.assertRaises(sqlite3.IntegrityError):
                    conn.execute(
                        """
                        INSERT INTO publish_jobs (
                            article_id, platform, auto_execute
                        ) VALUES (?, ?, ?)
                        """,
                        (article_id, "zhihu", 2),
                    )

    def test_initialization_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "database.db"

            initialize_database(db_path)
            initialize_database(db_path)

            with closing(sqlite3.connect(db_path)) as conn:
                user_info_count = conn.execute(
                    "SELECT COUNT(*) FROM user_info"
                ).fetchone()[0]
            self.assertEqual(user_info_count, 0)

    def test_reinitialization_preserves_project_data(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "database.db"
            initialize_database(db_path)

            with closing(sqlite3.connect(db_path)) as conn:
                with conn:
                    conn.execute(
                        """
                        INSERT INTO projects (name, keywords, competitors)
                        VALUES (?, ?, ?)
                        """,
                        ("XX科技", '["AI Agent"]', '["品牌A"]'),
                    )

            initialize_database(db_path)

            with closing(sqlite3.connect(db_path)) as conn:
                project = conn.execute(
                    "SELECT name, keywords, competitors FROM projects"
                ).fetchone()
            self.assertEqual(project, ("XX科技", '["AI Agent"]', '["品牌A"]'))

    def test_reinitialization_preserves_article_data(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "database.db"
            initialize_database(db_path)

            with closing(sqlite3.connect(db_path)) as conn:
                with conn:
                    project_id = conn.execute(
                        "INSERT INTO projects (name) VALUES (?)",
                        ("XX科技",),
                    ).lastrowid
                    conn.execute(
                        """
                        INSERT INTO articles (
                            project_id, title, summary, content, tags,
                            geo_score, geo_analysis, status
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            project_id,
                            "企业 AI Agent 指南",
                            "摘要",
                            "正文",
                            '["AI Agent"]',
                            82,
                            '{"brand": 15}',
                            "ready",
                        ),
                    )

            initialize_database(db_path)

            with closing(sqlite3.connect(db_path)) as conn:
                article = conn.execute(
                    """
                    SELECT title, tags, geo_score, geo_analysis, status
                    FROM articles
                    """
                ).fetchone()
            self.assertEqual(
                article,
                (
                    "企业 AI Agent 指南",
                    '["AI Agent"]',
                    82,
                    '{"brand": 15}',
                    "ready",
                ),
            )

    def test_reinitialization_preserves_publish_job_data(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "database.db"
            initialize_database(db_path)

            with closing(sqlite3.connect(db_path)) as conn:
                with conn:
                    project_id = conn.execute(
                        "INSERT INTO projects (name) VALUES (?)",
                        ("XX科技",),
                    ).lastrowid
                    article_id = conn.execute(
                        """
                        INSERT INTO articles (project_id, title, content)
                        VALUES (?, ?, ?)
                        """,
                        (project_id, "企业 AI Agent 指南", "正文"),
                    ).lastrowid
                    conn.execute(
                        """
                        INSERT INTO publish_jobs (
                            article_id, platform, status, message, demo
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                        (article_id, "zhihu", "failed", "平台暂不可用", 1),
                    )

            initialize_database(db_path)

            with closing(sqlite3.connect(db_path)) as conn:
                job = conn.execute(
                    """
                    SELECT platform, status, message, demo
                    FROM publish_jobs
                    """
                ).fetchone()
            self.assertEqual(job, ("zhihu", "failed", "平台暂不可用", 1))


if __name__ == "__main__":
    unittest.main()
