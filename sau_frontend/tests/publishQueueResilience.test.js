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


test('job polling runs only for active jobs, is serial and has a hard bound', () => {
  assert.match(source, /const ACTIVE_JOB_STATUSES = new Set\(\['queued', 'processing'\]\)/)
  assert.match(source, /activeJobCount\.value === 0/)
  assert.match(source, /if \(pollTimer \|\| pollRequestInFlight\) return/)
  assert.match(source, /pollAttempts >= JOB_POLL_MAX_ATTEMPTS/)
  assert.match(source, /JOB_POLL_MAX_CONSECUTIVE_FAILURES = 3/)
  assert.match(source, /任务状态连续 3 次同步失败，已暂停自动刷新/)
  assert.match(source, /await fetchJobs\(true\)/)
  assert.match(source, /scheduleJobPoll\(\)/)
  assert.match(source, /stopJobPoll\(\{ resetWindow: true \}\)/)
  assert.match(source, /jobsRequestSerial \+= 1/)
  assert.match(source, /stopJobPoll\(\)/)
  assert.doesNotMatch(source, /window\.setInterval/)
})


test('batch execution isolates failures and always reports each prepared item', () => {
  assert.match(source, /for \(const job of preparedJobs\)/)
  assert.match(source, /status: 'request_error'/)
  assert.match(source, /finally \{\s*markJobBusy\(job\.job_id, false\)/)
  assert.match(source, /const outcomeLines = outcomes\.map/)
  assert.match(source, /不会阻塞后续，也不会自动重试同一任务/)
})


test('verification, account failure and unknown platform state show concrete next steps', () => {
  assert.match(source, /class="job-next-step"/)
  assert.match(source, /短信\|验证码\|扫码\|滑块\|安全验证\|本人验证/)
  assert.match(source, /封禁\|封号\|账号异常\|登录失效\|离线\|cookie/)
  assert.match(source, /先到平台后台核对是否已产生内容/)
  assert.match(source, /软件会自动刷新终态；结果未知前不要重复提交/)
})
