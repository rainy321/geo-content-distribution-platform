import assert from 'node:assert/strict'
import fs from 'node:fs'
import test from 'node:test'
import * as Vue from 'vue'
import {parse,compileScript,compileTemplate,compileStyle} from '@vue/compiler-sfc'
import * as review from '../src/utils/geoBusinessReview.js'
import * as feedback from '../src/utils/geoExternalFeedback.js'
import {journeyDate} from '../src/utils/geoJourney.js'
import * as checkpoints from '../src/utils/geoFeedbackSession.js'
const raw='  官网业务介绍未找到。\n仅看到工商记录。\n'
const cycle=(changes={})=>({id:'cycle-a',project_id:4,revision:2,test_plan:{plan_sha256:'a'.repeat(64)},
  facts:[{id:'fact-1',text:'示例公司官网介绍模型接入业务',source_url:'https://example.org/about',confirmed:true}],...changes})
const probe=(changes={})=>({id:'probe-a',cycle_id:'cycle-a',case_id:'q02',after:{response:raw,answer_evaluable:null},...changes})
const digest=await review.rawAnswerHash(probe())
const context=(changes={})=>({cycle_id:'cycle-a',project_id:4,probe_id:'probe-a',case_id:'q02',plan_sha256:'a'.repeat(64),revision:2,
  response_sha256:digest,review_sequence:0,reviews:[],confirmed_facts:cycle().facts.map(({confirmed,...fact})=>fact),
  scope:'business_material_acknowledgment_only_not_fact_or_effect_verification',...changes})
const snapshot=()=>review.businessReviewSnapshot(cycle(),probe(),context(),digest)
const form=()=>({status:'not_acknowledged',quote:'官网业务介绍未找到。\n仅看到工商记录。',factIds:['fact-1'],note:'保持负面原文',confirmed:true})
const receipt=(payload,changes={})=>{
  const latest={...payload.review,by:'operator',response_sha256:digest,reviewed_at:'2026-10-28T00:00:00Z'}
  const record={...probe(),business_relationship_review:latest,business_relationship_reviews:[latest],business_review_sequence:1,...changes}
  return {code:200,data:{probe:record,cycle:cycle({probes:[record]})}}
}
const deferred=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no});return {promise,resolve,reject}}
test('review snapshot binds exact current raw response, confirmed facts and original plan',()=>{
  assert.equal(snapshot().raw,raw)
  for(const changed of [{project_id:5},{case_id:'q03'},{revision:3},{response_sha256:'b'.repeat(64)},
    {confirmed_facts:[{id:'fact-1',text:'改写事实',source_url:'https://example.org/about'}]}]){
    assert.throws(()=>review.businessReviewSnapshot(cycle(),probe(),context(changed),digest))
  }
  assert.throws(()=>review.businessReviewSnapshot(cycle(),probe({cycle_id:'cycle-b'}),context(),digest))
})
test('human review requires exact quotation and confirmed facts, and never submits an assistant identity',()=>{
  const body=review.businessReviewPayload(snapshot(),form())
  assert.equal(body.review.quote,form().quote);assert.ok(!Object.hasOwn(body.review,'by'))
  assert.ok(!JSON.stringify(body).includes('causal'))
  for(const change of [{confirmed:false},{quote:'官网已经收录'},{factIds:['other']},{factIds:['fact-1','fact-1']},{status:'success'}]){
    assert.throws(()=>review.businessReviewPayload(snapshot(),{...form(),...change}))
  }
  const incomplete={...snapshot(),evaluable:false}
  assert.throws(()=>review.businessReviewPayload(incomplete,form()))
  assert.equal(review.businessReviewPayload(incomplete,{...form(),status:'unknown'}).review.status,'unknown')
})
test('review receipts cannot replace a different raw response, human identity or another company',()=>{
  const body=review.businessReviewPayload(snapshot(),form())
  assert.equal(review.businessReviewReceipt(receipt(body),snapshot(),body).id,'cycle-a')
  for(const change of [{after:{response:raw+'changed'}},{business_relationship_review:{...body.review,by:'assistant',response_sha256:digest}},
    {business_review_sequence:9}])assert.throws(()=>review.businessReviewReceipt(receipt(body,change),snapshot(),body))
  const wrong=receipt(body);wrong.data.cycle.project_id=5
  assert.throws(()=>review.businessReviewReceipt(wrong,snapshot(),body))
})
const source=fs.readFileSync(new URL('../src/components/GeoBusinessReview.vue',import.meta.url),'utf8')
const {descriptor}=parse(source)
const script=compileScript(descriptor,{id:'business-review'})
const names=script.scriptSetupAst.filter(row=>row.type==='VariableDeclaration').flatMap(row=>row.declarations)
  .flatMap(row=>row.id.type==='Identifier'?[row.id.name]:[])
