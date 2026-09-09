import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import {
  countPublishCharacters,
  frequencyGuidanceLabel,
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
  assert.match(source, /publish_title: platformDrafts\[platform\]\?\.editable_title/)
  assert.match(source, /publish_content: platformDrafts\[platform\]\?\.editable_content/)
  assert.match(source, /发布前检查 · 可能需要本人验证/)
  assert.match(source, /任务已暂停 · 需要本人验证/)
  assert.match(source, /不是平台官方配额/)
  assert.match(apiSource, /\/api\/publish\/preflight/)
})
