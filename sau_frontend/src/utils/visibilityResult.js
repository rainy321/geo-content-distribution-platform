const asArray = value => {
  if (Array.isArray(value)) return value
  if (value == null || value === '') return []
  return [value]
}

const compactStrings = value => (
  [...new Set(asArray(value)
    .flatMap(item => typeof item === 'string' ? item.split(/[,，]/) : [])
    .map(item => item.trim())
    .filter(Boolean))]
)

const numberOrNull = value => {
  if (value == null || value === '' || value === '—') return null
  const parsed = Number.parseFloat(String(value).replace('%', ''))
  return Number.isFinite(parsed) ? parsed : null
}

const percentOrNull = value => {
  const parsed = numberOrNull(value)
  if (parsed == null) return null
  if (typeof value === 'string' && value.includes('%')) return parsed
  return parsed > 0 && parsed <= 1 ? parsed * 100 : parsed
}

const first = (source, keys, fallback = null) => {
  for (const key of keys) {
    if (source?.[key] != null) return source[key]
  }
  return fallback
}

const booleanValue = value => {
  if (typeof value === 'boolean') return value
  if (typeof value === 'number') return value !== 0
  if (typeof value === 'string') return ['true', '1', 'yes', 'mentioned'].includes(value.toLowerCase())
  return false
}

export const toSafeVisibilityUrl = value => {
  if (!value) return ''
  try {
    const url = new URL(String(value).trim())
    return ['http:', 'https:'].includes(url.protocol) ? url.href : ''
  } catch {
    return ''
  }
}

const urlDomain = value => {
  const safeUrl = toSafeVisibilityUrl(value)
  if (!safeUrl) return ''
  try {
    return new URL(safeUrl).hostname.replace(/^www\./, '')
  } catch {
    return ''
  }
}

const normalizeCitation = (citation, index = 0) => {
  const source = typeof citation === 'string' ? { url: citation, raw: citation } : (citation || {})
  const rawUrl = first(source, ['normalized_url', 'canonical_url', 'url', 'href'], '')
  const rawValues = first(source, ['raw_urls'], [])
  const raw = first(
    source,
    ['raw', 'raw_url', 'text', 'citation', 'label', 'title'],
    asArray(rawValues).join('、') || rawUrl || `引用 ${index + 1}`
  )
  const safeUrl = toSafeVisibilityUrl(rawUrl)

  return {
    id: first(source, ['id'], `citation-${index + 1}`),
    raw: String(raw || ''),
    url: safeUrl,
    domain: String(first(source, ['domain', 'host'], urlDomain(safeUrl)) || ''),
    count: numberOrNull(first(source, ['count', 'occurrences', 'frequency'], 1)) ?? 1,
    isOwned: booleanValue(first(source, ['is_owned', 'owned', 'isOwned'], false)),
    isCompetitor: booleanValue(first(source, ['is_competitor', 'competitor_owned', 'isCompetitor'], false)),
    sourceType: String(first(source, ['source_type', 'classification'], '') || ''),
    samples: asArray(first(source, ['samples', 'sample_ids', 'sample_indexes'], []))
  }
}

const normalizeEvidence = (evidence, index = 0) => {
  const source = typeof evidence === 'string' ? { quote: evidence } : (evidence || {})
  return {
    id: first(source, ['id'], `evidence-${index + 1}`),
    type: String(first(source, ['type', 'kind', 'signal'], '品牌证据')),
    quote: String(first(source, ['quote', 'snippet', 'text', 'matched_text'], '') || ''),
    start: numberOrNull(first(source, ['start', 'start_index', 'offset_start'])),
    end: numberOrNull(first(source, ['end', 'end_index', 'offset_end']))
  }
}

