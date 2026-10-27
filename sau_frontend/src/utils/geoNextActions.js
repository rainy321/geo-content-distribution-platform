const destinations = Object.freeze({
  review_baseline: 'baseline', review_external_feedback: 'feedback', review_facts: 'facts',
  review_sources: 'sources', review_source_maintenance: 'maintenance', resume_source_maintenance: 'maintenance',
  review_destinations: 'destinations', review_destination: 'destinations', review_drafts: 'drafts',
  review_publication: 'publication', review_webmaster: 'webmaster', retest_same_question: 'feedback'
})
const identifier = value => typeof value === 'string' && /^[A-Za-z0-9_-]{1,100}$/.test(value)
const shortText = (value, maximum) => typeof value === 'string' ? value.slice(0, maximum) : ''
const retainedMaintenance=(cycle,action)=>action?.key==='resume_source_maintenance'&&cycle?.profile?.entity_type==='company'
  &&typeof cycle.profile.name==='string'&&Boolean(cycle.profile.name.trim())&&Array.isArray(cycle.source_maintenance?.tasks)&&cycle.source_maintenance.tasks.some(task=>task?.id===action.target?.task_id
    &&task.cycle_id===action.target?.cycle_id&&task.company_name===cycle.profile.name)
const actionLabel=(cycle,action)=>{
  const task=Array.isArray(cycle?.source_maintenance?.tasks)?cycle.source_maintenance.tasks.find(item=>item.id===action.target?.task_id):null
  const module={website:'企业官网',introduction:'企业简介'}[task?.module]
  return (action.key==='resume_source_maintenance'&&module?`${module} · `:'')+shortText(action.label,80)
}

export function nextActions(cycle) {
  const diagnostics = cycle?.feedback_diagnostics
  if (!identifier(cycle?.id) || diagnostics?.schema_version !== 'geo_feedback_diagnostics_v1' ||
      diagnostics.cycle_id !== cycle.id || diagnostics.read_only !== true) return {actions: [], attention: [], evidence: []}
  const evidence = (Array.isArray(diagnostics.evidence) ? diagnostics.evidence : []).slice(0, 200)
    .filter(item => typeof item?.id === 'string' && item.id.length <= 220)
    .map(item => ({id:item.id, label:shortText(item.label, 160)}))
  const references = new Set(evidence.map(item => item.id)), seen = new Set()
  const refs = value => Array.isArray(value) ? [...new Set(value.filter(key => references.has(key)))].slice(0, 8) : []
  const actions = (Array.isArray(diagnostics.actions) ? diagnostics.actions : []).filter(item => {
    if (!identifier(item?.id) || !Object.hasOwn(destinations,item.key) || item.target?.cycle_id !== cycle.id && !retainedMaintenance(cycle,item) || seen.has(item.id)) return false
    if (Object.entries(item.target).some(([key,value]) => !['cycle_id','case_id','target_id','task_id','article_id','job_id'].includes(key) ||
        !identifier(typeof value === 'number' && Number.isSafeInteger(value) && value > 0 ? String(value) : value))) return false
    seen.add(item.id);return true
  }).slice(0,6).map(item => ({id:item.id,key:item.key,label:actionLabel(cycle,item),reason:shortText(item.reason,500),
    target:{...item.target},section:destinations[item.key],evidence_ids:refs(item.evidence_ids)}))
  const attention = (Array.isArray(diagnostics.attention_items) ? diagnostics.attention_items : []).slice(0,8)
    .filter(item => typeof item?.message === 'string').map(item => ({message:shortText(item.message,600),evidence_ids:refs(item.evidence_ids)}))
  return {actions,attention,evidence}
}

export function diagnosticSection(action, cycle) {
  const cycleId=typeof cycle==='string'?cycle:cycle?.id
  return identifier(cycleId) && (action?.target?.cycle_id === cycleId || retainedMaintenance(cycle,action)) && Object.hasOwn(destinations,action?.key)
    ? destinations[action.key] : null
}

// Only fixed application sections can be selected. This never opens a platform or submits a task.
export function focusDiagnosticSection(section, root = document) {
  if (!Object.values(destinations).includes(section)) return false
  const element = root.querySelector(`[data-geo-section="${section}"]`)
  if (!element) return false
  for(let parent=element.parentElement;parent;parent=parent.parentElement){if(parent.tagName==='DETAILS')parent.open=true}
  element.scrollIntoView({behavior:globalThis.matchMedia?.('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth',block:'start'})
  element.setAttribute('tabindex','-1');element.focus({preventScroll:true})
  return true
}
