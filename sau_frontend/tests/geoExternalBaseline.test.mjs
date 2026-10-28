import assert from 'node:assert/strict'
import fs from 'node:fs'
import test from 'node:test'
import * as Vue from 'vue'
import {renderToString} from '@vue/server-renderer'
import {parse,compileScript,compileTemplate,compileStyle} from '@vue/compiler-sfc'
import * as baseline from '../src/utils/geoExternalBaseline.js'
import * as feedback from '../src/utils/geoExternalFeedback.js'
import * as checkpoints from '../src/utils/geoFeedbackSession.js'
import {sameBrand,journeyDate} from '../src/utils/geoJourney.js'

const clone=value=>JSON.parse(JSON.stringify(value))
const raw='  未找到足够的公开资料。\r\n不能确认官网，来源只有名称。\n  '
const deferred=()=>{let resolve,reject;const promise=new Promise((done,fail)=>{resolve=done;reject=fail});return {promise,resolve,reject}}
const until=async predicate=>{for(let n=0;n<100&&!predicate();n++)await new Promise(setImmediate);assert.ok(predicate(),'asynchronous request reached its checkpoint')}
async function fixture(changes={}){
  const profile={name:'合成协作科技有限公司',website:'https://example.org/company',entity_type:'company',industry:'团队协作',description:'供测试使用的公开说明。',...changes}
  const project={id:7,...profile}
  const cases=[{id:'q01',category:'discovery',question:'哪些团队协作服务适合小公司？',intent:'了解行业'},
    {id:'q02',category:'brand_identity',question:`${profile.name}提供哪些服务？`,intent:'核对主体'},
    {id:'q03',category:'website_guided',question:`请核对${profile.website}介绍的业务。`,intent:'核对官网'}]
  const plan={schema_version:'1.0',platform:'doubao_web',profile:clone(profile),cases,planner:{method:'template_not_ai'}}
  const profile_sha256=await baseline.externalSha(profile)
  const plan_sha256=await baseline.externalSha(Object.fromEntries(['schema_version','platform','profile','cases'].map(key=>[key,plan[key]])))
  plan.plan_sha256=plan_sha256
  const prepared={profile,profile_sha256,plan_sha256,plan,requires_user_confirmation:true}
  const case_markers=Object.fromEntries(await Promise.all(cases.map(async row=>[row.id,{basis:'frozen_plan_text',category:row.category,
    question_sha256:await baseline.externalSha(row.question),question_with_url:row.id==='q03'}])))
  return {project,prepared,frozen:{...clone(prepared),id:'f'.repeat(32),project_id:7,revision:1,case_markers,current_profile_matches:true,created_at:'2026-10-29T10:00:00+08:00'}}
}
const data=await fixture()
const receipt=async(body,operation,idempotent=false)=>({request_id:body.request_id,request_sha256:await baseline.externalOperationSha(operation,body),idempotent})
const freezeReply=async(body,value=data)=>({code:200,data:{receipt:await receipt(body,'freeze_external_baseline_plan'),frozen_plan:clone(value.frozen)}})
async function createReply(body,value=data){
  const observation=body.first_observation,response_sha256=await baseline.externalSha(observation.raw_response_text)
  const probe={id:'e'.repeat(32),cycle_id:'external-cycle-a',case_id:observation.case_id,source:'user_reported_external',native_verified:false,
    response_sha256,after:{response:observation.raw_response_text,response_sha256}}
  const cycle={id:probe.cycle_id,project_id:body.project_id,report_ref:`external-baseline:${body.frozen_plan_id}`,revision:1,
    baseline:null,baseline_source:'user_reported_external',profile:clone(value.prepared.profile),test_plan:clone(value.prepared.plan),
    capabilities:{native_probe:false,native_retest:false,native_metrics:false,editorial_research:true,work_drafting:true},
    external_baseline:{kind:'geo_external_baseline_v1',frozen_plan_id:body.frozen_plan_id,profile_sha256:value.prepared.profile_sha256,
      native_verified:false,independent_verification:false,baseline_observation_ids:[probe.id],observations:[clone(probe)]},probes:[probe]}
  return {code:200,data:{receipt:await receipt(body,'create_external_baseline_cycle'),cycle,probe}}
}
const filename=new URL('../src/components/GeoExternalBaseline.vue',import.meta.url)
const {descriptor,errors}=parse(fs.readFileSync(filename,'utf8'),{filename:filename.pathname})
assert.deepEqual(errors,[])
const compiled=compileScript(descriptor,{id:'external-baseline-test'})
const names=compiled.scriptSetupAst.flatMap(node=>node.type==='VariableDeclaration'?node.declarations:[]).filter(row=>row.id.type==='Identifier').map(row=>row.id.name)
let body=descriptor.scriptSetup.content
for(const node of compiled.scriptSetupAst.filter(node=>node.type==='ImportDeclaration').reverse())body=body.slice(0,node.start)+body.slice(node.end)
const pages=[]
test.afterEach(()=>{while(pages.length)pages.pop().stop()})
function harness(api={},session=checkpoints.createFeedbackSession(),value=data,clipboard=async()=>{}){
  const props=Vue.reactive({project:clone(value.project),plan:null,busy:false,blocked:false}),events=[],busyEvents=[],messages=[],exports=[],copies=[],unmount=[]
  const calls={prepare:[],freeze:[],create:[],list:[]},effect=Vue.effectScope()
  const defaults={prepare:async()=>({code:200,data:clone(value.prepared)}),freeze:request=>freezeReply(request,value),create:request=>createReply(request,value),
    list:async()=>({code:200,data:{plans:[clone(value.frozen)],has_more:false,next_offset:null}})}
  const geoExternalBaselineApi=Object.fromEntries(Object.keys(calls).map(name=>[name,async(...args)=>{calls[name].push(clone(args));return (api[name]||defaults[name])(...args)}]))
  const deps={computed:Vue.computed,reactive:Vue.reactive,ref:Vue.ref,watch:Vue.watch,...baseline,...feedback,...checkpoints,
    externalBaselineSession:session,defineProps:()=>props,defineEmits:()=>((...args)=>(args[0]==='busy'?busyEvents:events).push(args)),
    onUnmounted:callback=>unmount.push(callback),geoExternalBaselineApi,sameBrand,journeyDate,
    ElMessage:{info:message=>messages.push(message)},exportFeedbackDraft:(...args)=>exports.push(args),
    navigator:{clipboard:{writeText:async text=>{copies.push(text);return clipboard(text)}}}}
  const methods=effect.run(()=>new Function(...Object.keys(deps),body+`;return {${names.join(',')}}`)(...Object.values(deps)))
  let stopped=false
  const page={...methods,props,calls,events,busyEvents,messages,exports,copies,session,stop(){if(stopped)return;stopped=true;for(const close of unmount)close();effect.stop()}}
  pages.push(page);return page
}
async function frozenPage(api={},session,value=data){const page=harness(api,session,value);await page.prepare();page.confirmed.value=true;await page.submit();assert.ok(page.frozenPlan.value);return page}
async function render(page){
  const template=compileTemplate({source:descriptor.template.content,filename:filename.pathname,id:'external-baseline-test',compilerOptions:{bindingMetadata:compiled.bindings}})
  assert.deepEqual(template.errors,[])
  const code=template.code.replace(/import \{([^}]+)\} from "vue"/g,(_all,imports)=>`const {${imports.split(',').map(item=>{const [name,alias]=item.trim().split(/\s+as\s+/);return alias?`${name}:${alias}`:name}).join(',')}}=Vue;`).replace('export function render','return function render')
  const app=Vue.createSSRApp({props:['project','plan','busy','blocked'],setup:()=>page,render:new Function('Vue',code)(Vue)},page.props)
  for(const [name,tag] of [['el-button','button'],['el-input','textarea'],['el-select','select'],['el-option','option'],['el-checkbox','label'],['el-radio-group','div'],['el-radio-button','label'],['el-form','form'],['el-form-item','label'],['el-alert','aside'],['el-dialog','section']]){
    app.component(name,{props:['modelValue','disabled','loading','label','title'],setup:(props,{slots})=>()=>Vue.h(tag,{disabled:props.disabled,'aria-label':props.label},[slots.default?.()||props.title||props.modelValue,slots.footer?.()])})
  }
  return renderToString(app)
}

