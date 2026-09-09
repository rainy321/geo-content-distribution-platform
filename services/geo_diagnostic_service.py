"""Deterministic, auditable GEO commercial-diagnostic workflow.

The service turns existing project data, local GEO scoring and the latest
user-supplied AI visibility run into a measurable baseline and prioritized
actions. It never calls an external model and never persists ad-hoc content.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any, Mapping

from services.ai_visibility_service import AI_VISIBILITY_ALGORITHM_VERSION
from services.geo_score_service import score_geo_content


GEO_DIAGNOSTIC_VERSION = "geo-commercial-diagnostic-1.0.1"
PROJECT_DIMENSION_WEIGHTS = {
    "positioning": 20,
    "discoverability": 20,
    "competition": 15,
    "content_readiness": 25,
    "ai_visibility": 20,
}
CONTENT_DIMENSION_WEIGHTS = {
    "entity": 15,
    "keywords": 30,
    "structure": 35,
    "faq": 10,
    "citation": 10,
}


class GEODiagnosticError(ValueError):
    """Raised when diagnostic input is invalid or its project is missing."""


class GEODiagnosticNotFoundError(LookupError):
    """Raised when a persisted diagnostic run cannot be found."""


def diagnose_geo_readiness(
    database_path: str | Path,
    *,
    project_id: int,
    article_id: int | None = None,
    title: str | None = None,
    content: str | None = None,
    persist: bool = True,
) -> dict[str, Any]:
    """Create a project or content baseline with a repeatable improvement plan."""

    normalized_project_id = _positive_int(project_id, "project_id")
    if not isinstance(persist, bool):
        raise GEODiagnosticError("persist 必须是布尔值")
    with closing(_connect(database_path)) as conn:
        project = conn.execute(
            "SELECT * FROM projects WHERE id = ?", (normalized_project_id,)
        ).fetchone()
        if project is None:
            raise GEODiagnosticError("品牌项目不存在")

        article = None
        source_type = "project"
        if article_id not in (None, ""):
            normalized_article_id = _positive_int(article_id, "article_id")
            article = conn.execute(
                "SELECT * FROM articles WHERE id = ? AND project_id = ?",
                (normalized_article_id, normalized_project_id),
            ).fetchone()
            if article is None:
                raise GEODiagnosticError("文章不存在或不属于该项目")
            source_type = "article"
            if title not in (None, "") or content not in (None, ""):
                raise GEODiagnosticError("article_id 不能与临时 title/content 同时提供")
        elif title not in (None, "") or content not in (None, ""):
            normalized_article_id = None
            title = _required_text(title, "title", 500)
            content = _required_text(content, "content", 250_000)
            source_type = "ad_hoc"
        else:
            normalized_article_id = None

        article_rows = conn.execute(
            """
            SELECT id, geo_score, geo_analysis
            FROM articles
            WHERE project_id = ?
            ORDER BY id
            """,
            (normalized_project_id,),
        ).fetchall()
        visibility_row = conn.execute(
            """
            SELECT id, result_json, algorithm_version, created_at
            FROM ai_visibility_runs
            WHERE project_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (normalized_project_id,),
        ).fetchone()
        visibility = _decode_object(visibility_row["result_json"]) if visibility_row else {}

        project_result = _diagnose_project(
            project=dict(project),
            articles=[dict(row) for row in article_rows],
            visibility=visibility,
            visibility_row=dict(visibility_row) if visibility_row else None,
        )
        if source_type == "project":
            result = project_result
            digest_payload = {
                "project_id": normalized_project_id,
                # Hash the normalized values consumed by the rules directly.
                # ``updated_at`` has second-level precision in SQLite and can
                # remain unchanged across a create/edit pair, so it is not a
                # safe audit fingerprint by itself.
                "project_inputs_sha256": _digest_json(
                    {
                        "product": str(project["product"] or "").strip(),
                        "industry": str(project["industry"] or "").strip(),
                        "description": str(project["description"] or "").strip(),
                        "website": str(project["website"] or "").strip(),
                        "keywords": _decode_string_list(project["keywords"]),
                        "competitors": _decode_string_list(project["competitors"]),
                    }
                ),
                "articles": [
                    {
                        "id": int(row["id"]),
                        "geo_score": int(row["geo_score"] or 0),
                        "geo_analysis_sha256": _sha256_text(
                            str(row["geo_analysis"] or "")
                        ),
                    }
                    for row in article_rows
                ],
                "visibility_run_id": visibility_row["id"] if visibility_row else None,
                "visibility_algorithm_version": (
                    visibility_row["algorithm_version"] if visibility_row else None
                ),
                "visibility_result_sha256": (
                    _sha256_text(str(visibility_row["result_json"] or ""))
                    if visibility_row
                    else None
                ),
            }
            scope = "project"
        else:
            content_title = article["title"] if article is not None else title
            content_body = article["content"] if article is not None else content
            result = _diagnose_content(
                project=dict(project),
                title=str(content_title),
                content=str(content_body),
                project_result=project_result,
                source_type=source_type,
                article_id=normalized_article_id,
            )
            digest_payload = {
                "project_id": normalized_project_id,
                "article_id": normalized_article_id,
                "title_sha256": _sha256_text(str(content_title)),
                "content_sha256": _sha256_text(str(content_body)),
                # Content scoring uses the project's normalized brand and
                # keyword list, while the visible baseline also includes the
                # current project-readiness score.  Keep those effective
                # inputs in the digest so equal digests can never describe
                # different diagnostic results after a project edit.
                "content_scoring_context_sha256": _digest_json(
                    {
                        "brand": str(project["name"] or "").strip(),
                        "keywords": _decode_string_list(project["keywords"]),
                        "project_readiness_score": project_result.get("score"),
                    }
                ),
            }
            scope = "content"

        result["input_digest"] = _digest_json(digest_payload)
        if persist:
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                cursor = conn.execute(
                    """
                    INSERT INTO geo_diagnostic_runs (
                        project_id, article_id, scope, source_type,
                        input_digest, result_json, diagnostic_version
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        normalized_project_id,
                        normalized_article_id,
                        scope,
                        source_type,
                        result["input_digest"],
                        _encode_json(result),
                        GEO_DIAGNOSTIC_VERSION,
                    ),
                )
                saved = conn.execute(
                    "SELECT id, created_at FROM geo_diagnostic_runs WHERE id = ?",
                    (cursor.lastrowid,),
                ).fetchone()
            result["run_id"] = int(saved["id"])
            result["created_at"] = saved["created_at"]
        return result


def list_geo_diagnostic_runs(
    database_path: str | Path,
    *,
    project_id: int,
    limit: int = 20,
) -> list[dict[str, Any]]:
    normalized_project_id = _positive_int(project_id, "project_id")
    normalized_limit = _bounded_limit(limit)
    with closing(_connect(database_path)) as conn:
        if conn.execute(
            "SELECT 1 FROM projects WHERE id = ?", (normalized_project_id,)
        ).fetchone() is None:
            raise GEODiagnosticError("品牌项目不存在")
        rows = conn.execute(
            """
            SELECT id, project_id, article_id, scope, source_type,
                   input_digest, result_json, diagnostic_version, created_at
            FROM geo_diagnostic_runs
            WHERE project_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (normalized_project_id, normalized_limit),
        ).fetchall()
    output = []
    for row in rows:
        result = _decode_object(row["result_json"])
        output.append(
            {
                "id": int(row["id"]),
                "project_id": int(row["project_id"]),
                "article_id": row["article_id"],
                "scope": row["scope"],
                "source_type": row["source_type"],
                "score": result.get("score"),
                "grade": result.get("grade"),
                "gap_counts": result.get("gap_counts", {}),
                "diagnostic_version": row["diagnostic_version"],
                "created_at": row["created_at"],
            }
        )
    return output