const body=descriptor.scriptSetup.content.replace(/^import .*$/gm,'')
function harness(api={},session=checkpoints.createFeedbackSession()){
  const props=Vue.reactive({cycle:cycle(),probe:probe(),busy:false}),events=[],busyEvents=[],messages=[],effect=Vue.effectScope(),unmount=[]
  const deps={computed:Vue.computed,onUnmounted:callback=>unmount.push(callback),reactive:Vue.reactive,ref:Vue.ref,watch:Vue.watch,
    ...review,...feedback,...checkpoints,feedbackSession:session,journeyDate,
    defineProps:()=>props,defineEmits:()=>((...args)=>(args[0]==='busy'?busyEvents:events).push(args)),ElMessage:{success:value=>messages.push(value),info:value=>messages.push(value)},
    ElMessageBox:{confirm:async()=>{}},geoBusinessReviewApi:{get:async()=>({code:200,data:context()}),save:async(_c,_p,value)=>receipt(value),...api}}
  const methods=effect.run(()=>new Function(...Object.keys(deps),body+`;return {${names.join(',')}}`)(...Object.values(deps)))
  return {...methods,props,events,busyEvents,messages,session,stop(){unmount.forEach(fn=>fn());effect.stop()}}
}
test('duplicate save clicks append one review and clear text only after the correct receipt',async()=>{
  const result=deferred(),calls=[],page=harness({save:(...args)=>{calls.push(args);return result.promise}})
  await page.open();Object.assign(page.form,form())
  const pending=page.save();await page.save();assert.equal(calls.length,1)
  result.resolve(receipt(calls[0][2]));await pending
  assert.equal(page.events.length,1);assert.equal(page.form.quote,'');assert.equal(page.visible.value,false);page.stop()
})
test('lost acknowledgement retains an immutable retry and does not invent a review outcome',async()=>{
  const calls=[],page=harness({save:async(...args)=>{calls.push(args);if(calls.length===1)throw new Error('network');return receipt(args[2])}})
  await page.open();Object.assign(page.form,form());await page.save()
  assert.equal(page.uncertain.value,true);assert.equal(page.locked.value,true);assert.equal(page.events.length,0)
  page.form.quote='程序性变化不可替换同次原始请求';await page.save()
  assert.deepEqual(calls[0][2],calls[1][2]);assert.equal(page.events.length,1);page.stop()
})
test('A to B to A does not apply a late read or late saved opinion',async()=>{
  const result=deferred(),page=harness({get:()=>result.promise})
  const pending=page.open();page.props.cycle=cycle({id:'cycle-b'});await Vue.nextTick()
  page.props.cycle=cycle();await Vue.nextTick();result.resolve({code:200,data:context()});await pending
  assert.equal(page.snapshot.value,null);assert.equal(page.events.length,0);page.stop()
  const saved=deferred(),other=harness({save:()=>saved.promise});await other.open();Object.assign(other.form,form())
  const saving=other.save();other.props.cycle=cycle({id:'cycle-b'});await Vue.nextTick()
  saved.resolve(receipt(review.businessReviewPayload(snapshot(),form())));await saving
  assert.equal(other.events.length,0);other.stop()
})
test('a successful review cannot roll back facts edited while the save was pending',async()=>{
  const result=deferred(),page=harness({save:()=>result.promise});await page.open();Object.assign(page.form,form())
  const pending=page.save();page.props.cycle=cycle({revision:3});await Vue.nextTick()
  result.resolve(receipt(review.businessReviewPayload(snapshot(),form())));await pending
  assert.equal(page.events.length,0);assert.ok(page.messages.some(value=>value.includes('资料同时有更新')));page.stop()
})
test('review dialog compiles with a bounded scrolling body and preserved history disclosure',()=>{
  assert.deepEqual(compileTemplate({source:descriptor.template.content,filename:'GeoBusinessReview.vue',id:'business-review',compilerOptions:{bindingMetadata:script.bindings}}).errors,[])
  assert.deepEqual(compileStyle({source:descriptor.styles[0].content,id:'business-review'}).errors,[])
  assert.ok(source.includes('max-height:90vh'));assert.ok(source.includes('flex-shrink:0'));assert.ok(source.includes('snapshot.history'))
  assert.ok(!source.includes('v-html'))
})

