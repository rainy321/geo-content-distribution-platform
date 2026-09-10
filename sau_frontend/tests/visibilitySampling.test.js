import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import {
  VISIBILITY_PROVIDER_PRESETS,
  applyVisibilityProviderPreset,
  buildManualSampleImport,
  buildVisibilitySamplingRequest,
  normalizeVisibilitySamplingResponse,
  validateVisibilitySamplingDraft
} from '../src/utils/visibilitySampling.js'

test('provider presets cover Qwen, DeepSeek, Doubao and custom without persisting secrets', () => {
  assert.deepEqual(VISIBILITY_PROVIDER_PRESETS.map(item => item.id), [
    'qwen', 'deepseek', 'doubao', 'custom'
  ])
  const next = applyVisibilityProviderPreset({ providerId: 'qwen', apiKey: 'session-secret' }, 'deepseek')
  assert.equal(next.baseUrl, 'https://api.deepseek.com')
  assert.equal(next.model, 'deepseek-v4-flash')
  assert.equal(next.apiKey, '')
  assert.equal(JSON.stringify(VISIBILITY_PROVIDER_PRESETS).includes('session-secret'), false)
})

test('sampling request uses the saved unified engine without sending a key again', () => {
  const body = buildVisibilitySamplingRequest({
    projectId: 7,
    prompt: '  哪些随身 AI 助手值得考虑？  ',
    sampleCount: 3,
    aliases: ['DongClaw'],
    competitors: ['RanClaw'],
    persist: true,
    confirmed: true,
    useUnifiedEngine: true,
    unifiedConfigured: true
  })

  assert.deepEqual(Object.keys(body).sort(), [
    'aliases',
    'competitors',
    'confirm_external_call',
    'persist',
    'project_id',
    'prompt',
    'sample_count'
  ])
  assert.equal(body.prompt, '哪些随身 AI 助手值得考虑？')
  assert.equal(body.confirm_external_call, true)
  assert.equal('project' in body, false)
  assert.equal('articles' in body, false)
  assert.equal('accounts' in body, false)
  assert.equal('ai_config' in body, false)
})

test('sampling request can carry an explicit one-time advanced override', () => {
  const body = buildVisibilitySamplingRequest({
    projectId: 7,
    prompt: '有效问题',
    sampleCount: 1,
    aliases: [],
    competitors: [],
    persist: false,
    confirmed: true,
    useUnifiedEngine: false,
    baseUrl: 'https://api.example.test/v1',
    apiKey: 'one-time-only',
    model: 'example-model'
  })
  assert.deepEqual(body.ai_config, {
    base_url: 'https://api.example.test/v1',
    api_key: 'one-time-only',
    model: 'example-model'
  })
})

test('sampling draft blocks invalid advanced override and unconfirmed calls', () => {
  const issues = validateVisibilitySamplingDraft({
    projectId: 1,
    prompt: '有效问题',
    sampleCount: 6,
    baseUrl: 'http://localhost:8000',
    model: '',
    apiKey: '',
    useUnifiedEngine: false,
    confirmed: false
  })
  assert.ok(issues.includes('真实采样次数必须是 1–5'))
  assert.ok(issues.includes('模型地址必须使用 HTTPS'))
  assert.ok(issues.includes('请填写精确模型 ID'))
  assert.ok(issues.includes('请填写本次覆盖使用的 API Key'))
  assert.ok(issues.includes('请确认本次外发边界'))
})

test('sampling draft requires a configured unified engine but never requires its saved key', () => {
  const missing = validateVisibilitySamplingDraft({
    projectId: 1,
    prompt: '有效问题',
    sampleCount: 3,
    useUnifiedEngine: true,
    unifiedConfigured: false,
    confirmed: true
  })
  assert.deepEqual(missing, ['请先在系统设置中保存统一 AI 引擎'])

  const ready = validateVisibilitySamplingDraft({
    projectId: 1,
    prompt: '有效问题',
    sampleCount: 3,
    useUnifiedEngine: true,
    unifiedConfigured: true,
    confirmed: true
  })
  assert.deepEqual(ready, [])
})

test('partial result keeps completed evidence, honest unknown cost and failure action', () => {
  const result = normalizeVisibilitySamplingResponse({
    code: 207,
    data: {
      status: 'partial',
      need_action: true,
      requested_sample_count: 5,
      completed_sample_count: 1,
      unattempted_sample_count: 3,
      executions: [{
        index: 1,
        provider: 'qwen',
        model: 'qwen-plus',
        elapsed_ms: 25,
        ai_usage: {
          usage_status: 'unknown',
          total_tokens: null,
          estimated_cost: null,
          cost_status: 'unknown_usage'
        }
      }],
      captured_responses: [{ text: 'DongClaw 是一个选项。', provider: 'qwen', model: 'qwen-plus' }],
      failure: {
        code: 'provider_outcome_unknown',
        message: '结果未知，请勿自动重试',
        sample_index: 2,
        outcome_unknown: true
      },
      data_disclosure: {
        sent_to_provider: ['prompt'],
        not_sent_to_provider: ['project_record', 'stored_articles'],
        prompt_sha256: 'abc'
      },
      retry_policy: { automatic_retry: false }
    }
  })

  assert.equal(result.status, 'partial')
  assert.equal(result.completedSampleCount, 1)
  assert.equal(result.executions[0].receipt.totalTokens, null)
  assert.equal(result.executions[0].receipt.costStatus, 'unknown_usage')
  assert.equal(result.failure.outcomeUnknown, true)
  assert.equal(result.retryPolicy.automatic_retry, false)
  assert.equal(buildManualSampleImport(result), 'DongClaw 是一个选项。')
  assert.equal(JSON.stringify(result).includes('api_key'), false)
})

test('EXE visibility view defaults to the unified engine and keeps explicit-call boundaries', () => {
  const apiSource = readFileSync(new URL('../src/api/diagnostics.js', import.meta.url), 'utf8')
  const viewSource = readFileSync(new URL('../src/views/AiVisibility.vue', import.meta.url), 'utf8')

  assert.match(apiSource, /\/api\/geo\/visibility\/sample/)
  assert.match(apiSource, /getVisibilitySample/)
  assert.match(apiSource, /Idempotency-Key/)
  assert.match(viewSource, /仅发送下面这一段问题/)
  assert.match(viewSource, /项目、文章、账号、Cookie 均不发送/)
  assert.match(viewSource, /使用已保存的统一引擎/)
  assert.match(viewSource, /高级：仅本次覆盖/)
  assert.match(viewSource, /不会后台自动回测/)
  assert.match(viewSource, /撤销导入/)
  assert.match(viewSource, /不会自动重试/)
  assert.match(viewSource, /保留原请求身份并继续查询/)
})