def get_geo_diagnostic_run(
    database_path: str | Path,
    run_id: int,
) -> dict[str, Any]:
    normalized_run_id = _positive_int(run_id, "run_id")
    with closing(_connect(database_path)) as conn:
        row = conn.execute(
            "SELECT * FROM geo_diagnostic_runs WHERE id = ?", (normalized_run_id,)
        ).fetchone()
    if row is None:
        raise GEODiagnosticNotFoundError("诊断记录不存在")
    result = _decode_object(row["result_json"])
    if not result:
        raise GEODiagnosticNotFoundError("诊断记录数据不可读")
    result.update(
        {
            "run_id": int(row["id"]),
            "created_at": row["created_at"],
            "article_id": row["article_id"],
        }
    )
    return result


def _diagnose_project(
    *,
    project: Mapping[str, Any],
    articles: list[Mapping[str, Any]],
    visibility: Mapping[str, Any],
    visibility_row: Mapping[str, Any] | None,
) -> dict[str, Any]:
    keywords = _decode_string_list(project.get("keywords"))
    competitors = _decode_string_list(project.get("competitors"))
    description_length = len(str(project.get("description") or "").strip())
    positioning_score = (
        (30 if str(project.get("product") or "").strip() else 0)
        + (20 if str(project.get("industry") or "").strip() else 0)
        + (50 if description_length >= 100 else 35 if description_length >= 40 else 15 if description_length else 0)
    )
    discoverability_score = (30 if str(project.get("website") or "").strip() else 0) + min(
        70, round(len(keywords) / 5 * 70)
    )
    competition_score = min(100, round(len(competitors) / 3 * 100))
    article_scores = [
        max(0, min(100, int(row.get("geo_score") or 0))) for row in articles
    ]
    average_geo_score = round(sum(article_scores) / len(article_scores), 1) if article_scores else 0.0
    content_readiness_score = round(
        min(100, len(articles) / 3 * 100) * 0.4 + average_geo_score * 0.6
    )
    visibility_summary = visibility.get("summary") if isinstance(visibility.get("summary"), Mapping) else {}
    visibility_score = _visibility_score(visibility_summary) if visibility_row else 0

    scores = {
        "positioning": positioning_score,
        "discoverability": discoverability_score,
        "competition": competition_score,
        "content_readiness": content_readiness_score,
        "ai_visibility": visibility_score,
    }
    dimensions = [
        _dimension("positioning", "产品定位完整度", scores["positioning"], {
            "product_configured": bool(str(project.get("product") or "").strip()),
            "industry_configured": bool(str(project.get("industry") or "").strip()),
            "description_characters": description_length,
        }),
        _dimension("discoverability", "可发现性基础", scores["discoverability"], {
            "website_configured": bool(str(project.get("website") or "").strip()),
            "keyword_count": len(keywords),
            "keyword_target": 5,
        }),
        _dimension("competition", "竞品参照完整度", scores["competition"], {
            "competitor_count": len(competitors),
            "competitor_target": 3,
        }),
        _dimension("content_readiness", "内容资产就绪度", scores["content_readiness"], {
            "article_count": len(articles),
            "article_target": 3,
            "average_geo_score": average_geo_score,
        }),
        _dimension("ai_visibility", "AI 可见度基线", scores["ai_visibility"], {
            "visibility_run_id": int(visibility_row["id"]) if visibility_row else None,
            "sample_count": visibility_summary.get("sample_count"),
            "mention_rate": visibility_summary.get("mention_rate"),
            "owned_citation_rate": visibility_summary.get("owned_citation_rate"),
            "share_of_voice": visibility_summary.get("share_of_voice"),
            "confidence": (
                visibility_summary.get("confidence", {}).get("level")
                if isinstance(visibility_summary.get("confidence"), Mapping)
                else None
            ),
        }),
    ]
    score = round(
        sum(scores[key] * weight for key, weight in PROJECT_DIMENSION_WEIGHTS.items()) / 100
    )
    gaps = _project_gaps(
        project=project,
        keywords=keywords,
        competitors=competitors,
        article_count=len(articles),
        average_geo_score=average_geo_score,
        visibility=visibility,
        visibility_row=visibility_row,
    )
    return _base_result(
        project_id=int(project["id"]),
        scope="project",
        source_type="project",
        score=score,
        dimensions=dimensions,
        gaps=gaps,
        algorithm_versions=_algorithm_versions(visibility),
        baseline={
            "article_count": len(articles),
            "average_geo_score": average_geo_score,
            "latest_visibility_run_id": int(visibility_row["id"]) if visibility_row else None,
            "latest_visibility_created_at": visibility_row["created_at"] if visibility_row else None,
        },
    )


