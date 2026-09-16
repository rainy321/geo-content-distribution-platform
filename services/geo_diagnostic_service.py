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
from services.geo_article_compiler_service import (
    AI_CITEABILITY_SCORE_VERSION,
    score_ai_citeability,
)
from services.geo_score_service import GEO_SCORE_VERSION, score_geo_content
from services.target_length_score_profile import TARGET_LENGTH_SCORE_PROFILE_VERSION
from services.platform_capability_service import list_platform_capabilities
from services.publish_preflight_service import filter_sources_for_outbound_copy


GEO_DIAGNOSTIC_VERSION = "geo-commercial-diagnostic-1.3.0"
CHANNEL_RECOMMENDATION_VERSION = "geo-channel-fit-rules-1.0.0"
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


# These are product-side channel archetypes, not claims about a platform's
# ranking algorithm or an official traffic weight.  The deterministic fit
# score below only orders a customer's own controlled publishing experiments.
_CHANNEL_ARCHETYPES: dict[str, dict[str, Any]] = {
    "zhihu": {
        "label": "高意图问答与决策解释",
        "objective": "覆盖用户会直接向 AI 提问的选型、原理和对比问题",
        "content_angle": "问题式标题 + 先给结论 + 边界条件 + 可核验来源",
        "segment_bonus": {"technology_b2b": 20, "general": 8},
    },
    "toutiao": {
        "label": "行业解释与大众信息流图文",
        "objective": "扩展品类问题、使用场景和行业解释的内容覆盖",
        "content_angle": "行业问题 + 分步骤说明 + 事实依据；话题不匹配时直接跳过",
        "segment_bonus": {"technology_b2b": 12, "consumer_lifestyle": 8, "general": 10},
    },
    "baijiahao": {
        "label": "搜索型图文与产品知识",
        "objective": "验证品牌词、品类词和问题词的图文解释入口",
        "content_angle": "实体名称一致 + 图文步骤 + FAQ + 稳定官网来源",
        "segment_bonus": {"technology_b2b": 12, "local_service": 8, "general": 10},
    },
    "sohu": {
        "label": "企业知识与行业观点",
        "objective": "沉淀克制、可核验的企业产品说明和行业知识",
        "content_angle": "弱营销表达 + 完整事实链 + 来源说明 + 风险边界",
        "segment_bonus": {"technology_b2b": 15, "general": 7},
    },
    "xiaohongshu": {
        "label": "场景化图文笔记",
        "objective": "验证具体使用场景、体验步骤和视觉素材的表达效果",
        "content_angle": "单一场景 + 短段落 + 实际步骤 + 配图；避免绝对化承诺",
        "segment_bonus": {"consumer_lifestyle": 22, "local_service": 12, "general": 4},
    },
    "douyin": {
        "label": "短图文或短视频演示",
        "objective": "验证一个问题、一个结论、一个演示的短内容表达",
        "content_angle": "前置结论 + 单一演示 + 简短描述；发布验证由人工完成",
        "segment_bonus": {"video_demo": 18, "consumer_lifestyle": 15, "general": 3},
    },
    "kuaishou": {
        "label": "短内容与实操演示",
        "objective": "验证通俗问题解答和可复现操作步骤",
        "content_angle": "口语化结论 + 实操过程 + 明确适用范围",
        "segment_bonus": {"video_demo": 15, "consumer_lifestyle": 12, "local_service": 10, "general": 2},
    },
    "bilibili": {
        "label": "长视频教程与深度演示",
        "objective": "验证复杂产品教程、技术原理和完整操作演示",
        "content_angle": "章节化脚本 + 真实演示 + 简介摘要 + 可核验链接",
        "segment_bonus": {"technology_b2b": 12, "video_demo": 20, "general": 3},
    },
    "channels": {
        "label": "社交关系链中的产品演示",
        "objective": "验证已有私域受众对产品说明和演示内容的反馈",
        "content_angle": "短演示 + 明确受众 + 可执行下一步；不把互动量当作 AI 收录证明",
        "segment_bonus": {"local_service": 14, "video_demo": 12, "consumer_lifestyle": 8, "general": 2},
    },
    "tiktok": {
        "label": "国际化短视频试验",
        "objective": "仅在存在海外市场、外语内容和国际受众时验证",
        "content_angle": "目标市场语言 + 单一场景演示 + 当地合规复核",
        "segment_bonus": {"international": 28, "video_demo": 8, "general": -18},
    },
    "weibo": {
        "label": "短帖式实体与事实印证",
        "objective": "用可公开核验的短帖补充品牌、产品实体名称、事实摘要和来源指向",
        "content_angle": "标题作首句 + 单一事实 + 实体全称 + 稳定来源；不把互动量当作 AI 收录证明",
        "segment_bonus": {
            "technology_b2b": 10,
            "consumer_lifestyle": 8,
            "local_service": 8,
            "general": 6,
        },
    },
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
            SELECT id, title, summary, content, geo_analysis
            FROM articles
            WHERE project_id = ?
            ORDER BY id
            """,
            (normalized_project_id,),
        ).fetchall()
        articles = _recompute_project_article_scores(
            project=dict(project),
            articles=[dict(row) for row in article_rows],
        )
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
            articles=articles,
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
                        "name": str(project["name"] or "").strip(),
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
                        "title_sha256": _sha256_text(str(row["title"] or "")),
                        "summary_sha256": _sha256_text(str(row["summary"] or "")),
                        "content_sha256": _sha256_text(str(row["content"] or "")),
                        "score_context": dict(row["score_context"]),
                        "recomputed_geo_score": int(row["recomputed_geo_score"]),
                        "score_version": str(row["score_version"]),
                    }
                    for row in articles
                ],
                "score_basis": "current_rules_recomputed",
                "score_version": GEO_SCORE_VERSION,
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
            content_summary = article["summary"] if article is not None else ""
            if article is not None:
                citeability_sources, evidence_source_summary = (
                    _bound_generation_sources_for_diagnostic(
                        conn,
                        article_id=int(article["id"]),
                        project_id=normalized_project_id,
                        content=str(content_body),
                    )
                )
            else:
                citeability_sources = []
                evidence_source_summary = _empty_evidence_source_summary(
                    receipt_status="ad_hoc_no_receipt"
                )
            stored_length_context = (
                _stored_score_length_context(article["geo_analysis"])
                if article is not None
                else {}
            )
            target_length = stored_length_context.get("effective_target_length")
            result = _diagnose_content(
                project=dict(project),
                title=str(content_title),
                summary=str(content_summary or ""),
                content=str(content_body),
                project_result=project_result,
                source_type=source_type,
                article_id=normalized_article_id,
                target_length=target_length,
                requested_target_length=stored_length_context.get(
                    "requested_target_length"
                ),
                length_source=stored_length_context.get("source"),
                sources=citeability_sources,
                evidence_source_summary=evidence_source_summary,
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
                        "product": str(project["product"] or "").strip(),
                        "website": str(project["website"] or "").strip(),
                        "keywords": _decode_string_list(project["keywords"]),
                        "project_readiness_score": project_result.get("score"),
                        "target_length": target_length,
                        "evidence_source_summary": evidence_source_summary,
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
        max(0, min(100, int(row.get("recomputed_geo_score") or 0)))
        for row in articles
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
    result = _base_result(
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
            "score_basis": "current_rules_recomputed",
            "score_version": GEO_SCORE_VERSION,
            "latest_visibility_run_id": int(visibility_row["id"]) if visibility_row else None,
            "latest_visibility_created_at": visibility_row["created_at"] if visibility_row else None,
        },
    )
    result["channel_strategy"] = _build_channel_strategy(
        project=project,
        scores=scores,
        article_count=len(articles),
        visibility_summary=visibility_summary,
        visibility_row=visibility_row,
    )
    result["length_profile_distribution"] = _length_profile_distribution(articles)
    return result


def _diagnose_content(
    *,
    project: Mapping[str, Any],
    title: str,
    summary: str,
    content: str,
    project_result: Mapping[str, Any],
    source_type: str,
    article_id: int | None,
    target_length: int | None,
    requested_target_length: int | None,
    length_source: str | None,
    sources: list[Any],
    evidence_source_summary: Mapping[str, Any],
) -> dict[str, Any]:
    score_result = score_geo_content(
        title=title,
        content=content,
        brand=str(project.get("name") or "").strip(),
        keywords=_decode_string_list(project.get("keywords")),
        target_length=target_length,
        requested_target_length=requested_target_length,
        source=length_source,
    )
    citeability = score_ai_citeability(
        title=title,
        summary=summary,
        content=content,
        brand=str(project.get("name") or "").strip(),
        product=str(project.get("product") or "").strip(),
        website=str(project.get("website") or "").strip(),
        keywords=_decode_string_list(project.get("keywords")),
        sources=sources,
        target_length=target_length,
        requested_target_length=requested_target_length,
        source=length_source,
    )
    dimensions = [
        _dimension(key, _content_dimension_label(key), score_result["dimensions"].get(key), {
            "weight": CONTENT_DIMENSION_WEIGHTS[key],
        })
        for key in CONTENT_DIMENSION_WEIGHTS
    ]
    citeability_passed = bool(citeability.get("passed"))
    publish_gate_passed = bool(
        citeability.get("publish_gate_passed", citeability_passed)
    )
    gaps = _sort_dedupe_gaps(
        [*_content_gaps(score_result), *_citeability_gaps(citeability)]
    )
    legacy_score = int(score_result["score"])
    result = _base_result(
        project_id=int(project["id"]),
        scope="content",
        source_type=source_type,
        # Keep the original five-dimension content score stable for existing
        # consumers. Publication readiness is a separate gate below: a high
        # legacy score must never imply that citeability checks passed.
        score=legacy_score,
        dimensions=dimensions,
        gaps=gaps,
        algorithm_versions={
            "diagnostic": GEO_DIAGNOSTIC_VERSION,
            "content_score": GEO_SCORE_VERSION,
            "ai_citeability": AI_CITEABILITY_SCORE_VERSION,
            "channel_fit": CHANNEL_RECOMMENDATION_VERSION,
        },
        baseline={
            "article_id": article_id,
            "title_characters": len(title),
            "content_characters": len(content),
            "project_readiness_score": project_result.get("score"),
            "ai_citeability_score": citeability["score"],
            "ai_citeability_passed": citeability_passed,
            "publish_gate_passed": publish_gate_passed,
            "evidence_source_summary": dict(evidence_source_summary),
        },
    )
    result["publish_ready"] = publish_gate_passed
    result["readiness_status"] = "ready" if publish_gate_passed else "blocked"
    success_definition = result["measurement_plan"]["success_definition"]
    success_definition.update(
        {
            "publish_ready": True,
            "ai_citeability_score_at_least": int(citeability["pass_score"]),
        }
    )
    result["measurement_plan"]["current_status"] = {
        "score_target_met": legacy_score >= int(success_definition["score_at_least"]),
        "p0_target_met": result["gap_counts"]["P0"]
        <= int(success_definition["p0_gap_count"]),
        "ai_citeability_score": int(citeability["score"]),
        "ai_citeability_passed": citeability_passed,
        "publish_ready": publish_gate_passed,
        "success_definition_met": bool(
            legacy_score >= int(success_definition["score_at_least"])
            and result["gap_counts"]["P0"]
            <= int(success_definition["p0_gap_count"])
            and citeability_passed
        ),
    }
    result["score_suggestions"] = list(score_result.get("suggestions") or [])
    result["length_profile"] = dict(score_result.get("length_profile") or {})
    result["citeability"] = citeability
    result["evidence_source_summary"] = dict(evidence_source_summary)
    result["channel_strategy"] = _content_channel_strategy(
        project_result.get("channel_strategy"),
        publish_ready=publish_gate_passed,
        citeability_score=int(citeability["score"]),
    )
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


def _build_channel_strategy(
    *,
    project: Mapping[str, Any],
    scores: Mapping[str, int],
    article_count: int,
    visibility_summary: Mapping[str, Any],
    visibility_row: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Rank controlled channel experiments without claiming platform weight.

    Every point in ``fit_score`` is traceable to local project readiness, the
    latest imported/sampled visibility evidence, a declared audience segment,
    or this repository's runtime capability status.  The score is deliberately
    named *fit*, not weight, reach or expected lift.
    """

    segments = _project_segments(project)
    visibility_available = visibility_row is not None
    sample_count = int(_safe_number(visibility_summary.get("sample_count")))
    mention_rate = _safe_ratio(visibility_summary.get("mention_rate"))
    owned_citation_rate = _safe_ratio(
        visibility_summary.get("owned_citation_rate")
    )
    content_readiness = int(scores.get("content_readiness") or 0)
    recommendations: list[dict[str, Any]] = []

    for capability in list_platform_capabilities():
        platform = str(capability["key"])
        archetype = _CHANNEL_ARCHETYPES[platform]
        components: list[dict[str, Any]] = [
            {
                "signal": "controlled_experiment_baseline",
                "points": 24,
                "reason": "所有支持渠道从同一中性起点比较，避免把产品内排序伪装成平台权重。",
            }
        ]

        release_status = str(capability.get("release_status") or "")
        release_points = {
            "stable": 12,
            "experimental": -6,
            "review_required": -10,
        }.get(release_status, -8)
        components.append(
            {
                "signal": "local_runtime_readiness",
                "points": release_points,
                "reason": {
                    "stable": "本机适配标记为 stable，可进入受控试验。",
                    "experimental": "本机适配仍为 experimental，需要人工核对结果。",
                    "review_required": "本机适配要求人工复核，不能按全自动成功理解。",
                }.get(release_status, "本机适配状态未确认，先人工验证。"),
            }
        )

        content_mode = str(capability.get("content_mode") or "")
        is_long_form = content_mode in {"article", "image_article"}
        is_visual = content_mode in {"image_note", "video"}
        if content_readiness < 80 and is_long_form:
            components.append(
                {
                    "signal": "content_readiness_gap",
                    "points": 16,
                    "reason": "当前内容资产就绪度未达 80，优先用结构化长文补实体、FAQ 与来源。",
                }
            )
        elif article_count >= 3 and is_visual:
            components.append(
                {
                    "signal": "repurpose_ready",
                    "points": 8,
                    "reason": "已有至少 3 篇内容，可在不新增事实的前提下改写为视觉内容试验。",
                }
            )

        if not visibility_available:
            if is_long_form:
                components.append(
                    {
                        "signal": "visibility_baseline_missing",
                        "points": 8,
                        "reason": "尚无 AI 可见度基线；长文渠道只作为基线完成后的首批候选。",
                    }
                )
        else:
            if mention_rate < 0.4:
                components.append(
                    {
                        "signal": "mention_rate_gap",
                        "points": 14 if is_long_form else 6,
                        "reason": "最近样本的品牌提及率低于 40%，优先验证可直接回答高意图问题的内容。",
                    }
                )
            if owned_citation_rate < 0.2 and is_long_form:
                components.append(
                    {
                        "signal": "owned_citation_gap",
                        "points": 12,
                        "reason": "最近样本的自有来源引用率低于 20%，优先验证可承载稳定来源的长文。",
                    }
                )

        segment_points = 0
        matched_segments: list[str] = []
        for segment in segments:
            points = int(archetype["segment_bonus"].get(segment, 0))
            if points:
                segment_points += points
                matched_segments.append(segment)
        if not matched_segments:
            segment_points = int(archetype["segment_bonus"].get("general", 0))
            matched_segments = ["general"]
        components.append(
            {
                "signal": "declared_audience_fit",
                "points": segment_points,
                "reason": f"项目字段匹配受众场景：{'、'.join(_segment_label(item) for item in matched_segments)}。",
            }
        )

        fit_score = max(0, min(100, sum(int(item["points"]) for item in components)))
        risks = [
            "平台动态审核、账号状态、收录与推荐不可由本软件保证。",
            "渠道适配度不等于搜索或大模型平台权重。",
        ]
        if capability.get("requires_images"):
            risks.append("需要人工确认图片版权、清晰度和平台展示效果。")
        if capability.get("requires_video"):
            risks.append("需要人工确认视频文件、封面、字幕和最终发布状态。")
        if release_status != "stable":
            risks.append("本机发布能力尚非 stable，必须保留人工复核步骤。")

        recommendations.append(
            {
                "platform": platform,
                "platform_name": str(capability["name"]),
                "fit_score": fit_score,
                "fit_label": str(archetype["label"]),
                "stage": _channel_stage(
                    fit_score=fit_score,
                    visibility_available=visibility_available,
                    release_status=release_status,
                ),
                "objective": str(archetype["objective"]),
                "content_angle": str(archetype["content_angle"]),
                "fit_components": components,
                "evidence": {
                    "visibility_run_id": (
                        int(visibility_row["id"]) if visibility_row else None
                    ),
                    "visibility_sample_count": sample_count,
                    "mention_rate": mention_rate if visibility_available else None,
                    "owned_citation_rate": (
                        owned_citation_rate if visibility_available else None
                    ),
                    "content_readiness_score": content_readiness,
                    "article_count": article_count,
                    "content_mode": content_mode,
                    "local_release_status": release_status,
                },
                "validation": {
                    "test_unit": "一个问题簇的一组唯一内容，不做跨平台原文复制作为归因依据。",
                    "metrics": [
                        "mention_rate",
                        "owned_citation_rate",
                        "share_of_voice",
                    ],
                    "target": "相对同口径基线改善；没有基线时先建立基线，不预填虚假提升比例。",
                    "timing": "内容公开可访问且可检索后复测；短期结果必须标记为初步观察。",
                },
                "risks": risks,
            }
        )

    recommendations.sort(
        key=lambda item: (-int(item["fit_score"]), str(item["platform"]))
    )
    for rank, item in enumerate(recommendations, start=1):
        item["rank"] = rank

    return {
        "version": CHANNEL_RECOMMENDATION_VERSION,
        "evaluated_platform_count": len(recommendations),
        "segments": segments,
        "baseline_required": not visibility_available,
        "recommendations": recommendations,
        "measurement_protocol": {
            "before": "固定问题集、模型、模型版本与样本数，保存首次回答作为基线。",
            "change": "一次只验证一个渠道和一个内容簇，记录公开 URL 与发布时间。",
            "after": "内容公开可访问且可检索后，用同一问题集与采样口径复测。",
            "compare": ["mention_rate", "owned_citation_rate", "share_of_voice"],
            "attribution_limit": "观察到的变化只能记为相关性信号；模型更新、平台抓取节奏和其他内容都会影响结果。",
        },
        "claim_boundary": (
            "fit_score 仅为本软件依据项目字段、内容缺口、最近可见度样本与本机能力状态生成的试验优先级；"
            "它不是平台权重、流量预测、收录概率或审核通过承诺。"
        ),
    }


def _content_channel_strategy(
    strategy: Any,
    *,
    publish_ready: bool,
    citeability_score: int,
) -> dict[str, Any]:
    if not isinstance(strategy, Mapping):
        return {}
    result = {key: value for key, value in strategy.items() if key != "recommendations"}
    recommendations: list[dict[str, Any]] = []
    for raw in strategy.get("recommendations") or []:
        if not isinstance(raw, Mapping):
            continue
        item = dict(raw)
        if not publish_ready:
            item["stage"] = "content_preflight_blocked"
            item["next_action"] = "先修复当前内容的 AI 可引用性门禁，再进入任何渠道试验。"
        else:
            item["next_action"] = "按该渠道内容角度生成独立副本，并在发布前人工确认。"
        recommendations.append(item)
    result["recommendations"] = recommendations
    result["content_gate"] = {
        "publish_ready": publish_ready,
        "ai_citeability_score": citeability_score,
        "next_action": (
            "可以准备受控渠道试验，发布仍需人工确认。"
            if publish_ready
            else "当前文章不可进入渠道试验；先按 P0/P1 缺口优化并复测。"
        ),
    }
    return result


def _project_segments(project: Mapping[str, Any]) -> list[str]:
    text = " ".join(
        str(project.get(key) or "").casefold()
        for key in ("name", "product", "industry", "description")
    )
    definitions = {
        "technology_b2b": (
            "ai", "api", "saas", "软件", "模型", "人工智能", "开发者", "企业", "科技", "云服务", "技术"
        ),
        "consumer_lifestyle": (
            "消费", "生活", "美妆", "穿搭", "家居", "旅游", "母婴", "健康", "餐饮", "教育"
        ),
        "video_demo": ("视频", "直播", "游戏", "演示", "教程", "影音", "娱乐"),
        "local_service": ("本地", "门店", "到店", "同城", "线下", "地区", "餐饮"),
        "international": ("海外", "国际", "出海", "跨境", "global", "international"),
    }
    segments = [
        key for key, terms in definitions.items() if any(term in text for term in terms)
    ]
    return segments or ["general"]


def _segment_label(value: str) -> str:
    return {
        "technology_b2b": "技术/B2B",
        "consumer_lifestyle": "消费/生活方式",
        "video_demo": "视频演示",
        "local_service": "本地服务",
        "international": "国际化",
        "general": "通用",
    }.get(value, value)


def _channel_stage(
    *,
    fit_score: int,
    visibility_available: bool,
    release_status: str,
) -> str:
    if not visibility_available:
        return "baseline_then_pilot"
    if release_status != "stable" and fit_score >= 55:
        return "manual_review_pilot"
    if fit_score >= 65:
        return "priority_pilot"
    if fit_score >= 45:
        return "second_wave"
    return "observe"


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
        if score is None:
            continue
        numeric_score = int(score or 0)
        if numeric_score >= 80:
            continue
        priority = "P0" if numeric_score < 50 else "P1"
        title, action, target = actions.get(key, ("该维度需要优化", "按评分建议优化后复测。", f"{key} >= 80"))
        gaps.append(_gap(f"content.{key}", priority, key, title, action, target))
    return _sort_dedupe_gaps(gaps)


def _citeability_gaps(citeability: Mapping[str, Any]) -> list[dict[str, str]]:
    """Turn every failed publication gate into an actionable P0/P1 gap."""

    pass_score = int(citeability.get("pass_score") or 0)
    gaps: list[dict[str, str]] = []
    for issue in citeability.get("blocking_issues", []):
        if not isinstance(issue, Mapping):
            continue
        priority = str(issue.get("priority") or "P0").strip().upper()
        # Citeability is a publication gate, so a failed issue cannot be
        # downgraded to an informational P2 item by a future rule version.
        if priority not in {"P0", "P1"}:
            priority = "P0"
        gaps.append(
            _gap(
                f"citeability.{issue.get('code') or 'blocking'}",
                priority,
                "citeability",
                str(issue.get("message") or "AI 可引用性门禁未通过"),
                str(issue.get("action") or "按门禁提示修复后重新诊断。"),
                f"ai_citeability >= {pass_score} 且无 P0 阻断",
            )
        )

    if not bool(citeability.get("passed")) and not any(
        gap["priority"] in {"P0", "P1"} for gap in gaps
    ):
        suggestions = [
            str(item).strip()
            for item in citeability.get("suggestions", [])
            if str(item).strip()
        ]
        gaps.append(
            _gap(
                "citeability.score_below_threshold",
                "P1",
                "citeability",
                "AI 可引用性总分未达到发布门禁",
                suggestions[0] if suggestions else "按可引用性分维度建议优化后重新诊断。",
                f"ai_citeability >= {pass_score} 且无 P0 阻断",
            )
        )
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


def _dimension(
    key: str,
    label: str,
    score: int | None,
    evidence: Mapping[str, Any],
) -> dict[str, Any]:
    weight = PROJECT_DIMENSION_WEIGHTS.get(key, CONTENT_DIMENSION_WEIGHTS.get(key, 0))
    if score is None:
        return {
            "key": key,
            "label": label,
            "score": None,
            "weight": weight,
            "status": "not_applicable",
            "applicable": False,
            "evidence": {**dict(evidence), "applicable": False},
        }
    score = max(0, min(100, int(score)))
    return {
        "key": key,
        "label": label,
        "score": score,
        "weight": weight,
        "status": "ready" if score >= 80 else "attention" if score >= 50 else "critical",
        "applicable": True,
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
        "content_score": GEO_SCORE_VERSION,
        "channel_fit": CHANNEL_RECOMMENDATION_VERSION,
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


def _empty_evidence_source_summary(*, receipt_status: str) -> dict[str, Any]:
    return {
        "generation_run_id": None,
        "receipt_status": receipt_status,
        "provided_count": 0,
        "matched_count": 0,
        "visible_url_count": 0,
        "policy": "structured_claim_requires_url_in_exact_outbound_copy",
        "matched_sources_sha256": _sha256_text(_encode_json([])),
    }


def _bound_generation_sources_for_diagnostic(
    connection: sqlite3.Connection,
    *,
    article_id: int,
    project_id: int,
    content: str,
) -> tuple[list[Any], dict[str, Any]]:
    """Read only the succeeded immutable receipt bound to this article.

    The run result remains the source of structured claims, while the shared
    outbound-copy filter requires the corresponding public URL to remain in
    the article body.  No model or network request is made here.
    """

    row = connection.execute(
        """
        SELECT id, status, result_payload
        FROM content_generation_runs
        WHERE article_id = ? AND project_id = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (article_id, project_id),
    ).fetchone()
    if row is None:
        return [], _empty_evidence_source_summary(receipt_status="not_bound")

    run_id = int(row["id"])
    status = str(row["status"] or "unknown")
    if status != "succeeded":
        summary = _empty_evidence_source_summary(receipt_status=status)
        summary["generation_run_id"] = run_id
        return [], summary

    result_payload = _decode_object(row["result_payload"])
    raw_sources = result_payload.get("sources")
    receipt_sources = raw_sources if isinstance(raw_sources, list) else []
    matched_sources, filter_summary = filter_sources_for_outbound_copy(
        receipt_sources,
        content,
    )
    summary = {
        "generation_run_id": run_id,
        "receipt_status": status,
        **filter_summary,
        "matched_sources_sha256": _sha256_text(_encode_json(matched_sources)),
    }
    return matched_sources, summary


def _stored_score_length_context(value: Any) -> dict[str, Any]:
    analysis = _decode_object(value)
    profile = analysis.get("length_profile")
    if not isinstance(profile, Mapping):
        return {}

    def normalize(raw: Any) -> int | None:
        if isinstance(raw, bool):
            return None
        try:
            target = int(raw)
        except (TypeError, ValueError):
            return None
        return target if 200 <= target <= 3000 else None

    requested = normalize(
        profile.get("requested_target_length", profile.get("target_length"))
    )
    effective = normalize(
        profile.get("effective_target_length", profile.get("target_length"))
    )
    return {
        "requested_target_length": requested,
        "effective_target_length": effective,
        "source": str(profile.get("source") or "explicit"),
    }


def _recompute_project_article_scores(
    *,
    project: Mapping[str, Any],
    articles: list[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    brand = str(project.get("name") or "").strip()
    keywords = _decode_string_list(project.get("keywords"))
    recomputed: list[dict[str, Any]] = []
    for article in articles:
        score_context = _stored_score_length_context(article.get("geo_analysis"))
        score_result = score_geo_content(
            title=str(article.get("title") or ""),
            content=str(article.get("content") or ""),
            brand=brand,
            keywords=keywords,
            target_length=score_context.get("effective_target_length"),
            requested_target_length=score_context.get("requested_target_length"),
            source=score_context.get("source"),
        )
        recomputed.append(
            {
                **dict(article),
                "score_context": score_context,
                "recomputed_geo_score": int(score_result["score"]),
                "score_version": str(score_result.get("version") or GEO_SCORE_VERSION),
            }
        )
    return recomputed


def _length_profile_distribution(
    articles: list[Mapping[str, Any]],
) -> dict[str, Any]:
    labels = {
        "legacy": "兼容长文规则",
        "compact": "精简稿（200–400字）",
        "short": "短稿（401–799字）",
        "standard": "标准稿（800–1199字）",
        "long": "长稿（1200–3000字）",
        "unknown": "未记录长度档位",
    }
    buckets: dict[str, dict[str, Any]] = {}
    for article in articles:
        analysis = _decode_object(article.get("geo_analysis"))
        profile = analysis.get("length_profile")
        key = (
            str(profile.get("key") or "").strip().lower()
            if isinstance(profile, Mapping)
            else ""
        )
        if key not in labels:
            key = "unknown"
        bucket = buckets.setdefault(
            key,
            {"key": key, "label": labels[key], "count": 0, "target_lengths": set()},
        )
        bucket["count"] += 1
        target = _stored_score_length_context(analysis).get(
            "requested_target_length"
        )
        if target is not None:
            bucket["target_lengths"].add(target)

    ordered_keys = ["compact", "short", "standard", "long", "legacy", "unknown"]
    profiles = [
        {
            **{name: value for name, value in buckets[key].items() if name != "target_lengths"},
            "target_lengths": sorted(buckets[key]["target_lengths"]),
        }
        for key in ordered_keys
        if key in buckets
    ]
    known_keys = {item["key"] for item in profiles if item["key"] != "unknown"}
    return {
        "version": TARGET_LENGTH_SCORE_PROFILE_VERSION,
        "total_articles": len(articles),
        "profiles": profiles,
        "mixed_profiles": len(known_keys) > 1,
        "comparison_policy": "长度档位会改变适用结构项；只建议比较同一长度档位的分数与变化。",
    }


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
    "CHANNEL_RECOMMENDATION_VERSION",
    "GEO_DIAGNOSTIC_VERSION",
    "GEODiagnosticError",
    "GEODiagnosticNotFoundError",
    "diagnose_geo_readiness",
    "get_geo_diagnostic_run",
    "list_geo_diagnostic_runs",
]
