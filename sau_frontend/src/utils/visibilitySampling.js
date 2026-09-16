import { resolveUnifiedAIChatEndpoint } from './aiConfig.js'

const asArray = value => (Array.isArray(value) ? value : [])

const numberOrNull = value => {
  if (value == null || value === '') return null
  const number = Number(value)
  return Number.isFinite(number) ? number : null
}

const booleanOrNull = value => {
  if (value === true || value === false) return value
  return null
}

const normalizedPercent = value => {
  const number = numberOrNull(value)
  if (number == null) return null
  const percent = number >= 0 && number <= 1 ? number * 100 : number
  return Math.min(100, Math.max(0, percent))
}

const measurementReasonText = value => {
  if (typeof value === 'string') return value.trim()
  if (!value || typeof value !== 'object') return ''
  return String(value.message || value.reason || value.detail || '').trim()
}

const normalizeApiChannelAnalysis = value => {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null
  const source = value
  const status = source.status === 'available' ? 'available' : 'unavailable'
  const summarySource = status === 'available'
    && source.summary
    && typeof source.summary === 'object'
    && !Array.isArray(source.summary)
    ? source.summary
    : {}
  const confidenceSource = summarySource.confidence
    && typeof summarySource.confidence === 'object'
    && !Array.isArray(summarySource.confidence)
    ? summarySource.confidence
    : {}

  return {
    status,
    channelType: String(source.channel_type || 'provider_api'),
    metricScope: String(source.metric_scope || ''),
    consumerSurfaceVerified: source.consumer_surface_verified === true,
    consumerGeoClaimAllowed: source.consumer_geo_claim_allowed === true,
    singleCompositeScoreProduced: source.single_composite_score_produced === true,
    sourceModelCount: Math.max(0, numberOrNull(source.source_model_count) ?? 0),
    summary: {
      sampleCount: numberOrNull(summarySource.sample_count),
      mentionRate: normalizedPercent(summarySource.mention_rate),
      shareOfVoice: normalizedPercent(summarySource.share_of_voice),
      averageRank: numberOrNull(summarySource.average_rank),
      citationRate: normalizedPercent(summarySource.citation_rate),
      sentimentScore: numberOrNull(summarySource.sentiment_score),
      confidence: {
        label: String(confidenceSource.label || ''),
        reason: String(confidenceSource.reason || '')
      }
    }
  }
}

const normalizeApiChannelAnalysisUnavailableReason = value => {
  if (typeof value === 'string') {
    const message = value.trim()
    return message
      ? {
          code: 'api_channel_analysis_unavailable',
          message,
          measurementQualityStatus: '',
          channelType: 'provider_api',
          externalCallAttempted: false,
          consumerSurfaceVerified: false,
          errorType: ''
        }
      : null
  }
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null
  return {
    code: String(value.code || 'api_channel_analysis_unavailable'),
    message: String(value.message || value.reason || 'API 渠道聚合指标暂不可用。'),
    measurementQualityStatus: String(
      value.measurement_quality_status || value.measurementQualityStatus || ''
    ),
    channelType: String(value.channel_type || value.channelType || 'provider_api'),
    externalCallAttempted: value.external_call_attempted === true || value.externalCallAttempted === true,
    consumerSurfaceVerified: value.consumer_surface_verified === true || value.consumerSurfaceVerified === true,
    errorType: String(value.error_type || value.errorType || '')
  }
}

// Keep this display contract aligned with the backend sampling policy.  The
// authoritative value is still returned on every new sampling receipt.
export const VISIBILITY_SAMPLE_OUTPUT_TOKEN_LIMIT = 768
export const VISIBILITY_SAMPLE_REASONING_OUTPUT_TOKEN_LIMIT = 2048
export const VISIBILITY_MINIMUM_QUANTITATIVE_RESPONSES = 5

const boundedSampleCount = value => {
  const normalized = Number(value)
  if (!Number.isInteger(normalized)) return 1
  return Math.min(5, Math.max(1, normalized))
}

const normalizedDistinctModelCount = value => {
  const normalized = Number(value)
  if (!Number.isFinite(normalized)) return 0
  return Math.max(0, Math.floor(normalized))
}

