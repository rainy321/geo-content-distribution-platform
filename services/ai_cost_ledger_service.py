"""Auditable AI token and estimated-cost ledger.

Only operations that actually invoke an AI model belong in this ledger.
Browser automation and platform publishing are deliberately excluded: calling
those actions "tokens" would give customers a false cost number.
"""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import closing, nullcontext
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Mapping, Sequence


AI_COST_LEDGER_VERSION = "ai-cost-ledger-1.4.0"
AI_PRICE_CATALOG_ENV = "AI_PRICE_CATALOG_JSON"
AI_USAGE_OPERATIONS = frozenset(
    {
        "generate",
        "optimize",
        "evaluate",
        "platform_adapt",
        "visibility_sample",
        "image_generate",
        "connection_test",
    }
)
AI_USAGE_STATUSES = frozenset({"reported", "estimated", "unknown"})


class AIUsageLedgerError(ValueError):
    """Raised when an AI usage record or summary request is invalid."""


def record_ai_usage(
    database_path: str | Path,
    *,
    operation: str,
    provider: str,
    model: str,
    usage: Mapping[str, Any] | None,
    usage_status: str | None = None,
    project_id: int | None = None,
    article_id: int | None = None,
    reference_type: str = "",
    reference_id: str | int = "",
    pricing_catalog: Mapping[str, Any] | None = None,
    metadata: Mapping[str, Any] | None = None,
    connection: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """Create one immutable price snapshot and return its public record.

    ``pricing_catalog`` is injectable for tests. In production the catalog is
    read from ``AI_PRICE_CATALOG_JSON``. A missing price never becomes zero:
    the record remains ``unpriced`` so the UI cannot misrepresent cost.
    """

    normalized_operation = str(operation or "").strip().lower()
    if normalized_operation not in AI_USAGE_OPERATIONS:
        raise AIUsageLedgerError(
            "operation 仅支持 generate、optimize、evaluate、platform_adapt、visibility_sample、image_generate、connection_test；"
            "平台发布本身不计 Token"
        )
    normalized_model = _bounded_text(model, "model", 200, fallback="unknown")
    normalized_provider = canonicalize_provider(
        _bounded_text(provider, "provider", 120, fallback="unknown"),
        normalized_model,
    )
    normalized_reference_type = _bounded_optional_text(reference_type, "reference_type", 80)
    normalized_reference_id = _bounded_optional_text(reference_id, "reference_id", 160)
    if bool(normalized_reference_type) != bool(normalized_reference_id):
        raise AIUsageLedgerError("reference_type 和 reference_id 必须同时提供")

    tokens = normalize_usage(usage)
    normalized_usage_status = str(usage_status or "").strip().lower()
    if not normalized_usage_status:
        normalized_usage_status = "reported" if tokens else "unknown"
    if normalized_usage_status not in AI_USAGE_STATUSES:
        raise AIUsageLedgerError("usage_status 必须是 reported、estimated 或 unknown")
    if normalized_usage_status in {"reported", "estimated"} and not tokens:
        normalized_usage_status = "unknown"
    if normalized_usage_status == "unknown":
        tokens = {}

    price = resolve_price_snapshot(
        provider=normalized_provider,
        model=normalized_model,
        pricing_catalog=pricing_catalog,
    )
    cost = _calculate_cost(tokens=tokens, usage_status=normalized_usage_status, price=price)
    safe_metadata = _normalize_metadata(metadata)

    own_connection = connection is None
    conn_context = closing(_connect(database_path)) if own_connection else nullcontext(connection)
    with conn_context as conn:
        transaction = conn if own_connection else nullcontext()
        with transaction:
            if own_connection:
                conn.execute("BEGIN IMMEDIATE")
            try:
                cursor = conn.execute(
                    """
                    INSERT INTO ai_usage_ledger (
                        project_id, article_id, operation, provider, model,
                        reference_type, reference_id, usage_status,
                        input_tokens, output_tokens, total_tokens,
                        cached_tokens, reasoning_tokens,
                        input_price_per_million, output_price_per_million,
                        estimated_cost, currency, cost_status, pricing_source,
                        metadata_json, ledger_version
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        _optional_positive_int(project_id, "project_id"),
                        _optional_positive_int(article_id, "article_id"),
                        normalized_operation,
                        normalized_provider,
                        normalized_model,
                        normalized_reference_type,
                        normalized_reference_id,
                        normalized_usage_status,
                        tokens.get("input_tokens"),
                        tokens.get("output_tokens"),
                        tokens.get("total_tokens"),
                        tokens.get("cached_tokens"),
                        tokens.get("reasoning_tokens"),
                        price.get("input_price_per_million"),
                        price.get("output_price_per_million"),
                        cost.get("estimated_cost"),
                        price.get("currency", ""),
                        cost["cost_status"],
                        price.get("pricing_source", "unconfigured"),
                        _encode_json(safe_metadata),
                        AI_COST_LEDGER_VERSION,
                    ),
                )
                row = _fetch_record(conn, cursor.lastrowid)
            except sqlite3.IntegrityError:
                if not normalized_reference_type:
                    raise
                row = conn.execute(
                    """
                    SELECT * FROM ai_usage_ledger
                    WHERE operation = ? AND reference_type = ? AND reference_id = ?
                    """,
                    (
                        normalized_operation,
                        normalized_reference_type,
                        normalized_reference_id,
                    ),
                ).fetchone()
                if row is None:
                    raise
    return _serialize_record(row)


def attach_article_to_usage(
    database_path: str | Path,
    *,
    operation: str,
    reference_type: str,
    reference_id: str | int,
    article_id: int,
    connection: sqlite3.Connection | None = None,
) -> None:
    """Attach a saved article to an existing operation without changing cost."""

    if str(operation or "").strip().lower() not in AI_USAGE_OPERATIONS:
        raise AIUsageLedgerError("未知的 AI operation")
    normalized_reference_type = _bounded_optional_text(reference_type, "reference_type", 80)
    normalized_reference_id = _bounded_optional_text(reference_id, "reference_id", 160)
    normalized_article_id = _optional_positive_int(article_id, "article_id")
    own_connection = connection is None
    conn_context = closing(_connect(database_path)) if own_connection else nullcontext(connection)
    with conn_context as conn:
        transaction = conn if own_connection else nullcontext()
        with transaction:
            if own_connection:
                conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                UPDATE ai_usage_ledger
                SET article_id = COALESCE(article_id, ?)
                WHERE operation = ? AND reference_type = ? AND reference_id = ?
                """,
                (
                    normalized_article_id,
                    str(operation).strip().lower(),
                    normalized_reference_type,
                    normalized_reference_id,
                ),
            )


def get_project_ai_cost_summary(
    database_path: str | Path,
    *,
    project_id: int,
    limit: int = 50,
) -> dict[str, Any]:
    """Return project totals, honest unknowns, breakdowns and recent records."""

    normalized_project_id = _optional_positive_int(project_id, "project_id")
    try:
        normalized_limit = int(limit)
    except (TypeError, ValueError) as exc:
        raise AIUsageLedgerError("limit 必须是正整数") from exc
    if not 1 <= normalized_limit <= 200:
        raise AIUsageLedgerError("limit 必须在 1 到 200 之间")

    with closing(_connect(database_path)) as conn:
        if conn.execute(
            "SELECT 1 FROM projects WHERE id = ?", (normalized_project_id,)
        ).fetchone() is None:
            raise AIUsageLedgerError("品牌项目不存在")
        rows = conn.execute(
            """
            SELECT * FROM ai_usage_ledger
            WHERE project_id = ?
            ORDER BY created_at DESC, id DESC
            """,
            (normalized_project_id,),
        ).fetchall()

    records = [_serialize_record(row) for row in rows]
    by_operation = _group_records(records, ("operation",))
    # Old rows may contain ``dashscope`` while new Qwen rows use the canonical
    # ``qwen`` key. Aggregate through the canonical view without rewriting the
    # immutable historical receipts themselves.
    by_model = _group_records(
        [
            {**record, "provider": record["canonical_provider"]}
            for record in records
        ],
        ("provider", "model"),
    )
    cost_totals: dict[str, Decimal] = {}
    for record in records:
        if record["cost_status"] != "calculated" or not record["estimated_cost"]:
            continue
        currency = record["currency"] or "UNKNOWN"
        cost_totals[currency] = cost_totals.get(currency, Decimal("0")) + Decimal(
            record["estimated_cost"]
        )

    known_token_records = [record for record in records if record["total_tokens"] is not None]
    return {
        "ledger_version": AI_COST_LEDGER_VERSION,
        "project_id": normalized_project_id,
        "summary": {
            "operation_count": len(records),
            "known_total_tokens": sum(record["total_tokens"] or 0 for record in known_token_records),
            "known_token_record_count": len(known_token_records),
            "unknown_usage_count": sum(record["usage_status"] == "unknown" for record in records),
            "unpriced_count": sum(record["cost_status"] == "unpriced" for record in records),
            "calculated_costs": [
                {"currency": currency, "estimated_cost": _decimal_text(amount)}
                for currency, amount in sorted(cost_totals.items())
            ],
        },
        "by_operation": by_operation,
        "by_model": by_model,
        "recent": records[:normalized_limit],
        "publishing": {
            "token_usage_applicable": False,
            "message": "发布到媒体平台若未调用模型，不产生也不记录 Token；浏览器上传成本不能伪装成模型成本。",
        },
        "pricing": {
            "configuration": AI_PRICE_CATALOG_ENV,
            "semantics": "每条记录保存调用当时的单价快照；未配置价格时成本保持未知，不按 0 处理。",
        },
    }


def get_ai_usage_bill(
    database_path: str | Path,
    *,
    project_id: int | None = None,
    days: int = 30,
    operation: str = "",
    cost_status: str = "",
    limit: int = 200,
) -> dict[str, Any]:
    """Return a filterable, cross-project bill made from immutable receipts.

    Totals are calculated across every matching row while ``records`` is a
    bounded newest-first window for the desktop detail table. Unknown usage or
    missing prices stay explicit; they are never folded into a zero amount.
    """

    normalized_project_id = _optional_positive_int(project_id, "project_id")
    normalized_days = _bounded_integer(days, "days", minimum=0, maximum=3650)
    normalized_limit = _bounded_integer(limit, "limit", minimum=1, maximum=500)
    normalized_operation = str(operation or "").strip().lower()
    if normalized_operation and normalized_operation not in AI_USAGE_OPERATIONS:
        raise AIUsageLedgerError("operation 格式无效")
    normalized_cost_status = str(cost_status or "").strip().lower()
    valid_cost_statuses = {
        "calculated",
        "unpriced",
        "unknown_usage",
        "insufficient_usage",
    }
    if normalized_cost_status and normalized_cost_status not in valid_cost_statuses:
        raise AIUsageLedgerError("cost_status 格式无效")

    where = ["1 = 1"]
    params: list[Any] = []
    generated_at = datetime.now(timezone.utc).replace(microsecond=0)
    period_start = None
    if normalized_project_id is not None:
        where.append("ledger.project_id = ?")
        params.append(normalized_project_id)
    if normalized_days:
        period_start = generated_at - timedelta(days=normalized_days)
        where.append("ledger.created_at >= ?")
        params.append(period_start.strftime("%Y-%m-%dT%H:%M:%SZ"))
    if normalized_operation:
        where.append("ledger.operation = ?")
        params.append(normalized_operation)
    if normalized_cost_status:
        where.append("ledger.cost_status = ?")
        params.append(normalized_cost_status)

    with closing(_connect(database_path)) as conn:
        if normalized_project_id is not None and conn.execute(
            "SELECT 1 FROM projects WHERE id = ?", (normalized_project_id,)
        ).fetchone() is None:
            raise AIUsageLedgerError("品牌项目不存在")
        rows = conn.execute(
            f"""
            SELECT
                ledger.*,
                project.name AS project_name,
                article.title AS article_title
            FROM ai_usage_ledger AS ledger
            LEFT JOIN projects AS project ON project.id = ledger.project_id
            LEFT JOIN articles AS article ON article.id = ledger.article_id
            WHERE {' AND '.join(where)}
            ORDER BY ledger.created_at DESC, ledger.id DESC
            """,
            tuple(params),
        ).fetchall()

    records = [_serialize_record(row) for row in rows]
    # Metadata is intentionally excluded from the billing endpoint even though
    # the ledger itself only persists allow-listed values. A bill needs usage,
    # price and identity fields, never prompts or provider payloads.
    for record in records:
        record.pop("metadata", None)

    summary = _summarize_bill_records(records)
    return {
        "ledger_version": AI_COST_LEDGER_VERSION,
        "generated_at": generated_at.isoformat().replace("+00:00", "Z"),
        "period": {
            "days": normalized_days,
            "start": (
                period_start.isoformat().replace("+00:00", "Z")
                if period_start is not None
                else None
            ),
            "end": generated_at.isoformat().replace("+00:00", "Z"),
        },
        "filters": {
            "project_id": normalized_project_id,
            "operation": normalized_operation,
            "cost_status": normalized_cost_status,
        },
        "summary": summary,
        "by_operation": _group_bill_records(records, ("operation",)),
        "by_model": _group_bill_records(
            [
                {**record, "provider": record["canonical_provider"]}
                for record in records
            ],
            ("provider", "model"),
        ),
        "by_project": _group_bill_records(records, ("project_id", "project_name")),
        "by_day": _group_bill_by_day(records),
        "records": records[:normalized_limit],
        "record_window": {
            "matched": len(records),
            "returned": min(len(records), normalized_limit),
            "limit": normalized_limit,
            "has_more": len(records) > normalized_limit,
        },
        "publishing": {
            "token_usage_applicable": False,
            "message": "登录、上传与发布若未调用模型，Token 和模型费用均为不适用，不记作 0。",
        },
        "pricing": {
            "configuration": AI_PRICE_CATALOG_ENV,
            "semantics": "金额是调用时单价快照的估算值；未知用量或未定价记录不计入已知金额，最终以供应商账单为准。",
        },
    }


def get_ai_usage_records_by_references(
    database_path: str | Path,
    *,
    operation: str,
    reference_type: str,
    reference_ids: Sequence[str | int],
) -> dict[str, dict[str, Any]]:
    """Fetch immutable usage receipts for a bounded set of model operations.

    Batch workflows use this lookup after all model calls have completed.  It
    keeps the article response tied to the durable ledger instead of deriving a
    second, potentially different cost from the provider payload.
    """

    normalized_operation = str(operation or "").strip().lower()
    if normalized_operation not in AI_USAGE_OPERATIONS:
        raise AIUsageLedgerError("未知的 AI operation")
    normalized_reference_type = _bounded_optional_text(
        reference_type,
        "reference_type",
        80,
    )
    if not normalized_reference_type:
        raise AIUsageLedgerError("reference_type 不能为空")
    if isinstance(reference_ids, (str, bytes)) or not isinstance(
        reference_ids, Sequence
    ):
        raise AIUsageLedgerError("reference_ids 必须是数组")

    normalized_reference_ids: list[str] = []
    for value in reference_ids:
        normalized = _bounded_optional_text(value, "reference_id", 160)
        if normalized and normalized not in normalized_reference_ids:
            normalized_reference_ids.append(normalized)
    if len(normalized_reference_ids) > 200:
        raise AIUsageLedgerError("一次最多读取 200 条成本回执")
    if not normalized_reference_ids:
        return {}

    placeholders = ",".join("?" for _item in normalized_reference_ids)
    with closing(_connect(database_path)) as conn:
        rows = conn.execute(
            f"""
            SELECT * FROM ai_usage_ledger
            WHERE operation = ?
              AND reference_type = ?
              AND reference_id IN ({placeholders})
            ORDER BY id ASC
            """,
            (
                normalized_operation,
                normalized_reference_type,
                *normalized_reference_ids,
            ),
        ).fetchall()
    records = [_serialize_record(row) for row in rows]
    return {str(record["reference_id"]): record for record in records}


def summarize_ai_usage_records(
    records: Sequence[Mapping[str, Any]],
    *,
    expected_item_count: int | None = None,
) -> dict[str, Any]:
    """Aggregate receipts without turning missing measurements into zero.

    Token totals are nullable when a provider reported no value for that token
    class.  Known monetary amounts are grouped by currency, while every
    unpriced, insufficient, unknown-usage or missing receipt remains visible in
    ``unknown_cost_item_count``.
    """

    if isinstance(records, (str, bytes)) or not isinstance(records, Sequence):
        raise AIUsageLedgerError("records 必须是数组")
    normalized_records = [
        dict(record) for record in records if isinstance(record, Mapping)
    ]
    if len(normalized_records) != len(records):
        raise AIUsageLedgerError("records 包含无效成本回执")

    if expected_item_count is None:
        normalized_expected_count = len(normalized_records)
    else:
        if isinstance(expected_item_count, bool):
            raise AIUsageLedgerError("expected_item_count 必须是非负整数")
        try:
            normalized_expected_count = int(expected_item_count)
        except (TypeError, ValueError) as exc:
            raise AIUsageLedgerError("expected_item_count 必须是非负整数") from exc
        if normalized_expected_count < len(normalized_records):
            raise AIUsageLedgerError("expected_item_count 不能小于成本回执数量")

    token_totals: dict[str, int | None] = {}
    token_known_item_counts: dict[str, int] = {}
    for field in (
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "cached_tokens",
        "reasoning_tokens",
    ):
        known_values: list[int] = []
        for record in normalized_records:
            value = record.get(field)
            if value is None:
                continue
            if isinstance(value, bool):
                raise AIUsageLedgerError(f"成本回执中的 {field} 无效")
            try:
                normalized_value = int(value)
            except (TypeError, ValueError) as exc:
                raise AIUsageLedgerError(f"成本回执中的 {field} 无效") from exc
            if normalized_value < 0:
                raise AIUsageLedgerError(f"成本回执中的 {field} 无效")
            known_values.append(normalized_value)
        token_totals[field] = sum(known_values) if known_values else None
        token_known_item_counts[field] = len(known_values)

    calculated_costs: dict[str, Decimal] = {}
    known_cost_item_count = 0
    for record in normalized_records:
        if record.get("cost_status") != "calculated" or record.get(
            "estimated_cost"
        ) is None:
            continue
        try:
            amount = Decimal(str(record["estimated_cost"]))
        except (InvalidOperation, ValueError) as exc:
            raise AIUsageLedgerError("成本回执中的 estimated_cost 无效") from exc
        if not amount.is_finite() or amount < 0:
            raise AIUsageLedgerError("成本回执中的 estimated_cost 无效")
        currency = str(record.get("currency") or "UNKNOWN").strip() or "UNKNOWN"
        calculated_costs[currency] = (
            calculated_costs.get(currency, Decimal("0")) + amount
        )
        known_cost_item_count += 1

    missing_receipt_item_count = normalized_expected_count - len(normalized_records)
    known_total_token_item_count = token_known_item_counts["total_tokens"]
    unknown_usage_item_count = (
        sum(
            record.get("usage_status") == "unknown"
            for record in normalized_records
        )
        + missing_receipt_item_count
    )
    unpriced_item_count = sum(
        record.get("cost_status") == "unpriced"
        for record in normalized_records
    )
    return {
        "ledger_version": AI_COST_LEDGER_VERSION,
        "batch_item_count": normalized_expected_count,
        "operation_count": len(normalized_records),
        "receipt_count": len(normalized_records),
        "token_totals": token_totals,
        "token_known_item_counts": token_known_item_counts,
        "known_total_tokens": token_totals["total_tokens"],
        "known_token_record_count": known_total_token_item_count,
        "known_total_token_item_count": known_total_token_item_count,
        "unknown_token_item_count": (
            normalized_expected_count - known_total_token_item_count
        ),
        "known_cost_item_count": known_cost_item_count,
        "unknown_cost_item_count": normalized_expected_count - known_cost_item_count,
        "calculated_costs": [
            {"currency": currency, "estimated_cost": _decimal_text(amount)}
            for currency, amount in sorted(calculated_costs.items())
        ],
        "unknown_usage_count": unknown_usage_item_count,
        "unknown_usage_item_count": unknown_usage_item_count,
        "unpriced_count": unpriced_item_count,
        "unpriced_item_count": unpriced_item_count,
        "insufficient_usage_item_count": sum(
            record.get("cost_status") == "insufficient_usage"
            for record in normalized_records
        ),
        "missing_receipt_item_count": missing_receipt_item_count,
        "semantics": "空 Token 或空成本表示平台未返回或未配置价格，不能按 0 成本理解。",
    }


def normalize_usage(value: Mapping[str, Any] | None) -> dict[str, int]:
    if not isinstance(value, Mapping):
        return {}
    aliases = {
        "input_tokens": ("input_tokens", "prompt_tokens", "input"),
        "output_tokens": ("output_tokens", "completion_tokens", "output"),
        "total_tokens": ("total_tokens", "total"),
        "cached_tokens": ("cached_tokens", "cache_tokens"),
        "reasoning_tokens": ("reasoning_tokens",),
    }
    result: dict[str, int] = {}
    for target, names in aliases.items():
        raw = next((value.get(name) for name in names if value.get(name) is not None), None)
        if raw is None or isinstance(raw, bool):
            continue
        try:
            number = int(raw)
        except (TypeError, ValueError):
            continue
        if number >= 0:
            result[target] = number
    if "total_tokens" not in result and (
        "input_tokens" in result or "output_tokens" in result
    ):
        result["total_tokens"] = result.get("input_tokens", 0) + result.get(
            "output_tokens", 0
        )
    return result


def canonicalize_provider(provider: str, model: str) -> str:
    """Return the stable cost-catalog key without collapsing other vendors.

    The content engine historically called Alibaba-hosted Qwen ``qwen`` while
    the direct optimization client called the same provider ``dashscope``.
    Only that known Qwen/DashScope pair is aliased; arbitrary OpenAI-compatible
    hosts and non-Qwen DashScope models retain their original provider key.
    """

    provider_text = str(provider or "unknown").strip() or "unknown"
    provider_key = provider_text.casefold()
    model_key = str(model or "unknown").strip().casefold()
    if provider_key in {"qwen", "dashscope"} and model_key.startswith("qwen"):
        return "qwen"
    return provider_text


def resolve_price_snapshot(
    *,
    provider: str,
    model: str,
    pricing_catalog: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    catalog = pricing_catalog if pricing_catalog is not None else _catalog_from_environment()
    if not isinstance(catalog, Mapping):
        return {"pricing_source": "unconfigured"}
    raw_provider_key = str(provider or "unknown").strip().casefold()
    model_key = str(model or "unknown").strip().casefold()
    provider_key = canonicalize_provider(raw_provider_key, model_key).casefold()
    provider_aliases = [provider_key]
    if provider_key == "qwen" and model_key.startswith("qwen"):
        # Existing deployments may already use the pre-1.0.1 key. Prefer the
        # canonical entry when both exist, but keep the old catalog valid.
        provider_aliases.append("dashscope")
    normalized_catalog = {str(key).strip().casefold(): value for key, value in catalog.items()}
    exact_candidates = [f"{item}/{model_key}" for item in provider_aliases]
    provider_wildcard_candidates = [f"{item}/*" for item in provider_aliases]
    matched_key = next(
        (
            key
            for key in (
                *exact_candidates,
                f"*/{model_key}",
                *provider_wildcard_candidates,
                "*/*",
            )
            if key in normalized_catalog
        ),
        None,
    )
    if matched_key is None or not isinstance(normalized_catalog[matched_key], Mapping):
        return {"pricing_source": "unconfigured"}
    entry = normalized_catalog[matched_key]
    try:
        input_price = _nonnegative_decimal(entry.get("input_per_million"))
        output_price = _nonnegative_decimal(entry.get("output_per_million"))
    except AIUsageLedgerError:
        return {"pricing_source": "invalid_configuration"}
    currency = str(entry.get("currency") or "").strip().upper()
    if not (3 <= len(currency) <= 8 and currency.isascii() and currency.isalnum()):
        return {"pricing_source": "invalid_configuration"}
    return {
        "input_price_per_million": _decimal_text(input_price),
        "output_price_per_million": _decimal_text(output_price),
        "currency": currency,
        "pricing_source": f"environment:{matched_key}",
    }


def _catalog_from_environment() -> Mapping[str, Any] | None:
    raw = str(os.getenv(AI_PRICE_CATALOG_ENV, "") or "").strip()
    if not raw or len(raw) > 65_536:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, Mapping) else None


def _calculate_cost(
    *,
    tokens: Mapping[str, int],
    usage_status: str,
    price: Mapping[str, str],
) -> dict[str, str]:
    if usage_status == "unknown" or not tokens:
        return {"cost_status": "unknown_usage"}
    if not price.get("input_price_per_million") or not price.get(
        "output_price_per_million"
    ):
        return {"cost_status": "unpriced"}
    input_tokens = tokens.get("input_tokens")
    output_tokens = tokens.get("output_tokens")
    input_price = Decimal(price["input_price_per_million"])
    output_price = Decimal(price["output_price_per_million"])
    if input_tokens is None or output_tokens is None:
        total_tokens = tokens.get("total_tokens")
        if total_tokens is None or input_price != output_price:
            return {"cost_status": "insufficient_usage"}
        amount = Decimal(total_tokens) * input_price / Decimal(1_000_000)
    else:
        amount = (
            Decimal(input_tokens) * input_price
            + Decimal(output_tokens) * output_price
        ) / Decimal(1_000_000)
    return {
        "cost_status": "calculated",
        "estimated_cost": _decimal_text(amount.quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP)),
    }


