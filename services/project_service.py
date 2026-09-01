from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any


class ProjectNotFoundError(LookupError):
    """Raised when a project id does not exist."""


class ProjectHasArticlesError(RuntimeError):
    """Raised when deleting a project would also remove articles."""


def create_project(
    database_path: str | Path,
    *,
    name: str,
    website: str,
    product: str,
    industry: str,
    description: str,
    keywords: list[str],
    competitors: list[str],
) -> dict[str, Any]:
    with closing(_connect(database_path)) as conn:
        with conn:
            cursor = conn.execute(
                """
                INSERT INTO projects (
                    name, website, product, industry, description,
                    keywords, competitors
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    name,
                    website,
                    product,
                    industry,
                    description,
                    _encode_list(keywords),
                    _encode_list(competitors),
                ),
            )
            row = _fetch_project(conn, cursor.lastrowid)
    return _serialize_project(row)


def list_projects(database_path: str | Path) -> list[dict[str, Any]]:
    with closing(_connect(database_path)) as conn:
        rows = conn.execute(
            """
            SELECT p.*, COUNT(a.id) AS article_count
            FROM projects AS p
            LEFT JOIN articles AS a ON a.project_id = p.id
            GROUP BY p.id
            ORDER BY p.updated_at DESC, p.id DESC
            """
        ).fetchall()
    return [_serialize_project(row) for row in rows]


def get_project(database_path: str | Path, project_id: int) -> dict[str, Any]:
    with closing(_connect(database_path)) as conn:
        row = conn.execute(
            """
            SELECT p.*, COUNT(a.id) AS article_count
            FROM projects AS p
            LEFT JOIN articles AS a ON a.project_id = p.id
            WHERE p.id = ?
            GROUP BY p.id
            """,
            (project_id,),
        ).fetchone()
    if row is None:
        raise ProjectNotFoundError("品牌项目不存在")
    return _serialize_project(row)


def update_project(
    database_path: str | Path,
    project_id: int,
    changes: dict[str, Any],
) -> dict[str, Any]:
    with closing(_connect(database_path)) as conn:
        with conn:
            current_row = _fetch_project(conn, project_id)
            if current_row is None:
                raise ProjectNotFoundError("品牌项目不存在")
            current = _serialize_project(current_row)
            values = {
                "name": changes.get("name", current["name"]),
                "website": changes.get("website", current["website"]),
                "product": changes.get("product", current["product"]),
                "industry": changes.get("industry", current["industry"]),
                "description": changes.get("description", current["description"]),
                "keywords": changes.get("keywords", current["keywords"]),
                "competitors": changes.get("competitors", current["competitors"]),
            }
            conn.execute(
                """
                UPDATE projects
                SET name = ?, website = ?, product = ?, industry = ?,
                    description = ?, keywords = ?, competitors = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    values["name"],
                    values["website"],
                    values["product"],
                    values["industry"],
                    values["description"],
                    _encode_list(values["keywords"]),
                    _encode_list(values["competitors"]),
                    project_id,
                ),
            )
            updated_row = _fetch_project(conn, project_id)
    return _serialize_project(updated_row)


def delete_project(database_path: str | Path, project_id: int) -> None:
    with closing(_connect(database_path)) as conn:
        with conn:
            row = _fetch_project(conn, project_id)
            if row is None:
                raise ProjectNotFoundError("品牌项目不存在")
            article_count = conn.execute(
                "SELECT COUNT(*) FROM articles WHERE project_id = ?",
                (project_id,),
            ).fetchone()[0]
            if article_count:
                raise ProjectHasArticlesError("项目下仍有文章，不能删除")
            conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))


def _connect(database_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(Path(database_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _fetch_project(conn: sqlite3.Connection, project_id: int):
    return conn.execute(
        """
        SELECT p.*,
               (SELECT COUNT(*) FROM articles AS a WHERE a.project_id = p.id)
                   AS article_count
        FROM projects AS p
        WHERE p.id = ?
        """,
        (project_id,),
    ).fetchone()


def _serialize_project(row: sqlite3.Row) -> dict[str, Any]:
    project = dict(row)
    project["keywords"] = _decode_list(project.get("keywords"))
    project["competitors"] = _decode_list(project.get("competitors"))
    return project


def _encode_list(value: list[str]) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _decode_list(value: Any) -> list[str]:
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
