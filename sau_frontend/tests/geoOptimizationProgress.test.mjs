import assert from 'node:assert/strict'
import fs from 'node:fs'
import test from 'node:test'
import * as Vue from 'vue'
import {renderToString} from '@vue/server-renderer'
import {parse,compileScript,compileTemplate,compileStyle} from '@vue/compiler-sfc'
import * as progress from '../src/utils/geoOptimizationProgress.js'
import {externalSha} from '../src/utils/geoExternalBaseline.js'
import {externalWriteUncertain} from '../src/utils/geoExternalFeedback.js'
import * as checkpoints from '../src/utils/geoFeedbackSession.js'
import {journeyDate} from '../src/utils/geoJourney.js'
import {progressEvidenceLayout} from '../src/utils/geoOptimizationProgressExport.js'

const clone=value=>JSON.parse(JSON.stringify(value))
const project={id:7,name:'合成公开服务有限公司',website:'https://example.org/company',entity_type:'company',aliases:['合成服务','Synthetic Service'],official_urls:['https://example.org/about']}
const projectIdentity=value=>({name:value.name,entity_type:value.entity_type,website:value.website,aliases:clone(value.aliases||[]),official_urls:clone(value.official_urls||[]),
  measurement_signature:{semantics:'target_entity_scoped_urls',entity_type:value.entity_type,name:value.name.trim().toLowerCase(),
    aliases:[...new Set((value.aliases||[]).map(item=>item.trim().toLowerCase()))].sort(),official_scopes:[...new Set([value.website,...value.official_urls||[]])].map(item=>{
      const url=new URL(item);return [url.protocol.slice(0,-1),url.hostname.toLowerCase(),Number(url.port)||(url.protocol==='https:'?443:80),url.pathname.replace(/\/+$/,'')||'/']
    }).sort((a,b)=>JSON.stringify(a).localeCompare(JSON.stringify(b))),legacy_product:'',legacy_company:''}})
const identity=projectIdentity(project),identitySha=await externalSha(identity)
const oldIdentity=projectIdentity({...project,name:'历史合成主体',website:'https://example.org/old-company',aliases:[],official_urls:[]}),oldIdentitySha=await externalSha(oldIdentity)
const payload=(scope,value,selected,raw)=>progress.progressPayload(scope,value,selected,raw,identitySha)
const question='请核对合成公司的公开业务和官网。'
const plan={schema_version:'1.0',platform:'doubao_web',profile:project,cases:[{id:'q01',category:'brand_identity',question}]}
const planSha=await externalSha(plan),questionSha=await externalSha(question)
const rawBefore='  之前：暂未找到足够的公开信息。\r\n不能确认官网。  '
const rawAfter='\n  之后：找到官网名称，是否被引用仍未知。\n保留否定与限制。  '
const beforeSha=await externalSha(rawBefore),afterSha=await externalSha(rawAfter)
const option=(side='before',changes={})=>({probe_id:`probe-${side}`,cycle_id:`cycle-${side}`,case_id:'q01',plan_sha256:planSha,
  question,question_sha256:questionSha,effective_question_sha256:questionSha,observed_question:null,question_basis:'frozen_question',
  source:'user_reported_external',native_verified:false,independent_verification:false,
  recorded_at:side==='before'?'2026-10-28T10:00:00+08:00':'2026-10-29T10:00:00+08:00',
  collected_at:null,collection_time_known:false,client_mode:null,model_label:null,answer_evaluable:null,answer_error:null,
  excluded_from_improvement:false,conditions:{same_question:null,client_mode_match:null,model_label_match:null},context_attestations:[],
  response_sha256:side==='before'?beforeSha:afterSha,response_sha256_basis:'stored_and_verified',
  response_excerpt:side==='before'?rawBefore:rawAfter,response_truncated:false,response_chars:(side==='before'?rawBefore:rawAfter).length,...changes})
const options=()=>[option(),option('after')]
const raws=()=>({before:{probeId:'probe-before',sha256:beforeSha,raw:rawBefore},after:{probeId:'probe-after',sha256:afterSha,raw:rawAfter}})
const form=(changes={})=>({...progress.emptyProgressForm(),direction:'  官网资料  ',actionSummary:'  核对并补充公开说明。  ',...changes})
const comparisonForm=(changes={})=>form({status:'observed_improvement',beforeProbeId:'probe-before',afterProbeId:'probe-after',beforeQuote:rawBefore,afterQuote:rawAfter,...changes})
const observationForm=(changes={})=>form({attachObservation:true,observationProbeId:'probe-after',observationQuote:rawAfter,...changes})
const observationRaw=()=>({observation:{probeId:'probe-after',sha256:afterSha,raw:rawAfter}})
const snapshot=(value,quote)=>{
  const {response_excerpt,response_truncated,response_chars,...metadata}=value
  return {...metadata,quote}
}
const record=(changes={})=>({id:'progress-a',project_id:7,request_id:'a'.repeat(32),status:'preparation',direction:'官网资料',
  action_summary:'核对并补充公开说明。',review_note:'',recorded_at:'2026-10-29T11:00:00+08:00',action_at:null,
  assessment_basis:'operator_review_of_server_quotes',independent_verification:false,native_metrics_available:false,causal_effect:null,
  project_identity:clone(identity),project_identity_sha256:identitySha,project_identity_basis:'stored_at_record',current_entity_match:true,
  evidence_kind:'no_evidence',before:null,after:null,...changes})
const emptyCounts=()=>({total:0,observed_improvement:0,not_observed:0,preparation:0,native_only:0,user_reported_included:0,research_import_included:0,no_evidence:0,independently_verified_effects:0})
const countRecords=records=>{const counts=emptyCounts();for(const value of records){counts.total++;counts[value.status]++;counts[value.evidence_kind]++}return counts}
function pageReply(records=[record()],overrides={}){
  const counts=countRecords(records)
  return {code:200,data:{project_id:7,records:clone(records),project_identity:clone(identity),project_identity_sha256:identitySha,counts,
    counts_current:overrides.counts||countRecords(records.filter(item=>item.current_entity_match===true)),
    counts_history:countRecords(records.filter(item=>item.current_entity_match===false)),counts_unknown:countRecords(records.filter(item=>item.current_entity_match===null)),
    snapshot_sequence:4,has_more:false,next_offset:null,
    probe_options:options(),probe_options_has_more:false,next_probe_offset:null,...overrides}}
}
const cycle=(value=option(),raw=rawBefore,changes={})=>({id:value.cycle_id,project_id:7,test_plan:{plan_sha256:planSha},probes:[{
  id:value.probe_id,cycle_id:value.cycle_id,case_id:value.case_id,plan_sha256:planSha,question,question_sha256:questionSha,
  source:value.source,native_verified:value.native_verified,response_sha256:value.response_sha256,
  after:{response:raw,response_sha256:value.response_sha256}}],...changes})
async function attempt(value=comparisonForm(),selected=options(),raw=raws()){
  const request=payload(project,value,selected,raw)
  return {...request,digest:await progress.progressRequestSha(project,request.body)}
}
function appendReply(request,changes={}){
  const body=request.body,selected=options(),linked=Boolean(body.before_probe_id||body.after_probe_id)
  return {code:200,data:{record:record({request_id:body.request_id,status:body.status,direction:body.direction,action_summary:body.action_summary,
    project_identity:clone(body.expected_project_identity_sha256===oldIdentitySha?oldIdentity:identity),project_identity_sha256:body.expected_project_identity_sha256,
    current_entity_match:body.expected_project_identity_sha256===identitySha,
    review_note:body.review_note||'',action_at:body.action_at||null,evidence_kind:linked?'user_reported_included':'no_evidence',
    before:body.before_probe_id?snapshot(selected.find(item=>item.probe_id===body.before_probe_id),body.before_quote):null,after:body.after_probe_id?snapshot(selected.find(item=>item.probe_id===body.after_probe_id),body.after_quote):null,...changes}),
    receipt:{request_id:body.request_id,request_sha256:request.digest,idempotent:false}}}
}

test('project scope keys include exact identity while new forms default to preparation with unknown evidence',()=>{
  const value=progress.emptyProgressForm()
  assert.equal(value.status,'preparation');assert.equal(value.actionAt,'');assert.equal(value.beforeProbeId,'');assert.equal(value.beforeQuote,'')
  assert.notEqual(progress.emptyProgressForm(),value)
  const key=progress.progressProjectKey(project)
  for(const change of [{id:8},{name:'另一公司'},{website:'https://example.org/other'},{entity_type:'product'},{aliases:['新增别名']},{official_urls:['https://example.org/another-scope']}])assert.notEqual(progress.progressProjectKey({...project,...change}),key)
  for(const invalid of [null,{id:0},{id:'7'},{id:-1}])assert.equal(progress.progressProjectKey(invalid),null)
})

