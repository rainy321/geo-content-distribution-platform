import {externalFeedbackScope} from './geoExternalFeedback.js'
const id=value=>typeof value==='string'&&/^[A-Za-z0-9_-]{1,100}$/.test(value)
const hash=value=>typeof value==='string'&&/^[a-f0-9]{64}$/.test(value)
const statuses=['acknowledged','not_acknowledged','unknown']
export const businessReviewStatus=value=>({acknowledged:'原文识别了所选业务资料',not_acknowledged:'原文未识别所选业务资料',unknown:'暂时无法判断'}[value]||'未核对')

export async function rawAnswerHash(probe) {
  if(typeof probe?.after?.response!=='string'||!probe.after.response.trim())throw new Error('当前没有可核对的回答原文。')
  const digest=await globalThis.crypto.subtle.digest('SHA-256',new TextEncoder().encode(probe.after.response))
  return Array.from(new Uint8Array(digest),value=>value.toString(16).padStart(2,'0')).join('')
}

export function businessReviewSnapshot(cycle,probe,context,responseHash) {
  const scope=externalFeedbackScope(cycle)
  if(!scope||!id(probe?.id)||probe.cycle_id!==scope.cycleId||!id(probe.case_id)||!hash(responseHash)||
     context?.cycle_id!==scope.cycleId||context.project_id!==scope.projectId||context.probe_id!==probe.id||
     context.case_id!==probe.case_id||context.plan_sha256!==scope.plan||context.revision!==scope.revision||
     context.response_sha256!==responseHash||!Number.isSafeInteger(context.review_sequence)||context.review_sequence<0||
     context.scope!=='business_material_acknowledgment_only_not_fact_or_effect_verification')throw new Error('回答或事实版本已变化，请重新打开核对窗口。')
  const facts=Array.isArray(context.confirmed_facts)?context.confirmed_facts:[]
  const current=(Array.isArray(cycle.facts)?cycle.facts:[]).filter(fact=>fact.confirmed===true)
  if(facts.length!==current.length||facts.some(fact=>!id(fact?.id)||!current.some(item=>item.id===fact.id&&item.text===fact.text&&(item.source_url??null)===(fact.source_url??null))))throw new Error('事实资料与当前页面不一致，请先刷新本轮。')
  return {...scope,probeId:probe.id,caseId:probe.case_id,responseHash,sequence:context.review_sequence,
    raw:probe.after.response,facts:structuredClone(facts),history:structuredClone(Array.isArray(context.reviews)?context.reviews:[]),
    evaluable:probe.after.answer_evaluable!==false&&!probe.after.answer_error}
}

export function businessReviewPayload(snapshot,form) {
  if(!snapshot||!statuses.includes(form.status)||form.confirmed!==true)throw new Error('请选择核对意见，并确认摘录与资料。')
  if(!snapshot.evaluable&&form.status!=='unknown')throw new Error('这份回答未完成或已被排除，目前只能保留无法判断。')
  if(typeof form.quote!=='string'||!form.quote.trim()||form.quote.length>3000||!snapshot.raw.includes(form.quote))throw new Error('请从回答中复制一段精确原文，最多3000字。')
  const references=form.factIds
  if(!Array.isArray(references)||!references.length||references.length>30||new Set(references).size!==references.length||references.some(value=>!snapshot.facts.some(fact=>fact.id===value)))throw new Error('请选择本轮已核对的事实资料作为比较依据。')
  if(typeof form.note!=='string'||form.note.length>1000)throw new Error('备注最多1000字。')
  return {project_id:snapshot.projectId,case_id:snapshot.caseId,expected_plan_sha256:snapshot.plan,
    expected_revision:snapshot.revision,expected_response_sha256:snapshot.responseHash,expected_review_sequence:snapshot.sequence,
    review:{status:form.status,quote:form.quote,reference_fact_ids:[...references],note:form.note}}
}

export function businessReviewReceipt(reply,snapshot,payload) {
  const value=reply?.data,probe=value?.probe,cycle=value?.cycle,review=probe?.business_relationship_review
  const next=externalFeedbackScope(cycle)
  if(reply?.code!==200||!next||next.cycleId!==snapshot.cycleId||next.projectId!==snapshot.projectId||next.plan!==snapshot.plan||next.revision<snapshot.revision||
     probe?.id!==snapshot.probeId||probe.cycle_id!==snapshot.cycleId||probe.case_id!==snapshot.caseId||
     probe.after?.response!==snapshot.raw||!cycle.probes?.some(item=>item.id===probe.id&&item.after?.response===snapshot.raw)||
     review?.by!=='operator'||review.response_sha256!==snapshot.responseHash||review.status!==payload.review.status||review.quote!==payload.review.quote||
     JSON.stringify(review.reference_fact_ids)!==JSON.stringify(payload.review.reference_fact_ids)||review.note!==payload.review.note||
     !Array.isArray(probe.business_relationship_reviews)||probe.business_review_sequence!==probe.business_relationship_reviews.length||
     probe.business_review_sequence<snapshot.sequence||probe.business_review_sequence>snapshot.sequence+1||
     probe.business_relationship_reviews.at(-1)?.reviewed_at!==review.reviewed_at)throw new Error('未能核对保存回执，填写内容已保留，请先刷新确认。')
  return cycle
}
