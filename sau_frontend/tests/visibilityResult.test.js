import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import {
  buildVisibilityPayload,
  normalizeVisibilityHistory,
  normalizeVisibilityResult,
  parseVisibilityResponses,
  toSafeVisibilityUrl
} from '../src/utils/visibilityResult.js'

test('pasted answers split only on a standalone dash divider', () => {
  const answers = parseVisibilityResponses('第一段\n有正文\n---\n第二段\n\n仍属于第二段')
  assert.deepEqual(answers, ['第一段\n有正文', '第二段\n\n仍属于第二段'])
})

test('request payload follows the visibility endpoint contract', () => {
  assert.deepEqual(buildVisibilityPayload({
    projectId: 7,
    prompt: ' 推荐什么工具？ ',
    responses: ['回答一', ' 回答二 '],
    aliases: [' GEO Lab ', 'GEO Lab', '几何实验室'],
    competitors: ['竞品甲', '竞品乙'],
    provider: '千问网页',
    model: 'qwen-plus'
  }), {
    project_id: 7,
    prompt: '推荐什么工具？',
    responses: [
      { text: '回答一', provider: '千问网页', model: 'qwen-plus' },
      { text: '回答二', provider: '千问网页', model: 'qwen-plus' }
    ],
    aliases: ['GEO Lab', '几何实验室'],
    competitors: ['竞品甲', '竞品乙'],
    persist: true
  })
})

test('frontend API posts to the protected visibility analysis endpoint', () => {
  const apiSource = readFileSync(new URL('../src/api/visibility.js', import.meta.url), 'utf8')
  const viewSource = readFileSync(new URL('../src/views/AiVisibility.vue', import.meta.url), 'utf8')

  assert.match(apiSource, /http\.post\('\/api\/geo\/visibility\/analyze', data, config\)/)
  assert.match(apiSource, /http\.get\('\/api\/geo\/visibility\/runs'/)
  assert.match(apiSource, /\/api\/geo\/visibility\/runs\/\$\{encodeURIComponent\(runId\)\}/)
  assert.match(viewSource, /visibilityApi\.analyze\(payload, \{ suppressGlobalError: true \}\)/)
  assert.match(viewSource, /v-if="analysisError"/)
  assert.doesNotMatch(apiSource, /qwen|external[_-]model/i)
})

test('history list tolerates lightweight snake case rows', () => {
  const history = normalizeVisibilityHistory({
    code: 200,
    data: {
      runs: [{
        run_id: 31,
        project_id: 7,
        prompt: '哪个工具更适合？',
        sample_count: 4,
        mention_rate: 0.5,
        share_of_voice: 0.625,
        confidence_level: 'MEDIUM',
        created_at: '2026-09-09T10:00:00Z'
      }]
    }
  })

  assert.equal(history[0].id, 31)
  assert.equal(history[0].mentionRate, 50)
  assert.equal(history[0].shareOfVoice, 62.5)
  assert.equal(history[0].confidence.label, '中')
})

test('stored run wrapper restores result and persistence metadata', () => {
  const result = normalizeVisibilityResult({
    code: 200,
    data: {
      id: 44,
      created_at: '2026-09-09T11:00:00Z',
      prompt: '来自历史外层的问题',
      result: {
        summary: { sample_count: 1, mention_rate: 1 },
        samples: [{ text: 'GEO Lab', brand_mentioned: true }]
      }
    }
  })

  assert.equal(result.runId, 44)
  assert.equal(result.prompt, '来自历史外层的问题')
  assert.equal(result.analyzedAt, '2026-09-09T11:00:00Z')
  assert.equal(result.summary.mentionRate, 100)
  assert.equal(result.samples[0].brandMentioned, true)
})

test('snake case analysis becomes the stable frontend evidence model', () => {
  const result = normalizeVisibilityResult({
    data: {
      summary: {
        sample_count: 2,
        mentioned_count: 1,
        mention_rate: 0.5,
        share_of_voice: 40,
        average_rank: 2,
        citation_count: 1,
        owned_citation_count: 1,
        confidence: { level: 'medium', reason: '样本较少' }
      },
      samples: [{
        sample_id: 'a',
        brand_mentioned: true,
        recommendation_rank: 2,
        evidence: [{ type: 'alias', quote: '推荐 GEO Lab' }],
        competitor_mentions: [{ name: '竞品甲', occurrence_count: 2 }],
        citations: [{ url: 'https://example.test/post', is_owned: true }]
      }],
      entities: [{ name: 'GEO Lab', type: 'brand', mention_rate: 50, share_of_voice: 40 }],
      methodology: {
        external_model_used: false,
        sample_semantics: 'MEAN_WITH_DISPERSION',
        citation_semantics: '只统计回答正文中的显式链接',
        metric_comparability: '只比较相同口径批次'
      },
      algorithm_versions: { integration: 'ai-visibility-python-port-1.1.0' },
      source_trace: { handoff_archive_sha256: 'abc123' }
    }
  })

  assert.equal(result.summary.mentionRate, 50)
  assert.equal(result.summary.confidence.label, '中')
  assert.equal(result.samples[0].evidence[0].quote, '推荐 GEO Lab')
  assert.equal(result.samples[0].citations[0].domain, 'example.test')
  assert.equal(result.samples[0].competitorMentions[0].mentions, 2)
  assert.equal(result.methodology.externalModelUsed, false)
  assert.match(result.methodology.sampleSemantics, /多样本均值与离散度/)
  assert.equal(result.methodology.version, 'ai-visibility-python-port-1.1.0')
  assert.equal(result.methodology.sourceTrace.handoff_archive_sha256, 'abc123')
  assert.match(result.methodology.citationSemantics, /显式链接/)
})

test('single sample semantics and incomplete protocol stay explicit', () => {
  const result = normalizeVisibilityResult({
    summary: { sample_count: 1 },
    samples: [{ text: '合成回答' }],
    methodology: { sample_semantics: 'SINGLE_SAMPLE' }
  })

  assert.match(result.methodology.sampleSemantics, /单一样本/)
  assert.equal(result.summary.shareOfVoice, null)
  assert.equal(result.methodology.externalModelUsed, null)
  assert.ok(result.warnings.some(item => item.includes('缺少声量份额')))
  assert.ok(result.warnings.some(item => item.includes('外部模型')))
})

test('unsafe citation schemes never become clickable links', () => {
  assert.equal(toSafeVisibilityUrl('javascript:alert(1)'), '')
  assert.equal(toSafeVisibilityUrl('https://example.test/a'), 'https://example.test/a')
})