test('status and source labels remain user-recorded categories and unknown status names never resolve prototype fields',()=>{
  assert.equal(progress.progressStatusLabel('observed_improvement'),'用户记录回答改善')
  assert.equal(progress.progressStatusLabel('not_observed'),'未观察到改善');assert.equal(progress.progressStatusLabel('preparation'),'基础准备')
  for(const value of ['unknown','__proto__','constructor','toString'])assert.equal(progress.progressStatusLabel(value),'状态未知')
  assert.equal(progress.progressSourceLabel(option()),'用户提供')
  assert.equal(progress.progressSourceLabel(option('before',{source:'native_collector',native_verified:true})),'软件采集')
  assert.match(progress.progressSourceLabel(option('before',{source:'research_prototype_import'})),/研究导入.*未独立核验/)
})

test('valid paginated data preserves server order, explicit source counts and detached snapshot evidence without response excerpts',()=>{
  const request=payload(project,comparisonForm(),options(),raws())
  const compared=appendReply(request).data.record
  const reply=pageReply([record({id:'newest'}),{...compared,id:'older'}]),value=progress.progressPage(project,reply)
  assert.deepEqual(value.records.map(item=>item.id),['newest','older']);assert.equal(value.counts.preparation,1);assert.equal(value.counts.user_reported_included,1)
  assert.equal(value.snapshot,4);assert.equal(value.hasMore,false);assert.equal(value.options.length,2)
  assert.equal(value.records[1].before.quote,rawBefore);assert.equal(Object.hasOwn(value.records[1].before,'response_excerpt'),false)
  value.records[1].before.quote='local change';assert.equal(reply.data.records[1].before.quote,rawBefore)
})

test('server identities use actual canonical hashes and every non-null row snapshot is verified before display',async()=>{
  const page=progress.progressPage(project,pageReply())
  assert.equal(page.identitySha,identitySha);assert.deepEqual(page.identity,identity)
  assert.equal(await externalSha(page.identity),page.identitySha);assert.equal(await progress.progressVerifyIdentities(page),page)
  const top=clone(page);top.identity.name='另一个对象';await assert.rejects(progress.progressVerifyIdentities(top))
  const row=clone(page);row.records[0].project_identity.website='https://example.org/another-page';await assert.rejects(progress.progressVerifyIdentities(row))
  for(const change of [{project_identity:null},{project_identity_sha256:'invalid'},
    {project_identity_basis:'unknown'},{current_entity_match:'true'},
    {project_identity:clone(identity),project_identity_sha256:identitySha,current_entity_match:false}]){
    assert.throws(()=>progress.progressPage(project,pageReply([record(change)])))
  }
  assert.throws(()=>progress.progressPage(project,pageReply(),{identitySha:oldIdentitySha}),'later pages must retain the first identity CAS guard')
})

test('the same measurement scope permits changed alias order or spelling while retaining distinct immutable snapshot hashes',async()=>{
  const equivalent={...clone(identity),aliases:[...identity.aliases].reverse().map(item=>item.toUpperCase())}
  const equivalentSha=await externalSha(equivalent)
  assert.notEqual(equivalentSha,identitySha)
  assert.deepEqual(equivalent.measurement_signature,identity.measurement_signature)
  const page=progress.progressPage(project,pageReply([record({project_identity:equivalent,project_identity_sha256:equivalentSha,current_entity_match:true})]))
  await progress.progressVerifyIdentities(page)
  assert.equal(page.records[0].current_entity_match,true);assert.equal(page.countsCurrent.total,1)
})

test('old and unknown entity records remain archived with their own identities and never enter current export counts',async()=>{
  const request=await attempt(),current=appendReply(request).data.record
  const historical=record({id:'history-a',project_identity:clone(oldIdentity),project_identity_sha256:oldIdentitySha,current_entity_match:false,project_identity_basis:'derived_frozen_profile'})
  const unknown=record({id:'unknown-a',project_identity:null,project_identity_sha256:null,current_entity_match:null,project_identity_basis:'unknown'})
  const page=await progress.progressVerifyIdentities(progress.progressPage(project,pageReply([current,historical,unknown])))
  assert.equal(page.counts.total,3);assert.equal(page.countsCurrent.total,1);assert.equal(page.countsHistory.total,1);assert.equal(page.countsUnknown.total,1)
  const view=progress.progressExportView(page)
  assert.deepEqual(view.summary,{observed_improvement:1,not_observed:0,preparation:0})
  assert.deepEqual(view.records.map(item=>item.id),[current.id]);assert.deepEqual(view.archived_records.map(item=>item.id),['history-a','unknown-a'])
  assert.deepEqual(view.archive_summary,{historical:1,unknown:1})
  assert.equal(view.archived_records[0].entity.name,oldIdentity.name);assert.equal(view.archived_records[0].entity.website,oldIdentity.website)
  assert.equal(view.archived_records[0].entity.basis,'derived_frozen_profile');assert.equal(view.archived_records[1].entity,null)
  assert.equal(view.archived_records[1].current_entity_match,null)
})

test('scope counters partition every total category and invalid grouped or null identity claims are refused',()=>{
  for(const mutation of [reply=>{reply.data.counts_current=emptyCounts()},reply=>{reply.data.counts_unknown.independently_verified_effects=1},
    reply=>{delete reply.data.counts_history},reply=>{reply.data.records[0].project_identity=null;reply.data.records[0].project_identity_sha256=null;reply.data.records[0].current_entity_match=null},
    reply=>{reply.data.project_identity_sha256='invalid'}]){
    const reply=pageReply();mutation(reply);assert.throws(()=>progress.progressPage(project,reply))
  }
})

test('new appends require the displayed identity SHA and an idempotent old request retains its original identity guard',async()=>{
  for(const guard of [undefined,null,'not-a-hash'])assert.throws(()=>progress.progressPayload(project,form(),[],{},guard))
  const request=await attempt(form()),reply=appendReply(request)
  assert.equal(request.body.expected_project_identity_sha256,identitySha)
  reply.data.record.project_identity_sha256=oldIdentitySha;reply.data.record.project_identity=clone(oldIdentity);reply.data.record.current_entity_match=false
  assert.throws(()=>progress.progressReceipt(project,reply,request))
  const prior=progress.progressPayload(project,form(),[],{},oldIdentitySha),frozen={...prior,digest:await progress.progressRequestSha(project,prior.body)}
  const retry=appendReply(frozen);retry.data.receipt.idempotent=true
  assert.equal(progress.progressReceipt(project,retry,frozen).project_identity_sha256,oldIdentitySha)
  assert.equal(frozen.body.expected_project_identity_sha256,oldIdentitySha)
  assert.notEqual(await progress.progressRequestSha(project,{...frozen.body,expected_project_identity_sha256:identitySha}),frozen.digest)
})

test('project, page limits, snapshots, duplicate IDs and malformed record or evidence pagination fail closed',()=>{
  const base=pageReply()
  const mutations=[value=>{value.code=500},value=>{value.data.project_id=8},value=>{value.data.project_id='7'},
    value=>{value.data.records=Array.from({length:21},(_,i)=>record({id:`row-${i}`}))},value=>{value.data.records=[record(),record()]},
    value=>{value.data.records[0].project_id=8},value=>{value.data.snapshot_sequence=-1},value=>{value.data.has_more=true;value.data.next_offset=19},
    value=>{value.data.next_offset=20},value=>{value.data.probe_options_has_more=true;value.data.next_probe_offset=99},
    value=>{value.data.next_probe_offset=100},value=>{value.data.probe_options=[option(),option()]},
    value=>{value.data.probe_options=Array.from({length:101},(_,i)=>option('before',{probe_id:`probe-${i}`}))}]
  for(const mutation of mutations){const value=clone(base);mutation(value);assert.throws(()=>progress.progressPage(project,value))}
  assert.throws(()=>progress.progressPage(project,base,{snapshot:5}))
  assert.throws(()=>progress.progressPage({id:0},base))
  const next=pageReply(Array.from({length:20},(_,index)=>record({id:`next-${index}`})), {has_more:true,next_offset:40,probe_options_has_more:true,next_probe_offset:200})
  assert.equal(progress.progressPage(project,next,{offset:20,probeOffset:100,snapshot:4}).nextOffset,40)
  assert.equal(progress.progressPage(project,next,{offset:20,probeOffset:100,snapshot:4}).nextProbeOffset,200)
})

