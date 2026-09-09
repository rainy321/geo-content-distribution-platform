import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import {
  buildVisibilitySeries,
  diagnosticRetestDelta,
  latestCitationEvidence,
  visibilityDelta
} from '../src/utils/dashboardPresentation.js'

test('dashboard visibility series uses only real finite history and restores chronological order', () => {
  const series = buildVisibilitySeries([
    { mentionRate: 72.5, createdAt: '2026-09-10T10:00:00Z' },
    { mentionRate: null, createdAt: '2026-09-09T10:00:00Z' },
    { mentionRate: 30, createdAt: '2026-09-08T10:00:00Z' }
  ])

  assert.deepEqual(series, [
    { value: 30, label: '2026-09-08T10:00:00Z' },
    { value: 72.5, label: '2026-09-10T10:00:00Z' }
  ])
  assert.equal(visibilityDelta(series), 42.5)
  assert.equal(visibilityDelta(series.slice(0, 1)), null)
})

test('dashboard extracts citation evidence without inventing a source', () => {
  const evidence = latestCitationEvidence({
    citations: [{ domain: 'example.test', url: 'https://example.test/a', isOwned: true }],
    samples: [{ evidence: [{ quote: 'DongClaw 提供本地内容工作流。' }] }]
  })

  assert.equal(evidence.quote, 'DongClaw 提供本地内容工作流。')
  assert.equal(evidence.domain, 'example.test')
  assert.equal(evidence.owned, true)
  assert.equal(latestCitationEvidence({ citations: [], samples: [] }), null)
})

test('diagnostic retest delta compares newest run against the previous run', () => {
  assert.equal(diagnosticRetestDelta([{ score: 68.2 }, { score: 61.1 }]), 7.1)
  assert.equal(diagnosticRetestDelta([{ score: 68.2 }]), null)
})

test('reference-led dashboard keeps real data language and wired product routes', () => {
  const source = readFileSync(new URL('../src/views/Dashboard.vue', import.meta.url), 'utf8')
  const chart = readFileSync(new URL('../src/components/VisibilityTrendCanvas.vue', import.meta.url), 'utf8')

  assert.match(source, /让品牌，<br>出现在 AI 的<br>答案里。/)
  assert.match(source, /品牌可见度概览/)
  assert.match(source, /真实数据/)
  assert.match(source, /diagnosticsApi\.getProjectCostSummary/)
  assert.match(source, /visibilityApi\.getRun/)
  assert.match(source, /router\.push\('\/ai-visibility'\)/)
  assert.match(source, /创建第一个品牌项目/)
  assert.match(source, /无需先连接媒体账号，也不会触发真实发布/)
  assert.match(chart, /<canvas/)
  assert.match(chart, /ResizeObserver/)
})

test('AI visibility keeps live form, history, evidence, diagnosis and retest delta together', () => {
  const source = readFileSync(new URL('../src/views/AiVisibility.vue', import.meta.url), 'utf8')

  assert.match(source, /visibilityApi\.analyze/)
  assert.match(source, /historyRuns/)
  assert.match(source, /客户诊断与 AI 成本凭证/)
  assert.match(source, /diagnosticRetestDelta/)
  assert.match(source, /较上次诊断/)
  assert.match(source, /逐次调用明细/)
})
