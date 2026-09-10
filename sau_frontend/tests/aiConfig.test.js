import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import {
  AI_ROLE_DEFINITIONS,
  applyUnifiedAIProviderPreset,
  buildUnifiedAIRoleConfigPayload,
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

test('role status exposes explicit fallback and reviewer similarity risk', () => {
  const status = normalizeUnifiedAIStatus({
    data: {
      configured: true,
      saved_configured: true,
      provider: 'qwen',
      base_url: 'https://api.example.test/v1',
      model: 'writer-model',
      api_key_configured: true,
      roles: {
        writer: {
          configured: true,
          effective_configured: true,
          provider: 'qwen',
          base_url: 'https://api.example.test/v1',
          model: 'writer-model',
          api_key_configured: true
        },
        reviewer: {
          configured: false,
          effective_configured: true,
          fallback: true,
          source_role: 'writer',
          provider: 'qwen',
          base_url: 'https://api.example.test/v1',
          model: 'writer-model',
          api_key_configured: true,
          same_as_writer: true
        }
      }
    }
  })

  assert.equal(AI_ROLE_DEFINITIONS.length, 4)
  assert.equal(status.roles.reviewer.fallback, true)
  assert.equal(status.roles.reviewer.sourceRole, 'writer')
  assert.equal(status.roles.reviewer.sameAsWriter, true)
})

test('optional role can reuse writer credential only on the same provider endpoint', () => {
  const writer = {
    provider: 'qwen',
    baseUrl: 'https://api.example.test/v1',
    apiKeyConfigured: true
  }
  const payload = buildUnifiedAIRoleConfigPayload({
    provider: 'qwen',
    baseUrl: 'https://api.example.test/v1/',
    model: 'review-model',
    apiKey: '',
    reuseWriterCredential: true
  }, {}, writer)

  assert.equal(payload.reuse_writer_credential, true)
  assert.equal(Object.hasOwn(payload, 'api_key'), false)
  assert.throws(() => buildUnifiedAIRoleConfigPayload({
    provider: 'custom',
    baseUrl: 'https://other.example.test/v1',
    model: 'review-model',
    apiKey: '',
    reuseWriterCredential: true
  }, {}, writer), /需要 API Key/)
  assert.throws(() => buildUnifiedAIRoleConfigPayload({
    provider: 'custom',
    baseUrl: 'https://other.example.test/v1',
    model: 'review-model-v2',
    apiKey: '',
    reuseWriterCredential: false
  }, {
    configured: true,
    apiKeyConfigured: true,
    provider: 'qwen',
    baseUrl: 'https://api.example.test/v1'
  }, writer), /需要 API Key/)
})

test('editing metadata reuses a protected key while first save requires one', () => {
  assert.deepEqual(buildUnifiedAIConfigPayload({
    provider: 'qwen',
    baseUrl: 'https://api.example.test/v1/',
    model: 'qwen-example',
    apiKey: ''
  }, {
    savedConfigured: true,
    apiKeyConfigured: true,
    provider: 'qwen',
    baseUrl: 'https://api.example.test/v1'
  }), {
    provider: 'qwen',
    base_url: 'https://api.example.test/v1',
    model: 'qwen-example'
  })

  assert.throws(() => buildUnifiedAIConfigPayload({
    provider: 'qwen',
    baseUrl: 'https://api.example.test/v1',
    model: 'qwen-example',
    apiKey: ''
  }, { savedConfigured: false, apiKeyConfigured: true }), /需要填写 API Key/)

  assert.throws(() => buildUnifiedAIConfigPayload({
    provider: 'custom',
    baseUrl: 'https://other.example.test/v1',
    model: 'replacement-model',
    apiKey: ''
  }, {
    savedConfigured: true,
    apiKeyConfigured: true,
    provider: 'qwen',
    baseUrl: 'https://api.example.test/v1'
  }), /切换服务地址时需要填写 API Key/)
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
  assert.match(source, /config\/roles\/\$\{encodeURIComponent\(role\)\}/)
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