test('counts reject negative, fractional, inconsistent or independently verified effect claims',()=>{
  for(const mutation of [counts=>{counts.total=-1},counts=>{counts.preparation=0.5},counts=>{counts.total=2},
    counts=>{counts.independently_verified_effects=1},counts=>{counts.native_only=-1},counts=>{delete counts.research_import_included}]){
    const reply=pageReply();mutation(reply.data.counts);assert.throws(()=>progress.progressPage(project,reply))
  }
  const reply=pageReply();reply.data.counts.native_only=1
  assert.throws(()=>progress.progressPage(project,reply),'source categories must describe the same total records')
})

test('forged verification, causal conclusions and invalid option identities are rejected before use',()=>{
  for(const change of [{independent_verification:true},{native_metrics_available:true},{causal_effect:'proved'},
    {assessment_basis:'verified_geo_success'},{status:'guaranteed_success'}])assert.throws(()=>progress.progressPage(project,pageReply([record(change)])))
  for(const change of [{probe_id:'../private'},{plan_sha256:'bad'},{effective_question_sha256:'bad'},
    {independent_verification:true},{question_basis:'verified_same_question'},{response_sha256_basis:'client_claim'},
    {client_mode:'native'},{collection_time_known:true,collected_at:null},{recorded_at:'2026-10-29T11:00:00'},
    {context_attestations:{}},{answer_evaluable:'true'},{excluded_from_improvement:'false'}]){
    const reply=pageReply([],{probe_options:[option('before',change)]});assert.throws(()=>progress.progressPage(project,reply))
  }
})

test('evidence source and native verification must agree rather than relabeling research or external records as software sampling',()=>{
  for(const change of [{source:'invented_verifier',native_verified:true},
    {source:'research_prototype_import',native_verified:true},{source:'user_reported_external',native_verified:true},
    {source:'native_collector',native_verified:false}]){
    assert.throws(()=>progress.progressPage(project,pageReply([],{probe_options:[option('before',change)]})))
  }
})

test('record-only pagination can omit option payloads without altering record validation or snapshot guards',()=>{
  const reply=pageReply();delete reply.data.probe_options;delete reply.data.probe_options_has_more;delete reply.data.next_probe_offset
  const value=progress.progressPage(project,reply,{includeProbes:false,snapshot:4})
  assert.deepEqual(value.options,[]);assert.equal(value.optionsHasMore,false);assert.equal(value.nextProbeOffset,null)
  reply.data.records[0].causal_effect=true;assert.throws(()=>progress.progressPage(project,reply,{includeProbes:false}))
})

test('a claimed next record page must be full and legacy research evidence keeps an unknown recording time',()=>{
  for(const records of [[],[record()]])assert.throws(()=>progress.progressPage(project,pageReply(records,{has_more:true,next_offset:20})))
  const legacy=option('before',{source:'research_prototype_import',native_verified:false,recorded_at:null,response_sha256_basis:'derived_from_server_record'})
  const value=progress.progressPage(project,pageReply([],{probe_options:[legacy]}))
  assert.equal(value.options[0].recorded_at,null);assert.equal(value.options[0].collection_time_known,false);assert.equal(value.options[0].collected_at,null)
  assert.match(progress.progressSourceLabel(value.options[0]),/未独立核验/)
})

test('evidence categories must match their actual sources and optional server request digests remain verified',async()=>{
  const request=await attempt(),reply=appendReply(request)
  reply.data.record.evidence_kind='native_only'
  assert.throws(()=>progress.progressPage(project,pageReply([reply.data.record])))
  const forged=pageReply([record({request_sha256:'not-a-hash'})]);assert.throws(()=>progress.progressPage(project,forged))
  const matched=appendReply(request,{request_sha256:request.digest});assert.equal(progress.progressReceipt(project,matched,request).request_sha256,request.digest)
  matched.data.record.request_sha256='a'.repeat(64);assert.throws(()=>progress.progressReceipt(project,matched,request))
})

test('unknown actual question, model, completeness and collection time remain unknown user observations',()=>{
  const value=option(),note=progress.progressContextNote(value)
  assert.match(note,/实际同题未知/);assert.match(note,/界面未知/);assert.match(note,/采集时间未知/);assert.match(note,/独立条件待核对/)
  assert.doesNotMatch(note,/已独立核验|确认首条|已完成|界面一致/)
  const selected=progress.progressPage(project,pageReply([])).options
  assert.equal(selected[0].answer_evaluable,null);assert.equal(selected[0].observed_question,null);assert.equal(selected[0].native_verified,false)
  const prepared=payload(project,form({beforeProbeId:value.probe_id,afterProbeId:'probe-after',beforeQuote:rawBefore,afterQuote:rawAfter}),selected,raws())
  assert.equal(prepared.body.status,'preparation');assert.deepEqual(prepared.proof,{})
  assert.equal(Object.hasOwn(prepared.body,'before_probe_id'),false)
})

test('complete raw responses are read only from the selected project cycle and validated against actual SHA256',async()=>{
  const value=option(),read=await progress.progressRaw(project,value,cycle(value))
  assert.deepEqual(read,{probeId:value.probe_id,sha256:beforeSha,raw:rawBefore})
  assert.equal(await externalSha(read.raw),value.response_sha256)
  for(const changes of [{project_id:8},{id:'other-cycle'},{test_plan:{plan_sha256:'a'.repeat(64)}},
    {probes:[...cycle(value).probes,...cycle(value).probes]},{probes:[]},
    {probes:[{...cycle(value).probes[0],after:{response:rawBefore.trim(),response_sha256:beforeSha}}]},
    {probes:[{...cycle(value).probes[0],response_sha256:'a'.repeat(64)}]}]){
    await assert.rejects(progress.progressRaw(project,value,cycle(value,rawBefore,changes)))
  }
})

test('raw guards reject a nested probe that claims another cycle even when its raw hash is equal',async()=>{
  const value=option(),reply=cycle(value);reply.probes[0].cycle_id='foreign-cycle'
  await assert.rejects(progress.progressRaw(project,value,reply))
})

test('comparison requires two distinct probes from the same plan, case and effective actual question hash',()=>{
  assert.equal(progress.comparableProgressEvidence(option(),option('after')),true)
  for(const change of [{probe_id:'probe-before'},{plan_sha256:'a'.repeat(64)},{case_id:'q02'},
    {effective_question_sha256:'a'.repeat(64)},{effective_question_sha256:'unknown'}]){
    const selected=[option(),option('after',change)]
    assert.equal(progress.comparableProgressEvidence(...selected),false)
    assert.throws(()=>payload(project,comparisonForm({afterProbeId:selected[1].probe_id}),selected,raws()))
  }
})

test('improvement refuses excluded evidence and reversed recorded order while not-observed remains a separate manual status',()=>{
  for(const change of [{excluded_from_improvement:true},{recorded_at:'2026-10-27T10:00:00+08:00'}]){
    const selected=[option(),option('after',change)]
    assert.throws(()=>payload(project,comparisonForm(),selected,raws()))
    const value=payload(project,comparisonForm({status:'not_observed'}),selected,raws())
    assert.equal(value.body.status,'not_observed')
  }
})

test('improvement independently checks root answer eligibility and known collection order',()=>{
  for(const change of [{answer_evaluable:false},{answer_error:'private_account_context'},
    {collection_time_known:true,collected_at:'2026-10-27T10:00:00+08:00'}]){
    const selected=[option('before',{collection_time_known:true,collected_at:'2026-10-28T10:00:00+08:00'}),option('after',change)]
    assert.throws(()=>payload(project,comparisonForm(),selected,raws()))
  }
})

test('GET observed-improvement snapshots also reject explicitly ineligible answer evidence',()=>{
  const request=payload(project,comparisonForm(),options(),raws()),value=appendReply(request).data.record
  value.after.answer_evaluable=false;value.after.excluded_from_improvement=true
  assert.throws(()=>progress.progressPage(project,pageReply([value])))
})