export const visibilityQuantitativeSamplingPlan = ({
  sampleCount,
  multiModel,
  distinctConfiguredModelCount
} = {}) => {
  const configuredDistinctModelCount = normalizedDistinctModelCount(distinctConfiguredModelCount)
  const effectiveModelCount = multiModel === true ? configuredDistinctModelCount : 1
  const minimumPerRole = Math.min(5, Math.max(
    1,
    Math.ceil(VISIBILITY_MINIMUM_QUANTITATIVE_RESPONSES / Math.max(1, configuredDistinctModelCount))
  ))
  const normalizedSampleCount = boundedSampleCount(sampleCount)
  const maximumEffectiveResponseCount = normalizedSampleCount * effectiveModelCount
  const quantitativeThresholdMet = multiModel === true
    && effectiveModelCount >= 2
    && maximumEffectiveResponseCount >= VISIBILITY_MINIMUM_QUANTITATIVE_RESPONSES

  return {
    configuredDistinctModelCount,
    effectiveModelCount,
    sampleCount: normalizedSampleCount,
    minimumPerRole,
    maximumEffectiveResponseCount,
    minimumQuantitativeResponses: VISIBILITY_MINIMUM_QUANTITATIVE_RESPONSES,
    quantitativeThresholdMet,
    directionalOnly: multiModel === true && !quantitativeThresholdMet
  }
}

export const reconcileVisibilitySampleCount = ({
  sampleCount,
  multiModel,
  distinctConfiguredModelCount,
  userSelected = false
} = {}) => {
  const plan = visibilityQuantitativeSamplingPlan({
    sampleCount,
    multiModel,
    distinctConfiguredModelCount
  })
  // Once the operator deliberately chooses a value, including a cheaper value
  // below the quantitative threshold, preserve that decision. Automatic mode
  // tracks the current distinct-model count and uses the smallest viable value.
  if (multiModel === true && userSelected !== true) return plan.minimumPerRole
  return plan.sampleCount
}

export const visibilityConfiguredModelIdentity = value => {
  const role = value && typeof value === 'object' ? value : {}
  const provider = String(role.provider || '').trim().toLowerCase()
  const model = String(role.model || '').trim().toLowerCase()
  let endpoint = String(role.baseUrl || role.base_url || '').trim()
  try {
    endpoint = resolveUnifiedAIChatEndpoint(endpoint)
  } catch {
    endpoint = endpoint.replace(/\/+$/, '')
  }
  return [provider, endpoint.toLowerCase(), model].join('|')
}

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
  const multiModel = draft?.multiModel === true
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
  if (multiModel) {
    if (!useUnifiedEngine) issues.push('多模型对照仅使用系统设置中已保存的独立角色')
    if (Number(draft?.distinctConfiguredModelCount || 0) < 2) {
      issues.push('至少配置 2 个不同的 API 模型后才能运行多模型对照')
    }
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

export const buildVisibilityPanelRequest = draft => {
  const panelDraft = { ...draft, multiModel: true, useUnifiedEngine: true }
  const issues = validateVisibilitySamplingDraft(panelDraft)
  if (issues.length) throw new Error(issues[0])
  return {
    project_id: panelDraft.projectId,
    prompt: String(panelDraft.prompt).trim(),
    sample_count: Number(panelDraft.sampleCount),
    roles: asArray(panelDraft.roles).length
      ? asArray(panelDraft.roles)
      : ['writer', 'reviewer', 'evaluator'],
    aliases: asArray(panelDraft.aliases),
    competitors: asArray(panelDraft.competitors),
    persist: Boolean(panelDraft.persist),
    confirm_external_call: true
  }
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
    configuredProvider: String(root.configured_provider || ''),
    configuredModel: String(root.configured_model || ''),
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
    channelType: String(root.channel_type || 'provider_api'),
    entryPoint: String(root.entry_point || 'openai_compatible_chat_completions'),
    distinctModelCount: Math.max(0, numberOrNull(root.distinct_model_count) ?? 0),
    multiModelExecuted: Boolean(root.multi_model_executed),
    consumerParity: root.consumer_parity && typeof root.consumer_parity === 'object'
      ? root.consumer_parity
      : { status: 'unverified', claim_allowed: false },
    channelCoverage: asArray(root.channel_coverage).map(item => ({
      channelType: String(item?.channel_type || ''),
      status: String(item?.status || 'unverified'),
      sampledModelCount: Math.max(0, numberOrNull(item?.sampled_model_count) ?? 0),
      webSearchVerified: Boolean(item?.web_search_verified)
    })),
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
          outputBudgetMode: String(root.request_policy.output_budget_mode || ''),
          outputBudgetProfile: String(root.request_policy.output_budget_profile || ''),
          outputBudgetCeilingTokens: numberOrNull(root.request_policy.output_budget_ceiling_tokens),
          configuredMaxOutputTokens: numberOrNull(root.request_policy.configured_max_output_tokens),
          timeoutSeconds: numberOrNull(root.request_policy.timeout_seconds),
          connectionTestEquivalent: Boolean(root.request_policy.connection_test_equivalent)
        }
      : {
          execution: '',
          maxOutputTokens: null,
          outputBudgetMode: '',
          outputBudgetProfile: '',
          outputBudgetCeilingTokens: null,
          configuredMaxOutputTokens: null,
          timeoutSeconds: null,
          connectionTestEquivalent: false
        },
    grounding: root.grounding && typeof root.grounding === 'object' ? root.grounding : {},
    retryPolicy: root.retry_policy && typeof root.retry_policy === 'object' ? root.retry_policy : {}
  }
}