test('preparing templates invokes no AI and hashes actual canonical profile and plan content',async()=>{
  assert.deepEqual(baseline.externalPrepareBody(data.project,null,false),{project_id:7})
  const value=await baseline.externalPrepared(data.project,{code:200,data:data.prepared})
  assert.equal(value.plan.planner.method,'template_not_ai')
  assert.equal(await baseline.externalSha({b:2,a:1}),await baseline.externalSha({a:1,b:2}))
  const existing=baseline.externalPrepareBody(data.project,data.prepared.plan,true)
  assert.deepEqual(existing.existing_plan,data.prepared.plan);assert.notEqual(existing.existing_plan,data.prepared.plan)
  for(const change of [{profile:{...data.prepared.profile,name:'另一公司'}},{plan_sha256:'a'.repeat(64)},
    {plan:{...data.prepared.plan,planner:{method:'ai_generated'}}},{plan:{...data.prepared.plan,cases:[...data.prepared.plan.cases,data.prepared.plan.cases[0]]}}]){
    await assert.rejects(baseline.externalPrepared(data.project,{code:200,data:{...data.prepared,...change}}))
  }
  assert.throws(()=>baseline.externalPrepareBody(data.project,{...data.prepared.plan,profile:{...data.prepared.profile,website:'https://example.org/other'}},true))
})