test('exact quoted whitespace and verified RAM versions form an identity-guarded eleven-field request without full responses or client metrics',()=>{
  const value=payload(project,comparisonForm({reviewNote:'保留限制。',actionAt:'2026-10-29T11:30:00+08:00',
    raw:'PRIVATE_FULL_RESPONSE',causal_effect:true,native_verified:true,brand_rate:1}),options(),raws())
  assert.equal(Object.keys(value.body).length,11)
  assert.deepEqual(Object.keys(value.body).sort(),['request_id','expected_project_identity_sha256','direction','action_summary','status','review_note','action_at','before_probe_id','before_quote','after_probe_id','after_quote'].sort())
  assert.equal(value.body.expected_project_identity_sha256,identitySha)
  assert.equal(value.body.before_quote,rawBefore);assert.equal(value.body.after_quote,rawAfter)
  assert.equal(value.body.direction,'官网资料');assert.equal(value.body.action_summary,'核对并补充公开说明。')
  assert.equal(value.body.action_at,'2026-10-29T03:30:00.000Z')
  assert.deepEqual(value.proof,{before:beforeSha,after:afterSha})
  assert.doesNotMatch(JSON.stringify(value.body),/PRIVATE_FULL_RESPONSE|causal_effect|native_verified|brand_rate|raw_response/)
  assert.doesNotMatch(JSON.stringify(value.proof),/之前|之后|raw/)
})

test('blank, oversized, rewritten or wrong-version quotations are refused before any append request',()=>{
  for(const changes of [{beforeQuote:''},{beforeQuote:' '},{beforeQuote:'改写后的结论'},
    {beforeQuote:'字'.repeat(2001)},{status:'fake_verified'},{direction:''},{direction:'字'.repeat(201)},
    {actionSummary:'字'.repeat(3001)},{reviewNote:'字'.repeat(3001)},{actionAt:'not-a-date'}]){
    assert.throws(()=>payload(project,comparisonForm(changes),options(),raws()))
  }
  for(const before of [{...raws().before,probeId:'other-probe'},{...raws().before,sha256:'a'.repeat(64)},
    {...raws().before,raw:'不同的服务器原文'},null])assert.throws(()=>payload(project,comparisonForm(),options(),{...raws(),before}))
})

test('request hashes bind canonical operation, project and the original unmodified body',async()=>{
  const request=await attempt()
  assert.equal(request.digest,await externalSha({operation:'append_optimization_progress',project_id:7,payload:request.body}))
  assert.equal(await progress.progressRequestSha(project,Object.fromEntries(Object.entries(request.body).reverse())),request.digest)
  assert.notEqual(await progress.progressRequestSha({id:8},request.body),request.digest)
  assert.notEqual(await progress.progressRequestSha(project,{...request.body,before_quote:rawBefore.trim()}),request.digest)
})

test('valid receipts retain exact quotations and corrupt receipts or foreign records cannot replace the acknowledged request',async()=>{
  const request=await attempt(),reply=appendReply(request),value=progress.progressReceipt(project,reply,request)
  assert.equal(value.before.quote,rawBefore);assert.equal(value.after.quote,rawAfter)
  for(const mutation of [value=>{value.code=500},value=>{value.data.receipt.request_id='foreign'},value=>{value.data.receipt.request_sha256='a'.repeat(64)},
    value=>{value.data.receipt.idempotent='true'},value=>{value.data.record.project_id=8},value=>{value.data.record.status='not_observed'},
    value=>{value.data.record.direction='another action'},value=>{value.data.record.action_summary='another summary'},
    value=>{value.data.record.review_note='another note'},value=>{value.data.record.independent_verification=true},
    value=>{value.data.record.causal_effect=1},value=>{value.data.record.native_metrics_available=true},
    value=>{value.data.record.before.quote=rawBefore.trim()},value=>{value.data.record.after.probe_id='other-probe'},
    value=>{value.data.record.before.response_sha256='a'.repeat(64)}]){
    const forged=clone(reply);mutation(forged);assert.throws(()=>progress.progressReceipt(project,forged,request))
  }
  reply.data.receipt.idempotent=true;assert.equal(progress.progressReceipt(project,reply,request).id,value.id)
})

test('preparation receipts cannot quietly attach unrequested evidence',async()=>{
  const request=await attempt(form()),reply=appendReply(request)
  assert.equal(progress.progressReceipt(project,reply,request).before,null)
  reply.data.record.before=snapshot(option(),rawBefore)
  assert.throws(()=>progress.progressReceipt(project,reply,request))
})

test('preparation links one explicitly enabled observation without borrowing comparison drafts or claiming an effect',async()=>{
  assert.equal(progress.emptyProgressForm().attachObservation,false)
  const value=observationForm({beforeProbeId:'probe-before',beforeQuote:rawBefore,afterProbeId:'probe-after',afterQuote:rawAfter})
  const disabled=payload(project,{...value,attachObservation:false},options(),{...raws(),...observationRaw()})
  assert.deepEqual(disabled.proof,{})
  assert.equal(Object.hasOwn(disabled.body,'before_probe_id'),false);assert.equal(Object.hasOwn(disabled.body,'after_probe_id'),false)
  const request=await attempt(value,options(),{...raws(),...observationRaw()})
  assert.equal(request.body.status,'preparation');assert.equal(request.body.after_probe_id,'probe-after');assert.equal(request.body.after_quote,rawAfter)
  assert.equal(Object.hasOwn(request.body,'before_probe_id'),false);assert.deepEqual(request.proof,{after:afterSha})
  assert.doesNotMatch(JSON.stringify(request.body),/attachObservation|observationProbeId|observationQuote|native_verified|causal_effect/)
  const saved=progress.progressReceipt(project,appendReply(request),request),page=progress.progressPage(project,pageReply([saved]))
  assert.equal(saved.before,null);assert.equal(saved.after.quote,rawAfter);assert.equal(saved.evidence_kind,'user_reported_included')
  assert.equal(page.countsCurrent.preparation,1);assert.equal(page.countsCurrent.observed_improvement,0);assert.equal(page.countsCurrent.independently_verified_effects,0)
})

test('single observation requires its exact original ID, SHA and quotation and never accepts a preview or comparison raw',()=>{
  for(const change of [{observationProbeId:''},{observationProbeId:'foreign'},{observationQuote:''},{observationQuote:' '},
    {observationQuote:'改写后的原文'},{observationQuote:'字'.repeat(2001)}])assert.throws(()=>payload(project,observationForm(change),options(),observationRaw()))
  for(const observation of [null,{...observationRaw().observation,probeId:'probe-before'},{...observationRaw().observation,sha256:'a'.repeat(64)},
    {...observationRaw().observation,raw:'只有列表预览'}])assert.throws(()=>payload(project,observationForm(),options(),{...raws(),observation}))
  assert.throws(()=>payload(project,observationForm(),options(),raws()),'comparison RAM cannot stand in for the separate observation read')
})

test('single-observation receipts reject missing, additional, mismatched or rewritten evidence',async()=>{
  const request=await attempt(observationForm(),options(),observationRaw()),reply=appendReply(request)
  assert.equal(progress.progressReceipt(project,reply,request).after.probe_id,'probe-after')
  for(const change of [value=>{value.data.record.after=null;value.data.record.evidence_kind='no_evidence'},
    value=>{value.data.record.before=snapshot(option(),rawBefore)},value=>{value.data.record.after.probe_id='foreign'},
    value=>{value.data.record.after.response_sha256='a'.repeat(64)},value=>{value.data.record.after.quote=rawAfter.trim()}]){
    const bad=clone(reply);change(bad);assert.throws(()=>progress.progressReceipt(project,bad,request))
  }
})

test('export view is an explicit HTML model whitelist, preserving evidence IDs and distinct recorded/collected times',()=>{
  const request=payload(project,comparisonForm(),options(),raws()),recorded=appendReply(request).data.record
  const secrets={raw:'PRIVATE_FULL_RESPONSE',response_excerpt:'PRIVATE_PREVIEW',token:'PRIVATE_TOKEN',account:'PRIVATE_ACCOUNT'}
  Object.assign(recorded,secrets);Object.assign(recorded.before,secrets)
  recorded.before.context_attestations=[{native_verified:false,independent_verification:false,confirmation_text:'PRIVATE_CONDITION_DETAILS'}]
  recorded.before.collected_at='2026-10-28T09:00:00+08:00';recorded.before.collection_time_known=true
  const page=progress.progressPage(project,pageReply([recorded])),value=progress.progressExportView(page)
  assert.deepEqual(value.summary,{observed_improvement:1,not_observed:0,preparation:0})
  assert.deepEqual(Object.keys(value.records[0]).sort(),['id','recorded_at','action_at','direction','action_summary','status','review_note','before','after','entity','current_entity_match'].sort())
  assert.equal(value.records[0].before.evidence_id,'probe-before');assert.equal(value.records[0].before.quote,rawBefore)
  assert.equal(value.records[0].before.recorded_at,option().recorded_at)
  assert.equal(value.records[0].before.collected_at,'2026-10-28T09:00:00+08:00');assert.equal(value.records[0].after.collected_at,null)
  assert.match(value.records[0].before.context_note,/含用户条件声明/)
  assert.doesNotMatch(JSON.stringify(value),/PRIVATE_[A-Z_]+|native_verified|conditions|context_attestations|response_excerpt/)
})

