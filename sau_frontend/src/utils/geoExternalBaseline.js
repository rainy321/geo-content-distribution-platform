import {sameBrand} from './geoJourney.js'
import {externalAnswerMetadata} from './geoExternalFeedback.js'
import {createFeedbackSession} from './geoFeedbackSession.js'

export const externalBaselineSession=createFeedbackSession({entries:12,bytes:8*1024*1024})
export class ExternalBaselineInputError extends Error {}
export function externalBaselineError(cause){
  if(cause instanceof ExternalBaselineInputError)return cause.message.slice(0,180)
  const status=cause?.response?.status
  return ({400:'内容或原题身份未通过校验，原填写保留，请核对后再保存。',401:'登录状态需要核对，原填写保留。',403:'页面来源或访问权限需要核对，原填写保留。',404:'原项目或已固定原题暂未找到，原填写保留。',409:'品牌资料或本次请求已变化，原填写与请求保留，请先导出再核对原题。',413:'请求超过大小限制，原填写保留，请自行保留完整原文。'}[status]
    ||'操作结果尚未确认，原填写与已发出的请求保留，请核对同一次操作。')
}
const clone=value=>JSON.parse(JSON.stringify(value))
const hash=value=>typeof value==='string'&&/^[a-f0-9]{64}$/.test(value)
const id=value=>typeof value==='string'&&/^[A-Za-z0-9_-]{1,100}$/.test(value)
const projectId=value=>Number.isSafeInteger(value)&&value>0
const canonical=value=>Array.isArray(value)?value.map(canonical):value&&typeof value==='object'?Object.fromEntries(Object.keys(value).sort().map(key=>[key,canonical(value[key])])):value
export const externalProjectKey=project=>JSON.stringify([project?.id,...['name','website','entity_type','company','product','industry','description','audience','region','aliases','official_urls','keywords','competitors'].map(key=>project?.[key])])
export const externalBaselineDraftKey=(project,plan)=>projectId(project?.id)?`external-baseline:${externalProjectKey(project)}:${plan?.plan_sha256||'template'}`:null
export const isExternalCycle=cycle=>cycle?.baseline===null&&cycle?.baseline_source==='user_reported_external'&&cycle?.external_baseline?.kind==='geo_external_baseline_v1'
export const nativeCycleCapability=(cycle,name)=>!isExternalCycle(cycle)&&cycle?.baseline_source!=='user_reported_external'&&cycle?.capabilities?.[name]!==false
export const originalCycleCases=cycle=>isExternalCycle(cycle)?cycle.test_plan?.cases||[]:cycle?.baseline?.cases||[]
export function externalBaselineSummary(cycle){
  const records=isExternalCycle(cycle)?cycle.external_baseline.observations||[]:[]
  const observed=new Set(records.filter(row=>row.source==='user_reported_external'&&row.native_verified===false&&row.cycle_id===cycle.id).map(row=>row.case_id))
  return {recorded:observed.size,total:originalCycleCases(cycle).length,text:`起始观察已保存 ${observed.size} 题（计划共 ${originalCycleCases(cycle).length} 题，尚未全测）；后续回答在反馈中单独追加，起始观察保留。用户提供，采样未独立核验；原生指标不可用。`}
}
export async function externalSha(value){
  const bytes=new TextEncoder().encode(typeof value==='string'?value:JSON.stringify(canonical(value)))
  const digest=await globalThis.crypto.subtle.digest('SHA-256',bytes)
  return Array.from(new Uint8Array(digest),byte=>byte.toString(16).padStart(2,'0')).join('')
}
export const externalOperationSha=(operation,payload)=>externalSha({operation,payload})
export const externalRequestId=()=>globalThis.crypto.randomUUID()
async function verifyPlan(project,value){
  const plan=value?.plan
  if(!projectId(project?.id)||!sameBrand(value?.profile,project)||!hash(value.profile_sha256)||!hash(value.plan_sha256)
    ||plan?.schema_version!=='1.0'||plan.platform!=='doubao_web'||plan.plan_sha256!==value.plan_sha256
    ||!Array.isArray(plan.cases)||plan.cases.length<3||plan.cases.length>12||new Set(plan.cases.map(row=>row.id)).size!==plan.cases.length
    ||!['template_not_ai','user_provided_not_verified'].includes(plan.planner?.method)
    ||plan.cases.some(row=>!id(row.id)||!['discovery','brand_identity','website_guided'].includes(row.category)||typeof row.question!=='string'||!row.question.trim()||row.question.length>800||typeof row.intent!=='string'))throw new ExternalBaselineInputError('原题或品牌范围未核对，当前填写保留。')
  if(await externalSha(value.profile)!==value.profile_sha256||await externalSha(plan.profile)!==value.profile_sha256
    ||await externalSha(Object.fromEntries(['schema_version','platform','profile','cases'].map(key=>[key,plan[key]])))!==value.plan_sha256)throw new ExternalBaselineInputError('原题或品牌摘要不一致，未使用该回执。')
  return clone(value)
}
export async function externalPrepared(project,reply){
  if(reply?.code!==200||reply.data?.requires_user_confirmation!==true)throw new ExternalBaselineInputError('尚未取得可核对原题，请重新准备。')
  return verifyPlan(project,reply.data)
}
export function externalPrepareBody(project,plan,useExisting){
  if(!projectId(project?.id))throw new ExternalBaselineInputError('请先保存当前品牌。')
  if(useExisting&&(!plan||!sameBrand(plan.profile,project)||!hash(plan.plan_sha256)))throw new ExternalBaselineInputError('页面计划与当前对象不一致，请使用固定模板或核对原计划。')
  return {project_id:project.id,...(useExisting?{existing_plan:clone(plan)}:{})}
}
export function externalFreezeBody(project,prepared,confirmed){
  if(!projectId(project?.id)||!prepared||!sameBrand(prepared.profile,project)||confirmed!==true)throw new ExternalBaselineInputError('请先核对原题，再明确确认冻结。')
  return {request_id:externalRequestId(),project_id:project.id,expected_profile_sha256:prepared.profile_sha256,
    expected_plan_sha256:prepared.plan_sha256,plan:clone(prepared.plan),confirmed:true}
}
function verifyReceipt(reply,body,digest){
  const receipt=reply?.data?.receipt
  if(reply?.code!==200||receipt?.request_id!==body.request_id||receipt.request_sha256!==digest||typeof receipt.idempotent!=='boolean')throw new ExternalBaselineInputError('保存回执未核对，原请求保留，请核对并重试同一次保存。')
}
export async function externalFreezeReceipt(project,reply,body,digest){
  verifyReceipt(reply,body,digest)
  const value=await verifyFrozen(project,reply.data.frozen_plan)
  if(!/^[a-f0-9]{32}$/.test(value.id)||value.project_id!==body.project_id||value.revision!==1
    ||value.profile_sha256!==body.expected_profile_sha256||value.plan_sha256!==body.expected_plan_sha256)throw new ExternalBaselineInputError('冻结回执范围不一致，原请求保留。')
  return value
}
async function verifyFrozen(project,input){
  const value=await verifyPlan(project,input)
  if(!/^[a-f0-9]{32}$/.test(value.id)||value.project_id!==project.id||value.revision!==1)throw new ExternalBaselineInputError('固定原题的身份不一致。')
  for(const row of value.plan.cases){
    const marker=value.case_markers?.[row.id]
    if(marker?.basis!=='frozen_plan_text'||marker.category!==row.category||typeof marker.question_with_url!=='boolean'||marker.question_sha256!==await externalSha(row.question))throw new ExternalBaselineInputError('冻结原题标记不一致，未开放复制或录入。')
  }
  return value
}
export async function externalStoredFrozen(project,value){
  if(!projectId(project?.id)||value?.project_id!==project.id||typeof value.current_profile_matches!=='boolean')throw new ExternalBaselineInputError('已固定原题不属于当前项目。')
  return verifyFrozen({...value.profile,id:project.id},value)
}
export async function externalCreateBody(project,frozen,form){
  const selected=frozen?.plan?.cases?.find(row=>row.id===form.caseId)
  if(!projectId(project?.id)||!sameBrand(frozen?.profile,project)||frozen.project_id!==project.id||!selected
    ||typeof form.raw!=='string'||!form.raw.trim()||new TextEncoder().encode(form.raw).length>1024*1024)throw new ExternalBaselineInputError('请选择冻结原题并粘贴1MiB以内的回答原文。')
  return {request_id:externalRequestId(),project_id:project.id,frozen_plan_id:frozen.id,
    expected_profile_sha256:frozen.profile_sha256,expected_plan_sha256:frozen.plan_sha256,
    first_observation:{case_id:selected.id,expected_question_sha256:await externalSha(selected.question),
      raw_response_text:form.raw,metadata:externalAnswerMetadata(form,selected.question)}}
}
export async function externalCreateReceipt(project,reply,body,digest){
  verifyReceipt(reply,body,digest)
  const cycle=reply.data.cycle,probe=reply.data.probe,observation=body.first_observation
  if(!isExternalCycle(cycle)||!id(cycle.id)||!sameBrand(cycle.profile,project)||cycle.project_id!==body.project_id
    ||cycle.test_plan?.plan_sha256!==body.expected_plan_sha256||cycle.external_baseline.profile_sha256!==body.expected_profile_sha256
    ||cycle.external_baseline.frozen_plan_id!==body.frozen_plan_id||cycle.external_baseline.native_verified!==false
    ||cycle.external_baseline.independent_verification!==false||cycle.capabilities?.native_metrics!==false
    ||cycle.capabilities.native_probe!==false||cycle.capabilities.native_retest!==false
    ||probe?.source!=='user_reported_external'||probe.native_verified!==false||probe.cycle_id!==cycle.id
    ||probe.case_id!==observation.case_id||probe.after?.response!==observation.raw_response_text
    ||probe.response_sha256!==await externalSha(observation.raw_response_text)
    ||!cycle.external_baseline.baseline_observation_ids?.includes(probe.id)
    ||!cycle.external_baseline.observations?.some(row=>row.id===probe.id&&row.after?.response===observation.raw_response_text))throw new ExternalBaselineInputError('外部基线回执的对象或原文不一致，原请求保留。')
  await verifyPlan(project,{profile:cycle.profile,profile_sha256:cycle.external_baseline.profile_sha256,plan:cycle.test_plan,plan_sha256:cycle.test_plan.plan_sha256})
  return clone(cycle)
}
