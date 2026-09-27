from __future__ import annotations

import hashlib
import hmac
import json
import re
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any, Mapping

from services.geo_score_service import GEO_SCORE_VERSION, score_geo_content
from services.geo_article_compiler_service import (
    resolve_explicit_scoring_length_context,
    resolve_generation_length_context,
    score_ai_citeability,
)
from services.content_generation_run_service import (
    GenerationRunNotFoundError,
    GenerationRunStateError,
    bind_article,
)
from services.project_service import ProjectNotFoundError


ARTICLE_STATUSES = frozenset({"draft", "ready", "publishing", "published"})


class ArticleNotFoundError(LookupError):
    """Raised when an article id does not exist."""


class ArticleConflictError(RuntimeError):
    """Raised when an article changed after a caller read its snapshot."""


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
    target_length: int | None = None,
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
                summary=summary,
                score_context=score_context,
                target_length=target_length,
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
                    summary=article.get("summary", ""),
                    target_length=article.get("target_length"),
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
    *,
    expected_content_fingerprint: str | None = None,
) -> dict[str, Any]:
    normalized_expected_fingerprint = _normalize_expected_content_fingerprint(
        expected_content_fingerprint
    )
    with closing(_connect(database_path)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            current_row = _fetch_article(conn, article_id)
            if current_row is None:
                raise ArticleNotFoundError("文章不存在")
            current = _serialize_article(current_row)
            if (
                normalized_expected_fingerprint is not None
                and not hmac.compare_digest(
                    normalized_expected_fingerprint,
                    current["content_fingerprint"],
                )
            ):
                raise ArticleConflictError(
                    "文章已在读取后发生变化，未覆盖当前版本"
                )
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
            if "target_length" not in changes:
                # The saved profile is the latest operator-approved length
                # contract. Keep run provenance, but do not restore the run's
                # original length after a later explicit edit.
                score_context = {
                    **(score_context or {}),
                    **_stored_length_context(current.get("geo_analysis")),
                }
            score_result = _score_article(
                project,
                title,
                content,
                summary=summary,
                score_context=score_context,
                target_length=changes.get("target_length"),
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
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return _serialize_article(updated_row)


def article_content_fingerprint(article: Mapping[str, Any]) -> str:
    """Return a stable digest for fields that affect an article's publish body."""

    canonical = _article_publish_payload(article)
    canonical["status"] = str(article.get("status") or "")
    return _fingerprint_article_payload(canonical)


def article_publish_payload_fingerprint(article: Mapping[str, Any]) -> str:
    """Digest immutable publish content while excluding lifecycle status."""

    return _fingerprint_article_payload(_article_publish_payload(article))


def _article_publish_payload(article: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": int(article["id"]),
        "project_id": int(article["project_id"]),
        "title": str(article.get("title") or ""),
        "summary": str(article.get("summary") or ""),
        "content": str(article.get("content") or ""),
        "tags": _decode_string_list(article.get("tags")),
    }


def _fingerprint_article_payload(canonical: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        canonical,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _normalize_expected_content_fingerprint(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError("expected_content_fingerprint 格式无效")
    return value


def _connect(database_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(Path(database_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _fetch_project(conn: sqlite3.Connection, project_id: int):
    return conn.execute(
        "SELECT id, name, product, website, keywords FROM projects WHERE id = ?",
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
    summary: str = "",
    score_context: dict[str, Any] | None = None,
    target_length: int | None = None,
) -> dict[str, Any]:
    context = score_context or {}
    brand = str(context.get("brand") or project["name"]).strip()
    keywords = (
        context["keywords"]
        if isinstance(context.get("keywords"), list)
        else _decode_string_list(project["keywords"])
    )
    explicit_target_length = _optional_target_length(target_length)
    if explicit_target_length is not None:
        # The editor/API choice becomes the new request, while an immutable
        # generation-run platform cap still defines what can safely publish.
        explicit_context = resolve_explicit_scoring_length_context(
            explicit_target_length,
            str(context.get("target_platform") or ""),
            platform_limit=_optional_target_length(context.get("platform_limit")),
        )
        effective_target_length = explicit_context["effective_length"]
        requested_target_length = explicit_context["requested_length"]
        length_source = explicit_context["source"]
    else:
        effective_target_length = (
            context.get("effective_target_length")
            or context.get("target_length")
        )
        requested_target_length = (
            context.get("requested_target_length")
            or effective_target_length
        )
        length_source = context.get("length_source")
    result = score_geo_content(
        title=title,
        content=content,
        brand=brand,
        keywords=keywords,
        target_length=effective_target_length,
        requested_target_length=requested_target_length,
        source=length_source,
    )
    result["citeability"] = score_ai_citeability(
        title=title,
        summary=summary,
        content=content,
        brand=brand,
        product=str(context.get("product") or project["product"] or "").strip(),
        website=str(context.get("website") or project["website"] or "").strip(),
        keywords=keywords,
        sources=(
            context["sources"]
            if isinstance(context.get("sources"), list)
            else ()
        ),
        target_length=effective_target_length,
        requested_target_length=requested_target_length,
        source=length_source,
    )
    return result


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
        SELECT project_id, project_snapshot, brief_snapshot, result_payload,
               status, article_id
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
    result_payload = _decode_json_object(row["result_payload"])
    sources = result_payload.get("sources")
    keywords = brief_snapshot.get("keywords")
    if not isinstance(keywords, list):
        keywords = project_snapshot.get("keywords")
    length_context = _generation_length_context(brief_snapshot)
    return {
        "brand": str(project_snapshot.get("name") or "").strip(),
        "keywords": _decode_string_list(keywords),
        "product": str(project_snapshot.get("product") or "").strip(),
        "website": str(project_snapshot.get("website") or "").strip(),
        "target_platform": str(brief_snapshot.get("target_platform") or "").strip(),
        "target_length": length_context["effective_length"],
        "requested_target_length": length_context["requested_length"],
        "effective_target_length": length_context["effective_length"],
        "platform_limit": length_context["platform_limit"],
        "length_source": length_context["source"],
        # Generation sources are immutable provenance stored in the existing
        # result receipt. Reusing them keeps pre-save, saved and later edited
        # citeability scores stable without adding another article column.
        "sources": sources if isinstance(sources, list) else [],
    }


def _serialize_article(row: sqlite3.Row) -> dict[str, Any]:
    article = dict(row)
    article["tags"] = _decode_string_list(article.get("tags"))
    article["geo_analysis"] = _decode_json_object(article.get("geo_analysis"))
    article["content_fingerprint"] = article_content_fingerprint(article)
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
            "diversity_profile": generation_result.get("generation", {}).get(
                "diversity_profile", {}
            )
            if isinstance(generation_result.get("generation"), dict)
            else {},
            "ai_role": generation_result.get("generation", {}).get("ai_role", {})
            if isinstance(generation_result.get("generation"), dict)
            else {},
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
    analysis = {
        "base_score": {
            "version": str(score_result.get("version") or GEO_SCORE_VERSION),
            "score": int(score_result["score"]),
        },
        "dimensions": score_result["dimensions"],
        "suggestions": score_result["suggestions"],
    }
    if isinstance(score_result.get("length_profile"), dict):
        analysis["length_profile"] = score_result["length_profile"]
    if isinstance(score_result.get("citeability"), dict):
        analysis["citeability"] = score_result["citeability"]
    return _encode_json(analysis)


def _optional_target_length(value: Any) -> int | None:
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        return None
    return normalized if 200 <= normalized <= 3_000 else None


def _stored_length_context(analysis: Any) -> dict[str, Any]:
    if not isinstance(analysis, dict):
        return {}
    profile = analysis.get("length_profile")
    if not isinstance(profile, dict):
        return {}
    requested = _optional_target_length(
        profile.get(
            "requested_target_length",
            profile.get("target_length", profile.get("targetLength")),
        )
    )
    effective = _optional_target_length(
        profile.get(
            "effective_target_length",
            profile.get("target_length", profile.get("targetLength")),
        )
    )
    if requested is None and effective is None:
        return {}
    requested = requested or effective
    effective = effective or requested
    return {
        "requested_target_length": requested,
        "effective_target_length": effective,
        "length_source": str(profile.get("source") or "explicit"),
    }


def _generation_length_context(brief: dict[str, Any]) -> dict[str, Any]:
    stored = brief.get("length_context")
    if isinstance(stored, dict):
        requested = _optional_target_length(stored.get("requested_length"))
        effective = _optional_target_length(stored.get("effective_length"))
        raw_limit = stored.get("platform_limit")
        platform_limit = _optional_target_length(raw_limit) if raw_limit is not None else None
        if (
            requested is not None
            and effective is not None
            and effective <= requested
            and (platform_limit is None or effective <= platform_limit)
        ):
            return {
                "requested_length": requested,
                "effective_length": effective,
                "platform_limit": platform_limit,
                "source": str(stored.get("source") or "requested_length"),
            }
    requested = _optional_target_length(
        brief.get("requested_length", brief.get("length"))
    )
    if requested is None:
        return {
            "requested_length": None,
            "effective_length": None,
            "platform_limit": None,
            "source": "legacy",
        }
    return resolve_generation_length_context(
        requested,
        str(brief.get("target_platform") or ""),
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
