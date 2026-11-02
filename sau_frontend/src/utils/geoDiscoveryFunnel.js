const stages = Object.freeze({
  natural_discovery: ['自然需求中的品牌提及', 'discovery'],
  brand_search: ['点名查询的官网来源', 'brand_identity'],
  website_guided: ['给网址后的官网来源', 'website_guided']
})
const states = Object.freeze({observed: '已观察到', not_observed: '未观察到', unknown: '待核对'})
const actions = new Set(['review_facts', 'review_webmaster', 'review_drafts'])
const text = (value, limit) => typeof value === 'string' ? value.slice(0, limit) : ''

export function discoveryFunnelView(cycle) {
  const value = cycle?.discovery_funnel, baseline = cycle?.baseline
  if (!cycle?.id || cycle.baseline_source !== 'native_report' || value?.schema_version !== 'geo_discovery_funnel_v1'
      || value.supported !== true || !baseline?.run_id || value.run_id !== baseline.run_id
      || !baseline.plan_sha256 || value.plan_sha256 !== baseline.plan_sha256
      || value.plan_sha256 !== cycle.test_plan?.plan_sha256 || !Array.isArray(value.stages)) return null
  const questions = new Map((Array.isArray(cycle.test_plan?.cases) ? cycle.test_plan.cases : [])
    .filter(row => typeof row?.id === 'string').map(row => [row.id, row]))
  const rows = [], seen = new Set()
  for (const stage of value.stages) {
    if (!Object.hasOwn(stages, stage?.key) || seen.has(stage.key)) continue
    const [label, category] = stages[stage.key]
    const cases = (Array.isArray(stage.cases) ? stage.cases : []).filter(row =>
      questions.get(row?.case_id)?.category === category).map(row => ({
      id: row.case_id, question: text(questions.get(row.case_id)?.question, 800),
      mode: row.client_mode === 'consumer' ? '普通对话' : row.client_mode === 'work' ? '工作界面' : '界面未知',
      model: text(row.model_label, 100) || '模型未知',
      citation: row.official_citation_present === true ? '正文引用：有' : row.official_citation_present === false ? '正文引用：未观察到' : '正文引用：未知'
    }))
    seen.add(stage.key)
    const state = Object.hasOwn(states, stage.state) ? stage.state : 'unknown'
    rows.push({key: stage.key, label, state, stateLabel: states[state], cases, note: text(stage.note, 700)})
  }
  if (!rows.length) return null
  const matched = value.comparability?.matched === true
  const priorities = matched && Array.isArray(value.priorities) ? value.priorities.slice(0, 3).filter(row =>
    actions.has(row?.action_key) && Array.isArray(row.case_ids) && row.case_ids.length > 0
    && row.case_ids.every(id => questions.has(id))).map(row => ({
    id: text(row.key, 80), label: text(row.label, 100), reason: text(row.reason, 900),
    action: {key: row.action_key, target: {cycle_id: cycle.id}}
  })) : []
  return {rows, priorities, matched, note: text(value.note, 800)}
}
