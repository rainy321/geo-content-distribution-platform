export const GEO_AUTOMATION_ITEM_LIMIT = 20

export const TERMINAL_AUTOMATION_STATUSES = new Set([
  'completed',
  'need_action',
  'failed',
  'skipped'
])

export const AUTOMATION_STAGES = [
  { key: 'generating', label: '写稿' },
  { key: 'optimizing', label: 'GEO 优化' },
  { key: 'preflighting', label: '平台预检' },
  { key: 'publishing', label: '发布' }
]

const STAGE_INDEX = {
  queued: -1,
  generating: 0,
  optimizing: 1,
  preflighting: 2,
  publishing: 3,
  completed: 4,
  need_action: 4,
  failed: 4,
  skipped: 4
}

export const parseAutomationTopics = (value) => {
  const result = []
  String(value || '')
    .split(/\r?\n/)
    .map(item => item.trim())
    .filter(Boolean)
    .forEach((topic) => {
      if (!result.includes(topic)) result.push(topic)
    })
  return result
}

export const makeAutomationIdentity = (prefix = 'geo-auto') => {
  const suffix = globalThis.crypto?.randomUUID?.()
    || `${Date.now()}-${Math.random().toString(16).slice(2)}`
  return `${prefix}-${suffix}`.replace(/[^A-Za-z0-9._:-]/g, '-')
}

export const automationProjectSnapshot = project => Object.fromEntries([
  'id',
  'name',
  'website',
  'product',
  'industry',
  'description',
  'keywords',
  'competitors',
  'updated_at'
].map(key => [key, project?.[key] ?? null]))

export const buildAutomationAuthorization = ({ projectId, project, topics, publishMode, channels }) => ({
  confirmed: true,
  project_id: Number(projectId),
  topic_count: topics.length,
  publish_mode: publishMode,
  channels: channels.map(channel => ({
    platform: channel.platform,
    account_id: channel.account_id == null ? null : Number(channel.account_id),
    ...(channel.credential_epoch == null
      ? {}
      : { credential_epoch: Number(channel.credential_epoch) })
  })),
  project_snapshot: automationProjectSnapshot(project)
})

export const automationRunCounts = (run) => {
  const items = Array.isArray(run?.items) ? run.items : []
  const terminal = items.filter(item => TERMINAL_AUTOMATION_STATUSES.has(item.status))
  const issues = terminal.filter(item => item.status !== 'completed')
  return {
    total: items.length || Number(run?.total_items || 0),
    completed: terminal.filter(item => item.status === 'completed').length,
    issues: issues.length,
    pending: items.filter(item => !TERMINAL_AUTOMATION_STATUSES.has(item.status)).length,
    processed: terminal.length
  }
}

export const automationStageState = (item, stageKey, publishMode = '') => {
  const stageIndex = AUTOMATION_STAGES.findIndex(stage => stage.key === stageKey)
  const currentIndex = STAGE_INDEX[item?.status] ?? STAGE_INDEX[item?.stage] ?? -1
  if (item?.status === 'completed') {
    if (publishMode === 'prepare' && stageKey === 'publishing') return 'not_applicable'
    return 'complete'
  }
  if (TERMINAL_AUTOMATION_STATUSES.has(item?.status)) {
    const failedStageIndex = STAGE_INDEX[item?.stage] ?? Math.max(0, currentIndex)
    if (stageIndex < failedStageIndex) return 'complete'
    if (stageIndex === failedStageIndex) return 'issue'
    return 'pending'
  }
  if (stageIndex < currentIndex) return 'complete'
  if (stageIndex === currentIndex) return 'active'
  return 'pending'
}

export const automationStatusLabel = (status) => ({
  queued: '等待开始',
  running: '运行中',
  paused: '已暂停',
  completed: '全部完成',
  completed_with_issues: '完成，部分需处理',
  cancelled: '已取消',
  generating: '正在写稿',
  optimizing: '正在优化',
  preflighting: '正在预检',
  publishing: '正在发布',
  need_action: '需要处理',
  failed: '失败',
  skipped: '已跳过'
}[status] || status || '未知')

export const automationErrorEvidence = (error, fallback = '步骤未完成') => {
  const response = error?.response?.data
  const data = response?.data
  const message = String(response?.msg || error?.message || fallback).slice(0, 500)
  return {
    code: String(data?.error_code || response?.code || error?.code || 'workflow_step_error'),
    message,
    state_unknown: Boolean(data?.state_unknown),
    request_id: String(data?.request_id || ''),
    trace_id: String(data?.trace_id || '')
  }
}

export const publishJobEvidence = (job) => ({
  platform: String(job?.platform || ''),
  job_id: Number(job?.job_id ?? job?.id),
  status: String(job?.status || ''),
  message: String(job?.message || ''),
  result_url: String(job?.result_url || ''),
  outcome_category: String(job?.outcome?.category || job?.outcome_category || '')
})

export const orderAutomationPublishJobs = (jobs, channels) => {
  const order = new Map(
    (Array.isArray(channels) ? channels : []).map((channel, index) => [
      String(channel?.platform || ''),
      index
    ])
  )
  const seen = new Set()
  const ordered = []
  let valid = order.size > 0
  for (const raw of Array.isArray(jobs) ? jobs : []) {
    const evidence = publishJobEvidence(raw)
    if (!order.has(evidence.platform) || seen.has(evidence.platform)) {
      valid = false
      continue
    }
    seen.add(evidence.platform)
    ordered.push(evidence)
  }
  ordered.sort((left, right) => order.get(left.platform) - order.get(right.platform))
  return { valid, jobs: ordered }
}

export const isAutomationTerminal = status => (
  status === 'completed'
  || status === 'completed_with_issues'
  || status === 'cancelled'
)