const normalizePanelMeasurementQuality = (root, targets) => {
  const rawSource = root.measurement_quality ?? root.measurementQuality
  const source = rawSource && typeof rawSource === 'object' ? rawSource : {}
  const requestedFromTargets = targets.reduce((total, target) => total + target.requestedSampleCount, 0)
  const validFromTargets = targets.reduce((total, target) => total + target.completedSampleCount, 0)
  const requestedResponseCount = Math.max(
    0,
    numberOrNull(source.requested_response_count ?? source.requestedResponseCount)
      ?? (requestedFromTargets > 0 ? requestedFromTargets : null)
      ?? numberOrNull(root.requested_role_count)
      ?? targets.length
  )
  const validResponseCount = Math.max(
    0,
    numberOrNull(source.valid_response_count ?? source.validResponseCount)
      ?? (targets.length ? validFromTargets : null)
      ?? numberOrNull(root.sampled_role_count)
      ?? 0
  )
  const completeTargetCount = targets.filter(target => (
    target.requestedSampleCount > 0
    && target.completedSampleCount >= target.requestedSampleCount
    && !target.failure
  )).length
  const distinctModelCount = Math.max(0, numberOrNull(root.distinct_model_count ?? root.distinctModelCount) ?? 0)
  const comparisonEligible = Boolean(root.comparison_eligible ?? root.comparisonEligible)
  const minimumDistinctModels = Math.max(
    2,
    numberOrNull(source.minimum_distinct_models ?? source.minimumDistinctModels) ?? 2
  )
  const minimumQuantitativeResponses = Math.max(
    1,
    numberOrNull(source.minimum_quantitative_responses ?? source.minimumQuantitativeResponses) ?? 5
  )

  let derivedStatus = 'insufficient'
  if (validResponseCount === 0) derivedStatus = 'invalid'
  else if (distinctModelCount >= minimumDistinctModels && completeTargetCount >= minimumDistinctModels) {
    derivedStatus = validResponseCount >= minimumQuantitativeResponses && comparisonEligible ? 'quantitative' : 'directional'
  }
  const reportedStatus = String(source.status || '').trim().toLowerCase()
  let status = ['invalid', 'insufficient', 'directional', 'quantitative'].includes(reportedStatus)
    ? reportedStatus
    : derivedStatus
  if (validResponseCount === 0) status = 'invalid'
  else if (status === 'quantitative' && (validResponseCount < minimumQuantitativeResponses || !comparisonEligible)) status = derivedStatus
  else if (status === 'directional' && (distinctModelCount < minimumDistinctModels || completeTargetCount < minimumDistinctModels)) status = 'insufficient'

  const reportedRate = normalizedPercent(source.valid_response_rate ?? source.validResponseRate)
  const validResponseRate = reportedRate ?? (
    requestedResponseCount > 0
      ? Math.min(100, validResponseCount / requestedResponseCount * 100)
      : null
  )
  const reportedScoreAllowed = booleanOrNull(source.score_allowed ?? source.scoreAllowed)
  const reportedApiClaimAllowed = booleanOrNull(source.api_channel_claim_allowed ?? source.apiChannelClaimAllowed)
  const reportedConsumerClaimAllowed = booleanOrNull(
    source.consumer_geo_claim_allowed
    ?? source.consumerGeoClaimAllowed
    ?? root.consumer_geo_claim_allowed
    ?? root.consumerGeoClaimAllowed
  )
  const consumerParityClaim = booleanOrNull(root.consumer_parity?.claim_allowed ?? root.consumerParity?.claim_allowed)
  const confidenceLevel = String(source.confidence_level ?? source.confidenceLevel ?? '').trim().toLowerCase()
    || (status === 'invalid' ? 'none' : status === 'directional' ? 'low' : status === 'quantitative' ? (validResponseCount >= 10 ? 'high' : 'medium') : 'low')
  const reportedReasons = [
    ...asArray(source.reasons),
    ...asArray(source.blocking_reasons ?? source.blockingReasons),
    ...asArray(source.unquantifiable_reasons ?? source.unquantifiableReasons)
  ].map(measurementReasonText).filter(Boolean)
  const singleReason = measurementReasonText(source.reason)
  const reasons = [...new Set(singleReason ? [...reportedReasons, singleReason] : reportedReasons)]

  return {
    status,
    // A zero-evidence or non-quantitative panel is never scoreable, even if an
    // older/malformed backend accidentally reports score_allowed=true.
    scoreAllowed: validResponseCount > 0
      && status === 'quantitative'
      && (reportedScoreAllowed ?? derivedStatus === 'quantitative')
      && (reportedApiClaimAllowed ?? true),
    validResponseCount,
    requestedResponseCount,
    validResponseRate,
    confidenceLevel,
    consumerGeoClaimAllowed: reportedConsumerClaimAllowed ?? consumerParityClaim ?? false,
    completeTargetCount,
    validModelCount: Math.max(0, numberOrNull(source.valid_model_count ?? source.validModelCount) ?? distinctModelCount),
    minimumDistinctModels,
    minimumQuantitativeResponses,
    directionalComparisonAllowed: booleanOrNull(
      source.directional_comparison_allowed ?? source.directionalComparisonAllowed
    ) ?? comparisonEligible,
    apiChannelClaimAllowed: status === 'quantitative' && (reportedApiClaimAllowed ?? true),
    reasons,
    source: Object.keys(source).length ? 'reported' : 'derived'
  }
}

