from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from services.geo_score_service import score_geo_content
from services.content_generation_run_service import (
    GenerationRunNotFoundError,
    GenerationRunStateError,
    bind_article,
)
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
    generation_run_id: int | None = None,
) -> dict[str, Any]:
    with closing(_connect(database_path)) as conn:
        with conn:
            project = _fetch_project(conn, project_id)
            if project is None:
                raise ProjectNotFoundError("品牌项目不存在")
            score_context = _score_context_for_run(
                conn,
                generation_run_id,
                project_id=project_id,
            )
            score_result = _score_article(
                project,
                title,
                content,
                score_context=score_context,
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
            if generation_run_id is not None:
                bind_article(
                    database_path,
                    generation_run_id,
                    article_id,
                    connection=conn,
                )
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
            score_context = _score_context_for_run(
                conn,
                current.get("generation_run_id"),
                project_id=current["project_id"],
                article_id=article_id,
            )
            score_result = _score_article(
                project,
                title,
                content,
                score_context=score_context,
            )

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
        """
        SELECT a.*,
               r.id AS generation_run_id,
               r.configured_engine AS generation_configured_engine,
               r.actual_engine AS generation_actual_engine,
               r.engine_version AS generation_engine_version,
               r.trace_id AS generation_trace_id,
               r.elapsed_ms AS generation_elapsed_ms,
               r.usage AS generation_usage,
               r.usage_status AS generation_usage_status,
               r.provider_geo_score AS generation_provider_geo_score,
               r.fallback_used AS generation_fallback_used,
               r.fallback_from AS generation_fallback_from,
               r.warnings AS generation_warnings,
               r.result_payload AS generation_result_payload
        FROM articles AS a
        LEFT JOIN content_generation_runs AS r ON r.article_id = a.id
        WHERE a.id = ?
        """,
        (article_id,),
    ).fetchone()


def _score_article(
    project: sqlite3.Row,
    title: str,
    content: str,
    *,
    score_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    context = score_context or {}
    return score_geo_content(
        title=title,
        content=content,
        brand=str(context.get("brand") or project["name"]).strip(),
        keywords=(
            context["keywords"]
            if isinstance(context.get("keywords"), list)
            else _decode_string_list(project["keywords"])
        ),
    )


def _score_context_for_run(
    conn: sqlite3.Connection,
    generation_run_id: int | None,
    *,
    project_id: int,
    article_id: int | None = None,
) -> dict[str, Any] | None:
    if generation_run_id is None:
        return None
    row = conn.execute(
        """
        SELECT project_id, project_snapshot, brief_snapshot, status, article_id
        FROM content_generation_runs
        WHERE id = ?
        """,
        (int(generation_run_id),),
    ).fetchone()
    if row is None:
        raise GenerationRunNotFoundError("生成记录不存在")
    if row["status"] != "succeeded":
        raise GenerationRunStateError("仅成功的生成记录可以保存为文章")
    if row["project_id"] is not None and int(row["project_id"]) != int(project_id):
        raise GenerationRunStateError("生成记录与品牌项目不匹配")
    if row["article_id"] is not None and (
        article_id is None or int(row["article_id"]) != int(article_id)
    ):
        raise GenerationRunStateError("生成记录已经保存为另一篇文章")
    project_snapshot = _decode_json_object(row["project_snapshot"])
    brief_snapshot = _decode_json_object(row["brief_snapshot"])
    keywords = brief_snapshot.get("keywords")
    if not isinstance(keywords, list):
        keywords = project_snapshot.get("keywords")
    return {
        "brand": str(project_snapshot.get("name") or "").strip(),
        "keywords": _decode_string_list(keywords),
    }


def _serialize_article(row: sqlite3.Row) -> dict[str, Any]:
    article = dict(row)
    article["tags"] = _decode_string_list(article.get("tags"))
    article["geo_analysis"] = _decode_json_object(article.get("geo_analysis"))
    generation_run_id = article.get("generation_run_id")
    if generation_run_id is not None:
        generation_result = _decode_json_object(
            article.get("generation_result_payload")
        )
        article["generation"] = {
            "run_id": generation_run_id,
            "engine": (
                article.get("generation_actual_engine")
                or article.get("generation_configured_engine")
                or "unknown"
            ),
            "engine_version": article.get("generation_engine_version") or "unknown",
            "trace_id": article.get("generation_trace_id") or "",
            "elapsed_ms": article.get("generation_elapsed_ms"),
            "usage": _decode_json_object(article.get("generation_usage")) or None,
            "usage_status": article.get("generation_usage_status") or "unknown",
            "provider_geo_score": article.get("generation_provider_geo_score"),
            "fallback_used": bool(article.get("generation_fallback_used")),
            "fallback_from": article.get("generation_fallback_from") or "",
            "warnings": _decode_json_list(article.get("generation_warnings")),
            "sources": generation_result.get("sources")
            if isinstance(generation_result.get("sources"), list)
            else [],
        }
    for field in tuple(article):
        if field.startswith("generation_") and field != "generation_run_id":
            article.pop(field, None)
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


def _decode_json_list(value: Any) -> list[Any]:
    try:
        parsed = json.loads(value) if isinstance(value, str) else value
    except (TypeError, json.JSONDecodeError):
        return []
    return parsed if isinstance(parsed, list) else []