test('business review draft survives route unmount with the original facts, raw SHA, plan and revision',async()=>{
  const session=checkpoints.createFeedbackSession(),first=harness({},session)
  await first.open();Object.assign(first.form,form());first.stop()
  let reads=0
  const second=harness({get:async()=>{reads++;return {code:200,data:context()}}},session)
  second.props.cycle=cycle({revision:3,facts:[{...cycle().facts[0],text:'A changed fact under the same ID'}]})
  await second.open()
  assert.equal(reads,0);assert.deepEqual({...second.form},form())
  assert.equal(second.snapshot.value.responseHash,digest);assert.equal(second.snapshot.value.plan,'a'.repeat(64))
  assert.equal(second.snapshot.value.revision,2);assert.equal(second.snapshot.value.facts[0].text,cycle().facts[0].text)
  second.stop()
})

test('unknown business save survives unmount and resends the same frozen request without reassigning opinion',async()=>{
  const session=checkpoints.createFeedbackSession(),calls=[]
  const first=harness({save:async(...args)=>{calls.push(args);throw new Error('lost receipt')}},session)
  await first.open();Object.assign(first.form,form());await first.save();first.stop()
  const second=harness({save:async(...args)=>{calls.push(args);return receipt(args[2])}},session)
  second.props.cycle=cycle({revision:3});await second.open()
  assert.equal(second.uncertain.value,true);assert.equal(second.locked.value,true)
  await second.save()
  assert.deepEqual(calls[1][2],calls[0][2]);assert.equal(calls[1][2].expected_revision,2)
  assert.equal(second.events.length,0);assert.equal(second.form.quote,'');assert.equal(session.stats().entries,0)
  second.stop()
})

test('400 retains draft, while deliberate reread resets material confirmation for changed facts',async()=>{
  const session=checkpoints.createFeedbackSession(),first=harness({save:async()=>{throw {response:{status:400,data:{msg:'资料版本变化'}}}}},session)
  await first.open();Object.assign(first.form,form());await first.save()
  assert.equal(first.uncertain.value,false);assert.equal(first.form.quote,form().quote);first.stop()
  const facts=[{...cycle().facts[0],text:'A different current material statement'}]
  const second=harness({get:async()=>({code:200,data:context({revision:3,confirmed_facts:facts.map(({confirmed,...fact})=>fact)})})},session)
  second.props.cycle=cycle({revision:3,facts});await second.load()
  assert.equal(second.form.quote,form().quote);assert.equal(second.form.confirmed,false);assert.deepEqual(second.form.factIds,[])
  assert.equal(second.snapshot.value.revision,3);assert.equal(second.snapshot.value.facts[0].text,facts[0].text)
  second.stop()
})

test('unmounted business receipt cannot alter another scope and distinct instances release only their own busy owner',async()=>{
  const session=checkpoints.createFeedbackSession(),done=deferred(),first=harness({save:()=>done.promise},session)
  await first.open();Object.assign(first.form,form());const saving=first.save();first.stop()
  const second=harness({},session);second.props.cycle=cycle({id:'cycle-b'});second.props.probe=probe({id:'probe-b',cycle_id:'cycle-b'})
  done.resolve(receipt(review.businessReviewPayload(snapshot(),form())));await saving
  assert.equal(first.events.length,0);assert.equal(second.events.length,0);assert.equal(second.form.quote,'')
  assert.equal(session.stats().entries,0)
  const owners=[first.busyEvents[0][1].owner,second.busyOwner]
  assert.notEqual(owners[0],owners[1]);assert.equal(first.busyEvents.at(-1)[1].active,false);second.stop()
})

test('business checkpoint capacity never starts a request it cannot safely retain',async()=>{
  let calls=0
  const session=checkpoints.createFeedbackSession({entries:0,bytes:10000}),page=harness({save:async(_c,_p,value)=>{calls++;return receipt(value)}},session)
  await page.open();Object.assign(page.form,form());await page.save()
  assert.equal(calls,0);assert.equal(page.form.quote,form().quote);assert.match(page.checkpointError.value,/导出|保留/)
  page.stop();assert.ok(page.messages.some(value=>value.includes('容量')))
})

test('two visible entries for the same external record restore the newest draft and stale unmount cannot erase it',async()=>{
  const session=checkpoints.createFeedbackSession(),first=harness({},session),second=harness({},session)
  await first.open();Object.assign(first.form,form())
  await second.open();assert.equal(second.form.quote,form().quote)
  second.form.note='A newer note from the other entry'
  first.stop()
  const third=harness({},session);await third.open()
  assert.equal(third.form.note,'A newer note from the other entry')
  assert.ok(first.messages.some(value=>value.includes('另一入口')))
  second.stop();third.stop()
})
