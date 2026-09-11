from __future__ import annotations

from typing import Any


PUBLISH_OUTCOME_VERSION = "publish-outcome-1.0.0"
PUBLISH_OUTCOME_CATEGORIES = (
    "published",
    "platform_processing",
    "action_required",
    "failed",
)

_CATEGORY_LABELS = {
    "published": "已公开发布",
    "platform_processing": "平台处理中/审核中",
    "action_required": "需要人工处理",
    "failed": "明确失败",
}

_CONFIRMED_PLATFORM_PROCESSING_MARKERS = (
    "平台已接受",
    "已接受",
    "已接受提交",
    "提交成功",
    "提交审核",
    "审核中",
    "等待审核",
    "等待平台审核",
    "平台处理中",
)

_AMBIGUOUS_SUBMISSION_MARKERS = (
    "超时",
    "最终状态未知",
    "结果未知",
    "状态未知",
    "无回执",
    "未收到回执",
    "未取得明确发布结果",
    "未返回可核验",
    "无法确认",
    "发布过程异常",
    "请求异常",
)

_PRE_SUBMISSION_MARKERS = (
    "在点击发布前失败，尚未提交",
    "在点击发表前中止，尚未提交",
    "在点击 post 前中止，尚未提交",
    "点击发布前失败，尚未提交",
    "点击发表前中止，尚未提交",
    "点击 post 前中止，尚未提交",
    "尚未提交",
    "未访问平台",
)

_SUBMISSION_MAY_HAVE_HAPPENED_MARKERS = (
    "已点击发布",
    "已点击发表",
    "已触发一次",
    "已接受提交",
    "提交成功",
    "提交审核",
    "审核中",
    "处理中",
    "最终状态未知",
    "结果未知",
    "状态未知",
    "无回执",
    "未收到回执",
    "未取得明确发布结果",
)

_HUMAN_OR_ACCOUNT_MARKERS = (
    "短信",
    "验证码",
    "扫码",
    "滑块",
    "安全验证",
    "本人验证",
    "登录",
    "cookie",
    "账号离线",
    "账号失效",
    "账号异常",
    "封禁",
    "封号",
    "违规",
    "限流",
    "操作频繁",
)

_PUBLISH_ROUTE_MARKERS = (
    "页面结构",
    "未找到图文发布入口",
    "未找到图片上传控件",
    "未找到可用的图文发布按钮",
    "未找到可用发布按钮",
    "浏览器页面",
)

_TRANSIENT_RUNTIME_MARKERS = (
    "网络或浏览器连接异常",
    "连接异常",
    "页面加载失败",
    "浏览器已关闭",
    "target closed",
    "timeout",
    "超时",
)


def detect_safe_publish_repair(
    *,
    status: Any,
    message: Any = "",
    reconciliation_required: Any = False,
) -> dict[str, Any]:
    """Describe the only failures that software may repair automatically.

    The decisive boundary is evidence from the publisher adapter that the final
    submit control was never reached.  A timeout alone is never enough.  Login,
    verification and account-risk gates always remain human actions.
    """

    normalized_status = str(status or "unknown").strip().lower()
    normalized_message = str(message or "").strip()
    lowered = normalized_message.casefold()
    result = {
        "eligible": False,
        "automatic_retry_allowed": False,
        "strategy": "none",
        "summary": "该结果不能安全自动重试。",
        "max_attempts": 0,
    }
    if normalized_status != "failed" or bool(reconciliation_required):
        return result
    if not any(marker in lowered for marker in _PRE_SUBMISSION_MARKERS):
        return result
    if any(marker in lowered for marker in _SUBMISSION_MAY_HAVE_HAPPENED_MARKERS):
        return result
    if any(marker in lowered for marker in _HUMAN_OR_ACCOUNT_MARKERS):
        result["summary"] = "检测到账号或本人验证问题，必须由账号持有人处理。"
        return result

    if any(marker in lowered for marker in _PUBLISH_ROUTE_MARKERS):
        strategy = "refresh_publish_route"
        summary = "已确认尚未提交；软件可刷新发布入口并用同一快照重试一次。"
    elif any(marker in lowered for marker in _TRANSIENT_RUNTIME_MARKERS):
        strategy = "restart_publisher"
        summary = "已确认尚未提交；软件可重启本次浏览器发布链路并重试一次。"
    else:
        strategy = "retry_same_snapshot"
        summary = "已确认尚未提交；软件可保留原任务快照并安全重试一次。"

    return {
        "eligible": True,
        "automatic_retry_allowed": True,
        "strategy": strategy,
        "summary": summary,
        "max_attempts": 1,
    }