test('mounting and opening never prepare, freeze, fetch old plans or infer actual sampling conditions',async()=>{
  const page=harness();assert.equal(page.visible.value,false);page.open()
  assert.equal(page.visible.value,true);assert.deepEqual(page.form,feedback.emptyExternalFeedback())
  for(const calls of Object.values(page.calls))assert.deepEqual(calls,[])
  const html=await render(page)
  assert.match(html,/固定问题模板，未调用AI/);assert.doesNotMatch(html,/aria-label="手机首轮回答原文"|复制这道原题/)
  assert.equal(page.candidateAvailable.value,false)
})

test('the actual prepare, explicit confirmation and freeze sequence opens copy and raw entry only after acknowledgement',async()=>{
  const page=harness();page.open();await page.prepare()
  assert.equal(page.prepared.value.plan_sha256,data.prepared.plan_sha256);assert.equal(page.frozenPlan.value,null)
  await page.submit();assert.equal(page.calls.freeze.length,0)
  assert.doesNotMatch(await render(page),/aria-label="手机首轮回答原文"|复制这道原题/)
  page.confirmed.value=true;await page.submit();assert.equal(page.calls.freeze.length,1)
  assert.equal(page.frozenPlan.value.id,data.frozen.id);assert.match(await render(page),/手机首轮回答原文/)
  assert.deepEqual(page.form,feedback.emptyExternalFeedback());await page.copyQuestion();assert.deepEqual(page.copies,[])
  page.form.caseId='q01';await page.copyQuestion();assert.deepEqual(page.copies,[data.prepared.plan.cases[0].question])
  assert.equal(page.form.questionMode,'unknown');assert.equal(page.form.clientMode,'unknown')
  page.form.raw=raw;await page.submit()
  assert.equal(page.calls.create.length,1);assert.equal(page.calls.create[0][0].first_observation.raw_response_text,raw)
  assert.deepEqual(page.calls.create[0][0].first_observation.metadata,{})
  assert.equal(page.events.length,1);assert.equal(page.events[0][0],'created');assert.equal(page.events[0][1].baseline,null)
  assert.equal(page.form.raw,'');assert.equal(page.visible.value,false);assert.equal(page.session.stats().entries,0)
})

