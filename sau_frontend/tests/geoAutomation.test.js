import test from 'node:test'
import { readFileSync } from 'node:fs'
import assert from 'node:assert/strict'

import {
  automationErrorEvidence,
  automationRunCounts,
  automationStageState,
  buildAutomationAuthorization,
  orderAutomationPublishJobs,
  parseAutomationTopics,
  publishJobEvidence
} from '../src/utils/geoAutomation.js'

const appSource = readFileSync(new URL('../src/App.vue', import.meta.url), 'utf8')
const projectSource = readFileSync(
  new URL('../src/views/ProjectManagement.vue', import.meta.url),
  'utf8'
)
const automationSource = readFileSync(
  new URL('../src/views/GeoAutomation.vue', import.meta.url),
  'utf8'
)

test('automation stays alive across route switches and projects link into scoped setup', () => {
  assert.match(appSource, /<KeepAlive include="AiVisibility,ContentCreation,GeoAutomation">/)
  assert.match(projectSource, /router\.push\(\{ path: '\/geo-automation', query: \{ project_id:/)
  assert.match(automationSource, /watch\([\s\S]*route\.fullPath[\s\S]*route\.path !== '\/geo-automation'/)
  assert.match(automationSource, /if \(processing\.value && currentRun\.value\)/)
})

test('one item failure is isolated from the remaining automation queue', () => {
  assert.match(
    automationSource,
    /for \(const snapshot of currentRun\.value\.items \|\| \[\]\)[\s\S]*try \{[\s\S]*await processItem\(snapshot\)[\s\S]*catch \(error\)/
  )
  assert.match(automationSource, /结果无法确认，已停止自动重试/)
})

test('active run context cannot switch and model stages use fenced worker leases', () => {
  assert.match(automationSource, /let activeRunContext = null/)
  assert.match(automationSource, /运行上下文已变化，已停止写入以避免串批/)
  assert.match(automationSource, /:disabled="starting \|\| processing" @click="openRun\(run\.id\)"/)
  assert.match(automationSource, /optimization_attempt_started: true/)
  assert.match(automationSource, /await acquireItemLease\(item, 'generating'\)/)
  assert.match(automationSource, /await acquireItemLease\(item, 'optimizing'\)/)
  assert.match(automationSource, /automation_context:[\s\S]*worker_id: workerId/)
  assert.match(automationSource, /automation_item_busy[\s\S]*automation_paused[\s\S]*generation_in_progress/)
  assert.match(automationSource, /if \(latest && !TERMINAL_AUTOMATION_STATUSES\.has\(latest\.status\)\) break/)
  assert.doesNotMatch(
    automationSource,
    /sourceItem\.result\?\.optimization_attempt_started === true/
  )
})

test('automation protects manual article edits from asynchronous overwrite', () => {
  assert.match(automationSource, /editableArticleSignature\(latestArticle\) !== sourceSignature/)
  assert.match(automationSource, /article_changed_during_optimization/)
  assert.match(automationSource, /article_changed_during_preflight/)
  assert.match(automationSource, /运行中的文章已锁定/)
})

test('publishing recovery uses frozen jobs before considering another preflight', () => {
  assert.match(
    automationSource,
    /publishApi\.getJobs\(\{ automation_item_id: item\.id, page_size: 100 \}\)/
  )
  assert.match(
    automationSource,
    /const recoveredJobs = await recoverAutomationJobs\(item\)[\s\S]*if \(allJobsRecovered\) \{[\s\S]*await executeAutomationJobs\(item, recoveredEvidence\)[\s\S]*return[\s\S]*articleApi\.getArticle/
  )
  assert.match(automationSource, /incomplete_publish_job_set/)
  assert.match(automationSource, /未重新预检或补建缺失任务/)
})

test('automation topics preserve unique order and expose over-limit input', () => {
  const topics = parseAutomationTopics('主题一\n主题二\n主题一\n\n主题三')
  assert.deepEqual(topics, ['主题一', '主题二', '主题三'])
})

test('authorization binds exact project topic mode channel and account scope', () => {
  const project = {
    id: 9,
    name: '品牌 A',
    website: 'https://example.test',
    product: '产品 A',
    industry: '软件',
    description: '项目事实',
    keywords: ['A'],
    competitors: [],
    updated_at: '2026-09-17 00:00:00'
  }
  const authorization = buildAutomationAuthorization({
    projectId: '9',
    project,
    topics: ['A', 'B'],
    publishMode: 'real',
    channels: [
      { platform: 'zhihu', account_id: '17', credential_epoch: '3' },
      { platform: 'toutiao', account_id: 22, credential_epoch: 5 }
    ]
  })
  assert.deepEqual(authorization, {
    confirmed: true,
    project_id: 9,
    topic_count: 2,
    publish_mode: 'real',
    channels: [
      { platform: 'zhihu', account_id: 17, credential_epoch: 3 },
      { platform: 'toutiao', account_id: 22, credential_epoch: 5 }
    ],
    project_snapshot: project
  })
})

test('real account selection freezes the current credential epoch', () => {
  assert.match(automationSource, /credential_epoch: Number\(account\?\.credential_epoch \|\| 0\) \|\| null/)
  assert.match(automationSource, /账号缺少凭证版本，请刷新账号列表后重选/)
})

test('switching execution modes removes unavailable channels and their stale account bindings', () => {
  assert.match(automationSource, /@click="setPublishMode\(mode\.value\)"/)
  assert.match(
    automationSource,
    /const pruneUnavailableChannels = \(\) => \{[\s\S]*form\.platforms = form\.platforms\.filter\(platformKey => selectable\.has\(platformKey\)\)[\s\S]*delete form\.account_ids\[platformKey\]/
  )
  assert.match(
    automationSource,
    /watch\([\s\S]*form\.publish_mode[\s\S]*platform\.runtime_available[\s\S]*pruneUnavailableChannels\(\)[\s\S]*flush: 'sync'/
  )
})

test('run counts separate success issues and unfinished work', () => {
  const counts = automationRunCounts({
    items: [
      { status: 'completed' },
      { status: 'need_action' },
      { status: 'failed' },
      { status: 'publishing' },
      { status: 'queued' }
    ]
  })
  assert.deepEqual(counts, {
    total: 5,
    completed: 1,
    issues: 2,
    pending: 2,
    processed: 3
  })
})

test('rail marks the failed stage without presenting later stages as complete', () => {
  const item = { status: 'need_action', stage: 'optimizing' }
  assert.equal(automationStageState(item, 'generating'), 'complete')
  assert.equal(automationStageState(item, 'optimizing'), 'issue')
  assert.equal(automationStageState(item, 'preflighting'), 'pending')
  assert.equal(automationStageState(item, 'publishing'), 'pending')
})

test('prepare completion keeps publishing explicitly not applicable', () => {
  const item = { status: 'completed', stage: 'preflighting' }
  assert.equal(automationStageState(item, 'preflighting', 'prepare'), 'complete')
  assert.equal(automationStageState(item, 'publishing', 'prepare'), 'not_applicable')
  assert.equal(automationStageState(item, 'publishing', 'real'), 'complete')
})

test('unknown external evidence stays explicit and publish ids are normalized', () => {
  const evidence = automationErrorEvidence({
    response: {
      data: {
        code: 504,
        msg: '模型请求结果未知',
        data: {
          error_code: 'generation_state_unknown',
          state_unknown: true,
          request_id: 'request-1'
        }
      }
    }
  })
  assert.equal(evidence.code, 'generation_state_unknown')
  assert.equal(evidence.state_unknown, true)
  assert.equal(evidence.request_id, 'request-1')
  assert.deepEqual(publishJobEvidence({
    id: 31,
    platform: 'zhihu',
    status: 'success',
    outcome: { category: 'published' }
  }), {
    platform: 'zhihu',
    job_id: 31,
    status: 'success',
    message: '',
    result_url: '',
    outcome_category: 'published'
  })
})

test('recovered publish jobs follow the authorized channel order', () => {
  const ordered = orderAutomationPublishJobs(
    [
      { id: 2, platform: 'toutiao', status: 'queued' },
      { id: 1, platform: 'zhihu', status: 'success' }
    ],
    [{ platform: 'zhihu' }, { platform: 'toutiao' }]
  )
  assert.equal(ordered.valid, true)
  assert.deepEqual(ordered.jobs.map(job => job.platform), ['zhihu', 'toutiao'])

  const unexpected = orderAutomationPublishJobs(
    [{ id: 3, platform: 'sohu', status: 'queued' }],
    [{ platform: 'zhihu' }]
  )
  assert.equal(unexpected.valid, false)
  assert.deepEqual(unexpected.jobs, [])
})
