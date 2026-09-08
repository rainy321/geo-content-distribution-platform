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
    sources
  }
}
