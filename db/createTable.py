import sqlite3
from contextlib import closing
from pathlib import Path


DEFAULT_DB_FILE = Path(__file__).resolve().parent / "database.db"


def initialize_database(db_file=DEFAULT_DB_FILE):
    """Create application tables at a deterministic location."""
    db_path = Path(db_file).expanduser().resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    with closing(sqlite3.connect(db_path, timeout=30)) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 30000")
        # Both web and worker initialize the same SQLite file during container
        # startup.  Serialize schema checks/migrations so two processes cannot
        # race between PRAGMA table_info and ALTER/CREATE statements.
        conn.execute("BEGIN IMMEDIATE")
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
                    file_path TEXT,
                    media_type TEXT NOT NULL DEFAULT 'file',
                    tags TEXT NOT NULL DEFAULT '[]',
                    source TEXT NOT NULL DEFAULT 'upload'
                )
                '''
            )
            file_record_columns = {
                row[1] for row in cursor.execute("PRAGMA table_info(file_records)")
            }
            if "media_type" not in file_record_columns:
                cursor.execute(
                    "ALTER TABLE file_records "
                    "ADD COLUMN media_type TEXT NOT NULL DEFAULT 'file'"
                )
            if "tags" not in file_record_columns:
                cursor.execute(
                    "ALTER TABLE file_records "
                    "ADD COLUMN tags TEXT NOT NULL DEFAULT '[]'"
                )
            if "source" not in file_record_columns:
                cursor.execute(
                    "ALTER TABLE file_records "
                    "ADD COLUMN source TEXT NOT NULL DEFAULT 'upload'"
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
                CREATE TABLE IF NOT EXISTS content_generation_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id TEXT NOT NULL UNIQUE,
                    idempotency_key TEXT,
                    request_fingerprint TEXT NOT NULL,
                    project_id INTEGER,
                    project_snapshot TEXT NOT NULL,
                    brief_snapshot TEXT NOT NULL,
                    configured_engine TEXT NOT NULL,
                    actual_engine TEXT NOT NULL DEFAULT '',
                    engine_version TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'started'
                        CHECK (status IN ('started', 'succeeded', 'failed', 'unknown')),
                    trace_id TEXT NOT NULL DEFAULT '',
                    elapsed_ms INTEGER,
                    usage TEXT,
                    usage_status TEXT NOT NULL DEFAULT 'unknown'
                        CHECK (usage_status IN ('reported', 'unknown')),
                    provider_geo_score REAL,
                    fallback_used INTEGER NOT NULL DEFAULT 0
                        CHECK (fallback_used IN (0, 1)),
                    fallback_from TEXT NOT NULL DEFAULT '',
                    warnings TEXT NOT NULL DEFAULT '[]',
                    local_score INTEGER,
                    local_analysis TEXT,
                    result_payload TEXT,
                    result_digest TEXT NOT NULL DEFAULT '',
                    error_code TEXT NOT NULL DEFAULT '',
                    error_message TEXT NOT NULL DEFAULT '',
                    retry_after_seconds INTEGER,
                    article_id INTEGER,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE SET NULL,
                    FOREIGN KEY (article_id) REFERENCES articles(id) ON DELETE SET NULL
                )
                '''
            )
            generation_run_columns = {
                row[1]
                for row in cursor.execute(
                    "PRAGMA table_info(content_generation_runs)"
                )
            }
            if "retry_after_seconds" not in generation_run_columns:
                cursor.execute(
                    "ALTER TABLE content_generation_runs "
                    "ADD COLUMN retry_after_seconds INTEGER"
                )
            cursor.execute(
                '''
                CREATE UNIQUE INDEX IF NOT EXISTS idx_generation_runs_idempotency_key
                ON content_generation_runs(idempotency_key)
                WHERE idempotency_key IS NOT NULL
                '''
            )
            cursor.execute(
                '''
                CREATE INDEX IF NOT EXISTS idx_generation_runs_project_id
                ON content_generation_runs(project_id)
                '''
            )
            cursor.execute(
                '''
                CREATE UNIQUE INDEX IF NOT EXISTS idx_generation_runs_article_id
                ON content_generation_runs(article_id)
                WHERE article_id IS NOT NULL
                '''
            )
            cursor.execute(
                '''
                CREATE TABLE IF NOT EXISTS content_templates (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    description TEXT NOT NULL DEFAULT '',
                    content_type TEXT NOT NULL DEFAULT '行业科普',
                    instruction TEXT NOT NULL,
                    is_builtin INTEGER NOT NULL DEFAULT 0
                        CHECK (is_builtin IN (0, 1)),
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                '''
            )
            cursor.executemany(
                '''
                INSERT OR IGNORE INTO content_templates (
                    id, name, description, content_type, instruction, is_builtin
                ) VALUES (?, ?, ?, ?, ?, 1)
                ''',
                (
                    (
                        1,
                        "行业问题拆解",
                        "从用户问题出发，形成解释、方案、FAQ 与总结。",
                        "行业科普",
                        "开头直接回答文章主题中的核心问题；正文按问题、原因、判断标准、行动建议组织，并保留 FAQ。",
                    ),
                    (
                        2,
                        "产品解决方案",
                        "围绕业务痛点说明产品如何解决问题。",
                        "解决方案",
                        "先描述可验证的业务场景和痛点，再说明产品能力、适用边界和实施步骤；禁止编造客户案例与效果数据。",
                    ),
                    (
                        3,
                        "竞品选择指南",
                        "用透明维度帮助读者比较不同方案。",
                        "对比文章",
                        "使用适用对象、核心能力、实施成本和限制条件作为比较维度；只比较输入中可确认的事实，不贬低竞品。",
                    ),
                    (
                        4,
                        "问答知识库",
                        "生成适合搜索与 AI 摘要引用的结构化问答。",
                        "FAQ",
                        "正文以 5 至 8 个真实用户问题为主，每个回答先给结论再解释依据；最后增加选择或实施清单。",
                    ),
                ),
            )
            cursor.execute(
                '''
                CREATE TABLE IF NOT EXISTS publish_jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    article_id INTEGER NOT NULL,
                    account_id INTEGER,
                    platform TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'queued'
                        CHECK (status IN (
                            'queued', 'processing', 'success', 'failed',
                            'need_action', 'scheduled'
                        )),
                    message TEXT NOT NULL DEFAULT '',
                    result_url TEXT NOT NULL DEFAULT '',
                    images TEXT NOT NULL DEFAULT '[]',
                    video TEXT NOT NULL DEFAULT '',
                    publish_at DATETIME,
                    authorization_fingerprint TEXT NOT NULL DEFAULT '',
                    auto_execute INTEGER NOT NULL DEFAULT 0
                        CHECK (auto_execute IN (0, 1)),
                    demo INTEGER NOT NULL DEFAULT 0
                        CHECK (demo IN (0, 1)),
                    state_version INTEGER NOT NULL DEFAULT 0
                        CHECK (state_version >= 0),
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    started_at DATETIME,
                    finished_at DATETIME,
                    FOREIGN KEY (article_id) REFERENCES articles(id) ON DELETE CASCADE,
                    FOREIGN KEY (account_id) REFERENCES user_info(id) ON DELETE SET NULL
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
            if "video" not in publish_job_columns:
                cursor.execute(
                    "ALTER TABLE publish_jobs "
                    "ADD COLUMN video TEXT NOT NULL DEFAULT ''"
                )
            if "auto_execute" not in publish_job_columns:
                cursor.execute(
                    "ALTER TABLE publish_jobs "
                    "ADD COLUMN auto_execute INTEGER NOT NULL DEFAULT 0 "
                    "CHECK (auto_execute IN (0, 1))"
                )
            if "state_version" not in publish_job_columns:
                cursor.execute(
                    "ALTER TABLE publish_jobs "
                    "ADD COLUMN state_version INTEGER NOT NULL DEFAULT 0 "
                    "CHECK (state_version >= 0)"
                )
            if "authorization_fingerprint" not in publish_job_columns:
                cursor.execute(
                    "ALTER TABLE publish_jobs "
                    "ADD COLUMN authorization_fingerprint TEXT NOT NULL DEFAULT ''"
                )
            if "account_id" not in publish_job_columns:
                cursor.execute(
                    "ALTER TABLE publish_jobs ADD COLUMN account_id INTEGER"
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
            cursor.execute(
                '''
                CREATE INDEX IF NOT EXISTS idx_publish_jobs_account_id
                ON publish_jobs(account_id)
                '''
            )

    return db_path


if __name__ == "__main__":
    created_path = initialize_database()
    print(f"✅ 表创建成功: {created_path}")
