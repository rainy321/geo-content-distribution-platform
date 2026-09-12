import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const source = readFileSync(
  new URL('../src/views/DistributionCenter.vue', import.meta.url),
  'utf8'
)


test('newer job refreshes win and execute responses update the visible row immediately', () => {
  assert.match(source, /const requestSerial = \+\+jobsRequestSerial/)
  assert.match(source, /requestSerial !== jobsRequestSerial \|\| pollingStopped/)
  assert.match(source, /const applyJobSnapshot = snapshot =>/)
  assert.match(source, /jobsRequestSerial \+= 1[\s\S]{0,240}jobs\.value = jobs\.value\.map/)
  assert.match(source, /applyJobSnapshot\(result\)/)
  assert.match(source, /jobs\.value = jobs\.value\.map/)
})


test('job polling tracks active submissions and durable review monitors without overlapping requests', () => {
  assert.match(source, /const ACTIVE_JOB_STATUSES = new Set\(\['queued', 'processing'\]\)/)
  assert.match(source, /const REVIEWING_JOB_STATES = new Set\(\['watching', 'checking'\]\)/)
  assert.match(source, /const pollableJobCount = computed\(\(\) => activeJobCount\.value \+ reviewingJobCount\.value\)/)
  assert.match(source, /pollableJobCount\.value === 0/)
  assert.match(source, /if \(pollTimer \|\| pollRequestInFlight\) return/)
  assert.match(source, /pollAttempts >= JOB_POLL_MAX_ATTEMPTS/)
  assert.match(source, /JOB_REVIEW_POLL_INTERVAL_MS = 30_000/)
  assert.match(source, /平台审核结果仍会继续同步/)
  assert.match(source, /平台审核结果将继续低频重试/)
  assert.match(source, /JOB_POLL_MAX_CONSECUTIVE_FAILURES = 3/)
  assert.match(source, /任务状态连续 3 次同步失败，已暂停自动刷新/)
  assert.match(source, /await fetchJobs\(true\)/)
  assert.match(source, /scheduleJobPoll\(\)/)
  assert.match(source, /stopJobPoll\(\{ resetWindow: true \}\)/)
  assert.match(source, /watch\(\[activeJobCount, reviewingJobCount\]/)
  assert.match(source, /jobsRequestSerial \+= 1/)
  assert.match(source, /stopJobPoll\(\)/)
  assert.doesNotMatch(source, /window\.setInterval/)
})


test('batch execution isolates failures and always reports each prepared item', () => {
  assert.match(source, /for \(const job of preparedJobs\)/)
  assert.match(source, /outcomes\.push\(await executeConfirmedJobWithSafeRepair\(job\)\)/)
  assert.match(source, /const latestResponse = await publishApi\.getJob\(job\.job_id\)/)
  assert.match(source, /已按刷新后的持久化任务状态归类/)
  assert.doesNotMatch(source, /status: 'request_error'/)
  assert.match(source, /finally \{\s*markJobBusy\(job\.job_id, false\)/)
  assert.match(source, /const outcomeLines = outcomes\.map/)
  assert.match(source, /summarizePublishOutcomes\(outcomes\)/)
  assert.match(source, /分母仅含本次强确认后实际请求的/)
  assert.match(source, /明确记录为“尚未提交”的页面或浏览器故障会自动修复并且只重试一次/)
  assert.match(source, /审核中、结果未知、封禁、登录失效或本人验证绝不自动重发/)
})


test('verification, account failure and unknown platform state show concrete next steps', () => {
  assert.match(source, /class="job-next-step"/)
  assert.match(source, /job\.demo \? `演示 \/ \$\{statusLabel\(job\.status\)\}` : jobOutcome\(job\)\.label/)
  assert.match(source, /短信\|验证码\|扫码\|滑块\|安全验证\|本人验证/)
  assert.match(source, /封禁\|封号\|账号异常\|登录失效\|离线\|cookie/)
  assert.match(source, /先到平台后台核对是否已产生内容/)
  assert.match(source, /软件会自动刷新终态；结果未知前不要重复提交/)
})


test('retry is gated by the latest backend outcome and an explicit validation confirmation', () => {
  assert.match(source, /const currentResponse = await publishApi\.getJob\(job\.job_id\)/)
  assert.match(source, /!outcome\.retry_allowed \|\| outcome\.reconciliation_required/)
  assert.match(source, /无法校验任务最新状态，已阻止重试/)
  assert.match(source, /已修复并核对未发布，重新排队/)
  assert.match(source, /已完成验证并核对未发布，重新排队/)
  assert.match(source, /jobOutcome\(job\)\.action === 'fix_and_rebuild'/)
})


test('safe pre-submit failures use one bounded automatic repair path', () => {
  assert.match(source, /const executeConfirmedJobWithSafeRepair = async job =>/)
  assert.match(source, /!outcome\.auto_repair_eligible/)
  assert.match(source, /!outcome\.automatic_retry_allowed/)
  assert.match(source, /await publishApi\.autoRepairJob\(first\.snapshot\)/)
  assert.match(source, /const second = await executeConfirmedJobRequest\(repairedJob\)/)
  assert.match(source, /autoRepairMaxAttempts: 1/)
  assert.match(source, /已停止继续循环/)
  assert.doesNotMatch(source, /while\s*\([^)]*autoRepair/i)
})
