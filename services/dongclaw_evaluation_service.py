"""Repeatable, import-only GEO evaluation for the authorized DongClaw pilot.

The evaluator never contacts an AI provider.  It accepts manually or
programmatically captured answers, validates that the study is limited to the
authorized DongClaw subject, delegates answer parsing to the production
visibility engine, and emits aggregate measurements without copying raw model
answers into the report payload.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import datetime
from typing import Any, Mapping, Sequence

from services.ai_visibility_service import (
    AI_VISIBILITY_ALGORITHM_VERSION,
    VisibilityAnalysisError,
    analyze_ai_visibility,
)


DONGCLAW_EVALUATION_SCHEMA_VERSION = "dongclaw-geo-evaluation-input-1.0.0"
DONGCLAW_EVALUATION_REPORT_VERSION = "dongclaw-geo-evaluation-report-1.0.0"

AUTHORIZED_SUBJECT = {
    "company": "北京同于野科技有限公司",
    "product": "DongClaw 小龙虾",
    "aliases": ["DongClaw", "DongClaw小龙虾"],
    "competitor": "RanClaw",
    # No official product domain was independently verified in the 2026-09-10
    # evidence snapshot, so an owned-citation claim must remain unavailable.
    "website": "",
}

ALLOWED_PROVIDERS = ("qwen", "deepseek", "doubao")
ALLOWED_PHASES = ("baseline", "post_optimization")
ALLOWED_STATUSES = ("success", "failed", "blocked", "verification_required")
ALLOWED_SURFACES = ("api", "web")
RECOMMENDED_REPLICATES = 5
MINIMUM_OBSERVATION_HOURS = 72.0
MAX_ATTEMPTS = 360
MAX_RESPONSE_LENGTH = 50_000

QUESTION_MATRIX = (
    {
        "id": "cn-discovery",
        "region": "中国大陆",
        "intent": "品类发现",
        "cohort": "unbranded",
        "text": "中国中小企业想快速部署可持续运行的 AI 助手，有哪些产品或服务值得考虑？",
    },
    {
        "id": "beijing-local-data",
        "region": "北京",
        "intent": "本地数据与部署",
        "cohort": "unbranded",
        "text": "北京的中小企业需要兼顾本地数据与远程运行时，应该如何选择 AI 助手部署方案？",
    },
    {
        "id": "cn-low-ops",
        "region": "中国大陆",
        "intent": "低运维部署",
        "cohort": "unbranded",
        "text": "没有专职运维团队时，如何快速部署并长期运行一个 AI 助手？",
    },
    {
        "id": "dongclaw-identity",
        "region": "中国大陆",
        "intent": "品牌实体认知",
        "cohort": "branded",
        "text": "DongClaw 小龙虾是什么产品，由哪家公司提供，适合解决什么问题？",
    },
    {
        "id": "dongclaw-vs-ranclaw",
        "region": "中国大陆",
        "intent": "竞品比较",
        "cohort": "branded_comparison",
        "text": "DongClaw 小龙虾和 RanClaw 有什么区别，分别适合什么团队？",
    },
    {
        "id": "beijing-chinese-support",
        "region": "北京",
        "intent": "中文与本地服务",
        "cohort": "unbranded",
        "text": "如果希望在中国使用、强调中文支持和本地化服务，AI 助手部署产品该怎么选？",
    },
    {
        "id": "dongclaw-public-evidence",
        "region": "北京",
        "intent": "事实与来源核验",
        "cohort": "branded",
        "text": "北京同于野科技有限公司提供的 DongClaw 小龙虾有哪些可验证的公开信息？",
    },
)

PUBLIC_EVIDENCE_SNAPSHOT = {
    "checked_at": "2026-09-10",
    "search_scope": "公开网页精确名称检索",
    "findings": [
        {
            "entity": "北京同于野科技有限公司",
            "status": "THIRD_PARTY_REGISTRY_ONLY",
            "finding": (
                "检索到第三方企业信息页，可核对主体名称与一般经营范围；"
                "该页面不是企业官网，也未形成 DongClaw 产品归属的独立证明。"
            ),
            "source": (
                "https://www.shuididp.cn/yp-"
                "4c2d8440523800360ce008a135fc3b01.html"
            ),
        },
        {
            "entity": "DongClaw 小龙虾",
            "status": "NO_VERIFIED_OFFICIAL_PRODUCT_SOURCE",
            "finding": "未找到可独立核验的官方产品页或权威第三方产品介绍。",
            "source": None,
        },
        {
            "entity": "RanClaw",
            "status": "EXACT_ENTITY_UNVERIFIED",
            "finding": (
                "未找到足以确认用户所指竞品的精确实体；搜索结果中的 RunClaw "
                "拼写不同，不能并入 RanClaw 声量或引用。"
            ),
            "source": "https://runclaw.sh/",
        },
    ],
}

_TOP_LEVEL_FIELDS = {
    "schema_version",
    "subject",
    "study",
    "questions",
    "attempts",
    "interventions",
}
_STUDY_FIELDS = {
    "study_id",
    "evaluation_type",
    "timezone",
    "optimized_at",
    "notes",
}
_ATTEMPT_FIELDS = {
    "id",
    "phase",
    "question_id",
    "provider",
    "model",
    "surface",
    "web_search_enabled",
    "replicate",
    "captured_at",
    "status",
    "response_text",
    "error_code",
    "source_reference",
}
_INTERVENTION_FIELDS = {"id", "occurred_at", "type", "description", "evidence"}
_FORBIDDEN_KEY_PARTS = (
    "api_key",
    "authorization",
    "cookie",
    "password",
    "secret",
    "token",
)


class DongClawEvaluationError(ValueError):
    """Raised when an evaluation manifest violates the fixed pilot contract."""


def build_dongclaw_input_template(
    *, study_id: str = "dongclaw-geo-pilot-20260910"
) -> dict[str, Any]:
    """Return the credential-free, authorized pilot input template."""

    return {
        "schema_version": DONGCLAW_EVALUATION_SCHEMA_VERSION,
        "subject": _deep_copy(AUTHORIZED_SUBJECT),
        "study": {
            "study_id": study_id,
            "evaluation_type": "short-horizon-observational-pilot",
            "timezone": "Asia/Hong_Kong",
            "optimized_at": None,
            "notes": (
                "只导入三个指定模型对固定问题的原始回答；不要放入账号、Cookie、"
                "API 密钥、其他项目或客户资料。"
            ),
        },
        "questions": _deep_copy(list(QUESTION_MATRIX)),
        "attempts": [],
        "interventions": [],
    }


def evaluate_dongclaw_manifest(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and evaluate a DongClaw baseline/retest manifest.

    No network call is made.  Raw response text is used transiently by the
    deterministic visibility parser and replaced with a SHA-256 digest in the
    returned report.
    """

    manifest = _validate_manifest(payload)
    measurements: list[dict[str, Any]] = []
    cells: list[dict[str, Any]] = []

    grouped: dict[tuple[str, str, str, str, bool, str], list[dict[str, Any]]] = (
        defaultdict(list)
    )
    for attempt in manifest["attempts"]:
        key = (
            attempt["phase"],
            attempt["provider"],
            attempt["model"],
            attempt["surface"],
            attempt["web_search_enabled"],
            attempt["question_id"],
        )
        grouped[key].append(attempt)

    questions_by_id = {item["id"]: item for item in QUESTION_MATRIX}
    for key in sorted(grouped):
        phase, provider, model, surface, web_search_enabled, question_id = key
        attempts = sorted(grouped[key], key=lambda item: (item["replicate"], item["id"]))
        successful = [item for item in attempts if item["status"] == "success"]
        analysis = None
        if successful:
            try:
                analysis = analyze_ai_visibility(
                    project_id=1,
                    project_name=AUTHORIZED_SUBJECT["company"],
                    project_product=AUTHORIZED_SUBJECT["product"],
                    project_website=AUTHORIZED_SUBJECT["website"],
                    stored_competitors=[AUTHORIZED_SUBJECT["competitor"]],
                    prompt=questions_by_id[question_id]["text"],
                    responses=[
                        {
                            "text": item["response_text"],
                            "provider": provider,
                            "model": model,
                        }
                        for item in successful
                    ],
                    aliases=AUTHORIZED_SUBJECT["aliases"],
                )
            except VisibilityAnalysisError as exc:  # defensive contract bridge
                raise DongClawEvaluationError(str(exc)) from exc

            for attempt, sample in zip(successful, analysis["samples"]):
                measurements.append(_measurement_from_sample(attempt, sample))

        attempt_measurements = [
            item for item in measurements if item["attempt_id"] in {a["id"] for a in attempts}
        ]
        cells.append(
            {
                "phase": phase,
                "provider": provider,
                "model": model,
                "surface": surface,
                "web_search_enabled": web_search_enabled,
                "question": _deep_copy(questions_by_id[question_id]),
                "metrics": _summarize_attempts(attempts, attempt_measurements),
                "analysis_warnings": analysis["warnings"] if analysis else [],
                "algorithm_version": (
                    analysis["algorithm_versions"]["integration"] if analysis else None
                ),
            }
        )

    overviews = []
    overview_keys = sorted(
        {
            (
                attempt["phase"],
                attempt["provider"],
                attempt["model"],
                attempt["surface"],
                attempt["web_search_enabled"],
                questions_by_id[attempt["question_id"]]["cohort"],
            )
            for attempt in manifest["attempts"]
        }
    )
    for phase, provider, model, surface, web_search_enabled, cohort in overview_keys:
        selected_attempts = [
            item
            for item in manifest["attempts"]
            if (
                item["phase"],
                item["provider"],
                item["model"],
                item["surface"],
                item["web_search_enabled"],
                questions_by_id[item["question_id"]]["cohort"],
            )
            == (phase, provider, model, surface, web_search_enabled, cohort)
        ]
        selected_ids = {item["id"] for item in selected_attempts}
        selected_measurements = [
            item for item in measurements if item["attempt_id"] in selected_ids
        ]
        overviews.append(
            {
                "phase": phase,
                "provider": provider,
                "model": model,
                "surface": surface,
                "web_search_enabled": web_search_enabled,
                "cohort": cohort,
                "metrics": _summarize_attempts(
                    selected_attempts, selected_measurements
                ),
            }
        )

    comparison = _build_paired_comparison(manifest["attempts"], measurements)
    quality_gates = _quality_gates(manifest, cells, comparison)
    status = _study_status(manifest, comparison)
    blocking_items = _blocking_items(manifest, status, comparison)
    readiness_snapshot = _readiness_snapshot(manifest, cells)

    return {
        "report_version": DONGCLAW_EVALUATION_REPORT_VERSION,
        "input_schema_version": DONGCLAW_EVALUATION_SCHEMA_VERSION,
        "study": _deep_copy(manifest["study"]),
        "subject": _deep_copy(AUTHORIZED_SUBJECT),
        "status": status,
        "claim_boundary": _claim_boundary(status),
        "blocking_items": blocking_items,
        "readiness_snapshot": readiness_snapshot,
        "public_evidence_snapshot": _deep_copy(PUBLIC_EVIDENCE_SNAPSHOT),
        "question_matrix": _deep_copy(list(QUESTION_MATRIX)),
        "provider_overviews": overviews,
        "question_results": cells,
        "paired_comparison": comparison,
        "quality_gates": quality_gates,
        "measurement_records": sorted(
            measurements, key=lambda item: item["attempt_id"]
        ),
        "interventions": _deep_copy(manifest["interventions"]),
        "methodology": {
            "visibility_algorithm": AI_VISIBILITY_ALGORITHM_VERSION,
            "external_model_called_by_evaluator": False,
            "raw_answers_in_report": False,
            "recommended_replicates_per_cell": RECOMMENDED_REPLICATES,
            "minimum_observation_hours": MINIMUM_OBSERVATION_HOURS,
            "comparison_unit": (
                "same provider, exact model, surface, web-search setting, question, "
                "and replicate"
            ),
            "rates": (
                "success-denominator rates use successful answers; mention_rate_all_attempts "
                "also exposes failures in the denominator"
            ),
            "cohort_policy": (
                "Unbranded discovery, branded identity, and branded comparison prompts "
                "are reported separately so prompt-echo cannot inflate discovery metrics."
            ),
            "owned_citation_semantics": (
                "Unavailable until an independently verified official DongClaw domain is "
                "entered into the authorized subject contract."
            ),
        },
    }


