import {externalFeedbackScope} from './geoExternalFeedback.js'
import {publicationReviewUrl} from './geoPublicationReview.js'
const hash=value=>typeof value==='string'&&/^[a-f0-9]{64}$/.test(value)
const identifier=value=>typeof value==='string'&&/^[A-Za-z0-9_-]{1,100}$/.test(value)
export function auditSnapshot(cycle,view,selected) {
  const scope=externalFeedbackScope(cycle)
  if(!scope||!hash(view?.context_sha256)||view.context_sha256!==cycle.website_audit?.context_sha256||
     !Number.isSafeInteger(view.audit_revision)||view.audit_revision<0||view.supported!==true)throw new Error('本轮官网范围尚未就绪，请刷新资料后再检测。')
  const pages=Array.isArray(view.allowed_pages)?view.allowed_pages:[]
  if(!Array.isArray(selected)||selected.length<1||selected.length>5||new Set(selected).size!==selected.length||
     selected.some(id=>!identifier(id)||!pages.some(page=>page.id===id&&publicationReviewUrl(page.url)?.startsWith('https://'))))throw new Error('请选择1到5个已登记的官网页面。')
  return {...scope,payload:{project_id:scope.projectId,expected_plan_sha256:scope.plan,expected_revision:scope.revision,
    expected_context_sha256:view.context_sha256,expected_audit_revision:view.audit_revision,page_ids:[...selected]}}
}
export function auditRunMatches(record,snapshot) {
  const body=snapshot?.payload
  return Boolean(body&&record?.cycle_id===snapshot.cycleId&&record.project_id===snapshot.projectId&&
    record.plan_sha256===snapshot.plan&&record.cycle_revision===snapshot.revision&&record.context_sha256===body.expected_context_sha256&&
    record.revision===body.expected_audit_revision+1&&JSON.stringify(record.selected_page_ids)===JSON.stringify(body.page_ids)&&
    record.evidence_source==='software_public_http'&&record.index_status==='unknown'&&record.ai_adoption==='unknown')
}
export function auditReceipt(reply,snapshot) {
  const data=reply?.data
  if(reply?.code!==200||!auditRunMatches(data?.audit,snapshot)||!Array.isArray(data?.overview?.history)||
     !data.overview.history.some(record=>record.id===data.audit.id&&auditRunMatches(record,snapshot)))throw new Error('未能核对这次检测回执，请检查原记录。')
  return data.overview
}
export const auditStatus=value=>({running:'正在读取官网',completed:'已取得页面内容',completed_with_gaps:'部分页面未完成',failed:'未完成检查',stale_context:'原范围记录，资料已变化',interrupted:'检测中断，结果未知'}[value]||'尚未检测')
export const auditPresence=value=>value===true?'观察到':value===false?'本次未观察到':'未知'
export const auditNamePresence=value=>value?.body_text_present===true?'观察到完整字面':value?.body_literal_substring_present===true?'文字出现，需核对上下文':auditPresence(value?.body_text_present)
