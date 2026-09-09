import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'


test('saved media accounts expose confirmed local disconnect semantics', () => {
  const component = readFileSync(
    new URL('../src/views/AccountManagement.vue', import.meta.url),
    'utf8'
  )
  const accountApi = readFileSync(
    new URL('../src/api/account.js', import.meta.url),
    'utf8'
  )

  assert.match(component, /退出 \/ 解绑/)
  assert.match(component, /accountApi\.disconnectMediaAccount\(account\.id\)/)
  assert.match(component, /confirmButtonText: '退出并解绑'/)
  assert.match(component, /不会注销\$\{platform\.name\}网站上的其他设备会话/)
  assert.match(component, /解除待发布任务的账号绑定/)
  assert.match(component, /已完成和失败的发布历史仍保留账号名称/)
  assert.match(component, /response\.data\?\.cleanup_pending/)
  assert.match(component, /disconnectingIds\.value\.has\(account\.id\) \|\| checkingIds\.value\.has\(account\.id\)/)
  assert.match(component, /disabled: props\.platform\.login_available === false \|\| lifecycleBusy/)
  assert.match(accountApi, /post\(\s*`\/api\/media-accounts\/\$\{id\}\/disconnect`/)
  assert.match(accountApi, /\{ confirm_unbind: true \}/)
})


test('active login dialog exposes a cooperative end-login action', () => {
  const component = readFileSync(
    new URL('../src/views/AccountManagement.vue', import.meta.url),
    'utf8'
  )
  const accountApi = readFileSync(
    new URL('../src/api/account.js', import.meta.url),
    'utf8'
  )

  assert.match(component, /结束本次登录/)
  assert.match(component, /accountApi\.cancelMediaLogin/)
  assert.match(component, /正在请求后台结束登录并关闭\$\{loginWindowName\.value\}/)
  assert.match(component, /selectedPlatform\.value\?\.key === 'bilibili' \? ' Bilibili 扫码窗口' : ' Google Chrome'/)
  assert.match(accountApi, /post\('\/api\/media-login\/cancel'/)
})