const normalizeCompetitorMention = (item, index = 0) => {
  const source = typeof item === 'string' ? { name: item } : (item || {})
  return {
    id: first(source, ['id'], `competitor-${index + 1}`),
    name: String(first(source, ['name', 'entity', 'brand'], `竞品 ${index + 1}`)),
    mentions: numberOrNull(first(source, ['mentions', 'mention_count', 'occurrence_count', 'count'], 1)) ?? 1,
    rank: numberOrNull(first(source, ['rank', 'recommendation_rank', 'position']))
  }
}

const normalizeSample = (sample, index = 0) => {
  const source = typeof sample === 'string' ? { text: sample } : (sample || {})
  const citations = asArray(first(source, ['citations', 'normalized_citations', 'sources'], []))
    .map(normalizeCitation)
  const evidence = asArray(first(source, ['evidence', 'evidences', 'brand_evidence', 'matched_evidence'], []))
    .map(normalizeEvidence)
    .filter(item => item.quote)

  return {
    id: first(source, ['id', 'sample_id'], `sample-${index + 1}`),
    index: numberOrNull(first(source, ['index', 'sample_index', 'order'], index + 1)) ?? index + 1,
    provider: String(first(source, ['provider', 'platform'], '') || ''),
    model: String(first(source, ['model', 'model_name'], '') || ''),
    text: String(first(source, ['text', 'response', 'answer', 'response_text'], '') || ''),
    brandMentioned: booleanValue(first(source, ['brand_mentioned', 'mentioned', 'brandMentioned'], false)),
    recommendationRank: numberOrNull(first(source, ['recommendation_rank', 'brand_rank', 'rank', 'position'])),
    matchedAliases: compactStrings(first(source, ['matched_aliases', 'aliases', 'brand_matches'], [])),
    competitorMentions: asArray(first(source, ['competitor_mentions', 'competitors', 'competitor_evidence'], []))
      .map(normalizeCompetitorMention),
    citations,
    evidence,
    warnings: compactStrings(first(source, ['warnings', 'warning'], []))
  }
}

const normalizeEntity = (entity, index = 0) => {
  const source = typeof entity === 'string' ? { name: entity } : (entity || {})
  return {
    id: first(source, ['id'], `entity-${index + 1}`),
    name: String(first(source, ['name', 'entity', 'brand'], `实体 ${index + 1}`)),
    type: String(first(source, ['type', 'entity_type', 'role'], index === 0 ? 'brand' : 'competitor')),
    mentions: numberOrNull(first(source, ['mentions', 'mention_count', 'mentioned_count', 'count'], 0)) ?? 0,
    mentionRate: percentOrNull(first(source, ['mention_rate', 'brand_mention_rate', 'visibility_rate'])),
    shareOfVoice: percentOrNull(first(source, ['share_of_voice', 'sov', 'voice_share'])),
    averageRank: numberOrNull(first(source, ['average_rank', 'average_recommendation_rank', 'avg_rank']))
  }
}

const confidenceObject = value => {
  const source = typeof value === 'string' ? { level: value } : (value || {})
  const level = String(first(source, ['level', 'grade', 'value'], 'unknown')).toLowerCase()
  const labels = { high: '高', medium: '中', low: '低', unknown: '待判断' }
  return {
    level,
    label: String(first(source, ['label'], labels[level] || level)),
    reason: String(first(source, ['reason', 'description', 'message'], '') || '')
  }
}

const sampleSemanticsLabel = (value, sampleCount) => {
  const code = String(value || '').trim()
  const labels = {
    NO_DATA: '当前批次没有有效样本。',
    SINGLE_SAMPLE: '单一样本观察，仅用于核对规则命中，不代表稳定趋势。',
    SINGLE_OBSERVATION: '单一样本观察，仅用于核对规则命中，不代表稳定趋势。',
    MEAN_WITH_DISPERSION: '多样本均值与离散度观察；样本不足 5 条，暂不计算 95% 区间。',
    MEAN_WITH_95_CI: '多样本均值，并结合 95% 置信区间判断本批次稳定性。'
  }
  if (labels[code]) return labels[code]
  if (code) return code
  if (sampleCount === 1) return labels.SINGLE_OBSERVATION
  if (sampleCount > 1 && sampleCount < 5) return labels.MEAN_WITH_DISPERSION
  if (sampleCount >= 5) return labels.MEAN_WITH_95_CI
  return labels.NO_DATA
}

