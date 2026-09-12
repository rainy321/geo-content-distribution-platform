const asObject = value => (
  value && typeof value === 'object' && !Array.isArray(value) ? value : {}
)

const normalizeWarnings = (value) => {
  const source = Array.isArray(value) ? value : (value ? [value] : [])
  return source.map(item => String(item || '').trim()).filter(Boolean)
}

export const toSafeHttpUrl = (value) => {
  const candidate = String(value || '').trim()
  if (!candidate) return ''
  try {
    const parsed = new URL(candidate)
    return parsed.protocol === 'https:' || parsed.protocol === 'http:' ? parsed.href : ''
  } catch {
    return ''
  }
}

export const normalizeGenerationSources = (value) => {
  const source = Array.isArray(value) ? value : (value ? [value] : [])
  const normalized = source.map((item, index) => {
    if (typeof item === 'string') {
      const label = item.trim()
      return label ? { title: label, url: toSafeHttpUrl(label) } : null
    }
    const entry = asObject(item)
    const rawUrl = entry.url || entry.href || entry.link || ''
    const url = toSafeHttpUrl(rawUrl)
    const title = String(
      entry.title || entry.name || entry.label || entry.source || rawUrl || `来源 ${index + 1}`
    ).trim()
    return title || url ? { title: title || url, url } : null
  }).filter(Boolean)

  const seen = new Set()
  return normalized.filter((item) => {
    const key = `${item.url || ''}\u0000${item.title.toLocaleLowerCase()}`
    if (seen.has(key)) return false
    seen.add(key)
    return true
  })
}

const firstAvailableSources = (...candidates) => {
  for (const candidate of candidates) {
    const normalized = normalizeGenerationSources(candidate)
    if (normalized.length) return normalized
  }
  return []
}

const AGENT_STAGE_DEFINITIONS = Object.freeze([
  Object.freeze({ role: 'writer', code: 'WRITE', label: '写稿' }),
  Object.freeze({ role: 'reviewer', code: 'REVIEW', label: 'GEO 调优' }),
  Object.freeze({ role: 'evaluator', code: 'EVAL', label: '独立评估' })
])

const firstObject = (...candidates) => (
  candidates.find(candidate => (
    candidate
    && typeof candidate === 'object'
    && !Array.isArray(candidate)
    && Object.keys(candidate).length > 0
  )) || {}
)

const firstBoolean = (...candidates) => {
  const value = candidates.find(candidate => typeof candidate === 'boolean')
  return typeof value === 'boolean' ? value : null
}

const stageStatus = ({ recorded, invoked, completed, nonIndependent }) => {
  if (invoked && completed) return { key: 'completed', label: '已完成' }
  if (invoked) return { key: 'incomplete', label: '调用未完成' }
  if (nonIndependent) return { key: 'non-independent', label: '未调用 · 非独立' }
  if (recorded) return { key: 'not-invoked', label: '未调用' }
  return { key: 'unrecorded', label: '未记录' }
}

const normalizeEvaluationOpinion = value => {
  const source = asObject(value)
  const verdict = ['pass', 'revise', 'block'].includes(String(source.verdict || '').toLowerCase())
    ? String(source.verdict).toLowerCase()
    : ''
  const explicitHumanReview = firstBoolean(
    source.requires_human_review,
    source.requiresHumanReview
  )
  return {
    verdict,
    verdictLabel: {
      pass: '通过',
      revise: '建议修改',
      block: '建议阻断'
    }[verdict] || '无结论',
    requiresHumanReview: explicitHumanReview ?? (verdict ? verdict !== 'pass' : null),
    summary: String(source.summary || '').trim(),
    issues: (Array.isArray(source.issues) ? source.issues : []).map((item) => {
      if (typeof item === 'string') return { severity: '', message: item.trim() }
      const issue = asObject(item)
      return {
        severity: String(issue.severity || '').trim().toLowerCase(),
        message: String(issue.message || '').trim()
      }
    }).filter(item => item.message)
  }
}

