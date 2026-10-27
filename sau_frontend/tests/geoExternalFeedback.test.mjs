import assert from 'node:assert/strict'
import fs from 'node:fs'
import test from 'node:test'
import * as Vue from 'vue'
import {parse, compileScript, compileTemplate, compileStyle} from '@vue/compiler-sfc'
import * as feedback from '../src/utils/geoExternalFeedback.js'
import {journeyDate} from '../src/utils/geoJourney.js'
import {publicationReviewUrl} from '../src/utils/geoPublicationReview.js'
import * as checkpoints from '../src/utils/geoFeedbackSession.js'

const cases = [{id:'q02',question:'示例产品为哪些客户提供哪些服务？',category:'brand_identity'}]
const cycle = (id='cycle-a', changes={}) => ({id,project_id:id==='cycle-a'?4:5,revision:2,
  test_plan:{plan_sha256:'a'.repeat(64)},baseline:{cases},probes:[],...changes})
const original = '  根据公开信息：\n官网介绍尚未找到。\n来源名称只有“平台甲”。  '
const form = () => ({...feedback.emptyExternalFeedback(),caseId:'q02',raw:original})
const probe = (changes={}) => ({id:'e'.repeat(32),cycle_id:'cycle-a',case_id:'q02',source:'user_reported_external',
  native_verified:false,response_sha256:'b'.repeat(64),after:{response:original,response_sha256:'b'.repeat(64)},...changes})
const response = (owner=cycle(), record=probe()) => ({code:200,data:{probe:record,cycle:{...owner,probes:[record]}}})
const deferred = () => {let resolve,reject;const promise=new Promise((done,fail)=>{resolve=done;reject=fail});return {promise,resolve,reject}}

test('external text remains byte-equivalent while unspecified sampling conditions stay absent',()=>{
  const value=feedback.externalFeedbackPayload(cycle(),form())
  assert.equal(value.raw_response_text,original)
  assert.deepEqual(value.metadata,{})
  assert.equal(value.expected_revision,2)
  assert.equal(value.expected_plan_sha256,'a'.repeat(64))
  assert.equal(value.project_id,4)
  assert.equal(value.case_id,'q02')
  assert.ok(!JSON.stringify(value).includes('native_verified'))
})

test('observed questions are a deliberate statement, and report anchors do not prove matching text',()=>{
  const value=form()
  assert.ok(!Object.hasOwn(feedback.externalFeedbackPayload(cycle(),value).metadata,'question'))
  value.questionMode='same'
  assert.equal(feedback.externalFeedbackPayload(cycle(),value).metadata.question,cases[0].question)
  value.questionMode='custom';value.question='实际问题\n保留换行'
  assert.equal(feedback.externalFeedbackPayload(cycle(),value).metadata.question,value.question)
  assert.deepEqual(feedback.externalFeedbackCases(cycle('cycle-a',{baseline:{cases:[...cases,...cases]}})),[])
  assert.throws(()=>feedback.externalFeedbackPayload(cycle('cycle-a',{revision:null}),value))
})

test('provided modes, partial answers, dates and reported links stay separate from observed citations',()=>{
  const value={...form(),clientMode:'consumer',completeness:'partial',model:' 示例 Lite ',
    collectedAt:'2026-10-28T10:30:00+08:00',sourceLinks:'https://example.org/company\nhttps://example.org/company',note:'手机取得'}
  const metadata=feedback.externalFeedbackPayload(cycle(),value).metadata
  assert.equal(metadata.client_mode,'consumer');assert.equal(metadata.answer_complete,false)
  assert.equal(metadata.collected_at,'2026-10-28T02:30:00.000Z')
  assert.deepEqual(metadata.source_links,[{url:'https://example.org/company'}])
  assert.equal(metadata.model_label,'示例 Lite')
  assert.ok(!Object.hasOwn(metadata,'citations'))
  assert.ok(!Object.hasOwn(metadata,'official_citation_present'))
})

test('oversized UTF8, fake modes, malformed dates and active or credential URLs are rejected before submitting',()=>{
  for(const changes of [{raw:' '},{raw:'字'.repeat(400000)},{clientMode:'native'},
    {completeness:true},{collectedAt:'not-a-date'},{sourceLinks:'javascript:alert(1)'},
    {sourceLinks:'https://name:secret@example.org/a'},{sourceLinks:Array.from({length:21},(_,n)=>`https://example.org/${n}`).join('\n')}]) {
    assert.throws(()=>feedback.externalFeedbackPayload(cycle(),{...form(),...changes}))
  }
})

