// Read-only presentation of server-validated follow-up leads; never publishing authority.
const role = 'followup_channel_research_only'
const list = value => Array.isArray(value) ? value : []
const id = value => typeof value === 'string' && /^[A-Za-z0-9_-]{1,100}$/.test(value)
const ref = value => typeof value === 'string' && /^fs_[a-f0-9]{64}$/.test(value)
const text = (value, maximum = 200) => typeof value === 'string' ? value.slice(0, maximum) : ''
const binding = value => value && id(value.probe_id) && id(value.case_id) && ref(value.source_ref)
  ? `${value.probe_id}:${value.case_id}:${value.source_ref}` : null
const unique = (values, key) => {
  const map = new Map(), duplicates = new Set()
  for (const value of list(values)) {
    const identifier = value?.[key]
    if (typeof identifier !== 'string') continue
    if (map.has(identifier)) duplicates.add(identifier)
    map.set(identifier, value)
  }
  for (const identifier of duplicates) map.delete(identifier)
  return map
}

export function followupPublicUrl(value) {
  if (typeof value !== 'string' || value.length > 1800 || /[\s\\\u0000-\u001f\u007f]/u.test(value)
      || !/^https:\/\/[a-z0-9.-]+(?::443)?(?:[/?#]|$)/i.test(value)) return null
  try {
    const parsed = new URL(value), host = parsed.hostname
    if (parsed.protocol !== 'https:' || parsed.username || parsed.password || parsed.port
        || !/^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+(?:[a-z]{2,63}|xn--[a-z0-9-]{1,59})$/i.test(host)
        || /\.(?:localhost|local|localdomain|internal|invalid|test|example|onion|alt|arpa|home|lan|corp|intranet)$/i.test(host)) return null
    return value
  } catch { return null }
}

function probeCases(cycle) {
  const originals = unique(cycle?.test_plan?.cases, 'id'), result = new Map()
  for (const [identifier, probe] of unique(cycle?.probes, 'id')) {
    const original = originals.get(probe?.case_id)
    if (!id(identifier) || !id(original?.id) || typeof original.question !== 'string' || !original.question || original.question.length > 800
        || typeof cycle?.test_plan?.plan_sha256 !== 'string' || !/^[a-f0-9]{64}$/.test(cycle.test_plan.plan_sha256)
        || probe.cycle_id !== cycle?.id || probe.plan_sha256 !== cycle?.test_plan?.plan_sha256
        || probe.observation_role === 'baseline' || probe.conditions?.same_question === false
        || probe.after?.answer_evaluable === false || probe.after?.answer_error
        || typeof probe.after?.response !== 'string' || !probe.after.response.trim()
        || !['native_collector', 'research_prototype_import', 'user_reported_external'].includes(probe.source)
        || (probe.source === 'native_collector' ? probe.native_verified !== true : probe.native_verified !== false)
        || (probe.source !== 'user_reported_external' && probe.after.answer_evaluable !== true)
        || ['question', 'observed_question'].some(field => probe[field] != null && probe[field] !== original.question)) continue
    result.set(identifier, {caseId: probe.case_id, question: original.question,
      source: probe.source, nativeVerified: probe.native_verified,
      clientMode: ['consumer', 'work'].includes(probe.after.client_mode) ? probe.after.client_mode : 'unknown',
      modelLabel: text(probe.after.model_label, 100).trim() || '模型未知'})
  }
  return result
}

export function followupSourcesView(cycle) {
  const probes = probeCases(cycle), routes = []
  for (const [identifier, route] of unique(list(cycle?.followup_opportunities).slice(0, 120), 'id')) {
    if (!identifier.startsWith('feedback:') || identifier.length > 300 || route.evidence_role !== role
        || route.facts_verified !== false || route.publication_authorized !== false || route.geo_effect_verified !== false
        || !Array.isArray(route.source_ids) || route.source_ids.length) continue
    const sources = new Map(), duplicate = new Set()
    for (const source of list(route.feedback_sources).slice(0, 120)) {
      const key = binding(source), probe = probes.get(source?.probe_id)
      if (!key || !probe || source.case_id !== probe.caseId || source.source !== probe.source
          || source.native_verified !== probe.nativeVerified || !['citations', 'displayed_sources'].includes(source.kind)) continue
      if (sources.has(key)) duplicate.add(key)
      sources.set(key, {probeId: source.probe_id, caseId: source.case_id, sourceRef: source.source_ref,
        title: text(source.title), url: followupPublicUrl(source.url), kind: source.kind, question: probe.question,
        source: text(source.source, 80), nativeVerified: source.native_verified === true,
        clientMode: probe.clientMode, modelLabel: probe.modelLabel})
    }
    for (const key of duplicate) sources.delete(key)
    const references = list(route.feedback_source_refs), keys = references.map(binding)
    if (!keys.length || keys.some(key => !key || !sources.has(key)) || new Set(keys).size !== keys.length) continue
    const surface = ['owned_site', 'community_candidate', 'vendor_documentation', 'unknown', 'url_unknown'].includes(route.surface)
      ? route.surface : 'unknown'
    routes.push({id: identifier, label: text(route.candidate?.name) || text(route.domain) || '来源网址待核对', surface,
      sources: keys.map(key => sources.get(key)), note: text(route.note, 500)})
  }
  const sources = routes.flatMap(route => route.sources)
  return {routes, sourceCount: new Set(sources.map(source => source.sourceRef)).size,
    probeCount: new Set(sources.map(source => source.probeId)).size, caseCount: new Set(sources.map(source => source.caseId)).size}
}

export function feedbackRoutesForBrief(cycle, brief, routes) {
  const identifiers = brief?.feedback_probe_ids, cases = brief?.case_ids, probes = probeCases(cycle)
  if (!Array.isArray(identifiers) || !identifiers.length || identifiers.length > 6 || identifiers.some(value => !id(value))
      || new Set(identifiers).size !== identifiers.length || !Array.isArray(cases)
      || identifiers.some(value => !probes.has(value) || !cases.includes(probes.get(value).caseId))) return []
  const available = new Map(followupSourcesView(cycle).routes.map(route => [route.id, route]))
  return list(routes).slice(0, 130).flatMap(route => {
    const opportunity = available.get(route?.id), references = route?.feedback_source_refs
    if (!opportunity || route.evidence_role !== role || !Array.isArray(route.source_ids) || route.source_ids.length
        || route.facts_verified !== false || route.publication_authorized !== false || route.geo_effect_verified !== false
        || !Array.isArray(references) || !references.length) return []
    const allowed = new Set(opportunity.sources.map(source => `${source.probeId}:${source.caseId}:${source.sourceRef}`))
    const keys = references.map(binding)
    if (keys.some((key, index) => !key || !allowed.has(key) || !identifiers.includes(references[index].probe_id)
        || !cases.includes(references[index].case_id)) || new Set(keys).size !== keys.length) return []
    return [{id: route.id, label: opportunity.label, surface: opportunity.surface, sourceIds: [],
      feedbackSourceRefs: references.map(item => ({probeId: item.probe_id, caseId: item.case_id, sourceRef: item.source_ref}))}]
  })
}
