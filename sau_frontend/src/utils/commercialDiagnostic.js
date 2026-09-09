const unwrap = value => {
  const source = value && typeof value === 'object' ? value : {}
  if (source.code != null && source.data && typeof source.data === 'object') return source.data
  if (source.data && typeof source.data === 'object') {
    if (source.data.code != null && source.data.data && typeof source.data.data === 'object') {
      return source.data.data
    }
    return source.data
  }
  return source
}

const numberOrNull = value => {
  if (value == null || value === '') return null
  const number = Number(value)
  return Number.isFinite(number) ? number : null
}

const array = value => (Array.isArray(value) ? value : [])

export const normalizeCommercialDiagnostic = response => {
  const root = unwrap(response)
  const dimensions = array(root.dimensions).map((item, index) => ({
    key: String(item?.key || `dimension-${index + 1}`),
    label: String(item?.label || item?.key || `维度 ${index + 1}`),
    score: Math.max(0, Math.min(100, numberOrNull(item?.score) ?? 0)),
    weight: Math.max(0, numberOrNull(item?.weight) ?? 0),
    status: ['ready', 'attention', 'critical'].includes(item?.status) ? item.status : 'attention',
    evidence: item?.evidence && typeof item.evidence === 'object' ? item.evidence : {}
  }))
  const gaps = array(root.gaps).map((item, index) => ({
    key: String(item?.key || `gap-${index + 1}`),
    priority: ['P0', 'P1', 'P2'].includes(String(item?.priority).toUpperCase())
      ? String(item.priority).toUpperCase()
      : 'P2',
    dimension: String(item?.dimension || ''),
    title: String(item?.title || '待优化项'),
    action: String(item?.action || '按诊断建议完成优化后复测。'),
    metricTarget: String(item?.metric_target || item?.metricTarget || '使用同一口径复测')
  }))
  return {
    runId: root.run_id ?? root.id ?? null,
    createdAt: root.created_at || '',
    projectId: root.project_id ?? null,
    score: Math.max(0, Math.min(100, numberOrNull(root.score) ?? 0)),
    grade: String(root.grade || '—'),
    scope: String(root.scope || 'project'),
    dimensions,
    gaps,
    gapCounts: {
      P0: numberOrNull(root.gap_counts?.P0) ?? gaps.filter(item => item.priority === 'P0').length,
      P1: numberOrNull(root.gap_counts?.P1) ?? gaps.filter(item => item.priority === 'P1').length,
      P2: numberOrNull(root.gap_counts?.P2) ?? gaps.filter(item => item.priority === 'P2').length
    },
    baseline: root.baseline && typeof root.baseline === 'object' ? root.baseline : {},
    externalModelUsed: Boolean(root.methodology?.external_model_used),
    contentSentExternally: Boolean(root.methodology?.customer_content_sent_externally),
    claimBoundary: String(root.methodology?.claim_boundary || ''),
    version: String(root.diagnostic_version || root.algorithm_versions?.diagnostic || '')
  }
}

export const normalizeCommercialDiagnosticHistory = response => {
  const root = unwrap(response)
  return array(root).map(item => ({
    id: item?.id ?? item?.run_id,
    score: numberOrNull(item?.score),
    grade: String(item?.grade || '—'),
    scope: String(item?.scope || 'project'),
    createdAt: item?.created_at || ''
  })).filter(item => item.id != null)
}

export const normalizeProjectCostSummary = response => {
  const root = unwrap(response)
  const summary = root.summary && typeof root.summary === 'object' ? root.summary : {}
  const modelOperations = new Set(['generate', 'optimize', 'platform_adapt', 'visibility_sample'])
  return {
    operationCount: Math.max(0, numberOrNull(summary.operation_count) ?? 0),
    knownTotalTokens: Math.max(0, numberOrNull(summary.known_total_tokens) ?? 0),
    knownTokenRecordCount: Math.max(0, numberOrNull(summary.known_token_record_count) ?? 0),
    unknownUsageCount: Math.max(0, numberOrNull(summary.unknown_usage_count) ?? 0),
    unpricedCount: Math.max(0, numberOrNull(summary.unpriced_count) ?? 0),
    calculatedCosts: array(summary.calculated_costs).map(item => ({
      currency: String(item?.currency || 'UNKNOWN'),
      amount: String(item?.estimated_cost ?? '')
    })).filter(item => item.amount !== ''),
    operations: array(root.by_operation).map(item => ({
      operation: String(item?.operation || 'unknown'),
      operationCount: Math.max(0, numberOrNull(item?.operation_count) ?? 0),
      knownTotalTokens: Math.max(0, numberOrNull(item?.known_total_tokens) ?? 0),
      unknownUsageCount: Math.max(0, numberOrNull(item?.unknown_usage_count) ?? 0)
    })),
    recent: array(root.recent).filter(item => modelOperations.has(String(item?.operation))).map(item => ({
      id: item?.id ?? null,
      operation: String(item.operation),
      provider: String(item?.provider || 'unknown'),
      model: String(item?.model || 'unknown'),
      createdAt: String(item?.created_at || ''),
      usageStatus: ['reported', 'estimated', 'unknown'].includes(String(item?.usage_status))
        ? String(item.usage_status)
        : 'unknown',
      inputTokens: numberOrNull(item?.input_tokens),
      outputTokens: numberOrNull(item?.output_tokens),
      totalTokens: numberOrNull(item?.total_tokens),
      inputPricePerMillion: numberOrNull(item?.input_price_per_million),
      outputPricePerMillion: numberOrNull(item?.output_price_per_million),
      estimatedCost: numberOrNull(item?.estimated_cost),
      currency: String(item?.currency || ''),
      costStatus: ['calculated', 'unpriced', 'unknown_usage', 'insufficient_usage'].includes(String(item?.cost_status))
        ? String(item.cost_status)
        : 'unknown_usage'
    })),
    publishingNonModel: root.publishing?.token_usage_applicable === false,
    publishingMessage: String(root.publishing?.message || ''),
    pricingMessage: String(root.pricing?.semantics || '')
  }
}

export const operationLabel = value => ({
  generate: '文章生成',
  optimize: 'GEO 调优',
  platform_adapt: '平台适配',
  visibility_sample: 'AI 可见度采样'
}[String(value)] || String(value || '未知操作'))