test('external context requires the original saved hash and explicit user sampling statements',()=>{
  const value={question:cases[0].question,newConversation:true,firstMessage:true,confirmation:'同事确认空白新对话首条只问原题。',confirmed:true}
  const body=feedback.externalContextPayload(cycle(),probe(),value)
  assert.equal(body.expected_context_sequence,0)
  assert.equal(body.expected_response_sha256,'b'.repeat(64))
  assert.equal(body.attestation.confirmation_text,value.confirmation)
  for(const invalid of [{...value,newConversation:'unknown'},{...value,firstMessage:'true'},{...value,confirmed:false}]) {
    assert.throws(()=>feedback.externalContextPayload(cycle(),probe(),invalid))
  }
  assert.throws(()=>feedback.externalContextPayload(cycle(),probe({source:'native_collector',native_verified:true}),value))
  assert.throws(()=>feedback.externalContextPayload(cycle(),probe({cycle_id:'cycle-b'}),value))
  assert.throws(()=>feedback.externalContextPayload(cycle(),probe({after:{response:original,response_sha256:'c'.repeat(64)}}),value))
})

test('foreign or stale receipts cannot replace a newer current cycle',()=>{
  const scope=feedback.externalFeedbackScope(cycle())
  assert.equal(feedback.externalFeedbackResponse(response(),scope,'q02').probe.id,'e'.repeat(32))
  assert.throws(()=>feedback.externalFeedbackResponse(response(cycle('cycle-b')),scope,'q02'))
  assert.throws(()=>feedback.externalFeedbackResponse(response(cycle(),probe({native_verified:true})),scope,'q02'))
  assert.equal(feedback.externalCycleCanApply(cycle('cycle-a',{revision:2}),cycle('cycle-a',{revision:3})),false)
  assert.equal(feedback.externalCycleCanApply(cycle(),cycle()),true)
  assert.equal(feedback.externalCycleCanApply(cycle('cycle-b'),cycle()),false)
})

const filename=new URL('../src/components/GeoExternalFeedback.vue',import.meta.url)
const {descriptor,errors}=parse(fs.readFileSync(filename,'utf8'),{filename:filename.pathname})
assert.deepEqual(errors,[])
const compiled=compileScript(descriptor,{id:'external-feedback'})
const declarations=compiled.scriptSetupAst.flatMap(node=>node.type==='VariableDeclaration'?node.declarations:[])
const names=declarations.flatMap(row=>row.id.type==='Identifier'?[row.id.name]:[])
const body=descriptor.scriptSetup.content.replace(/^import .*$/gm,'')
function harness(api={},session=checkpoints.createFeedbackSession()) {
  const props=Vue.reactive({cycle:cycle(),busy:false}),events=[],busyEvents=[],messages=[],unmount=[],effect=Vue.effectScope()
  const deps={computed:Vue.computed,reactive:Vue.reactive,ref:Vue.ref,watch:Vue.watch,...feedback,...checkpoints,feedbackSession:session,defineProps:()=>props,defineEmits:()=>((...args)=>(args[0]==='busy'?busyEvents:events).push(args)),
    onUnmounted:callback=>unmount.push(callback),journeyDate,publicationReviewUrl,
    ElMessage:{success:value=>messages.push(value),info:value=>messages.push(value)},ElMessageBox:{confirm:async()=>{}},
    geoExternalFeedbackApi:{create:async()=>response(),context:async()=>response(),...api}}
  const methods=effect.run(()=>new Function(...Object.keys(deps),body+`;return {${names.join(',')}}`)(...Object.values(deps)))
  return {...methods,props,events,busyEvents,messages,session,stop(){for(const close of unmount)close();effect.stop()}}
}

test('new dialogs make no implicit metadata claims and duplicate clicks send one immutable original',async()=>{
  const done=deferred(),calls=[]
  const page=harness({create:(...args)=>{calls.push(args);return done.promise}})
  Object.assign(page.form,form())
  page.openCreate()
  const pending=page.saveCreate()
  await page.saveCreate()
  assert.equal(calls.length,1);assert.equal(page.pending.value,true)
  assert.equal(calls[0][1].raw_response_text,original)
  assert.deepEqual(calls[0][1].metadata,{})
  done.resolve(response());await pending
  assert.equal(page.events.length,1);assert.equal(page.createVisible.value,false)
  assert.equal(page.form.raw,'');page.stop()
})