test('malicious server errors are mapped to bounded local messages without backend text or private paths',()=>{
  for(const status of [400,401,403,404,409,413,500]){
    const message=progress.progressError({response:{status,data:{msg:'PRIVATE_TOKEN <script>bad()</script> C:/private/account'}}})
    assert.equal(typeof message,'string');assert.ok(message.length<=220);assert.doesNotMatch(message,/PRIVATE_TOKEN|script|private\/account/)
  }
  assert.doesNotMatch(progress.progressError(Error('PRIVATE_NETWORK_PATH')),/PRIVATE_NETWORK_PATH/)
  assert.equal(progress.progressError(new progress.ProgressInputError('字'.repeat(300))).length,220)
})

test('API wrappers send bounded identity-guarded project GET queries and append bodies without network or AI configuration',()=>{
  const source=fs.readFileSync(new URL('../src/api/geoOptimizationProgress.js',import.meta.url),'utf8').replace(/^import .*$/gm,''),calls=[]
  const api=Function('http',source.replace('export const geoOptimizationProgressApi','const geoOptimizationProgressApi')+';return geoOptimizationProgressApi')({
    get:(...args)=>calls.push(['get',...args]),post:(...args)=>calls.push(['post',...args])})
  const query={offset:20,probe_offset:100,snapshot_sequence:4,include_probes:false,expected_project_identity_sha256:identitySha},body=payload(project,form(),[],{}).body
  api.get(7);api.get(7,query);api.append(7,body)
  assert.deepEqual(calls.map(item=>item.slice(0,3)),[['get','/api/geo-content/projects/7/progress',{}],['get','/api/geo-content/projects/7/progress',query],['post','/api/geo-content/projects/7/progress',body]])
  for(const value of calls)assert.deepEqual(value[3],{timeout:20000,suppressGlobalError:true})
})

const componentFile=new URL('../src/components/GeoOptimizationProgress.vue',import.meta.url)
const {descriptor,errors}=parse(fs.readFileSync(componentFile,'utf8'),{filename:componentFile.pathname})
assert.deepEqual(errors,[])
const compiled=compileScript(descriptor,{id:'optimization-progress-test'})
const names=compiled.scriptSetupAst.flatMap(node=>node.type==='VariableDeclaration'?node.declarations:[]).filter(row=>row.id.type==='Identifier').map(row=>row.id.name)
let componentBody=descriptor.scriptSetup.content
for(const node of compiled.scriptSetupAst.filter(node=>node.type==='ImportDeclaration').reverse())componentBody=componentBody.slice(0,node.start)+componentBody.slice(node.end)
const deferred=()=>{let resolve,reject;const promise=new Promise((done,fail)=>{resolve=done;reject=fail});return {promise,resolve,reject}}
const until=async predicate=>{
  const deadline=performance.now()+2000
  while(!predicate()&&performance.now()<deadline)await new Promise(resolve=>setTimeout(resolve,1))
  assert.ok(predicate(),'component reached the expected async checkpoint within 2 seconds')
}
const mounted=[]
test.afterEach(()=>{while(mounted.length)mounted.pop().stop()})
function component(api={},session=checkpoints.createFeedbackSession(),settings={}){
  const props=Vue.reactive({project:clone(settings.project||project),busy:settings.busy||false,blocked:false})
  const calls={get:[],append:[],raw:[]},busyEvents=[],messages=[],exports=[],downloads=[],unmount=[],effect=Vue.effectScope()
  let saved=clone(settings.records||[])
  const defaults={get:async(id)=>{
    const target=projectIdentity(props.project),targetSha=await externalSha(target)
    const rows=saved.filter(item=>item.project_id===id).map(item=>({...item,current_entity_match:item.project_identity===null?null:JSON.stringify(item.project_identity.measurement_signature)===JSON.stringify(target.measurement_signature)}))
    return pageReply(rows,{project_id:id,project_identity:target,project_identity_sha256:targetSha,probe_options:id===7?options():[]})
  },
    append:async(id,body)=>appendReply({body,proof:{before:beforeSha,after:afterSha},digest:await progress.progressRequestSha({id},body)}),
    raw:async id=>({data:id==='cycle-before'?cycle():cycle(option('after'),rawAfter)})}
  const deps={computed:Vue.computed,reactive:Vue.reactive,ref:Vue.ref,watch:Vue.watch,...progress,...checkpoints,
    optimizationProgressSession:session,externalWriteUncertain,journeyDate,progressEvidenceLayout,defineProps:()=>props,
    defineEmits:()=>((_name,event)=>{busyEvents.push(event);if(settings.echoBusy)props.busy=event.active}),
    onUnmounted:callback=>unmount.push(callback),ElMessage:{info:value=>messages.push(value)},
    exportFeedbackDraft:(...args)=>exports.push(clone(args)),downloadOptimizationProgress:(...args)=>downloads.push(clone(args)),
    geoOptimizationProgressApi:{get:async(...args)=>{calls.get.push(clone(args));return (api.get||defaults.get)(...args)},
      append:async(...args)=>{calls.append.push(clone(args));const reply=await (api.append||defaults.append)(...args);if(reply?.code===200&&reply.data?.record)saved=[reply.data.record,...saved.filter(item=>item.id!==reply.data.record.id)];return reply}},
    geoContentApi:{get:async(...args)=>{calls.raw.push(clone(args));return (api.raw||defaults.raw)(...args)}}}
  const methods=effect.run(()=>Function(...Object.keys(deps),componentBody+`;return {${names.join(',')}}`)(...Object.values(deps)))
  let stopped=false
  const page={...progress,journeyDate,progressEvidenceLayout,...methods,props,calls,busyEvents,messages,exports,downloads,session,stop(){if(stopped)return;stopped=true;for(const close of unmount)close();effect.stop()}}
  mounted.push(page);return page
}
const ready=async page=>{await until(()=>!page.loading.value);assert.ok(page.page.value,page.error.value);return page}
const fillPreparation=page=>Object.assign(page.form,form())
async function renderComponent(page){
  const template=compileTemplate({source:descriptor.template.content,filename:componentFile.pathname,id:'optimization-progress-test',compilerOptions:{bindingMetadata:compiled.bindings}})
  assert.deepEqual(template.errors,[])
  const code=template.code.replace(/import \{([^}]+)\} from "vue"/g,(_all,imports)=>`const {${imports.split(',').map(item=>{const [name,alias]=item.trim().split(/\s+as\s+/);return alias?`${name}:${alias}`:name}).join(',')}}=Vue;`).replace('export function render','return function render')
  const app=Vue.createSSRApp({props:['project','busy','blocked'],setup:()=>page,render:Function('Vue',code)(Vue)},page.props)
  for(const [name,tag] of [['el-button','button'],['el-input','textarea'],['el-checkbox','label'],['el-select','select'],['el-option','option'],['el-form','form'],['el-form-item','label'],['el-dialog','section']]){
    app.component(name,{props:['modelValue','disabled','loading','label'],setup:(props,{slots})=>()=>Vue.h(tag,{disabled:props.disabled,'aria-label':props.label},[slots.default?.()||props.modelValue,slots.footer?.()])})
  }
  return renderToString(app)
}

test('the component automatically GETs the current project only, defaults to preparation and never appends on opening',async()=>{
  const page=await ready(component())
  assert.deepEqual(page.calls.get,[[7]]);assert.deepEqual(page.calls.append,[]);assert.deepEqual(page.calls.raw,[])
  assert.deepEqual(page.form,progress.emptyProgressForm());page.open();assert.equal(page.visible.value,true)
  assert.deepEqual(page.calls.append,[]);assert.equal(page.form.status,'preparation');assert.equal(page.form.actionAt,'')
  const html=await renderComponent(page)
  assert.match(html,/还没有进展记录/);assert.match(html,/基础准备单独记录，不计为回答改善/)
})

