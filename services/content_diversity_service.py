"""Deterministic, auditable writing variation without inventing evidence.

The selector changes presentation choices only.  It never adds facts, sources,
claims, dates, or measurements.  A stable seed makes retries idempotent while
different topics/batch positions receive meaningfully different combinations.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping


DIVERSITY_VERSION = "content-diversity-v1"
MAX_DIVERSITY_SEED_LENGTH = 128

_ANGLES = (
    ("decision_criteria", "从决策标准切入，回答读者如何判断与选择"),
    ("mechanism_explainer", "从工作机制切入，解释为什么以及如何实现"),
    ("risk_boundary", "从风险与适用边界切入，说明何时适合或不适合"),
    ("implementation_path", "从落地路径切入，给出可执行步骤和检查点"),
    ("comparative_tradeoff", "从方案权衡切入，按明确维度比较取舍"),
)

_STRUCTURES = (
    ("answer_evidence_checklist", "直接答案 → 可核验证据 → 决策清单"),
    ("scenario_mechanism_boundary", "使用场景 → 实现机制 → 条件与边界"),
    ("problem_options_recommendation", "问题拆解 → 方案选项 → 有条件建议"),
    ("definition_steps_faq", "准确界定 → 操作步骤 → 精简 FAQ"),
    ("claim_method_result_limit", "原子结论 → 方法 → 结果 → 限制"),
)

_TONES = (
    ("technical_plain", "克制、清楚的技术说明口吻"),
    ("operator_guide", "面向实际操作者的指导口吻"),
    ("analyst_neutral", "中立分析口吻，明确区分事实与建议"),
    ("buyer_advisor", "面向采购或决策者的顾问口吻"),
)

_EVIDENCE_EMPHASIS = (
    ("method_and_scope", "优先呈现方法、适用范围与限制"),
    ("source_and_date", "有输入证据时优先写明来源与日期；没有则明确缺口"),
    ("comparison_dimensions", "优先使用有事实支撑的对比维度"),
    ("reproducible_steps", "优先呈现可复现步骤和验证条件"),
    ("atomic_claims", "把关键结论写成可独立抽取的短证据单元"),
)


class ContentDiversityError(ValueError):
    """Raised when an operator-supplied diversity seed is invalid."""


def build_content_diversity_profile(
    *,
    seed: object,
    topic: object,
    content_type: object = "",
    target_platform: object = "",
) -> dict[str, Any]:
    normalized_seed = str(seed or "").strip()
    if not normalized_seed:
        raise ContentDiversityError("内容差异化 seed 不能为空")
    if (
        len(normalized_seed) > MAX_DIVERSITY_SEED_LENGTH
        or any(marker in normalized_seed for marker in ("\r", "\n", "\0"))
    ):
        raise ContentDiversityError(
            f"内容差异化 seed 不能超过 {MAX_DIVERSITY_SEED_LENGTH} 个字符"
        )
    envelope = json.dumps(
        {
            "seed": normalized_seed,
            "topic": str(topic or "").strip(),
            "content_type": str(content_type or "").strip(),
            "target_platform": str(target_platform or "").strip(),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(envelope.encode("utf-8")).digest()
    angle = _ANGLES[digest[0] % len(_ANGLES)]
    structure = _STRUCTURES[digest[1] % len(_STRUCTURES)]
    tone = _TONES[digest[2] % len(_TONES)]
    evidence = _EVIDENCE_EMPHASIS[digest[3] % len(_EVIDENCE_EMPHASIS)]
    return {
        "version": DIVERSITY_VERSION,
        "seed_fingerprint": hashlib.sha256(
            normalized_seed.encode("utf-8")
        ).hexdigest()[:12],
        "angle": {"id": angle[0], "label": angle[1]},
        "structure": {"id": structure[0], "label": structure[1]},
        "tone": {"id": tone[0], "label": tone[1]},
        "evidence_emphasis": {"id": evidence[0], "label": evidence[1]},
        "fact_policy": "input_only_no_invention",
    }


def build_diversity_instruction(profile: Mapping[str, Any]) -> str:
    """Compile a profile into a bounded instruction for the writing model."""

    def label(key: str) -> str:
        value = profile.get(key)
        return str(value.get("label") or "").strip() if isinstance(value, Mapping) else ""

    return "\n".join(
        (
            "本次内容差异化编排（只改变表达，不改变事实）：",
            f"- 切入角度：{label('angle')}",
            f"- 文章结构：{label('structure')}",
            f"- 叙述语气：{label('tone')}",
            f"- 证据侧重：{label('evidence_emphasis')}",
            "- 事实边界：只能使用输入资料已有事实；不得为了差异化虚构数据、来源、案例、日期或结论。",
        )
    )


__all__ = [
    "ContentDiversityError",
    "DIVERSITY_VERSION",
    "MAX_DIVERSITY_SEED_LENGTH",
    "build_content_diversity_profile",
    "build_diversity_instruction",
]
