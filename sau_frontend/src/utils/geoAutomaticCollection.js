import {sameBrand} from './geoJourney.js'
import {externalSha,isExternalCycle} from './geoExternalBaseline.js'

export const automaticCollectionUnavailable='当前软件缺少自动网页采集能力，请更新软件；不会切换到旧采集方式或要求手机录入。'
export const automaticBridgeReady=api=>['plan_automatic','start_automatic_run'].every(name=>typeof api?.[name]==='function')
export function automaticCollectionOptions(profileMode,model=''){
  if(!['consumer','work'].includes(profileMode)||typeof model!=='string'||model.length>100)throw Error('请核对专用会话与模型选项。')
  return {profile_mode:profileMode,model_label:model.trim()}
}
export function requireAutomaticBridge(api){
  if(!automaticBridgeReady(api))throw Error(automaticCollectionUnavailable)
  return api
}
export function storedSessionScope(execution={}){
  const scope={}
  if(Object.hasOwn(execution,'profile_mode')){
    if(!['consumer','work'].includes(execution.profile_mode))throw Error('原测试专用会话范围无效，请核对原记录；未打开其他会话。')
    scope.profile_mode=execution.profile_mode
  }
  if(Object.hasOwn(execution,'mode_policy')){
    if(execution.mode_policy!=='available_web'||!scope.profile_mode)throw Error('原自动测试会话策略或范围未核对，未打开其他会话。')
    scope.mode_policy=execution.mode_policy
  }
  return scope
}
const identifier=value=>typeof value==='string'&&/^[A-Za-z0-9_-]{1,100}$/.test(value)
export function automaticSourceRequest(query){
  if(query?.source_cycle===undefined)return null
  const projectId=Number(query.journey_project)
  if(!identifier(query.source_cycle)||!Number.isSafeInteger(projectId)||projectId<1)throw Error('原任务单或品牌范围无效，未载入旧原题。')
  return {cycleId:query.source_cycle,projectId}
}
export function externalAutomaticRoute(cycle,project){
  if(!isExternalCycle(cycle)||!identifier(cycle.id)||!Number.isSafeInteger(project?.id)||project.id<1||cycle.project_id!==project.id||!sameBrand(cycle.profile,project))throw Error('原任务单与当前品牌不一致，未建立自动测试。')
  return {path:'/geo-tests',query:{source_cycle:cycle.id,journey_project:String(project.id)}}
}
export async function automaticPlanFromExternalCycle(cycle,project){
  externalAutomaticRoute(cycle,project)
  const plan=cycle.test_plan
  if(plan?.schema_version!=='1.0'||plan.platform!=='doubao_web'||!sameBrand(plan.profile,project)||!Array.isArray(plan.cases)||plan.cases.length<3||plan.cases.length>12
    ||new Set(plan.cases.map(item=>item?.id)).size!==plan.cases.length||plan.cases.some(item=>!identifier(item?.id)||!['discovery','brand_identity','website_guided'].includes(item.category)
      ||typeof item.question!=='string'||!item.question.trim()||item.question.length>800||typeof item.intent!=='string')
    ||!(/^[a-f0-9]{64}$/).test(plan.plan_sha256||''))throw Error('原任务单的固定问题尚未核对，未建立自动测试。')
  const digest=await externalSha(Object.fromEntries(['schema_version','platform','profile','cases'].map(key=>[key,plan[key]])))
  if(digest!==plan.plan_sha256)throw Error('原题摘要不一致，未建立自动测试。')
  return JSON.parse(JSON.stringify(plan))
}
export function actualCollectionMode(report){
  const modes=new Set((report?.cases||[]).map(item=>item?.client_mode).filter(value=>['consumer','work'].includes(value)))
  return modes.size===1?(modes.has('work')?'豆包工作网页':'豆包普通对话网页'):modes.size>1?'含不同网页模式，分别核对原文':'实际网页模式未知'
}
