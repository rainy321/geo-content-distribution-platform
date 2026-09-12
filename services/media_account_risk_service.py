from __future__ import annotations

import hashlib
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


MEDIA_ACCOUNT_RISK_VERSION = "media-account-risk-1.1.0"


class ConcurrentMediaAccountCredentialUpdateError(RuntimeError):
    """Raised when risk evidence belongs to an obsolete login session."""


class ConcurrentMediaAccountPublishJobUpdateError(RuntimeError):
    """Raised when risk evidence belongs to an obsolete publish-job version."""

RISK_CATEGORIES = (
    "permanent_ban",
    "temporary_restriction",
    "verification_required",
    "session_expired",
    "network_error",
)
RISK_SOURCES = ("login_check", "login", "publish", "manual")
FROZEN_RISK_CATEGORIES = frozenset(
    {
        "permanent_ban",
        "temporary_restriction",
        "verification_required",
        "session_expired",
    }
)

_RISK_DEFINITIONS = {
    "permanent_ban": {
        "label": "账号永久封禁",
        "reason": "平台显示该账号已被永久封禁。",
        "recommended_action": (
            "停止该账号的发布任务，前往平台安全中心查看原因并按官方流程申诉；"
            "申诉确认恢复后，再在软件中重新验证。软件不会自动换号。"
        ),
        "severity": "critical",
        "frozen": True,
    },
    "temporary_restriction": {
        "label": "账号暂时受限",
        "reason": "平台显示该账号当前受到临时限制。",
        "recommended_action": (
            "暂停该账号的发布，查看平台通知中的限制期限或申诉入口；"
            "限制解除后再重新验证。"
        ),
        "severity": "blocked",
        "frozen": True,
    },
    "verification_required": {
        "label": "需要本人验证",
        "reason": "平台要求完成短信、扫码、滑块或其他本人验证。",
        "recommended_action": (
            "在平台窗口中由账号本人完成验证，然后回到软件重新检测登录状态。"
        ),
        "severity": "blocked",
        "frozen": True,
    },
    "session_expired": {
        "label": "登录会话已失效",
        "reason": "本机保存的登录会话已经失效或缺失。",
        "recommended_action": "重新登录该账号并完成平台要求的验证。",
        "severity": "warning",
        "frozen": True,
    },
    "network_error": {
        "label": "网络检测失败",
        "reason": "本次检测因网络或浏览器连接异常未能得出账号结论。",
        "recommended_action": (
            "检查网络和平台页面是否可访问后再检测；本次错误不会把账号判定为封禁。"
        ),
        "severity": "warning",
        "frozen": False,
    },
}

# Order is deliberate: a permanent ban must win over a generic restriction,
# while an explicit verification prompt must win over a generic login prompt.
_RISK_PATTERNS = (
    (
        "permanent_ban",
        re.compile(
            r"永久(?:封禁|封号|冻结|停用)|"
            r"(?:账号|账户)(?:已|被)?.{0,6}永久(?:封禁|封号|冻结|停用)|"
            r"permanent(?:ly)?\s+(?:banned|suspended)",
            re.IGNORECASE,
        ),
    ),
    (
        "temporary_restriction",
        re.compile(
            r"临时(?:封禁|冻结|限制)|暂时(?:封禁|冻结|限制|无法使用)|"
            r"操作(?:过于)?频繁|账号(?:当前)?受限|违规操作.*限制|"
            r"(?:账号|账户)(?:已|被)?.{0,4}(?:封禁|封号)|已被封号|"
            r"temporar(?:y|ily)\s+(?:restricted|suspended)|"
            r"account\s+(?:is\s+)?banned|too\s+many\s+(?:requests|operations)",
            re.IGNORECASE,
        ),
    ),
    (
        "verification_required",
        re.compile(
            r"短信(?:验证|验证码)|扫码验证|滑块(?:验证)?|人机验证|安全验证|"
            r"身份验证|本人验证|请完成验证|(?:需要|需)(?:完成)?验证|接收短信验证码|captcha|"
            r"verify\s+(?:your\s+)?(?:identity|account)|verification\s+required",
            re.IGNORECASE,
        ),
    ),
    (
        "session_expired",
        re.compile(
            r"登录(?:状态|会话)?(?:已)?(?:失效|过期|无效)|会话(?:已)?失效|(?:请|需要)重新登录|"
            r"尚未登录|未登录|cookie.*?(?:不存在|缺失|无效|失效|过期)|"
            r"session\s*(?:has\s*)?expired|not\s+logged\s+in|unauthori[sz]ed",
            re.IGNORECASE,
        ),
    ),
    (
        "network_error",
        re.compile(
            r"网络(?:异常|错误|不可用|中断)|连接(?:超时|失败|中断)|请求超时|"
            r"无法访问|dns|network(?:\s+(?:down|error|unavailable))?|"
            r"timed?\s*out|connection\s+(?:failed|closed|reset)|targetclosed|"
            r"err_(?:network|connection)",
            re.IGNORECASE,
        ),
    ),
)

