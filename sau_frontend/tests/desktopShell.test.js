import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'


test('desktop shell hides unavailable media and publishing navigation', () => {
  const appSource = readFileSync(new URL('../src/App.vue', import.meta.url), 'utf8')
  const statusSource = readFileSync(
    new URL('../src/components/DesktopStatusBar.vue', import.meta.url),
    'utf8'
  )

  assert.match(appSource, /v-if="!desktopMode" index="\/account-management"/)
  assert.match(appSource, /v-if="!desktopMode" index="\/publish-center"/)
  assert.match(appSource, /authState\.mode === 'desktop_local'/)
  assert.match(appSource, /<DesktopStatusBar @status="handleDesktopStatus"/)
  assert.match(statusSource, /emit\('status', \{ \.\.\.status \}\)/)
  assert.match(statusSource, /发布锁定/)
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
