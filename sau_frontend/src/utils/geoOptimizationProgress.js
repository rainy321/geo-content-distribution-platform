import {createFeedbackSession} from './geoFeedbackSession.js'
import {externalSha} from './geoExternalBaseline.js'

export const optimizationProgressSession=createFeedbackSession({entries:20,bytes:512*1024})
export const progressStatuses=Object.freeze({observed_improvement:'用户记录回答改善',not_observed:'未观察到改善',preparation:'基础准备'})
export class ProgressInputError extends Error {}
const clone=value=>JSON.parse(JSON.stringify(value))
const identifier=value=>typeof value==='string'&&/^[A-Za-z0-9_-]{1,100}$/.test(value)
const hash=value=>typeof value==='string'&&/^[a-f0-9]{64}$/.test(value)
const projectId=value=>Number.isSafeInteger(value)&&value>0
const bounded=(value,max,required=false)=>typeof value==='string'&&value.length<=max&&(!required||Boolean(value.trim()))
const count=value=>Number.isSafeInteger(value)&&value>=0
const date=value=>typeof value==='string'&&value.length<=64&&/(?:Z|[+-]\d\d:\d\d)$/.test(value)&&Number.isFinite(Date.parse(value))
const canonical=value=>Array.isArray(value)?value.map(canonical):value&&typeof value==='object'?Object.fromEntries(Object.keys(value).sort().map(key=>[key,canonical(value[key])])):value
export const progressProjectKey=project=>projectId(project?.id)?`optimization-progress:${JSON.stringify([project.id,project.name,project.website,project.entity_type,project.aliases,project.official_urls])}`:null
export const emptyProgressForm=()=>({direction:'',actionSummary:'',status:'preparation',actionAt:'',beforeProbeId:'',afterProbeId:'',beforeQuote:'',afterQuote:'',reviewNote:''})
export const progressStatusLabel=value=>Object.hasOwn(progressStatuses,value)?progressStatuses[value]:'状态未知'
export const progressSourceLabel=value=>value?.source==='user_reported_external'?'用户提供':value?.native_verified===true?'软件采集':'研究导入，未独立核验'
export function progressContextNote(value){
  if(!value)return ''
  return [value.question_basis==='frozen_question'?'实际同题未知':'实际题文已登记',
    ({consumer:'普通对话',work:'豆包工作'}[value.client_mode]||'界面未知'),
    value.collection_time_known===true?'采集时间已登记':'采集时间未知',
    value.context_attestations?.length?'含用户条件声明':'独立条件待核对'].join(' · ')
}
export function progressError(cause){
  if(cause instanceof ProgressInputError)return cause.message.slice(0,220)
  return ({400:'填写或证据未通过校验，原填写保留，请核对后再保存。',401:'登录状态需要核对，原填写保留。',403:'页面来源或访问权限需要核对，原填写保留。',404:'项目或所选证据暂未找到，原填写保留。',409:'对象范围、证据或同次保存内容已变化，原填写与请求保留，请刷新范围后核对原记录。',413:'填写超过大小限制，原填写保留，请先导出。'}[cause?.response?.status]
    ||'保存结果尚未确认，原填写与请求保留，请核对同一次操作。')
}
function identity(value){
  if(!value||!bounded(value.name,300,true)||!['company','product'].includes(value.entity_type)||!bounded(value.website,2000)
    ||!Array.isArray(value.aliases)||value.aliases.length>100||value.aliases.some(item=>!bounded(item,300,true))
    ||!Array.isArray(value.official_urls)||value.official_urls.length>100||value.official_urls.some(item=>!bounded(item,2000,true))
    ||!value.measurement_signature||typeof value.measurement_signature!=='object'||JSON.stringify(value).length>32000)throw new ProgressInputError('项目对象与官网范围的身份未核对。')
  return clone(value)
}
const countKeys=['total','observed_improvement','not_observed','preparation','native_only','user_reported_included','research_import_included','no_evidence','independently_verified_effects']
export const progressCountSnapshot=page=>JSON.stringify(countKeys.map(key=>[page.counts[key],page.countsCurrent[key],page.countsHistory[key],page.countsUnknown[key]]))
function validCounts(value){return Boolean(value&&countKeys.every(key=>count(value[key]))&&value.independently_verified_effects===0
  &&value.total===value.observed_improvement+value.not_observed+value.preparation
  &&value.total===value.native_only+value.user_reported_included+value.research_import_included+value.no_evidence)}