const normalizeAgentStage = ({
  definition,
  value,
  recorded,
  defaultProvider = '',
  defaultModel = '',
  generated = false
}) => {
  const source = asObject(value)
  const fallback = Boolean(source.fallback)
  const sameAsWriter = Boolean(source.same_as_writer ?? source.sameAsWriter)
  const sameAsReviewer = Boolean(source.same_as_reviewer ?? source.sameAsReviewer)
  const mode = String(source.mode || '').trim().toLowerCase()
  const invoked = firstBoolean(source.invoked) ?? (
    definition.role === 'writer' && generated
  )
  const completed = firstBoolean(source.completed) ?? (
    definition.role === 'writer' && invoked
  )
  const nonIndependent = definition.role !== 'writer' && (
    fallback
    || sameAsWriter
    || sameAsReviewer
    || mode === 'non_independent'
    || mode === 'writer_only'
  )
  const status = stageStatus({ recorded, invoked, completed, nonIndependent })
  const diff = asObject(source.diff)
  const selectedStage = String(source.selected || '').trim().toLowerCase()
  const opinion = normalizeEvaluationOpinion(source.opinion)

  return {
    ...definition,
    recorded,
    invoked,
    completed,
    configured: Boolean(source.configured),
    fallback,
    nonIndependent,
    sameAsWriter,
    sameAsReviewer,
    provider: String(source.provider || defaultProvider || '').trim(),
    model: String(source.model || defaultModel || '').trim(),
    providerRequestId: String(
      source.provider_request_id || source.providerRequestId || ''
    ).trim(),
    elapsedMs: source.elapsed_ms ?? source.elapsedMs ?? null,
    message: String(source.message || '').trim(),
    status: status.key,
    statusLabel: status.label,
    selected: selectedStage === definition.role,
    changed: typeof diff.changed === 'boolean' ? diff.changed : null,
    changedFields: Array.isArray(diff.changed_fields)
      ? diff.changed_fields.map(item => String(item || '').trim()).filter(Boolean)
      : [],
    opinion
  }
}

export const normalizeGenerationAgentPipeline = (metadata, article = {}) => {
  const source = asObject(metadata)
  const result = asObject(source.result)
  const nestedGeneration = asObject(result.generation)
  const pipeline = firstObject(
    source.agent_pipeline,
    nestedGeneration.agent_pipeline,
    result.agent_pipeline
  )
  const rawStages = Array.isArray(pipeline.stages) ? pipeline.stages : []
  const stagesByRole = new Map(rawStages.map((item) => [
    String(asObject(item).role || '').trim().toLowerCase(),
    asObject(item)
  ]))
  const review = firstObject(
    stagesByRole.get('reviewer'),
    source.review,
    nestedGeneration.review,
    result.review
  )
  const evaluation = firstObject(
    stagesByRole.get('evaluator'),
    source.evaluation,
    nestedGeneration.evaluation,
    result.evaluation
  )
  const writer = firstObject(stagesByRole.get('writer'))
  const engine = String(
    source.actual_engine || source.engine || nestedGeneration.engine || ''
  ).trim()
  const model = String(
    source.engine_version || source.version || nestedGeneration.engine_version || ''
  ).trim()
  const generated = Boolean(String(article.content || result.content || '').trim())
  const hasPipelineRecord = Boolean(
    rawStages.length || Object.keys(review).length || Object.keys(evaluation).length
  )
  const values = { writer, reviewer: review, evaluator: evaluation }
  const stages = AGENT_STAGE_DEFINITIONS.map(definition => normalizeAgentStage({
    definition,
    value: values[definition.role],
    recorded: rawStages.some(item => (
      String(asObject(item).role || '').trim().toLowerCase() === definition.role
    )) || (
      definition.role === 'reviewer'
        ? Object.keys(review).length > 0
        : definition.role === 'evaluator'
          ? Object.keys(evaluation).length > 0
          : hasPipelineRecord
    ),
    defaultProvider: definition.role === 'writer' ? engine : '',
    defaultModel: definition.role === 'writer' ? model : '',
    generated
  }))
  const rawIndependence = firstObject(pipeline.model_independence)
  const invokedStages = stages.filter(stage => stage.invoked)
  const identities = new Set(invokedStages
    .filter(stage => stage.provider && stage.model)
    .map(stage => `${stage.provider.toLocaleLowerCase()}\u0000${stage.model.toLocaleLowerCase()}`))
  const invokedStageCount = invokedStages.length
  const distinctModelCount = identities.size

  return {
    available: hasPipelineRecord,
    version: String(pipeline.version || '').trim(),
    stages,
    invokedStageCount,
    distinctModelCount,
    multiAgentExecuted: invokedStageCount >= 2,
    multiModelExecuted: distinctModelCount >= 2,
    selectedStage: String(pipeline.selected_stage || '').trim().toLowerCase(),
    claimBoundary: String(rawIndependence.claim_boundary || '').trim()
  }
}

