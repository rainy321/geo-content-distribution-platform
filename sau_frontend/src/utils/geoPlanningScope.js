import {sameResearchBrief} from './geoJourney.js'

export const PLANNING_ACTIVE_LIMIT = 6
export const PLANNING_HISTORY_LIMIT = 20
const identifier = value => typeof value === 'string' && /^[A-Za-z0-9_-]{1,100}$/.test(value)
const rows = value => Array.isArray(value) ? value : []
const uniqueIds = values => Array.isArray(values) && values.length > 0
  && values.every(identifier) && new Set(values).size === values.length

// An editing scope never changes the frozen plan or the saved observations.
export function planningScopeView(cycle, selectedCaseId = '') {
  const questions = rows(cycle?.test_plan?.cases)
  const observed = rows(cycle?.observations?.cases)
  const options = questions.filter(item => identifier(item?.id) && typeof item.question === 'string'
    && item.question.trim() && questions.filter(row => row?.id === item.id).length === 1)
    .map(item => ({id:item.id, question:item.question,
      usable:observed.filter(row => row?.id === item.id && row.question === item.question && row.usable === true).length === 1}))
  const base = {options, selectedCaseId, scope:undefined, allowed:Boolean(cycle?.id), blockedReason:'',
    selectedQuestion:null, maxBriefs:6, retainedBriefCount:0, replacedBriefCount:0,
    message:'使用全部原题策划；内容仍需核对，不代表整体 GEO 优化通过。'}
  const reject = message => ({...base, allowed:false, blockedReason:message})
  if (!cycle?.id) return reject('请先打开当前内容任务单。')
  const history = cycle.analysis?.history
  if (history !== undefined && (!Array.isArray(history) || history.length >= PLANNING_HISTORY_LIMIT)) {
    return reject('策划历史已达可保留上限，不能继续生成；现有选题和稿件保留。')
  }
  if (selectedCaseId === '') return base
  if (!identifier(selectedCaseId)) return reject('所选原题无效，请重新明确选择。')
  const selected = options.find(item => item.id === selectedCaseId)
  if (!selected?.usable) return reject('所选原题没有可用于策划的当前有效回答，原记录保留。')
  const briefs = cycle.analysis?.briefs
  if (!Array.isArray(briefs) || briefs.length > PLANNING_ACTIVE_LIMIT
      || briefs.some(item => !identifier(item?.id) || !uniqueIds(item.case_ids))
      || new Set(briefs.map(item => item.id)).size !== briefs.length) return reject('当前选题结构超出可核对范围，请先保留原记录并核对。')
  if (briefs.some(item => item.case_ids.includes(selectedCaseId) && item.case_ids.length > 1)) {
    return reject('已有选题同时关联所选题和其他原题，不能自动拆分；请改用全部原题或先核对该选题。')
  }
  const retained = briefs.filter(item => !item.case_ids.includes(selectedCaseId)).length
  const capacity = Math.min(2, PLANNING_ACTIVE_LIMIT - retained)
  if (capacity < 1) return reject('保留其他原题选题后已无新增容量，不能开始本次单题策划。')
  return {...base, scope:{case_ids:[selectedCaseId]}, selectedQuestion:selected,
    retainedBriefCount:retained, replacedBriefCount:briefs.length - retained, maxBriefs:capacity,
    message:`本次只更新 ${selectedCaseId}，最多生成 ${capacity} 条选题；其他 ${retained} 条选题保留。原题、原始观察和全部已确认事实保留；不代表整体 GEO 优化通过。`}
}

export function planningRequestScope(cycle, selectedCaseId, kind) {
  if (kind !== 'analysis') return undefined
  const view = planningScopeView(cycle, selectedCaseId)
  if (!view.allowed) throw new Error(view.blockedReason)
  return view.scope
}

export function planningTouchesDraft(cycle, selectedCaseId, draft) {
  if (selectedCaseId === '') return true
  const view = planningScopeView(cycle, selectedCaseId)
  if (!view.allowed || !uniqueIds(draft?.provenance?.brief?.case_ids)) return true
  return draft.provenance.brief.case_ids.includes(selectedCaseId)
}

export function latestPlanningScopeNotice(cycle) {
  const latest = cycle?.analysis?.latest_scope
  if (latest?.mode === 'all_cases') return '最近一次按全部原题策划；历史稿件仍保留，策划结果不代表整体 GEO 优化通过。'
  if (cycle?.analysis?.method !== 'mixed_scope_planning') return ''
  if (latest?.mode !== 'selected_case' || !uniqueIds(latest?.case_ids) || latest.case_ids.length !== 1) return '当前选题包含分题策划历史，各题范围仍需分别核对；不代表整体 GEO 优化通过。'
  return `最近仅更新 ${latest.case_ids[0]} 的内容选题；其他原题继续保留原选题与原记录，未计作本次已更新，不代表整体 GEO 优化通过。`
}

export function historicalPlanningDrafts(cycle) {
  const history = cycle?.analysis?.history
  if (!Array.isArray(history) || history.length > PLANNING_HISTORY_LIMIT) return []
  const active = rows(cycle.analysis?.briefs), drafts = rows(cycle.drafts)
  const seen = new Set(), result = []
  for (const draft of drafts) {
    if (!Number.isSafeInteger(draft?.article_id) || draft.article_id < 1 || seen.has(draft.article_id)
        || draft.cycle_id !== cycle.id || draft.brief_id !== draft.provenance?.brief?.id
        || active.some(brief => sameResearchBrief(draft, brief))) continue
    const archived = history.find(record => rows(record?.analysis?.briefs).some(brief => sameResearchBrief(draft, brief)))
    if (!archived) continue
    seen.add(draft.article_id)
    result.push({draft, brief:draft.provenance.brief, archivedAt:archived.created_at || null})
  }
  return result
}
