export const BATCH_GENERATION_LIMIT = 20

const finiteNumber = value => {
  if (value == null || value === '') return null
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : null
}

const nonNegativeInteger = value => {
  const parsed = finiteNumber(value)
  return parsed == null ? 0 : Math.max(0, Math.trunc(parsed))
}

export const parseBatchTopics = value => {
  const lines = String(value || '')
    .split(/\r?\n/)
    .map(item => item.trim())
    .filter(Boolean)
  return [...new Set(lines)]
}

export const normalizeBatchGenerationResult = value => {
  const root = value && typeof value === 'object' ? value : {}
  const summary = root.cost_summary && typeof root.cost_summary === 'object'
    ? root.cost_summary
    : {}
  const tokenTotals = summary.token_totals && typeof summary.token_totals === 'object'
    ? summary.token_totals
    : {}
  const calculatedCosts = Array.isArray(summary.calculated_costs)
    ? summary.calculated_costs.map(item => ({
        currency: String(item?.currency || 'UNKNOWN'),
        amount: item?.estimated_cost == null ? '' : String(item.estimated_cost)
      })).filter(item => item.amount !== '')
    : []
  const items = Array.isArray(root.items) ? root.items.map(item => {
    const source = item && typeof item === 'object' ? item : {}
    const receipt = source.cost_receipt && typeof source.cost_receipt === 'object'
      ? source.cost_receipt
      : {}
    return {
      ...source,
      topic: String(source.topic || ''),
      status: String(source.status || 'unknown'),
      costReceipt: {
        recorded: Boolean(receipt.recorded),
        inputTokens: finiteNumber(receipt.input_tokens),
        outputTokens: finiteNumber(receipt.output_tokens),
        totalTokens: finiteNumber(receipt.total_tokens),
        usageStatus: String(receipt.usage_status || 'unknown'),
        costStatus: String(receipt.cost_status || 'not_recorded'),
        estimatedCost: receipt.estimated_cost == null ? null : String(receipt.estimated_cost),
        currency: String(receipt.currency || '')
      }
    }
  }) : []
  return {
    ...root,
    items,
    created_count: nonNegativeInteger(root.created_count),
    failed_count: nonNegativeInteger(root.failed_count),
    replayed_count: nonNegativeInteger(root.replayed_count),
    batch_limit: nonNegativeInteger(root.batch_limit) || BATCH_GENERATION_LIMIT,
    execution_mode: String(root.execution_mode || 'sequential'),
    cost_summary: {
      operationCount: nonNegativeInteger(summary.operation_count),
      knownTotalTokens: finiteNumber(summary.known_total_tokens),
      knownInputTokens: finiteNumber(summary.known_input_tokens ?? summary.input_tokens ?? tokenTotals.input_tokens),
      knownOutputTokens: finiteNumber(summary.known_output_tokens ?? summary.output_tokens ?? tokenTotals.output_tokens),
      knownTokenRecordCount: nonNegativeInteger(summary.known_token_record_count),
      unknownUsageCount: nonNegativeInteger(summary.unknown_usage_count),
      unpricedCount: nonNegativeInteger(summary.unpriced_count),
      unknownCostItemCount: nonNegativeInteger(summary.unknown_cost_item_count),
      missingReceiptItemCount: nonNegativeInteger(summary.missing_receipt_item_count),
      calculatedCosts
    }
  }
}

export const formatBatchCost = summary => {
  const costs = Array.isArray(summary?.calculatedCosts) ? summary.calculatedCosts : []
  if (!costs.length) return '尚无可计算金额'
  return costs.map(item => `${item.currency} ${item.amount}`).join(' + ')
}

export const formatBatchItemTokens = receipt => (
  receipt?.totalTokens == null ? '用量未知' : Number(receipt.totalTokens).toLocaleString('zh-CN')
)

export const formatBatchItemCost = receipt => {
  if (receipt?.costStatus === 'calculated' && receipt?.estimatedCost != null) {
    return `${receipt.currency || 'UNKNOWN'} ${receipt.estimatedCost}`
  }
  if (receipt?.costStatus === 'unpriced') return '有 Token，未配置单价'
  if (receipt?.costStatus === 'insufficient_usage') return 'Token 明细不足'
  return '成本未知'
}
