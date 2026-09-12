"""Secret-free audit helpers for the content-agent pipeline.

The helpers are intentionally deterministic and database agnostic.  Generation
stores their output inside its existing result payload, so no live schema or
publishing task needs to be mutated to retain writer/reviewer/evaluator lineage.
"""

from __future__ import annotations

import difflib
import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any


AI_AGENT_PIPELINE_VERSION = "content-agent-pipeline-1.0.0"


def article_snapshot(value: Mapping[str, Any]) -> dict[str, Any]:
    """Return the bounded public article fields used by review agents."""

    tags = value.get("tags")
    faq = value.get("faq")
    sources = value.get("sources")
    return {
        "title": str(value.get("title") or "")[:200],
        "summary": str(value.get("summary") or "")[:4_000],
        "content": str(value.get("content") or "")[:30_000],
        "tags": [str(item)[:160] for item in tags[:50]]
        if isinstance(tags, Sequence) and not isinstance(tags, (str, bytes))
        else [],
        "faq": [dict(item) for item in faq[:30] if isinstance(item, Mapping)]
        if isinstance(faq, Sequence) and not isinstance(faq, (str, bytes))
        else [],
        "sources": [dict(item) for item in sources[:30] if isinstance(item, Mapping)]
        if isinstance(sources, Sequence) and not isinstance(sources, (str, bytes))
        else [],
    }


def article_digest(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        article_snapshot(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def article_diff(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
) -> dict[str, Any]:
    """Describe exactly which public fields changed plus a readable text diff."""

    left = article_snapshot(before)
    right = article_snapshot(after)
    changed_fields = [key for key in left if left[key] != right[key]]
    before_text = _article_as_text(left)
    after_text = _article_as_text(right)
    unified = "\n".join(
        difflib.unified_diff(
            before_text.splitlines(),
            after_text.splitlines(),
            fromfile="writer",
            tofile="reviewer",
            lineterm="",
            n=2,
        )
    )
    return {
        "changed": bool(changed_fields),
        "changed_fields": changed_fields,
        "before_sha256": article_digest(left),
        "after_sha256": article_digest(right),
        "unified_diff": unified[:30_000],
        "truncated": len(unified) > 30_000,
    }


def stage_run_id(generation_run_id: int, role: str, attempt: int = 1) -> str:
    normalized_role = str(role or "unknown").strip().lower() or "unknown"
    return f"generation:{int(generation_run_id)}:{normalized_role}:{max(1, int(attempt))}"


def summarize_model_independence(stages: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Count actually invoked model identities; fallback roles do not count."""

    identities: set[tuple[str, str]] = set()
    invoked_roles: list[str] = []
    for stage in stages:
        if not bool(stage.get("invoked")):
            continue
        provider = str(stage.get("provider") or "unknown").strip().casefold()
        model = str(stage.get("model") or "unknown").strip().casefold()
        identities.add((provider, model))
        role = str(stage.get("role") or "unknown").strip().lower()
        if role not in invoked_roles:
            invoked_roles.append(role)
    return {
        "invoked_roles": invoked_roles,
        "invoked_stage_count": len(invoked_roles),
        "distinct_model_count": len(identities),
        "multi_agent_executed": len(invoked_roles) >= 2,
        "multi_model_executed": len(identities) >= 2,
        "claim_boundary": (
            "multi_model_executed 仅在至少两个实际调用阶段使用不同 provider/model 时为 true；"
            "未配置角色和 writer 回退不会计入。"
        ),
    }


def _article_as_text(value: Mapping[str, Any]) -> str:
    return "\n".join(
        [
            f"TITLE: {value['title']}",
            f"SUMMARY: {value['summary']}",
            "CONTENT:",
            str(value["content"]),
            "TAGS: " + json.dumps(value["tags"], ensure_ascii=False, sort_keys=True),
            "FAQ: " + json.dumps(value["faq"], ensure_ascii=False, sort_keys=True),
            "SOURCES: "
            + json.dumps(value["sources"], ensure_ascii=False, sort_keys=True),
        ]
    )


__all__ = [
    "AI_AGENT_PIPELINE_VERSION",
    "article_diff",
    "article_digest",
    "article_snapshot",
    "stage_run_id",
    "summarize_model_independence",
]
