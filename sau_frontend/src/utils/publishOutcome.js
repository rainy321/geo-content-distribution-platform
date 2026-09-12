export const PUBLISH_OUTCOME_CATEGORIES = Object.freeze([
  'published',
  'platform_processing',
  'action_required',
  'failed'
])

export const PUBLISH_OUTCOME_LABELS = Object.freeze({
  published: '平台已发布',
  platform_processing: '平台处理中/审核中',
  action_required: '需要人工处理',
  failed: '明确失败'
})

const fallbackPublicationEvidence = source => {
  const url = String(source?.url || source?.result_url || '').trim()
  const demo = source?.demo === true
  const state = demo
    ? 'not_applicable'
    : url
      ? 'anonymous_unverified'
      : 'no_public_url'
  return {
    version: 'publication-evidence-1.0.0',
    platform_state: String(source?.status || 'unknown') === 'success'
      ? 'reported_published'
      : 'pending_or_unknown',
    evidence_scope: demo ? 'demo' : 'local_task',
    result_url_present: Boolean(url),
    public_access_state: state,
    public_access_label: demo
      ? '不适用'
      : url
        ? '匿名访问未核验'
        : '尚无作品链接',
    anonymous_access_verified: false,
    geo_crawlability_state: 'not_verified',
    geo_crawlable_verified: false,
    can_claim_publicly_accessible: false,
    can_claim_geo_crawlable: false
  }
}

const validCategory = value => PUBLISH_OUTCOME_CATEGORIES.includes(String(value || ''))

const confirmedProcessingMarkers = [
  '平台已接受',
  '已接受',
  '已接受提交',
  '提交成功',
  '提交审核',
  '审核中',
  '等待审核',
  '等待平台审核',
  '平台处理中'
]

const ambiguousSubmissionMarkers = [
  '超时',
  '最终状态未知',
  '结果未知',
  '状态未知',
  '无回执',
  '未收到回执',
  '未取得明确发布结果',
  '未返回可核验',
  '无法确认',
  '发布过程异常',
  '请求异常'
]

const creatorSideEvidenceMarkers = [
  '创作中心',
  '创作者后台',
  '内容管理',
  '已发布列表',
  'posted 接口'
]

const hasConfirmedProcessingEvidence = source => {
  const message = String(source?.message || '').trim()
  if (!message || ambiguousSubmissionMarkers.some(marker => message.includes(marker))) return false
  return confirmedProcessingMarkers.some(marker => message.includes(marker))
}

const fallbackCategory = source => {
  const status = String(source?.status || source?.source_status || '').toLowerCase()
  const message = String(source?.message || '')
  const hasCreatorOnlyEvidence = creatorSideEvidenceMarkers.some(marker => message.includes(marker))
  if (status === 'success') {
    return hasCreatorOnlyEvidence ? 'platform_processing' : 'published'
  }
  if (source?.reconciliation_required === true) {
    return hasConfirmedProcessingEvidence(source) ? 'platform_processing' : 'action_required'
  }
  if (status === 'processing') {
    return 'platform_processing'
  }
  if (status === 'failed') return 'failed'
  return 'action_required'
}

const fallbackReason = (category, source) => {
  const message = String(source?.message || '').trim()
  if (message) return message
  return {
    published: '平台已返回可核验的发布成功状态。',
    platform_processing: '平台尚未返回可确认的公开终态。',
    action_required: '任务需要人工检查后才能继续。',
    failed: '任务已明确失败，未记录为发布成功。'
  }[category]
}

const fallbackNextAction = (category, source) => {
  if (category === 'published') return '复核公开链接；不要再次提交同一任务。'
  if (category === 'platform_processing') {
    return source?.reconciliation_required
      ? '先检查平台后台，再用“核验平台结果”收口；核验前禁止重试。'
      : '等待状态刷新；平台未返回终态前禁止重试。'
  }
  if (source?.reconciliation_required) {
    return '先检查平台后台，再用“核验平台结果”收口；核验前禁止重试。'
  }
  if (category === 'failed') return '修复原因并确认平台未产生内容后，再手动重试。'
  return '按原因完成验证或修复，刷新任务状态后再选择下一步。'
}

