import assert from 'node:assert/strict'
import test from 'node:test'

import {
  formatPublishOutcomeSummary,
  normalizePublishOutcome,
  summarizePublishOutcomes
} from '../src/utils/publishOutcome.js'


test('batch outcomes always fall into four mutually exclusive categories', () => {
  const outcomes = [
    normalizePublishOutcome({ job_id: 1, status: 'success' }),
    normalizePublishOutcome({ job_id: 2, status: 'processing' }),
    normalizePublishOutcome({
      job_id: 3,
      status: 'need_action',
      reconciliation_required: true,
      message: '今日头条已接受文章提交，正在等待平台审核或公开链接'
    }),
    normalizePublishOutcome({ job_id: 4, status: 'need_action' }),
    normalizePublishOutcome({ job_id: 5, status: 'failed' })
  ]
  const summary = summarizePublishOutcomes(outcomes)

  assert.deepEqual(summary, {
    total: 5,
    counts: {
      published: 1,
      platform_processing: 2,
      action_required: 1,
      failed: 1
    }
  })
  assert.equal(Object.values(summary.counts).reduce((sum, count) => sum + count, 0), 5)
})


test('timeout or missing receipt requires action even when reconciliation is set', () => {
  const outcome = normalizePublishOutcome({
    job_id: 6,
    status: 'need_action',
    reconciliation_required: true,
    message: '发布操作超时，平台最终状态未知'
  })

  assert.equal(outcome.category, 'action_required')
  assert.equal(outcome.retry_allowed, false)
  assert.match(outcome.next_action, /核验平台结果/)
  assert.match(outcome.next_action, /禁止重试/)

  const noReceipt = normalizePublishOutcome({
    job_id: 7,
    status: 'need_action',
    reconciliation_required: true,
    message: '已观察到搜狐号文章写入请求，但未取得明确发布结果'
  })
  assert.equal(noReceipt.category, 'action_required')
  assert.equal(noReceipt.retry_allowed, false)
})


test('backend classification wins over confusing raw need_action status', () => {
  const outcome = normalizePublishOutcome({
    job_id: 8,
    status: 'need_action',
    message: '等待核验',
    outcome: {
      category: 'platform_processing',
      reason: '平台已接受提交，公开结果尚未确认。',
      next_action: '先核验平台后台，禁止重试。',
      retry_allowed: false,
      validation_required: true,
      reconciliation_required: true
    }
  })

  assert.equal(outcome.category, 'platform_processing')
  assert.equal(outcome.label, '平台处理中/审核中')
  assert.equal(outcome.retry_allowed, false)
  assert.equal(outcome.reconciliation_required, true)
  assert.match(outcome.next_action, /禁止重试/)
})


test('transport timeout is a note on latest persisted state, not a fifth category', () => {
  const outcome = normalizePublishOutcome(
    {
      job_id: 11,
      status: 'success',
      outcome: {
        category: 'published',
        reason: '平台已返回公开链接。',
        next_action: '复核公开内容。',
        retry_allowed: false
      }
    },
    { transportNote: '执行请求曾超时，已按刷新后的任务状态归类。' }
  )

  assert.equal(outcome.category, 'published')
  assert.match(outcome.transport_note, /曾超时/)
  assert.equal(summarizePublishOutcomes([outcome]).counts.published, 1)
})


test('summary names only the four user-facing categories and includes an exact total', () => {
  const summary = summarizePublishOutcomes([
    { category: 'published' },
    { category: 'published' },
    { category: 'platform_processing' },
    { category: 'action_required' },
    { category: 'failed' }
  ])
  const label = formatPublishOutcomeSummary(summary)

  assert.equal(
    label,
    '已公开发布 2；平台处理中/审核中 1；需要人工处理 1；明确失败 1（合计 5）'
  )
  assert.doesNotMatch(label, /请求异常|已提交待核验|结果未知/)
})