test('review confirmation stays in the fixed footer and prepared questions can return to an editable choice without writes',async()=>{
  const page=harness();page.props.plan=clone(data.prepared.plan);page.useExisting.value=false;await page.prepare()
  assert.equal(page.prepared.value.plan_sha256,data.prepared.plan_sha256);page.confirmed.value=true
  const footer=descriptor.template.content.slice(descriptor.template.content.indexOf('<template #footer>'))
  assert.match(footer,/data-testid="external-baseline-confirmation"[\s\S]*v-model="confirmed"/)
  assert.doesNotMatch(descriptor.template.content.slice(0,descriptor.template.content.indexOf('<template #footer>')),/v-model="confirmed"/)
  assert.match(await render(page),/data-testid="external-baseline-reselect"/)
  page.backToPlans()
  assert.equal(page.prepared.value,null);assert.equal(page.frozenPlan.value,null);assert.equal(page.confirmed.value,false)
  assert.equal(page.session.stats().entries,0);assert.match(page.notice.value,/尚未固定原题或保存回答/)
  assert.deepEqual(page.calls.freeze,[]);assert.deepEqual(page.calls.create,[])
  page.useExisting.value=true;await page.prepare()
  assert.deepEqual(page.calls.prepare[1][0].existing_plan,data.prepared.plan)
  assert.equal(page.confirmed.value,false)
})

test('reselecting never discards raw answers, unknown requests, in-flight operations or another entrance checkpoint',async()=>{
  const rawPage=await frozenPage();rawPage.form.raw=raw;rawPage.backToPlans()
  assert.equal(rawPage.form.raw,raw);assert.equal(rawPage.frozenPlan.value.id,data.frozen.id)
  assert.doesNotMatch(await render(rawPage),/data-testid="external-baseline-reselect"/)
  const request=deferred(),pending=harness({freeze:()=>request.promise});await pending.prepare();pending.confirmed.value=true
  const submit=pending.submit();await until(()=>pending.calls.freeze.length===1);pending.backToPlans()
  assert.ok(pending.prepared.value);request.reject(Error('lost acknowledgement'));await submit
  const checkpoint=pending.session.read(pending.scopeKey());pending.backToPlans()
  assert.deepEqual(pending.session.read(pending.scopeKey()),checkpoint);assert.equal(pending.uncertain.value,true)
  assert.doesNotMatch(await render(pending),/data-testid="external-baseline-reselect"/)
  const session=checkpoints.createFeedbackSession(),first=harness({},session);await first.prepare()
  const second=harness({},session);second.confirmed.value=true
  first.backToPlans();assert.ok(first.prepared.value);assert.match(first.checkpointError.value,/另一入口/)
  assert.equal(session.read(first.scopeKey()).value.confirmed,true)
  for(const key of ['busy','blocked']){second.props[key]=true;second.backToPlans();assert.ok(second.prepared.value);second.props[key]=false}
})

test('explicit pending-plan restoration reads the server without preparing or freezing a replacement',async()=>{
  const page=harness();await page.readStored()
  assert.deepEqual(page.calls.list,[[7,0]]);assert.equal(page.storedPlans.value.length,1)
  page.chooseStored(page.storedPlans.value[0]);assert.equal(page.frozenPlan.value.id,data.frozen.id)
  assert.equal(page.form.caseId,'');assert.equal(page.form.raw,'')
  assert.deepEqual(page.calls.prepare,[]);assert.deepEqual(page.calls.freeze,[]);assert.deepEqual(page.calls.create,[])
  page.form.raw='保留当前填写';page.chooseStored({...data.frozen,id:'a'.repeat(32)})
  assert.equal(page.frozenPlan.value.id,data.frozen.id);assert.equal(page.form.raw,'保留当前填写')
})

test('old-profile plans remain viewable but cannot save and can return to the plan list without creating a cycle',async()=>{
  const page=harness({list:async()=>({code:200,data:{plans:[{...clone(data.frozen),current_profile_matches:false}],has_more:false}})})
  page.props.project={...clone(data.project),name:'更新后的公司名称'}
  await page.readStored();page.chooseStored(page.storedPlans.value[0]);page.form.caseId='q02'
  assert.equal(page.locked.value,true);assert.match(await render(page),/旧题可查看，不能向这一轮保存新原文/)
  await page.submit();assert.equal(page.calls.create.length,0)
  page.backToPlans();assert.equal(page.frozenPlan.value,null);assert.equal(page.confirmed.value,false)
  assert.match(page.notice.value,/尚未创建外部内容轮/);assert.equal(page.calls.freeze.length,0)
})

