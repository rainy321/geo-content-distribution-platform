const asObject = value => (
  value && typeof value === 'object' && !Array.isArray(value) ? value : {}
)

const asArray = value => (Array.isArray(value) ? value : [])

const compactStrings = value => (
  [...new Set(asArray(value).map(item => String(item || '').trim()).filter(Boolean))]
)

const numberOrNull = value => {
  if (value == null || value === '') return null
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : null
}

const clampPercent = value => Math.max(0, Math.min(100, numberOrNull(value) ?? 0))

const booleanValue = (value, fallback = false) => {
  if (typeof value === 'boolean') return value
  if (typeof value === 'number') return value !== 0
  if (typeof value === 'string') {
    if (['true', '1', 'yes', 'passed', 'ready'].includes(value.toLowerCase())) return true
    if (['false', '0', 'no', 'blocked', 'failed'].includes(value.toLowerCase())) return false
  }
  return fallback
}

const unwrapEnvelope = value => {
  let root = asObject(value)
  for (let index = 0; index < 3; index += 1) {
    if (root.blueprint && typeof root.blueprint === 'object') return asObject(root.blueprint)
    if (root.data && typeof root.data === 'object' && !Array.isArray(root.data)) {
      root = asObject(root.data)
      continue
    }
    break
  }
  return root
}

const safeHttpUrl = value => {
  if (!value) return ''
  try {
    const url = new URL(String(value).trim())
    return ['http:', 'https:'].includes(url.protocol) ? url.href : ''
  } catch {
    return ''
  }
}

const INTENT_LABELS = {
  informational: '信息理解',
  comparison: '对比决策',
  recommendation: '推荐选择',
  transactional: '购买部署',
  navigational: '官网导航',
  troubleshooting: '问题解决'
}

export const CITEABILITY_DIMENSIONS = [
  { key: 'direct_answer', label: '答案前置' },
  { key: 'entity_consistency', label: '实体一致性' },
  { key: 'evidence_traceability', label: '证据可追溯' },
  { key: 'structural_extractability', label: '结构可提取' },
  { key: 'semantic_coverage', label: '语义覆盖' },
  { key: 'faq_decision_support', label: 'FAQ 决策支持' },
  { key: 'risk_hygiene', label: '主张风险控制' }
]

const DIMENSION_LABELS = Object.fromEntries(
  CITEABILITY_DIMENSIONS.map(item => [item.key, item.label])
)

const normalizeIssue = (item, index = 0) => {
  const source = typeof item === 'string' ? { message: item } : asObject(item)
  const priority = String(source.priority || 'P2').toUpperCase()
  return {
    code: String(source.code || `issue-${index + 1}`),
    priority: ['P0', 'P1', 'P2'].includes(priority) ? priority : 'P2',
    message: String(source.message || source.title || '需要补充信息'),
    action: String(source.action || '补充资料后重新建立蓝图。')
  }
}

const normalizeQueryItem = (item, index = 0) => {
  const source = typeof item === 'string' ? { query: item } : asObject(item)
  const intent = String(source.intent || 'informational')
  return {
    id: String(source.id || `query-${index + 1}`),
    query: String(source.query || source.question || source.text || '').trim(),
    intent,
    intentLabel: String(source.intent_label || source.intentLabel || INTENT_LABELS[intent] || intent),
    cohort: String(source.cohort || ''),
    priority: String(source.priority || '')
  }
}