def _summarize_bill_records(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    token_fields = (
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "cached_tokens",
        "reasoning_tokens",
    )
    token_totals: dict[str, int | None] = {}
    for field in token_fields:
        values = [
            int(record[field])
            for record in records
            if record.get(field) is not None
        ]
        token_totals[field] = sum(values) if values else None

    calculated_costs = _cost_totals(records)
    calculated_count = sum(
        record.get("cost_status") == "calculated" for record in records
    )
    record_count = len(records)
    return {
        "operation_count": record_count,
        "known_total_tokens": token_totals["total_tokens"],
        "token_totals": token_totals,
        "known_token_record_count": sum(
            record.get("total_tokens") is not None for record in records
        ),
        "calculated_cost_count": calculated_count,
        "cost_coverage_percent": (
            round(calculated_count * 100 / record_count, 1) if record_count else 0.0
        ),
        "unknown_cost_count": record_count - calculated_count,
        "unknown_usage_count": sum(
            record.get("usage_status") == "unknown" for record in records
        ),
        "unpriced_count": sum(
            record.get("cost_status") == "unpriced" for record in records
        ),
        "insufficient_usage_count": sum(
            record.get("cost_status") == "insufficient_usage" for record in records
        ),
        "calculated_costs": calculated_costs,
    }


def _cost_totals(records: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    totals: dict[str, Decimal] = {}
    for record in records:
        if record.get("cost_status") != "calculated":
            continue
        raw_amount = record.get("estimated_cost")
        if raw_amount is None:
            continue
        try:
            amount = Decimal(str(raw_amount))
        except (InvalidOperation, ValueError):
            continue
        if not amount.is_finite() or amount < 0:
            continue
        currency = str(record.get("currency") or "UNKNOWN").strip() or "UNKNOWN"
        totals[currency] = totals.get(currency, Decimal("0")) + amount
    return [
        {"currency": currency, "estimated_cost": _decimal_text(amount)}
        for currency, amount in sorted(totals.items())
    ]


def _group_bill_records(
    records: Sequence[Mapping[str, Any]],
    fields: tuple[str, ...],
) -> list[dict[str, Any]]:
    groups: dict[tuple[Any, ...], list[Mapping[str, Any]]] = {}
    for record in records:
        key = tuple(record.get(field) for field in fields)
        groups.setdefault(key, []).append(record)
    output: list[dict[str, Any]] = []
    for key, items in groups.items():
        row = {field: value for field, value in zip(fields, key)}
        row.update(_summarize_bill_records(items))
        output.append(row)
    return sorted(
        output,
        key=lambda item: (
            -int(item.get("operation_count") or 0),
            *(str(item.get(field) or "") for field in fields),
        ),
    )


def _group_bill_by_day(records: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[Mapping[str, Any]]] = {}
    for record in records:
        created_at = str(record.get("created_at") or "")
        day = created_at[:10] if len(created_at) >= 10 else "unknown"
        groups.setdefault(day, []).append(record)
    output = []
    for day, items in sorted(groups.items()):
        row = {"date": day}
        row.update(_summarize_bill_records(items))
        output.append(row)
    return output


def _group_records(records: list[dict[str, Any]], fields: tuple[str, ...]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for record in records:
        key = tuple(str(record.get(field) or "unknown") for field in fields)
        groups.setdefault(key, []).append(record)
    output = []
    for key, items in sorted(groups.items()):
        row = {field: value for field, value in zip(fields, key)}
        row.update(
            {
                "operation_count": len(items),
                "known_total_tokens": sum(item["total_tokens"] or 0 for item in items),
                "unknown_usage_count": sum(item["usage_status"] == "unknown" for item in items),
            }
        )
        output.append(row)
    return output


def _fetch_record(conn: sqlite3.Connection, record_id: int):
    return conn.execute("SELECT * FROM ai_usage_ledger WHERE id = ?", (int(record_id),)).fetchone()


def _serialize_record(row: sqlite3.Row) -> dict[str, Any]:
    record = dict(row)
    record["canonical_provider"] = canonicalize_provider(
        record.get("provider", "unknown"),
        record.get("model", "unknown"),
    )
    try:
        record["metadata"] = json.loads(record.pop("metadata_json") or "{}")
    except (TypeError, json.JSONDecodeError):
        record["metadata"] = {}
    return record


def _normalize_metadata(value: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    # This allow-list prevents prompts, article bodies, URLs and credentials
    # from accidentally entering a cost receipt.
    allowed = {"outcome", "fallback_used", "usage_origin", "estimator_version"}
    result: dict[str, Any] = {}
    for key in allowed:
        if key not in value:
            continue
        item = value[key]
        if isinstance(item, bool):
            result[key] = item
        elif isinstance(item, (str, int, float)):
            result[key] = str(item)[:120]
    return result


def _bounded_text(value: Any, field: str, maximum: int, *, fallback: str) -> str:
    text = str(value or "").strip() or fallback
    if "\0" in text or len(text) > maximum:
        raise AIUsageLedgerError(f"{field} 格式无效")
    return text


def _bounded_optional_text(value: Any, field: str, maximum: int) -> str:
    text = str(value or "").strip()
    if "\0" in text or len(text) > maximum:
        raise AIUsageLedgerError(f"{field} 格式无效")
    return text


def _optional_positive_int(value: Any, field: str) -> int | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        raise AIUsageLedgerError(f"{field} 必须是正整数")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise AIUsageLedgerError(f"{field} 必须是正整数") from exc
    if number <= 0:
        raise AIUsageLedgerError(f"{field} 必须是正整数")
    return number


def _bounded_integer(value: Any, field: str, *, minimum: int, maximum: int) -> int:
    if isinstance(value, bool):
        raise AIUsageLedgerError(f"{field} 必须是整数")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise AIUsageLedgerError(f"{field} 必须是整数") from exc
    if not minimum <= number <= maximum:
        raise AIUsageLedgerError(f"{field} 必须在 {minimum} 到 {maximum} 之间")
    return number


def _nonnegative_decimal(value: Any) -> Decimal:
    if isinstance(value, bool):
        raise AIUsageLedgerError("价格必须是非负数字")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise AIUsageLedgerError("价格必须是非负数字") from exc
    if not number.is_finite() or number < 0:
        raise AIUsageLedgerError("价格必须是非负数字")
    return number


def _decimal_text(value: Decimal) -> str:
    normalized = format(value, "f")
    if "." in normalized:
        normalized = normalized.rstrip("0").rstrip(".")
    return normalized or "0"


def _encode_json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _connect(database_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(Path(database_path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


__all__ = [
    "AI_COST_LEDGER_VERSION",
    "AI_PRICE_CATALOG_ENV",
    "AI_USAGE_OPERATIONS",
    "AIUsageLedgerError",
    "attach_article_to_usage",
    "canonicalize_provider",
    "get_ai_usage_bill",
    "get_ai_usage_records_by_references",
    "get_project_ai_cost_summary",
    "normalize_usage",
    "record_ai_usage",
    "resolve_price_snapshot",
    "summarize_ai_usage_records",
]
