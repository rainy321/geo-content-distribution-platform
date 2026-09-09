import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import {
  buildStreamFailure,
  parseLoginSessionEvent,
  resolveLoginCancellation
} from '../src/utils/loginSession.js'


test('legacy login events remain supported', () => {
  assert.deepEqual(parseLoginSessionEvent('MANUAL_LOGIN'), {
    kind: 'stage', stage: 'manual', message: ''
  })
  assert.equal(parseLoginSessionEvent('200').kind, 'success')
  assert.equal(parseLoginSessionEvent('500').code, 'login_not_completed')
})


test('diagnostic error event preserves safe message and maps recovery action', () => {
  const event = parseLoginSessionEvent(
    'ERROR:browser_launch_failed:没有打开 Chrome：请检查本机浏览器'
  )

  assert.equal(event.kind, 'failure')
  assert.equal(event.code, 'browser_launch_failed')
  assert.equal(event.message, '没有打开 Chrome：请检查本机浏览器')
  assert.match(event.action, /Google Chrome/)
})


test('structured login events support stages, QR codes and actionable failures', () => {
  const stage = parseLoginSessionEvent(JSON.stringify({
    type: 'stage', stage: 'waiting_for_account', message: '请在浏览器中继续'
  }))
  const qr = parseLoginSessionEvent(JSON.stringify({
    type: 'qr_code', data_url: 'data:image/png;base64,iVBORw0KGgoAAA=='
  }))
  const failure = parseLoginSessionEvent(JSON.stringify({
    type: 'error', code: 'login_timeout', message: '等待登录超时', next_action: '重新扫码'
  }))

  assert.deepEqual(stage, {
    kind: 'stage', stage: 'manual', message: '请在浏览器中继续'
  })
  assert.equal(qr.kind, 'qr_code')
  assert.equal(failure.action, '重新扫码')
})


test('structured interruption receives a concrete browser recovery action', () => {
  const failure = parseLoginSessionEvent(JSON.stringify({
    type: 'error', code: 'login_interrupted', message: '登录窗口已关闭'
  }))

  assert.equal(failure.kind, 'failure')
  assert.match(failure.action, /保持浏览器窗口打开/)
})


test('machine action codes are translated instead of shown to operators', () => {
  const failure = parseLoginSessionEvent(JSON.stringify({
    type: 'error',
    code: 'login_timeout',
    message: '等待登录超时',
    action: 'complete_login_then_retry'
  }))

  assert.match(failure.action, /平台规定时间内完成/)
  assert.doesNotMatch(failure.action, /_/)
})


test('arbitrary long server text is never treated as a QR image', () => {
  const htmlError = `<html>${'server error '.repeat(30)}</html>`
  assert.deepEqual(parseLoginSessionEvent(htmlError), { kind: 'unknown' })
  assert.deepEqual(parseLoginSessionEvent('javascript:alert(1)'), { kind: 'unknown' })
})


test('stream failures have a distinct diagnosis from platform rejection', () => {
  const failure = buildStreamFailure()
  assert.equal(failure.code, 'stream_disconnected')
  assert.match(failure.message, /状态连接已中断/)
  assert.match(failure.action, /软件仍在运行/)
})


test('account dialog routes every SSE payload through the guarded parser', () => {
  const component = readFileSync(
    new URL('../src/views/AccountManagement.vue', import.meta.url),
    'utf8'
  )
  const accountApi = readFileSync(
    new URL('../src/api/account.js', import.meta.url),
    'utf8'
  )

  assert.match(component, /parseLoginSessionEvent\(rawValue\)/)
  assert.match(component, /&protocol=2/)
  assert.match(component, /Number\.isInteger\(existingAccountId\) && existingAccountId > 0/)
  assert.match(component, /&existing_account_id=\$\{existingAccountId\}/)
  assert.match(component, /const source = \(\(\) =>/)
  assert.match(component, /return new EventSource\(url/)
  assert.ok((component.match(/if \(eventSource !== source\) return/g) || []).length >= 3)
  assert.match(component, /const closeEventSource = \(source = eventSource\)/)
  assert.match(component, /if \(eventSource === source\) eventSource = null/)
  assert.match(component, /finishLoginWithFailure\(buildStreamFailure\(\)\)/)
  assert.match(component, /请查看桌面和任务栏/)
  assert.match(component, /软件将请求结束本次登录/)
  assert.match(component, /accountApi\.cancelMediaLogin/)
  assert.match(component, /resolveLoginCancellation\(response\)/)
  assert.match(component, /finishLoginWithSuccess\(resolution\.message\)/)
  assert.match(component, /finishLoginWithReview\(resolution\)/)
  assert.match(component, /检查关闭状态/)
  assert.match(accountApi, /post\('\/api\/media-login\/cancel'/)
  assert.doesNotMatch(component, /data\.length\s*>\s*100/)
})


test('browser close and operator cancellation remain distinct', () => {
  const browserClosed = parseLoginSessionEvent(JSON.stringify({
    type: 'error', code: 'browser_closed', message: 'Chrome 登录窗口已关闭'
  }))
  const cancelled = parseLoginSessionEvent(JSON.stringify({
    type: 'error', code: 'operator_cancelled', message: '已结束本次登录等待'
  }))

  assert.match(browserClosed.action, /保持软件打开的 Chrome 窗口/)
  assert.match(cancelled.action, /结束请求已接受/)
})


test('cancellation outcome wins the success-cancel race without guessing', () => {
  assert.equal(resolveLoginCancellation({
    msg: '账号已经登录成功', data: { outcome: 'success', active: false }
  }).kind, 'success')
  assert.equal(resolveLoginCancellation({
    data: { outcome: 'cancelled', active: false }
  }).kind, 'cancelled')
  assert.equal(resolveLoginCancellation({
    data: { outcome: 'cancelled', active: true }
  }).kind, 'pending')
  assert.equal(resolveLoginCancellation({
    data: { outcome: 'unknown', active: true }
  }).kind, 'pending')
  assert.equal(resolveLoginCancellation({
    data: { outcome: 'failed', active: false }
  }).kind, 'failure')

  const unknown = resolveLoginCancellation({
    data: { cancel_requested: false, outcome: 'unknown', active: false }
  })
  assert.equal(unknown.kind, 'review')
  assert.match(unknown.message, /刷新确认账号状态/)
  assert.doesNotMatch(`${unknown.message}${unknown.action}`, /未保存|丢弃/)
})
