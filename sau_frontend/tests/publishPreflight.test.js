import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import {
  countPublishCharacters,
  frequencyGuidanceLabel,
  isPublishVariantReady,
  refreshEditableVariant
} from '../src/utils/publishPreflight.js'


test('platform draft counts Unicode code points and enforces the server safe line', () => {
  assert.equal(countPublishCharacters('中文😀'), 3)
  const valid = refreshEditableVariant({
    title: '标题',
    publish_content: '短版😀',
    content_policy: { safe_limit: 4 }
  })
  const invalid = refreshEditableVariant({
    title: '标题',
    editable_content: '超过安全线',
    content_policy: { safe_limit: 4 }
  })

  assert.equal(valid.publish_length, 3)
  assert.equal(valid.within_safe_limit, true)
  assert.equal(invalid.within_safe_limit, false)
})


test('platform draft validates the visible title snapshot independently', () => {
  const valid = refreshEditableVariant({
    title: '三十字内标题',
    publish_content: '正文',
    title_policy: { min_length: 1, safe_limit: 30 }
  })
  const invalid = refreshEditableVariant({
    editable_title: '字'.repeat(31),
    publish_content: '正文',
    title_policy: { min_length: 1, safe_limit: 30 }
  })

  assert.equal(valid.title_within_safe_limit, true)
  assert.equal(invalid.title_within_safe_limit, false)
})


test('citeability receipt is valid only for the exact visible outbound snapshot', () => {
  const scored = refreshEditableVariant({
    title: 'DongClaw 如何部署',
    publish_content: '北京同于野科技有限公司提供部署步骤与限制。',
    citeability: {
      score: 82,
      pass_score: 75,
      passed: true,
      blocking_issues: [],
      suggestions: ['补充来源']
    }
  })
  const edited = refreshEditableVariant({
    ...scored,
    editable_content: `${scored.editable_content}新增一句`
  })

  assert.equal(scored.citeability_current, true)
  assert.equal(scored.citeability_passed, true)
  assert.equal(scored.citeability_publish_gate_passed, true)
  assert.equal(scored.citeability_score, 82)
  assert.equal(edited.citeability_current, false)
  assert.equal(edited.citeability_passed, false)
  assert.equal(edited.citeability_publish_gate_passed, false)
  assert.deepEqual(edited.citeability_suggestions, ['补充来源'])
})


test('quality score below 75 remains publishable when the hard gate has no P0 issue', () => {
  const draft = refreshEditableVariant({
    title: 'DongClaw 部署说明',
    publish_content: '北京同于野科技有限公司提供适用范围与可核验来源。',
    citeability: {
      score: 71,
      pass_score: 75,
      passed: false,
      publish_gate_passed: true,
      blocking_issues: [],
      suggestions: ['补充一项独立来源。']
    }
  })

  assert.equal(draft.citeability_passed, false)
  assert.equal(draft.citeability_publish_gate_passed, true)
  assert.equal(isPublishVariantReady(draft), true)
})


test('a P0 citeability issue blocks task creation even if the score is high', () => {
  const draft = refreshEditableVariant({
    title: 'DongClaw 部署说明',
    publish_content: '正文包含尚未证实的绝对化主张。',
    citeability: {
      score: 92,
      pass_score: 75,
      passed: true,
      publish_gate_passed: true,
      blocking_issues: [{
        code: 'unsupported-claim',
        priority: 'P0',
        message: '绝对化主张缺少证据。'
      }]
    }
  })

  assert.equal(draft.citeability_publish_gate_passed, false)
  assert.equal(isPublishVariantReady(draft), false)
})


test('offline preflight is bound to the exact visible platform copy', () => {
  const checked = refreshEditableVariant({
    title: '企业内容发布说明',
    publish_content: '中性正文。',
    offline_preflight: {
      passed: true,
      issues: [{ code: 'sohu_no_public_source_url', severity: 'warning', message: '缺少公开来源' }],
      disclaimer: '软件不保证审核通过。'
    }
  })
  const edited = refreshEditableVariant({
    ...checked,
    editable_content: `${checked.editable_content}待补充来源`
  })

  assert.equal(checked.offline_preflight_current, true)
  assert.equal(checked.offline_preflight_passed, true)
  assert.equal(checked.offline_preflight_issues.length, 1)
  assert.equal(edited.offline_preflight_current, false)
  assert.equal(edited.offline_preflight_passed, false)
})


test('frequency label is explicitly a software recommendation', () => {
  assert.equal(
    frequencyGuidanceLabel({ daily_recommended_max: 3, cooldown_minutes: 120 }),
    '软件建议 ≤3 条/日 · 间隔 ≥120 分钟'
  )
})


test('distribution center previews per-platform copies and warns about human verification', () => {
  const source = readFileSync(
    new URL('../src/views/DistributionCenter.vue', import.meta.url),
    'utf8'
  )
  const apiSource = readFileSync(new URL('../src/api/publish.js', import.meta.url), 'utf8')

  assert.match(source, /平台短版预检/)
  assert.match(source, /原稿始终保留在内容库/)
  assert.match(source, /恢复自动短版/)
  assert.match(source, /载入原稿编辑/)
  assert.match(source, /发布标题/)
  assert.match(source, /publish_title: drafts\[platform\]\?\.editable_title/)
  assert.match(source, /publish_content: drafts\[platform\]\?\.editable_content/)
  assert.match(source, /发布前检查 · 可能需要本人验证/)
  assert.match(source, /任务已暂停 · 需要本人验证/)
  assert.match(source, /不是平台官方配额/)
  assert.match(source, /AI 可引用性/)
  assert.match(source, /平台离线预检/)
  assert.match(source, /offline_preflight_passed/)
  assert.match(source, /offline_preflight_disclaimer/)
  assert.match(source, /title_overrides/)
  assert.match(source, /citeability_current/)
  assert.match(source, /isPublishVariantReady/)
  assert.match(source, /citeability_publish_gate_passed/)
  assert.match(source, /可发布，建议先优化/)
  assert.match(source, /发布硬门禁未通过/)
  assert.match(source, /内容编辑后正在重新检查/)
  assert.match(apiSource, /\/api\/publish\/preflight/)
})