export const normalizeVisibilityPanelResponse = value => {
  const root = unwrapSamplingPayload(value)
  const manifest = root.manifest && typeof root.manifest === 'object' ? root.manifest : {}
  const manifestByRole = new Map(asArray(manifest.targets).map(item => [String(item?.role || ''), item]))
  const knownIdentity = (value, fallback) => {
    const normalized = String(value || '').trim()
    return !normalized || normalized.toLowerCase() === 'unknown'
      ? String(fallback || '').trim()
      : normalized
  }
  const targets = asArray(root.targets).map(item => {
    const role = String(item?.role || '')
    const frozen = manifestByRole.get(role) || {}
    return {
      role,
      executionStatus: String(item?.execution_status || 'skipped'),
      status: String(item?.status || 'not_configured'),
      provider: knownIdentity(item?.provider, frozen?.provider),
      model: knownIdentity(item?.model, frozen?.model),
      requestId: String(item?.request_id || frozen?.subrequest_id || ''),
      runId: item?.run_id ?? null,
      analysisAvailable: Boolean(item?.analysis && typeof item.analysis === 'object'),
      completedSampleCount: Math.max(0, numberOrNull(item?.completed_sample_count) ?? 0),
      requestedSampleCount: Math.max(0, numberOrNull(item?.requested_sample_count) ?? 0),
      skipReason: String(item?.skip_reason || ''),
      channelType: String(item?.channel_type || 'provider_api'),
      webSearchVerified: Boolean(item?.web_search_verified),
      consumerSurfaceVerified: Boolean(item?.consumer_surface_verified),
      failure: item?.failure && typeof item.failure === 'object' ? item.failure : null,
      capturedResponses: asArray(item?.analysis?.samples)
    }
  })
  const measurementQuality = normalizePanelMeasurementQuality(root, targets)
  const apiChannelAnalysis = normalizeApiChannelAnalysis(
    root.api_channel_analysis ?? root.apiChannelAnalysis
  )
  const apiChannelAnalysisUnavailableReason = normalizeApiChannelAnalysisUnavailableReason(
    root.api_channel_analysis_unavailable_reason ?? root.apiChannelAnalysisUnavailableReason
  )
  return {
    status: ['completed', 'partial', 'need_action'].includes(String(root.status))
      ? String(root.status)
      : 'need_action',
    needAction: Boolean(root.need_action),
    requestId: String(root.request_id || ''),
    idempotencyKey: String(root.idempotency_key || ''),
    requestedRoleCount: Math.max(0, numberOrNull(root.requested_role_count) ?? targets.length),
    executedRoleCount: Math.max(0, numberOrNull(root.executed_role_count) ?? 0),
    sampledRoleCount: Math.max(0, numberOrNull(root.sampled_role_count) ?? 0),
    distinctModelCount: Math.max(0, numberOrNull(root.distinct_model_count) ?? 0),
    multiModelExecuted: Boolean(root.multi_model_executed),
    comparisonEligible: Boolean(root.comparison_eligible),
    targets,
    manifest,
    outcomeUnknown: Boolean(
      root.outcome_unknown
      || targets.some(target => (
        target.executionStatus === 'outcome_unknown'
        || target.failure?.outcome_unknown
        || target.failure?.outcomeUnknown
      ))
    ),
    failure: root.failure && typeof root.failure === 'object' ? root.failure : null,
    retryPolicy: root.retry_policy && typeof root.retry_policy === 'object' ? root.retry_policy : {},
    recoveryPolicy: root.recovery_policy && typeof root.recovery_policy === 'object' ? root.recovery_policy : {},
    channelCoverage: asArray(root.channel_coverage).map(item => ({
      channelType: String(item?.channel_type || ''),
      status: String(item?.status || 'unverified'),
      sampledModelCount: Math.max(0, numberOrNull(item?.sampled_model_count) ?? 0),
      webSearchVerified: Boolean(item?.web_search_verified)
    })),
    consumerParity: root.consumer_parity && typeof root.consumer_parity === 'object'
      ? root.consumer_parity
      : { status: 'unverified', claim_allowed: false },
    claimBoundary: String(root.claim_boundary || ''),
    measurementQuality,
    apiChannelAnalysis,
    apiChannelAnalysisUnavailableReason
  }
}

