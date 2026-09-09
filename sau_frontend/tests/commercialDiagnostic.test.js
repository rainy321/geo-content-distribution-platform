import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import {
  normalizeCommercialDiagnostic,
  normalizeProjectCostSummary,
  operationLabel
} from '../src/utils/commercialDiagnostic.js'

test('commercial diagnostic normalizes dimensions, priorities and privacy boundary', () => {
  const result = normalizeCommercialDiagnostic({
    code: 200,
    data: {
      run_id: 9,
      score: 63,
      grade: 'C',
      dimensions: [{ key: 'positioning', label: '产品定位', score: 50, weight: 20, status: 'attention' }],
      gaps: [{ key: 'x', priority: 'P0', title: '缺少基线', action: '建立基线', metric_target: '样本 >= 5' }],
      methodology: {
        external_model_used: false,
        customer_content_sent_externally: false,
        claim_boundary: '不是官方排名'
      }
    }
  })

  assert.equal(result.runId, 9)
  assert.equal(result.dimensions[0].score, 50)
  assert.equal(result.gaps[0].priority, 'P0')
  assert.equal(result.gaps[0].metricTarget, '样本 >= 5')
  assert.equal(result.gapCounts.P0, 1)
  assert.equal(result.externalModelUsed, false)
  assert.equal(result.contentSentExternally, false)
})

test('cost summary preserves unknown and non-model publishing instead of turning them into zero cost', () => {
  const result = normalizeProjectCostSummary({
    data: {
      summary: {
        operation_count: 3,
        known_total_tokens: 1200,
        known_token_record_count: 2,
        unknown_usage_count: 1,
        unpriced_count: 2,
        calculated_costs: [{ currency: 'CNY', estimated_cost: '0.018' }]
      },
      by_operation: [{ operation: 'generate', operation_count: 2, known_total_tokens: 1200, unknown_usage_count: 0 }],
      recent: [
        {
          id: 18,
          operation: 'generate',
          provider: 'dashscope',
          model: 'qwen-plus',
          created_at: '2026-09-10T00:00:00Z',
          usage_status: 'reported',
          input_tokens: 700,
          output_tokens: 500,
          total_tokens: 1200,
          input_price_per_million: '0.8',
          output_price_per_million: '2',
          estimated_cost: '0.00156',
          currency: 'CNY',
          cost_status: 'calculated',
          metadata: { prompt: 'must never reach the client receipt model' }
        },
        {
          id: 19,
          operation: 'optimize',
          provider: 'unknown',
          model: 'unknown',
          usage_status: 'unknown',
          input_tokens: null,
          output_tokens: null,
          total_tokens: null,
          cost_status: 'unknown_usage'
        },
        {
          id: 20,
          operation: 'publish',
          provider: 'browser',
          model: 'none',
          total_tokens: 0,
          cost_status: 'calculated'
        },
        {
          id: 21,
          operation: 'visibility_sample',
          provider: 'qwen',
          model: 'qwen-plus',
          usage_status: 'reported',
          input_tokens: 30,
          output_tokens: 120,
          total_tokens: 150,
          cost_status: 'unpriced'
        }
      ],
      publishing: { token_usage_applicable: false, message: '发布未调用模型' },
      pricing: { semantics: '未配置价格不是零成本' }
    }
  })

  assert.equal(result.knownTotalTokens, 1200)
  assert.equal(result.unknownUsageCount, 1)
  assert.equal(result.unpricedCount, 2)
  assert.equal(result.calculatedCosts[0].amount, '0.018')
  assert.equal(result.publishingNonModel, true)
  assert.equal(result.recent.length, 3)
  assert.deepEqual(result.recent[0], {
    id: 18,
    operation: 'generate',
    provider: 'dashscope',
    model: 'qwen-plus',
    createdAt: '2026-09-10T00:00:00Z',
    usageStatus: 'reported',
    inputTokens: 700,
    outputTokens: 500,
    totalTokens: 1200,
    inputPricePerMillion: 0.8,
    outputPricePerMillion: 2,
    estimatedCost: 0.00156,
    currency: 'CNY',
    costStatus: 'calculated'
  })
  assert.equal('metadata' in result.recent[0], false)
  assert.equal(result.recent[1].totalTokens, null)
  assert.equal(operationLabel('optimize'), 'GEO 调优')
  assert.equal(result.recent[2].operation, 'visibility_sample')
  assert.equal(result.recent[2].totalTokens, 150)
  assert.equal(result.recent[2].costStatus, 'unpriced')
  assert.equal(operationLabel('visibility_sample'), 'AI 可见度采样')
})

test('AI visibility view exposes diagnostic and cost endpoints with explicit boundary copy', () => {
  const apiSource = readFileSync(new URL('../src/api/diagnostics.js', import.meta.url), 'utf8')
  const viewSource = readFileSync(new URL('../src/views/AiVisibility.vue', import.meta.url), 'utf8')

  assert.match(apiSource, /\/api\/geo\/diagnostics/)
  assert.match(apiSource, /\/api\/ai-usage\/projects\//)
  assert.match(viewSource, /客户诊断/)
  assert.match(viewSource, /发布自动化不产生模型 Token/)
  assert.match(viewSource, /未提供 Token ≠ 0 Token/)
  assert.match(viewSource, /逐次调用明细/)
  assert.match(viewSource, /输入 \/ 输出 \/ 合计/)
})