export const normalizeGeoBlueprint = response => {
  const root = unwrapEnvelope(response)
  const matrix = asObject(root.query_matrix || root.queryMatrix)
  const rawQueries = asArray(matrix.items).length ? matrix.items : matrix.related_queries
  const queryItems = asArray(rawQueries).map(normalizeQueryItem).filter(item => item.query)
  const primaryIntent = String(matrix.primary_intent || matrix.primaryIntent || 'informational')
  const entity = asObject(root.entity)
  const gate = asObject(root.gate)
  const evidence = asArray(root.evidence).map((item, index) => {
    const source = asObject(item)
    return {
      id: String(source.id || `evidence-${index + 1}`),
      claim: String(source.claim || ''),
      sourceTitle: String(source.source_title || source.sourceTitle || ''),
      url: safeHttpUrl(source.canonical_url || source.url),
      sourceType: String(source.source_type || source.sourceType || 'UNKNOWN'),
      isOwned: booleanValue(source.is_owned ?? source.isOwned),
      isCompetitor: booleanValue(source.is_competitor ?? source.isCompetitor),
      verifiedAt: String(source.verified_at || source.verifiedAt || '')
    }
  })

  return {
    version: String(root.compiler_version || root.version || ''),
    topic: String(root.topic || ''),
    contentType: String(root.content_type || root.contentType || ''),
    length: numberOrNull(root.length),
    keywords: compactStrings(root.keywords),
    queryMatrix: {
      primaryIntent,
      intentLabel: String(matrix.intent_label || matrix.intentLabel || INTENT_LABELS[primaryIntent] || primaryIntent),
      intentSource: String(matrix.intent_source || matrix.intentSource || ''),
      primaryQuery: String(matrix.primary_query || matrix.primaryQuery || root.topic || ''),
      requestedCount: numberOrNull(matrix.requested_count || matrix.requestedCount),
      total: numberOrNull(matrix.total) ?? queryItems.length,
      items: queryItems,
      cohortPolicy: String(matrix.cohort_policy || matrix.cohortPolicy || '')
    },
    entity: {
      name: String(entity.name || ''),
      product: String(entity.product || ''),
      aliases: compactStrings(entity.aliases),
      website: safeHttpUrl(entity.website),
      ownedDomains: compactStrings(entity.owned_domains || entity.ownedDomains),
      competitors: asArray(entity.competitors)
    },
    evidence,
    platformProfile: asObject(root.platform_profile || root.platformProfile),
    visibilityGuidance: asObject(root.visibility_guidance || root.visibilityGuidance),
    gate: {
      ready: booleanValue(gate.ready),
      evidenceMode: String(gate.evidence_mode || gate.evidenceMode || 'guarded'),
      blockingIssues: asArray(gate.blocking_issues || gate.blockingIssues).map(normalizeIssue),
      warnings: asArray(gate.warnings).map(normalizeIssue)
    },
    methodology: asObject(root.methodology),
    instruction: String(root.instruction || '')
  }
}

const normalizeCiteability = root => {
  const raw = root.citeability || root.citability || root.ai_citeability || root.aiCiteability
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null
  const source = asObject(raw)
  const rawDimensions = asObject(source.dimensions)
  const weights = asObject(source.dimension_weights || source.dimensionWeights)
  const knownKeys = CITEABILITY_DIMENSIONS.map(item => item.key)
  const unknownKeys = Object.keys(rawDimensions).filter(key => !knownKeys.includes(key)).sort()
  const dimensionKeys = [...knownKeys.filter(key => key in rawDimensions), ...unknownKeys]
  const dimensions = dimensionKeys.map(key => ({
    key,
    label: DIMENSION_LABELS[key] || key.replaceAll('_', ' '),
    value: clampPercent(rawDimensions[key]),
    weight: Math.max(0, numberOrNull(weights[key]) ?? 0)
  }))
  const score = clampPercent(source.score)
  const passScore = clampPercent(source.pass_score ?? source.passScore ?? 75)

  return {
    version: String(source.version || ''),
    score,
    grade: String(source.grade || '—'),
    passScore,
    passed: booleanValue(source.passed, score >= passScore),
    dimensions,
    blockingIssues: asArray(source.blocking_issues || source.blockingIssues).map(normalizeIssue),
    suggestions: compactStrings(source.suggestions),
    signals: asObject(source.signals),
    citations: asArray(source.citations),
    methodology: asObject(source.methodology)
  }
}

export const normalizeGeoScoreResult = response => {
  const root = unwrapEnvelope(response)
  return {
    score: clampPercent(root.score),
    dimensions: asObject(root.dimensions),
    suggestions: compactStrings(root.suggestions),
    scoreContext: asObject(root.score_context || root.scoreContext),
    citeability: normalizeCiteability(root)
  }
}