test('pending plan lists reject malformed pagination, foreign project rows and forged question markers',async()=>{
  for(const reply of [{code:200,data:{plans:[],has_more:true,next_offset:1}},
    {code:200,data:{plans:[{...data.frozen,project_id:8}],has_more:false}},
    {code:200,data:{plans:[{...data.frozen,case_markers:{}}],has_more:false}}]){
    const page=harness({list:async()=>reply});await page.readStored()
    assert.deepEqual(page.storedPlans.value,[]);assert.ok(page.error.value);assert.equal(page.frozenPlan.value,null)
  }
})

test('raw whitespace remains exact and optional actual conditions are never inferred from the plan or writing mode',async()=>{
  const form={...feedback.emptyExternalFeedback(),caseId:'q01',raw}
  const body=await baseline.externalCreateBody(data.project,data.frozen,form)
  assert.equal(body.first_observation.raw_response_text,raw);assert.deepEqual(body.first_observation.metadata,{})
  assert.equal(body.first_observation.expected_question_sha256,await baseline.externalSha(data.prepared.plan.cases[0].question))
  for(const changes of [{raw:'   '},{raw:'字'.repeat(400000)},{clientMode:'native'},{completeness:true},{sourceLinks:'javascript:alert(1)'},{collectedAt:'malformed'}]){
    await assert.rejects(baseline.externalCreateBody(data.project,data.frozen,{...form,...changes}))
  }
  const explicit=await baseline.externalCreateBody(data.project,data.frozen,{...form,questionMode:'same',clientMode:'work',completeness:'partial'})
  assert.deepEqual(explicit.first_observation.metadata,{question:data.prepared.plan.cases[0].question,client_mode:'work',answer_complete:false})
})

test('freeze receipts bind request identity, operation payload digest and every frozen question marker',async()=>{
  const body=baseline.externalFreezeBody(data.project,data.prepared,true),digest=await baseline.externalOperationSha('freeze_external_baseline_plan',body)
  const reply=await freezeReply(body)
  assert.equal((await baseline.externalFreezeReceipt(data.project,reply,body,digest)).id,data.frozen.id)
  for(const mutation of [value=>{value.data.receipt.request_id='foreign'},value=>{value.data.receipt.request_sha256='a'.repeat(64)},
    value=>{value.data.receipt.idempotent='true'},value=>{value.data.frozen_plan.project_id=8},
    value=>{value.data.frozen_plan.case_markers.q01.question_sha256='a'.repeat(64)},value=>{value.data.frozen_plan.plan.cases[0].question='篡改原题'}]){
    const forged=clone(reply);mutation(forged);await assert.rejects(baseline.externalFreezeReceipt(data.project,forged,body,digest))
  }
  await assert.rejects(baseline.externalFreezeReceipt(data.project,reply,body,await baseline.externalOperationSha('create_external_baseline_cycle',body)))
})

test('cycle receipts refuse foreign raw text, plan identity, fake native verification and enabled native rates',async()=>{
  const body=await baseline.externalCreateBody(data.project,data.frozen,{...feedback.emptyExternalFeedback(),caseId:'q01',raw})
  const digest=await baseline.externalOperationSha('create_external_baseline_cycle',body),reply=await createReply(body)
  assert.equal((await baseline.externalCreateReceipt(data.project,reply,body,digest)).baseline,null)
  for(const mutation of [value=>{value.data.receipt.request_sha256='a'.repeat(64)},value=>{value.data.cycle.project_id=9},
    value=>{value.data.cycle.profile.website='https://example.org/foreign'},value=>{value.data.cycle.test_plan.cases[0].question='篡改'},
    value=>{value.data.probe.after.response=raw.trim()},value=>{value.data.probe.response_sha256='a'.repeat(64)},
    value=>{value.data.cycle.external_baseline.baseline_observation_ids=[]},value=>{value.data.probe.native_verified=true},
    value=>{value.data.cycle.external_baseline.independent_verification=true},value=>{value.data.cycle.baseline={cases:[]}},
    ...['native_probe','native_retest','native_metrics'].map(key=>value=>{value.data.cycle.capabilities[key]=true})]){
    const forged=clone(reply);mutation(forged);await assert.rejects(baseline.externalCreateReceipt(data.project,forged,body,digest))
  }
})

