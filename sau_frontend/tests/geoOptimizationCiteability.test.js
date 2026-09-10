import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import { normalizeGeoScoreResult } from '../src/utils/geoBlueprint.js'

test('optimization citeability accepts backend snake case and keeps all seven dimensions', () => {
  const result = normalizeGeoScoreResult({
    score: 70,
    citeability: {
      score: 78,
      pass_score: 85,
      passed: false,
      dimensions: {
        direct_answer: 80,
        entity_consistency: 100,
        evidence_traceability: 50,
        structural_extractability: 80,
        semantic_coverage: 70,
        faq_decision_support: 60,
        risk_hygiene: 100
      },
      blocking_issues: [{ code: 'risk.claim', priority: 'P0', message: '量化主张没有来源', action: '补充来源。' }]
    }
  })

  assert.equal(result.citeability.dimensions.length, 7)
  assert.equal(result.citeability.passScore, 85)
  assert.equal(result.citeability.passed, false)
  assert.equal(result.citeability.blockingIssues[0].priority, 'P0')
})

test('legacy optimization score without citeability stays compatible', () => {
  const result = normalizeGeoScoreResult({ score: 66, dimensions: { entity: 80 } })
  assert.equal(result.score, 66)
  assert.equal(result.citeability, null)
})

test('optimization view renders the gate only when citeability exists', () => {
  const source = readFileSync(new URL('../src/views/GeoOptimization.vue', import.meta.url), 'utf8')
  assert.match(source, /v-if="hasCiteability"/)
  assert.match(source, /发布前可引用性门禁/)
  assert.match(source, /P0 · 发布前必须处理/)
  assert.match(source, /citeabilityComparison/)
  assert.match(source, /remainingP0Issues/)
  assert.match(source, /normalizeGeoScoreResult\(response\.data\.before\)/)
  assert.match(source, /normalizeGeoScoreResult\(response\.data\.after\)/)
  assert.match(source, /response\.data\.warnings/)
  assert.match(source, /费用凭证需要人工核对/)
  assert.doesNotMatch(source, /:disabled="[^\"]*citeability/)
})