test('A to B to A drops late replies and keeps the same unknown write for explicit idempotent retry',async()=>{
  const first=deferred(),again=deferred(),calls=[]
  const page=harness({create:(...args)=>{calls.push(args);return calls.length===1?first.promise:again.promise}})
  Object.assign(page.form,form())
  const initial=page.saveCreate()
  page.props.cycle=cycle('cycle-b');await Vue.nextTick()
  assert.equal(page.form.raw,'')
  page.props.cycle=cycle();await Vue.nextTick()
  assert.equal(page.form.raw,original);assert.equal(page.uncertain.value,true);assert.equal(page.locked.value,true)
  first.resolve(response());await initial
  assert.equal(page.events.length,0)
  const retry=page.saveCreate()
  assert.deepEqual(calls[0][1],calls[1][1])
  again.resolve(response());await retry
  assert.equal(page.events.length,1);assert.equal(page.form.raw,'');page.stop()
})

test('a lost acknowledgement keeps the original payload and malformed success cannot erase the draft',async()=>{
  let requests=0
  const page=harness({create:async()=>{requests++;if(requests===1)throw new Error('connection lost');return response(cycle(),probe({after:{response:'different'}}))}})
  Object.assign(page.form,form())
  await page.saveCreate()
  assert.equal(page.uncertain.value,true);assert.equal(page.form.raw,original)
  await page.saveCreate()
  assert.equal(page.uncertain.value,true);assert.equal(page.form.raw,original);assert.equal(page.events.length,0)
  page.stop()
})

test('saved feedback cannot roll newer facts back while a request is in flight',async()=>{
  const done=deferred(),page=harness({create:()=>done.promise})
  Object.assign(page.form,form())
  const pending=page.saveCreate()
  page.props.cycle=cycle('cycle-a',{revision:3});await Vue.nextTick()
  done.resolve(response());await pending
  assert.equal(page.events.length,0)
  assert.equal(page.form.raw,'')
  assert.match(page.messages.join(' '),/当前资料同时有更新/)
  page.stop()
})

test('closing an uncertain context keeps its exact declaration for reopening and does not modify the raw answer',async()=>{
  const value=probe(),page=harness({context:async()=>{throw new Error('network')}})
  page.props.cycle=cycle('cycle-a',{probes:[value]})
  page.openContext(value)
  Object.assign(page.contextForm,{question:cases[0].question,newConversation:true,firstMessage:true,confirmation:'同事确认。',confirmed:true})
  await page.saveContext()
  assert.equal(page.contextUncertain.value,true)
  page.closeContext(()=>{page.contextVisible.value=false})
  page.openContext(value)
  assert.equal(page.contextForm.confirmation,'同事确认。')
  assert.equal(page.contextUncertain.value,true)
  assert.equal(value.after.response,original)
  page.stop()
})

