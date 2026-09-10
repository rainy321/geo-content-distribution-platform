"""Evidence-bound longitudinal comparison for black-box GEO experiments.

This module does not crawl pages, query search indexes, call an AI provider, or
infer hidden model state.  It consumes reports already produced by
``dongclaw_evaluation_service`` and adds the experiment structure that report
does not provide by itself: explicit treatment/control arms and day 7/14/30
checkpoints.  Comparisons use only exact provider/model/surface/search/question/
replicate matches with retained evidence references.
"""

from __future__ import annotations

import copy
import re
from collections import defaultdict
from datetime import datetime
from typing import Any, Mapping, Sequence

from services.dongclaw_evaluation_service import (
    DONGCLAW_EVALUATION_REPORT_VERSION,
)


GEO_BLACKBOX_EXPERIMENT_VERSION = "geo-blackbox-panel-1.0.0"
GEO_BLACKBOX_TRUTH_MATRIX_VERSION = "geo-blackbox-truth-matrix-1.0.0"

EXPERIMENT_ARMS = ("treatment", "control")
RETEST_CHECKPOINT_DAYS = (0, 7, 14, 30)
_POST_CHECKPOINT_DAYS = RETEST_CHECKPOINT_DAYS[1:]
_ALLOWED_PHASES = ("baseline", "post_optimization")
_SNAPSHOT_FIELDS = {
    "arm",
    "checkpoint_day",
    "unit_id",
    "phase",
    "evidence_reference",
    "report",
}
_HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_COMPARABLE_METRICS = (
    "brand_recall_rate_success_unbranded",
    "brand_mention_rate_success",
    "citation_rate_success",
    "ranked_recommendation_rate_success",
    "average_prominence_weight_success",
)


class GEOBlackboxExperimentError(ValueError):
    """Raised when imported evidence cannot support an auditable comparison."""


def build_geo_blackbox_truth_matrix() -> dict[str, Any]:
    """Return an honest audit of the requested black-box GEO capabilities.

    ``existing_state`` describes the three pre-existing services.  The two
    longitudinal rows additionally disclose the import-only gap closed by this
    module.  No row claims that model-internal crawl/index/retrieval state can be
    observed from an answer.
    """

    rows = (
        (
            "question_clusters",
            "PARTIAL",
            "PARTIAL",
            "DongClaw has a fixed seven-question matrix with region, intent and cohort, but no generic cluster discovery engine.",
        ),
        (
            "brand_evidence_graph",
            "MISSING",
            "MISSING",
            "The public evidence snapshot is a flat finding list; it is not a claim/entity/source graph.",
        ),
        (
            "recall_citation_separation",
            "IMPLEMENTED",
            "IMPLEMENTED",
            "Unbranded cohorts and brand mentions are measured separately from explicit response citations.",
        ),
        (
            "public",
            "PARTIAL",
            "PARTIAL",
            "A dated public-evidence snapshot exists, but the evaluator performs no live availability check.",
        ),
        (
            "crawl",
            "UNOBSERVABLE_INTERNAL",
            "UNOBSERVABLE_INTERNAL",
            "No provider crawl telemetry exists; an answer or citation cannot prove that hidden crawler state.",
        ),
        (
            "index",
            "NOT_MEASURED",
            "NOT_MEASURED",
            "No external index inspection is imported, and model inclusion must not be inferred from publication alone.",
        ),
        (
            "retrieval",
            "PARTIAL",
            "PARTIAL",
            "Captured model answers and the web-search setting are retained, but internal candidate retrieval is not observable.",
        ),
        (
            "citation",
            "IMPLEMENTED",
            "IMPLEMENTED",
            "Explicit HTTP(S) URLs in answer text are normalized and classified; implicit sources are not invented.",
        ),
        (
            "brand_mention",
            "IMPLEMENTED",
            "IMPLEMENTED",
            "Versioned exact, alias, domain and reviewable fuzzy matching produces per-answer evidence.",
        ),
        (
            "prominence",
            "PARTIAL",
            "PARTIAL",
            "Recommendation rank, first/top-three rates and position-weighted voice are heuristics, not provider attention telemetry.",
        ),
        (
            "fact_accuracy",
            "NOT_MEASURED",
            "NOT_MEASURED",
            "There is no independently verified product fact set against which answer claims can be checked.",
        ),
        (
            "recommendation_share",
            "PARTIAL",
            "PARTIAL",
            "Ranked recommendation presence can be reported; current evidence does not capture every implicit recommendation.",
        ),
        (
            "retest_day_7_14_30",
            "MISSING",
            "IMPLEMENTED_IMPORT_ONLY",
            "This service now validates separate day 7/14/30 snapshots; it never fabricates missing checkpoints.",
        ),
        (
            "matched_control",
            "MISSING",
            "IMPLEMENTED_IMPORT_ONLY",
            "This service now calculates exact-match treatment/control descriptive difference-in-differences when both arms are imported.",
        ),
    )
    return {
        "version": GEO_BLACKBOX_TRUTH_MATRIX_VERSION,
        "rows": [
            {
                "capability": capability,
                "existing_state": existing_state,
                "state_after_this_service": state_after,
                "boundary": boundary,
            }
            for capability, existing_state, state_after, boundary in rows
        ],
        "state_semantics": {
            "IMPLEMENTED": "The repository computes this from retained evidence.",
            "IMPLEMENTED_IMPORT_ONLY": "The repository can evaluate imported evidence but does not collect it automatically.",
            "PARTIAL": "A narrower observable proxy exists and its boundary is disclosed.",
            "NOT_MEASURED": "No defensible measurement is currently available.",
            "MISSING": "No implementation existed before this service.",
            "UNOBSERVABLE_INTERNAL": "Black-box output cannot establish this provider-internal state.",
        },
    }


