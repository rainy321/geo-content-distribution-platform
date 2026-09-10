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
  assert.match(component, /disconnectingIds\.value\.has\(account\.id\)[\s\S]{0,120}checkingIds\.value\.has\(account\.id\)[\s\S]{0,120}resolvingRiskIds\.value\.has\(account\.id\)/)
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


test('account cards expose independent multi-account sessions and risk recovery', () => {
  const component = readFileSync(
    new URL('../src/views/AccountManagement.vue', import.meta.url),
    'utf8'
  )
  const accountApi = readFileSync(
    new URL('../src/api/account.js', import.meta.url),
    'utf8'
  )

  assert.match(component, /为\$\{props\.platform\.name\}添加一个独立账号会话/)
  assert.match(component, /\(\) => '添加账号'/)
  assert.match(component, /平台没有公布统一的“同 IP 安全账号数量”/)
  assert.match(component, /不自动轮换账号，也不绕过封禁/)
  assert.match(component, /permanent_ban: \{ label: '永久封禁'/)
  assert.match(component, /temporary_restriction: \{ label: '暂时受限'/)
  assert.match(component, /verification_required: \{ label: '待本人验证'/)
  assert.match(component, /risk\.recommended_action/)
  assert.match(component, /申诉已通过，重新验证/)
  assert.match(component, /accountApi\.resolveMediaAccountRisk\(account\.id, risk\.category\)/)
  assert.match(component, /软件不会绕过封禁，也不会自动改用其他账号/)
  assert.match(accountApi, /`\/api\/media-accounts\/\$\{id\}\/risk\/resolve`/)
  assert.match(accountApi, /\{ confirm_recovery: true, category \}/)
})


test('only Zhihu can reuse an exact account row while other platforms unlink first', () => {
  const component = readFileSync(
    new URL('../src/views/AccountManagement.vue', import.meta.url),
    'utf8'
  )

  assert.match(component, /const supportsInPlaceReauth = platform => Number\(platform\?\.type\) === 9/)
  assert.match(component, /supportsInPlaceReauth\(props\.platform\) \? '重新登录' : '解绑后重连'/)
  assert.match(component, /emit\('reconnect', account, props\.platform\)/)
  assert.match(component, /account && !supportsInPlaceReauth\(platform\)[\s\S]{0,180}不能直接复用原账号记录/)
  assert.match(component, /accountApi\.disconnectMediaAccount\(account\.id\)[\s\S]{0,500}openLogin\(platform, null, account\.account_name\)/)
  assert.match(component, /response\.data\?\.cleanup_pending[\s\S]{0,180}return/)
  assert.match(component, /解绑旧账号并继续/)
  assert.match(component, /不会复用受封禁凭证、自动换号或绕过平台限制/)
})
