import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import {
  applyUnifiedAIProviderPreset,
  buildUnifiedAIConfigPayload,
  normalizeUnifiedAIStatus,
  unifiedAICredentialLabel
} from '../src/utils/aiConfig.js'

test('unified AI status exposes safe metadata without inventing a credential', () => {
  const status = normalizeUnifiedAIStatus({
    data: {
      configured: true,
      saved_configured: true,
      save_supported: true,
      source: 'saved',
      provider: 'qwen',
      base_url: 'https://api.example.test/v1',
      model: 'qwen-example',
      api_key_configured: true,
      credential_storage: 'windows_dpapi_current_user'
    }
  })

  assert.equal(status.configured, true)
  assert.equal(status.providerLabel, '千问 / Qwen')
  assert.equal(status.model, 'qwen-example')
  assert.equal(unifiedAICredentialLabel(status), '•••••••• · 已受保护')
  assert.equal(JSON.stringify(status).includes('api_key'), false)
})

test('editing metadata reuses a protected key while first save requires one', () => {
  assert.deepEqual(buildUnifiedAIConfigPayload({
    provider: 'qwen',
    baseUrl: 'https://api.example.test/v1/',
    model: 'qwen-example',
    apiKey: ''
  }, { savedConfigured: true, apiKeyConfigured: true }), {
    provider: 'qwen',
    base_url: 'https://api.example.test/v1',
    model: 'qwen-example'
  })

  assert.throws(() => buildUnifiedAIConfigPayload({
    provider: 'qwen',
    baseUrl: 'https://api.example.test/v1',
    model: 'qwen-example',
    apiKey: ''
  }, { savedConfigured: false, apiKeyConfigured: true }), /首次保存需要填写 API Key/)
})

test('changing providers clears the in-memory draft key', () => {
  const next = applyUnifiedAIProviderPreset({ provider: 'qwen', apiKey: 'secret' }, 'deepseek')
  assert.equal(next.provider, 'deepseek')
  assert.equal(next.baseUrl, 'https://api.deepseek.com')
  assert.equal(next.apiKey, '')
})

test('unified AI API includes read, save, clear and fixed synthetic test actions', () => {
  const source = readFileSync(new URL('../src/api/aiConfig.js', import.meta.url), 'utf8')
  assert.match(source, /http\.get\('\/api\/ai\/config'/)
  assert.match(source, /http\.put\('\/api\/ai\/config'/)
  assert.match(source, /http\.delete\('\/api\/ai\/config'/)
  assert.match(source, /http\.post\('\/api\/ai\/config\/test'/)
  assert.match(source, /confirm_external_call: true/)
  assert.doesNotMatch(source, /api_key\s*:/)
})

test('content creation and visibility show the same unified engine bus', () => {
  const creation = readFileSync(new URL('../src/views/ContentCreation.vue', import.meta.url), 'utf8')
  const visibility = readFileSync(new URL('../src/views/AiVisibility.vue', import.meta.url), 'utf8')
  assert.match(creation, /UNIFIED AI BUS/)
  assert.match(creation, /fetchUnifiedAIStatus/)
  assert.match(visibility, /UNIFIED AI BUS/)
  assert.match(visibility, /useUnifiedEngine: true/)
})
