import { sameBrand, journeyDate } from './geoJourney.js'

const cycleId = value => typeof value === 'string' && /^[a-z0-9_-]{1,120}$/i.test(value)
const projectSnapshot = project => project ? Object.freeze({id:project.id,name:project.name,website:project.website,entity_type:project.entity_type}) : null
const validProject = project => Boolean(project && project.id != null && sameBrand(project,project))

export function savedCycleRows(rows, project) {
  if(!Array.isArray(rows))throw new Error('invalid-cycle-list')
  if(!validProject(project))return []
  const matching=rows.filter(row=>row && row.project_id===project.id && cycleId(row.id) &&
    typeof row.report_ref==='string' && row.report_ref.trim())
  const counts=new Map()
  for(const row of matching)counts.set(row.id,(counts.get(row.id)||0)+1)
  return matching.filter(row=>counts.get(row.id)===1).map(row=>Object.freeze({
    id:row.id,project_id:row.project_id,report_ref:row.report_ref,
    updated_at:row.updated_at,created_at:row.created_at,
    draft_count:Number.isSafeInteger(row.draft_count)&&row.draft_count>=0?row.draft_count:null
  }))
}

export const savedCycleLabel = row => `更新于 ${journeyDate(row.updated_at||row.created_at)} · 基线报告 ${row.report_ref} · ${row.draft_count==null?'稿件数待核对':`${row.draft_count} 篇稿件`}`

export function savedCycleMatches(value, row, project) {
  return Boolean(value && row && validProject(project) && value.id===row.id &&
    row.project_id===project.id && value.project_id===project.id &&
    typeof row.report_ref==='string' && row.report_ref.trim() && value.report_ref===row.report_ref &&
    sameBrand(value.profile,project) && sameBrand(value.baseline?.profile,project) &&
    Array.isArray(value.drafts) && Array.isArray(value.analysis?.briefs))
}

export function savedCycleContinuation(value, project) {
  const row={id:value?.id,project_id:value?.project_id,report_ref:value?.report_ref}
  if(!savedCycleMatches(value,row,project) || !Array.isArray(value.baseline?.cases))throw new Error('已有内容与当前对象或原基线不一致，已阻止混用。')
  const draft=value.drafts.find(item=>value.analysis.briefs.some(brief=>brief.id===item.brief_id) && Object.hasOwn(value.channels || {},item.platform))
  return {report:value.baseline,reportId:value.report_ref,cycle:value,
    briefId:draft?.brief_id || value.analysis.briefs[0]?.id || '',
    platform:draft?.platform || '',articleId:draft?.article_id || null}
}

export function savedCycleFactEdits(cycle,rows,project) {
  if(!cycle || !Array.isArray(rows))return false
  const saved=cycle.facts?.length?cycle.facts:cycle.fact_candidates?.facts || [{text:project?.description || '',source_url:'',confirmed:false}]
  const comparable=items=>items.map(item=>({text:item.text,source_url:item.source_url || '',confirmed:cycle.facts?.length>0 && item.confirmed===true}))
  const current=rows.map(item=>({text:item.text,source_url:item.source_url || '',confirmed:item.confirmed===true}))
  return JSON.stringify(current)!==JSON.stringify(comparable(saved))
}

// Own each project epoch and request sequence, including A -> B -> A. Only an
// explicit select reads a full cycle. There is no create call or latest fallback.
export function createSavedCyclesReader({api,getProject,isBusy=()=>false,onState=()=>{},onSelected=()=>{}}) {
  let active=true,epoch=0,listRequest=0,selectionRequest=0,project=null
  let state={items:[],loading:false,selecting:'',error:''}
  const publish=patch=>{state={...state,...patch};if(active)onState(state)}
  const scope=()=>({epoch,project})
  const current=opened=>active && opened.epoch===epoch && validProject(opened.project) &&
    getProject()?.id===opened.project.id && sameBrand(getProject(),opened.project)
  const invalidateSelection=()=>{selectionRequest++;publish({selecting:''})}
  const refresh=async()=>{
    if(!active || !validProject(project))return []
    const opened=scope(),request=++listRequest
    invalidateSelection();publish({items:[],loading:true,error:''})
    try {
      const response=await api.list()
      if(!current(opened) || request!==listRequest)return []
      const items=savedCycleRows(response.data,opened.project)
      publish({items});return items
    } catch {
      if(current(opened) && request===listRequest)publish({items:[],error:'已有内容暂时读取失败，服务器记录仍保留，请重试。'})
      return []
    } finally {if(current(opened) && request===listRequest)publish({loading:false})}
  }
  const changeProject=value=>{
    epoch++;listRequest++;selectionRequest++;project=projectSnapshot(value)
    publish({items:[],loading:false,selecting:'',error:''})
    return refresh()
  }
  const select=async id=>{
    if(!active || isBusy() || state.loading || state.selecting)return null
    const row=state.items.find(item=>item.id===id)
    if(!row)return null
    const opened=scope(),request=++selectionRequest
    if(!current(opened))return null
    publish({selecting:id,error:''})
    try {
      const response=await api.get(row.id)
      if(!current(opened) || request!==selectionRequest || isBusy())return null
      if(!savedCycleMatches(response.data,row,opened.project)) {
        publish({error:'这轮内容的品牌或基线报告与列表不一致，已阻止混用；请刷新后核对。'});return null
      }
      onSelected(response.data);return response.data
    } catch {
      if(current(opened) && request===selectionRequest)publish({error:'这轮内容暂时未能读取，原稿仍保留，请重试。'})
      return null
    } finally {if(current(opened) && request===selectionRequest)publish({selecting:''})}
  }
  const dispose=()=>{active=false;epoch++;listRequest++;selectionRequest++}
  return {changeProject,refresh,select,invalidateSelection,dispose}
}