def _diagnose_content(
    *,
    project: Mapping[str, Any],
    title: str,
    content: str,
    project_result: Mapping[str, Any],
    source_type: str,
    article_id: int | None,
) -> dict[str, Any]:
    score_result = score_geo_content(
        title=title,
        content=content,
        brand=str(project.get("name") or "").strip(),
        keywords=_decode_string_list(project.get("keywords")),
    )
    dimensions = [
        _dimension(key, _content_dimension_label(key), int(score_result["dimensions"].get(key, 0)), {
            "weight": CONTENT_DIMENSION_WEIGHTS[key],
        })
        for key in CONTENT_DIMENSION_WEIGHTS
    ]
    gaps = _content_gaps(score_result)
    result = _base_result(
        project_id=int(project["id"]),
        scope="content",
        source_type=source_type,
        score=int(score_result["score"]),
        dimensions=dimensions,
        gaps=gaps,
        algorithm_versions={
            "diagnostic": GEO_DIAGNOSTIC_VERSION,
            "content_score": "geo-score-mvp-rules-1.0.0",
        },
        baseline={
            "article_id": article_id,
            "title_characters": len(title),
            "content_characters": len(content),
            "project_readiness_score": project_result.get("score"),
        },
    )
    result["score_suggestions"] = list(score_result.get("suggestions") or [])
    return result


