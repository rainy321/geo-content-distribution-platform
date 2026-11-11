// A source read belongs to the old answer, never to a new test or account.
const hash=value=>typeof value==='string'&&/^[a-f0-9]{64}$/.test(value)
const id=value=>typeof value==='string'&&/^[a-f0-9]{32}$/.test(value)
const fields=['plan_sha256','revision','response_sha256','raw_result_sha256']
const known=value=>value===true||value===false||value===null
export function probeInspectionContext(cycle,item){
  const view=item?.source_inspection, expected=view?.expected
  if(!id(cycle?.id)||!id(item?.id)||item.cycle_id!==cycle.id||cycle.baseline_source==='user_reported_external'
    ||item.source!=='native_collector'||item.native_verified!==true||item.after?.answer_evaluable!==true||item.after?.answer_error
    ||typeof item.after.response!=='string'||!item.after.response.trim()||view?.available!==true||view.question_sent!==false
    ||item.source_reinspection?.source_collection_complete===true||!expected||Object.keys(expected).sort().join('|')!==[...fields].sort().join('|')
    ||!Number.isInteger(expected.revision)||expected.revision!==cycle.revision
    ||!hash(expected.plan_sha256)||expected.plan_sha256!==cycle.test_plan?.plan_sha256||expected.plan_sha256!==item.plan_sha256
    ||!hash(expected.response_sha256)||!hash(expected.raw_result_sha256)||expected.raw_result_sha256!==item.evidence?.result_sha256
    ||!['primary','secondary'].includes(view.session_slot)||view.session_slot!==item.after.session_slot||view.session_slot!==item.execution?.session_slot
    ||!['consumer','work'].includes(view.client_mode)||view.client_mode!==item.after.client_mode
    ||view.session_slot==='secondary'&&view.client_mode!=='consumer')return null
  return {cycleId:cycle.id,probeId:item.id,expected:{...expected},sessionSlot:view.session_slot,clientMode:view.client_mode,
    question:item.question,response:item.after.response,originalAnswerTimestamp:item.after.collected_at,
    previousInspectionSha256:item.source_reinspection?.inspection_result_sha256||null}
}
export const inspectionScopeMatches=(cycle,scope)=>{
  const item=cycle?.probes?.find(item=>item.id===scope?.probeId),current=probeInspectionContext(cycle,item)
  return Boolean(current&&JSON.stringify(current)===JSON.stringify(scope))
}
export async function freezeProbeInspection(cycle,probeId){
  const scope=probeInspectionContext(cycle,cycle?.probes?.find(item=>item.id===probeId))
  if(!scope)throw Error('这条原生回答尚不满足来源补采条件，请刷新原记录。')
  const bytes=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(scope.response))
  const digest=Array.from(new Uint8Array(bytes),value=>value.toString(16).padStart(2,'0')).join('')
  if(digest!==scope.expected.response_sha256)throw Error('原回答内容与冻结凭据不一致，未补采来源。')
  return scope
}
export const inspectionAckMatches=(ack,scope)=>Boolean(id(ack?.id)&&ack.status==='running'&&ack.kind==='probe_source_inspection'
  &&ack.cycle_id===scope.cycleId&&ack.target_id===scope.probeId&&ack.session_slot===scope.sessionSlot&&ack.requested_client_mode===scope.clientMode)
export function sourceReinspectionView(item){
  const view=item?.source_reinspection
  if(item?.source!=='native_collector'||item.native_verified!==true||item.after?.answer_evaluable!==true
    ||!view||view.kind!=='later_read_only_source_view'||view.integrity_passed!==true
    ||view.new_answer_collected!==false||view.question_sent!==false
    ||!hash(view.inspection_result_sha256)||view.bound_original_raw_sha256!==item.evidence?.result_sha256
    ||view.original_answer_timestamp!==item.after.collected_at||!Number.isFinite(Date.parse(view.inspected_at))
    ||Date.parse(view.inspected_at)<=Date.parse(item.after.collected_at)
    ||typeof view.source_collection_complete!=='boolean'||!Array.isArray(view.displayed_sources)||!Array.isArray(view.citations)
    ||!known(view.source_assessment?.official_source_present)||!known(view.source_assessment?.official_citation_present))return null
  return view
}