# Some creator consoles omit the word "permanent" from their terminal lockout
# page.  A bare "account banned" remains conservatively temporary, but the
# combination of a ban plus loss of access to the creator console is strong
# enough to stop the account as a permanent ban.  Keep this separate from the
# broad patterns so ordinary article copy and generic temporary notices do not
# receive the stronger classification.
_TERMINAL_CREATOR_CONSOLE_BAN_PATTERN = re.compile(
    r"(?=.*(?:账号|账户)(?:已|被)?.{0,6}(?:封禁|封号))"
    r"(?:(?=.*无法访问.{0,12}(?:创作者|作者)中心)|"
    r"(?=.*违规操作.{0,20}(?:受到)?限制))",
    re.IGNORECASE | re.DOTALL,
)

# Risk messages are copied from creator consoles, where a positive keyword may
# occur inside an explicitly safe statement (for example, "账号未被封禁").
# Mask only tightly-bound negated phrases before classification.  Keeping the
# match local is important: a negation in an earlier clause such as
# "申诉未通过，账号已被封禁" must not cancel the later, real risk.
#
# Double negatives are first rewritten to their positive meaning so the mask
# below cannot turn "并非无需验证" into a safe result.
_RISK_DOUBLE_NEGATION_REWRITES = (
    (
        re.compile(
            r"(?:并非|不是|并不是|不能|不可)(?:完全)?"
            r"(?:不需要|不需|无需|无须|不用|不必|不)"
            r"(?:再|完成|进行|接受|接收|做)?"
            r"(?:本人|身份|短信|扫码|滑块|安全|人机|账号|账户)?"
            r"(?:验证|验证码)",
            re.IGNORECASE,
        ),
        "需要验证",
    ),
    (
        re.compile(
            r"(?:并非|不是|并不是)(?:完全)?"
            r"(?:没有|没|未|不会)(?:曾经|已经|再|可能)?"
            r"(?:被|受到|遭到|处于)?永久"
            r"(?:封禁|封号|冻结|停用|限制|受限)",
            re.IGNORECASE,
        ),
        "账号已被永久封禁",
    ),
    (
        re.compile(
            r"(?:并非|不是|并不是)(?:完全)?"
            r"(?:没有|没|未|不会)(?:曾经|已经|再|可能)?"
            r"(?:被|受到|遭到|处于)?"
            r"(?:封禁|封号|冻结|停用|限制|受限)",
            re.IGNORECASE,
        ),
        "账号已被封禁",
    ),
)

