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
  assert.match(source, /add\('content_rules', '平台短版、离线规则或 AI 可引用性尚未通过', 'platform'\)/)
  assert.match(source, /add\('account', '请补齐或明确选择每个平台的可用账号', 'account'\)/)
  assert.match(source, /add\('image', '所选渠道缺少图片素材', 'image'\)/)
  assert.match(source, /add\('video', '所选渠道缺少视频素材', 'video'\)/)
  assert.match(source, /add\('schedule', '请选择计划发布时间', 'schedule'\)/)
  assert.match(source, /add\('dispatching', '任务正在创建，请等待本批次完成', 'top'\)/)
})


test('up to twenty ready articles create independent per-article and per-platform tasks', () => {
  assert.match(source, /const MAX_BATCH_ARTICLES = 20/)
  assert.match(source, /v-model="selectedArticleIds"\s+multiple/)
  assert.match(source, /最多一次选择 \{\{ MAX_BATCH_ARTICLES \}\} 篇/)
  assert.match(source, /\.slice\(0, MAX_BATCH_ARTICLES\)/)
  assert.match(source, /const resolveArticleDispatchDrafts = async articleId =>/)
  assert.match(source, /for \(const articleId of selectedArticleIds\.value\)[\s\S]{0,700}drafts = await resolveArticleDispatchDrafts\(articleId\)[\s\S]{0,300}for \(const platform of selectedPlatforms\.value\)/)
  assert.match(source, /article_id: articleId/)
  assert.match(source, /publish_title: drafts\[platform\]\?\.editable_title/)
  assert.match(source, /publish_content: drafts\[platform\]\?\.editable_content/)
})


test('automatic setup never chooses among multiple accounts or rotates an account after failure', () => {
  assert.match(source, /if \(!current && accounts\.length === 1\) \{\s*selectedAccountIds\[platform\.key\] = accounts\.at\(0\)\?\.id \|\| null\s*\}/)
  assert.match(source, /connectedAccounts\(platform\.key\)\.length > 1 && !selectedAccountFor\(platform\.key\)/)
  assert.match(source, /存在多个账号的平台仍需由你明确选择目标账号/)
  assert.doesNotMatch(source, /account(?:Ids?|s)?\.(?:shift|pop|reverse)\(/i)
  assert.doesNotMatch(source, /rotateAccount|nextAccount|fallbackAccount/i)
})


test('confirmed real jobs execute serially in one queue and are never retried implicitly', () => {
  assert.match(source, /for \(const job of preparedJobs\) \{[\s\S]{0,260}await publishApi\.executeRealJob\(job\)/)
  assert.doesNotMatch(source, /Promise\.all\([^)]*executeRealJob/s)
  assert.match(source, /普通单项失败不拖住其他账号，也不会自动重试/)
  assert.match(source, /封禁、限制、登录失效或本人验证，则冻结该账号余下任务/)
  assert.match(source, /await fetchJobs\(true\)/)
})
