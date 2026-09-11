import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const source = readFileSync(
  new URL('../src/views/DistributionCenter.vue', import.meta.url),
  'utf8'
)


test('the sticky command bar exposes readiness, blockers and the primary publish action', () => {
  assert.match(source, /id="publish-command-bar" class="publish-command-bar" aria-label="发布控制台"/)
  assert.match(source, /<span>阻塞项<\/span><strong>\{\{ dispatchBlockers\.length \}\}<\/strong>/)
  assert.match(source, /:content="dispatchBlockerSummary"/)
  assert.match(source, /class="command-blocker-copy"[\s\S]{0,180}@click="focusFirstBlocker"/)
  assert.match(source, /const focusFirstBlocker = \(\) => \{\s*focusDispatchSection\(dispatchBlockers\.value\[0\]\?\.section \|\| 'top'\)/)
  assert.match(source, /element\?\.scrollIntoView\(\{ behavior: 'smooth', block: 'center' \}\)/)
  assert.match(source, /if \(!canDispatch\.value\) \{\s*ElMessage\.warning\(dispatchBlockerSummary\.value\)\s*focusFirstBlocker\(\)/)
})


test('blockers cover content, account, assets, scheduling and in-flight work', () => {
  assert.match(source, /add\('article', '请选择至少一篇就绪稿件', 'article'\)/)
  assert.match(source, /add\('platform', '请选择至少一个发布渠道', 'platform'\)/)
  assert.match(source, /add\('content_rules', '平台短版、离线规则或发布硬门禁尚未通过', 'platform'\)/)
  assert.match(source, /add\('account', '请补齐或明确选择每个平台的可用账号', 'account'\)/)
  assert.match(source, /add\('image', '所选渠道缺少图片素材', 'image'\)/)
  assert.match(source, /add\('video', '所选渠道缺少视频素材', 'video'\)/)
  assert.match(source, /add\('schedule', '请选择计划发布时间', 'schedule'\)/)
  assert.match(source, /if \(batchExecuting\.value\) add\('batch_executing', '已有真实发布批次正在准备或执行，请等待本批次结束', 'top'\)/)
  assert.match(source, /add\('dispatching', '任务正在创建，请等待本批次完成', 'top'\)/)
})


test('up to twenty ready articles create independent per-article and per-platform tasks', () => {
  assert.match(source, /const MAX_BATCH_ARTICLES = 20/)
  assert.match(source, /v-model="selectedArticleIds"\s+multiple/)
  assert.match(source, /最多一次选择 \{\{ MAX_BATCH_ARTICLES \}\} 篇/)
  assert.match(source, /\.slice\(0, MAX_BATCH_ARTICLES\)/)
  assert.match(source, /const resolveArticleDispatchDrafts = async \(articleId, platformKeys = selectedPlatforms\.value\) =>/)
  assert.match(source, /const preflightDispatchBatch = async \(articleIds, platformKeys\) =>/)
  assert.match(source, /for \(const articleId of articleIds\)[\s\S]{0,300}const drafts = draftCache\.get\(Number\(articleId\)\)[\s\S]{0,100}for \(const platform of platformKeys\)/)
  assert.match(source, /article_id: articleId/)
  assert.match(source, /publish_title: drafts\[platform\]\?\.editable_title/)
  assert.match(source, /publish_content: drafts\[platform\]\?\.editable_content/)
})


test('automatic setup never chooses among multiple accounts or rotates an account after failure', () => {
  assert.match(source, /if \(!current && accounts\.length === 1\) \{\s*selectedAccountIds\[platform\.key\] = accounts\.at\(0\)\?\.id \|\| null\s*\}/)
  assert.match(source, /connectedAccounts\(platform\.key\)\.length > 1 && !selectedAccountFor\(platform\.key\)/)
  assert.match(source, /存在多个账号的平台仍需由你明确选择目标账号/)
  assert.match(source, /:label="accountDisplayName\(account\)"/)
  assert.match(source, /const accountDisplayName = account => `\$\{account\.account_name\} · ID \$\{account\.id\}`/)
  assert.doesNotMatch(source, /account(?:Ids?|s)?\.(?:shift|pop|reverse)\(/i)
  assert.doesNotMatch(source, /rotateAccount|nextAccount|fallbackAccount/i)
})


test('confirmed real jobs execute serially and only safe pre-submit failures get one retry', () => {
  assert.match(source, /if \(!listedJobs\.length\)[\s\S]{0,180}batchExecuting\.value = true[\s\S]{0,180}const accountsLoaded = await fetchAccounts\(\)/)
  assert.match(source, /for \(const job of preparedJobs\) \{[\s\S]{0,260}executeConfirmedJobWithSafeRepair\(job\)/)
  assert.doesNotMatch(source, /Promise\.all\([^)]*executeRealJob/s)
  assert.match(source, /明确记录为“尚未提交”的页面或浏览器故障会自动修复并且只重试一次/)
  assert.match(source, /审核中、结果未知、封禁、登录失效或本人验证绝不自动重发/)
  assert.match(source, /await publishApi\.autoRepairJob\(first\.snapshot\)/)
  assert.match(source, /autoRepairMaxAttempts: 1/)
  assert.match(source, /await fetchJobs\(true\)/)
})


test('one batch confirmation creates and immediately executes every selected real route', () => {
  assert.match(source, /return `确认并立即发布 \$\{dispatchTaskCount\.value\} 项`/)
  assert.match(source, /const executeImmediately = Boolean\([\s\S]{0,140}!scheduleEnabled\.value/)
  assert.match(source, /const createdRealJobs = \[\]/)
  assert.match(source, /createdRealJobs\.push\(job\)[\s\S]{0,260}mergePublishBatchCheckpoint/)
  assert.match(source, /await executeRealBatch\(\{\s*jobsOverride: createdRealJobs,\s*skipConfirmation: true,\s*checkpointBatchId: recoveryBatchId\s*\}\)/)
  assert.match(source, /if \(!skipConfirmation\) \{[\s\S]{0,1500}ElMessageBox\.prompt/)
  assert.match(source, /recoverInterruptedPublishBatch/)
  assert.match(source, /已提交、处理中、待核验或成功的任务不会重发/)
})


test('the full article-by-platform matrix is preflighted and cached before final confirmation', () => {
  const start = source.indexOf('const dispatchArticle = async () =>')
  const end = source.indexOf('const executeDemoJob = async', start)
  const dispatchSource = source.slice(start, end)
  const preflightIndex = dispatchSource.indexOf('await preflightDispatchBatch(')
  const confirmIndex = dispatchSource.indexOf('await ElMessageBox.confirm(')

  assert.ok(preflightIndex >= 0)
  assert.ok(confirmIndex > preflightIndex)
  assert.match(dispatchSource, /if \(preflightFailures\.length\) \{[\s\S]{0,900}\u672c\u6279\u6ca1\u6709\u521b\u5efa\u4efb\u4f55\u4efb\u52a1/)
  assert.match(dispatchSource, /const drafts = draftCache\.get\(Number\(articleId\)\)/)
  assert.doesNotMatch(dispatchSource.slice(confirmIndex), /resolveArticleDispatchDrafts|previewVariants/)
})


test('one-click repair previews model output before saving and never overwrites unresolved gates', () => {
  assert.match(source, /@click="autoRepairSelectedArticles"/)
  assert.match(source, /const autoRepairSelectedArticles = async \(\) =>/)
  assert.match(source, /const optimization = await articleApi\.optimizeArticle\(articleId\)/)
  assert.match(source, /const candidateVariants = await previewArticleVariants\(articleId, optimized\)/)
  assert.match(source, /if \(candidateBlocked \|\| \(!clearedHardGate && candidateMinimum <= currentMinimum\)\)/)
  assert.match(source, /await articleApi\.updateArticle\(articleId/)
  assert.match(source, /await refreshPublishPreflight\(\{ preserveEdits: false \}\)/)
  assert.match(source, /软件不会伪造链接或数据/)
})


test('manual reconciliation prioritizes a verified target work then offers management', () => {
  const targetButton = source.indexOf('打开目标作品')
  const managementButton = source.indexOf('打开{{ reconciliationDestination.label }}', targetButton)
  assert.ok(targetButton >= 0)
  assert.ok(managementButton > targetButton)
  assert.match(source, /const openReconciliationDestination = \(\) =>/)
  assert.match(source, /isSafeExternalVerificationUrl\(destination\.url, platform\)/)
  assert.match(source, /isSafePublishedContentUrl\(url, platform\)/)
  assert.match(source, /创作者管理页不能作为已发布证据/)
  assert.match(source, /核验账号：\{\{ reconciliationAccountLabel \}\}/)
})


test('every channel card distinguishes browser publishing from billable AI work', () => {
  assert.match(source, /浏览器发布 Token 不适用 · 本地规则适配不产生 AI 费用/)
  assert.match(source, /AI 改写费用见成本账单/)
})


test('automatic cover discloses and opens the independently configurable IMAGE role', () => {
  assert.match(source, /如需调用外部 IMAGE 模型，请先点“生成封面”/)
  assert.match(source, /调用与成本均会留痕/)
  assert.match(source, /confirm_external_call: true/)
  assert.match(source, /Idempotency-Key/)
  assert.match(source, /router\.push\(\{ path: '\/settings', query: \{ role: 'image' \} \}\)/)
})
