const asArray = value => (Array.isArray(value) ? value : [])

const numberOrNull = value => {
  if (value == null || value === '') return null
  const number = Number(value)
  return Number.isFinite(number) ? number : null
}

// Keep this display contract aligned with the backend sampling policy.  The
// authoritative value is still returned on every new sampling receipt.
export const VISIBILITY_SAMPLE_OUTPUT_TOKEN_LIMIT = 768

export const VISIBILITY_PROVIDER_PRESETS = Object.freeze([
  Object.freeze({
    id: 'qwen',
    label: '千问 / Qwen',
    baseUrl: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
    model: 'qwen-plus',
    modelHint: '可替换为你账号可用的精确模型 ID'
  }),
  Object.freeze({
    id: 'deepseek',
    label: 'DeepSeek',
    baseUrl: 'https://api.deepseek.com',
    model: 'deepseek-v4-flash',
    modelHint: '默认采用 2026-09 当前模型；仍需按控制台核对精确 ID'
  }),
  Object.freeze({
    id: 'doubao',
    label: '豆包 / 火山方舟',
    baseUrl: 'https://ark.cn-beijing.volces.com/api/v3',
    model: '',
    modelHint: '填写火山方舟控制台中的模型或推理接入点 ID'
  }),
  Object.freeze({
    id: 'custom',
    label: '自定义兼容服务',
    baseUrl: '',
    model: '',
    modelHint: '支持 OpenAI-compatible Chat Completions'
  })
])

export const visibilityProviderPreset = id => (
  VISIBILITY_PROVIDER_PRESETS.find(item => item.id === id) || VISIBILITY_PROVIDER_PRESETS.at(-1)
)

export const applyVisibilityProviderPreset = (current, id) => {
  const preset = visibilityProviderPreset(id)
  return {
    providerId: preset.id,
    baseUrl: preset.baseUrl,
    model: preset.model,
    // Never carry one provider's credential into another provider by accident.
    apiKey: preset.id === current?.providerId ? String(current?.apiKey || '') : ''
  }
}

export const validateVisibilitySamplingDraft = draft => {
  const issues = []
  const prompt = String(draft?.prompt || '').trim()
  const baseUrl = String(draft?.baseUrl || '').trim()
  const model = String(draft?.model || '').trim()
  const apiKey = String(draft?.apiKey || '').trim()
  const sampleCount = Number(draft?.sampleCount)

  if (!draft?.projectId) issues.push('请选择品牌项目')
  if (prompt.length < 2 || prompt.length > 500) issues.push('监测问题需为 2–500 个字符')
  if (!Number.isInteger(sampleCount) || sampleCount < 1 || sampleCount > 5) {
    issues.push('真实采样次数必须是 1–5')
  }
  const useUnifiedEngine = draft?.useUnifiedEngine !== false
  if (useUnifiedEngine) {
    if (!draft?.unifiedConfigured) issues.push('请先在系统设置中保存统一 AI 引擎')
  } else {
    try {
      const url = new URL(baseUrl)
      if (url.protocol !== 'https:') issues.push('模型地址必须使用 HTTPS')
    } catch {
      issues.push('请填写有效的模型地址')
    }
    if (!model) issues.push('请填写精确模型 ID')
    if (!apiKey) issues.push('请填写本次覆盖使用的 API Key')
  }
  if (draft?.confirmed !== true) issues.push('请确认本次外发边界')
  return [...new Set(issues)]
}

export const buildVisibilitySamplingRequest = draft => {
  const issues = validateVisibilitySamplingDraft(draft)
  if (issues.length) throw new Error(issues[0])
  const payload = {
    project_id: draft.projectId,
    prompt: String(draft.prompt).trim(),
    sample_count: Number(draft.sampleCount),
    aliases: asArray(draft.aliases),
    competitors: asArray(draft.competitors),
    persist: Boolean(draft.persist),
    confirm_external_call: true
  }
  if (draft.useUnifiedEngine === false) {
    payload.ai_config = {
      base_url: String(draft.baseUrl).trim(),
      api_key: String(draft.apiKey).trim(),
      model: String(draft.model).trim()
    }
  }
  return payload
}

const unwrapSamplingPayload = value => {
  const source = value && typeof value === 'object' ? value : {}
  if (source.data && typeof source.data === 'object') return source.data
  return source
}

