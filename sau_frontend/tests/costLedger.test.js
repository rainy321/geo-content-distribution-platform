import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import {
  buildCostLedgerCsv,
  formatMoney,
  normalizeCostBill,
  normalizePriceCatalog,
  operationLabel
} from '../src/utils/costLedger.js'

const response = {
  code: 200,
  data: {
    ledger_version: 'ai-cost-ledger-1.2.0',
    period: { days: 30, start: '2026-08-11T00:00:00Z', end: '2026-09-10T00:00:00Z' },
    summary: {
      operation_count: 3,
      known_total_tokens: 1800,
      known_token_record_count: 2,
      calculated_cost_count: 1,
      cost_coverage_percent: 33.3,
      unknown_cost_count: 2,
      unknown_usage_count: 1,
      unpriced_count: 1,
      insufficient_usage_count: 0,
      calculated_costs: [
        { currency: 'CNY', estimated_cost: '0.00500001' },
        { currency: 'USD', estimated_cost: '0.00002' }
      ]
    },
    by_operation: [{ operation: 'generate', operation_count: 1, known_total_tokens: 1500 }],
    by_model: [{ provider: 'qwen', model: 'qwen-plus', operation_count: 2, known_total_tokens: 1800 }],
    by_project: [{ project_id: 1, project_name: 'Acme', operation_count: 3, known_total_tokens: 1800 }],
    by_day: [{ date: '2026-09-10', operation_count: 3, known_total_tokens: 1800 }],
    records: [
      {
        id: 1,
        project_id: 1,
        project_name: 'Acme',
        article_id: 9,
        article_title: '=unsafe title',
        operation: 'generate',
        provider: 'dashscope',
        canonical_provider: 'qwen',
        model: 'qwen-plus',
        created_at: '2026-09-10T00:00:00Z',
        usage_status: 'reported',
        input_tokens: 1000,
        output_tokens: 500,
        total_tokens: 1500,
        input_price_per_million: '2.00000000',
        output_price_per_million: '6',
        estimated_cost: '0.00500001',
        currency: 'CNY',
        cost_status: 'calculated',
        pricing_source: 'environment:qwen/qwen-plus',
        metadata: { prompt: 'must not survive normalization' }
      },
      {
        id: 2,
        operation: 'optimize',
        usage_status: 'unknown',
        total_tokens: null,
        estimated_cost: null,
        cost_status: 'unknown_usage'
      }
    ],
    record_window: { matched: 3, returned: 2, limit: 2, has_more: true },
    publishing: { token_usage_applicable: false, message: '发布不产生模型 Token' },
    pricing: { semantics: '最终以供应商账单为准' }
  }
}

test('billing normalizer preserves unknowns, currency boundaries and exact decimal strings', () => {
  const bill = normalizeCostBill(response)

  assert.equal(bill.summary.knownTotalTokens, 1800)
  assert.equal(bill.summary.unknownUsageCount, 1)
  assert.equal(bill.summary.unpricedCount, 1)
  assert.deepEqual(bill.summary.calculatedCosts, [
    { currency: 'CNY', amount: '0.00500001' },
    { currency: 'USD', amount: '0.00002' }
  ])
  assert.equal(bill.records[0].provider, 'qwen')
  assert.equal(bill.records[0].estimatedCost, '0.00500001')
  assert.equal(bill.records[1].totalTokens, null)
  assert.equal(bill.records[1].estimatedCost, null)
  assert.equal('metadata' in bill.records[0], false)
  assert.equal(bill.recordWindow.hasMore, true)
  assert.equal(bill.publishingNonModel, true)
})

test('money formatter never merges currencies or turns unknown cost into zero', () => {
  assert.equal(formatMoney('0.00500001', 'CNY'), '¥0.00500001')
  assert.equal(formatMoney('1000.25', 'USD'), '$1,000.25')
  assert.equal(formatMoney(null, 'CNY'), '待核算')
})

test('price catalog normalizer preserves exact decimal prices and pending model counts', () => {
  const catalog = normalizePriceCatalog({
    code: 200,
    data: {
      catalog_version: 'ai-price-catalog-1.0.0',
      unit: 'per_million_tokens',
      entries: [{
        id: 7,
        provider: 'qwen',
        model: 'qwen3.8-max',
        input_price_per_million: '0.12345678',
        output_price_per_million: '9',
        currency: 'CNY',
        source_note: '官网价格'
      }],
      detected_models: [{
        provider: 'qwen',
        model: 'qwen3.8-max',
        receipt_count: 20,
        unpriced_receipt_count: 12,
        known_total_tokens: 40301,
        price_entry_id: 7,
        priced: true,
        input_price_per_million: '0.12345678',
        output_price_per_million: '9',
        currency: 'CNY'
      }]
    }
  })

  assert.equal(catalog.entries[0].inputPricePerMillion, '0.12345678')
  assert.equal(catalog.detectedModels[0].unpricedReceiptCount, 12)
  assert.equal(catalog.detectedModels[0].knownTotalTokens, 40301)
})

test('CSV export contains the displayed receipt window and neutralizes spreadsheet formulas', () => {
  const bill = normalizeCostBill(response)
  const csv = buildCostLedgerCsv(bill.records)

  assert.ok(csv.startsWith('\uFEFF'))
  assert.match(csv, /文章生成/)
  assert.match(csv, /0\.00500001/)
  assert.match(csv, /'\=unsafe title/)
  assert.doesNotMatch(csv, /must not survive normalization/)
  assert.equal(operationLabel('visibility_sample'), 'AI 可见度采样')
})

test('desktop shell contains a dedicated cost ledger route and audit language', () => {
  const appSource = readFileSync(new URL('../src/App.vue', import.meta.url), 'utf8')
  const routerSource = readFileSync(new URL('../src/router/index.js', import.meta.url), 'utf8')
  const viewSource = readFileSync(new URL('../src/views/CostLedger.vue', import.meta.url), 'utf8')
  const apiSource = readFileSync(new URL('../src/api/billing.js', import.meta.url), 'utf8')

  assert.match(appSource, /index="\/cost-ledger"/)
  assert.match(appSource, /成本账单/)
  assert.match(routerSource, /path: '\/cost-ledger'/)
  assert.match(apiSource, /\/api\/ai-usage\/billing/)
  assert.match(apiSource, /\/api\/ai-usage\/prices/)
  assert.match(viewSource, /全部筛选结果参与汇总/)
  assert.match(viewSource, /单价设置/)
  assert.match(viewSource, /每百万 Token/)
  assert.match(viewSource, /已核算凭证永不改写/)
  assert.match(viewSource, /登录、上传与平台发布/)
  assert.match(viewSource, /不适用，不是 0/)
})