def _base_result(
    *,
    project_id: int,
    scope: str,
    source_type: str,
    score: int,
    dimensions: list[dict[str, Any]],
    gaps: list[dict[str, Any]],
    algorithm_versions: Mapping[str, Any],
    baseline: Mapping[str, Any],
) -> dict[str, Any]:
    gap_counts = {
        priority: sum(gap["priority"] == priority for gap in gaps)
        for priority in ("P0", "P1", "P2")
    }
    return {
        "diagnostic_version": GEO_DIAGNOSTIC_VERSION,
        "project_id": project_id,
        "scope": scope,
        "source_type": source_type,
        "score": score,
        "grade": _grade(score),
        "dimensions": dimensions,
        "gaps": gaps,
        "gap_counts": gap_counts,
        "recommended_actions": [gap["action"] for gap in gaps],
        "baseline": dict(baseline),
        "measurement_plan": {
            "method": "完成 P0/P1 动作后，用同一项目和同一评分版本重新诊断并比较 score 与分维度差值。",
            "success_definition": {
                "score_at_least": 80,
                "p0_gap_count": 0,
                "comparison_requires_same_version": GEO_DIAGNOSTIC_VERSION,
            },
        },
        "methodology": {
            "engine": "deterministic-local-rules",
            "external_model_used": False,
            "customer_content_sent_externally": False,
            "claim_boundary": "分数衡量当前配置、内容结构及已导入回答样本，不等同于搜索或大模型平台官方排名。",
        },
        "algorithm_versions": dict(algorithm_versions),
    }