test('unknown freeze saves survive unmount and retry the identical request instead of creating a second frozen plan',async()=>{
  const session=checkpoints.createFeedbackSession(),calls=[]
  const first=harness({freeze:async request=>{calls.push(clone(request));throw Error('lost freeze acknowledgement')}},session)
  await first.prepare();first.confirmed.value=true;await first.submit()
  assert.equal(first.uncertain.value,true);assert.equal(first.locked.value,true);first.stop()
  const second=harness({freeze:async request=>{calls.push(clone(request));return freezeReply(request)}},session)
  assert.equal(second.uncertain.value,true);second.confirmed.value=false;await second.submit()
  assert.deepEqual(calls[1],calls[0]);assert.equal(second.frozenPlan.value.id,data.frozen.id);assert.equal(second.uncertain.value,false)
})

test('unknown creation retries its immutable original after unmount even when the visible draft changes',async()=>{
  const session=checkpoints.createFeedbackSession(),calls=[]
  const first=await frozenPage({create:async request=>{calls.push(clone(request));throw Error('lost creation acknowledgement')}},session)
  first.form.caseId='q01';first.form.raw=raw;await first.submit();first.stop()
  const second=harness({create:async request=>{calls.push(clone(request));return createReply(request)}},session)
  assert.equal(second.form.raw,raw);assert.equal(second.uncertain.value,true)
  second.form.raw='这段后来的编辑不能替换原请求';second.form.caseId='q02';await second.submit()
  assert.deepEqual(calls[1],calls[0]);assert.equal(second.events.length,1);assert.equal(second.session.stats().entries,0)
})

test('definitive validation failures keep the draft editable and malicious backend messages stay private',async()=>{
  const page=await frozenPage({create:async()=>{throw {response:{status:400,data:{msg:'<img onerror=private_secret()> internal-token'}}}}})
  page.form.caseId='q01';page.form.raw=raw;await page.submit()
  assert.equal(page.uncertain.value,false);assert.equal(page.form.raw,raw);assert.equal(page.locked.value,false)
  assert.doesNotMatch(page.error.value,/private_secret|internal-token|img/)
  assert.doesNotMatch(baseline.externalBaselineError(Error('private local path C:/secret')),/private|secret/)
  page.exportDraft();assert.equal(page.exports[0][0].form.raw,raw)
})

test('a malformed success retains the original request for explicit retry and never emits a new cycle',async()=>{
  const page=await frozenPage({create:async request=>{const reply=await createReply(request);reply.data.receipt.request_sha256='a'.repeat(64);return reply}})
  page.form.caseId='q01';page.form.raw=raw;await page.submit()
  assert.equal(page.form.raw,raw);assert.equal(page.uncertain.value,true);assert.equal(page.events.length,0)
  await page.submit();assert.deepEqual(page.calls.create[1],page.calls.create[0]);assert.equal(page.events.length,0)
})

test('A to B to A discards late preparation results and never starts a replacement request automatically',async()=>{
  const wait=deferred(),page=harness({prepare:()=>wait.promise})
  const request=page.prepare();page.props.project={...clone(data.project),name:'另一公司'};page.props.project=clone(data.project)
  wait.resolve({code:200,data:clone(data.prepared)});await request
  assert.equal(page.prepared.value,null);assert.equal(page.frozenPlan.value,null);assert.equal(page.pending.value,'')
  assert.equal(page.calls.prepare.length,1);assert.equal(page.calls.freeze.length,0)
})

test('late creation receipts settle only their own session and cannot emit after unmount',async()=>{
  const wait=deferred(),session=checkpoints.createFeedbackSession(),first=await frozenPage({create:()=>wait.promise},session)
  first.form.caseId='q01';first.form.raw=raw;const request=first.submit();await until(()=>first.calls.create.length===1)
  const body=first.calls.create[0][0];first.stop()
  const second=harness({},session);second.props.project={...clone(data.project),name:'另一公司'}
  wait.resolve(await createReply(body));await request
  assert.equal(first.events.length,0);assert.equal(second.events.length,0);assert.equal(second.form.raw,'')
  assert.equal(session.stats().entries,0);assert.equal(first.busyEvents.at(-1)[1].active,false)
})