function evidence(value,{snapshot=false}={}){
  if(!value||!identifier(value.probe_id)||!identifier(value.cycle_id)||!identifier(value.case_id)||!hash(value.plan_sha256)
    ||!hash(value.question_sha256)||!hash(value.effective_question_sha256)||!hash(value.response_sha256)
    ||!['observed_question','frozen_question'].includes(value.question_basis)
    ||!['stored_and_verified','derived_from_server_record'].includes(value.response_sha256_basis)
    ||!bounded(value.question,4000,true)||!['native_collector','research_prototype_import','user_reported_external'].includes(value.source)||value.native_verified!==(value.source==='native_collector')||value.independent_verification!==false
    ||typeof value.collection_time_known!=='boolean'||value.collection_time_known&&!date(value.collected_at)
    ||value.collected_at!==null&&!date(value.collected_at)||value.recorded_at!==null&&!date(value.recorded_at)
    ||value.observed_question!==null&&!bounded(value.observed_question,4000)
    ||value.client_mode!==null&&!['consumer','work'].includes(value.client_mode)
    ||value.model_label!==null&&!bounded(value.model_label,100)
    ||value.answer_evaluable!==null&&typeof value.answer_evaluable!=='boolean'
    ||value.answer_error!==null&&!bounded(value.answer_error,300)||typeof value.excluded_from_improvement!=='boolean'
    ||!Array.isArray(value.context_attestations)||value.context_attestations.length>100
    ||snapshot&&!bounded(value.quote,2000,true)
    ||!snapshot&&(!bounded(value.response_excerpt,2000)||typeof value.response_truncated!=='boolean'||!count(value.response_chars)))throw new ProgressInputError('回答证据的身份或原文版本未核对。')
  return clone(value)
}
function record(project,value){
  if(!value||!identifier(value.id)||value.project_id!==project.id||!bounded(value.request_id,100,true)
    ||!Object.hasOwn(progressStatuses,value.status)||!bounded(value.direction,200,true)||!bounded(value.action_summary,3000,true)
    ||!bounded(value.review_note,3000)||!date(value.recorded_at)||value.action_at!==null&&!date(value.action_at)
    ||value.assessment_basis!=='operator_review_of_server_quotes'||value.independent_verification!==false||value.native_metrics_available!==false||value.causal_effect!==null)throw new ProgressInputError('进展记录与当前项目或声明范围不一致。')
  if(![true,false,null].includes(value.current_entity_match)||!['stored_at_record','derived_frozen_profile','derived_unchanged_project','unknown'].includes(value.project_identity_basis)
    ||value.project_identity===null&&(value.project_identity_sha256!==null||value.current_entity_match!==null||value.project_identity_basis!=='unknown')
    ||value.project_identity!==null&&(!hash(value.project_identity_sha256)||value.current_entity_match===null||value.project_identity_basis==='unknown'))throw new ProgressInputError('历史记录的对象范围未核对。')
  const next=clone(value)
  next.project_identity=value.project_identity===null?null:identity(value.project_identity)
  next.before=value.before===null?null:evidence(value.before,{snapshot:true})
  next.after=value.after===null?null:evidence(value.after,{snapshot:true})
  const sources=[next.before,next.after].filter(Boolean).map(item=>item.source)
  const kind=sources.includes('user_reported_external')?'user_reported_included':sources.includes('research_prototype_import')?'research_import_included':sources.length?'native_only':'no_evidence'
  if(value.evidence_kind!==kind||value.request_sha256!==undefined&&!hash(value.request_sha256))throw new ProgressInputError('进展记录的证据来源未核对。')
  if(value.status!=='preparation'&&(!next.before||!next.after||!comparableProgressEvidence(next.before,next.after)))throw new ProgressInputError('前后证据不属于同一组原题。')
  if(value.status==='observed_improvement'&&([next.before,next.after].some(item=>item.excluded_from_improvement||item.answer_evaluable===false||Boolean(item.answer_error))
    ||Date.parse(next.after.recorded_at)<Date.parse(next.before.recorded_at)||next.before.collection_time_known&&next.after.collection_time_known&&Date.parse(next.after.collected_at)<Date.parse(next.before.collected_at)))throw new ProgressInputError('改善记录的回答有效性或前后顺序未核对。')
  return next
}
export function progressPage(project,reply,{offset=0,probeOffset=0,snapshot=null,identitySha=null,includeProbes=true}={}){
  const value=reply?.data
  if(!projectId(project?.id)||reply?.code!==200||value?.project_id!==project.id||!Array.isArray(value.records)||value.records.length>20
    ||!count(value.snapshot_sequence)||snapshot!==null&&value.snapshot_sequence!==snapshot||typeof value.has_more!=='boolean'
    ||value.has_more&&value.next_offset!==offset+20||!value.has_more&&value.next_offset!==null
    ||!hash(value.project_identity_sha256)||identitySha!==null&&value.project_identity_sha256!==identitySha||!validCounts(value.counts)||!validCounts(value.counts_current)||!validCounts(value.counts_history)||!validCounts(value.counts_unknown)
    ||countKeys.some(key=>value.counts[key]!==value.counts_current[key]+value.counts_history[key]+value.counts_unknown[key]))throw new ProgressInputError('进展列表的项目或分页回执未核对。')
  const currentIdentity=identity(value.project_identity)
  const records=value.records.map(item=>record(project,item))
  if(records.some(item=>item.current_entity_match===false&&item.project_identity_sha256===value.project_identity_sha256
    ||item.current_entity_match===true&&JSON.stringify(canonical(item.project_identity.measurement_signature))!==JSON.stringify(canonical(currentIdentity.measurement_signature))))throw new ProgressInputError('历史记录与当前对象范围标记不一致。')
  if(new Set(records.map(item=>item.id)).size!==records.length||records.length>value.counts.total||value.has_more&&records.length!==20)throw new ProgressInputError('进展列表出现重复身份或缺失页，请重新读取。')
  let options=[]
  if(includeProbes){
    if(!Array.isArray(value.probe_options)||value.probe_options.length>100||typeof value.probe_options_has_more!=='boolean'
      ||value.probe_options_has_more&&value.next_probe_offset!==probeOffset+100||!value.probe_options_has_more&&value.next_probe_offset!==null)throw new ProgressInputError('回答选择列表未核对。')
    options=value.probe_options.map(item=>evidence(item))
    if(new Set(options.map(item=>item.probe_id)).size!==options.length)throw new ProgressInputError('回答选择列表出现重复身份。')
  }
  return {records,counts:clone(value.counts),countsCurrent:clone(value.counts_current),countsHistory:clone(value.counts_history),countsUnknown:clone(value.counts_unknown),identity:currentIdentity,identitySha:value.project_identity_sha256,
    hasMore:value.has_more,nextOffset:value.next_offset,snapshot:value.snapshot_sequence,
    options,optionsHasMore:includeProbes?value.probe_options_has_more:false,nextProbeOffset:includeProbes?value.next_probe_offset:null}
}
export async function progressVerifyIdentities(value){
  if(await externalSha(value.identity)!==value.identitySha)throw new ProgressInputError('当前对象的身份摘要不一致，未采用该回执。')
  for(const record of value.records)if(record.project_identity!==null&&await externalSha(record.project_identity)!==record.project_identity_sha256)throw new ProgressInputError('历史对象的身份摘要不一致，未采用该回执。')
  return value
}
export const comparableProgressEvidence=(before,after)=>Boolean(before&&after&&before.probe_id!==after.probe_id
  &&before.plan_sha256===after.plan_sha256&&before.case_id===after.case_id&&hash(before.effective_question_sha256)&&before.effective_question_sha256===after.effective_question_sha256)