_NEGATED_RISK_PHRASE_PATTERNS = (
    # Ban/restriction negation: 未被封禁、没有受到限制、并非受限。
    re.compile(
        r"(?:(?:账号|账户)(?:当前|现在)?(?:也)?)?"
        r"(?:并未|未曾|未|并没有|没有|没|并不会|不会|不曾)"
        r"(?:已经|曾经|再|可能)?(?:被|受到|遭到|处于)?"
        r"(?:永久)?(?:封禁|封号|冻结|停用|限制|受限)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:(?:账号|账户)(?:当前|现在)?(?:也)?)?"
        r"(?:并非|不是|并不是)(?:被|受到|遭到|处于)?"
        r"(?:永久)?(?:封禁|封号|冻结|停用|限制|受限)",
        re.IGNORECASE,
    ),
    # Verification negation: 不需要验证、无需本人验证、未要求扫码验证。
    re.compile(
        r"(?:(?:账号|账户)(?:当前|现在)?(?:也)?)?"
        r"(?:(?:并不|不)(?:再)?(?:需要|需|用|必)|"
        r"(?:无需|无须|不用|不必|不需)|"
        r"(?:并未|未|并没有|没有|没)(?:被)?(?:要求|提示))"
        r"(?:再|完成|进行|接受|接收|做)?"
        r"(?:本人|身份|短信|扫码|滑块|安全|人机|账号|账户)?"
        r"(?:验证|验证码)",
        re.IGNORECASE,
    ),
    # A successful session statement must not be confused with an expiry.
    re.compile(
        r"(?:登录(?:状态|会话)?|会话|cookie)(?:当前|现在)?"
        r"(?:并未|未曾|未|并没有|没有|没|并不会|不会|不曾)"
        r"(?:已经|再)?(?:失效|过期|无效|缺失|不存在)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:并不|不)(?:再)?(?:需要|需)重新登录|"
        r"(?:无需|无须|不用|不必|不需)(?:再)?重新登录",
        re.IGNORECASE,
    ),
    # Network health statements occasionally appear next to account status.
    re.compile(
        r"(?:网络|连接|请求|dns)(?:当前|现在)?"
        r"(?:并未|未|并没有|没有|没|并无|无)"
        r"(?:出现|发生)?(?:异常|错误|不可用|中断|超时|失败)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:并非|不是|并不是)(?:无法访问|网络异常|连接失败|请求超时)",
        re.IGNORECASE,
    ),
    # Equivalent English console wording supported by the classifier.
    re.compile(
        r"account\s+(?:is\s+)?not\s+(?:permanent(?:ly)?\s+)?"
        r"(?:banned|suspended|restricted)|"
        r"not\s+permanent(?:ly)?\s+(?:banned|suspended)",
        re.IGNORECASE,
    ),
    re.compile(
        r"verification\s+(?:is\s+)?not\s+required|"
        r"no\s+verification\s+required|"
        r"do(?:es)?\s+not\s+require\s+verification",
        re.IGNORECASE,
    ),
    re.compile(
        r"session\s+(?:has\s+)?not\s+expired|"
        r"network\s+(?:is\s+)?not\s+(?:down|unavailable)|"
        r"connection\s+(?:has\s+)?not\s+(?:failed|closed|reset)",
        re.IGNORECASE,
    ),
)

_SECRET_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)\b(cookie|set-cookie|authorization|access[_-]?token|refresh[_-]?token|"
    r"api[_-]?key|secret|session[_-]?id)\b\s*[:=]\s*"
    r"(\"[^\"]*\"|'[^']*'|[^\s,;]+)"
)
_JSON_SECRET_PATTERN = re.compile(
    r"(?i)([\"'](?:cookie|set-cookie|authorization|access[_-]?token|"
    r"refresh[_-]?token|api[_-]?key|secret|session[_-]?id)[\"']\s*:\s*)"
    r"(?:\"[^\"]*\"|'[^']*'|[^,}\s]+)"
)
_COOKIE_HEADER_PATTERN = re.compile(
    r"(?i)\b(set-cookie|cookie)\b\s*:\s*[^\r\n]+"
)
_BEARER_PATTERN = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
_API_KEY_PATTERN = re.compile(r"\bsk-[A-Za-z0-9._-]{8,}\b", re.IGNORECASE)
_SPACE_PATTERN = re.compile(r"\s+")


def classify_media_account_risk(error_text: Any) -> dict[str, Any] | None:
    """Classify an operator-visible platform error without retaining secrets."""

    raw_text = str(error_text or "").strip()
    if not raw_text:
        return None
    detection_text = _prepare_risk_detection_text(raw_text)
    if _TERMINAL_CREATOR_CONSOLE_BAN_PATTERN.search(detection_text):
        return _build_risk_payload(
            "permanent_ban",
            evidence=sanitize_risk_evidence(raw_text),
        )
    for category, pattern in _RISK_PATTERNS:
        if pattern.search(detection_text):
            return _build_risk_payload(
                category,
                evidence=sanitize_risk_evidence(raw_text),
            )
    return None


def _prepare_risk_detection_text(raw_text: str) -> str:
    """Preserve positive risks while masking only nearby explicit negations."""

    detection_text = raw_text
    for pattern, replacement in _RISK_DOUBLE_NEGATION_REWRITES:
        detection_text = pattern.sub(replacement, detection_text)
    for pattern in _NEGATED_RISK_PHRASE_PATTERNS:
        # Spaces preserve clause boundaries and prevent two surrounding tokens
        # from being accidentally joined into a new risk phrase.
        detection_text = pattern.sub(
            lambda match: " " * len(match.group(0)),
            detection_text,
        )
    return detection_text


