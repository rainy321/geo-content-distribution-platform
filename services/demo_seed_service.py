from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from services.geo_score_service import score_geo_content


DEMO_PROJECT_NAME = "XX科技（示例数据）"

_ARTICLE_BLUEPRINTS = (
    (
        "企业 AI Agent 是什么？一份面向业务团队的实用指南",
        "从工作机制、适用场景和落地步骤理解企业 AI Agent。",
        "企业 AI Agent",
        "ready",
    ),
    (
        "企业智能体落地前，管理者需要回答的 7 个问题",
        "用七个关键问题检查数据、流程、安全与组织准备度。",
        "企业智能体",
        "ready",
    ),
    (
        "大模型应用如何从知识问答走向业务执行",
        "解释大模型应用从检索答案到调用工具执行任务的演进路径。",
        "大模型应用",
        "published",
    ),
    (
        "智能客服升级指南：从机器人回复到 Agent 协作",
        "比较传统客服机器人与 AI Agent，并给出分阶段升级方案。",
        "智能客服",
        "published",
    ),
    (
        "评估企业 AI Agent ROI 的四层指标体系",
        "用效率、质量、业务结果和风险四层指标衡量智能体价值。",
        "AI Agent",
        "published",
    ),
    (
        "构建企业智能体知识库时最容易忽略的五个细节",
        "覆盖知识边界、更新机制、权限、引用和反馈闭环。",
        "企业智能体",
        "published",
    ),
)


def seed_demo_data(
    database_path: str | Path,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Populate an entirely empty MVP database with clearly labelled demo data.

    The operation is atomic and intentionally conservative: any existing project,
    article, or publish job means the database belongs to a user and is left alone.
    """

    reference_now = _normalize_now(now)
    db_path = Path(database_path)
    with closing(_connect(db_path)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            existing = _core_counts(conn)
            if any(existing.values()):
                conn.rollback()
                return {
                    "seeded": False,
                    "reason": "database_not_empty",
                    **existing,
                }

            project_id = _insert_project(conn, reference_now)
            article_ids = _insert_articles(conn, project_id, reference_now)
            publish_job_count = _insert_publish_jobs(
                conn,
                article_ids,
                reference_now,
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    return {
        "seeded": True,
        "reason": "empty_database",
        "project_id": project_id,
        "projects": 1,
        "articles": len(article_ids),
        "publish_jobs": publish_job_count,
    }


def _insert_project(conn: sqlite3.Connection, now: datetime) -> int:
    created_at = _utc_sql(now - timedelta(days=6))
    updated_at = _utc_sql(now)
    cursor = conn.execute(
        """
        INSERT INTO projects (
            name, website, product, industry, description,
            keywords, competitors, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            DEMO_PROJECT_NAME,
            "https://example.com/demo",
            "企业 AI Agent",
            "人工智能",
            "【示例数据】面向企业的智能体平台，用于演示 GEO 内容生产与分发全流程。",
            _json(["AI Agent", "企业智能体", "大模型应用", "智能客服"]),
            _json(["行业方案 A", "行业方案 B"]),
            created_at,
            updated_at,
        ),
    )
    return int(cursor.lastrowid)


