import {externalFeedbackScope} from './geoExternalFeedback.js'
import {webmasterOrigin} from './geoWebmaster.js'
import {createFeedbackSession} from './geoFeedbackSession.js'
export const searchOnboardingSession=createFeedbackSession({entries:16,bytes:128*1024})
export const searchProviders=Object.freeze({
  baidu:{name:'百度搜索资源平台',entry:'https://ziyuan.baidu.com/site/index',submit:'https://ziyuan.baidu.com/linksubmit/index',help:'https://ziyuan.baidu.com/college/articleinfo?id=1003'},
  toutiao:{name:'头条搜索站长平台',entry:'https://zhanzhang.toutiao.com/',submit:'https://zhanzhang.toutiao.com/',help:'https://zhanzhang.toutiao.com/page/outer/docs'}
})
const hash=value=>typeof value==='string'&&/^[a-f0-9]{64}$/.test(value)
const number=value=>Number.isSafeInteger(value)&&value>=0&&value<=1000000
const clone=value=>JSON.parse(JSON.stringify(value))
export const emptySearchProgress=()=>({site_added:null,ownership_confirmed:null,sitemap_status:'unknown',note:''})
export const searchProgressEqual=(a,b)=>Boolean(a&&b&&Object.keys(emptySearchProgress()).every(key=>a[key]===b[key]))
export const searchCheckpointKey=(view,provider)=>`${view.project_id??'unassigned'}:${view.cycle_id}:${view.identity_sha256}:${provider}`
export function createSearchStoppedReceipts(limits={entries:16,bytes:128*1024}){
  const session=createFeedbackSession(limits),keys=new Set()
  return {
    keep(view,body,digest){
      const key=searchCheckpointKey(view,body.provider)+':'+digest
      if(session.read(key))return true
      const result=session.write(key,{cycle_id:view.cycle_id,project_id:view.project_id,identity_sha256:view.identity_sha256,
        provider:body.provider,request_sha256:digest,payload:clone(body),status:'user_stopped_retry_result_unknown',recorded_at:new Date().toISOString()})
      if(result.ok)keys.add(key)
      return result.ok
    },
    list(view){return view?[...keys].map(key=>session.read(key)?.value).filter(value=>value&&value.cycle_id===view.cycle_id
      &&value.project_id===view.project_id&&value.identity_sha256===view.identity_sha256):[]}
  }
}
export const searchStoppedReceipts=createSearchStoppedReceipts()
export const searchScopeKey=cycle=>JSON.stringify([externalFeedbackScope(cycle)?.cycleId,cycle?.project_id,externalFeedbackScope(cycle)?.plan,cycle?.profile?.name,cycle?.profile?.entity_type,cycle?.profile?.website,cycle?.profile?.official_urls])
export const searchRecord=(view,provider)=>view?.providers?.find(item=>item.id===provider)?.record||null
export const searchSitemapLabel=value=>({unknown:'未知',not_submitted:'尚未提交',pending:'已提交，处理中',processed:'平台显示处理成功',failed:'平台显示失败'}[value]||'未知')
export function validSearchProgress(value){
  return Boolean(value&&Object.keys(value).length===4&&['site_added','ownership_confirmed'].every(key=>value[key]===null||typeof value[key]==='boolean')
    &&!(value.site_added===false&&value.ownership_confirmed===true)&&['unknown','not_submitted','pending','processed','failed'].includes(value.sitemap_status)&&typeof value.note==='string'&&Array.from(value.note).length<=500)
}
export function searchOverview(cycle,value){
  const scope=externalFeedbackScope(cycle),origin=webmasterOrigin(cycle)
  if(!scope||value?.cycle_id!==scope.cycleId||value.project_id!==scope.projectId||value.plan_sha256!==scope.plan||!number(value.revision)||value.revision<scope.revision)throw new Error('任务或资料版本已变化，请刷新本轮。')
  if(value.supported===false)return clone(value)
  if(value.supported!==true||!origin||value.site_origin!==origin||value.sitemap_url!==origin+'/sitemap.xml'||!hash(value.identity_sha256)||!hash(value.context_sha256)
    ||!Array.isArray(value.providers)||value.providers.length!==2||new Set(value.providers.map(item=>item.id)).size!==2)throw new Error('官网范围尚未确认，请刷新。')
  for(const item of value.providers){
    const known=searchProviders[item.id],record=item.record
    if(!known||item.entry_url!==known.entry||item.submission_url!==known.submit||item.help_url!==known.help)throw new Error('官方入口不匹配，已阻止打开或保存。')
    if(record&&(Object.keys(record).length!==11||record.provider!==item.id||record.site_origin!==origin||record.identity_sha256!==value.identity_sha256||!number(record.revision)||record.revision<1
      ||record.scope?.cycle_id!==value.cycle_id||record.scope.project_id!==value.project_id||record.scope.plan_sha256!==value.plan_sha256||record.scope.site_origin!==origin
      ||typeof record.updated_at!=='string'||!Number.isFinite(Date.parse(record.updated_at))
      ||record.evidence_source!=='user_reported_platform_progress'||record.software_verified!==false||record.software_submitted!==false||!hash(record.request_sha256)||!validSearchProgress(record.progress)))throw new Error('已保存进度的身份待核对，请刷新。')
  }
  return clone(value)
}
export function searchPayload(view,provider,progress,baseRevision){
  if(!view?.supported||!searchProviders[provider]||!hash(view.context_sha256)||!number(baseRevision)||!validSearchProgress(progress))throw new Error('请核对当前平台和填写内容；备注最多500字。')
  return {project_id:view.project_id,expected_plan_sha256:view.plan_sha256,expected_revision:view.revision,
    expected_context_sha256:view.context_sha256,provider,expected_provider_revision:baseRevision,progress:clone(progress)}
}
export async function searchRequestSha(payload){
  const canonical=value=>Array.isArray(value)?value.map(canonical):value&&typeof value==='object'?Object.fromEntries(Object.keys(value).sort().map(key=>[key,canonical(value[key])])):value
  const digest=await globalThis.crypto.subtle.digest('SHA-256',new TextEncoder().encode(JSON.stringify(canonical(payload))))
  return Array.from(new Uint8Array(digest),value=>value.toString(16).padStart(2,'0')).join('')
}
export function searchSavedRequest(view,body,digest){
  const record=searchRecord(view,body.provider)
  return Boolean(record&&record.revision===body.expected_provider_revision+1&&record.request_sha256===digest&&searchProgressEqual(record.progress,body.progress))
}
export function searchReceipt(reply,captured,body,digest){
  const next=reply?.data?.overview,receipt=reply?.data?.receipt
  const cycle={id:captured.cycle_id,project_id:captured.project_id,revision:body.expected_revision,test_plan:{plan_sha256:captured.plan_sha256},profile:{website:captured.site_origin}}
  const value=searchOverview(cycle,next)
  if(reply?.code!==200||value.identity_sha256!==captured.identity_sha256||!searchSavedRequest(value,body,digest)
    ||receipt?.provider!==body.provider||receipt.identity_sha256!==captured.identity_sha256||receipt.revision!==body.expected_provider_revision+1||receipt.request_sha256!==digest)throw new Error('保存回执未核对，原填写保留；请核对同次保存。')
  return value
}
export function searchCanApply(next,current){
  return !current||next.identity_sha256===current.identity_sha256&&next.revision>=current.revision&&Object.keys(searchProviders).every(id=>(searchRecord(next,id)?.revision||0)>=(searchRecord(current,id)?.revision||0))
}