export const normalizeGenerationReceipt = (
  metadata,
  article = {},
  requestId = '',
  elapsedMs = null
) => {
  const source = asObject(metadata)
  const result = asObject(source.result)
  const nestedGeneration = asObject(result.generation)
  const fallback = asObject(source.fallback)
  const nestedFallback = asObject(nestedGeneration.fallback)
  const warnings = [
    ...normalizeWarnings(source.warnings),
    ...normalizeWarnings(nestedGeneration.warnings),
    ...normalizeWarnings(result.warnings)
  ]
  const sources = firstAvailableSources(source.sources, result.sources, article.sources)
  const summary = article.summary ?? result.summary ?? ''
  const tags = article.tags ?? result.tags ?? []
  const content = article.content ?? result.content ?? ''
  const faq = article.faq ?? result.faq

  if (!String(summary || '').trim()) warnings.push('生成结果未提供摘要，可在编辑器中补充。')
  if (!Array.isArray(tags) || tags.length === 0) warnings.push('生成结果未提供标签，可在编辑器中补充。')
  const hasFaq = (Array.isArray(faq) && faq.length > 0)
    || /(?:FAQ|常见问题|常见问答)/i.test(String(content || ''))
  if (!hasFaq) warnings.push('生成结果未提供 FAQ，可在正文中补充。')
  if (!sources.length) warnings.push('生成结果未提供可核验来源。')

  const agentPipeline = normalizeGenerationAgentPipeline(source, article)

  return {
    runId: source.run_id ?? source.id ?? nestedGeneration.run_id ?? null,
    engine: source.actual_engine || source.engine || source.engine_name || source.provider
      || nestedGeneration.engine || null,
    version: source.version || source.engine_version || nestedGeneration.engine_version || null,
    elapsedMs: source.elapsed_ms ?? source.elapsed ?? nestedGeneration.elapsed_ms ?? elapsedMs ?? null,
    tokenUsage: source.usage ?? source.token_usage ?? source.tokens ?? source.token
      ?? nestedGeneration.usage ?? null,
    usageStatus: source.usage_status || nestedGeneration.usage_status || '',
    traceId: source.trace_id || source.trace || nestedGeneration.trace_id || requestId || null,
    fallbackUsed: Boolean(
      source.fallback_used ?? source.used_fallback ?? fallback.used
      ?? nestedGeneration.fallback_used ?? nestedFallback.used ?? false
    ),
    fallbackReason: source.fallback_reason || fallback.reason
      || nestedGeneration.fallback_reason || nestedFallback.reason || '',
    fallbackFrom: source.fallback_from || fallback.from
      || nestedGeneration.fallback_from || nestedFallback.from || '',
    warnings: [...new Set(warnings)],
    providerGeoScore: source.provider_geo_score ?? nestedGeneration.provider_geo_score
      ?? result.provider_geo_score ?? article.provider_geo_score ?? null,
    diversityProfile: asObject(source.diversity_profile || nestedGeneration.diversity_profile),
    aiRole: asObject(source.ai_role || nestedGeneration.ai_role),
    agentPipeline,
    sources
  }
}