export const visibilityPanelMeasurementPresentation = value => {
  const receipt = value && typeof value === 'object' ? value : {}
  const quality = receipt.measurementQuality && typeof receipt.measurementQuality === 'object'
    ? receipt.measurementQuality
    : normalizePanelMeasurementQuality(receipt, asArray(receipt.targets))
  const status = ['invalid', 'insufficient', 'directional', 'quantitative'].includes(quality.status)
    ? quality.status
    : 'insufficient'
  const confidenceLevel = String(quality.confidenceLevel || 'none').toLowerCase()
  const confidenceLabel = ({
    high: '高',
    medium: '中',
    low: '低',
    none: '不可判定',
    unknown: '不可判定'
  })[confidenceLevel] || '不可判定'
  const statusPresentation = {
    invalid: {
      label: '不可量化',
      summary: '没有有效回答，本批次不能计算品牌可见度，更不能生成总体 GEO 可用分。'
    },
    insufficient: {
      label: '证据不足',
      summary: '有效回答或独立完整模型不足，本批次不生成对照分。'
    },
    directional: {
      label: '仅方向性判断',
      summary: `已有至少 ${quality.minimumDistinctModels || 2} 个完整独立模型，但有效回答少于 ${quality.minimumQuantitativeResponses || 5} 条，只能观察方向，不能当作稳定分数。`
    },
    quantitative: {
      label: 'API 渠道可量化',
      summary: '有效回答达到门槛且模型对照合格，可量化本批次 API 渠道表现。'
    }
  }[status]

  const reasons = [...asArray(quality.reasons)]
  if (status === 'invalid' && !reasons.length) {
    reasons.push('0 条有效回答：品牌提及率、声量份额、推荐位次都没有观察值。')
  } else if (status === 'insufficient' && !reasons.length) {
    reasons.push(`仅 ${quality.completeTargetCount || 0} 个调用目标完整返回；至少需要 ${quality.minimumDistinctModels || 2} 个独立完整模型。`)
  } else if (status === 'directional' && !reasons.length) {
    reasons.push(`当前只有 ${quality.validResponseCount || 0} 条有效回答；达到 ${quality.minimumQuantitativeResponses || 5} 条且对照合格后才允许量化。`)
  }
  if (receipt.comparisonEligible === false && status !== 'invalid') {
    reasons.push('comparison_eligible=false：模型身份或采样口径尚未形成合格对照。')
  }
  if (!quality.consumerGeoClaimAllowed) {
    reasons.push('缺少消费者网页 / App 入口证据，不能生成总体 GEO 可用分。')
  }

  return {
    status,
    tone: status,
    label: statusPresentation.label,
    summary: statusPresentation.summary,
    scoreAllowed: Boolean(quality.scoreAllowed),
    overallGeoScoreAllowed: Boolean(quality.scoreAllowed && quality.consumerGeoClaimAllowed),
    overallGeoScoreLabel: quality.scoreAllowed && quality.consumerGeoClaimAllowed ? '证据允许' : '—',
    overallGeoScoreHint: !quality.scoreAllowed
      ? '样本证据不足，不显示数值'
      : !quality.consumerGeoClaimAllowed
        ? '仅 API 渠道，缺少 App / Web 证据'
        : '证据门槛允许出分',
    validResponseCount: Math.max(0, numberOrNull(quality.validResponseCount) ?? 0),
    requestedResponseCount: Math.max(0, numberOrNull(quality.requestedResponseCount) ?? 0),
    validResponseRate: normalizedPercent(quality.validResponseRate),
    confidenceLevel,
    confidenceLabel,
    consumerGeoClaimAllowed: Boolean(quality.consumerGeoClaimAllowed),
    reasons: [...new Set(reasons.map(measurementReasonText).filter(Boolean))],
    scopeLabel: 'provider_api · 仅 API 渠道'
  }
}

