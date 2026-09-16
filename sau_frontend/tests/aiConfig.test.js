import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import {
  AI_ROLE_DEFINITIONS,
  applyUnifiedAIProviderPreset,
  buildUnifiedAIRoleConfigPayload,
  buildUnifiedAIConfigPayload,
  normalizeUnifiedAIBaseUrl,
  normalizeUnifiedAIStatus,
  resolveUnifiedAIChatEndpoint,
  resolveUnifiedAIImageEndpoint,
  unifiedAIImageCompatibilityIssue,
  unifiedAIProviderConfigurationHint,
  unifiedAICredentialLabel,
  validateUnifiedAIImageCompatibility
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

test('evaluator similarity metadata remains visible instead of implying an independent model', () => {
  const status = normalizeUnifiedAIStatus({
    data: {
      configured: true,
      provider: 'doubao',
      base_url: 'https://ark.example.test/api/v3',
      model: 'writer-model',
      roles: {
        evaluator: {
          configured: true,
          effective_configured: true,
          provider: 'doubao',
          base_url: 'https://ark.example.test/api/v3',
          model: 'writer-model',
          same_as_writer: true,
          same_as_reviewer: true
        }
      }
    }
  })
  const settingsSource = readFileSync(
    new URL('../src/views/SystemSettings.vue', import.meta.url),
    'utf8'
  )

  assert.equal(status.roles.evaluator.sameAsWriter, true)
  assert.equal(status.roles.evaluator.sameAsReviewer, true)
  assert.match(settingsSource, /评估模型不是独立模型/)
  assert.match(settingsSource, /不会作为独立评估调用，也不计入多模型数量/)
  assert.match(settingsSource, /已配置 · 非独立/)
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
  assert.equal(next.model, 'deepseek-chat')
  assert.equal(next.apiKey, '')
})

test('provider endpoints are normalized without duplicate or injected v1 paths', () => {
  assert.equal(
    resolveUnifiedAIChatEndpoint('https://dashscope.aliyuncs.com/compatible-mode/v1/'),
    'https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions'
  )
  assert.equal(
    resolveUnifiedAIChatEndpoint('https://api.deepseek.com'),
    'https://api.deepseek.com/chat/completions'
  )
  assert.equal(
    resolveUnifiedAIChatEndpoint('https://ark.cn-beijing.volces.com/api/v3'),
    'https://ark.cn-beijing.volces.com/api/v3/chat/completions'
  )
  assert.equal(
    resolveUnifiedAIChatEndpoint('https://gateway.example.com/v2/chat/completions/chat/completions'),
    'https://gateway.example.com/v2/chat/completions'
  )
  assert.equal(
    normalizeUnifiedAIBaseUrl('https://API.EXAMPLE.COM:443/v1/'),
    'https://api.example.com/v1'
  )
})

test('image endpoint replaces a pasted chat endpoint without duplicate paths', () => {
  assert.equal(
    resolveUnifiedAIImageEndpoint('https://gateway.example.com/v1'),
    'https://gateway.example.com/v1/images/generations'
  )
  assert.equal(
    resolveUnifiedAIImageEndpoint('https://gateway.example.com/v1/chat/completions'),
    'https://gateway.example.com/v1/images/generations'
  )
  assert.equal(
    resolveUnifiedAIImageEndpoint('https://gateway.example.com/v1/images/generations'),
    'https://gateway.example.com/v1/images/generations'
  )
  assert.equal(
    resolveUnifiedAIImageEndpoint('https://tongyuye.com/v1'),
    'https://tongyuye.com/v1/images/generations'
  )
})

test('image role rejects Seedance and video task endpoints before save', () => {
  const issue = unifiedAIImageCompatibilityIssue(
    'https://tongyuye.com/v1',
    'seedance-5.0'
  )
  assert.equal(issue.code, 'video_model_not_supported')
  assert.match(issue.message, /Seedance 是视频模型/)
  assert.match(issue.message, /Seedream/)
  assert.throws(() => buildUnifiedAIRoleConfigPayload({
    provider: 'custom',
    baseUrl: 'https://tongyuye.com/v1',
    model: 'seedance-5.0',
    apiKey: 'synthetic-secret',
    reuseWriterCredential: false
  }, {}, {}, 'image'), /Seedance 是视频模型/)
  assert.throws(() => validateUnifiedAIImageCompatibility(
    'https://ark.cn-beijing.volces.com/api/v3/contents/generations/tasks',
    'ep-synthetic-video-endpoint'
  ), /视频生成任务地址/)
  assert.equal(validateUnifiedAIImageCompatibility(
    'https://ark.cn-beijing.volces.com/api/v3',
    'seedream-5.0'
  ), true)

  const settingsSource = readFileSync(
    new URL('../src/views/SystemSettings.vue', import.meta.url),
    'utf8'
  )
  assert.match(settingsSource, /IMAGE 只接收图片生成模型/)
  assert.match(settingsSource, /Seedream 是图片模型，Seedance 是视频模型/)
  assert.match(settingsSource, /diagnosticCode/)
  assert.match(settingsSource, /最小 Images 请求（仅模型与提示词）/)
  assert.match(settingsSource, /服务商请求编号/)
})

test('ark preset binds the api v3 base and requires its own model id', () => {
  const next = applyUnifiedAIProviderPreset({ provider: 'qwen', apiKey: 'secret' }, 'doubao')
  assert.equal(next.provider, 'doubao')
  assert.equal(next.baseUrl, 'https://ark.cn-beijing.volces.com/api/v3')
  assert.equal(next.model, '')
  assert.equal(next.apiKey, '')
  assert.match(unifiedAIProviderConfigurationHint('doubao'), /模型或接入点 ID/)

  assert.throws(() => buildUnifiedAIConfigPayload({
    provider: 'doubao',
    baseUrl: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
    model: 'ep-test',
    apiKey: 'secret'
  }), /服务商与 Base URL 不匹配/)

  assert.throws(() => buildUnifiedAIConfigPayload({
    provider: 'qwen',
    baseUrl: 'https://dashscope.aliyuncs.com/v1',
    model: 'qwen-test',
    apiKey: 'secret'
  }), /compatible-mode\/v1/)
})

test('credential reuse compares the resolved chat endpoint identity', () => {
  const payload = buildUnifiedAIConfigPayload({
    provider: 'custom',
    baseUrl: 'https://gateway.example.com/openai/v2',
    model: 'model-v2',
    apiKey: ''
  }, {
    savedConfigured: true,
    apiKeyConfigured: true,
    provider: 'custom',
    baseUrl: 'https://gateway.example.com/openai/v2/chat/completions'
  })

  assert.equal(payload.base_url, 'https://gateway.example.com/openai/v2')
  assert.equal(Object.hasOwn(payload, 'api_key'), false)
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
  assert.match(creation, /unifiedAIStatus\.configured\s*\?\s*unifiedAIEngineLabel\.value/)
  assert.match(visibility, /UNIFIED AI BUS/)
  assert.match(visibility, /useUnifiedEngine: true/)
})