def classify_publish_outcome(
    *,
    status: Any,
    message: Any = "",
    reconciliation_required: Any = False,
    result_url: Any = "",
    can_execute_real: Any = False,
    account_id: Any = None,
    demo: Any = False,
) -> dict[str, Any]:
    """Map one persisted task to exactly one user-facing outcome category.

    Transport errors are deliberately absent from the status model: callers
    should re-read the persisted task and classify that snapshot.  This keeps a
    timeout from being counted as a fifth result in addition to the task's real
    platform state.
    """

    normalized_status = str(status or "unknown").strip().lower()
    normalized_message = str(message or "").strip()
    normalized_url = str(result_url or "").strip()
    needs_reconciliation = bool(reconciliation_required)
    is_demo = bool(demo)
    automatic_repair = detect_safe_publish_repair(
        status=normalized_status,
        message=normalized_message,
        reconciliation_required=needs_reconciliation,
    )

    if normalized_status == "success":
        category = "published"
        reason = normalized_message or (
            "演示流程已完成，未访问真实平台。"
            if is_demo
            else "平台已返回可核验的发布成功状态。"
        )
        next_action = (
            "演示结果无需平台核验。"
            if is_demo
            else (
                "可打开公开链接复核内容；不要再次提交同一任务。"
                if normalized_url
                else "无需重试；如需留档，请在平台后台复核公开状态。"
            )
        )
        action = "open_result" if normalized_url and not is_demo else "none"
        retry_allowed = False
        validation_required = False
    elif needs_reconciliation and not _has_confirmed_processing_evidence(
        normalized_message
    ):
        category = "action_required"
        reason = normalized_message or "发布请求已经结束，但没有取得可确认的平台回执。"
        next_action = (
            "先到平台后台检查作品、审核中和草稿箱，再用“核验平台结果”收口；"
            "核验前禁止重试。"
        )
        action = "reconcile"
        retry_allowed = False
        validation_required = True
    elif normalized_status == "processing" or needs_reconciliation:
        category = "platform_processing"
        reason = normalized_message or (
            "平台已接受提交，公开或审核终态尚未确认。"
            if needs_reconciliation
            else "发布器仍在执行，平台尚未返回终态。"
        )
        next_action = (
            "先到平台后台检查作品、审核中和草稿箱，再用“核验平台结果”收口；"
            "核验前禁止重试。"
            if needs_reconciliation
            else "等待任务自动刷新；执行结束前禁止重试或重复提交。"
        )
        action = "reconcile" if needs_reconciliation else "wait"
        retry_allowed = False
        validation_required = needs_reconciliation
    elif normalized_status == "failed":
        category = "failed"
        reason = normalized_message or "发布任务已明确失败，未记录为发布成功。"
        if automatic_repair["eligible"]:
            next_action = automatic_repair["summary"]
            action = "auto_repair"
        else:
            next_action = (
                "根据失败原因修复正文、素材、账号或运行环境；确认平台未产生内容后，"
                "再手动重新排队。"
            )
            action = "retry_after_validation"
        retry_allowed = True
        validation_required = True
    else:
        category = "action_required"
        reason, next_action, action, retry_allowed = _action_required_details(
            status=normalized_status,
            message=normalized_message,
            can_execute_real=bool(can_execute_real),
            account_id=account_id,
        )
        validation_required = normalized_status not in {"scheduled"}

    return {
        "version": PUBLISH_OUTCOME_VERSION,
        "category": category,
        "label": _CATEGORY_LABELS[category],
        "reason": reason,
        "next_action": next_action,
        "action": action,
        "retry_allowed": retry_allowed,
        "validation_required": validation_required,
        "reconciliation_required": needs_reconciliation,
        "source_status": normalized_status,
        "auto_repair_eligible": bool(automatic_repair["eligible"]),
        "automatic_retry_allowed": bool(
            automatic_repair["automatic_retry_allowed"]
        ),
        "auto_repair_strategy": str(automatic_repair["strategy"]),
        "auto_repair_summary": str(automatic_repair["summary"]),
        "auto_repair_max_attempts": int(automatic_repair["max_attempts"]),
    }


def _has_confirmed_processing_evidence(message: str) -> bool:
    if not message:
        return False
    if any(marker in message for marker in _AMBIGUOUS_SUBMISSION_MARKERS):
        return False
    return any(marker in message for marker in _CONFIRMED_PLATFORM_PROCESSING_MARKERS)


def _action_required_details(
    *,
    status: str,
    message: str,
    can_execute_real: bool,
    account_id: Any,
) -> tuple[str, str, str, bool]:
    if status == "queued" and can_execute_real:
        return (
            message or "任务已通过创建校验，正在等待本次真实发布确认。",
            "核对账号、正文和素材快照后，点击“确认真实发布”。",
            "execute",
            False,
        )
    if status == "scheduled":
        return (
            message or "任务正在等待计划时间，尚未访问平台。",
            "等待到期进入队列；到期后重新核对任务，再确认是否执行。",
            "wait_for_schedule",
            False,
        )
    if status == "queued":
        if account_id is None:
            return (
                message or "任务尚未绑定可用发布账号，当前不能执行。",
                "登录并选择正确平台账号，然后重建任务。",
                "login_and_rebuild",
                False,
            )
        return (
            message or "任务仍在队列中，但当前不满足真实执行条件。",
            "刷新并重新校验账号、运行环境和任务快照；不要盲目重复创建。",
            "validate",
            False,
        )

    lowered = message.casefold()
    if any(marker in lowered for marker in ("短信", "验证码", "扫码", "滑块", "安全验证", "本人验证")):
        return (
            message or "平台要求账号本人完成安全验证。",
            "在平台窗口完成验证，并先核对后台是否已产生内容；确认未发布后再重新排队。",
            "verify_then_retry",
            True,
        )
    if (status == "need_action" and account_id is None) or any(
        marker in lowered
        for marker in ("登录", "cookie", "账号离线", "账号失效", "未绑定账号")
    ):
        return (
            message or "发布账号未连接或登录状态已经失效。",
            "重新登录并绑定正确平台账号，然后重建任务。",
            "login_and_rebuild",
            False,
        )
    if any(marker in lowered for marker in ("前置校验", "可引用性", "正文", "素材", "封面", "视频")):
        return (
            message or "正文或素材未通过发布前校验。",
            "按原因修正平台副本或素材并重新创建任务，不要直接复用失效快照。",
            "fix_and_rebuild",
            False,
        )
    return (
        message or "任务需要人工确认后才能继续。",
        "先按原因完成处理并核对平台后台；确认未发布后，才可手动重新排队。",
        "validate_then_retry",
        status == "need_action",
    )


__all__ = [
    "PUBLISH_OUTCOME_CATEGORIES",
    "PUBLISH_OUTCOME_VERSION",
    "classify_publish_outcome",
    "detect_safe_publish_repair",
]