def sanitize_risk_evidence(value: Any, *, limit: int = 500) -> str:
    """Return bounded diagnostic text with common credential forms redacted."""

    text = str(value or "")
    text = _JSON_SECRET_PATTERN.sub(lambda match: f'{match.group(1)}"[已隐藏]"', text)
    text = _COOKIE_HEADER_PATTERN.sub(lambda match: f"{match.group(1)}: [已隐藏]", text)
    text = _BEARER_PATTERN.sub("Bearer [已隐藏]", text)
    text = _SECRET_ASSIGNMENT_PATTERN.sub(lambda match: f"{match.group(1)}=[已隐藏]", text)
    text = _API_KEY_PATTERN.sub("[API_KEY_已隐藏]", text)
    text = _SPACE_PATTERN.sub(" ", text).strip()
    return text[: max(0, int(limit))]


def record_media_account_risk(
    database_path: str | Path,
    account_id: int,
    *,
    source: str,
    error_text: Any,
    occurred_at: datetime | str | None = None,
    expected_credential_epoch: int | None = None,
    expected_publish_job_id: int | None = None,
    expected_publish_job_state_version: int | None = None,
) -> dict[str, Any] | None:
    """Persist one classified risk and fail closed for account-level blockers.

    Pending jobs are stopped before they reach a publisher.  Processing jobs are
    deliberately not rewritten because their remote outcome may be unknown.
    Repeated identical observations update the same active event instead of
    producing an unbounded retry journal.
    """

    normalized_account_id = _normalize_account_id(account_id)
    normalized_expected_epoch = _normalize_credential_epoch(
        expected_credential_epoch
    )
    normalized_expected_job_id = (
        _normalize_positive_id(
            expected_publish_job_id,
            field="expected_publish_job_id",
        )
        if expected_publish_job_id is not None
        else None
    )
    normalized_expected_job_version = _normalize_job_state_version(
        expected_publish_job_state_version
    )
    if (normalized_expected_job_id is None) != (
        normalized_expected_job_version is None
    ):
        raise ValueError(
            "expected_publish_job_id 与 expected_publish_job_state_version 必须同时提供"
        )
    normalized_source = str(source or "").strip().lower()
    if normalized_source not in RISK_SOURCES:
        raise ValueError("不支持的账号风险来源")
    risk = classify_media_account_risk(error_text)
    if risk is None:
        return None
    timestamp = _normalize_timestamp(occurred_at)
    with closing(_connect(database_path)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            _ensure_schema(conn)
            account = conn.execute(
                """
                SELECT id, status, credential_epoch
                FROM user_info WHERE id = ?
                """,
                (normalized_account_id,),
            ).fetchone()
            if account is None or int(account["status"]) == -2:
                raise LookupError("媒体账号不存在或已解绑")
            current_credential_epoch = int(account["credential_epoch"])
            if (
                normalized_expected_epoch is not None
                and normalized_expected_epoch != current_credential_epoch
            ):
                # This check and every write below share one IMMEDIATE
                # transaction.  A successful re-login therefore either wins
                # before this check (old evidence is discarded) or after this
                # transaction; an obsolete browser can never freeze the new
                # credential generation.
                raise ConcurrentMediaAccountCredentialUpdateError(
                    "账号登录会话已更新，旧会话风险结果已丢弃"
                )
            if normalized_expected_job_id is not None:
                if not _table_exists(conn, "publish_jobs"):
                    raise ConcurrentMediaAccountPublishJobUpdateError(
                        "发布任务已变化，旧任务风险结果已丢弃"
                    )
                publish_job = conn.execute(
                    """
                    SELECT account_id, state_version
                    FROM publish_jobs
                    WHERE id = ?
                    """,
                    (normalized_expected_job_id,),
                ).fetchone()
                if (
                    publish_job is None
                    or publish_job["account_id"] is None
                    or int(publish_job["account_id"]) != normalized_account_id
                    or int(publish_job["state_version"])
                    != normalized_expected_job_version
                ):
                    # The job-version and credential-generation comparisons
                    # run under the same IMMEDIATE transaction as every risk,
                    # account and queued-job write below.  A verifier result
                    # can therefore never freeze the current account after
                    # the claimed publish attempt has been replaced.
                    raise ConcurrentMediaAccountPublishJobUpdateError(
                        "发布任务已变化，旧任务风险结果已丢弃"
                    )
            fingerprint = hashlib.sha256(
                (
                    f"{normalized_account_id}\0{current_credential_epoch}\0"
                    f"{normalized_source}\0{risk['category']}\0{risk['evidence']}"
                ).encode("utf-8")
            ).hexdigest()
            active_event = conn.execute(
                """
                SELECT * FROM media_account_risk_events
                WHERE account_id = ? AND credential_epoch = ?
                  AND source = ? AND category = ? AND active = 1
                ORDER BY occurred_at DESC, id DESC
                LIMIT 1
                """,
                (
                    normalized_account_id,
                    current_credential_epoch,
                    normalized_source,
                    risk["category"],
                ),
            ).fetchone()
            if active_event is not None:
                # Repeated monitors often render the same warning with a live
                # timestamp or slightly different surrounding copy.  Keep one
                # active incident per account/source/category and refresh its
                # evidence instead of flooding the UI with near-duplicates.
                conn.execute(
                    """
                    UPDATE media_account_risk_events
                    SET reason = ?, evidence = ?, occurred_at = ?
                    WHERE id = ?
                    """,
                    (
                        risk["reason"],
                        risk["evidence"],
                        timestamp,
                        int(active_event["id"]),
                    ),
                )
                event_row = conn.execute(
                    "SELECT * FROM media_account_risk_events WHERE id = ?",
                    (int(active_event["id"]),),
                ).fetchone()
            else:
                conn.execute(
                    """
                    INSERT INTO media_account_risk_events (
                        account_id, credential_epoch, category, source, reason, evidence,
                        occurred_at, active, event_fingerprint
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)
                    ON CONFLICT(event_fingerprint) DO UPDATE SET
                        reason = excluded.reason,
                        evidence = excluded.evidence,
                        occurred_at = excluded.occurred_at,
                        active = 1,
                        resolved_at = NULL
                    """,
                    (
                        normalized_account_id,
                        current_credential_epoch,
                        risk["category"],
                        normalized_source,
                        risk["reason"],
                        risk["evidence"],
                        timestamp,
                        fingerprint,
                    ),
                )
                event_row = conn.execute(
                    """
                    SELECT * FROM media_account_risk_events
                    WHERE event_fingerprint = ?
                    """,
                    (fingerprint,),
                ).fetchone()
            stopped_jobs = 0
            if risk["frozen"]:
                conn.execute(
                    """
                    UPDATE user_info
                    SET status = 0, last_checked_at = ?
                    WHERE id = ? AND status != -2
                    """,
                    (timestamp.replace("T", " ").removesuffix("Z"), normalized_account_id),
                )
                if _table_exists(conn, "publish_jobs"):
                    job_message = f"账号风险拦截：{risk['label']}。{risk['recommended_action']}"
                    updated = conn.execute(
                        """
                        UPDATE publish_jobs
                        SET status = 'need_action',
                            auto_execute = 0,
                            authorization_fingerprint = '',
                            message = ?,
                            state_version = state_version + 1
                        WHERE account_id = ?
                          AND status IN ('queued', 'scheduled')
                        """,
                        (job_message, normalized_account_id),
                    )
                    stopped_jobs = int(updated.rowcount)
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    result = _serialize_event(event_row)
    result["stopped_jobs"] = stopped_jobs
    return result


def record_publish_job_account_risk(
    database_path: str | Path,
    job_id: int,
    error_text: Any,
    *,
    occurred_at: datetime | str | None = None,
    expected_credential_epoch: int | None = None,
    expected_state_version: int | None = None,
) -> dict[str, Any] | None:
    """Classify one publisher failure against its explicitly bound account.

    This is the narrow integration seam for the publish executor.  It never
    guesses or rotates an account: an unbound/demo job simply yields no account
    risk, while a missing job is reported to the caller.
    """

    normalized_job_id = _normalize_positive_id(job_id, field="job_id")
    with closing(_connect(database_path)) as conn:
        if not _table_exists(conn, "publish_jobs"):
            raise LookupError("发布任务不存在")
        row = conn.execute(
            "SELECT account_id FROM publish_jobs WHERE id = ?",
            (normalized_job_id,),
        ).fetchone()
    if row is None:
        raise LookupError("发布任务不存在")
    if row["account_id"] is None:
        return None
    return record_media_account_risk(
        database_path,
        int(row["account_id"]),
        source="publish",
        error_text=error_text,
        occurred_at=occurred_at,
        expected_credential_epoch=expected_credential_epoch,
        expected_publish_job_id=(
            normalized_job_id if expected_state_version is not None else None
        ),
        expected_publish_job_state_version=expected_state_version,
    )


def get_active_media_account_risks(
    database_path: str | Path,
    account_ids: Iterable[int] | None = None,
) -> dict[int, dict[str, Any]]:
    """Return the highest-priority active risk for each requested account."""

    normalized_ids = None
    if account_ids is not None:
        normalized_ids = sorted({_normalize_account_id(value) for value in account_ids})
        if not normalized_ids:
            return {}
    with closing(_connect(database_path)) as conn:
        with conn:
            _ensure_schema(conn)
        parameters: list[Any] = []
        where = (
            "WHERE r.active = 1 "
            "AND r.credential_epoch = u.credential_epoch"
        )
        if normalized_ids is not None:
            placeholders = ", ".join("?" for _ in normalized_ids)
            where += f" AND r.account_id IN ({placeholders})"
            parameters.extend(normalized_ids)
        rows = conn.execute(
            f"""
            SELECT r.*
            FROM media_account_risk_events AS r
            JOIN user_info AS u ON u.id = r.account_id
            {where}
            ORDER BY r.account_id,
                CASE r.category
                    WHEN 'permanent_ban' THEN 1
                    WHEN 'temporary_restriction' THEN 2
                    WHEN 'verification_required' THEN 3
                    WHEN 'session_expired' THEN 4
                    ELSE 5
                END,
                r.occurred_at DESC,
                r.id DESC
            """,
            parameters,
        ).fetchall()
    result: dict[int, dict[str, Any]] = {}
    for row in rows:
        account_id = int(row["account_id"])
        result.setdefault(account_id, _serialize_event(row))
    return result


def get_active_media_account_risk(
    database_path: str | Path,
    account_id: int,
) -> dict[str, Any] | None:
    return get_active_media_account_risks(database_path, [account_id]).get(
        _normalize_account_id(account_id)
    )


def resolve_media_account_risks(
    database_path: str | Path,
    account_id: int,
    *,
    category: str | None = None,
    resolved_at: datetime | str | None = None,
) -> dict[str, Any]:
    """Acknowledge platform-side recovery; login remains required afterwards."""

    normalized_account_id = _normalize_account_id(account_id)
    normalized_category = str(category or "").strip().lower() or None
    if normalized_category is not None and normalized_category not in RISK_CATEGORIES:
        raise ValueError("不支持的账号风险类型")
    timestamp = _normalize_timestamp(resolved_at)
    with closing(_connect(database_path)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            _ensure_schema(conn)
            account = conn.execute(
                "SELECT status FROM user_info WHERE id = ?",
                (normalized_account_id,),
            ).fetchone()
            if account is None or int(account["status"]) == -2:
                raise LookupError("媒体账号不存在或已解绑")
            where = "account_id = ? AND active = 1"
            parameters: list[Any] = [timestamp, normalized_account_id]
            if normalized_category is not None:
                where += " AND category = ?"
                parameters.append(normalized_category)
            updated = conn.execute(
                f"""
                UPDATE media_account_risk_events
                SET active = 0, resolved_at = ?
                WHERE {where}
                """,
                parameters,
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return {
        "account_id": normalized_account_id,
        "resolved_count": int(updated.rowcount),
        "requires_login": True,
        "account_enabled": False,
    }


def resolve_recoverable_media_account_risks(
    database_path: str | Path,
    account_id: int,
    *,
    resolved_at: datetime | str | None = None,
    expected_credential_epoch: int | None = None,
) -> int:
    """Resolve risks that a successful explicit login check can disprove."""

    normalized_account_id = _normalize_account_id(account_id)
    normalized_expected_epoch = _normalize_credential_epoch(
        expected_credential_epoch
    )
    timestamp = _normalize_timestamp(resolved_at)
    with closing(_connect(database_path)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            _ensure_schema(conn)
            account = conn.execute(
                """
                SELECT credential_epoch FROM user_info
                WHERE id = ? AND status != -2
                """,
                (normalized_account_id,),
            ).fetchone()
            if account is None:
                raise LookupError("媒体账号不存在或已解绑")
            current_epoch = int(account["credential_epoch"])
            if (
                normalized_expected_epoch is not None
                and normalized_expected_epoch != current_epoch
            ):
                raise ConcurrentMediaAccountCredentialUpdateError(
                    "账号登录会话已更新，旧会话检测结果已丢弃"
                )
            result = conn.execute(
                """
                UPDATE media_account_risk_events
                SET active = 0, resolved_at = ?
                WHERE account_id = ? AND active = 1
                  AND credential_epoch = ?
                  AND category != 'permanent_ban'
                """,
                (timestamp, normalized_account_id, current_epoch),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return int(result.rowcount)


def _build_risk_payload(category: str, *, evidence: str) -> dict[str, Any]:
    definition = _RISK_DEFINITIONS[category]
    return {
        "category": category,
        "label": definition["label"],
        "reason": definition["reason"],
        "evidence": evidence,
        "recommended_action": definition["recommended_action"],
        "severity": definition["severity"],
        "frozen": bool(definition["frozen"]),
    }


def _serialize_event(row: sqlite3.Row) -> dict[str, Any]:
    payload = _build_risk_payload(row["category"], evidence=row["evidence"])
    payload.update(
        {
            "id": int(row["id"]),
            "account_id": int(row["account_id"]),
            "source": row["source"],
            "reason": row["reason"],
            "occurred_at": row["occurred_at"],
            "active": bool(row["active"]),
            "resolved_at": row["resolved_at"],
        }
    )
    return payload


def _ensure_schema(conn: sqlite3.Connection) -> None:
    account_columns = {
        str(row[1]) for row in conn.execute("PRAGMA table_info(user_info)")
    }
    if account_columns and "credential_epoch" not in account_columns:
        conn.execute(
            "ALTER TABLE user_info ADD COLUMN credential_epoch "
            "INTEGER NOT NULL DEFAULT 1 CHECK (credential_epoch >= 1)"
        )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS media_account_risk_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER NOT NULL,
            credential_epoch INTEGER NOT NULL DEFAULT 1
                CHECK (credential_epoch >= 1),
            category TEXT NOT NULL CHECK (category IN (
                'permanent_ban', 'temporary_restriction',
                'verification_required', 'session_expired', 'network_error'
            )),
            source TEXT NOT NULL CHECK (source IN (
                'login_check', 'login', 'publish', 'manual'
            )),
            reason TEXT NOT NULL,
            evidence TEXT NOT NULL DEFAULT '',
            occurred_at TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
            resolved_at TEXT,
            event_fingerprint TEXT NOT NULL UNIQUE,
            FOREIGN KEY (account_id) REFERENCES user_info(id) ON DELETE CASCADE
        )
        """
    )
    risk_columns = {
        str(row[1])
        for row in conn.execute("PRAGMA table_info(media_account_risk_events)")
    }
    if "credential_epoch" not in risk_columns:
        conn.execute(
            "ALTER TABLE media_account_risk_events ADD COLUMN credential_epoch "
            "INTEGER NOT NULL DEFAULT 1 CHECK (credential_epoch >= 1)"
        )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_media_account_risk_active
        ON media_account_risk_events(account_id, active, occurred_at DESC)
        """
    )


def ensure_media_account_risk_schema(conn: sqlite3.Connection) -> None:
    """Ensure the risk ledger on an existing caller-owned transaction."""

    _ensure_schema(conn)


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (name,),
    ).fetchone() is not None


def _normalize_account_id(value: Any) -> int:
    return _normalize_positive_id(value, field="account_id")


def _normalize_credential_epoch(value: Any) -> int | None:
    if value is None:
        return None
    return _normalize_positive_id(value, field="expected_credential_epoch")


def _normalize_job_state_version(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("expected_publish_job_state_version 必须是非负整数")
    try:
        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "expected_publish_job_state_version 必须是非负整数"
        ) from exc
    if normalized < 0:
        raise ValueError("expected_publish_job_state_version 必须是非负整数")
    return normalized


def _normalize_positive_id(value: Any, *, field: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field} 必须是正整数")
    try:
        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} 必须是正整数") from exc
    if normalized <= 0:
        raise ValueError(f"{field} 必须是正整数")
    return normalized


def _normalize_timestamp(value: datetime | str | None) -> str:
    if value is None:
        parsed = datetime.now(timezone.utc)
    elif isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        raw = value.strip()
        if not raw:
            raise ValueError("occurred_at 不能为空")
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("occurred_at 必须是 ISO 8601 时间") from exc
    else:
        raise ValueError("occurred_at 必须是 datetime 或 ISO 8601 时间")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _connect(database_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(Path(database_path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn
