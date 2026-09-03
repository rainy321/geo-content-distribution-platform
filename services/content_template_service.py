from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any


class ContentTemplateNotFoundError(LookupError):
    """Raised when a content template does not exist."""


class BuiltinTemplateMutationError(RuntimeError):
    """Raised when a built-in template is edited or deleted."""


def list_content_templates(database_path: str | Path) -> list[dict[str, Any]]:
    with closing(_connect(database_path)) as conn:
        rows = conn.execute(
            """
            SELECT * FROM content_templates
            ORDER BY is_builtin DESC, id ASC
            """
        ).fetchall()
    return [_serialize(row) for row in rows]


def get_content_template(
    database_path: str | Path,
    template_id: int,
) -> dict[str, Any]:
    with closing(_connect(database_path)) as conn:
        row = conn.execute(
            "SELECT * FROM content_templates WHERE id = ?",
            (template_id,),
        ).fetchone()
    if row is None:
        raise ContentTemplateNotFoundError("内容模板不存在")
    return _serialize(row)


def create_content_template(
    database_path: str | Path,
    *,
    name: str,
    description: str,
    content_type: str,
    instruction: str,
) -> dict[str, Any]:
    try:
        with closing(_connect(database_path)) as conn:
            with conn:
                cursor = conn.execute(
                    """
                    INSERT INTO content_templates (
                        name, description, content_type, instruction
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (name, description, content_type, instruction),
                )
                row = conn.execute(
                    "SELECT * FROM content_templates WHERE id = ?",
                    (cursor.lastrowid,),
                ).fetchone()
    except sqlite3.IntegrityError as exc:
        raise ValueError("内容模板名称已存在") from exc
    return _serialize(row)


def update_content_template(
    database_path: str | Path,
    template_id: int,
    changes: dict[str, str],
) -> dict[str, Any]:
    with closing(_connect(database_path)) as conn:
        with conn:
            current = conn.execute(
                "SELECT * FROM content_templates WHERE id = ?",
                (template_id,),
            ).fetchone()
            if current is None:
                raise ContentTemplateNotFoundError("内容模板不存在")
            if current["is_builtin"]:
                raise BuiltinTemplateMutationError("内置模板不能修改，可复制后编辑")
            values = {
                "name": current["name"],
                "description": current["description"],
                "content_type": current["content_type"],
                "instruction": current["instruction"],
                **changes,
            }
            try:
                conn.execute(
                    """
                    UPDATE content_templates
                    SET name = ?, description = ?, content_type = ?,
                        instruction = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (
                        values["name"],
                        values["description"],
                        values["content_type"],
                        values["instruction"],
                        template_id,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("内容模板名称已存在") from exc
            row = conn.execute(
                "SELECT * FROM content_templates WHERE id = ?",
                (template_id,),
            ).fetchone()
    return _serialize(row)


def delete_content_template(database_path: str | Path, template_id: int) -> None:
    with closing(_connect(database_path)) as conn:
        with conn:
            current = conn.execute(
                "SELECT is_builtin FROM content_templates WHERE id = ?",
                (template_id,),
            ).fetchone()
            if current is None:
                raise ContentTemplateNotFoundError("内容模板不存在")
            if current["is_builtin"]:
                raise BuiltinTemplateMutationError("内置模板不能删除")
            conn.execute(
                "DELETE FROM content_templates WHERE id = ?",
                (template_id,),
            )


def _connect(database_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(Path(database_path))
    conn.row_factory = sqlite3.Row
    return conn


def _serialize(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    result["is_builtin"] = bool(result["is_builtin"])
    return result