def render_dongclaw_markdown(report: Mapping[str, Any]) -> str:
    """Render a compact, auditable Markdown report from evaluator output."""

    status = str(report.get("status") or "INVALID")
    subject = report.get("subject") or {}
    lines = [
        "# DongClaw GEO 短周期诊断与回测报告",
        "",
        f"- 报告状态：`{status}`",
        f"- 公司：{_md(subject.get('company'))}",
        f"- 产品：{_md(subject.get('product'))}",
        f"- 指定竞品：{_md(subject.get('competitor'))}",
        f"- 研究编号：`{_md((report.get('study') or {}).get('study_id'))}`",
        "",
        "> " + _md(report.get("claim_boundary")),
        "",
        "## 1. 当前结论",
        "",
    ]
    if status == "NOT_EXECUTED":
        lines.append(
            "尚未导入任何 Qwen、DeepSeek 或豆包真实回答，因此没有可报告的品牌提及率、"
            "排名、引用率或优化提升；本文件是已通过数据边界检查的验收骨架，不是效果证明。"
        )
    elif status == "BASELINE_ONLY":
        lines.append(
            "已形成基线，但尚无可配对的优化后样本；可以描述当前可见度，不能计算提升。"
        )
    elif status == "SHORT_HORIZON_EXPLORATORY":
        lines.append(
            "存在基线与优化后配对样本，但观察窗少于 72 小时；差值只用于验证流程和发现"
            "回归，不得对外表述为 GEO 的因果效果。"
        )
    else:
        lines.append(
            "已形成超过 72 小时的观察性复测；仍应报告模型版本、联网设置、失败执行与置信"
            "区间，且不能把非随机观察直接解释为因果效果。"
        )

    lines.extend(["", "### 可量化准备度（不等于 GEO 效果分）", ""])
    lines.append("| 检查项 | 当前/目标 | 解释 |")
    lines.append("| --- | ---: | --- |")
    for item in report.get("readiness_snapshot") or []:
        lines.append(
            f"| {_md(item['label'])} | {item['current']}/{item['target']} | "
            f"{_md(item['detail'])} |"
        )

    lines.extend(["", "## 2. 公开证据基线", ""])
    snapshot = report.get("public_evidence_snapshot") or {}
    lines.append(f"检索快照日期：{_md(snapshot.get('checked_at'))}。")
    lines.append("")
    lines.append("| 实体 | 状态 | 可核验结论 | 来源 |")
    lines.append("| --- | --- | --- | --- |")
    for finding in snapshot.get("findings") or []:
        source = finding.get("source")
        source_cell = f"[查看]({source})" if source else "无"
        lines.append(
            "| {entity} | `{status}` | {finding} | {source} |".format(
                entity=_md(finding.get("entity")),
                status=_md(finding.get("status")),
                finding=_md(finding.get("finding")),
                source=source_cell,
            )
        )

    lines.extend(["", "## 3. 多模型结果概览", ""])
    overviews = report.get("provider_overviews") or []
    if not overviews:
        lines.append("尚无真实模型样本。")
    else:
        lines.append(
            "| 阶段 | 问题类型 | 模型来源 | 模型/界面 | 尝试 | 成功率 | 提及率（成功回答） | "
            "提及率（全部尝试） | 首位率 | Top 3 | SOV | 引用率 |"
        )
        lines.append(
            "| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"
        )
        for item in overviews:
            metrics = item["metrics"]
            model_surface = (
                f"{item['model']} / {item['surface']} / "
                f"联网={'开' if item['web_search_enabled'] else '关'}"
            )
            lines.append(
                "| {phase} | {cohort} | {provider} | {model} | {attempts} | {success} | "
                "{mention_success} | {mention_all} | {first} | {top3} | {sov} | {citation} |".format(
                    phase=_md(item["phase"]),
                    cohort=_md(item["cohort"]),
                    provider=_md(item["provider"]),
                    model=_md(model_surface),
                    attempts=metrics["attempt_count"],
                    success=_pct(metrics["success_rate"]),
                    mention_success=_pct(metrics["mention_rate_success"]),
                    mention_all=_pct(metrics["mention_rate_all_attempts"]),
                    first=_pct(metrics["first_position_rate_success"]),
                    top3=_pct(metrics["top3_rate_success"]),
                    sov=_pct(metrics["share_of_voice"]),
                    citation=_pct(metrics["citation_rate_success"]),
                )
            )

    lines.extend(["", "## 4. 地区 / 问题 / 结果矩阵", ""])
    cells = report.get("question_results") or []
    if not cells:
        lines.append("固定问题矩阵已准备，但尚未采样。")
    else:
        lines.append(
            "| 阶段 | 地区 | 意图 | 问题 | Provider | 成功/尝试 | 提及率 | 平均位次 | 引用率 |"
        )
        lines.append("| --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: |")
        for item in cells:
            metrics = item["metrics"]
            question = item["question"]
            lines.append(
                "| {phase} | {region} | {intent} | {question} | {provider} | "
                "{success}/{attempts} | {mention} | {rank} | {citation} |".format(
                    phase=_md(item["phase"]),
                    region=_md(question["region"]),
                    intent=_md(question["intent"]),
                    question=_md(question["text"]),
                    provider=_md(f"{item['provider']} / {item['model']}"),
                    success=metrics["success_count"],
                    attempts=metrics["attempt_count"],
                    mention=_pct(metrics["mention_rate_success"]),
                    rank=_number(metrics["average_rank"]),
                    citation=_pct(metrics["citation_rate_success"]),
                )
            )

    lines.extend(["", "## 5. 配对回测", ""])
    comparison = report.get("paired_comparison") or {}
    if not comparison.get("matched_pair_count"):
        lines.append(
            "没有基线/优化后配对。配对键必须同时匹配 provider、精确模型版本、网页/API "
            "界面、联网设置、问题和重复编号。"
        )
    else:
        lines.extend(
            [
                f"- 可配对尝试：{comparison['matched_pair_count']}",
                f"- 双方均成功：{comparison['matched_success_pair_count']}",
                f"- 提及率变化：{_pp(comparison['deltas'].get('mention_rate_success'))}",
                f"- 首位率变化：{_pp(comparison['deltas'].get('first_position_rate_success'))}",
                f"- Top 3 率变化：{_pp(comparison['deltas'].get('top3_rate_success'))}",
                f"- 引用率变化：{_pp(comparison['deltas'].get('citation_rate_success'))}",
                f"- 平均位次改善（正数更好）：{_number(comparison['deltas'].get('average_rank_improvement'))}",
            ]
        )
        lines.extend(
            [
                "",
                "| 问题类型 | 成功配对 | 提及率变化 | 首位率变化 | Top 3 变化 | 引用率变化 |",
                "| --- | ---: | ---: | ---: | ---: | ---: |",
            ]
        )
        for cohort in comparison.get("by_cohort") or []:
            deltas = cohort["deltas"]
            lines.append(
                "| {cohort} | {pairs} | {mention} | {first} | {top3} | {citation} |".format(
                    cohort=_md(cohort["cohort"]),
                    pairs=cohort["matched_success_pair_count"],
                    mention=_pp(deltas.get("mention_rate_success")),
                    first=_pp(deltas.get("first_position_rate_success")),
                    top3=_pp(deltas.get("top3_rate_success")),
                    citation=_pp(deltas.get("citation_rate_success")),
                )
            )

    lines.extend(["", "## 6. 质量门禁", ""])
    gates = report.get("quality_gates") or []
    for gate in gates:
        marker = "通过" if gate["passed"] else "未通过"
        lines.append(f"- **{marker}** `{_md(gate['id'])}`：{_md(gate['detail'])}")

    lines.extend(
        [
            "",
            "## 7. 当前阻塞项",
            "",
        ]
    )
    blockers = report.get("blocking_items") or []
    if not blockers:
        lines.append("当前没有阻止观察性报告生成的输入缺口。")
    else:
        for blocker in blockers:
            lines.append(
                f"- `{_md(blocker['id'])}`：{_md(blocker['detail'])}"
            )

    lines.extend(
        [
            "",
            "## 8. 解释边界与下一步",
            "",
            "1. 同日或不足 72 小时的复测只能验证采样、诊断、内容、发布和回填链路是否顺畅，不能证明 GEO 已改变模型长期认知。",
            "2. 每个 provider × 问题 × 阶段至少使用 5 个独立新会话；保留失败、验证码和超时，不能只导入成功回答。",
            "3. 基线与复测必须固定精确模型版本、联网开关、地区、问题原文和界面/API；模型升级后另开序列。",
            "4. 在建立可核验官网/产品页和明确公司—产品关系之前，任何“自有来源引用率提升”都没有可靠分母。",
            "5. 三天是最低观察窗，建议持续 14–30 天；对外宣传应使用“观察到的变化”，不要使用未经实验支持的因果或保证性表述。",
            "",
            "## 9. 数据边界",
            "",
            "本报告只允许使用北京同于野科技有限公司、DongClaw 小龙虾、RanClaw 和固定测试问题。评估器不会联网，不读取软件数据库，不处理媒体账号/Cookie/API 密钥，也不在输出中复制模型回答原文。",
            "",
        ]
    )
    return "\n".join(lines)


