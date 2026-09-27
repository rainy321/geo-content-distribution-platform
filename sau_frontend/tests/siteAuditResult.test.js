import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import {
  normalizeSiteAuditHistory,
  normalizeSiteAuditResult,
  safeSiteAuditUrl,
  siteAuditStatusPresentation
} from '../src/utils/siteAuditResult.js'

test('missing site-audit fields stay explicitly untested', () => {
  const result = normalizeSiteAuditResult({ code: 200, data: {} })

  assert.equal(result.score, null)
  assert.equal(result.grade, '未检测')
  assert.deepEqual(
    result.checks.map(item => [item.key, item.status]),
    [
      ['page_access', 'not_checked'],
      ['indexability', 'not_checked'],
      ['crawler_access', 'not_checked'],
      ['extractable_content', 'not_checked'],
      ['canonical', 'not_checked'],
      ['sitemap', 'not_checked'],
      ['structured_data', 'not_checked'],
      ['metadata', 'not_checked']
    ]
  )
  assert.equal(result.resources.every(item => item.status === 'not_checked'), true)
  assert.equal(siteAuditStatusPresentation(result.status).label, '未检测')
  assert.equal(siteAuditStatusPresentation('processing').label, '审计中')
  assert.equal(siteAuditStatusPresentation('outcome_unknown').label, '结果未知')
})

test('site-audit response becomes a stable frontend evidence model', () => {
  const result = normalizeSiteAuditResult({
    code: 201,
    data: {
      id: 17,
      project_id: 4,
      requested_url: 'https://example.test',
      final_url: 'https://www.example.test/home',
      status: 'ready',
      score: 82,
      grade: 'B',
      checks: [
        { key: 'homepage', label: '首页', status: 'passed', score: 25, max_score: 25, evidence: ['HTTP 200'] },
        { key: 'robots_txt', status: 'warning', evidence: [{ message: '未声明 GPTBot' }] },
        { key: 'schema', status: 'failed', score: 0, max_score: 20 }
      ],
      crawler_access: [
        { user_agent: 'GPTBot', label: 'OpenAI', purpose: '训练', allowed: false, source: 'robots.txt' },
        { user_agent: 'OAI-SearchBot', label: 'OpenAI Search', purpose: '搜索', allowed: true, source: 'robots.txt' }
      ],
      structured_data: { valid_blocks: 2, invalid_blocks: 1, types: ['Organization', 'Article'] },
      resources: {
        robots: { status: 'found', url: 'https://example.test/robots.txt' },
        sitemaps: ['https://example.test/sitemap.xml'],
        llms_txt: { status: 'absent', proposal_only: true, score_weight: 0 }
      },
      recommendations: [{ priority: 'P0', code: 'schema.invalid', title: '修复 JSON-LD', action: '移除无效块。' }],
      boundaries: ['不证明 AI 已收录。'],
      audit_version: 'site-audit-1.0.0',
      audited_at: '2026-09-16T10:30:00Z'
    }
  })

  assert.equal(result.id, 17)
  assert.equal(result.score, 82)
  assert.equal(result.checks.find(item => item.key === 'crawler_access').status, 'warning')
  assert.deepEqual(result.checks.find(item => item.key === 'crawler_access').evidence, ['未声明 GPTBot'])
  assert.equal(result.checks.find(item => item.key === 'structured_data').status, 'failed')
  assert.equal(result.crawlerAccess[0].allowed, false)
  assert.equal(result.crawlerAccess[1].allowed, true)
  assert.deepEqual(result.structuredData.types, ['Organization', 'Article'])
  assert.equal(result.resources.find(item => item.key === 'robots').status, 'passed')
  assert.equal(result.resources.find(item => item.key === 'sitemaps').items[0], 'https://example.test/sitemap.xml')
  assert.equal(result.resources.find(item => item.key === 'llms_txt').status, 'failed')
  assert.equal(result.resources.find(item => item.key === 'llms_txt').advisoryOnly, true)
  assert.equal(result.resources.find(item => item.key === 'llms_txt').scoreWeight, 0)
  assert.equal(result.recommendations[0].priority, 'P0')
  assert.equal(result.boundaries[0], '不证明 AI 已收录。')
  assert.equal(siteAuditStatusPresentation(result.status).label, '技术准备较好')
})

test('site-audit history tolerates lightweight wrappers and nested saved results', () => {
  const history = normalizeSiteAuditHistory({
    data: {
      runs: [{
        id: 21,
        project_id: 9,
        score: 67,
        grade: 'C',
        status: 'completed',
        requested_url: 'https://brand.example',
        audited_at: '2026-09-16T11:00:00Z'
      }]
    }
  })
  const restored = normalizeSiteAuditResult({
    data: {
      id: 21,
      result: {
        project_id: 9,
        score: 67,
        requested_url: 'https://brand.example'
      }
    }
  })

  assert.equal(history.length, 1)
  assert.equal(history[0].id, 21)
  assert.equal(history[0].score, 67)
  assert.equal(restored.id, 21)
  assert.equal(restored.projectId, 9)
})

test('only public HTTP URLs become clickable audit targets', () => {
  assert.equal(safeSiteAuditUrl('javascript:alert(1)'), '')
  assert.equal(safeSiteAuditUrl('file:///etc/passwd'), '')
  assert.equal(safeSiteAuditUrl('https://example.test/path'), 'https://example.test/path')
})

test('AI visibility mounts one site-audit panel after project selection and uses protected APIs', () => {
  const apiSource = readFileSync(new URL('../src/api/diagnostics.js', import.meta.url), 'utf8')
  const viewSource = readFileSync(new URL('../src/views/AiVisibility.vue', import.meta.url), 'utf8')
  const panelSource = readFileSync(new URL('../src/components/SiteAuditPanel.vue', import.meta.url), 'utf8')

  assert.match(apiSource, /http\.post\('\/api\/geo\/site-audits', data/)
  assert.match(apiSource, /'X-Request-ID': requestId/)
  assert.match(apiSource, /'Idempotency-Key': idempotencyKey/)
  assert.match(apiSource, /http\.get\('\/api\/geo\/site-audits\/runs'/)
  assert.match(apiSource, /\/api\/geo\/site-audits\/runs\/\$\{encodeURIComponent\(runId\)\}/)
  assert.match(viewSource, /<SiteAuditPanel/)
  assert.match(viewSource, /:website="selectedProject\?\.website \|\| ''"/)
  assert.match(panelSource, /不发送项目正文、Cookie 或 API Key/)
  assert.match(panelSource, /confirm_external_fetch: true/)
  assert.match(panelSource, /sameOriginAsSavedWebsite/)
  assert.match(panelSource, /requestId: `site-audit-ui-/)
  assert.match(panelSource, /idempotencyKey: `site-audit-/)
  assert.match(panelSource, /上一次成功结果仍保留在下方/)
  assert.match(panelSource, /auditError\.value = readableError\(error\)/)
  assert.doesNotMatch(panelSource, /catch \(error\) \{\s*auditResult\.value = null/s)
})