export const visibilityPanelTargetPresentation = value => {
  const target = value && typeof value === 'object' ? value : {}
  const completed = Math.max(0, numberOrNull(target.completedSampleCount ?? target.completed_sample_count) ?? 0)
  const requested = Math.max(0, numberOrNull(target.requestedSampleCount ?? target.requested_sample_count) ?? 0)
  const failure = target.failure && typeof target.failure === 'object' ? target.failure : null
  const executionStatus = String(target.executionStatus ?? target.execution_status ?? '').trim()
  const outcomeUnknown = Boolean(
    target.outcomeUnknown
    || target.outcome_unknown
    || failure?.outcomeUnknown
    || failure?.outcome_unknown
    || executionStatus === 'outcome_unknown'
  )
  const analysisAvailable = Boolean(target.analysisAvailable ?? target.analysis)
  const archivedSuffix = analysisAvailable ? '，已完成本地分析' : ''

  if (requested > 0 && completed >= requested && !failure) {
    return {
      tone: 'complete',
      label: '完整完成',
      detail: `${completed} 条有效回答已保留${archivedSuffix}`
    }
  }
  if (completed > 0) {
    return {
      tone: 'partial',
      label: '部分完成',
      detail: `已保留 ${completed} 条有效回答${archivedSuffix}`
    }
  }
  if (outcomeUnknown) {
    return {
      tone: 'unknown',
      label: '结果未知',
      detail: '尚未取得可确认的有效回答'
    }
  }
  if (failure || executionStatus === 'failed') {
    return {
      tone: 'failed',
      label: '调用失败',
      detail: '未取得有效回答'
    }
  }
  if (executionStatus === 'in_progress') {
    return { tone: 'pending', label: '处理中', detail: '正在等待原请求返回' }
  }
  if (executionStatus === 'pending') {
    return { tone: 'pending', label: '等待执行', detail: '尚未开始调用' }
  }
  if (executionStatus === 'skipped') {
    return { tone: 'skipped', label: '未调用', detail: '该目标未进入本次调用' }
  }
  return { tone: 'unknown', label: '状态未知', detail: '后端未返回完整状态' }
}

export const samplingStatusLabel = value => ({
  completed: '采样完成',
  partial: '部分完成',
  need_action: '需要人工处理'
}[String(value)] || '需要人工处理')

export const buildManualSampleImport = receipt => (
  asArray(receipt?.capturedResponses).map(item => item.text).filter(Boolean).join('\n\n---\n\n')
)