def _readiness_snapshot(
    manifest: Mapping[str, Any], cells: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    baseline_attempts = [
        item for item in manifest["attempts"] if item["phase"] == "baseline"
    ]
    post_attempts = [
        item for item in manifest["attempts"] if item["phase"] == "post_optimization"
    ]
    successful_baseline = [
        item for item in baseline_attempts if item["status"] == "success"
    ]
    successful_post = [item for item in post_attempts if item["status"] == "success"]
    expected_cells = len(ALLOWED_PROVIDERS) * len(QUESTION_MATRIX)
    expected_samples = expected_cells * RECOMMENDED_REPLICATES
    baseline_cells = sum(
        item["phase"] == "baseline" and item["metrics"]["success_count"] > 0
        for item in cells
    )
    post_cells = sum(
        item["phase"] == "post_optimization" and item["metrics"]["success_count"] > 0
        for item in cells
    )
    return [
        {
            "id": "fixed-question-matrix",
            "label": "固定问题矩阵",
            "current": len(QUESTION_MATRIX),
            "target": len(QUESTION_MATRIX),
            "detail": "覆盖中国大陆/北京、发现、部署、品牌、竞品和事实核验。",
        },
        {
            "id": "company-evidence",
            "label": "公司主体辅助证据",
            "current": 1,
            "target": 1,
            "detail": "仅第三方企业信息页，不等于官方产品归属证明。",
        },
        {
            "id": "official-product-source",
            "label": "官方产品来源",
            "current": int(bool(AUTHORIZED_SUBJECT["website"])),
            "target": 1,
            "detail": "当前未核验，产品事实和自有引用无法可靠判定。",
        },
        {
            "id": "exact-competitor-source",
            "label": "精确竞品来源",
            "current": 0,
            "target": 1,
            "detail": "RanClaw 精确实体未核验；不合并 RunClaw。",
        },
        {
            "id": "baseline-provider-coverage",
            "label": "基线模型覆盖",
            "current": len({item["provider"] for item in successful_baseline}),
            "target": len(ALLOWED_PROVIDERS),
            "detail": "Qwen、DeepSeek、豆包分别统计。",
        },
        {
            "id": "baseline-cell-coverage",
            "label": "基线单元覆盖",
            "current": baseline_cells,
            "target": expected_cells,
            "detail": "三模型乘七问题；至少一条成功回答才算覆盖。",
        },
        {
            "id": "baseline-sample-depth",
            "label": "基线建议样本量",
            "current": len(successful_baseline),
            "target": expected_samples,
            "detail": "每单元建议五个独立新会话，失败另保留在尝试分母。",
        },
        {
            "id": "post-cell-coverage",
            "label": "复测单元覆盖",
            "current": post_cells,
            "target": expected_cells,
            "detail": "必须与基线精确配置配对。",
        },
        {
            "id": "post-sample-depth",
            "label": "复测建议样本量",
            "current": len(successful_post),
            "target": expected_samples,
            "detail": "不足 72 小时只做流程验收，建议持续 14–30 天。",
        },
    ]


def _blocking_items(
    manifest: Mapping[str, Any],
    status: str,
    comparison: Mapping[str, Any],
) -> list[dict[str, str]]:
    blockers: list[dict[str, str]] = []
    successes = [
        item for item in manifest["attempts"] if item["status"] == "success"
    ]
    if not successes:
        blockers.append(
            {
                "id": "REAL_MODEL_OUTPUTS_MISSING",
                "detail": (
                    "尚未从 Qwen、DeepSeek、豆包的已授权网页会话或 API 导入真实回答；"
                    "评估器不会自行读取登录态或凭据。"
                ),
            }
        )
    missing_providers = sorted(
        set(ALLOWED_PROVIDERS) - {item["provider"] for item in successes}
    )
    if missing_providers:
        blockers.append(
            {
                "id": "PROVIDER_COVERAGE_INCOMPLETE",
                "detail": f"缺少成功样本：{missing_providers}。",
            }
        )
    if not AUTHORIZED_SUBJECT["website"]:
        blockers.append(
            {
                "id": "OFFICIAL_PRODUCT_SOURCE_UNVERIFIED",
                "detail": (
                    "没有独立核验的 DongClaw 官方产品页/域名，无法计算可信的自有引用率或"
                    "核对模型生成的产品事实。"
                ),
            }
        )
    blockers.append(
        {
            "id": "RANCLAW_ENTITY_UNVERIFIED",
            "detail": (
                "指定竞品 RanClaw 尚无精确、可信的公开实体来源；RunClaw 等相近拼写"
                "不会自动合并。"
            ),
        }
    )
    if status == "BASELINE_ONLY":
        blockers.append(
            {
                "id": "PAIRED_RETEST_MISSING",
                "detail": (
                    "没有同模型、同 surface、同联网设置、同问题和同重复编号的成功配对复测。"
                ),
            }
        )
    elif status == "SHORT_HORIZON_EXPLORATORY":
        blockers.append(
            {
                "id": "OBSERVATION_WINDOW_TOO_SHORT",
                "detail": (
                    f"已有 {comparison['matched_success_pair_count']} 个成功配对，但最短观察窗"
                    f"不足 {MINIMUM_OBSERVATION_HOURS:.0f} 小时。"
                ),
            }
        )
    return blockers


def _validate_manifest(payload: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise DongClawEvaluationError("评估输入必须是 JSON 对象")
    _reject_forbidden_keys(payload)
    _require_exact_fields(payload, _TOP_LEVEL_FIELDS, "root")
    if payload.get("schema_version") != DONGCLAW_EVALUATION_SCHEMA_VERSION:
        raise DongClawEvaluationError("不支持的评估输入版本")
    if payload.get("subject") != AUTHORIZED_SUBJECT:
        raise DongClawEvaluationError("subject 必须与已授权 DongClaw 测试主体完全一致")
    if payload.get("questions") != list(QUESTION_MATRIX):
        raise DongClawEvaluationError("questions 必须使用固定、可配对的问题矩阵")

    study = payload.get("study")
    if not isinstance(study, Mapping):
        raise DongClawEvaluationError("study 必须是对象")
    _require_exact_fields(study, _STUDY_FIELDS, "study")
    normalized_study = {
        "study_id": _text(study.get("study_id"), "study.study_id", 120),
        "evaluation_type": _text(
            study.get("evaluation_type"), "study.evaluation_type", 120
        ),
        "timezone": _text(study.get("timezone"), "study.timezone", 80),
        "optimized_at": _optional_timestamp(study.get("optimized_at"), "optimized_at"),
        "notes": _optional_text(study.get("notes"), "study.notes", 2_000),
    }

    attempts = payload.get("attempts")
    if not isinstance(attempts, list):
        raise DongClawEvaluationError("attempts 必须是数组")
    if len(attempts) > MAX_ATTEMPTS:
        raise DongClawEvaluationError(f"attempts 最多支持 {MAX_ATTEMPTS} 条")
    normalized_attempts = [_validate_attempt(item, index) for index, item in enumerate(attempts)]
    ids = [item["id"] for item in normalized_attempts]
    if len(ids) != len(set(ids)):
        raise DongClawEvaluationError("attempts.id 不能重复")
    cell_replicates = [
        (
            item["phase"],
            item["question_id"],
            item["provider"],
            item["model"],
            item["surface"],
            item["web_search_enabled"],
            item["replicate"],
        )
        for item in normalized_attempts
    ]
    if len(cell_replicates) != len(set(cell_replicates)):
        raise DongClawEvaluationError("同一评估单元的 replicate 不能重复")

    interventions = payload.get("interventions")
    if not isinstance(interventions, list) or len(interventions) > 100:
        raise DongClawEvaluationError("interventions 必须是不超过 100 项的数组")
    normalized_interventions = [
        _validate_intervention(item, index) for index, item in enumerate(interventions)
    ]
    intervention_ids = [item["id"] for item in normalized_interventions]
    if len(intervention_ids) != len(set(intervention_ids)):
        raise DongClawEvaluationError("interventions.id 不能重复")

    if normalized_study["optimized_at"] is not None:
        optimized_at = _parse_timestamp(normalized_study["optimized_at"], "optimized_at")
        for item in normalized_attempts:
            captured = _parse_timestamp(item["captured_at"], "captured_at")
            if item["phase"] == "baseline" and captured >= optimized_at:
                raise DongClawEvaluationError("baseline 必须早于 optimized_at")
            if item["phase"] == "post_optimization" and captured <= optimized_at:
                raise DongClawEvaluationError("post_optimization 必须晚于 optimized_at")
    elif any(item["phase"] == "post_optimization" for item in normalized_attempts):
        raise DongClawEvaluationError("存在优化后样本时必须提供 optimized_at")

    return {
        "schema_version": DONGCLAW_EVALUATION_SCHEMA_VERSION,
        "subject": _deep_copy(AUTHORIZED_SUBJECT),
        "study": normalized_study,
        "questions": _deep_copy(list(QUESTION_MATRIX)),
        "attempts": normalized_attempts,
        "interventions": normalized_interventions,
    }


def _validate_attempt(value: Any, index: int) -> dict[str, Any]:
    field = f"attempts[{index}]"
    if not isinstance(value, Mapping):
        raise DongClawEvaluationError(f"{field} 必须是对象")
    unsupported = set(value) - _ATTEMPT_FIELDS
    required = {
        "id",
        "phase",
        "question_id",
        "provider",
        "model",
        "surface",
        "web_search_enabled",
        "replicate",
        "captured_at",
        "status",
    }
    missing = required - set(value)
    if unsupported or missing:
        raise DongClawEvaluationError(
            f"{field} 字段不符合契约; missing={sorted(missing)}, unsupported={sorted(unsupported)}"
        )
    phase = _enum(value.get("phase"), ALLOWED_PHASES, f"{field}.phase")
    question_id = _enum(
        value.get("question_id"),
        tuple(item["id"] for item in QUESTION_MATRIX),
        f"{field}.question_id",
    )
    provider = _enum(value.get("provider"), ALLOWED_PROVIDERS, f"{field}.provider")
    surface = _enum(value.get("surface"), ALLOWED_SURFACES, f"{field}.surface")
    status = _enum(value.get("status"), ALLOWED_STATUSES, f"{field}.status")
    web_search_enabled = value.get("web_search_enabled")
    if not isinstance(web_search_enabled, bool):
        raise DongClawEvaluationError(f"{field}.web_search_enabled 必须是布尔值")
    replicate = value.get("replicate")
    if isinstance(replicate, bool) or not isinstance(replicate, int) or not 1 <= replicate <= 10:
        raise DongClawEvaluationError(f"{field}.replicate 必须是 1..10 的整数")
    response_text = value.get("response_text")
    if status == "success":
        response_text = _text(response_text, f"{field}.response_text", MAX_RESPONSE_LENGTH)
    elif response_text not in (None, ""):
        raise DongClawEvaluationError(f"{field} 非成功状态不能包含 response_text")
    else:
        response_text = None
    error_code = _optional_text(value.get("error_code"), f"{field}.error_code", 160)
    if status != "success" and error_code is None:
        raise DongClawEvaluationError(f"{field} 非成功状态必须提供 error_code")
    return {
        "id": _text(value.get("id"), f"{field}.id", 160),
        "phase": phase,
        "question_id": question_id,
        "provider": provider,
        "model": _text(value.get("model"), f"{field}.model", 160),
        "surface": surface,
        "web_search_enabled": web_search_enabled,
        "replicate": replicate,
        "captured_at": _timestamp(value.get("captured_at"), f"{field}.captured_at"),
        "status": status,
        "response_text": response_text,
        "error_code": error_code,
        "source_reference": _optional_text(
            value.get("source_reference"), f"{field}.source_reference", 2_048
        ),
    }


def _validate_intervention(value: Any, index: int) -> dict[str, Any]:
    field = f"interventions[{index}]"
    if not isinstance(value, Mapping):
        raise DongClawEvaluationError(f"{field} 必须是对象")
    _require_exact_fields(value, _INTERVENTION_FIELDS, field)
    return {
        "id": _text(value.get("id"), f"{field}.id", 160),
        "occurred_at": _timestamp(value.get("occurred_at"), f"{field}.occurred_at"),
        "type": _text(value.get("type"), f"{field}.type", 120),
        "description": _text(value.get("description"), f"{field}.description", 4_000),
        "evidence": _optional_text(value.get("evidence"), f"{field}.evidence", 2_048),
    }


def _measurement_from_sample(
    attempt: Mapping[str, Any], sample: Mapping[str, Any]
) -> dict[str, Any]:
    brand_sentiments = [
        item
        for item in (sample.get("perception") or {}).get("sentiments", [])
        if item.get("entity_id") == "brand"
    ]
    sentiment_score = (
        sum(float(item["score"]) for item in brand_sentiments) / len(brand_sentiments)
        if brand_sentiments
        else None
    )
    citations = sample.get("citations") or []
    brand_rank = sample.get("recommendation_rank")
    return {
        "attempt_id": attempt["id"],
        "phase": attempt["phase"],
        "provider": attempt["provider"],
        "model": attempt["model"],
        "surface": attempt["surface"],
        "web_search_enabled": attempt["web_search_enabled"],
        "question_id": attempt["question_id"],
        "replicate": attempt["replicate"],
        "captured_at": attempt["captured_at"],
        "response_sha256": hashlib.sha256(
            attempt["response_text"].encode("utf-8")
        ).hexdigest(),
        "source_reference": attempt.get("source_reference"),
        "brand_mentioned": bool(sample.get("brand_mentioned")),
        "competitor_mentioned": bool(sample.get("competitor_mentions")),
        "recommendation_rank": int(brand_rank) if brand_rank is not None else None,
        "citation_present": bool(citations),
        "owned_citation_present": any(item.get("is_owned") for item in citations),
        "citation_source_types": sorted(
            {str(item.get("source_type")) for item in citations if item.get("source_type")}
        ),
        "sentiment_score": sentiment_score,
        "brand_weighted_voice": _entity_weight(sample, "brand"),
        "competitor_weighted_voice": sum(
            _rank_weight(item.get("rank"))
            for item in (sample.get("ranks") or [])
            if str(item.get("entity_id") or "").startswith("competitor-")
        ),
        "warnings": list(sample.get("warnings") or []),
    }


def _entity_weight(sample: Mapping[str, Any], entity_id: str) -> float:
    mention = next(
        (item for item in sample.get("mentions") or [] if item.get("entity_id") == entity_id),
        None,
    )
    if mention is None:
        return 0.0
    rank = next(
        (
            item.get("rank")
            for item in sample.get("ranks") or []
            if item.get("entity_id") == entity_id
        ),
        None,
    )
    return _rank_weight(rank)


def _rank_weight(rank: Any) -> float:
    if rank is None:
        return 1.0
    # Keep this local helper deliberately identical to the visibility engine's
    # public methodology without importing a private implementation detail.
    import math

    return 1.0 / math.log2(int(rank) + 1)


def _summarize_attempts(
    attempts: Sequence[Mapping[str, Any]], measurements: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    attempt_count = len(attempts)
    success_count = len(measurements)
    mention_count = sum(bool(item["brand_mentioned"]) for item in measurements)
    competitor_count = sum(bool(item["competitor_mentioned"]) for item in measurements)
    first_count = sum(item["recommendation_rank"] == 1 for item in measurements)
    top3_count = sum(
        item["recommendation_rank"] is not None
        and int(item["recommendation_rank"]) <= 3
        for item in measurements
    )
    ranks = [
        int(item["recommendation_rank"])
        for item in measurements
        if item["recommendation_rank"] is not None
    ]
    citation_count = sum(bool(item["citation_present"]) for item in measurements)
    owned_citation_count = sum(
        bool(item["owned_citation_present"]) for item in measurements
    )
    sentiments = [
        float(item["sentiment_score"])
        for item in measurements
        if item["sentiment_score"] is not None
    ]
    brand_voice = sum(float(item["brand_weighted_voice"]) for item in measurements)
    competitor_voice = sum(
        float(item["competitor_weighted_voice"]) for item in measurements
    )
    voice_total = brand_voice + competitor_voice
    error_counts: dict[str, int] = defaultdict(int)
    for attempt in attempts:
        if attempt["status"] != "success":
            error_counts[str(attempt["error_code"])] += 1
    return {
        "attempt_count": attempt_count,
        "success_count": success_count,
        "success_rate": _ratio(success_count, attempt_count),
        "brand_mention_count": mention_count,
        "mention_rate_success": _ratio(mention_count, success_count),
        "mention_rate_all_attempts": _ratio(mention_count, attempt_count),
        "competitor_mention_count": competitor_count,
        "competitor_mention_rate_success": _ratio(competitor_count, success_count),
        "first_position_rate_success": _ratio(first_count, success_count),
        "top3_rate_success": _ratio(top3_count, success_count),
        "average_rank": sum(ranks) / len(ranks) if ranks else None,
        "ranked_answer_count": len(ranks),
        "citation_rate_success": _ratio(citation_count, success_count),
        "owned_citation_rate_success": _ratio(owned_citation_count, success_count),
        "share_of_voice": brand_voice / voice_total if voice_total else None,
        "sentiment_score": sum(sentiments) / len(sentiments) if sentiments else None,
        "source_reference_coverage": _ratio(
            sum(bool(item.get("source_reference")) for item in attempts), attempt_count
        ),
        "error_counts": dict(sorted(error_counts.items())),
    }


def _build_paired_comparison(
    attempts: Sequence[Mapping[str, Any]], measurements: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    by_phase: dict[str, dict[tuple[Any, ...], Mapping[str, Any]]] = {
        "baseline": {},
        "post_optimization": {},
    }
    for attempt in attempts:
        pair_key = (
            attempt["provider"],
            attempt["model"],
            attempt["surface"],
            attempt["web_search_enabled"],
            attempt["question_id"],
            attempt["replicate"],
        )
        by_phase[attempt["phase"]][pair_key] = attempt
    matched_keys = sorted(set(by_phase["baseline"]) & set(by_phase["post_optimization"]))
    measurement_by_id = {item["attempt_id"]: item for item in measurements}
    overall = _summarize_matched_pairs(
        matched_keys, by_phase=by_phase, measurement_by_id=measurement_by_id
    )
    questions_by_id = {item["id"]: item for item in QUESTION_MATRIX}
    by_cohort = []
    for cohort in sorted({item["cohort"] for item in QUESTION_MATRIX}):
        cohort_keys = [
            key
            for key in matched_keys
            if questions_by_id[key[4]]["cohort"] == cohort
        ]
        by_cohort.append(
            {
                "cohort": cohort,
                **_summarize_matched_pairs(
                    cohort_keys,
                    by_phase=by_phase,
                    measurement_by_id=measurement_by_id,
                ),
            }
        )
    return {**overall, "by_cohort": by_cohort}


def _summarize_matched_pairs(
    matched_keys: Sequence[tuple[Any, ...]],
    *,
    by_phase: Mapping[str, Mapping[tuple[Any, ...], Mapping[str, Any]]],
    measurement_by_id: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    baseline_success_attempts: list[Mapping[str, Any]] = []
    post_success_attempts: list[Mapping[str, Any]] = []
    baseline_success: list[Mapping[str, Any]] = []
    post_success: list[Mapping[str, Any]] = []
    pair_records = []
    for key in matched_keys:
        baseline = by_phase["baseline"][key]
        post = by_phase["post_optimization"][key]
        baseline_measurement = measurement_by_id.get(baseline["id"])
        post_measurement = measurement_by_id.get(post["id"])
        both_success = baseline_measurement is not None and post_measurement is not None
        if both_success:
            baseline_success_attempts.append(baseline)
            post_success_attempts.append(post)
            baseline_success.append(baseline_measurement)
            post_success.append(post_measurement)
        pair_records.append(
            {
                "provider": key[0],
                "model": key[1],
                "surface": key[2],
                "web_search_enabled": key[3],
                "question_id": key[4],
                "replicate": key[5],
                "baseline_attempt_id": baseline["id"],
                "post_attempt_id": post["id"],
                "both_success": both_success,
            }
        )

    baseline_metrics = _summarize_attempts(
        baseline_success_attempts, baseline_success
    )
    post_metrics = _summarize_attempts(post_success_attempts, post_success)
    delta_fields = (
        "mention_rate_success",
        "mention_rate_all_attempts",
        "first_position_rate_success",
        "top3_rate_success",
        "citation_rate_success",
        "owned_citation_rate_success",
        "share_of_voice",
        "sentiment_score",
    )
    deltas = {
        field: _difference(post_metrics.get(field), baseline_metrics.get(field))
        for field in delta_fields
    }
    deltas["average_rank_improvement"] = _difference(
        baseline_metrics.get("average_rank"), post_metrics.get("average_rank")
    )
    return {
        "matched_pair_count": len(matched_keys),
        "matched_success_pair_count": len(baseline_success),
        "baseline_metrics_on_success_pairs": baseline_metrics,
        "post_metrics_on_success_pairs": post_metrics,
        "deltas": deltas,
        "pairs": pair_records,
    }


def _quality_gates(
    manifest: Mapping[str, Any],
    cells: Sequence[Mapping[str, Any]],
    comparison: Mapping[str, Any],
) -> list[dict[str, Any]]:
    attempts = manifest["attempts"]
    providers_with_success = {
        item["provider"] for item in attempts if item["status"] == "success"
    }
    expected_cell_count = len(ALLOWED_PROVIDERS) * len(QUESTION_MATRIX)
    baseline_good_cells = sum(
        item["phase"] == "baseline"
        and item["metrics"]["success_count"] >= RECOMMENDED_REPLICATES
        for item in cells
    )
    post_good_cells = sum(
        item["phase"] == "post_optimization"
        and item["metrics"]["success_count"] >= RECOMMENDED_REPLICATES
        for item in cells
    )
    observation_hours = _minimum_post_observation_hours(manifest)
    return [
        {
            "id": "all-three-providers",
            "passed": set(ALLOWED_PROVIDERS) <= providers_with_success,
            "detail": (
                f"已有成功样本 provider={sorted(providers_with_success)}；要求="
                f"{list(ALLOWED_PROVIDERS)}。"
            ),
        },
        {
            "id": "baseline-five-replicates",
            "passed": baseline_good_cells == expected_cell_count,
            "detail": (
                f"达到每单元 {RECOMMENDED_REPLICATES} 个成功样本的基线单元 "
                f"{baseline_good_cells}/{expected_cell_count}。"
            ),
        },
        {
            "id": "post-five-replicates",
            "passed": post_good_cells == expected_cell_count,
            "detail": (
                f"达到每单元 {RECOMMENDED_REPLICATES} 个成功样本的复测单元 "
                f"{post_good_cells}/{expected_cell_count}。"
            ),
        },
        {
            "id": "paired-successes",
            "passed": comparison["matched_success_pair_count"] >= expected_cell_count * RECOMMENDED_REPLICATES,
            "detail": (
                f"成功配对 {comparison['matched_success_pair_count']}/"
                f"{expected_cell_count * RECOMMENDED_REPLICATES}；失败执行也必须保留。"
            ),
        },
        {
            "id": "observation-window",
            "passed": observation_hours is not None
            and observation_hours >= MINIMUM_OBSERVATION_HOURS,
            "detail": (
                "尚无优化后时间窗。"
                if observation_hours is None
                else f"最短优化后观察窗 {observation_hours:.1f} 小时；最低 {MINIMUM_OBSERVATION_HOURS:.0f} 小时。"
            ),
        },
        {
            "id": "raw-evidence-references",
            "passed": bool(attempts)
            and all(bool(item.get("source_reference")) for item in attempts),
            "detail": (
                "每次执行都应保留截图、录屏、导出文件或请求 ID 的引用；"
                "报告本身不复制回答原文。"
            ),
        },
        {
            "id": "official-product-source",
            "passed": bool(AUTHORIZED_SUBJECT["website"]),
            "detail": "当前没有独立核验的 DongClaw 官方产品域名，自有引用率不可作为销售证明。",
        },
    ]


def _study_status(
    manifest: Mapping[str, Any], comparison: Mapping[str, Any]
) -> str:
    if not manifest["attempts"]:
        return "NOT_EXECUTED"
    if comparison["matched_success_pair_count"] == 0:
        return "BASELINE_ONLY"
    observation_hours = _minimum_post_observation_hours(manifest)
    if observation_hours is None or observation_hours < MINIMUM_OBSERVATION_HOURS:
        return "SHORT_HORIZON_EXPLORATORY"
    return "OBSERVATIONAL_RETEST"


def _claim_boundary(status: str) -> str:
    if status == "NOT_EXECUTED":
        return "未执行真实模型采样；不能报告任何 GEO 效果。"
    if status == "BASELINE_ONLY":
        return "只有基线；不能报告优化提升。"
    if status == "SHORT_HORIZON_EXPLORATORY":
        return "短周期差值只验证流程，不构成 GEO 有效性或因果证明。"
    return "这是观察性复测；可报告变化，但不能承诺或推断必然的因果效果。"


def _minimum_post_observation_hours(manifest: Mapping[str, Any]) -> float | None:
    optimized_at_raw = manifest["study"].get("optimized_at")
    posts = [
        item for item in manifest["attempts"] if item["phase"] == "post_optimization"
    ]
    if optimized_at_raw is None or not posts:
        return None
    optimized_at = _parse_timestamp(optimized_at_raw, "optimized_at")
    return min(
        (_parse_timestamp(item["captured_at"], "captured_at") - optimized_at).total_seconds()
        / 3600
        for item in posts
    )


def _reject_forbidden_keys(value: Any, path: str = "root") -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            normalized = str(key).strip().lower()
            if any(part in normalized for part in _FORBIDDEN_KEY_PARTS):
                raise DongClawEvaluationError(f"{path}.{key} 不允许包含凭据或令牌字段")
            _reject_forbidden_keys(nested, f"{path}.{key}")
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            _reject_forbidden_keys(nested, f"{path}[{index}]")


def _require_exact_fields(value: Mapping[str, Any], expected: set[str], field: str) -> None:
    actual = set(value)
    if actual != expected:
        raise DongClawEvaluationError(
            f"{field} 字段不符合契约; missing={sorted(expected - actual)}, "
            f"unsupported={sorted(actual - expected)}"
        )


def _text(value: Any, field: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise DongClawEvaluationError(f"{field} 必须是字符串")
    text = value.strip()
    if not text or "\0" in text or len(text) > maximum:
        raise DongClawEvaluationError(f"{field} 为空或超出 {maximum} 字符")
    return text


def _optional_text(value: Any, field: str, maximum: int) -> str | None:
    if value in (None, ""):
        return None
    return _text(value, field, maximum)


def _enum(value: Any, allowed: Sequence[str], field: str) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise DongClawEvaluationError(f"{field} 必须是 {list(allowed)} 之一")
    return value


def _timestamp(value: Any, field: str) -> str:
    text = _text(value, field, 80)
    _parse_timestamp(text, field)
    return text


def _optional_timestamp(value: Any, field: str) -> str | None:
    if value in (None, ""):
        return None
    return _timestamp(value, field)


def _parse_timestamp(value: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DongClawEvaluationError(f"{field} 必须是 ISO-8601 时间") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise DongClawEvaluationError(f"{field} 必须包含时区")
    return parsed


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _difference(after: Any, before: Any) -> float | None:
    if after is None or before is None:
        return None
    return float(after) - float(before)


def _deep_copy(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False))


def _md(value: Any) -> str:
    if value is None:
        return "—"
    return str(value).replace("|", "\\|").replace("\n", " ")


def _pct(value: Any) -> str:
    return "—" if value is None else f"{float(value) * 100:.1f}%"


def _pp(value: Any) -> str:
    return "—" if value is None else f"{float(value) * 100:+.1f} 个百分点"


def _number(value: Any) -> str:
    return "—" if value is None else f"{float(value):.2f}"


__all__ = [
    "ALLOWED_PROVIDERS",
    "AUTHORIZED_SUBJECT",
    "DONGCLAW_EVALUATION_REPORT_VERSION",
    "DONGCLAW_EVALUATION_SCHEMA_VERSION",
    "DongClawEvaluationError",
    "PUBLIC_EVIDENCE_SNAPSHOT",
    "QUESTION_MATRIX",
    "build_dongclaw_input_template",
    "evaluate_dongclaw_manifest",
    "render_dongclaw_markdown",
]
