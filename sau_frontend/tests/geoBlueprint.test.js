import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import {
  CITEABILITY_DIMENSIONS,
  normalizeGeoBlueprint,
  normalizeGeoScoreResult
} from '../src/utils/geoBlueprint.js'


test('blueprint normalizer unwraps the API envelope and preserves gate semantics', () => {
  const blueprint = normalizeGeoBlueprint({
    code: 200,
    data: {
      blueprint: {
        compiler_version: 'geo-compiler-2.0.0',
        topic: '企业如何部署 AI 助手',
        content_type: '解决方案',
        length: 1000,
        keywords: ['AI 助手', 'AI 助手', '企业部署'],
        query_matrix: {
          primary_intent: 'troubleshooting',
          intent_label: '问题解决',
          intent_source: 'topic',
          primary_query: '企业如何部署 AI 助手',
          requested_count: 12,
          total: 2,
          items: [
            {
              id: 'q-1',
              query: '企业部署 AI 助手需要哪些条件？',
              intent: 'informational',
              intent_label: '信息理解',
              cohort: 'decision',
              priority: 'P0'
            },
            'AI 助手如何保护企业数据？'
          ],
          cohort_policy: 'cover-awareness-to-decision'
        },
        entity: {
          name: '北京示例科技有限公司',
          product: '示例助手',
          aliases: ['示例 AI', '示例 AI'],
          website: 'https://example.test/product',
          owned_domains: ['example.test']
        },
        evidence: [
          {
            id: 'ev-1',
            claim: '产品支持本地部署',
            source_title: '产品说明',
            url: 'https://example.test/facts'
          },
          {
            id: 'ev-2',
            claim: '待核验主张',
            source_title: '危险链接',
            url: 'javascript:alert(1)'
          }
        ],
        gate: {
          ready: false,
          evidence_mode: 'strict',
          blocking_issues: [
            {
              code: 'missing-evidence',
              priority: 'P0',
              message: '缺少可核验来源',
              action: '先补充产品事实页。'
            }
          ],
          warnings: ['避免无法证实的第一名主张']
        },
        methodology: {
          claim_boundary: '蓝图不等于真实收录。'
        }
      }
    }
  })

  assert.equal(blueprint.version, 'geo-compiler-2.0.0')
  assert.deepEqual(blueprint.keywords, ['AI 助手', '企业部署'])
  assert.equal(blueprint.queryMatrix.intentLabel, '问题解决')
  assert.equal(blueprint.queryMatrix.items.length, 2)
  assert.equal(blueprint.queryMatrix.items[1].intentLabel, '信息理解')
  assert.equal(blueprint.gate.ready, false)
  assert.equal(blueprint.gate.evidenceMode, 'strict')
  assert.equal(blueprint.gate.blockingIssues[0].priority, 'P0')
  assert.equal(blueprint.gate.warnings[0].message, '避免无法证实的第一名主张')
  assert.equal(blueprint.evidence[0].url, 'https://example.test/facts')
  assert.equal(blueprint.evidence[1].url, '')
  assert.equal(blueprint.methodology.claim_boundary, '蓝图不等于真实收录。')
})


test('score normalizer exposes the seven citeability dimensions in a stable order', () => {
  const response = normalizeGeoScoreResult({
    data: {
      score: 82,
      dimensions: { entity: 90, keywords: 80 },
      suggestions: ['补充 FAQ'],
      citeability: {
        version: 'ai-citeability-rules-2.0.0',
        score: 76,
        grade: 'B',
        pass_score: 75,
        passed: true,
        publish_gate_passed: true,
        dimensions: {
          risk_hygiene: 92,
          semantic_coverage: 78,
          direct_answer: 88,
          faq_decision_support: 70,
          evidence_traceability: 61,
          entity_consistency: 85,
          structural_extractability: 81
        },
        dimension_weights: {
          direct_answer: 0.18
        },
        blocking_issues: [],
        suggestions: ['为关键结论增加来源链接。'],
        methodology: {
          claim_boundary: '结构分不代表真实引用率。'
        }
      }
    }
  })

  assert.equal(response.score, 82)
  assert.equal(response.citeability.score, 76)
  assert.equal(response.citeability.passed, true)
  assert.equal(response.citeability.publishGatePassed, true)
  assert.deepEqual(
    response.citeability.dimensions.map(item => item.key),
    CITEABILITY_DIMENSIONS.map(item => item.key)
  )
  assert.equal(response.citeability.dimensions[0].label, '答案前置')
  assert.equal(response.citeability.dimensions[0].weight, 0.18)
  assert.deepEqual(response.citeability.suggestions, ['为关键结论增加来源链接。'])
  assert.equal(response.citeability.methodology.claim_boundary, '结构分不代表真实引用率。')
})