export const normalizePublishOutcome = (source = {}, overrides = {}) => {
  const backend = source?.outcome && typeof source.outcome === 'object'
    ? source.outcome
    : {}
  const requestedCategory = overrides.category ?? backend.category
  const category = validCategory(requestedCategory)
    ? String(requestedCategory)
    : fallbackCategory(source)
  const reason = String(
    overrides.reason
    ?? backend.reason
    ?? fallbackReason(category, source)
  ).trim()
  const nextAction = String(
    overrides.nextAction
    ?? backend.next_action
    ?? fallbackNextAction(category, source)
  ).trim()
  const reconciliationRequired = Boolean(
    overrides.reconciliationRequired
    ?? backend.reconciliation_required
    ?? source?.reconciliation_required
  )
  const retryAllowed = typeof overrides.retryAllowed === 'boolean'
    ? overrides.retryAllowed
    : typeof backend.retry_allowed === 'boolean'
      ? backend.retry_allowed
      : category === 'failed'
        || (
          String(source?.status) === 'need_action'
          && source?.account_id != null
          && !reconciliationRequired
        )
  const validationRequired = typeof overrides.validationRequired === 'boolean'
    ? overrides.validationRequired
    : typeof backend.validation_required === 'boolean'
      ? backend.validation_required
      : category !== 'published'
  const autoRepairEligible = typeof overrides.autoRepairEligible === 'boolean'
    ? overrides.autoRepairEligible
    : backend.auto_repair_eligible === true
  const automaticRetryAllowed = typeof overrides.automaticRetryAllowed === 'boolean'
    ? overrides.automaticRetryAllowed
    : backend.automatic_retry_allowed === true
  const publicationEvidence = (
    backend.publication_evidence
    && typeof backend.publication_evidence === 'object'
  )
    ? backend.publication_evidence
    : fallbackPublicationEvidence(source)
  return {
    job_id: Number(source?.job_id ?? overrides.jobId ?? 0) || 0,
    platform: String(source?.platform ?? overrides.platform ?? ''),
    source_status: String(source?.status ?? backend.source_status ?? 'unknown'),
    category,
    label: PUBLISH_OUTCOME_LABELS[category],
    reason,
    next_action: nextAction,
    action: String(overrides.action ?? backend.action ?? ''),
    retry_allowed: Boolean(retryAllowed),
    validation_required: Boolean(validationRequired),
    reconciliation_required: reconciliationRequired,
    auto_repair_eligible: Boolean(autoRepairEligible),
    automatic_retry_allowed: Boolean(automaticRetryAllowed),
    auto_repair_strategy: String(
      overrides.autoRepairStrategy ?? backend.auto_repair_strategy ?? 'none'
    ),
    auto_repair_summary: String(
      overrides.autoRepairSummary ?? backend.auto_repair_summary ?? ''
    ).trim(),
    auto_repair_max_attempts: Number(
      overrides.autoRepairMaxAttempts ?? backend.auto_repair_max_attempts ?? 0
    ) || 0,
    auto_repair_attempted: Boolean(overrides.autoRepairAttempted),
    auto_repair_note: String(overrides.autoRepairNote || '').trim(),
    transport_note: String(overrides.transportNote || '').trim(),
    publication_evidence: publicationEvidence
  }
}

export const summarizePublishOutcomes = values => {
  const items = Array.isArray(values) ? values : []
  const counts = Object.fromEntries(PUBLISH_OUTCOME_CATEGORIES.map(category => [category, 0]))
  for (const item of items) {
    const category = validCategory(item?.category)
      ? item.category
      : normalizePublishOutcome(item).category
    counts[category] += 1
  }
  return { total: items.length, counts }
}

export const formatPublishOutcomeSummary = summary => {
  const total = Number(summary?.total || 0)
  const counts = summary?.counts || {}
  const parts = PUBLISH_OUTCOME_CATEGORIES
    .filter(category => Number(counts[category] || 0) > 0)
    .map(category => `${PUBLISH_OUTCOME_LABELS[category]} ${Number(counts[category])}`)
  return `${parts.join('；') || '没有结果'}（合计 ${total}）`
}
