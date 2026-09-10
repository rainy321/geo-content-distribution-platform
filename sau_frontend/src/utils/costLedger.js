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

const array = value => (Array.isArray(value) ? value : [])

const numberOrNull = value => {
  if (value == null || value === '') return null
  const number = Number(value)
  return Number.isFinite(number) ? number : null
}

const nonnegative = value => {
  const number = numberOrNull(value)
  return number == null ? 0 : Math.max(0, number)
}

const decimalOrNull = value => {
  if (value == null || value === '') return null
  const text = String(value).trim()
  return /^(?:0|[1-9]\d*)(?:\.\d+)?$/.test(text) ? text : null
}

const normalizeCosts = value => array(value).map(item => ({
  currency: String(item?.currency || 'UNKNOWN').toUpperCase(),
  amount: decimalOrNull(item?.estimated_cost)
})).filter(item => item.amount != null)

const normalizeAggregate = item => ({
  operationCount: nonnegative(item?.operation_count),
  knownTotalTokens: numberOrNull(item?.known_total_tokens),
  knownTokenRecordCount: nonnegative(item?.known_token_record_count),
  calculatedCostCount: nonnegative(item?.calculated_cost_count),
  unknownCostCount: nonnegative(item?.unknown_cost_count),
  unknownUsageCount: nonnegative(item?.unknown_usage_count),
  unpricedCount: nonnegative(item?.unpriced_count),
  insufficientUsageCount: nonnegative(item?.insufficient_usage_count),
  costCoveragePercent: Math.max(0, Math.min(100, numberOrNull(item?.cost_coverage_percent) ?? 0)),
  calculatedCosts: normalizeCosts(item?.calculated_costs)
})

export const operationLabel = value => ({
  generate: '文章生成',
  optimize: 'GEO 调优',
  platform_adapt: '平台适配',
  visibility_sample: 'AI 可见度采样'
}[String(value)] || String(value || '未知操作'))

export const costStatusLabel = value => ({
  calculated: '已核算',
  unpriced: '未配置单价',
  unknown_usage: '用量未知',
  insufficient_usage: '用量不完整'
}[String(value)] || '状态未知')

export const usageStatusLabel = value => ({
  reported: '服务商回执',
  estimated: '系统估算',
  unknown: '用量未知'
}[String(value)] || '用量未知')

export const normalizeCostBill = response => {
  const root = unwrap(response)
  const summary = normalizeAggregate(root.summary || {})
  const validOperations = new Set(['generate', 'optimize', 'platform_adapt', 'visibility_sample'])
  const records = array(root.records).filter(item => validOperations.has(String(item?.operation))).map(item => ({
    id: item?.id ?? null,
    projectId: numberOrNull(item?.project_id),
    projectName: String(item?.project_name || '未归属项目'),
    articleId: numberOrNull(item?.article_id),
    articleTitle: String(item?.article_title || ''),
    operation: String(item.operation),
    provider: String(item?.canonical_provider || item?.provider || 'unknown'),
    model: String(item?.model || 'unknown'),
    createdAt: String(item?.created_at || ''),
    usageStatus: ['reported', 'estimated', 'unknown'].includes(String(item?.usage_status))
      ? String(item.usage_status)
      : 'unknown',
    inputTokens: numberOrNull(item?.input_tokens),
    outputTokens: numberOrNull(item?.output_tokens),
    totalTokens: numberOrNull(item?.total_tokens),
    cachedTokens: numberOrNull(item?.cached_tokens),
    reasoningTokens: numberOrNull(item?.reasoning_tokens),
    inputPricePerMillion: decimalOrNull(item?.input_price_per_million),
    outputPricePerMillion: decimalOrNull(item?.output_price_per_million),
    estimatedCost: decimalOrNull(item?.estimated_cost),
    currency: String(item?.currency || '').toUpperCase(),
    costStatus: ['calculated', 'unpriced', 'unknown_usage', 'insufficient_usage'].includes(String(item?.cost_status))
      ? String(item.cost_status)
      : 'unknown_usage',
    pricingSource: String(item?.pricing_source || ''),
    referenceType: String(item?.reference_type || ''),
    referenceId: String(item?.reference_id || '')
  }))

  const normalizeGroup = (item, dimensions) => ({
    ...Object.fromEntries(dimensions.map(key => [key, item?.[key] ?? null])),
    ...normalizeAggregate(item)
  })

  return {
    ledgerVersion: String(root.ledger_version || ''),
    generatedAt: String(root.generated_at || ''),
    period: {
      days: nonnegative(root.period?.days),
      start: root.period?.start ? String(root.period.start) : '',
      end: root.period?.end ? String(root.period.end) : ''
    },
    filters: {
      projectId: numberOrNull(root.filters?.project_id),
      operation: String(root.filters?.operation || ''),
      costStatus: String(root.filters?.cost_status || '')
    },
    summary,
    operations: array(root.by_operation).map(item => normalizeGroup(item, ['operation'])),
    models: array(root.by_model).map(item => normalizeGroup(item, ['provider', 'model'])),
    projects: array(root.by_project).map(item => normalizeGroup(item, ['project_id', 'project_name'])),
    days: array(root.by_day).map(item => normalizeGroup(item, ['date'])),
    records,
    recordWindow: {
      matched: nonnegative(root.record_window?.matched),
      returned: nonnegative(root.record_window?.returned),
      limit: nonnegative(root.record_window?.limit),
      hasMore: Boolean(root.record_window?.has_more)
    },
    publishingNonModel: root.publishing?.token_usage_applicable === false,
    publishingMessage: String(root.publishing?.message || ''),
    pricingMessage: String(root.pricing?.semantics || '')
  }
}

export const formatTokenCount = value => {
  const number = numberOrNull(value)
  return number == null ? '—' : Math.max(0, number).toLocaleString('zh-CN')
}

export const formatMoney = (amount, currency = '') => {
  const decimal = decimalOrNull(amount)
  if (decimal == null) return '待核算'
  const [integer, fraction = ''] = decimal.split('.')
  const grouped = integer.replace(/\B(?=(\d{3})+(?!\d))/g, ',')
  const exact = fraction ? `${grouped}.${fraction}` : grouped
  const code = String(currency || 'UNKNOWN').toUpperCase()
  if (code === 'CNY') return `¥${exact}`
  if (code === 'USD') return `$${exact}`
  return `${code} ${exact}`
}

const csvCell = value => {
  let text = value == null ? '' : String(value)
  if (/^[=+\-@]/.test(text)) text = `'${text}`
  return `"${text.replaceAll('"', '""')}"`
}

export const buildCostLedgerCsv = records => {
  const headers = [
    '凭证ID', '时间', '品牌项目', '操作', '服务商', '模型',
    '输入Token', '输出Token', '合计Token', '用量口径',
    '输入单价/百万Token', '输出单价/百万Token', '币种',
    '估算成本', '核算状态', '关联文章', '价格来源'
  ]
  const rows = array(records).map(item => [
    item.id,
    item.createdAt,
    item.projectName,
    operationLabel(item.operation),
    item.provider,
    item.model,
    item.inputTokens,
    item.outputTokens,
    item.totalTokens,
    usageStatusLabel(item.usageStatus),
    item.inputPricePerMillion,
    item.outputPricePerMillion,
    item.currency,
    item.estimatedCost,
    costStatusLabel(item.costStatus),
    item.articleTitle,
    item.pricingSource
  ])
  return `\uFEFF${[headers, ...rows].map(row => row.map(csvCell).join(',')).join('\r\n')}`
}
