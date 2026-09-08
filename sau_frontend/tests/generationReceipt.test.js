import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import {
  normalizeGenerationReceipt,
  normalizeGenerationSources,
  toSafeHttpUrl
} from '../src/utils/generationReceipt.js'
import { buildGeoScorePayload } from '../src/utils/generationScore.js'


test('generation response keeps sources in the visible receipt model', () => {
  const receipt = normalizeGenerationReceipt(
    {
      run_id: 17,
      engine: 'mock',
      engine_version: 'test-v1',
      warnings: []
    },
    {
      summary: '摘要',
      tags: ['GEO'],
      content: '## FAQ\n问：如何核验？\n答：查看来源。',
      sources: [
        { title: '产品白皮书', url: 'https://example.test/white-paper' }
      ]
    }
  )

  assert.equal(receipt.runId, 17)
  assert.deepEqual(receipt.sources, [
    { title: '产品白皮书', url: 'https://example.test/white-paper' }
  ])
  assert.equal(receipt.warnings.includes('生成结果未提供可核验来源。'), false)
})


test('saved article receipt restores sources from the public run result', () => {
  const receipt = normalizeGenerationReceipt(
    {
      id: 42,
      actual_engine: 'colleague',
      engine_version: '2026.09',
      result: {
        summary: '运行快照摘要',
        tags: ['GEO'],
        content: '## 常见问题\n问：来源在哪里？\n答：见回执。',
        sources: [
          { name: '运行记录来源', href: 'https://example.test/run-source' }
        ]
      }
    },
    { generation_run_id: 42 }
  )

  assert.equal(receipt.runId, 42)
  assert.equal(receipt.engine, 'colleague')
  assert.deepEqual(receipt.sources, [
    { title: '运行记录来源', url: 'https://example.test/run-source' }
  ])
})


test('unsafe source URLs remain readable but cannot become links', () => {
  assert.equal(toSafeHttpUrl('javascript:alert(1)'), '')
  assert.deepEqual(
    normalizeGenerationSources([
      { title: '待人工核验来源', url: 'javascript:alert(1)' },
      'https://example.test/reference'
    ]),
    [
      { title: '待人工核验来源', url: '' },
      {
        title: 'https://example.test/reference',
        url: 'https://example.test/reference'
      }
    ]
  )
})


test('component and API keep run restoration non-blocking', () => {
  const component = readFileSync(
    new URL('../src/views/ContentCreation.vue', import.meta.url),
    'utf8'
  )
  const api = readFileSync(new URL('../src/api/article.js', import.meta.url), 'utf8')

  assert.match(api, /getContentGenerationRun\(runId, config = \{\}\)/)
  assert.match(api, /\/api\/content-generation\/runs\/\$\{runId\}/)
  assert.match(component, /generationReceipt\.sources/)
  assert.match(
    component,
    /getContentGenerationRun\(\s*runId,\s*\{ suppressGlobalError: true \}\s*\)/
  )
  assert.match(component, /error\.response\?\.status === 404/)
})


test('GEO scoring uses the immutable run snapshot when a run id exists', () => {
  assert.deepEqual(
    buildGeoScorePayload({
      title: '生成后编辑的标题',
      content: '生成后编辑的正文',
      generationRunId: 73,
      brand: '后来改名的品牌',
      keywords: ['后来修改的关键词']
    }),
    {
      title: '生成后编辑的标题',
      content: '生成后编辑的正文',
      generation_run_id: 73
    }
  )
})


test('GEO scoring uses current brand and keywords when there is no run', () => {
  const keywords = ['当前关键词']
  const payload = buildGeoScorePayload({
    title: '手工稿标题',
    content: '手工稿正文',
    generationRunId: null,
    brand: '当前品牌',
    keywords
  })

  assert.deepEqual(payload, {
    title: '手工稿标题',
    content: '手工稿正文',
    brand: '当前品牌',
    keywords: ['当前关键词']
  })
  assert.notEqual(payload.keywords, keywords)
})