test('score normalizer remains compatible with typo aliases and legacy responses', () => {
  const aliasResult = normalizeGeoScoreResult({
    score: 70,
    citability: {
      score: 70,
      pass_score: 75,
      dimensions: { direct_answer: 140, custom_signal: -2 },
      blocking_issues: ['需要补充直接答案']
    }
  })

  assert.equal(aliasResult.citeability.passed, false)
  assert.equal(aliasResult.citeability.publishGatePassed, true)
  assert.deepEqual(
    aliasResult.citeability.dimensions.map(item => [item.key, item.value]),
    [['direct_answer', 100], ['custom_signal', 0]]
  )
  assert.equal(aliasResult.citeability.blockingIssues[0].message, '需要补充直接答案')
  assert.equal(normalizeGeoScoreResult({ score: 60 }).citeability, null)
})


test('quality threshold and P0 publish gate remain independent', () => {
  const advisory = normalizeGeoScoreResult({
    citeability: {
      score: 71,
      pass_score: 75,
      passed: false,
      publish_gate_passed: true,
      dimensions: {},
      blocking_issues: []
    }
  })
  const blocked = normalizeGeoScoreResult({
    citeability: {
      score: 92,
      pass_score: 75,
      passed: true,
      publish_gate_passed: true,
      dimensions: {},
      blocking_issues: [{
        code: 'unsupported-claim',
        priority: 'P0',
        message: '绝对化主张缺少证据。'
      }]
    }
  })

  assert.equal(advisory.citeability.passed, false)
  assert.equal(advisory.citeability.publishGatePassed, true)
  assert.equal(blocked.citeability.publishGatePassed, false)
})


test('score normalizer preserves length profiles and null not-applicable dimensions', () => {
  const result = normalizeGeoScoreResult({
    data: {
      score: 84,
      dimensions: { entity: 90, structure: 80, faq: null },
      length_profile: {
        version: 'target-length-score-profile-1.0.0',
        key: 'compact',
        label: '精简稿（200–400字）',
        target_length: 200,
        requested_target_length: 200,
        effective_target_length: 1000,
        over_target: true,
        actual_length: 184,
        minimum_length: 120,
        source: 'explicit',
        applicable_dimensions: ['entity', 'structure'],
        not_applicable_dimensions: ['faq'],
        criteria: { minimum_paragraphs: 2 },
        guidance: '仅按适用检查归一评分。'
      },
      citeability: {
        score: 78,
        pass_score: 75,
        passed: true,
        dimensions: {
          direct_answer: 88,
          faq_decision_support: null
        },
        lengthProfile: {
          key: 'compact',
          targetLength: 200,
          applicableDimensions: ['direct_answer'],
          notApplicableDimensions: ['faq_decision_support']
        }
      }
    }
  })

  assert.equal(result.dimensions.faq, null)
  assert.equal(result.lengthProfile.key, 'compact')
  assert.equal(result.lengthProfile.targetLength, 200)
  assert.equal(result.lengthProfile.requestedTargetLength, 200)
  assert.equal(result.lengthProfile.effectiveTargetLength, 1000)
  assert.equal(result.lengthProfile.overTarget, true)
  assert.equal(result.lengthProfile.minimumLength, 120)
  assert.equal(result.lengthProfile.applicabilityProvided, true)
  assert.deepEqual(result.lengthProfile.notApplicableDimensions, ['faq'])
  assert.deepEqual(result.lengthProfile.guidance, ['仅按适用检查归一评分。'])
  assert.equal(result.citeability.dimensions[1].key, 'faq_decision_support')
  assert.equal(result.citeability.dimensions[1].value, 0)
  assert.equal(result.citeability.dimensions[1].applicable, false)
  assert.equal(result.citeability.lengthProfile.targetLength, 200)
})


test('content creation wires blueprint preview and citeability to the existing screen', () => {
  const api = readFileSync(new URL('../src/api/article.js', import.meta.url), 'utf8')
  const component = readFileSync(
    new URL('../src/views/ContentCreation.vue', import.meta.url),
    'utf8'
  )

  assert.match(api, /getGeoBlueprint\(data, config = \{\}\)/)
  assert.match(api, /\/api\/geo\/content-blueprint/)
  assert.match(component, /AI 引用蓝图/)
  assert.match(component, /query_count:\s*12/)
  assert.match(component, /normalizeGeoBlueprint\(response\)/)
  assert.match(component, /normalizeGeoScoreResult\(response\)/)
  assert.match(component, /citeabilityDimensions/)
  assert.match(component, /发布前门禁预检/)
  assert.match(component, /不代表页面已被抓取、收录或被模型引用/)
  assert.match(component, /:value="200">200 字/)
  assert.match(component, /:min="200"/)
  assert.match(component, /:max="3000"/)
  assert.match(component, /可自定义 200–3000 字/)
  assert.match(component, /targetLength: brief\.length/)
  assert.match(component, /target_length: brief\.length/)
  assert.match(component, /基础 GEO 长度归一分/)
  assert.match(component, /本档不强制/)
  assert.match(component, /visibleScoreSuggestions/)
  assert.match(component, /实际内容已升级为/)
  assert.match(component, /不能继续享受短稿结构豁免/)
  assert.match(component, /requestedTargetLength/)
  assert.match(component, /publishGatePassed/)
  assert.match(component, /可发布，建议先优化/)
  assert.match(component, /发布硬门禁未通过/)
  assert.match(component, /content:\s*article\.content\s*\|\|\s*''/)
  assert.doesNotMatch(component, /mergeFaqIntoContent/)
})