export async function progressRaw(project,option,cycle){
  const matches=cycle?.probes?.filter(item=>item.id===option.probe_id&&item.case_id===option.case_id)||[]
  const probe=matches[0],raw=probe?.after?.response
  if(cycle?.id!==option.cycle_id||cycle.project_id!==project.id||matches.length!==1||probe.cycle_id!==option.cycle_id||cycle.test_plan?.plan_sha256!==option.plan_sha256
    ||probe.plan_sha256!==option.plan_sha256||probe.question_sha256!==option.question_sha256||probe.source!==option.source||probe.native_verified!==option.native_verified
    ||typeof raw!=='string'||await externalSha(raw)!==option.response_sha256
    ||probe.response_sha256&&probe.response_sha256!==option.response_sha256||probe.after?.response_sha256&&probe.after.response_sha256!==option.response_sha256)throw new ProgressInputError('完整回答与所选证据版本不一致，请重新读取后再摘录。')
  return {probeId:option.probe_id,sha256:option.response_sha256,raw}
}
export function progressPayload(project,form,options,raws,identitySha){
  if(!projectId(project?.id)||!hash(identitySha)||!Object.hasOwn(progressStatuses,form.status)||!bounded(form.direction,200,true)||!bounded(form.actionSummary,3000,true)||!bounded(form.reviewNote,3000))throw new ProgressInputError('请先核对当前项目范围，并填写优化方向和本次改动。')
  const body={request_id:globalThis.crypto.randomUUID(),expected_project_identity_sha256:identitySha,direction:form.direction.trim(),action_summary:form.actionSummary.trim(),status:form.status}
  if(form.reviewNote)body.review_note=form.reviewNote
  if(form.actionAt){const value=new Date(form.actionAt);if(!Number.isFinite(value.getTime()))throw new ProgressInputError('请核对实际操作时间；不知道时留空。');body.action_at=value.toISOString()}
  const proof={}
  if(form.status!=='preparation'){
    const before=options.find(item=>item.probe_id===form.beforeProbeId),after=options.find(item=>item.probe_id===form.afterProbeId)
    if(!comparableProgressEvidence(before,after))throw new ProgressInputError('请选择同一计划、同一道实际或固定原题的两条不同回答。')
    if(form.status==='observed_improvement'&&([before,after].some(item=>item.excluded_from_improvement||item.answer_evaluable===false||Boolean(item.answer_error))
      ||Date.parse(after.recorded_at)<Date.parse(before.recorded_at)||before.collection_time_known&&after.collection_time_known&&Date.parse(after.collected_at)<Date.parse(before.collected_at)))throw new ProgressInputError('所选回答不适合登记改善，请核对记录顺序和回答有效性。')
    for(const [side,option,quote] of [['before',before,form.beforeQuote],['after',after,form.afterQuote]]){
      const context=raws[side]
      if(!bounded(quote,2000,true)||context?.probeId!==option.probe_id||context.sha256!==option.response_sha256||typeof context.raw!=='string'||!context.raw.includes(quote))throw new ProgressInputError('请读取所选完整回答，并从原文复制不超过2000字的精确摘录。')
      body[`${side}_probe_id`]=option.probe_id;body[`${side}_quote`]=quote;proof[side]=option.response_sha256
    }
  }
  return {body,proof}
}
export const progressRequestSha=(project,payload)=>externalSha({operation:'append_optimization_progress',project_id:project.id,payload})
export function progressReceipt(project,reply,attempt){
  const receipt=reply?.data?.receipt,body=attempt.body
  if(reply?.code!==200||receipt?.request_id!==body.request_id||receipt.request_sha256!==attempt.digest||typeof receipt.idempotent!=='boolean')throw new ProgressInputError('保存回执未核对，原请求保留，请核对同一次保存。')
  const value=record(project,reply.data.record)
  if(value.request_id!==body.request_id||value.project_identity_sha256!==body.expected_project_identity_sha256||value.direction!==body.direction||value.action_summary!==body.action_summary||value.status!==body.status
    ||(body.action_at?Date.parse(value.action_at)!==Date.parse(body.action_at):value.action_at!==null)||value.review_note!==(body.review_note||'')
    ||value.request_sha256!==undefined&&value.request_sha256!==attempt.digest)throw new ProgressInputError('进展回执与本次填写不一致，原请求保留。')
  for(const side of ['before','after']){
    if(body.status==='preparation'){if(value[side]!==null)throw new ProgressInputError('准备记录出现非本次证据，原请求保留。')}
    else if(value[side]?.probe_id!==body[`${side}_probe_id`]||value[side]?.quote!==body[`${side}_quote`]||value[side]?.response_sha256!==attempt.proof[side])throw new ProgressInputError('进展回执的原文摘录或摘要不一致，原请求保留。')
  }
  return value
}
export function progressExportView(page,records=page.records){
  const excerpt=value=>value?{evidence_id:value.probe_id,cycle_id:value.cycle_id,case_id:value.case_id,quote:value.quote,source:progressSourceLabel(value),recorded_at:value.recorded_at,collected_at:value.collection_time_known?value.collected_at:null,context_note:progressContextNote(value)}:null
  const map=item=>({id:item.id,recorded_at:item.recorded_at,action_at:item.action_at,direction:item.direction,action_summary:item.action_summary,status:item.status,review_note:item.review_note,before:excerpt(item.before),after:excerpt(item.after),
    entity:item.project_identity?{name:item.project_identity.name,website:item.project_identity.website,entity_type:item.project_identity.entity_type,basis:item.project_identity_basis}:null,current_entity_match:item.current_entity_match})
  return {summary:{observed_improvement:page.countsCurrent.observed_improvement,not_observed:page.countsCurrent.not_observed,preparation:page.countsCurrent.preparation},
    records:records.filter(item=>item.current_entity_match===true).map(map),archived_records:records.filter(item=>item.current_entity_match!==true).map(map),
    archive_summary:{historical:page.countsHistory.total,unknown:page.countsUnknown.total}}
}
