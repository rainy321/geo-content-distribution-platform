const finitePercent = value => {
  if (value == null || value === '') return null
  const parsed = Number(value)
  if (!Number.isFinite(parsed)) return null
  return Math.max(0, Math.min(100, parsed))
}

export const buildVisibilitySeries = history => (
  (Array.isArray(history) ? history : [])
    .map(item => ({
      value: finitePercent(item?.mentionRate),
      label: String(item?.createdAt || '')
    }))
    .filter(item => item.value != null)
    .reverse()
)

export const visibilityDelta = series => {
  const values = (Array.isArray(series) ? series : [])
    .map(item => finitePercent(item?.value ?? item))
    .filter(value => value != null)
  if (values.length < 2) return null
  return Number((values.at(-1) - values.at(-2)).toFixed(1))
}

export const latestCitationEvidence = result => {
  if (!result || typeof result !== 'object') return null
  const citations = Array.isArray(result.citations) ? result.citations : []
  const samples = Array.isArray(result.samples) ? result.samples : []
  const citation = citations[0] || samples.flatMap(sample => sample?.citations || [])[0] || null
  const evidence = samples.flatMap(sample => sample?.evidence || []).find(item => item?.quote) || null
  if (!citation && !evidence) return null
  return {
    quote: String(evidence?.quote || '已识别到引用来源，可打开完整分析核对上下文。'),
    domain: String(citation?.domain || citation?.raw || '回答正文证据'),
    url: String(citation?.url || ''),
    owned: Boolean(citation?.isOwned),
    competitor: Boolean(citation?.isCompetitor)
  }
}

export const diagnosticRetestDelta = history => {
  const values = (Array.isArray(history) ? history : [])
    .map(item => finitePercent(item?.score))
    .filter(value => value != null)
  if (values.length < 2) return null
  return Number((values[0] - values[1]).toFixed(1))
}
