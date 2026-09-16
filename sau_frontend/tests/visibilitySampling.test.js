import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import {
  VISIBILITY_PROVIDER_PRESETS,
  VISIBILITY_SAMPLE_OUTPUT_TOKEN_LIMIT,
  applyVisibilityProviderPreset,
  buildManualSampleImport,
  buildVisibilityPanelRequest,
  buildVisibilitySamplingRequest,
  normalizeVisibilityPanelResponse,
  normalizeVisibilitySamplingResponse,
  validateVisibilitySamplingDraft,
  visibilityPanelTargetPresentation
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

test('multi-model panel requires two distinct configured API models', () => {
  const missing = validateVisibilitySamplingDraft({
    projectId: 1,
    prompt: '有效问题',
    sampleCount: 1,
    useUnifiedEngine: true,
    unifiedConfigured: true,
    multiModel: true,
    distinctConfiguredModelCount: 1,
    confirmed: true
  })
  assert.deepEqual(missing, ['至少配置 2 个不同的 API 模型后才能运行多模型对照'])

  const body = buildVisibilityPanelRequest({
    projectId: 1,
    prompt: '有效问题',
    sampleCount: 2,
    aliases: [],
    competitors: [],
    persist: true,
    useUnifiedEngine: true,
    unifiedConfigured: true,
    distinctConfiguredModelCount: 2,
    roles: ['writer', 'reviewer'],
    confirmed: true
  })
  assert.deepEqual(body.roles, ['writer', 'reviewer'])
  assert.equal(body.confirm_external_call, true)
  assert.equal('ai_config' in body, false)
})

test('panel receipt never promotes provider API samples into consumer app verification', () => {
  const receipt = normalizeVisibilityPanelResponse({
    data: {
      status: 'completed',
      need_action: false,
      requested_role_count: 2,
      executed_role_count: 2,
      sampled_role_count: 2,
      distinct_model_count: 2,
      multi_model_executed: true,
      comparison_eligible: true,
      manifest: {
        targets: [{
          role: 'writer',
          provider: 'qwen',
          model: 'writer-model',
          subrequest_id: 'panel-request:writer'
        }]
      },
      targets: [{
        role: 'writer',
        execution_status: 'executed',
        status: 'completed',
        provider: 'unknown',
        model: 'unknown',
        run_id: 91,
        analysis: { samples: [{ text: '已保留回答' }] },
        completed_sample_count: 1,
        requested_sample_count: 1,
        channel_type: 'provider_api',
        web_search_verified: false,
        consumer_surface_verified: false
      }],
      channel_coverage: [
        { channel_type: 'provider_api', status: 'sampled', sampled_model_count: 2, web_search_verified: false },
        { channel_type: 'consumer_web', status: 'unverified', sampled_model_count: 0, web_search_verified: false },
        { channel_type: 'mobile_app', status: 'unverified', sampled_model_count: 0, web_search_verified: false }
      ],
      consumer_parity: { status: 'unverified', claim_allowed: false },
      claim_boundary: 'API only'
    }
  })

  assert.equal(receipt.distinctModelCount, 2)
  assert.equal(receipt.requestedRoleCount, 2)
  assert.equal(receipt.executedRoleCount, 2)
  assert.equal(receipt.sampledRoleCount, 2)
  assert.equal(receipt.targets[0].provider, 'qwen')
  assert.equal(receipt.targets[0].model, 'writer-model')
  assert.equal(receipt.targets[0].requestId, 'panel-request:writer')
  assert.equal(receipt.targets[0].runId, 91)
  assert.equal(receipt.targets[0].analysisAvailable, true)
  assert.equal(receipt.multiModelExecuted, true)
  assert.equal(receipt.targets[0].channelType, 'provider_api')
  assert.equal(receipt.targets[0].webSearchVerified, false)
  assert.deepEqual(
    receipt.channelCoverage.filter(item => item.channelType !== 'provider_api').map(item => item.status),
    ['unverified', 'unverified']
  )
  assert.equal(receipt.consumerParity.claim_allowed, false)
})

test('panel receipt keeps root unknown-outcome recovery instructions', () => {
  const receipt = normalizeVisibilityPanelResponse({
    data: {
      status: 'need_action',
      need_action: true,
      outcome_unknown: true,
      requested_role_count: 3,
      executed_role_count: 3,
      sampled_role_count: 0,
      distinct_model_count: 0,
      failure: { code: 'panel_outcome_unknown', message: '先核对模型账单' },
      retry_policy: { automatic_retry: false },
      recovery_policy: { read_only: true },
      targets: []
    }
  })

  assert.equal(receipt.outcomeUnknown, true)
  assert.equal(receipt.failure.code, 'panel_outcome_unknown')
  assert.equal(receipt.retryPolicy.automatic_retry, false)
  assert.equal(receipt.recoveryPolicy.read_only, true)
})

test('panel target presentation distinguishes complete, retained partial, unknown and failed outcomes', () => {
  assert.deepEqual(visibilityPanelTargetPresentation({
    completedSampleCount: 3,
    requestedSampleCount: 3,
    analysisAvailable: true
  }), {
    tone: 'complete',
    label: '完整完成',
    detail: '3 条有效回答已保留，已完成本地分析'
  })

  const partial = visibilityPanelTargetPresentation({
    executionStatus: 'executed',
    completedSampleCount: 1,
    requestedSampleCount: 3,
    analysisAvailable: true,
    failure: { code: 'provider_outcome_unknown', outcome_unknown: true }
  })
  assert.equal(partial.tone, 'partial')
  assert.equal(partial.label, '部分完成')
  assert.equal(partial.detail, '已保留 1 条有效回答，已完成本地分析')

  assert.equal(visibilityPanelTargetPresentation({
    executionStatus: 'outcome_unknown',
    completedSampleCount: 0,
    requestedSampleCount: 3,
    failure: { outcome_unknown: true }
  }).label, '结果未知')

  assert.equal(visibilityPanelTargetPresentation({
    executionStatus: 'failed',
    completedSampleCount: 0,
    requestedSampleCount: 3,
    failure: { code: 'provider_protocol_error' }
  }).label, '调用失败')
})

test('partial result keeps completed evidence, honest unknown cost and failure action', () => {
  const result = normalizeVisibilitySamplingResponse({
    code: 207,
    data: {
      status: 'partial',
      need_action: true,
      configured_provider: 'qwen',
      configured_model: 'qwen-plus',
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
      retry_policy: { automatic_retry: false },
      request_policy: {
        execution: 'sequential_stop_on_first_failure',
        max_output_tokens: 768,
        timeout_seconds: 120,
        connection_test_equivalent: false
      }
    }
  })

  assert.equal(result.status, 'partial')
  assert.equal(result.configuredProvider, 'qwen')
  assert.equal(result.configuredModel, 'qwen-plus')
  assert.equal(result.completedSampleCount, 1)
  assert.equal(result.executions[0].receipt.totalTokens, null)
  assert.equal(result.executions[0].receipt.costStatus, 'unknown_usage')
  assert.equal(result.failure.outcomeUnknown, true)
  assert.equal(result.retryPolicy.automatic_retry, false)
  assert.equal(result.requestPolicy.maxOutputTokens, VISIBILITY_SAMPLE_OUTPUT_TOKEN_LIMIT)
  assert.equal(result.requestPolicy.timeoutSeconds, 120)
  assert.equal(result.requestPolicy.connectionTestEquivalent, false)
  assert.equal(buildManualSampleImport(result), 'DongClaw 是一个选项。')
  assert.equal(JSON.stringify(result).includes('api_key'), false)
})

test('EXE visibility view defaults to the unified engine and keeps explicit-call boundaries', () => {
  const apiSource = readFileSync(new URL('../src/api/diagnostics.js', import.meta.url), 'utf8')
  const viewSource = readFileSync(new URL('../src/views/AiVisibility.vue', import.meta.url), 'utf8')

  assert.match(apiSource, /\/api\/geo\/visibility\/sample/)
  assert.match(apiSource, /\/api\/geo\/visibility\/sample-panel/)
  assert.match(apiSource, /getVisibilitySample/)
  assert.match(apiSource, /Idempotency-Key/)
  assert.match(viewSource, /仅发送下面这一段问题/)
  assert.match(viewSource, /项目、文章、账号、Cookie 均不发送/)
  assert.match(viewSource, /使用已保存的统一引擎/)
  assert.match(viewSource, /高级：仅本次覆盖/)
  assert.match(viewSource, /不会后台自动回测/)
  assert.match(viewSource, /CONTROLLED API SAMPLE/)
  assert.match(viewSource, /channel_type/)
  assert.match(viewSource, /消费者网页 \/ App/)
  assert.match(viewSource, /撤销导入/)
  assert.match(viewSource, /不会自动重试/)
  assert.match(viewSource, /保留原请求身份并继续查询/)
  assert.match(viewSource, /检查原请求状态/)
  assert.match(viewSource, /打开成本账单/)
  assert.match(viewSource, /新建 1 次采样/)
  assert.match(viewSource, /这不是重试原请求/)
  assert.match(viewSource, /sampledRoleCount[^\n]*\/[^\n]*requestedRoleCount/)
  assert.match(viewSource, /对应[^\n]*distinctModelCount[^\n]*个独立模型/)
  assert.match(viewSource, /“取得有效回答数”不是“已配置模型数”/)
  assert.match(viewSource, /panel-target-message/)
  assert.match(viewSource, /target\.presentation\.detail/)
  assert.match(viewSource, /applyVisibilityPanelReceipt/)
  assert.match(viewSource, /visibilityPanelReceipt\.value = null/)
  assert.match(viewSource, /\.panel-target-identity strong \{[^}]*overflow-wrap: anywhere;[^}]*white-space: normal;/)
  assert.match(viewSource, /\.panel-target-message \{[^}]*grid-column: 2 \/ -1;[^}]*overflow-wrap: anywhere;/)
  assert.match(viewSource, /\.execution-receipts \.panel-target-message \{ grid-template-columns: 1fr; \}/)
})