test('a creation A to B to A keeps its original request and requires an explicit retry before applying a late receipt',async()=>{
  const wait=deferred(),requests=[],page=await frozenPage({create:async request=>{
    requests.push(clone(request));return requests.length===1?wait.promise:createReply(request)
  }})
  page.form.caseId='q01';page.form.raw=raw;const pending=page.submit();await until(()=>requests.length===1)
  page.props.project={...clone(data.project),name:'另一公司'};page.props.project=clone(data.project)
  assert.equal(page.form.raw,raw);assert.equal(page.uncertain.value,true)
  wait.resolve(await createReply(requests[0]));await pending
  assert.equal(page.events.length,0);assert.equal(page.form.raw,raw)
  await page.submit();assert.equal(requests.length,2);assert.deepEqual(requests[1],requests[0])
  assert.equal(page.events.length,1);assert.equal(page.form.raw,'')
})

test('capacity exhaustion blocks network writes and leaves the reviewed questions and raw draft exportable',async()=>{
  const session=checkpoints.createFeedbackSession({entries:0,bytes:100000}),page=harness({},session)
  await page.prepare();page.confirmed.value=true;await page.submit()
  assert.equal(page.calls.freeze.length,0);assert.ok(page.prepared.value);assert.match(page.checkpointError.value,/容量/)
  assert.equal(page.uncertain.value,false);page.exportDraft();assert.equal(page.exports[0][0].prepared.plan_sha256,data.prepared.plan_sha256)
  page.chooseStored(clone(data.frozen));page.form.caseId='q01';page.form.raw=raw;await page.submit()
  assert.equal(page.calls.create.length,0);assert.equal(page.form.raw,raw);assert.equal(page.uncertain.value,false)
  page.exportDraft();assert.equal(page.exports.at(-1)[0].form.raw,raw)
})

test('two mounted entrances use session CAS and the stale entrance cannot overwrite or send a new freeze',async()=>{
  const session=checkpoints.createFeedbackSession(),first=harness({},session)
  await first.prepare();const second=harness({},session)
  first.form.note='第一入口的新填写';second.confirmed.value=true;await second.submit()
  assert.equal(second.calls.freeze.length,0);assert.match(second.checkpointError.value,/另一入口/)
  const stored=session.read(baseline.externalBaselineDraftKey(data.project,null))
  assert.equal(stored.value.form.note,'第一入口的新填写');assert.equal(second.prepared.value.plan_sha256,data.prepared.plan_sha256)
})

test('a late valid receipt cannot overwrite a newer session version from another entrance',async()=>{
  const wait=deferred(),session=checkpoints.createFeedbackSession(),page=await frozenPage({create:()=>wait.promise},session)
  page.form.caseId='q01';page.form.raw=raw;const request=page.submit();await until(()=>page.calls.create.length===1)
  const key=baseline.externalBaselineDraftKey(data.project,null),current=session.read(key)
  assert.equal(session.write(key,{...current.value,form:{...current.value.form,note:'另一入口的新说明'}},current.token,current.version).ok,true)
  wait.resolve(await createReply(page.calls.create[0][0]));await request
  assert.equal(page.events.length,0);assert.equal(page.uncertain.value,true);assert.match(page.error.value,/另一入口/)
  assert.equal(session.read(key).value.form.note,'另一入口的新说明')
})

test('copy completion messages stay with the exact original case and scope without inferring sampling facts',async()=>{
  const wait=deferred(),page=harness({},undefined,data,()=>wait.promise)
  page.chooseStored(clone(data.frozen));page.form.caseId='q01';const pending=page.copyQuestion()
  page.form.caseId='q02';wait.resolve();await pending
  assert.equal(page.copyMessage.value,'');assert.equal(page.copyPending.value,false)
  assert.deepEqual(page.copies,[data.prepared.plan.cases[0].question]);assert.equal(page.form.questionMode,'unknown')
  const later=deferred(),other=harness({},undefined,data,()=>later.promise)
  other.chooseStored(clone(data.frozen));other.form.caseId='q01';const old=other.copyQuestion()
  other.props.project={...clone(data.project),name:'另一公司'};other.props.project=clone(data.project);later.resolve();await old
  assert.equal(other.copyMessage.value,'');assert.equal(other.copyPending.value,false)
})