test('the editor saves one independently selected observation as preparation and renders a single evidence column',async()=>{
  const page=await ready(component());page.open()
  assert.equal(page.form.attachObservation,false);assert.equal(page.session.stats().entries,0)
  Object.assign(page.form,observationForm());await page.readRaw('observation')
  assert.equal(page.raws.observation.raw,rawAfter);assert.equal(page.pending.value,false)
  await page.save();assert.equal(page.calls.append.length,1);assert.equal(page.error.value,'')
  const body=page.calls.append[0][1];assert.equal(body.status,'preparation');assert.equal(body.after_probe_id,'probe-after')
  assert.equal(Object.hasOwn(body,'before_probe_id'),false);assert.equal(page.records.value[0].before,null)
  assert.equal(page.page.value.countsCurrent.preparation,1);assert.equal(page.page.value.countsCurrent.observed_improvement,0)
  assert.equal(page.session.stats().entries,0);assert.equal(page.raws.observation,null)
  const html=await renderComponent(page)
  assert.match(html,/查看观察摘录/);assert.match(html,/本次观察摘录/);assert.match(html,/class="progress-observations"/)
  assert.doesNotMatch(html,/查看前后精确摘录|未关联回答。|之前的回答摘录|之后的回答摘录/)
})

test('changing the record status disables observation attachment and never borrows either editor evidence',async()=>{
  const page=await ready(component());Object.assign(page.form,observationForm());await page.readRaw('observation')
  const prior={id:page.form.observationProbeId,quote:page.form.observationQuote}
  Object.assign(page.form,{status:'observed_improvement',beforeProbeId:'probe-before',beforeQuote:rawBefore,afterProbeId:'probe-after',afterQuote:rawAfter});assert.equal(page.form.attachObservation,false);assert.equal(page.raws.observation,null)
  page.form.status='preparation'
  assert.equal(page.form.attachObservation,false);assert.equal(page.form.observationProbeId,prior.id);assert.equal(page.form.observationQuote,prior.quote)
  await page.save();assert.equal(page.calls.append.length,1)
  assert.equal(Object.hasOwn(page.calls.append[0][1],'before_probe_id'),false);assert.equal(Object.hasOwn(page.calls.append[0][1],'after_probe_id'),false)
})

test('an explicitly reenabled observation after changing status must reread its original answer',async()=>{
  const page=await ready(component());Object.assign(page.form,observationForm());await page.readRaw('observation')
  page.form.status='not_observed';page.form.status='preparation';page.form.attachObservation=true
  await page.save();assert.equal(page.calls.append.length,0);assert.match(page.error.value,/读取所选完整回答/)
  assert.equal(page.form.observationQuote,rawAfter);await page.readRaw('observation');await page.save()
  assert.equal(page.calls.append.length,1);assert.equal(page.calls.append[0][1].after_quote,rawAfter)
})

test('switching from a comparison to preparation and back preserves quotations but requires fresh original reads',async()=>{
  const page=await ready(component());Object.assign(page.form,comparisonForm());await page.readRaw('before');await page.readRaw('after')
  page.form.status='preparation';page.form.status='observed_improvement'
  assert.equal(page.form.beforeQuote,rawBefore);assert.equal(page.form.afterQuote,rawAfter)
  assert.equal(page.raws.before,null);assert.equal(page.raws.after,null)
  await page.save();assert.equal(page.calls.append.length,0);assert.match(page.error.value,/读取所选完整回答/)
})

test('a late observation read is discarded after its purpose changes and cannot keep the editor busy',async()=>{
  const wait=deferred(),page=await ready(component({raw:()=>wait.promise}))
  Object.assign(page.form,observationForm());const reading=page.readRaw('observation');await until(()=>page.calls.raw.length===1)
  assert.equal(page.pending.value,true);page.form.status='not_observed';assert.equal(page.pending.value,false)
  wait.resolve({data:cycle(option('after'),rawAfter)});await reading
  assert.equal(page.raws.observation,null);assert.equal(page.reading.observation,false);assert.equal(page.calls.append.length,0)
})

test('an uncertain single observation retries the original body after remount without serializing full raw text',async()=>{
  const session=checkpoints.createFeedbackSession(),first=await ready(component({append:async()=>{throw Error('lost acknowledgement')}},session))
  Object.assign(first.form,observationForm());await first.readRaw('observation');await first.save()
  assert.equal(first.uncertain.value,true);const original=clone(first.calls.append[0]),saved=session.read(progress.progressProjectKey(project))
  assert.deepEqual(saved.value.frozen.proof,{after:afterSha});assert.doesNotMatch(JSON.stringify(saved),/"raw"|response_excerpt/)
  first.stop();assert.equal(first.raws.observation,null)
  const second=await ready(component({},session));assert.equal(second.raws.observation,null)
  await second.save();assert.deepEqual(second.calls.append[0],original);assert.deepEqual(second.calls.raw,[])
  assert.equal(second.uncertain.value,false);assert.equal(session.stats().entries,0)
})

test('busy parent initialization waits to read and the initial false event cannot lock a fresh project permanently',async()=>{
  const held=component({},undefined,{busy:true});assert.deepEqual(held.calls.get,[])
  assert.equal(held.busyEvents[0].active,false);held.props.busy=false;await ready(held)
  assert.equal(held.calls.get.length,1);assert.equal(held.pending.value,false);assert.equal(held.locked.value,false)
  const echoed=await ready(component({},undefined,{echoBusy:true}))
  assert.equal(echoed.calls.get.length,1);assert.equal(echoed.props.busy,false);assert.equal(echoed.pending.value,false)
  echoed.props.busy=true;echoed.props.busy=false;await new Promise(setImmediate)
  assert.equal(echoed.calls.get.length,1);assert.equal(echoed.locked.value,false)
})

test('a definitive 400 preserves editable form data and never exposes malicious server messages',async()=>{
  const page=await ready(component({append:async()=>{throw {response:{status:400,data:{msg:'PRIVATE_SERVER_TOKEN <img onerror=bad()>'}}}}}))
  fillPreparation(page);page.form.reviewNote='仍需核对公开资料。';await page.save()
  assert.equal(page.calls.append.length,1);assert.equal(page.form.direction,form().direction);assert.equal(page.form.reviewNote,'仍需核对公开资料。')
  assert.equal(page.uncertain.value,false);assert.equal(page.locked.value,false);assert.doesNotMatch(page.error.value,/PRIVATE_SERVER_TOKEN|img|onerror/)
  const saved=page.session.read(progress.progressProjectKey(project));assert.equal(saved.value.frozen,null)
  assert.equal(saved.value.form.reviewNote,page.form.reviewNote)
})

test('same-project identity changes retain old draft scope until an explicit recheck and never silently append under a renamed entity',async()=>{
  let serverIdentity=clone(identity)
  const page=await ready(component({get:async()=>pageReply([],{project_identity:clone(serverIdentity),project_identity_sha256:await externalSha(serverIdentity)})}))
  fillPreparation(page);page.raws.before={probeId:'probe-before',sha256:beforeSha,raw:rawBefore}
  const original=clone(page.form);serverIdentity=clone(oldIdentity);await page.load()
  assert.equal(page.page.value.identitySha,oldIdentitySha);assert.equal(page.formIdentitySha.value,identitySha);assert.equal(page.identityMismatch.value,true)
  await page.save();assert.equal(page.calls.append.length,0);assert.deepEqual(page.form,original)
  assert.match(page.error.value,/范围已变化/);assert.equal(page.uncertain.value,false)
  page.recheckIdentity();assert.equal(page.formIdentitySha.value,oldIdentitySha);assert.deepEqual(page.form,original)
  assert.equal(page.raws.before,null);assert.equal(page.raws.after,null);assert.equal(page.identityMismatch.value,false)
  assert.equal(page.session.read(progress.progressProjectKey(project)).value.identitySha,oldIdentitySha)
})

test('404 and identity-CAS 409 responses keep one editable draft without automatically sending another request',async()=>{
  for(const status of [404,409]){
    const page=await ready(component({append:async()=>{throw {response:{status,data:{msg:'PRIVATE_IDENTITY_CHANGE'}}}}}))
    fillPreparation(page);await page.save();assert.equal(page.calls.append.length,1)
    assert.equal(page.calls.append[0][1].expected_project_identity_sha256,identitySha)
    assert.equal(page.form.direction,form().direction);assert.equal(page.uncertain.value,false);assert.equal(page.locked.value,false)
    await page.load();assert.equal(page.calls.append.length,1);assert.doesNotMatch(page.error.value,/PRIVATE_IDENTITY_CHANGE/)
  }
})