test('dialogs compile with separate scrollable body and accessible fixed footer',()=>{
  assert.deepEqual(compileTemplate({source:descriptor.template.content,filename:filename.pathname,id:'external-feedback',compilerOptions:{bindingMetadata:compiled.bindings}}).errors,[])
  for(const style of descriptor.styles)assert.deepEqual(compileStyle({source:style.content,filename:filename.pathname,id:'external-feedback'}).errors,[])
  assert.match(descriptor.template.content,/data-testid="external-feedback-save"/)
  assert.match(descriptor.styles[0].content,/\.el-dialog__body\{overflow-y:auto/)
  assert.match(descriptor.styles[0].content,/\.el-dialog__footer\{flex-shrink:0/)
})

test('unsent original and optional unknown conditions survive route unmount within this app session only',()=>{
  const session=checkpoints.createFeedbackSession(),first=harness({},session)
  Object.assign(first.form,{...form(),questionMode:'custom',question:'实际题文\n保留',clientMode:'unknown',note:'手机反馈'})
  first.stop()
  const second=harness({},session)
  assert.equal(second.form.raw,original);assert.equal(second.form.question,'实际题文\n保留')
  assert.equal(second.form.clientMode,'unknown');assert.equal(second.form.note,'手机反馈')
  second.props.cycle=cycle('cycle-b')
  assert.equal(second.form.raw,'')
  second.props.cycle=cycle()
  assert.equal(second.form.raw,original);second.stop()
  const anotherApplication=harness()
  assert.equal(anotherApplication.form.raw,'');anotherApplication.stop()
})

test('unknown creation survives unmount and retries the original guards rather than a newer revision',async()=>{
  const session=checkpoints.createFeedbackSession(),calls=[]
  const first=harness({create:async(...args)=>{calls.push(args);throw new Error('lost reply')}},session)
  Object.assign(first.form,{...form(),clientMode:'consumer',sourceLinks:'https://example.org/reported'})
  await first.saveCreate();first.stop()
  const second=harness({create:async(...args)=>{calls.push(args);return response(cycle('cycle-a',{revision:3}))}},session)
  second.props.cycle=cycle('cycle-a',{revision:3})
  assert.equal(second.uncertain.value,true);assert.equal(second.locked.value,true)
  await second.saveCreate()
  assert.deepEqual(calls[1][1],calls[0][1]);assert.equal(calls[1][1].expected_revision,2)
  assert.equal(second.form.raw,'');assert.equal(session.stats().entries,0);second.stop()
})

test('unknown context declaration survives unmount with its original raw SHA and sequence',async()=>{
  const session=checkpoints.createFeedbackSession(),record=probe(),calls=[]
  const first=harness({context:async(...args)=>{calls.push(args);throw new Error('lost context reply')}},session)
  first.props.cycle=cycle('cycle-a',{probes:[record]});first.openContext(record)
  Object.assign(first.contextForm,{question:'实际题文',newConversation:false,firstMessage:false,confirmation:'原对话继续提问。',confirmed:true})
  await first.saveContext();first.stop()
  const second=harness({context:async(...args)=>{
    calls.push(args);return response(cycle(),probe({context_attestations:[{...args[2].attestation}]}))
  }},session)
  second.props.cycle=cycle('cycle-a',{revision:3,probes:[record]});second.openContext(record)
  assert.equal(second.contextUncertain.value,true);assert.equal(second.contextForm.newConversation,false)
  await second.saveContext();assert.deepEqual(calls[1][2],calls[0][2])
  assert.equal(calls[1][2].expected_revision,2);assert.equal(calls[1][2].expected_context_sequence,0)
  assert.equal(session.stats().entries,0);second.stop()
})

test('unmounted late receipts settle only their checkpoint and cannot emit into a remounted scope',async()=>{
  const session=checkpoints.createFeedbackSession(),done=deferred(),first=harness({create:()=>done.promise},session)
  Object.assign(first.form,form());const pending=first.saveCreate();first.stop()
  const second=harness({},session);second.props.cycle=cycle('cycle-b')
  done.resolve(response());await pending
  assert.equal(first.events.length,0);assert.equal(second.events.length,0);assert.equal(second.form.raw,'')
  assert.equal(session.read(checkpoints.feedbackCheckpointKey('external',feedback.externalFeedbackScope(cycle()))),null)
  assert.equal(first.busyEvents.at(-1)[1].active,false);second.stop()
})

test('a definitive 400 keeps editable original for remount while capacity exhaustion blocks unsafe new submission',async()=>{
  const session=checkpoints.createFeedbackSession(),first=harness({create:async()=>{throw {response:{status:400,data:{msg:'版本已变化'}}}}},session)
  Object.assign(first.form,form());await first.saveCreate()
  assert.equal(first.uncertain.value,false);first.stop()
  const second=harness({},session);assert.equal(second.form.raw,original);assert.equal(second.locked.value,false);second.stop()
  let requests=0
  const full=checkpoints.createFeedbackSession({entries:0,bytes:10000}),blocked=harness({create:async()=>{requests++;return response()}},full)
  Object.assign(blocked.form,form());await blocked.saveCreate()
  assert.equal(requests,0);assert.equal(blocked.form.raw,original);assert.match(blocked.checkpointError.value,/导出|保留/)
  blocked.stop();assert.ok(blocked.messages.some(message=>message.includes('容量')))
})

test('a capacity-blocked attempt never freezes an unsent old answer after the user edits it',async()=>{
  const session=checkpoints.createFeedbackSession({entries:1,bytes:100000}),occupied=session.write('older-unknown',{uncertain:true})
  const calls=[],page=harness({create:async(...args)=>{calls.push(args);return response(cycle(),probe({after:{response:args[1].raw_response_text}}))}},session)
  Object.assign(page.form,form());await page.saveCreate();assert.equal(calls.length,0)
  session.confirmed('older-unknown',occupied.token)
  page.form.raw='A deliberately edited answer after capacity became available.'
  await page.saveCreate()
  assert.equal(calls.length,1);assert.equal(calls[0][1].raw_response_text,'A deliberately edited answer after capacity became available.')
  assert.equal(page.form.raw,'');page.stop()
})

test('opening and closing unchanged context dialogs does not exhaust the finite draft budget',()=>{
  const session=checkpoints.createFeedbackSession({entries:1,bytes:100000}),page=harness({},session),record=probe()
  page.props.cycle=cycle('cycle-a',{probes:[record]});page.openContext(record)
  page.closeContext(()=>{});assert.equal(session.stats().entries,0)
  page.contextForm.question='An explicit changed question'
  assert.equal(session.stats().entries,1);page.stop()
})