export const parseVisibilityResponses = value => (
  String(value || '')
    .split(/\r?\n\s*-{3,}\s*\r?\n/g)
    .map(item => item.trim())
    .filter(Boolean)
)

export const buildVisibilityPayload = ({
  projectId,
  prompt,
  responses,
  aliases,
  competitors,
  provider = '',
  model = '',
  persist = true
}) => ({
  project_id: projectId,
  prompt: String(prompt || '').trim(),
  responses: asArray(responses)
    .map((item) => {
      const source = typeof item === 'object' && item !== null ? item : { text: item }
      const normalized = { text: String(source.text || '').trim() }
      const sampleProvider = String(source.provider || provider || '').trim()
      const sampleModel = String(source.model || model || '').trim()
      if (sampleProvider) normalized.provider = sampleProvider
      if (sampleModel) normalized.model = sampleModel
      return normalized
    })
    .filter(item => item.text),
  aliases: compactStrings(aliases),
  competitors: compactStrings(competitors),
  persist: Boolean(persist)
})

const unwrapApiPayload = payload => payload?.data?.data ?? payload?.data ?? payload ?? {}

export const normalizeVisibilityHistory = payload => {
  const container = unwrapApiPayload(payload)
  const rows = Array.isArray(container)
    ? container
    : asArray(first(container, ['runs', 'items', 'history', 'results'], []))

  return rows.map((item, index) => {
    const source = item || {}
    const summary = first(source, ['summary', 'metrics', 'overview'], source) || {}
    return {
      id: first(source, ['run_id', 'id'], `run-${index + 1}`),
      projectId: first(source, ['project_id', 'projectId'], null),
      prompt: String(first(source, ['prompt', 'question'], '未命名监测问题') || '未命名监测问题'),
      sampleCount: numberOrNull(first(summary, ['sample_count', 'samples_analyzed', 'total_samples'], 0)) ?? 0,
      mentionRate: percentOrNull(first(summary, ['mention_rate', 'brand_mention_rate', 'visibility_rate'])),
      shareOfVoice: percentOrNull(first(summary, ['share_of_voice', 'sov', 'voice_share'])),
      confidence: confidenceObject(first(summary, ['confidence', 'confidence_level'], first(source, ['confidence'], null))),
      provider: String(first(source, ['provider'], '') || ''),
      model: String(first(source, ['model', 'model_name'], '') || ''),
      createdAt: first(source, ['created_at', 'analyzed_at', 'timestamp'], '')
    }
  })
}

