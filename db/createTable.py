import sqlite3
from contextlib import closing
from pathlib import Path


DEFAULT_DB_FILE = Path(__file__).resolve().parent / "database.db"


def initialize_database(db_file=DEFAULT_DB_FILE):
    """Create application tables at a deterministic location."""
    db_path = Path(db_file).expanduser().resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    with closing(sqlite3.connect(db_path)) as conn:
        with conn:
            cursor = conn.cursor()
            cursor.execute(
                '''
                CREATE TABLE IF NOT EXISTS user_info (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    type INTEGER NOT NULL,
                    filePath TEXT NOT NULL,
                    userName TEXT NOT NULL,
                    status INTEGER DEFAULT 0,
                    last_checked_at DATETIME
                )
                '''
            )
            user_info_columns = {
                row[1] for row in cursor.execute("PRAGMA table_info(user_info)")
            }
            if "last_checked_at" not in user_info_columns:
                cursor.execute(
                    "ALTER TABLE user_info ADD COLUMN last_checked_at DATETIME"
                )
            cursor.execute(
                '''
                CREATE TABLE IF NOT EXISTS file_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    filename TEXT NOT NULL,
                    filesize REAL,
                    upload_time DATETIME DEFAULT CURRENT_TIMESTAMP,
                    file_path TEXT
                )
                '''
            )
            cursor.execute(
                '''
                CREATE TABLE IF NOT EXISTS projects (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    website TEXT NOT NULL DEFAULT '',
                    product TEXT NOT NULL DEFAULT '',
                    industry TEXT NOT NULL DEFAULT '',
                    description TEXT NOT NULL DEFAULT '',
                    keywords TEXT NOT NULL DEFAULT '[]',
                    competitors TEXT NOT NULL DEFAULT '[]',
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                '''
            )
            cursor.execute(
                '''
                CREATE TABLE IF NOT EXISTS articles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    summary TEXT NOT NULL DEFAULT '',
                    content TEXT NOT NULL,
                    tags TEXT NOT NULL DEFAULT '[]',
                    geo_score INTEGER NOT NULL DEFAULT 0,
                    geo_analysis TEXT NOT NULL DEFAULT '{}',
                    status TEXT NOT NULL DEFAULT 'draft'
                        CHECK (status IN ('draft', 'ready', 'publishing', 'published')),
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
                )
                '''
            )
            cursor.execute(
                '''
                CREATE INDEX IF NOT EXISTS idx_articles_project_id
                ON articles(project_id)
                '''
            )
            cursor.execute(
                '''
                CREATE TABLE IF NOT EXISTS publish_jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    article_id INTEGER NOT NULL,
                    platform TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'queued'
                        CHECK (status IN (
                            'queued', 'processing', 'success', 'failed',
                            'need_action', 'scheduled'
                        )),
                    message TEXT NOT NULL DEFAULT '',
                    result_url TEXT NOT NULL DEFAULT '',
                    images TEXT NOT NULL DEFAULT '[]',
                    publish_at DATETIME,
                    demo INTEGER NOT NULL DEFAULT 0
                        CHECK (demo IN (0, 1)),
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    started_at DATETIME,
                    finished_at DATETIME,
                    FOREIGN KEY (article_id) REFERENCES articles(id) ON DELETE CASCADE
                )
                '''
            )
            publish_job_columns = {
                row[1] for row in cursor.execute("PRAGMA table_info(publish_jobs)")
            }
            if "images" not in publish_job_columns:
                cursor.execute(
                    "ALTER TABLE publish_jobs "
                    "ADD COLUMN images TEXT NOT NULL DEFAULT '[]'"
                )
            cursor.execute(
                '''
                CREATE INDEX IF NOT EXISTS idx_publish_jobs_article_id
                ON publish_jobs(article_id)
                '''
            )
            cursor.execute(
                '''
                CREATE INDEX IF NOT EXISTS idx_publish_jobs_status
                ON publish_jobs(status)
                '''
            )

    return db_path


if __name__ == "__main__":
    created_path = initialize_database()
    print(f"✅ 表创建成功: {created_path}")
