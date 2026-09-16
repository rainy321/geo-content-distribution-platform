import assert from 'node:assert/strict'
import fs from 'node:fs'
import test from 'node:test'

import {
  WEIBO_ACCOUNT_TYPE,
  WEIBO_CONNECTOR_UNAVAILABLE_REASON,
  WEIBO_FRONTEND_CAPABILITY,
  withWeiboAccountPlatform,
  withWeiboCapability
} from '../src/utils/weiboPlatform.js'

const readSource = path => fs.readFileSync(new URL(path, import.meta.url), 'utf8')

test('Weibo frontend capability is visible but fail-closed until connector and OAuth exist', () => {
  assert.equal(WEIBO_ACCOUNT_TYPE, 11)
  assert.equal(WEIBO_FRONTEND_CAPABILITY.name, '微博')
  assert.equal(WEIBO_FRONTEND_CAPABILITY.release_status, 'connector_required')
  assert.equal(WEIBO_FRONTEND_CAPABILITY.connector_available, false)
  assert.equal(WEIBO_FRONTEND_CAPABILITY.oauth_required, true)
  assert.equal(WEIBO_FRONTEND_CAPABILITY.login_available, false)
  assert.equal(WEIBO_FRONTEND_CAPABILITY.task_creation_available, false)
  assert.match(WEIBO_CONNECTOR_UNAVAILABLE_REASON, /微博官方连接器/)
  assert.match(WEIBO_CONNECTOR_UNAVAILABLE_REASON, /OAuth 授权/)
})

test('Weibo is inserted with P0 channels without mutating the backend response', () => {
  const backendPlatforms = [
    { key: 'zhihu', priority: 'P0' },
    { key: 'douyin', priority: 'P2' }
  ]
  const result = withWeiboCapability(backendPlatforms)

  assert.deepEqual(backendPlatforms.map(item => item.key), ['zhihu', 'douyin'])
  assert.deepEqual(result.map(item => item.key), ['zhihu', 'weibo', 'douyin'])
  assert.equal(result[1].type, 11)
  assert.equal(result[1].task_creation_available, false)
})

test('backend Weibo runtime availability is preserved without promoting release maturity', () => {
  const [weibo] = withWeiboCapability([{
    key: 'weibo',
    priority: 'P0',
    release_status: 'stable',
    connector_available: true,
    runtime_available: true,
    login_available: true,
    task_creation_available: true,
    real_publishing_authorized: true
  }])

  assert.equal(weibo.release_status, 'connector_required')
  assert.equal(weibo.connector_available, true)
  assert.equal(weibo.runtime_available, true)
  assert.equal(weibo.login_available, true)
  assert.equal(weibo.task_creation_available, true)
  assert.equal(weibo.real_publishing_authorized, true)
})

test('official backend release status and runtime booleans survive account overview merging', () => {
  const [weibo] = withWeiboAccountPlatform([{
    key: 'weibo',
    release_status: 'official_connector_required',
    connector_available: true,
    runtime_available: true,
    login_available: true,
    task_creation_available: true
  }], [])

  assert.equal(weibo.release_status, 'official_connector_required')
  assert.equal(weibo.connector_available, true)
  assert.equal(weibo.runtime_available, true)
  assert.equal(weibo.login_available, true)
  assert.equal(weibo.task_creation_available, true)
  assert.equal(weibo.account_count, 0)
})

test('missing backend Weibo capability remains a fail-closed placeholder', () => {
  const weibo = withWeiboCapability([]).find(item => item.key === 'weibo')

  assert.equal(weibo.release_status, 'connector_required')
  assert.equal(weibo.connector_available, false)
  assert.equal(weibo.runtime_available, false)
  assert.equal(weibo.login_available, false)
  assert.equal(weibo.task_creation_available, false)
})

test('account overview exposes type 11 records under the Weibo card', () => {
  const accounts = [
    { id: 1, type: 11, status: 'connected', account_name: '品牌微博' },
    { id: 2, type: 9, status: 'connected', account_name: '知乎账号' }
  ]
  const result = withWeiboAccountPlatform([], accounts)
  const weibo = result.find(item => item.key === 'weibo')

  assert.equal(weibo.type, 11)
  assert.equal(weibo.account_count, 1)
  assert.equal(weibo.connected_count, 1)
  assert.deepEqual(weibo.accounts.map(item => item.id), [1])
  assert.equal(weibo.connector_available, false)
  assert.equal(weibo.runtime_available, false)
  assert.equal(weibo.login_available, false)
  assert.equal(weibo.task_creation_available, false)
})

test('all existing frontend entry points surface Weibo and legacy publish fails before API submission', () => {
  const accountStore = readSource('../src/stores/account.js')
  const creation = readSource('../src/views/ContentCreation.vue')
  const accounts = readSource('../src/views/AccountManagement.vue')
  const distribution = readSource('../src/views/DistributionCenter.vue')
  const legacyPublish = readSource('../src/views/PublishCenter.vue')

  assert.match(accountStore, /11:\s*'微博'/)
  assert.match(creation, /\{ label: '微博', value: '微博' \}/)
  assert.match(creation, /weibo:\s*'微博'/)
  assert.match(accounts, /withWeiboAccountPlatform\(/)
  assert.match(distribution, /withWeiboCapability\(response\.data \|\| \[\]\)/)
  assert.match(distribution, /weibo:\s*'WB'/)
  assert.match(legacyPublish, /key:\s*WEIBO_ACCOUNT_TYPE/)
  assert.match(legacyPublish, /platform\.connector_available === false/)

  const guardIndex = legacyPublish.indexOf('tab.selectedPlatform === WEIBO_ACCOUNT_TYPE')
  const submitIndex = legacyPublish.indexOf("http.post('/postVideo'")
  assert.ok(guardIndex >= 0)
  assert.ok(submitIndex > guardIndex)
})