export const normalizeVisibilityResult = payload => {
  const container = unwrapApiPayload(payload)
  const storedResult = first(container, ['result', 'analysis_result', 'analysisResult'], null)
  const root = storedResult && typeof storedResult === 'object' && !Array.isArray(storedResult)
    ? storedResult
    : container
  const summarySource = first(root, ['summary', 'overview', 'metrics'], root) || {}
  const samples = asArray(first(root, ['samples', 'sample_results', 'responses'], [])).map(normalizeSample)
  const citations = asArray(first(root, ['citations', 'normalized_citations', 'citation_summary'], []))
    .map(normalizeCitation)
  const entities = asArray(first(root, ['entities', 'entity_comparison', 'entity_metrics', 'brands'], []))
    .map(normalizeEntity)
  const sampleCount = numberOrNull(first(summarySource, ['sample_count', 'samples_analyzed', 'total_samples'])) ?? samples.length
  const mentionedCount = numberOrNull(first(summarySource, ['mentioned_count', 'brand_mentioned_count', 'mention_count']))
    ?? samples.filter(sample => sample.brandMentioned).length
  const citationCount = numberOrNull(first(summarySource, ['citation_count', 'citations_count', 'total_citations']))
    ?? samples.reduce((sum, sample) => sum + sample.citations.length, 0)
  const ownedCitationCount = numberOrNull(first(summarySource, ['owned_citation_count', 'owned_citations_count']))
    ?? samples.reduce((sum, sample) => sum + sample.citations.filter(citation => citation.isOwned).length, 0)
  const methodologySource = first(root, ['methodology', 'method', 'analysis'], {}) || {}
  const algorithmVersions = first(root, ['algorithm_versions'], null)
  const sourceTrace = first(root, ['source_trace'], null)
  const shareOfVoiceValue = first(summarySource, ['share_of_voice', 'sov', 'voice_share'])
  const externalModelValue = first(
    methodologySource,
    ['external_model_used', 'uses_external_model', 'externalModelUsed'],
    null
  )
  const protocolWarnings = []
  if (shareOfVoiceValue == null) protocolWarnings.push('结果回执缺少声量份额字段，界面以“—”显示。')
  if (externalModelValue == null) protocolWarnings.push('结果回执没有声明是否使用外部模型，请核对来源。')

  return {
    runId: first(container, ['run_id', 'id'], first(root, ['run_id'], null)),
    project: first(root, ['project'], null),
    prompt: String(first(root, ['prompt', 'question'], first(container, ['prompt', 'question'], '')) || ''),
    analyzedAt: first(container, ['created_at', 'analyzed_at', 'timestamp'], first(root, ['analyzed_at', 'created_at', 'timestamp'], '')),
    summary: {
      sampleCount,
      mentionedCount,
      mentionRate: percentOrNull(first(summarySource, ['mention_rate', 'brand_mention_rate', 'visibility_rate']))
        ?? (sampleCount ? (mentionedCount / sampleCount) * 100 : 0),
      shareOfVoice: percentOrNull(shareOfVoiceValue),
      averageRank: numberOrNull(first(summarySource, ['average_rank', 'average_recommendation_rank', 'avg_rank'])),
      citationCount,
      citationRate: percentOrNull(first(summarySource, ['citation_rate', 'cited_sample_rate']))
        ?? (sampleCount ? (samples.filter(sample => sample.citations.length).length / sampleCount) * 100 : 0),
      ownedCitationCount,
      ownedCitationRate: percentOrNull(first(summarySource, ['owned_citation_rate', 'owned_rate']))
        ?? (sampleCount
          ? (samples.filter(sample => sample.citations.some(citation => citation.isOwned)).length / sampleCount) * 100
          : 0),
      confidence: confidenceObject(first(summarySource, ['confidence', 'confidence_level'], first(root, ['confidence'], null)))
    },
    entities,
    samples,
    citations,
    recommendations: compactStrings(first(
      root,
      ['recommendations', 'suggestions', 'actions'],
      asArray(first(root, ['recommendation_details'], [])).map(item => item?.title).filter(Boolean)
    )),
    warnings: [
      ...compactStrings(first(root, ['warnings', 'warning'], [])),
      ...protocolWarnings
    ],
    methodology: {
      engine: String(first(methodologySource, ['engine', 'algorithm', 'name'], first(root, ['engine'], '本地规则引擎')) || '本地规则引擎'),
      version: String(first(
        methodologySource,
        ['version', 'engine_version', 'algorithm_version'],
        algorithmVersions?.integration || ''
      ) || ''),
      externalModelUsed: externalModelValue == null ? null : booleanValue(externalModelValue),
      sampleSemantics: sampleSemanticsLabel(
        first(methodologySource, ['sample_semantics', 'sample_definition', 'description'], ''),
        sampleCount
      ),
      citationSemantics: String(first(methodologySource, ['citation_semantics'], '') || ''),
      metricComparability: String(first(methodologySource, ['metric_comparability'], '') || ''),
      sourceTrace,
      algorithmVersions
    }
  }
}
