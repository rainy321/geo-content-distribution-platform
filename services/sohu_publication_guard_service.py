from __future__ import annotations

import math
import re
import sqlite3
from collections import Counter
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping


SOHU_PUBLICATION_GUARD_VERSION = "sohu-publication-guard-1.3.0"
SOHU_TITLE_SIMILARITY_BLOCK = 0.65
SOHU_CONTENT_SIMILARITY_BLOCK = 0.40
SOHU_LOOKBACK_DAYS = 30

_NORMALIZE_PATTERN = re.compile(r"[^0-9a-z\u4e00-\u9fff]+", re.IGNORECASE)
_VERSION_WHITESPACE_PATTERN = re.compile(r"\s+")
_UNRESOLVED_STATUSES = frozenset({"queued", "scheduled", "processing", "need_action"})
_EXPLICIT_PLATFORM_REJECTION_PREFIX = "后台只读监测发现平台明确未通过："


class SohuPublicationGuardError(ValueError):
    """Raised before persistence when the Sohu account guard blocks a job."""

    def __init__(self, report: Mapping[str, Any]):
        self.report = dict(report)
        messages = [
            str(item.get("message") or "").strip()
            for item in self.report.get("issues", ())
            if isinstance(item, Mapping) and str(item.get("message") or "").strip()
        ]
        super().__init__("；".join(messages[:3]) or "搜狐账号发布保护未通过")


def evaluate_sohu_account_publication_guard(
    database_path: str | Path,
    *,
    account_id: int,
    article_id: int | None = None,
    title: str,
    content: str,
    now: datetime | None = None,
    exclude_job_ids: Iterable[int] = (),
) -> dict[str, Any]:
    """Block only explainable Sohu batch/retry risks before creating a job.

    Thresholds are product-side conservative controls, not claims about Sohu's
    unpublished moderation weights.  The function is local and read-only.
    """

    normalized_account_id = _positive_integer(account_id, "account_id")
    if article_id is not None:
        _positive_integer(article_id, "article_id")
    excluded = {_positive_integer(value, "exclude_job_ids") for value in exclude_job_ids}
    observed_at = _utc(now)
    with closing(sqlite3.connect(Path(database_path), timeout=30)) as conn:
        conn.row_factory = sqlite3.Row
        references = _recent_sohu_jobs_from_connection(
            conn,
            account_id=normalized_account_id,
            observed_at=observed_at,
        )
        explicit_rejections = _explicit_sohu_rejections_from_connection(conn)
    return _evaluate_sohu_account_publication_guard_from_references(
        references,
        explicit_rejections=explicit_rejections,
        excluded=excluded,
        observed_at=observed_at,
        title=title,
        content=content,
    )


def evaluate_sohu_account_publication_guard_in_connection(
    conn: sqlite3.Connection,
    *,
    account_id: int,
    article_id: int | None = None,
    title: str,
    content: str,
    now: datetime | None = None,
    exclude_job_ids: Iterable[int] = (),
) -> dict[str, Any]:
    """Run the same guard on an existing transaction-owned connection."""

    normalized_account_id = _positive_integer(account_id, "account_id")
    if article_id is not None:
        _positive_integer(article_id, "article_id")
    excluded = {_positive_integer(value, "exclude_job_ids") for value in exclude_job_ids}
    observed_at = _utc(now)
    references = _recent_sohu_jobs_from_connection(
        conn,
        account_id=normalized_account_id,
        observed_at=observed_at,
    )
    explicit_rejections = _explicit_sohu_rejections_from_connection(conn)
    return _evaluate_sohu_account_publication_guard_from_references(
        references,
        explicit_rejections=explicit_rejections,
        excluded=excluded,
        observed_at=observed_at,
        title=title,
        content=content,
    )