def evaluate_geo_blackbox_panel(
    snapshots: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Evaluate imported baseline/day-7/day-14/day-30 treatment/control reports.

    Every snapshot is one immutable DongClaw evaluation report plus an external
    evidence reference.  A comparison is emitted only for successful records
    sharing the exact sampling key in both arms and both time points.  Missing
    snapshots remain explicit blockers rather than being treated as zero.
    """

    normalized = _validate_snapshots(snapshots)
    by_slot = {(item["arm"], item["checkpoint_day"]): item for item in normalized}
    slots = []
    for arm in EXPERIMENT_ARMS:
        for day in RETEST_CHECKPOINT_DAYS:
            item = by_slot.get((arm, day))
            slots.append(
                {
                    "arm": arm,
                    "checkpoint_day": day,
                    "present": item is not None,
                    "unit_id": item["unit_id"] if item else None,
                    "evidence_reference": item["evidence_reference"] if item else None,
                    "metrics": copy.deepcopy(item["metrics"]) if item else None,
                }
            )

    comparisons = [
        _checkpoint_comparison(day, by_slot)
        for day in _POST_CHECKPOINT_DAYS
    ]
    blockers = _panel_blockers(by_slot, comparisons)
    return {
        "version": GEO_BLACKBOX_EXPERIMENT_VERSION,
        "status": "READY_FOR_DESCRIPTIVE_COMPARISON" if not blockers else "INCOMPLETE",
        "claim_boundary": (
            "Matched differences are descriptive black-box observations, not proof of crawl, index, retrieval internals, or causal GEO lift."
        ),
        "checkpoint_days": list(RETEST_CHECKPOINT_DAYS),
        "metric_definitions": {
            "brand_recall_rate_all_attempts": "Unbranded-question brand mentions divided by all attempts, including failed attempts.",
            "brand_recall_rate_success_unbranded": "Unbranded-question brand mentions divided by successful captured answers to unbranded questions.",
            "brand_mention_rate_success": "Brand mentions divided by successful captured answers.",
            "citation_rate_success": "Successful answers containing at least one explicit HTTP(S) URL divided by successful answers.",
            "ranked_recommendation_rate_success": "Successful answers assigning the brand a recommendation rank divided by successful answers.",
            "average_prominence_weight_success": "Mean existing rank-weighted brand voice over successful answers.",
            "fact_accuracy": "Not measured because the current report has no independently verified claim graph.",
        },
        "slots": slots,
        "question_clusters": _question_clusters(
            snapshots[0]["report"]["question_matrix"] if snapshots else []
        ),
        "checkpoint_comparisons": comparisons,
        "blockers": blockers,
        "truth_matrix": build_geo_blackbox_truth_matrix(),
    }


def _validate_snapshots(
    snapshots: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    if isinstance(snapshots, (str, bytes)) or not isinstance(snapshots, Sequence):
        raise GEOBlackboxExperimentError("snapshots must be an array")
    if len(snapshots) > len(EXPERIMENT_ARMS) * len(RETEST_CHECKPOINT_DAYS):
        raise GEOBlackboxExperimentError("snapshots contains more than the eight panel slots")

    normalized: list[dict[str, Any]] = []
    seen_slots: set[tuple[str, int]] = set()
    subject = None
    questions = None
    optimized_at = None
    visibility_algorithm = None
    for index, raw in enumerate(snapshots):
        field = f"snapshots[{index}]"
        if not isinstance(raw, Mapping):
            raise GEOBlackboxExperimentError(f"{field} must be an object")
        if set(raw) != _SNAPSHOT_FIELDS:
            raise GEOBlackboxExperimentError(
                f"{field} fields must equal {sorted(_SNAPSHOT_FIELDS)}"
            )
        arm = _enum(raw.get("arm"), EXPERIMENT_ARMS, f"{field}.arm")
        day = raw.get("checkpoint_day")
        if isinstance(day, bool) or day not in RETEST_CHECKPOINT_DAYS:
            raise GEOBlackboxExperimentError(
                f"{field}.checkpoint_day must be one of {RETEST_CHECKPOINT_DAYS}"
            )
        phase = _enum(raw.get("phase"), _ALLOWED_PHASES, f"{field}.phase")
        expected_phase = "baseline" if day == 0 else "post_optimization"
        if phase != expected_phase:
            raise GEOBlackboxExperimentError(
                f"{field}.phase must be {expected_phase} for day {day}"
            )
        slot = (arm, int(day))
        if slot in seen_slots:
            raise GEOBlackboxExperimentError(f"duplicate panel slot {slot}")
        seen_slots.add(slot)

        report = raw.get("report")
        if not isinstance(report, Mapping):
            raise GEOBlackboxExperimentError(f"{field}.report must be an object")
        if report.get("report_version") != DONGCLAW_EVALUATION_REPORT_VERSION:
            raise GEOBlackboxExperimentError(f"{field}.report has an unsupported version")
        current_subject = report.get("subject")
        current_questions = report.get("question_matrix")
        if not isinstance(current_subject, Mapping) or not isinstance(current_questions, list):
            raise GEOBlackboxExperimentError(f"{field}.report contract is incomplete")
        if subject is None:
            subject = current_subject
            questions = current_questions
        elif current_subject != subject or current_questions != questions:
            raise GEOBlackboxExperimentError(
                "all snapshots must use the exact same subject and question matrix"
            )

        study = report.get("study")
        if not isinstance(study, Mapping) or not study.get("optimized_at"):
            raise GEOBlackboxExperimentError(
                f"{field}.report.study.optimized_at is required for panel alignment"
            )
        current_optimized_at = _timestamp(
            study.get("optimized_at"), f"{field}.report.study.optimized_at"
        )
        if optimized_at is None:
            optimized_at = current_optimized_at
        elif current_optimized_at != optimized_at:
            raise GEOBlackboxExperimentError(
                "all snapshots must use the same optimized_at instant"
            )

        quality_gates = report.get("quality_gates")
        raw_reference_gate = next(
            (
                gate
                for gate in quality_gates or []
                if isinstance(gate, Mapping)
                and gate.get("id") == "raw-evidence-references"
            ),
            None,
        )
        if not raw_reference_gate or raw_reference_gate.get("passed") is not True:
            raise GEOBlackboxExperimentError(
                f"{field}.report must retain source_reference for every attempt"
            )
        methodology = report.get("methodology")
        current_algorithm = (
            methodology.get("visibility_algorithm")
            if isinstance(methodology, Mapping)
            else None
        )
        current_algorithm = _text(
            current_algorithm,
            f"{field}.report.methodology.visibility_algorithm",
            160,
        )
        if visibility_algorithm is None:
            visibility_algorithm = current_algorithm
        elif current_algorithm != visibility_algorithm:
            raise GEOBlackboxExperimentError(
                "all snapshots must use the same visibility algorithm version"
            )

        records = _phase_records(report, phase, field)
        if not records:
            raise GEOBlackboxExperimentError(
                f"{field}.report has no successful measurements for {phase}"
            )
        _validate_checkpoint_timing(
            records,
            checkpoint_day=int(day),
            optimized_at=current_optimized_at,
            field=field,
        )
        metrics = _snapshot_metrics(report, phase, records, current_questions, field)
        normalized.append(
            {
                "arm": arm,
                "checkpoint_day": int(day),
                "unit_id": _text(raw.get("unit_id"), f"{field}.unit_id", 160),
                "phase": phase,
                "evidence_reference": _text(
                    raw.get("evidence_reference"),
                    f"{field}.evidence_reference",
                    2_048,
                ),
                "records": records,
                "records_by_key": {_record_key(item): item for item in records},
                "metrics": metrics,
            }
        )

    for arm in EXPERIMENT_ARMS:
        unit_ids = {
            item["unit_id"] for item in normalized if item["arm"] == arm
        }
        if len(unit_ids) > 1:
            raise GEOBlackboxExperimentError(
                f"{arm} must keep the same unit_id across all checkpoints"
            )
    snapshot_references = [item["evidence_reference"] for item in normalized]
    if len(snapshot_references) != len(set(snapshot_references)):
        raise GEOBlackboxExperimentError(
            "each panel slot must use a distinct evidence_reference"
        )
    raw_references: dict[str, tuple[str, int]] = {}
    for item in normalized:
        for record in item["records"]:
            reference = record["source_reference"]
            existing_slot = raw_references.get(reference)
            current_slot = (item["arm"], item["checkpoint_day"])
            if existing_slot is not None and existing_slot != current_slot:
                raise GEOBlackboxExperimentError(
                    "the same raw source_reference cannot be reused across panel slots"
                )
            raw_references[reference] = current_slot
    for day in RETEST_CHECKPOINT_DAYS:
        treatment = next(
            (item for item in normalized if item["arm"] == "treatment" and item["checkpoint_day"] == day),
            None,
        )
        control = next(
            (item for item in normalized if item["arm"] == "control" and item["checkpoint_day"] == day),
            None,
        )
        if treatment and control and treatment["unit_id"] == control["unit_id"]:
            raise GEOBlackboxExperimentError(
                f"day {day} treatment and control must use different unit_id values"
            )
    return normalized


def _phase_records(
    report: Mapping[str, Any], phase: str, field: str
) -> list[dict[str, Any]]:
    raw_records = report.get("measurement_records")
    if not isinstance(raw_records, list):
        raise GEOBlackboxExperimentError(f"{field}.report.measurement_records must be an array")
    records = []
    seen_keys: set[tuple[Any, ...]] = set()
    question_cohorts = {
        str(item.get("id")): str(item.get("cohort"))
        for item in report.get("question_matrix") or []
        if isinstance(item, Mapping)
    }
    for index, raw in enumerate(raw_records):
        if not isinstance(raw, Mapping) or raw.get("phase") != phase:
            continue
        source_reference = _text(
            raw.get("source_reference"),
            f"{field}.report.measurement_records[{index}].source_reference",
            2_048,
        )
        response_sha256 = str(raw.get("response_sha256") or "")
        if not _HASH_PATTERN.fullmatch(response_sha256):
            raise GEOBlackboxExperimentError(
                f"{field}.report.measurement_records[{index}].response_sha256 is invalid"
            )
        record = {
            "attempt_id": _text(raw.get("attempt_id"), "attempt_id", 160),
            "provider": _text(raw.get("provider"), "provider", 160),
            "model": _text(raw.get("model"), "model", 160),
            "surface": _text(raw.get("surface"), "surface", 40),
            "web_search_enabled": bool(raw.get("web_search_enabled")),
            "question_id": _text(raw.get("question_id"), "question_id", 160),
            "question_cohort": question_cohorts.get(str(raw.get("question_id")), ""),
            "replicate": raw.get("replicate"),
            "captured_at": _timestamp(
                raw.get("captured_at"),
                f"{field}.report.measurement_records[{index}].captured_at",
            ),
            "response_sha256": response_sha256,
            "source_reference": source_reference,
            "brand_mentioned": bool(raw.get("brand_mentioned")),
            "citation_present": bool(raw.get("citation_present")),
            "recommendation_rank": raw.get("recommendation_rank"),
            "brand_weighted_voice": _non_negative_number(
                raw.get("brand_weighted_voice"), "brand_weighted_voice"
            ),
        }
        if isinstance(record["replicate"], bool) or not isinstance(record["replicate"], int):
            raise GEOBlackboxExperimentError("measurement replicate must be an integer")
        rank = record["recommendation_rank"]
        if rank is not None and (
            isinstance(rank, bool) or not isinstance(rank, int) or rank < 1
        ):
            raise GEOBlackboxExperimentError("recommendation_rank must be a positive integer")
        key = _record_key(record)
        if key in seen_keys:
            raise GEOBlackboxExperimentError(
                f"{field}.report has duplicate exact sampling key {key}"
            )
        seen_keys.add(key)
        records.append(record)
    return records


def _validate_checkpoint_timing(
    records: Sequence[Mapping[str, Any]],
    *,
    checkpoint_day: int,
    optimized_at: datetime,
    field: str,
) -> None:
    lower = float("-inf") if checkpoint_day == 0 else float(checkpoint_day)
    upper_by_day = {0: 0.0, 7: 14.0, 14: 30.0, 30: float("inf")}
    upper = upper_by_day[checkpoint_day]
    for record in records:
        captured_at = record["captured_at"]
        elapsed_days = (captured_at - optimized_at).total_seconds() / 86_400
        if checkpoint_day == 0:
            valid = elapsed_days < 0
        else:
            valid = lower <= elapsed_days < upper
        if not valid:
            interval = (
                "before optimized_at"
                if checkpoint_day == 0
                else f"in [{checkpoint_day}, {upper if upper != float('inf') else 'infinity'}) days"
            )
            raise GEOBlackboxExperimentError(
                f"{field} contains an observation outside checkpoint window {interval}"
            )


def _snapshot_metrics(
    report: Mapping[str, Any],
    phase: str,
    records: Sequence[Mapping[str, Any]],
    questions: Sequence[Mapping[str, Any]],
    field: str,
) -> dict[str, Any]:
    cells = report.get("question_results")
    if not isinstance(cells, list):
        raise GEOBlackboxExperimentError(f"{field}.report.question_results must be an array")
    attempt_count = 0
    success_count = 0
    for cell in cells:
        if not isinstance(cell, Mapping) or cell.get("phase") != phase:
            continue
        metrics = cell.get("metrics")
        if not isinstance(metrics, Mapping):
            raise GEOBlackboxExperimentError(f"{field}.report contains invalid cell metrics")
        attempt_count += _non_negative_int(metrics.get("attempt_count"), "attempt_count")
        success_count += _non_negative_int(metrics.get("success_count"), "success_count")
    if success_count != len(records):
        raise GEOBlackboxExperimentError(
            f"{field}.report success count does not match measurement records"
        )

    question_cohorts = {
        str(item.get("id")): str(item.get("cohort"))
        for item in questions
        if isinstance(item, Mapping)
    }
    unbranded_attempts = sum(
        _non_negative_int((cell.get("metrics") or {}).get("attempt_count"), "attempt_count")
        for cell in cells
        if isinstance(cell, Mapping)
        and cell.get("phase") == phase
        and isinstance(cell.get("question"), Mapping)
        and cell["question"].get("cohort") == "unbranded"
    )
    unbranded_mentions = sum(
        bool(item["brand_mentioned"])
        for item in records
        if question_cohorts.get(str(item["question_id"])) == "unbranded"
    )
    mention_count = sum(bool(item["brand_mentioned"]) for item in records)
    citation_count = sum(bool(item["citation_present"]) for item in records)
    ranked_count = sum(item["recommendation_rank"] is not None for item in records)
    prominence_sum = sum(float(item["brand_weighted_voice"]) for item in records)
    return {
        "attempt_count": attempt_count,
        "success_count": success_count,
        "brand_recall_rate_all_attempts": _ratio(unbranded_mentions, unbranded_attempts),
        "brand_recall_rate_success_unbranded": _ratio(
            unbranded_mentions,
            sum(
                question_cohorts.get(str(item["question_id"])) == "unbranded"
                for item in records
            ),
        ),
        "brand_mention_rate_success": _ratio(mention_count, success_count),
        "citation_rate_success": _ratio(citation_count, success_count),
        "ranked_recommendation_rate_success": _ratio(ranked_count, success_count),
        "average_prominence_weight_success": _ratio(prominence_sum, success_count),
        "fact_accuracy": None,
        "fact_accuracy_state": "NOT_MEASURED",
        "exact_sample_key_count": len(records),
    }


def _checkpoint_comparison(
    day: int,
    by_slot: Mapping[tuple[str, int], Mapping[str, Any]],
) -> dict[str, Any]:
    required = {
        "treatment_baseline": by_slot.get(("treatment", 0)),
        "treatment_checkpoint": by_slot.get(("treatment", day)),
        "control_baseline": by_slot.get(("control", 0)),
        "control_checkpoint": by_slot.get(("control", day)),
    }
    missing = [name for name, value in required.items() if value is None]
    if missing:
        return {
            "checkpoint_day": day,
            "status": "INCOMPLETE",
            "missing_slots": missing,
            "matched_key_count": 0,
            "metrics": {},
        }

    key_sets = [set(item["records_by_key"]) for item in required.values()]
    matched_keys = sorted(set.intersection(*key_sets)) if key_sets else []
    metrics: dict[str, Any] = {}
    for metric in _COMPARABLE_METRICS:
        values = {
            name: _metric_for_records(
                metric,
                [item["records_by_key"][key] for key in matched_keys],
                questions=None,
            )
            for name, item in required.items()
        }
        treatment_change = _difference(
            values["treatment_checkpoint"], values["treatment_baseline"]
        )
        control_change = _difference(
            values["control_checkpoint"], values["control_baseline"]
        )
        metrics[metric] = {
            **values,
            "treatment_change": treatment_change,
            "control_change": control_change,
            "difference_in_differences": _difference(
                treatment_change, control_change
            ),
        }
    return {
        "checkpoint_day": day,
        "status": "COMPARABLE" if matched_keys else "NO_EXACT_MATCHES",
        "missing_slots": [],
        "matched_key_count": len(matched_keys),
        "metrics": metrics if matched_keys else {},
    }


def _metric_for_records(
    metric: str,
    records: Sequence[Mapping[str, Any]],
    questions: Any,
) -> float | None:
    del questions  # Reserved for future generic question-cluster adapters.
    if not records:
        return None
    if metric == "brand_recall_rate_success_unbranded":
        unbranded = [
            item for item in records if item.get("question_cohort") == "unbranded"
        ]
        return _ratio(
            sum(bool(item["brand_mentioned"]) for item in unbranded),
            len(unbranded),
        )
    if metric == "brand_mention_rate_success":
        return _ratio(sum(bool(item["brand_mentioned"]) for item in records), len(records))
    if metric == "citation_rate_success":
        return _ratio(sum(bool(item["citation_present"]) for item in records), len(records))
    if metric == "ranked_recommendation_rate_success":
        return _ratio(
            sum(item["recommendation_rank"] is not None for item in records),
            len(records),
        )
    if metric == "average_prominence_weight_success":
        return _ratio(
            sum(float(item["brand_weighted_voice"]) for item in records),
            len(records),
        )
    raise GEOBlackboxExperimentError(f"unsupported metric {metric}")


def _panel_blockers(
    by_slot: Mapping[tuple[str, int], Mapping[str, Any]],
    comparisons: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    blockers = []
    for arm in EXPERIMENT_ARMS:
        for day in RETEST_CHECKPOINT_DAYS:
            if (arm, day) not in by_slot:
                blockers.append(
                    {
                        "code": "SNAPSHOT_MISSING",
                        "arm": arm,
                        "checkpoint_day": day,
                        "detail": f"No imported {arm} snapshot exists for day {day}.",
                    }
                )
    for comparison in comparisons:
        if comparison["status"] == "NO_EXACT_MATCHES":
            blockers.append(
                {
                    "code": "NO_EXACT_MATCHES",
                    "checkpoint_day": comparison["checkpoint_day"],
                    "detail": "No provider/model/surface/search/question/replicate key exists in all four required snapshots.",
                }
            )
    return blockers


def _record_key(record: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        record.get("provider"),
        record.get("model"),
        record.get("surface"),
        bool(record.get("web_search_enabled")),
        record.get("question_id"),
        record.get("replicate"),
    )


def _question_clusters(questions: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for question in questions:
        if not isinstance(question, Mapping):
            continue
        grouped[str(question.get("cohort") or "unclassified")].append(
            {
                "question_id": str(question.get("id") or ""),
                "region": str(question.get("region") or ""),
                "intent": str(question.get("intent") or ""),
            }
        )
    return [
        {"cluster": cluster, "questions": items}
        for cluster, items in sorted(grouped.items())
    ]


def _enum(value: Any, allowed: Sequence[str], field: str) -> str:
    normalized = str(value or "").strip()
    if normalized not in allowed:
        raise GEOBlackboxExperimentError(f"{field} must be one of {tuple(allowed)}")
    return normalized


def _text(value: Any, field: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise GEOBlackboxExperimentError(f"{field} must be text")
    normalized = value.strip()
    if not normalized or "\0" in normalized or len(normalized) > maximum:
        raise GEOBlackboxExperimentError(f"{field} is empty or too long")
    return normalized


def _non_negative_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise GEOBlackboxExperimentError(f"{field} must be a non-negative integer")
    return value


def _non_negative_number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise GEOBlackboxExperimentError(f"{field} must be a non-negative number")
    return float(value)


def _timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise GEOBlackboxExperimentError(f"{field} must be an ISO-8601 timestamp")
    normalized = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise GEOBlackboxExperimentError(
            f"{field} must be an ISO-8601 timestamp"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise GEOBlackboxExperimentError(f"{field} must include a timezone")
    return parsed


def _ratio(numerator: float | int, denominator: int) -> float | None:
    return float(numerator) / denominator if denominator else None


def _difference(after: Any, before: Any) -> float | None:
    if after is None or before is None:
        return None
    return float(after) - float(before)


__all__ = [
    "EXPERIMENT_ARMS",
    "GEO_BLACKBOX_EXPERIMENT_VERSION",
    "GEO_BLACKBOX_TRUTH_MATRIX_VERSION",
    "GEOBlackboxExperimentError",
    "RETEST_CHECKPOINT_DAYS",
    "build_geo_blackbox_truth_matrix",
    "evaluate_geo_blackbox_panel",
]
