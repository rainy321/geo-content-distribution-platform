from __future__ import annotations

import json
import ipaddress
import sqlite3
import re
from contextlib import closing
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


_MISSING = object()


def _project_url(value, field, *, empty=False):
    if not isinstance(value, str):
        raise ValueError(f'{field} 必须是字符串')
    value = value.strip()
    if empty and not value:
        return ''
    maximum = 300 if field == 'website' else 2000
    if len(value) > maximum:
        raise ValueError(f'{field} 不能超过 {maximum} 个字符')
    if (not value or any(c.isspace() or ord(c) < 32 for c in value)
            or any(marker in value for marker in ('?', '#', '\\'))):
        raise ValueError(f'{field} 必须是无账号、query 和 fragment 的完整 HTTP(S) 网址')
    try:
        parsed = urlsplit(value)
        if (parsed.scheme not in ('http', 'https') or not parsed.hostname
                or parsed.username is not None or parsed.password is not None
                or parsed.port is not None and not 1 <= parsed.port <= 65535):
            raise ValueError()
        # Invalid/empty ports and browser-normalized backslashes must not later
        # change the destination scope. Preserve legitimate explicit ports/path.
        if parsed.netloc.endswith(':'):
            raise ValueError()
        host = parsed.hostname.encode('idna').decode('ascii')
        try:
            ipaddress.ip_address(host)
        except ValueError:
            labels = host.rstrip('.').split('.')
            if len(host) > 253 or not all(re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?', label) for label in labels):
                raise ValueError()
    except (ValueError, UnicodeError) as error:
        raise ValueError(f'{field} 必须是无账号、query 和 fragment 的 HTTP(S) 网址，端口为 1–65535') from error
    # Import lazily so project CRUD does not establish an import-time cycle
    # through GEO planning. Store the user's exact URL; canonical identities are
    # shared only for validation and later scheme/host/port/path comparisons.
    from services.geo_test_service import canonical_entity_url
    if canonical_entity_url(value) is None:
        raise ValueError(f'{field} 包含无效域名、percent 编码或路径范围；不能包含编码斜杠、反斜杠或 dot 路径段')
    return value


def _identity_list(value, field, maximum, *, urls=False):
    if not isinstance(value, list) or len(value) > maximum:
        raise ValueError(f'{field} 必须是最多 {maximum} 项的字符串数组')
    result = []
    for item in value:
        if not isinstance(item, str) or not item.strip() or (not urls and len(item.strip()) > 120):
            raise ValueError(f'{field} 每项必须是非空字符串' + ('，最长 120 字' if not urls else ''))
        text = _project_url(item, field) if urls else item.strip()
        if text not in result:
            result.append(text)
    return result


def normalize_project_identity_fields(values, *, partial=False, validate_website=True):
    """Shared validation for API requests and service callers, without I/O."""
    result = {}
    if not partial or 'entity_type' in values:
        kind = values.get('entity_type', 'product')
        if not isinstance(kind, str) or kind.strip() not in ('company', 'product'):
            raise ValueError('entity_type 必须是 company 或 product')
        result['entity_type'] = kind.strip()
    for field, limit in (('company', 300), ('audience', 300)):
        if partial and field not in values:
            continue
        text = values.get(field, '')
        if not isinstance(text, str) or len(text.strip()) > limit:
            raise ValueError(f'{field} 必须是最多 {limit} 字的字符串')
        result[field] = text.strip()
    for field, maximum, urls in (('aliases', 20, False), ('official_urls', 10, True)):
        if not partial or field in values:
            result[field] = _identity_list(values.get(field, []), field, maximum, urls=urls)
    if not partial or 'website' in values:
        website = values.get('website', '')
        result['website'] = _project_url(website, 'website', empty=True) if validate_website else website
    if not partial and result['entity_type'] == 'product' and result['company']:
        fold = lambda text: re.sub(r'\s+', '', text).casefold()
        if any(fold(alias) == fold(result['company']) for alias in result['aliases']):
            raise ValueError('product 的 aliases 只能填写当前产品别名，不能使用运营公司的全称')
    return result


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
    entity_type: str = 'product',
    company: str = '',
    audience: str = '',
    aliases: Any = _MISSING,
    official_urls: Any = _MISSING,
) -> dict[str, Any]:
    identity = normalize_project_identity_fields({'website': website, 'entity_type': entity_type,
        'company': company, 'audience': audience, 'aliases': [] if aliases is _MISSING else aliases,
        'official_urls': [] if official_urls is _MISSING else official_urls})
    with closing(_connect(database_path)) as conn:
        with conn:
            cursor = conn.execute(
                """
                INSERT INTO projects (
                    name, website, product, industry, description,
                    keywords, competitors, entity_type, company, audience, aliases, official_urls
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    name,
                    identity['website'],
                    product,
                    industry,
                    description,
                    _encode_list(keywords),
                    _encode_list(competitors),
                    identity['entity_type'], identity['company'], identity['audience'],
                    _encode_list(identity['aliases']), _encode_list(identity['official_urls']),
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
            conn.execute('BEGIN IMMEDIATE')
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
                **{field: changes.get(field, current[field]) for field in
                   ('entity_type', 'company', 'audience', 'aliases', 'official_urls')},
            }
            values.update(normalize_project_identity_fields(values, validate_website='website' in changes))
            identity_changed = any(values[field] != current[field] for field in ('name', 'entity_type', 'website'))
            if identity_changed:
                has_history = any(conn.execute(f'SELECT 1 FROM {table} WHERE project_id=? LIMIT 1', (project_id,)).fetchone()
                                  for table in ('articles', 'geo_content_cycles', 'geo_distribution_targets'))
                if has_history:
                    from services.geo_entity_identity import same_entity_target
                    if not same_entity_target(current, values):
                        raise ValueError('该项目已有文章、采集周期或投放目标，不能更换评测对象名称、类型或主官网；请另建项目并保留原历史')
            conn.execute(
                """
                UPDATE projects
                SET name = ?, website = ?, product = ?, industry = ?,
                    description = ?, keywords = ?, competitors = ?,
                    entity_type = ?, company = ?, audience = ?, aliases = ?, official_urls = ?,
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
                    values['entity_type'], values['company'], values['audience'],
                    _encode_list(values['aliases']), _encode_list(values['official_urls']),
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
            research_count = conn.execute('SELECT COUNT(*) FROM geo_content_cycles WHERE project_id=?', (project_id,)).fetchone()[0]
            if research_count:
                raise ProjectHasArticlesError('项目下仍有关联的采集内容任务单，不能删除')
            if (conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='geo_external_baseline_plans'").fetchone()
                    and conn.execute('SELECT COUNT(*) FROM geo_external_baseline_plans WHERE project_id=?', (project_id,)).fetchone()[0]):
                raise ProjectHasArticlesError('项目下仍有已冻结的外部问题计划，历史记录保留，不能删除')
            if conn.execute('SELECT COUNT(*) FROM geo_distribution_targets WHERE project_id=?', (project_id,)).fetchone()[0]:
                raise ProjectHasArticlesError('项目下仍有平台接入记录，不能删除')
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
    project['entity_type'] = project.get('entity_type', 'product')
    project['company'] = project.get('company', '')
    project['audience'] = project.get('audience', '')
    project['aliases'] = _decode_list(project.get('aliases'))
    project['official_urls'] = _decode_list(project.get('official_urls'))
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