def _evaluate_sohu_account_publication_guard_from_references(
    references: list[dict[str, Any]],
    *,
    explicit_rejections: Iterable[Mapping[str, Any]] = (),
    excluded: set[int],
    observed_at: datetime,
    title: str = "",
    content: str = "",
) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []

    rejected_same_version = [
        item
        for item in explicit_rejections
        if _optional_positive_integer(item.get("job_id")) not in excluded
        and _same_publication_version(
            title=title,
            content=content,
            reference_title=item.get("title"),
            reference_content=item.get("content"),
        )
    ]
    if rejected_same_version:
        related_job_ids = [
            int(item["job_id"])
            for item in rejected_same_version
            if _optional_positive_integer(item.get("job_id")) is not None
        ]
        job_codes = "、".join(
            f"JOB-{job_id:05d}" for job_id in related_job_ids[:3]
        )
        issues.append(
            _issue(
                "sohu_explicit_rejection_same_article_version",
                "blocking",
                (
                    f"搜狐已明确拒绝当前文章版本（{job_codes}）；"
                    "切换搜狐账号不会改变该平台终态。"
                ),
                (
                    "不要复制或重试拒稿快照；必须实质修改标题或正文，"
                    "保存修订版本并重新通过搜狐预检后再提交。"
                ),
                related_job_ids=related_job_ids,
            )
        )

    unresolved = [
        item
        for item in references
        if item["job_id"] not in excluded
        and item["status"] in _UNRESOLVED_STATUSES
        and (
            item["status"] != "need_action"
            or bool(item.get("reconciliation_required"))
        )
    ]
    if unresolved:
        job_codes = "、".join(
            f"JOB-{item['job_id']:05d}" for item in unresolved[:3]
        )
        issues.append(
            _issue(
                "sohu_unresolved_prior_submission",
                "blocking",
                (
                    f"该搜狐账号仍有 {len(unresolved)} 条未收口任务（{job_codes}）；"
                    "继续投递会把平台处理中、拒稿和重复提交混在一起。"
                ),
                "先让只读监测取得通过/未通过结果，或人工核验并收口；软件不会自动重发。",
                related_job_ids=[item["job_id"] for item in unresolved],
            )
        )

    similarity_matches = evaluate_sohu_similarity(
        title=title,
        content=content,
        references=(item for item in references if item["job_id"] not in excluded),
    )
    for match in similarity_matches:
        issues.append(
            _issue(
                "sohu_recent_title_similarity"
                if match["title_similarity"] >= SOHU_TITLE_SIMILARITY_BLOCK
                else "sohu_recent_content_similarity",
                "blocking",
                (
                    f"与近 {SOHU_LOOKBACK_DAYS} 天 JOB-{match['job_id']:05d} 高度相似："
                    f"标题 {match['title_similarity']:.3f}，正文 {match['content_similarity']:.3f}。"
                ),
                "不要只换同义词；改成不同用户问题、独立证据和不同结论结构后再预检。",
                related_job_ids=[match["job_id"]],
                metrics={
                    "title_similarity": match["title_similarity"],
                    "content_similarity": match["content_similarity"],
                },
            )
        )

    recent_day = [
        item
        for item in references
        if item["job_id"] not in excluded
        and item.get("created_at") is not None
        and observed_at - item["created_at"] <= timedelta(hours=24)
    ]
    if recent_day:
        issues.append(
            _issue(
                "sohu_conservative_cooldown",
                "blocking",
                (
                    f"该账号最近 24 小时已创建 {len(recent_day)} 条搜狐任务；"
                    "当前启用一稿一审的产品侧保守冷却。"
                ),
                "等待上一稿审核收口且至少间隔 24 小时；这不是搜狐官方配额。",
                related_job_ids=[item["job_id"] for item in recent_day],
            )
        )

    return {
        "version": SOHU_PUBLICATION_GUARD_VERSION,
        "passed": not any(item["severity"] == "blocking" for item in issues),
        "issues": _dedupe_issues(issues),
        "reference_count": len(references),
        "platform_rejection_reference_count": len(rejected_same_version),
        "lookback_days": SOHU_LOOKBACK_DAYS,
        "thresholds": {
            "title_similarity": SOHU_TITLE_SIMILARITY_BLOCK,
            "content_similarity": SOHU_CONTENT_SIMILARITY_BLOCK,
            "basis": "product_conservative_guardrail",
        },
        "disclaimer": (
            "相似度和冷却线是软件侧保守策略，不是搜狐公开阈值，也不保证审核通过。"
        ),
    }


