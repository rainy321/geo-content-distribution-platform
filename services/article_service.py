from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from services.geo_score_service import score_geo_content
from services.project_service import ProjectNotFoundError


ARTICLE_STATUSES = frozenset({"draft", "ready", "publishing", "published"})


class ArticleNotFoundError(LookupError):
    """Raised when an article id does not exist."""


def create_article(
    database_path: str | Path,
    *,
    project_id: int,
    title: str,
    summary: str,
    content: str,
    tags: list[str],
    status: str,
) -> dict[str, Any]:
    with closing(_connect(database_path)) as conn:
        with conn:
            project = _fetch_project(conn, project_id)
            if project is None:
                raise ProjectNotFoundError("品牌项目不存在")
            score_result = _score_article(project, title, content)
            cursor = conn.execute(
                """
                INSERT INTO articles (
                    project_id, title, summary, content, tags,
                    geo_score, geo_analysis, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    project_id,
                    title,
                    summary,
                    content,
                    _encode_json(tags),
                    score_result["score"],
                    _encode_geo_analysis(score_result),
                    status,
                ),
            )
            article_id = cursor.lastrowid
            row = _fetch_article(conn, article_id)
    return _serialize_article(row)


def create_articles_bulk(
    database_path: str | Path,
    *,
    project_id: int,
    articles: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Insert validated articles in one transaction."""

    created: list[dict[str, Any]] = []
    with closing(_connect(database_path)) as conn:
        with conn:
            project = _fetch_project(conn, project_id)
            if project is None:
                raise ProjectNotFoundError("品牌项目不存在")
            for article in articles:
                score_result = _score_article(
                    project,
                    article["title"],
                    article["content"],
                )
                cursor = conn.execute(
                    """
                    INSERT INTO articles (
                        project_id, title, summary, content, tags,
                        geo_score, geo_analysis, status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        project_id,
                        article["title"],
                        article.get("summary", ""),
                        article["content"],
                        _encode_json(article.get("tags", [])),
                        score_result["score"],
                        _encode_geo_analysis(score_result),
                        article.get("status", "draft"),
                    ),
                )
                created.append(
                    _serialize_article(_fetch_article(conn, cursor.lastrowid))
                )
    return created


def get_article(database_path: str | Path, article_id: int) -> dict[str, Any]:
    with closing(_connect(database_path)) as conn:
        row = _fetch_article(conn, article_id)
    if row is None:
        raise ArticleNotFoundError("文章不存在")
    return _serialize_article(row)


def list_articles(
    database_path: str | Path,
    *,
    project_id: int | None = None,
    status: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    conditions = []
    parameters = []
    if project_id is not None:
        conditions.append("a.project_id = ?")
        parameters.append(project_id)
    if status is not None:
        conditions.append("a.status = ?")
        parameters.append(status)

    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    offset = (page - 1) * page_size
    with closing(_connect(database_path)) as conn:
        total = conn.execute(
            f"SELECT COUNT(*) FROM articles AS a {where_clause}",
            parameters,
        ).fetchone()[0]
        rows = conn.execute(
            f"""
            SELECT a.id, a.project_id, p.name AS project_name,
                   a.title, a.summary, a.tags, a.geo_score, a.status,
                   a.created_at, a.updated_at
            FROM articles AS a
            JOIN projects AS p ON p.id = a.project_id
            {where_clause}
            ORDER BY a.created_at DESC, a.id DESC
            LIMIT ? OFFSET ?
            """,
            [*parameters, page_size, offset],
        ).fetchall()

    return {
        "items": [_serialize_article_list_item(row) for row in rows],
        "pagination": {
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": (total + page_size - 1) // page_size,
        },
    }


def update_article(
    database_path: str | Path,
    article_id: int,
    changes: dict[str, Any],
) -> dict[str, Any]:
    with closing(_connect(database_path)) as conn:
        with conn:
            current_row = _fetch_article(conn, article_id)
            if current_row is None:
                raise ArticleNotFoundError("文章不存在")
            current = _serialize_article(current_row)
            project = _fetch_project(conn, current["project_id"])
            if project is None:
                raise ProjectNotFoundError("品牌项目不存在")

            title = changes.get("title", current["title"])
            summary = changes.get("summary", current["summary"])
            content = changes.get("content", current["content"])
            tags = changes.get("tags", current["tags"])
            status = changes.get("status", current["status"])
            score_result = _score_article(project, title, content)

            conn.execute(
                """
                UPDATE articles
                SET title = ?, summary = ?, content = ?, tags = ?,
                    geo_score = ?, geo_analysis = ?, status = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    title,
                    summary,
                    content,
                    _encode_json(tags),
                    score_result["score"],
                    _encode_geo_analysis(score_result),
                    status,
                    article_id,
                ),
            )
            updated_row = _fetch_article(conn, article_id)
    return _serialize_article(updated_row)


def _connect(database_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(Path(database_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _fetch_project(conn: sqlite3.Connection, project_id: int):
    return conn.execute(
        "SELECT id, name, keywords FROM projects WHERE id = ?",
        (project_id,),
    ).fetchone()


def _fetch_article(conn: sqlite3.Connection, article_id: int):
    return conn.execute(
        "SELECT * FROM articles WHERE id = ?",
        (article_id,),
    ).fetchone()


def _score_article(project: sqlite3.Row, title: str, content: str) -> dict[str, Any]:
    return score_geo_content(
        title=title,
        content=content,
        brand=project["name"],
        keywords=_decode_string_list(project["keywords"]),
    )


def _serialize_article(row: sqlite3.Row) -> dict[str, Any]:
    article = dict(row)
    article["tags"] = _decode_string_list(article.get("tags"))
    article["geo_analysis"] = _decode_json_object(article.get("geo_analysis"))
    return article


def _serialize_article_list_item(row: sqlite3.Row) -> dict[str, Any]:
    article = dict(row)
    article["tags"] = _decode_string_list(article.get("tags"))
    return article


def _encode_geo_analysis(score_result: dict[str, Any]) -> str:
    return _encode_json(
        {
            "dimensions": score_result["dimensions"],
            "suggestions": score_result["suggestions"],
        }
    )


def _encode_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _decode_string_list(value: Any) -> list[str]:
    try:
        parsed = json.loads(value) if isinstance(value, str) else value
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    result = []
    for item in parsed:
        text = str(item).strip()
        if text and text not in result:
            result.append(text)
    return result


def _decode_json_object(value: Any) -> dict[str, Any]:
    try:
        parsed = json.loads(value) if isinstance(value, str) else value
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}