test('an unknown saved request retries its old identity SHA after the server entity changes instead of rebranding the body',async()=>{
  let serverIdentity=clone(identity),appends=0
  const page=await ready(component({get:async()=>pageReply([],{project_identity:clone(serverIdentity),project_identity_sha256:await externalSha(serverIdentity)}),
    append:async(_id,body)=>{appends++;if(appends===1)throw Error('lost acknowledgement')
      const reply=appendReply({body,proof:{},digest:await progress.progressRequestSha(project,body)},{current_entity_match:false});reply.data.receipt.idempotent=true;return reply}}))
  fillPreparation(page);await page.save();const body=clone(page.calls.append[0][1]);serverIdentity=clone(oldIdentity);await page.load()
  assert.equal(page.uncertain.value,true);assert.equal(page.page.value.identitySha,oldIdentitySha)
  page.recheckIdentity();assert.equal(page.formIdentitySha.value,identitySha)
  await page.save();assert.deepEqual(page.calls.append[1][1],body);assert.equal(page.calls.append[1][1].expected_project_identity_sha256,identitySha)
  assert.equal(page.uncertain.value,false);assert.equal(page.formIdentitySha.value,oldIdentitySha);assert.equal(page.session.stats().entries,0)
})

test('component counts and exports use current identity only while historical and unknown records keep their own sections',async()=>{
  const request=await attempt(),historical={...appendReply(request).data.record,id:'historical-improvement',project_identity:clone(oldIdentity),project_identity_sha256:oldIdentitySha,current_entity_match:false}
  const unknown=record({id:'unknown-preparation',project_identity:null,project_identity_sha256:null,project_identity_basis:'unknown',current_entity_match:null})
  const page=await ready(component({},undefined,{records:[record({id:'current-preparation'}),historical,unknown]}))
  assert.equal(page.page.value.counts.observed_improvement,1);assert.equal(page.page.value.countsCurrent.observed_improvement,0)
  assert.deepEqual(page.recordGroups.value.map(item=>[item.key,item.records.length]),[['current',1],['history',1],['unknown',1]])
  const html=await renderComponent(page);assert.match(html,/回答改善\s*<strong>0<\/strong>/)
  assert.match(html,/历史对象归档/);assert.match(html,/范围待核对归档/);assert.ok(html.includes(oldIdentity.name))
  await page.exportAll();assert.deepEqual(page.downloads[0][1].records.map(item=>item.id),['current-preparation'])
  assert.deepEqual(page.downloads[0][1].archived_records.map(item=>item.id),['historical-improvement','unknown-preparation'])
  assert.equal(page.downloads[0][0].name,identity.name)
})

test('an unknown append survives unmount and retries the identical UUID and payload without rereading full raw',async()=>{
  const session=checkpoints.createFeedbackSession(),first=await ready(component({append:async()=>{throw Error('lost acknowledgement')}},session))
  fillPreparation(first);await first.save();const original=clone(first.calls.append[0]);assert.equal(first.uncertain.value,true);first.stop()
  const second=await ready(component({},session))
  assert.equal(second.uncertain.value,true);assert.equal(second.locked.value,true);assert.equal(second.raws.before,null);assert.equal(second.raws.after,null)
  second.form.direction='后来的编辑不得替换原请求';await second.save()
  assert.deepEqual(second.calls.append[0],original);assert.equal(second.uncertain.value,false);assert.equal(second.form.direction,'')
  assert.equal(second.records.value.length,1);assert.equal(second.records.value[0].request_id,original[1].request_id)
  assert.equal(session.stats().entries,0);assert.deepEqual(second.calls.raw,[])
})

test('a malformed append success keeps the original attempt and repeated clicks cannot create a second request',async()=>{
  const wait=deferred(),page=await ready(component({append:()=>wait.promise}))
  fillPreparation(page);const saving=page.save();await until(()=>page.calls.append.length===1);await page.save()
  assert.equal(page.calls.append.length,1)
  const body=page.calls.append[0][1],request={body,proof:{},digest:await progress.progressRequestSha(project,body)},reply=appendReply(request)
  reply.data.receipt.request_sha256='a'.repeat(64);wait.resolve(reply);await saving
  assert.equal(page.uncertain.value,true);assert.equal(page.form.actionSummary,form().actionSummary);assert.equal(page.records.value.length,0)
})

test('project A to B to A cannot let late auto-read responses overwrite the current project list',async()=>{
  const waits=[],page=component({get:(id)=>{const wait=deferred();waits.push({id,...wait});return wait.promise}})
  page.props.project={...project,id:8,name:'另一合成公司'};page.props.project=clone(project)
  assert.deepEqual(waits.map(item=>item.id),[7,8,7])
  waits[2].resolve(pageReply([record({id:'current-a'})]));await ready(page)
  waits[0].resolve(pageReply([record({id:'stale-a'})]));waits[1].resolve(pageReply([],{project_id:8,probe_options:[]}));await new Promise(setImmediate)
  assert.deepEqual(page.records.value.map(item=>item.id),['current-a']);assert.equal(page.loading.value,false)
})

test('late append acknowledgement after unmount settles its own checkpoint and cannot install a record into another project',async()=>{
  const wait=deferred(),session=checkpoints.createFeedbackSession(),first=await ready(component({append:()=>wait.promise},session))
  fillPreparation(first);const saving=first.save();await until(()=>first.calls.append.length===1)
  const body=first.calls.append[0][1],request={body,proof:{},digest:await progress.progressRequestSha(project,body)};first.stop()
  const second=await ready(component({},session,{project:{...project,id:8,name:'另一合成公司'}}))
  wait.resolve(appendReply(request));await saving
  assert.equal(second.form.direction,'');assert.deepEqual(second.records.value,[]);assert.equal(second.page.value.counts.total,0)
  assert.equal(session.stats().entries,0);assert.equal(first.busyEvents.at(-1).active,false)
})

test('concurrent entrances honor draft CAS and a stale entrance sends no append or replacement text',async()=>{
  const session=checkpoints.createFeedbackSession(),first=await ready(component({},session));fillPreparation(first)
  const second=await ready(component({},session));first.form.reviewNote='第一入口更新后的核对说明。'
  second.form.actionSummary='第二入口的本机修改';await second.save()
  assert.equal(second.calls.append.length,0);assert.match(second.checkpointError.value,/另一入口/)
  assert.equal(session.read(progress.progressProjectKey(project)).value.form.reviewNote,'第一入口更新后的核对说明。')
  assert.equal(second.form.actionSummary,'第二入口的本机修改')
})

test('a newer shared checkpoint cannot be erased by a late valid append receipt',async()=>{
  const wait=deferred(),session=checkpoints.createFeedbackSession(),page=await ready(component({append:()=>wait.promise},session))
  fillPreparation(page);const saving=page.save();await until(()=>page.calls.append.length===1)
  const key=progress.progressProjectKey(project),saved=session.read(key),body=page.calls.append[0][1]
  assert.equal(session.write(key,{...saved.value,form:{...saved.value.form,reviewNote:'较新的其他入口备注'}},saved.token,saved.version).ok,true)
  wait.resolve(appendReply({body,proof:{},digest:await progress.progressRequestSha(project,body)}));await saving
  assert.equal(page.uncertain.value,true);assert.match(page.error.value,/另一入口/)
  assert.equal(session.read(key).value.form.reviewNote,'较新的其他入口备注');assert.deepEqual(page.records.value,[])
})

test('capacity exhaustion leaves the form exportable and editable without ever appending an unsaved request',async()=>{
  const session=checkpoints.createFeedbackSession({entries:0,bytes:100000}),page=await ready(component({},session))
  fillPreparation(page);await page.save()
  assert.equal(page.calls.append.length,0);assert.equal(page.form.direction,form().direction);assert.equal(page.uncertain.value,false)
  assert.equal(page.locked.value,false);assert.match(page.checkpointError.value,/容量/)
  page.exportDraft();assert.equal(page.exports[0][0].form.actionSummary,form().actionSummary);assert.equal(session.stats().entries,0)
})