def evaluate_sohu_similarity(
    *,
    title: str,
    content: str,
    references: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for reference in references:
        job_id = _optional_positive_integer(reference.get("job_id"))
        if job_id is None:
            continue
        title_similarity = character_ngram_cosine(
            title,
            str(reference.get("title") or ""),
            n=2,
        )
        content_similarity = character_ngram_cosine(
            content,
            str(reference.get("content") or ""),
            n=5,
        )
        if (
            title_similarity < SOHU_TITLE_SIMILARITY_BLOCK
            and content_similarity < SOHU_CONTENT_SIMILARITY_BLOCK
        ):
            continue
        matches.append(
            {
                "job_id": job_id,
                "title_similarity": title_similarity,
                "content_similarity": content_similarity,
            }
        )
    return sorted(
        matches,
        key=lambda item: (
            max(item["title_similarity"], item["content_similarity"]),
            item["job_id"],
        ),
        reverse=True,
    )


def evaluate_sohu_batch_similarity(
    articles: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    normalized = []
    for position, article in enumerate(articles):
        article_id = _optional_positive_integer(article.get("article_id"))
        normalized.append(
            {
                "article_id": article_id,
                "position": position,
                "title": str(article.get("title") or "").strip(),
                "content": str(article.get("content") or "").strip(),
            }
        )
    issues = []
    if len(normalized) > 1:
        issues.append(
            _issue(
                "sohu_batch_one_at_a_time",
                "blocking",
                (
                    f"本批包含 {len(normalized)} 篇搜狐稿件；当前启用一稿一审的"
                    "产品侧保守门禁。"
                ),
                (
                    "搜狐每次只选择一篇，等只读监测返回通过/未通过后再提交下一篇；"
                    "这不是搜狐官方配额。"
                ),
                article_ids=[item["article_id"] for item in normalized],
            )
        )
    for left_index, left in enumerate(normalized):
        for right in normalized[left_index + 1 :]:
            title_similarity = character_ngram_cosine(
                left["title"], right["title"], n=2
            )
            content_similarity = character_ngram_cosine(
                left["content"], right["content"], n=5
            )
            if (
                title_similarity < SOHU_TITLE_SIMILARITY_BLOCK
                and content_similarity < SOHU_CONTENT_SIMILARITY_BLOCK
            ):
                continue
            labels = [
                str(left["article_id"] or left["position"] + 1),
                str(right["article_id"] or right["position"] + 1),
            ]
            issues.append(
                _issue(
                    "sohu_batch_similarity",
                    "blocking",
                    (
                        f"本批稿件 {labels[0]} 与 {labels[1]} 高度相似："
                        f"标题 {title_similarity:.3f}，正文 {content_similarity:.3f}。"
                    ),
                    "搜狐一次只提交一篇实质独立稿件；不要批量发送同一问题的改写版。",
                    article_ids=[left["article_id"], right["article_id"]],
                    metrics={
                        "title_similarity": title_similarity,
                        "content_similarity": content_similarity,
                    },
                )
            )
    return {
        "version": SOHU_PUBLICATION_GUARD_VERSION,
        "passed": not any(item["severity"] == "blocking" for item in issues),
        "issues": _dedupe_issues(issues),
        "thresholds": {
            "title_similarity": SOHU_TITLE_SIMILARITY_BLOCK,
            "content_similarity": SOHU_CONTENT_SIMILARITY_BLOCK,
            "basis": "product_conservative_guardrail",
        },
        "disclaimer": (
            "一稿一审、相似度和冷却线均为软件侧保守策略，不是搜狐公开阈值，"
            "也不保证审核通过。"
        ),
    }


def character_ngram_cosine(left: Any, right: Any, *, n: int) -> float:
    left_text = _NORMALIZE_PATTERN.sub("", str(left or "").casefold())
    right_text = _NORMALIZE_PATTERN.sub("", str(right or "").casefold())
    if not left_text or not right_text:
        return 0.0
    left_grams = _ngrams(left_text, n)
    right_grams = _ngrams(right_text, n)
    if not left_grams or not right_grams:
        return 1.0 if left_text == right_text else 0.0
    numerator = sum(value * right_grams.get(key, 0) for key, value in left_grams.items())
    denominator = math.sqrt(sum(value * value for value in left_grams.values())) * math.sqrt(
        sum(value * value for value in right_grams.values())
    )
    return round(numerator / denominator, 6) if denominator else 0.0


def _recent_sohu_jobs_from_connection(
    conn: sqlite3.Connection,
    *,
    account_id: int,
    observed_at: datetime,
) -> list[dict[str, Any]]:
    earliest = observed_at - timedelta(days=SOHU_LOOKBACK_DAYS)
    has_monitor_table = conn.execute(
        """
        SELECT 1 FROM sqlite_master
        WHERE type = 'table' AND name = 'publish_review_monitors'
        """
    ).fetchone() is not None
    if has_monitor_table:
        rows = conn.execute(
            """
            SELECT j.id AS job_id, j.status, j.reconciliation_required,
                   j.publish_title, j.publish_content, j.created_at,
                   a.title AS article_title, a.content AS article_content,
                   m.state AS review_state, m.last_result AS review_result
            FROM publish_jobs AS j
            JOIN articles AS a ON a.id = j.article_id
            LEFT JOIN publish_review_monitors AS m ON m.publish_job_id = j.id
            WHERE j.platform = 'sohu'
              AND j.account_id = ?
              AND j.demo = 0
              AND j.created_at >= ?
              AND (
                    j.status IN ('queued', 'scheduled', 'processing', 'success', 'need_action')
                    OR (
                        j.status = 'failed'
                        AND (
                            m.state = 'rejected'
                            OR m.last_result = 'rejected'
                            OR j.message LIKE '后台只读监测发现平台明确未通过：%'
                        )
                    )
              )
            ORDER BY j.created_at DESC, j.id DESC
            LIMIT 100
            """,
            (
                account_id,
                earliest.astimezone(timezone.utc).replace(tzinfo=None).isoformat(
                    sep=" ", timespec="seconds"
                ),
            ),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT j.id AS job_id, j.status, j.reconciliation_required,
                   j.publish_title, j.publish_content, j.created_at,
                   a.title AS article_title, a.content AS article_content,
                   '' AS review_state, '' AS review_result
            FROM publish_jobs AS j
            JOIN articles AS a ON a.id = j.article_id
            WHERE j.platform = 'sohu'
              AND j.account_id = ?
              AND j.demo = 0
              AND j.created_at >= ?
              AND (
                    j.status IN ('queued', 'scheduled', 'processing', 'success', 'need_action')
                    OR (
                        j.status = 'failed'
                        AND j.message LIKE '后台只读监测发现平台明确未通过：%'
                    )
              )
            ORDER BY j.created_at DESC, j.id DESC
            LIMIT 100
            """,
            (
                account_id,
                earliest.astimezone(timezone.utc).replace(tzinfo=None).isoformat(
                    sep=" ", timespec="seconds"
                ),
            ),
        ).fetchall()
    return [
        {
            "job_id": int(row["job_id"]),
            "status": str(row["status"] or ""),
            "reconciliation_required": bool(row["reconciliation_required"]),
            "title": str(row["publish_title"] or row["article_title"] or "").strip(),
            "content": str(row["publish_content"] or row["article_content"] or "").strip(),
            "created_at": _parse_sqlite_timestamp(row["created_at"]),
            "review_state": str(row["review_state"] or ""),
            "review_result": str(row["review_result"] or ""),
        }
        for row in rows
    ]


def _explicit_sohu_rejections_from_connection(
    conn: sqlite3.Connection,
) -> list[dict[str, Any]]:
    """Return every provable Sohu rejection snapshot across all local accounts.

    Similarity and cadence remain account-scoped.  Explicit rejection follows
    the exact submitted title/content snapshot rather than ``article_id`` so
    copying unchanged text into a new record or switching accounts cannot turn
    a known rejected version into a new submission.
    """

    has_monitor_table = conn.execute(
        """
        SELECT 1 FROM sqlite_master
        WHERE type = 'table' AND name = 'publish_review_monitors'
        """
    ).fetchone() is not None
    if has_monitor_table:
        rows = conn.execute(
            """
            SELECT j.id AS job_id, j.publish_title, j.publish_content
            FROM publish_jobs AS j
            LEFT JOIN publish_review_monitors AS m ON m.publish_job_id = j.id
            WHERE j.platform = 'sohu'
              AND j.demo = 0
              AND j.status = 'failed'
              AND (
                    j.message LIKE ?
                    OR m.state = 'rejected'
                    OR m.last_result = 'rejected'
              )
            ORDER BY j.id DESC
            """,
            (f"{_EXPLICIT_PLATFORM_REJECTION_PREFIX}%",),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT j.id AS job_id, j.publish_title, j.publish_content
            FROM publish_jobs AS j
            WHERE j.platform = 'sohu'
              AND j.demo = 0
              AND j.status = 'failed'
              AND j.message LIKE ?
            ORDER BY j.id DESC
            """,
            (f"{_EXPLICIT_PLATFORM_REJECTION_PREFIX}%",),
        ).fetchall()
    return [
        {
            "job_id": int(row["job_id"]),
            "title": str(row["publish_title"] or "").strip(),
            "content": str(row["publish_content"] or "").strip(),
        }
        for row in rows
    ]


def _same_publication_version(
    *,
    title: Any,
    content: Any,
    reference_title: Any,
    reference_content: Any,
) -> bool:
    current_title = _canonical_version_text(title)
    current_content = _canonical_version_text(content)
    rejected_title = _canonical_version_text(reference_title)
    rejected_content = _canonical_version_text(reference_content)
    # Empty legacy snapshots cannot prove which article version was rejected.
    return bool(
        current_title
        and current_content
        and rejected_title
        and rejected_content
        and current_title == rejected_title
        and current_content == rejected_content
    )


def _canonical_version_text(value: Any) -> str:
    return _VERSION_WHITESPACE_PATTERN.sub(
        " ",
        str(value or "").replace("\u00a0", " "),
    ).strip()


def _ngrams(value: str, n: int) -> Counter[str]:
    if len(value) < n:
        return Counter({value: 1}) if value else Counter()
    return Counter(value[index : index + n] for index in range(len(value) - n + 1))


def _issue(code: str, severity: str, message: str, suggestion: str, **extra: Any) -> dict[str, Any]:
    return {
        "code": code,
        "severity": severity,
        "message": message,
        "suggestion": suggestion,
        **extra,
    }


def _dedupe_issues(issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    seen = set()
    for issue in issues:
        identity = (issue.get("code"), tuple(issue.get("related_job_ids") or ()))
        if identity in seen:
            continue
        seen.add(identity)
        result.append(issue)
    return result


def _positive_integer(value: Any, field: str) -> int:
    normalized = _optional_positive_integer(value)
    if normalized is None:
        raise ValueError(f"{field} 必须是正整数")
    return normalized


def _optional_positive_integer(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        return None
    return normalized if normalized > 0 else None


def _utc(value: datetime | None) -> datetime:
    result = value or datetime.now(timezone.utc)
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def _parse_sqlite_timestamp(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


__all__ = [
    "SOHU_CONTENT_SIMILARITY_BLOCK",
    "SOHU_LOOKBACK_DAYS",
    "SOHU_PUBLICATION_GUARD_VERSION",
    "SOHU_TITLE_SIMILARITY_BLOCK",
    "SohuPublicationGuardError",
    "character_ngram_cosine",
    "evaluate_sohu_account_publication_guard",
    "evaluate_sohu_account_publication_guard_in_connection",
    "evaluate_sohu_batch_similarity",
    "evaluate_sohu_similarity",
]