const normalizeUsageReceipt = value => {
  const source = value && typeof value === 'object' ? value : {}
  return {
    usageStatus: String(source.usage_status || 'unknown'),
    inputTokens: numberOrNull(source.input_tokens),
    outputTokens: numberOrNull(source.output_tokens),
    totalTokens: numberOrNull(source.total_tokens),
    estimatedCost: source.estimated_cost == null ? null : String(source.estimated_cost),
    currency: String(source.currency || ''),
    costStatus: String(source.cost_status || 'unknown_usage'),
    pricingSource: String(source.pricing_source || '')
  }
}

export const normalizeVisibilitySamplingResponse = value => {
  const root = unwrapSamplingPayload(value)
  const disclosure = root.data_disclosure && typeof root.data_disclosure === 'object'
    ? root.data_disclosure
    : {}
  return {
    status: ['completed', 'partial', 'need_action'].includes(String(root.status))
      ? String(root.status)
      : 'need_action',
    needAction: Boolean(root.need_action),
    requestId: String(root.request_id || ''),
    idempotencyKey: String(root.idempotency_key || ''),
    requestedSampleCount: Math.max(0, numberOrNull(root.requested_sample_count) ?? 0),
    completedSampleCount: Math.max(0, numberOrNull(root.completed_sample_count) ?? 0),
    unattemptedSampleCount: Math.max(0, numberOrNull(root.unattempted_sample_count) ?? 0),
    persisted: Boolean(root.persisted),
    runId: root.run_id ?? null,
    createdAt: String(root.created_at || ''),
    samplingVersion: String(root.sampling_version || ''),
    executions: asArray(root.executions).map((execution, index) => ({
      index: numberOrNull(execution?.index) ?? index + 1,
      provider: String(execution?.provider || 'unknown'),
      model: String(execution?.model || 'unknown'),
      providerRequestId: String(execution?.provider_request_id || ''),
      elapsedMs: Math.max(0, numberOrNull(execution?.elapsed_ms) ?? 0),
      usage: execution?.usage && typeof execution.usage === 'object' ? execution.usage : null,
      receipt: normalizeUsageReceipt(execution?.ai_usage)
    })),
    capturedResponses: asArray(root.captured_responses).map(item => ({
      text: String(item?.text || '').trim(),
      provider: String(item?.provider || ''),
      model: String(item?.model || '')
    })).filter(item => item.text),
    analysis: root.analysis && typeof root.analysis === 'object' ? root.analysis : null,
    failure: root.failure && typeof root.failure === 'object'
      ? {
          code: String(root.failure.code || 'sampling_failed'),
          message: String(root.failure.message || '采样未完成'),
          sampleIndex: numberOrNull(root.failure.sample_index),
          outcomeUnknown: Boolean(root.failure.outcome_unknown),
          retryAfterSeconds: numberOrNull(root.failure.retry_after_seconds)
        }
      : null,
    disclosure: {
      sentToProvider: asArray(disclosure.sent_to_provider).map(String),
      transportMetadata: asArray(disclosure.sent_as_transport_metadata).map(String),
      notSentToProvider: asArray(disclosure.not_sent_to_provider).map(String),
      promptSha256: String(disclosure.prompt_sha256 || ''),
      boundary: String(disclosure.boundary || '')
    },
    requestPolicy: root.request_policy && typeof root.request_policy === 'object'
      ? {
          execution: String(root.request_policy.execution || ''),
          maxOutputTokens: numberOrNull(root.request_policy.max_output_tokens),
          timeoutSeconds: numberOrNull(root.request_policy.timeout_seconds),
          connectionTestEquivalent: Boolean(root.request_policy.connection_test_equivalent)
        }
      : {
          execution: '',
          maxOutputTokens: null,
          timeoutSeconds: null,
          connectionTestEquivalent: false
        },
    grounding: root.grounding && typeof root.grounding === 'object' ? root.grounding : {},
    retryPolicy: root.retry_policy && typeof root.retry_policy === 'object' ? root.retry_policy : {}
  }
}

export const samplingStatusLabel = value => ({
  completed: '采样完成',
  partial: '部分完成',
  need_action: '需要人工处理'
}[String(value)] || '需要人工处理')

export const buildManualSampleImport = receipt => (
  asArray(receipt?.capturedResponses).map(item => item.text).filter(Boolean).join('\n\n---\n\n')
)