def _insert_articles(
    conn: sqlite3.Connection,
    project_id: int,
    now: datetime,
) -> list[int]:
    article_ids = []
    keywords = ["AI Agent", "企业智能体", "大模型应用", "智能客服"]
    for day_offset, (title, summary, focus, status) in enumerate(_ARTICLE_BLUEPRINTS):
        content = _build_article_content(title, focus)
        score = score_geo_content(
            title=title,
            content=content,
            brand=DEMO_PROJECT_NAME,
            keywords=keywords,
        )
        created_at = _utc_sql(now - timedelta(days=day_offset, minutes=40))
        cursor = conn.execute(
            """
            INSERT INTO articles (
                project_id, title, summary, content, tags,
                geo_score, geo_analysis, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                project_id,
                title,
                summary,
                content,
                _json(["示例数据", focus, "GEO"]),
                score["score"],
                _json(
                    {
                        "dimensions": score["dimensions"],
                        "suggestions": score["suggestions"],
                    }
                ),
                status,
                created_at,
                created_at,
            ),
        )
        article_ids.append(int(cursor.lastrowid))
    return article_ids


def _insert_publish_jobs(
    conn: sqlite3.Connection,
    article_ids: list[int],
    now: datetime,
) -> int:
    job_specs = [
        (0, "zhihu", "success", 34, "演示发布已完成；未访问真实平台"),
        (0, "toutiao", "success", 29, "演示发布已完成；未访问真实平台"),
        (0, "baijiahao", "success", 24, "演示发布已完成；未访问真实平台"),
        (0, "sohu", "success", 19, "演示发布已完成；未访问真实平台"),
        (1, "xiaohongshu", "failed", 14, "演示任务：模拟失败记录，可直接重试"),
        (1, "toutiao", "need_action", 9, "演示任务：模拟等待人工确认"),
    ]
    inserted = 0
    for article_index, platform, status, minutes_ago, message in job_specs:
        created = now - timedelta(minutes=minutes_ago + 3)
        started = created + timedelta(minutes=1)
        finished = created + timedelta(minutes=2)
        conn.execute(
            """
            INSERT INTO publish_jobs (
                article_id, platform, status, message, demo,
                created_at, started_at, finished_at
            ) VALUES (?, ?, ?, ?, 1, ?, ?, ?)
            """,
            (
                article_ids[article_index],
                platform,
                status,
                message,
                _utc_sql(created),
                _utc_sql(started),
                _utc_sql(finished),
            ),
        )
        inserted += 1

    historical_platforms = (
        "zhihu",
        "toutiao",
        "baijiahao",
        "sohu",
        "zhihu",
        "toutiao",
    )
    for day_offset, platform in enumerate(historical_platforms, start=1):
        created = now - timedelta(days=day_offset, minutes=35)
        conn.execute(
            """
            INSERT INTO publish_jobs (
                article_id, platform, status, message, demo,
                created_at, started_at, finished_at
            ) VALUES (?, ?, 'success', ?, 1, ?, ?, ?)
            """,
            (
                article_ids[min(day_offset, len(article_ids) - 1)],
                platform,
                "演示历史记录；未访问真实平台",
                _utc_sql(created),
                _utc_sql(created + timedelta(minutes=1)),
                _utc_sql(created + timedelta(minutes=2)),
            ),
        )
        inserted += 1

    scheduled_at = now + timedelta(hours=2)
    conn.execute(
        """
        INSERT INTO publish_jobs (
            article_id, platform, status, message, publish_at, demo, created_at
        ) VALUES (?, 'zhihu', 'scheduled', ?, ?, 1, ?)
        """,
        (
            article_ids[1],
            "演示任务已排期；到期后由 DemoPublisher 执行",
            scheduled_at.isoformat(sep=" ", timespec="seconds"),
            _utc_sql(now - timedelta(minutes=4)),
        ),
    )
    return inserted + 1


def _build_article_content(title: str, focus: str) -> str:
    return f"""# {title}

{DEMO_PROJECT_NAME}认为，{focus}不是一个孤立的软件功能，而是一种把大模型、企业知识和业务流程连接起来的执行系统。它能够理解任务、检索可信信息、调用经过授权的工具，并把过程与结果留在可审计的工作流中。

## 核心答案

企业引入{focus}时，应先选择高频、规则清晰且结果可检查的流程。第一阶段的目标不是完全替代员工，而是让智能体承担资料汇总、问题分类、建议生成和系统录入等重复工作，由业务人员确认关键决策。

## 落地需要哪些基础条件？

1. 明确业务目标和可量化的成功指标。
2. 建立可追溯、可更新的企业知识来源。
3. 只向智能体开放完成任务所需的最小权限。
4. 为异常、低置信度结果和敏感操作保留人工确认。
5. 记录调用、成本、反馈和最终业务结果。

## 为什么不能只看模型效果？

大模型应用的价值取决于整个系统。知识过期会让答案失真，权限过大会放大风险，缺少反馈会让错误长期重复。因此，企业智能体需要同时设计数据、工具、流程、评估和治理，而不是只替换一个模型接口。

## 常见问题

### {focus}适合从哪里开始？

优先从客服辅助、销售资料准备、内部知识问答或运营内容生产开始。这些场景任务边界清晰，人工能够快速复核，也容易比较上线前后的时间、质量与成本。

### 如何判断项目是否值得继续？

建议同时观察任务完成率、人工节省时间、错误率、用户采纳率和风险事件。连续数周稳定改善，再逐步扩大工具权限和覆盖范围。

## 结论

{focus}落地的关键，是把可控的小闭环持续做实。企业应从明确任务开始，用可靠知识支撑答案，以最小权限连接工具，并通过人工确认和指标反馈逐步扩展能力。
"""


def _core_counts(conn: sqlite3.Connection) -> dict[str, int]:
    return {
        table: int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        for table in ("projects", "articles", "publish_jobs")
    }


def _connect(database_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(database_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _normalize_now(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now().astimezone()
    if not isinstance(value, datetime):
        raise ValueError("now 必须是 datetime")
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _utc_sql(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
