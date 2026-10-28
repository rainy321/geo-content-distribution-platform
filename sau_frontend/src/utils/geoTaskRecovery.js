const terminal = new Set(['failed', 'need_action', 'needs_review', 'stopped'])
const kinds = new Set(['plan', 'run', 'research', 'analysis', 'draft', 'retest'])
const reasons = {
  session_redirected_to_logout: ['豆包登录已失效', '专用会话跳转到了退出页。这次任务没有完成，请重新登录后再从当前步骤开始。', true],
  login_required: ['需要登录豆包', '请在专用窗口正常登录。登录窗口结束后，回到当前步骤重新开始。', true],
  human_verification_required: ['需要完成豆包安全验证', '这次验证等待已经结束。请重新打开专用窗口，正常完成登录或验证后再开始。', true],
  manual_verification_window_closed: ['豆包验证窗口已关闭', '窗口关闭前未取得完整结果。请先核对豆包里的原会话，再决定是否重新开始。', true],
  client_mode_mismatch: ['豆包实际模式与测试要求不同', '个人账号也可能进入豆包工作；登录成功或换账号不保证切换模式。普通网页暂不可用时，可在“搜索基线”选择“在手机记录首轮回答”；已有内容轮可在“反馈”中追加外部原文。用户提供不算独立核验，工作模式结果单独记录。', true],
  answer_timeout_or_selectors_changed: ['尚未确认豆包回答完成', '原始证据已保留。请先核对豆包里的原会话，再决定是否重新开始。', false],
  submission_receipt_timeout: ['发送结果尚未确认', '已执行发送操作，但没有取得可核对的提交回执。请先查看豆包原会话；这不代表问题未发送，软件不会自动重发。', false],
  background_task_timeout: ['豆包调研尚未返回最终结果', '豆包可能仍在处理原请求。请先查看原会话，避免重复发起同一问题。', false],
  rate_limited: ['豆包暂时限制了请求', '请稍后再试，先保留当前资料与原任务记录。', false],
  service_capacity_unavailable: ['豆包当前服务繁忙', '这次没有取得完整结果。请稍后再试，当前资料仍保留。', false],
  'browser_error:TargetClosedError': ['豆包窗口已结束', '窗口结束前未取得完整结果。请先核对豆包里的原会话；重新登录只处理账号，不会重发问题。', true],
  'browser_error:TimeoutError': ['豆包页面等待超时', '尚未确认本题的完整采集结果，原始记录已保留。请先核对原任务，已发送题不会自动重发。', false],
  user_cancelled_submission_unknown: ['任务已停止，原记录保留', '请先核对豆包里的原会话，再决定是否重新开始。', false],
  application_closed_submission_unknown: ['上次任务被中断', '软件关闭前任务尚未完成。原记录仍保留，请核对原会话后再继续操作。', false],
  interrupted_submission_unknown: ['上次采集被中断', '原报告和已完成题仍保留。新测一轮会再次提问，请先核对原会话。', false],
  task_failed_submission_unknown: ['上次豆包任务未完成', '发送状态未确认，原记录仍保留。请先核对豆包里的原会话，再决定是否重新开始。', false],
  submission_state_unknown: ['豆包请求状态待核对', '发送状态未确认。请先核对豆包里的原会话，软件不会自动重发。', false],
  collector_exception_submission_unknown: ['采集中断，发送状态待核对', '原报告仍保留。请先查看原会话，重新测试会新建一轮请求。', false],
}

const bool = value => typeof value === 'boolean' ? value : null
const mode = value => ['consumer', 'work'].includes(value) ? value : null
const identifier = value => typeof value === 'string' && /^[\w-]{1,100}$/.test(value) ? value : null

// Old task files retain their evidence; only known diagnostic codes are projected.
export function doubaoTaskRecovery(task) {
  const report = task?.result?.report
  if (task?.status === 'completed' && terminal.has(report?.status)) {
    task = {...task, status: report.status, recovery: {...task.recovery,
      failure_reason: task.recovery?.failure_reason || report.stop_reason,
      requested_client_mode: task.recovery?.requested_client_mode || report.execution?.client_mode,
      requested_model_label: task.recovery?.requested_model_label ?? report.execution?.requested_model_label}}
  }
  if (!task || !terminal.has(task.status) || !kinds.has(task.kind) || !identifier(task.id)) return null
  const source = task.recovery && typeof task.recovery === 'object' ? task.recovery : {}
  const reason = [source.failure_reason, task.failure_reason].find(value => Object.hasOwn(reasons, value))
    || Object.keys(reasons).find(value => typeof task.message === 'string' && task.message.includes(`（${value}）`))
  if (!reason) return null
  const flag = name => bool(Object.hasOwn(source, name) ? source[name] : task.progress?.[name])
  const attempted = flag('question_submission_attempted')
  const submitted = flag('question_submitted')
  const submission = submitted === true ? 'submitted' : attempted === false && submitted === false ? 'not_submitted' : 'unknown'
  // Improve old records' presentation without changing their saved reason or
  // inventing a no-send claim from a timeout.
  const receiptUnknown = reason === 'answer_timeout_or_selectors_changed' && attempted === true && submitted !== true
  const [title, description, canLogin] = reasons[receiptUnknown ? 'submission_receipt_timeout' : reason]
  return {
    taskId: task.id, kind: task.kind, cycleId: identifier(task.cycle_id), reason, title, description, canLogin,
    loginLabel: reason === 'client_mode_mismatch' ? '打开豆包检查入口' : '重新登录豆包',
    clientMode: mode(source.requested_client_mode),
    modelLabel: typeof source.requested_model_label === 'string' ? source.requested_model_label.slice(0, 200) : '',
    attempted, submitted, submission, requiresConfirmation: submission !== 'not_submitted',
    submissionText: submission === 'not_submitted' ? '已确认问题尚未发送。重新开始会新建一次请求。'
      : submission === 'submitted' ? '问题已经发送，尚未取得完整结果。请先查看原会话，重新开始会再次提问。'
        : '问题是否已发送尚未确认。请先查看原会话，重新开始可能重复提问。',
  }
}

// Keep only enough local state to retain the warning across a separate login.
export function recoveryCheckpoint(task) {
  const info = doubaoTaskRecovery(task)
  if (!info) return null
  return {id: info.taskId, kind: info.kind, status: 'need_action', ...(info.cycleId ? {cycle_id: info.cycleId} : {}),
    recovery: {failure_reason: info.reason, question_submission_attempted: info.attempted,
      question_submitted: info.submitted, requested_client_mode: info.clientMode, requested_model_label: info.modelLabel}}
}

export function scopedRecoveryCheckpoint(task, cycleId) {
  const value = recoveryCheckpoint(task)
  if (!value || (value.cycle_id || '') !== (cycleId || '')) return null
  return value
}
