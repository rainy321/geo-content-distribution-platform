import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const distributionSource = readFileSync(
  new URL('../src/views/DistributionCenter.vue', import.meta.url),
  'utf8'
)
const publishApiSource = readFileSync(
  new URL('../src/api/publish.js', import.meta.url),
  'utf8'
)


test('visible queued real jobs can be selected and executed as one confirmed sequence', () => {
  assert.match(distributionSource, /选择本页待发/)
  assert.match(distributionSource, /连续发布 \{\{ batchJobsToRun\.length \}\} 项/)
  assert.match(distributionSource, /const confirmationPhrase = `批量发布 \$\{preparedJobs\.length\} 项`/)
  assert.match(distributionSource, /for \(const job of preparedJobs\)/)
  assert.match(distributionSource, /await publishApi\.executeRealJob\(job\)/)
  assert.match(distributionSource, /normalizePublishOutcome\(latest \|\| \{\}/)
  assert.doesNotMatch(distributionSource, /status: 'request_error'/)
  assert.match(distributionSource, /formatPublishOutcomeSummary\(batchSummary\)/)
  assert.match(distributionSource, /不包含页面上的其他历史任务/)
  assert.match(distributionSource, /await fetchJobs\(true\)/)
  assert.match(distributionSource, /明确记录为“尚未提交”的页面或浏览器故障会自动修复并且只重试一次/)
  assert.match(distributionSource, /审核中、结果未知、封禁、登录失效或本人验证绝不自动重发/)
  assert.match(publishApiSource, /\/api\/publish\/jobs\/\$\{job\.job_id\}\/auto-repair/)
  assert.match(publishApiSource, /confirm_pre_submit: true/)
})


test('ambiguous jobs expose an explicit non-publishing reconciliation flow', () => {
  assert.match(distributionSource, /v-if="job\.reconciliation_required"/)
  assert.match(distributionSource, /current\?\.status !== 'need_action' \|\| !current\?\.reconciliation_required/)
  assert.doesNotMatch(distributionSource, /\['processing', 'need_action'\]\.includes\(job\.status\)/)
  assert.match(distributionSource, /核验平台结果/)
  assert.match(distributionSource, /只收口本地状态，不会再次点击发布/)
  assert.match(distributionSource, /publishApi\.reconcileJob\(job/)
  assert.match(distributionSource, /platformChecked/)
  assert.match(distributionSource, /executionStoppedChecked/)
  assert.match(distributionSource, /等待至少 5 分钟/)
  assert.match(distributionSource, /后台确认已发布/)
  assert.match(distributionSource, /后台确认未发布/)

  assert.match(publishApiSource, /\/api\/publish\/jobs\/\$\{job\.job_id\}\/reconcile/)
  assert.match(publishApiSource, /platform_checked: true/)
  assert.match(publishApiSource, /execution_stopped_confirmed:/)
  assert.match(publishApiSource, /state_version: job\.state_version/)
  assert.match(publishApiSource, /verification_note: reconciliation\.verification_note/)
})
