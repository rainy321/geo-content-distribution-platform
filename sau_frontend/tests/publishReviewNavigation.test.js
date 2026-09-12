import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const appSource = readFileSync(
  new URL('../src/App.vue', import.meta.url),
  'utf8'
)
const distributionSource = readFileSync(
  new URL('../src/views/DistributionCenter.vue', import.meta.url),
  'utf8'
)

test('global review alerts use a view-only focus route instead of the rebuild route', () => {
  const destination = appSource.match(/const openAlertDestination = alert => \{[\s\S]*?\n\}/)?.[0] || ''
  assert.match(appSource, /focus_account_id: String\(alert\.account_id\)/)
  assert.match(destination, /focus_job_id: String\(alert\.publish_job_id\)/)
  assert.doesNotMatch(destination, /\{ job_id:/)
})

test('focus_job_id locates and highlights an existing job on initial and same-page navigation', () => {
  assert.match(distributionSource, /await focusExistingJob\(route\.query\.focus_job_id\)/)
  assert.match(distributionSource, /watch\(\s*\(\) => route\.query\.focus_job_id/)
  assert.match(distributionSource, /document\.getElementById\(`publish-job-\$\{jobId\}`\)\?\.scrollIntoView/)
  assert.match(distributionSource, /'is-focused': Number\(job\.job_id\) === focusedJobId/)
  assert.match(distributionSource, /\.job-row[\s\S]*?&\.is-focused[\s\S]*?box-shadow:/)
})

test('a focused job outside the current page is fetched read-only and retained across polls', () => {
  assert.match(distributionSource, /focusId && !listedJobs\.some\(job => Number\(job\.job_id\) === focusId\)/)
  assert.match(distributionSource, /const focusedResponse = await publishApi\.getJob\(focusId\)/)
  assert.match(distributionSource, /focusedSnapshot = jobs\.value\.find\(job => Number\(job\.job_id\) === focusId\) \|\| null/)
  assert.match(distributionSource, /\? \[\{ \.\.\.focusedSnapshot, job_id: focusId \}, \.\.\.listedJobs\]/)
})

test('focus handling never selects old content or enters task creation', () => {
  const focusHandler = distributionSource.match(/const focusExistingJob = async rawJobId => \{[\s\S]*?\n\}/)?.[0] || ''
  assert.match(focusHandler, /focusedJobId\.value = jobId/)
  assert.doesNotMatch(focusHandler, /selectedArticleId|selectedArticleIds|selectedPlatforms|createJob|dispatch/)
})
