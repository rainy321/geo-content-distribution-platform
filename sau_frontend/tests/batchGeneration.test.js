import test from 'node:test'
import assert from 'node:assert/strict'

import {
  BATCH_GENERATION_LIMIT,
  formatBatchCost,
  formatBatchItemCost,
  formatBatchItemTokens,
  normalizeBatchGenerationResult,
  parseBatchTopics
} from '../src/utils/batchGeneration.js'

test('batch topics retain all unique lines so over-limit input is visible', () => {
  const topics = Array.from({ length: BATCH_GENERATION_LIMIT + 1 }, (_, index) => `主题 ${index + 1}`)
  const parsed = parseBatchTopics(`${topics.join('\n')}\n主题 1\n`)
  assert.equal(parsed.length, 21)
  assert.equal(parsed.at(-1), '主题 21')
})

test('batch result keeps unknown usage distinct from zero cost', () => {
  const normalized = normalizeBatchGenerationResult({
    created_count: 2,
    failed_count: 1,
    items: [{
      topic: '主题 A',
      status: 'created',
      cost_receipt: {
        recorded: true,
        usage_status: 'reported',
        total_tokens: 720,
        cost_status: 'calculated',
        estimated_cost: '0.0123',
        currency: 'CNY'
      }
    }],
    cost_summary: {
      operation_count: 3,
      known_total_tokens: 1580,
      token_totals: { input_tokens: 1000, output_tokens: 580 },
      known_token_record_count: 2,
      unknown_usage_count: 1,
      unpriced_count: 1,
      unknown_cost_item_count: 2,
      calculated_costs: [{ currency: 'CNY', estimated_cost: '0.0316' }]
    }
  })

  assert.equal(normalized.batch_limit, 20)
  assert.equal(normalized.cost_summary.knownTotalTokens, 1580)
  assert.equal(normalized.cost_summary.knownInputTokens, 1000)
  assert.equal(normalized.cost_summary.knownOutputTokens, 580)
  assert.equal(normalized.cost_summary.unknownUsageCount, 1)
  assert.equal(normalized.cost_summary.unknownCostItemCount, 2)
  assert.equal(formatBatchCost(normalized.cost_summary), 'CNY 0.0316')
  assert.equal(formatBatchItemTokens(normalized.items[0].costReceipt), '720')
  assert.equal(formatBatchItemCost(normalized.items[0].costReceipt), 'CNY 0.0123')
})

test('missing cost receipt is shown as unknown rather than a numeric zero', () => {
  const normalized = normalizeBatchGenerationResult({ created_count: 1 })
  assert.equal(normalized.cost_summary.knownTotalTokens, null)
  assert.equal(formatBatchCost(normalized.cost_summary), '尚无可计算金额')
})
