import assert from 'node:assert/strict'
import test from 'node:test'

import {
  filterLengthAwareSuggestions,
  isLengthMetricApplicable,
  resolveScoreBand,
  resolveScoreLengthProfile
} from '../src/utils/scoreLengthProfile.js'


test('compact profile hides long-form requirements but keeps valid short-form structure advice', () => {
  const profile = resolveScoreLengthProfile(200)
  const suggestions = filterLengthAwareSuggestions([
    '建议将正文扩充至至少 600 字',
    '建议将正文拆分为至少 3 个清晰段落',
    '建议将正文拆分为至少 2 个清晰段落',
    '建议增加清晰的 H2/H3 小标题',
    '建议增加 2–3 个常见问题及答案',
    '建议增加总结或结语',
    '建议在开头 80–150 字直接回答核心问题',
    '建议为关键结论补充来源、日期和适用范围'
  ], profile)

  assert.equal(profile.key, 'compact')
  assert.equal(profile.shortForm, true)
  assert.equal(isLengthMetricApplicable(profile, 'base', 'faq'), false)
  assert.equal(isLengthMetricApplicable(profile, 'base', 'structure'), true)
  assert.equal(isLengthMetricApplicable(profile, 'citeability', 'faq_decision_support'), false)
  assert.deepEqual(suggestions, [
    '建议将正文拆分为至少 2 个清晰段落',
    '建议在开头 80–150 字直接回答核心问题',
    '建议为关键结论补充来源、日期和适用范围'
  ])
})


test('target lengths resolve to the same four bands as the backend contract', () => {
  assert.equal(resolveScoreLengthProfile(200).key, 'compact')
  assert.equal(resolveScoreLengthProfile(400).key, 'compact')
  assert.equal(resolveScoreLengthProfile(600).key, 'short')
  assert.equal(resolveScoreLengthProfile(799).key, 'short')
  assert.equal(resolveScoreLengthProfile(1000).key, 'standard')
  assert.equal(resolveScoreLengthProfile(1500).key, 'long')
})


test('server applicability and criteria are authoritative for the calibration ledger', () => {
  const profile = resolveScoreLengthProfile(200, {
    version: 'target-length-score-profile-1.0.0',
    key: 'compact',
    label: '精简稿（200–400字）',
    targetLength: 200,
    actualLength: 184,
    minimumLength: 120,
    source: 'explicit',
    applicableDimensions: ['entity', 'keywords', 'structure', 'citation'],
    notApplicableDimensions: ['faq'],
    applicabilityProvided: true,
    criteria: {
      minimum_paragraphs: 2,
      headings_required: false,
      faq_required: false,
      summary_required: false
    },
    guidance: '不适用检查的有效权重为 0。'
  })

  assert.equal(profile.serverBacked, true)
  assert.equal(profile.key, 'compact')
  assert.equal(profile.actualLength, 184)
  assert.equal(isLengthMetricApplicable(profile, 'base', 'faq'), false)
  assert.equal(isLengthMetricApplicable(profile, 'base', 'structure'), true)
  assert.equal(profile.applicableLabels.includes('内容结构'), true)
  assert.equal(profile.optionalLabels.includes('600 字门槛'), true)
  assert.equal(profile.optionalLabels.includes('至少 3 段'), true)
  assert.equal(profile.optionalLabels.includes('H2 / H3'), true)
  assert.equal(profile.optionalLabels.includes('FAQ'), true)
  assert.equal(profile.optionalLabels.includes('重复总结'), true)
  assert.deepEqual(profile.scoringGuidance, ['不适用检查的有效权重为 0。'])
})


test('an over-target short brief uses the backend effective standard band', () => {
  const profile = resolveScoreLengthProfile(600, {
    key: 'standard',
    label: '标准稿（800–1199字）',
    targetLength: 600,
    requestedTargetLength: 600,
    effectiveTargetLength: 850,
    actualLength: 850,
    source: 'actual_length_upgrade',
    overTarget: true,
    applicableDimensions: ['entity', 'keywords', 'structure', 'faq', 'citation'],
    notApplicableDimensions: [],
    applicabilityProvided: true,
    criteria: { minimum_paragraphs: 3, headings_required: true, faq_required: true }
  })

  assert.equal(profile.key, 'standard')
  assert.equal(profile.shortForm, false)
  assert.equal(profile.requestedTargetLength, 600)
  assert.equal(profile.effectiveTargetLength, 850)
  assert.equal(profile.targetLabel, '600 字目标')
  assert.equal(profile.overTarget, true)
  assert.equal(isLengthMetricApplicable(profile, 'base', 'faq'), true)
})


test('a 200-character brief cannot retain compact exemptions at 1000 effective characters', () => {
  const profile = resolveScoreLengthProfile(200, {
    key: 'standard',
    label: '标准稿（800–1199字）',
    targetLength: 200,
    requestedTargetLength: 200,
    effectiveTargetLength: 1000,
    actualLength: 1000,
    source: 'actual_length_upgrade',
    overTarget: true,
    applicableDimensions: ['entity', 'keywords', 'structure', 'faq', 'citation'],
    notApplicableDimensions: [],
    applicabilityProvided: true,
    criteria: { minimum_paragraphs: 3, headings_required: true, faq_required: true }
  })
  const suggestions = filterLengthAwareSuggestions(
    ['建议将正文扩充至至少 600 字', '建议增加清晰的 H2/H3 小标题'],
    profile
  )

  assert.equal(profile.key, 'standard')
  assert.equal(profile.shortForm, false)
  assert.equal(profile.effectiveTargetLength, 1000)
  assert.equal(isLengthMetricApplicable(profile, 'base', 'faq'), true)
  assert.equal(suggestions.length, 2)
})


test('legacy server profile remains authoritative without borrowing the current brief length', () => {
  const profile = resolveScoreLengthProfile(200, {
    key: 'legacy',
    label: '兼容长文规则',
    targetLength: null,
    requestedTargetLength: null,
    effectiveTargetLength: null,
    source: 'legacy',
    overTarget: false,
    applicableDimensions: ['entity', 'keywords', 'structure', 'faq', 'citation'],
    notApplicableDimensions: [],
    applicabilityProvided: true,
    criteria: { minimum_paragraphs: 3, headings_required: true, faq_required: true }
  })

  assert.equal(profile.serverBacked, true)
  assert.equal(profile.key, 'legacy')
  assert.equal(profile.shortForm, false)
  assert.equal(profile.targetLength, null)
  assert.equal(profile.requestedTargetLength, null)
  assert.equal(profile.effectiveTargetLength, null)
  assert.equal(profile.targetLabel, '旧稿未声明目标')
  assert.equal(profile.label, '兼容长文规则')
  assert.equal(isLengthMetricApplicable(profile, 'base', 'faq'), true)
})


test('score bands only label the returned score and preserve existing thresholds', () => {
  assert.deepEqual(resolveScoreBand(85), { code: 'A', label: '人工复核档', minimum: 85 })
  assert.deepEqual(resolveScoreBand(70), { code: 'B', label: '继续加强档', minimum: 70 })
  assert.deepEqual(resolveScoreBand(69), { code: 'C', label: '优先补证据档', minimum: 0 })
})