def _project_gaps(
    *,
    project: Mapping[str, Any],
    keywords: list[str],
    competitors: list[str],
    article_count: int,
    average_geo_score: float,
    visibility: Mapping[str, Any],
    visibility_row: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    gaps: list[dict[str, Any]] = []
    if not str(project.get("product") or "").strip():
        gaps.append(_gap("positioning.product", "P0", "positioning", "缺少产品/服务定义", "补充客户实际销售的产品、服务对象与核心价值，作为后续内容实体基线。", "product 字段非空"))
    if not str(project.get("industry") or "").strip():
        gaps.append(_gap("positioning.industry", "P1", "positioning", "缺少行业语境", "补充行业与目标客户，避免生成内容缺少明确场景。", "industry 字段非空"))
    if len(str(project.get("description") or "").strip()) < 40:
        gaps.append(_gap("positioning.description", "P1", "positioning", "业务描述不足", "补全目标用户、问题、能力边界和可信证据，建议至少 40 字。", "description >= 40 字"))
    if not keywords:
        gaps.append(_gap("discoverability.keywords", "P0", "discoverability", "没有核心关键词", "录入至少 3 个真实业务关键词，并覆盖品牌词、品类词与问题词。", "keyword_count >= 3"))
    elif len(keywords) < 3:
        gaps.append(_gap("discoverability.keywords", "P1", "discoverability", "关键词覆盖偏窄", "将关键词扩充到至少 3 个，覆盖品牌、品类和客户问题。", "keyword_count >= 3"))
    if not str(project.get("website") or "").strip():
        gaps.append(_gap("discoverability.website", "P1", "discoverability", "缺少自有来源入口", "配置官网或稳定产品页，便于后续衡量自有引用。", "website 字段非空"))
    if not competitors:
        gaps.append(_gap("competition.baseline", "P1", "competition", "没有竞品参照", "录入至少 1 个真实竞品，建议 3 个，以量化提及率和声量差距。", "competitor_count >= 1"))
    if article_count == 0:
        gaps.append(_gap("content.inventory", "P0", "content_readiness", "没有可评估内容资产", "导入或生成首批至少 3 篇内容，形成可重复评估的内容基线。", "article_count >= 3"))
    elif average_geo_score < 60:
        gaps.append(_gap("content.quality", "P0", "content_readiness", "现有内容平均 GEO 分偏低", "优先优化低分文章的实体、结构、FAQ 和引用，再重新评分。", "average_geo_score >= 70"))
    elif average_geo_score < 80:
        gaps.append(_gap("content.quality", "P1", "content_readiness", "内容仍有结构化优化空间", "按分维度缺口优化文章，将项目平均 GEO 分提升至 80。", "average_geo_score >= 80"))

    summary = visibility.get("summary") if isinstance(visibility.get("summary"), Mapping) else {}
    if visibility_row is None:
        gaps.append(_gap("visibility.baseline", "P0", "ai_visibility", "没有 AI 可见度基线", "使用合成或经授权的回答样本完成一次 AI 可见度分析；系统不会自行发送客户数据。", "visibility_sample_count >= 5"))
    else:
        sample_count = _safe_number(summary.get("sample_count"))
        mention_rate = _safe_ratio(summary.get("mention_rate"))
        owned_rate = _safe_ratio(summary.get("owned_citation_rate"))
        if sample_count < 5:
            gaps.append(_gap("visibility.samples", "P1", "ai_visibility", "回答样本不足以形成稳定结论", "补充同一问题下至少 5 条可比回答后复测。", "sample_count >= 5"))
        if mention_rate < 0.4:
            gaps.append(_gap("visibility.mentions", "P0", "ai_visibility", "品牌提及率偏低", "围绕高意图问题补充清晰实体、答案段落与可验证事实，然后用同问题复测。", "mention_rate >= 40%"))
        if owned_rate < 0.2:
            gaps.append(_gap("visibility.owned_citations", "P1", "ai_visibility", "自有来源引用不足", "在官网内容中补充稳定 URL、明确事实来源和可引用结构。", "owned_citation_rate >= 20%"))
        for item in (visibility.get("recommendation_details") or [])[:5]:
            if not isinstance(item, Mapping):
                continue
            priority_score = _safe_ratio(item.get("priority_score"))
            priority = "P0" if priority_score >= 0.65 else "P1" if priority_score >= 0.35 else "P2"
            title = str(item.get("title") or "可见度算法建议").strip()[:160]
            description = str(item.get("description") or title).strip()[:500]
            rule_id = str(item.get("rule_id") or "recommendation").strip()[:120]
            gaps.append(_gap(f"visibility.algorithm.{rule_id}", priority, "ai_visibility", title, description, "按同一问题和样本口径复测后提升对应指标"))
    return _sort_dedupe_gaps(gaps)


def _content_gaps(score_result: Mapping[str, Any]) -> list[dict[str, Any]]:
    actions = {
        "entity": ("品牌实体信号不足", "让品牌/产品名称在标题或正文中自然出现，并保持命名一致。", "entity >= 80"),
        "keywords": ("核心关键词覆盖不足", "补充缺失关键词到标题和正文，避免连续堆砌。", "keywords >= 80"),
        "structure": ("内容结构不利于机器提取", "补充 H2/H3、清晰段落、总结，并满足基础正文长度。", "structure >= 80"),
        "faq": ("缺少可直接引用的问答", "增加 2–3 个真实常见问题及简洁答案。", "faq >= 100"),
        "citation": ("可验证事实和来源不足", "增加可核实数字、来源名称或稳定外部链接，不编造证据。", "citation >= 80"),
    }
    gaps = []
    for key, score in score_result.get("dimensions", {}).items():
        numeric_score = int(score or 0)
        if numeric_score >= 80:
            continue
        priority = "P0" if numeric_score < 50 else "P1"
        title, action, target = actions.get(key, ("该维度需要优化", "按评分建议优化后复测。", f"{key} >= 80"))
        gaps.append(_gap(f"content.{key}", priority, key, title, action, target))
    return _sort_dedupe_gaps(gaps)


def _visibility_score(summary: Mapping[str, Any]) -> int:
    sample = min(1.0, _safe_number(summary.get("sample_count")) / 5)
    mention = _safe_ratio(summary.get("mention_rate"))
    owned = _safe_ratio(summary.get("owned_citation_rate"))
    sov_raw = summary.get("share_of_voice")
    sov = _safe_ratio(sov_raw) if sov_raw is not None else 0.0
    confidence = summary.get("confidence") if isinstance(summary.get("confidence"), Mapping) else {}
    confidence_score = {"HIGH": 1.0, "MEDIUM": 0.65, "LOW": 0.35}.get(
        str(confidence.get("level") or "").upper(), 0.0
    )
    return round((sample * 25 + mention * 30 + owned * 20 + sov * 15 + confidence_score * 10))


def _dimension(key: str, label: str, score: int, evidence: Mapping[str, Any]) -> dict[str, Any]:
    score = max(0, min(100, int(score)))
    weight = PROJECT_DIMENSION_WEIGHTS.get(key, CONTENT_DIMENSION_WEIGHTS.get(key, 0))
    return {
        "key": key,
        "label": label,
        "score": score,
        "weight": weight,
        "status": "ready" if score >= 80 else "attention" if score >= 50 else "critical",
        "evidence": dict(evidence),
    }


def _gap(key: str, priority: str, dimension: str, title: str, action: str, metric_target: str) -> dict[str, str]:
    return {
        "key": key,
        "priority": priority,
        "dimension": dimension,
        "title": title,
        "action": action,
        "metric_target": metric_target,
    }


def _sort_dedupe_gaps(gaps: list[dict[str, str]]) -> list[dict[str, str]]:
    priority_order = {"P0": 0, "P1": 1, "P2": 2}
    unique: dict[str, dict[str, str]] = {}
    for gap in gaps:
        unique.setdefault(gap["key"], gap)
    return sorted(unique.values(), key=lambda item: (priority_order[item["priority"]], item["key"]))


def _algorithm_versions(visibility: Mapping[str, Any]) -> dict[str, Any]:
    versions = visibility.get("algorithm_versions")
    return {
        "diagnostic": GEO_DIAGNOSTIC_VERSION,
        "content_score": "geo-score-mvp-rules-1.0.0",
        "visibility_integration": (
            versions.get("integration")
            if isinstance(versions, Mapping)
            else AI_VISIBILITY_ALGORITHM_VERSION
        ),
        "visibility_components": dict(versions) if isinstance(versions, Mapping) else {},
    }


def _content_dimension_label(key: str) -> str:
    return {
        "entity": "品牌实体",
        "keywords": "关键词覆盖",
        "structure": "内容结构",
        "faq": "问答结构",
        "citation": "事实与引用",
    }.get(key, key)


def _grade(score: int) -> str:
    if score >= 90:
        return "A"
    if score >= 80:
        return "B"
    if score >= 60:
        return "C"
    return "D"


def _decode_string_list(value: Any) -> list[str]:
    try:
        parsed = json.loads(value) if isinstance(value, str) else value
    except (TypeError, json.JSONDecodeError):
        return []
    if not isinstance(parsed, list):
        return []
    output = []
    for item in parsed:
        text = str(item or "").strip()
        if text and text not in output:
            output.append(text)
    return output


def _decode_object(value: Any) -> dict[str, Any]:
    try:
        parsed = json.loads(value) if isinstance(value, str) else value
    except (TypeError, json.JSONDecodeError):
        return {}
    return dict(parsed) if isinstance(parsed, Mapping) else {}


def _required_text(value: Any, field: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise GEODiagnosticError(f"{field} 必须是字符串")
    text = value.strip()
    if not text:
        raise GEODiagnosticError(f"{field} 不能为空")
    if "\0" in text or len(text) > maximum:
        raise GEODiagnosticError(f"{field} 超出支持长度")
    return text


def _positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise GEODiagnosticError(f"{field} 必须是正整数")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise GEODiagnosticError(f"{field} 必须是正整数") from exc
    if number <= 0 or str(value).strip() != str(number):
        raise GEODiagnosticError(f"{field} 必须是正整数")
    return number


def _bounded_limit(value: Any) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise GEODiagnosticError("limit 必须是正整数") from exc
    if not 1 <= number <= 100:
        raise GEODiagnosticError("limit 必须在 1 到 100 之间")
    return number


def _safe_number(value: Any) -> float:
    if isinstance(value, bool):
        return 0.0
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return 0.0


def _safe_ratio(value: Any) -> float:
    return min(1.0, _safe_number(value))


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _digest_json(value: Mapping[str, Any]) -> str:
    return _sha256_text(_encode_json(value))


def _encode_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _connect(database_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(Path(database_path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


__all__ = [
    "GEO_DIAGNOSTIC_VERSION",
    "GEODiagnosticError",
    "GEODiagnosticNotFoundError",
    "diagnose_geo_readiness",
    "get_geo_diagnostic_run",
    "list_geo_diagnostic_runs",
]
