import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'


test('desktop shell exposes publishing navigation only to an authorized build', () => {
  const appSource = readFileSync(new URL('../src/App.vue', import.meta.url), 'utf8')
  const statusSource = readFileSync(
    new URL('../src/components/DesktopStatusBar.vue', import.meta.url),
    'utf8'
  )
  const aboutSource = readFileSync(new URL('../src/views/About.vue', import.meta.url), 'utf8')

  assert.match(appSource, /v-if="publishingWorkspaceVisible" index="\/account-management"/)
  assert.match(appSource, /v-if="publishingWorkspaceVisible" index="\/publish-center"/)
  assert.match(appSource, /!desktopMode\.value \|\| desktopRealPublishingAuthorized\.value/)
  assert.match(appSource, /status\?\.real_publishing_authorized/)
  assert.match(appSource, /!desktopRealPublishingAuthorized\.value/)
  assert.match(appSource, /watch\(\(\) => route\.path, enforcePublishingRouteBoundary\)/)
  assert.match(appSource, /authState\.mode === 'desktop_local'/)
  assert.match(appSource, /<DesktopStatusBar @status="handleDesktopStatus"/)
  assert.match(appSource, /grid-template-columns: auto minmax\(0, 1fr\)/)
  assert.match(statusSource, /publishing_runtime: \{ \.\.\.\(status\.publishing_runtime \|\| \{\}\) \}/)
  assert.match(statusSource, /real_publishing_authorized/)
  assert.match(statusSource, /运行时未就绪/)
  assert.match(statusSource, /真实发布已开启/)
  assert.match(statusSource, /发布锁定/)
  assert.match(statusSource, /@media \(max-width: 1600px\)/)
  assert.match(aboutSource, /publishingState/)
  assert.match(aboutSource, /安全构建锁定/)
  assert.match(aboutSource, /真实发布已开启/)
  assert.match(aboutSource, /运行时未就绪/)
})


test('real publishing UI binds capability, account, task version and fingerprint', () => {
  const accountSource = readFileSync(
    new URL('../src/views/AccountManagement.vue', import.meta.url),
    'utf8'
  )
  const distributionSource = readFileSync(
    new URL('../src/views/DistributionCenter.vue', import.meta.url),
    'utf8'
  )
  const publishApiSource = readFileSync(new URL('../src/api/publish.js', import.meta.url), 'utf8')

  assert.match(distributionSource, /platform\.task_creation_available === true/)
  assert.match(distributionSource, /item\.real_publishing_authorized === true/)
  assert.match(distributionSource, /item\.demo_available === true/)
  assert.match(distributionSource, /const selectedPlatforms = ref\(\[\]\)/)
  assert.match(distributionSource, /selectedAccountIds\[platform\.key\] = null/)
  assert.doesNotMatch(distributionSource, /selectedAccountIds\[[^\]]+\] = accounts\[0\]/)
  assert.match(distributionSource, /&& !videoUploading\.value/)
  assert.match(distributionSource, /v-if="scheduleEnabled && demoAvailable"/)
  assert.match(distributionSource, /到点后等待人工确认/)
  assert.match(distributionSource, /auto_execute: demoAvailable\.value && confirmedAutoExecute/)
  assert.doesNotMatch(distributionSource, /授权定时真实发布/)
  assert.doesNotMatch(distributionSource, /强确认 · 定时真实发布/)
  assert.match(distributionSource, /accountDisplayName\(account\)/)
  assert.match(distributionSource, /login_platform: platform\.key/)
  assert.match(distributionSource, /已定位原稿件与平台；请重新选择账号并核对素材/)
  assert.match(distributionSource, /ElMessageBox\.prompt/)
  assert.match(distributionSource, /发布 \$\{jobCode\}/)
  assert.match(distributionSource, /确认内容绑定指纹/)
  assert.match(distributionSource, /articleSnapshot = currentJob\?\.confirmation_snapshot/)
  assert.match(distributionSource, /Number\(articleSnapshot\.article_id\) !== Number\(currentJob\.article_id\)/)
  assert.doesNotMatch(distributionSource, /articleApi\.getArticle\(job\.article_id\)/)
  assert.match(distributionSource, /bilibili: '兼容运行时 · 视频发布'/)
  assert.match(distributionSource, /仅演示 · 真实运行时未开放/)
  assert.match(distributionSource, /douyin: '实验性 · 可能转待人工'/)

  assert.match(accountSource, /Promise\.allSettled/)
  assert.match(accountSource, /发布能力清单未载入，登录与检测已安全禁用/)
  assert.match(accountSource, /platform\.login_available !== true/)
  assert.match(accountSource, /route\.query\.login_platform/)
  assert.match(accountSource, /返回发布中心继续/)

  assert.match(publishApiSource, /job_id: job\.job_id/)
  assert.match(publishApiSource, /state_version: job\.state_version/)
  assert.match(publishApiSource, /account_id: job\.account_id/)
  assert.match(publishApiSource, /confirmation_fingerprint: job\.confirmation_fingerprint/)
})


test('first-run copy describes the local visibility workflow', () => {
  const gateSource = readFileSync(
    new URL('../src/components/AccessGate.vue', import.meta.url),
    'utf8'
  )
  const dashboardSource = readFileSync(
    new URL('../src/views/Dashboard.vue', import.meta.url),
    'utf8'
  )

  assert.match(gateSource, /项目、分析历史与模型额度/)
  assert.match(gateSource, /<strong>观察<\/strong><small>可见度证据<\/small>/)
  assert.match(dashboardSource, /创建第一个品牌项目/)
  assert.match(dashboardSource, /无需先连接媒体账号，也不会触发真实发布/)
})


test('AI settings exposes one protected engine for writing, tuning and retesting', () => {
  const settingsSource = readFileSync(
    new URL('../src/views/SystemSettings.vue', import.meta.url),
    'utf8'
  )
  const configSource = readFileSync(
    new URL('../src/utils/aiConfig.js', import.meta.url),
    'utf8'
  )

  assert.match(settingsSource, /ONE ENGINE \/ ALL WORKFLOWS/)
  assert.match(settingsSource, /Windows DPAPI/)
  assert.match(settingsSource, /写稿、调优与回测/)
  assert.match(settingsSource, /测试连接只会向当前模型发送固定合成文本/)
  assert.doesNotMatch(settingsSource, /Vercel Production/)
  assert.doesNotMatch(settingsSource, /仅当前标签页/)
  assert.doesNotMatch(settingsSource, /浏览器配置/)
  assert.match(configSource, /apiKeyConfigured/)
  assert.doesNotMatch(configSource, /sessionStorage/)
})
