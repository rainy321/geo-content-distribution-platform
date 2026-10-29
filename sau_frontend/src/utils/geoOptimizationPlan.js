const goals = Object.freeze({
  discovery: {key: 'natural_discovery', label: '自然需求发现'},
  brand_identity: {key: 'identity_accuracy', label: '品牌与业务识别'},
  website_guided: {key: 'official_source_visibility', label: '官网资料采用'}
})
const identifier = value => typeof value === 'string' && /^[A-Za-z0-9_-]{1,100}$/.test(value)
const text = (value, maximum = 1500) => typeof value === 'string' ? value.slice(0, maximum) : ''
const strings = (value, maximum = 8) => Array.isArray(value)
  ? value.filter(item => typeof item === 'string' && item.trim()).slice(0, maximum).map(item => text(item)) : []
const ids = value => Array.isArray(value) ? [...new Set(value.filter(identifier))] : []
const sameIds = (left, right) => left.length === right.length && left.every(id => right.includes(id))
const list = value => Array.isArray(value) ? value : []
const goalFor = category => Object.hasOwn(goals, category || '') ? goals[category] : null
const routeId = value => typeof value === 'string' && value.trim().length > 0 && value.trim().length <= 300 ? value.trim() : null

// Read-only projection: choosing an action can only select an existing writing brief.
export function optimizationPlanView(cycle, selectedBriefId = '') {
  const plan = cycle?.optimization_plan
  if (!identifier(cycle?.id) || plan?.schema_version !== 'geo_optimization_plan_v1' ||
      !Array.isArray(plan.questions) || !Array.isArray(plan.actions)) return null
  const originals = new Map(list(cycle.test_plan?.cases).filter(item => identifier(item?.id)
    && typeof item.question === 'string' && goalFor(item.category)).map(item => [item.id, item]))
  const questions = new Map()
  for (const item of plan.questions.slice(0, 100)) {
    const original = originals.get(item?.case_id), goal = goalFor(original?.category)
    if (!original || item.question !== original.question || item.category !== original.category ||
        item.goal !== goal.key || questions.has(item.case_id)) continue
    questions.set(item.case_id, {caseId: item.case_id, category: original.category,
      question: original.question, goal: goal.key, label: goal.label})
  }
  const briefs = new Map(list(cycle.analysis?.briefs).filter(item => identifier(item?.id)).map(item => [item.id, item]))
  const sourceIds = new Set(list(cycle.observations?.sources).map(item => item?.id).filter(identifier))
  const opportunities = new Map(list(cycle.opportunities).filter(item => routeId(item?.id)).map(item => [routeId(item.id), item]))
  const actions = [], seen = new Set()
  for (const item of plan.actions.slice(0, 30)) {
    const brief = briefs.get(item?.id), caseIds = ids(item?.case_ids)
    if (!brief || seen.has(item.id) || item.topic !== brief.topic || !caseIds.length ||
        !sameIds(caseIds, ids(brief.case_ids)) || caseIds.some(id => !questions.has(id))) continue
    seen.add(item.id)
    const allowedSources = new Set(ids(brief.source_ids).filter(id => sourceIds.has(id)))
    const actionQuestions = caseIds.map(id => questions.get(id))
    const checks = (Array.isArray(item.acceptance_checks) ? item.acceptance_checks : []).flatMap(check => {
      const original = questions.get(check?.case_id)
      return original && caseIds.includes(check.case_id) && check.question === original.question && check.goal === original.goal
        ? [{...original, signal: text(check.signal)}] : []
    })
    actions.push({id: item.id, topic: text(item.topic, 300), caseIds,
      goals: [...new Map(actionQuestions.map(question => [question.goal, {key: question.goal, label: question.label}])).values()],
      gap: text(item.gap), changeSummary: text(item.change_summary),
      writingRequirements: strings(item.writing_requirements), evidenceNeeded: strings(item.evidence_needed),
      sourceRoutes: (Array.isArray(item.source_routes) ? item.source_routes : []).slice(0, 10).flatMap(route => {
        const id = routeId(route?.id), opportunity = opportunities.get(id)
        const existing = new Set(ids(opportunity?.source_ids))
        const matched = ids(route?.source_ids).filter(id => allowedSources.has(id) && existing.has(id))
        return opportunity && matched.length ? [{id, label: text(route.label, 200),
          surface: text(route.surface, 80), sourceIds: matched}] : []
      }),
      checks: checks.length ? checks : actionQuestions.map(question => ({...question, signal: '按这道原题记录后续回答，再核对实际内容与来源。'}))})
  }
  if (!actions.length) return null
  const covered = new Set(actions.flatMap(action => action.caseIds))
  return {actions, selected: actions.find(action => action.id === selectedBriefId) || actions[0],
    plannedQuestions: questions.size, questionsWithActions: covered.size,
    uncoveredCaseIds: [...questions.keys()].filter(id => !covered.has(id)),
    needsRefresh: plan.needs_refresh === true, note: text(plan.note, 220)}
}

export function optimizationPlanSelection(view, briefId, busy = false) {
  return !busy && identifier(briefId) && view?.actions.some(action => action.id === briefId) ? briefId : null
}