test('external summaries keep observations separate from native rates and preserve native legacy cases',async()=>{
  const body=await baseline.externalCreateBody(data.project,data.frozen,{...feedback.emptyExternalFeedback(),caseId:'q01',raw})
  const reply=await createReply(body),cycle=reply.data.cycle
  cycle.external_baseline.observations.push(clone(reply.data.probe),{...clone(reply.data.probe),case_id:'q02',native_verified:true},
    {...clone(reply.data.probe),case_id:'q03',cycle_id:'foreign-cycle'})
  cycle.completion_rate=1;cycle.brand_mention_rate=1
  const summary=baseline.externalBaselineSummary(cycle)
  assert.equal(summary.recorded,1);assert.equal(summary.total,3);assert.match(summary.text,/尚未全测|起始观察保留/)
  assert.match(summary.text,/原生指标不可用/);assert.doesNotMatch(summary.text,/%|完成率|提及率|已全部完成/)
  for(const name of ['native_probe','native_retest','native_metrics'])assert.equal(baseline.nativeCycleCapability(cycle,name),false)
  assert.deepEqual(baseline.originalCycleCases(cycle),data.prepared.plan.cases)
  const native={baseline:{cases:[{id:'native'}]}}
  assert.equal(baseline.nativeCycleCapability(native,'native_retest'),true);assert.deepEqual(baseline.originalCycleCases(native),native.baseline.cases)
})

test('API wrappers use explicit bounded prepare, freeze, list and create routes without native or AI requests',async()=>{
  const source=fs.readFileSync(new URL('../src/api/geoExternalBaseline.js',import.meta.url),'utf8').replace(/^import .*$/gm,''),calls=[]
  const api=new Function('http',source.replace('export const geoExternalBaselineApi','const geoExternalBaselineApi')+';return geoExternalBaselineApi')({get:(...args)=>calls.push(['get',...args]),post:(...args)=>calls.push(['post',...args])})
  const payload={project_id:7};api.list(7);api.list(7,20);api.prepare(payload);api.freeze(payload);api.create(payload)
  assert.deepEqual(calls.map(item=>item.slice(0,3)),[
    ['get','/api/geo-content/external-baseline-plans',{project_id:7,offset:0}],['get','/api/geo-content/external-baseline-plans',{project_id:7,offset:20}],
    ['post','/api/geo-content/external-baseline-plans/prepare',payload],['post','/api/geo-content/external-baseline-plans/freeze',payload],['post','/api/geo-content/external-cycles',payload]])
  for(const call of calls)assert.deepEqual(call[3],{timeout:20000,suppressGlobalError:true})
})

test('the dialog compiles with a viewport bound, scrolling body, stable footer and reduced-motion support',()=>{
  assert.deepEqual(compileTemplate({source:descriptor.template.content,filename:filename.pathname,id:'external-baseline-test',compilerOptions:{bindingMetadata:compiled.bindings}}).errors,[])
  for(const style of descriptor.styles)assert.deepEqual(compileStyle({source:style.content,filename:filename.pathname,id:'external-baseline-test'}).errors,[])
  const css=descriptor.styles.map(style=>style.content).join('\n')
  assert.match(css,/max-height:calc\(100dvh - 24px\)/);assert.match(css,/\.el-dialog__body\{min-height:0;overflow-y:auto/)
  assert.match(css,/\.el-dialog__footer\{flex-shrink:0/);assert.match(css,/@media\(max-width:650px\)/)
  assert.match(css,/\.external-baseline-confirmation \.el-checkbox\{height:auto;white-space:normal/)
  assert.match(css,/\.external-baseline-confirmation \.el-checkbox__label\{white-space:normal;line-height:1\.6/)
  assert.match(css,/@media\(prefers-reduced-motion:reduce\)/);assert.match(descriptor.template.content,/#footer/)
  assert.match(descriptor.template.content,/:close-on-press-escape="false"/);assert.match(descriptor.template.content,/aria-label="手机首轮回答原文"/)
  assert.doesNotMatch(descriptor.scriptSetup.content,/localStorage|sessionStorage|start_test|generate_research/)
})