test('complete response text exists only in current RAM and neither checkpoint nor exported drafts or reports contain it',async()=>{
  const fullBefore=rawBefore+'\nPRIVATE_FULL_BEFORE_SUFFIX',fullAfter=rawAfter+'\nPRIVATE_FULL_AFTER_SUFFIX'
  const selected=[option('before',{response_sha256:await externalSha(fullBefore),response_excerpt:'列表只提供预览'}),
    option('after',{response_sha256:await externalSha(fullAfter),response_excerpt:'另一预览'})]
  const page=await ready(component({get:async()=>pageReply([],{probe_options:selected}),
    raw:async id=>({data:cycle(id==='cycle-before'?selected[0]:selected[1],id==='cycle-before'?fullBefore:fullAfter)}),
    append:async()=>{throw Error('lost acknowledgement')}}))
  Object.assign(page.form,comparisonForm({beforeQuote:'不能确认官网。',afterQuote:'找到官网名称'}))
  await page.readRaw('before');await page.readRaw('after')
  assert.equal(page.raws.before.raw,fullBefore);assert.equal(page.raws.after.raw,fullAfter)
  await page.save();assert.equal(page.uncertain.value,true)
  const stored=page.session.read(progress.progressProjectKey(project)),json=JSON.stringify(stored)
  assert.doesNotMatch(json,/PRIVATE_FULL_|response_excerpt|"raw"/);assert.match(json,/不能确认官网/)
  page.exportDraft();await page.exportAll()
  assert.doesNotMatch(JSON.stringify(page.exports),/PRIVATE_FULL_|response_excerpt|"raw"/)
  assert.doesNotMatch(JSON.stringify(page.downloads),/PRIVATE_FULL_|response_excerpt|"raw"/)
  page.stop();assert.equal(page.raws.before,null);assert.equal(page.raws.after,null)
})

test('late full-answer reads are discarded after selecting another probe and do not leave the editor busy',async()=>{
  const wait=deferred(),page=await ready(component({raw:id=>id==='cycle-before'?wait.promise:Promise.resolve({data:cycle(option('after'),rawAfter)})}))
  Object.assign(page.form,comparisonForm());const reading=page.readRaw('before');await until(()=>page.calls.raw.length===1)
  page.form.beforeProbeId='probe-after';await page.readRaw('before')
  assert.equal(page.raws.before.probeId,'probe-after');assert.equal(page.raws.before.raw,rawAfter)
  wait.resolve({data:cycle()});await reading
  assert.equal(page.raws.before.probeId,'probe-after');assert.equal(page.reading.before,false);assert.equal(page.pending.value,false)
})

test('all-project export fixes the snapshot across pages and downloads only a complete ordered list',async()=>{
  const all=Array.from({length:21},(_,i)=>record({id:`export-${i}`})),counts=pageReply(all).data.counts
  const page=await ready(component({get:async(_id,query={})=>query.include_probes===false?
    pageReply(all.slice(query.offset||0,(query.offset||0)+20),{counts,snapshot_sequence:12,has_more:!query.offset,next_offset:query.offset?null:20}):pageReply([])}))
  await page.exportAll()
  const queries=page.calls.get.slice(1).map(item=>item[1])
  assert.deepEqual(queries,[{include_probes:false},{include_probes:false,offset:20,snapshot_sequence:12,expected_project_identity_sha256:identitySha}])
  assert.equal(page.downloads.length,1);assert.equal(page.downloads[0][0].id,7)
  assert.deepEqual(page.downloads[0][1].records.map(item=>item.id),all.map(item=>item.id));assert.match(page.notice.value,/21 条/)
})

test('mismatched export snapshots or duplicate pages never produce a partial document claiming all records',async()=>{
  for(const duplicate of [false,true]){
    const all=Array.from({length:21},(_,i)=>record({id:`export-${i}`})),counts=pageReply(all).data.counts
    const page=await ready(component({get:async(_id,query={})=>query.include_probes===false?
      pageReply(query.offset?[all[duplicate?0:20]]:all.slice(0,20),{counts,snapshot_sequence:query.offset&&!duplicate?13:12,has_more:!query.offset,next_offset:query.offset?null:20}):pageReply([])}))
    await page.exportAll();assert.equal(page.downloads.length,0);assert.ok(page.error.value);assert.equal(page.exporting.value,false)
  }
})

test('export refuses classification-count drift even when total, identity SHA and sequence snapshot remain fixed',async()=>{
  const all=Array.from({length:21},(_,i)=>record({id:`drift-${i}`})),counts=pageReply(all).data.counts
  const unknown=record({id:all[20].id,project_identity:null,project_identity_sha256:null,project_identity_basis:'unknown',current_entity_match:null})
  const page=await ready(component({get:async(_id,query={})=>{
    if(query.include_probes!==false)return pageReply([])
    if(!query.offset)return pageReply(all.slice(0,20),{counts,snapshot_sequence:12,has_more:true,next_offset:20})
    return pageReply([unknown],{counts,snapshot_sequence:12,counts_current:countRecords(all.slice(0,20)),counts_unknown:countRecords([unknown])})
  }}))
  await page.exportAll();assert.equal(page.downloads.length,0);assert.ok(page.error.value);assert.equal(page.exporting.value,false)
})

test('loading more preserves the original page when classification counts drift without changing total, identity or snapshot',async()=>{
  const all=Array.from({length:21},(_,i)=>record({id:`more-drift-${i}`})),counts=pageReply(all).data.counts
  const unknown=record({id:all[20].id,project_identity:null,project_identity_sha256:null,project_identity_basis:'unknown',current_entity_match:null})
  const page=await ready(component({get:async(_id,query={})=>!query.offset?
    pageReply(all.slice(0,20),{counts,snapshot_sequence:12,has_more:true,next_offset:20}):
    pageReply([unknown],{counts,snapshot_sequence:12,counts_current:countRecords(all.slice(0,20)),counts_unknown:countRecords([unknown])})}))
  const originalPage=page.page.value,originalRecords=clone(page.records.value)
  await page.loadMore()
  assert.deepEqual(page.calls.get[1],[7,{offset:20,snapshot_sequence:12,expected_project_identity_sha256:identitySha,include_probes:false}])
  assert.equal(page.page.value,originalPage);assert.deepEqual(page.records.value,originalRecords)
  assert.equal(page.page.value.countsCurrent.total,21);assert.equal(page.page.value.countsUnknown.total,0)
  assert.deepEqual(page.recordGroups.value.map(group=>[group.key,group.records.length]),[['current',20]])
  assert.equal(page.page.value.hasMore,true);assert.equal(page.page.value.nextOffset,20)
  assert.match(page.error.value,/已显示记录保留.*刷新/);assert.equal(page.loading.value,false);assert.deepEqual(page.calls.append,[])
})

test('the 2000-record export bound refuses to claim an incomplete larger project as fully exported',async()=>{
  const counts={total:2001,observed_improvement:0,not_observed:0,preparation:2001,native_only:0,user_reported_included:0,research_import_included:0,no_evidence:2001,independently_verified_effects:0}
  const page=await ready(component({get:async(_id,query={})=>{
    if(query.include_probes!==false)return pageReply([])
    const offset=query.offset||0,records=Array.from({length:20},(_,i)=>record({id:`large-${offset+i}`}))
    return pageReply(records,{counts,snapshot_sequence:2001,has_more:true,next_offset:offset+20})
  }}))
  await page.exportAll();assert.equal(page.downloads.length,0);assert.match(page.error.value,/2000/)
  assert.equal(page.calls.get.filter(item=>item[1]?.include_probes===false).length,1);assert.equal(page.exporting.value,false)
})

test('the progress dialog compiles with a scrolling body, fixed footer and single-column controls at a 390px viewport',async()=>{
  assert.deepEqual(compileTemplate({source:descriptor.template.content,filename:componentFile.pathname,id:'optimization-progress-test',compilerOptions:{bindingMetadata:compiled.bindings}}).errors,[])
  for(const style of descriptor.styles)assert.deepEqual(compileStyle({source:style.content,filename:componentFile.pathname,id:'optimization-progress-test'}).errors,[])
  const css=descriptor.styles.map(style=>style.content).join('\n')
  assert.match(css,/max-height:calc\(100dvh - 24px\)/);assert.match(css,/\.el-dialog__body\{min-height:0;overflow-y:auto/)
  assert.match(css,/\.el-dialog__footer\{flex-shrink:0/);assert.match(css,/@media\(max-width:650px\).*\.progress-pair\{grid-template-columns:1fr\}/)
  assert.match(css,/flex-wrap:wrap/);assert.match(css,/@media\(prefers-reduced-motion:reduce\)/)
  assert.match(descriptor.template.content,/width="min\(820px, calc\(100vw - 24px\)\)"/);assert.match(descriptor.template.content,/#footer/)
  assert.doesNotMatch(descriptor.scriptSetup.content,/localStorage|sessionStorage|indexedDB|start_research|generate_research/)
  const page=await ready(component({},undefined,{records:[record({action_summary:'<img src=x onerror=bad()> 合成改动'})]}))
  const html=await renderComponent(page);assert.match(html,/&lt;img/);assert.doesNotMatch(html,/<img\b/)
})
