import assert from 'node:assert/strict'
import fs from 'node:fs'
import test from 'node:test'
import * as Vue from 'vue'
import { renderToString } from '@vue/server-renderer'
import { parse,compileScript,compileTemplate,compileStyle } from '@vue/compiler-sfc'
import { journeyDate } from '../src/utils/geoJourney.js'
import { publicationReviewUrl } from '../src/utils/geoPublicationReview.js'
import { scopedRecoveryCheckpoint } from '../src/utils/geoTaskRecovery.js'

const load=path=>{
  const filename=new URL(path,import.meta.url),{descriptor,errors}=parse(fs.readFileSync(filename,'utf8'),{filename:filename.pathname})
  assert.deepEqual(errors,[])
  const script=compileScript(descriptor,{id:path})
  const declarations=script.scriptSetupAst.flatMap(node=>node.type==='VariableDeclaration'?node.declarations:[])
  return {filename,descriptor,script,expression:name=>{const d=declarations.find(d=>d.id.name===name);assert.ok(d,name);return descriptor.scriptSetup.content.slice(d.init.start,d.init.end)},names:declarations.map(d=>d.id.name)}
}
const component=load('../src/components/GeoSingleCaseProbe.vue'),journey=load('../src/views/GeoJourney.vue')
const cases=[{id:'q01',category:'discovery',question:'如何比较协作工具？'},
  {id:'q02',category:'brand_identity',question:'示例协作平台是什么，与示例组织有什么关系？'},
  {id:'q03',category:'website_guided',question:'请核对 https://example.org/ 的公开功能。'}]
const side=(changes={})=>({response:'本次公开资料不足，尚未确认具体官网。',collected_at:'2026-10-25T10:00:00+08:00',model_label:'示例 Lite 高',client_mode:'work',answer_evaluable:true,answer_error:null,website_presence:false,official_source_present:null,official_citation_present:null,citations:null,displayed_sources:null,...changes})
const probe=(changes={})=>({id:'probe-a',run_id:'run-probe-a',cycle_id:'cycle-a',case_id:'q02',category:'brand_identity',question:cases[1].question,status:'observed',source:'research_prototype_import',native_verified:false,selected_count:1,created_at:'2026-10-26T10:00:00+08:00',collected_at:'2026-10-26T10:01:00+08:00',website_in_question:false,website_test_without_url:true,before:side(),after:side({response:'公开官网 example.org 介绍了示例协作平台的产品与技术服务。',collected_at:'2026-10-26T10:01:00+08:00',website_presence:true,citations:[{title:'官网页面条目',url:null}],displayed_sources:[]}),conditions:{same_question:true,client_mode_match:true,model_label_match:true},limitations:['单题观察，不代表文章因果效果。'],...changes})
const fact={id:'fact-ai',text:'示例组织提供协作AI产品与模型接入服务。',confirmed:true}
const rejectedQuote='只查到工商登记，尚无法从公开资料识别该组织的AI产品与模型接入业务。'
const businessReview=(changes={})=>({status:'not_acknowledged',by:'assistant',quote:rejectedQuote,reference_fact_ids:['fact-ai'],reviewed_at:'2026-10-26T11:00:00+08:00',delivery_capability:'unknown',note:'',...changes})
const externalProbe=(changes={})=>probe({source:'user_reported_external',native_verified:false,observed_question:null,question_role:'cycle_anchor_only',before:null,
  after:side({response:`文字提到了备案域名 example.org。${rejectedQuote}`,answer_evaluable:null,client_mode:null,model_label:null,collected_at:null,website_presence:true,response_sha256:'a'.repeat(64)}),
  conditions:{same_question:null,client_mode_match:null,model_label_match:null},business_relationship_review:businessReview(),...changes})
const cycle=(changes={})=>({id:'cycle-a',baseline:{cases},probes:[probe()],retests:[],...changes})
const componentSetup=(props,emit=()=>{})=>{
  const deps={computed:Vue.computed,ref:Vue.ref,watch:Vue.watch,defineProps:()=>props,defineEmits:()=>emit,journeyDate,publicationReviewUrl}
  const body=component.descriptor.scriptSetup.content.replace(/^import .*$/gm,'')
  return new Function(...Object.keys(deps),body+`;return {${component.names.join(',')}};`)(...Object.values(deps))
}
const componentHarness=(value=cycle())=>{
  const props=Vue.reactive({cycle:value,busy:false,canStart:true}),events=[]
  return {props,events,...componentSetup(props,(...args)=>events.push(args))}
}
const deferred=()=>{let resolve;const promise=new Promise(done=>resolve=done);return {promise,resolve}}
const journeyHarness=()=>{
  const calls={probe:[],whole:[],confirms:[],applied:[]}
  const deps={computed:Vue.computed,cycle:Vue.ref(cycle()),cycleId:Vue.ref('cycle-a'),step:Vue.ref(4),nativeAvailable:Vue.ref(true),
    task:Vue.ref({status:'idle'}),taskId:Vue.ref('old-task'),taskKind:Vue.ref('retest'),working:Vue.ref(false),finishingTask:Vue.ref(false),error:Vue.ref(''),
    recoveryTask:Vue.ref({id:'old-failed-retest',kind:'retest'}),replacementSourceId:Vue.ref(null),project:Vue.ref({}),plan:Vue.ref(null),planConfirmed:Vue.ref(false),
    report:Vue.ref(null),reports:Vue.ref([]),reportId:Vue.ref(''),reportOrigin:Vue.ref(''),historyReportId:Vue.ref(''),selectedTarget:Vue.ref(null),matchingDrafts:Vue.ref([]),articleId:Vue.ref(null),
    scopedRecoveryCheckpoint,sameBrand:()=>true,loadReports:async()=>{},loadEditor:()=>{},persist:()=>{},
    ElMessageBox:{confirm:async(...args)=>calls.confirms.push(args)}}
  deps.nativeBusy=Vue.computed(()=>deps.task.value.status==='running')
  deps.busy=Vue.computed(()=>deps.working.value||deps.nativeBusy.value||deps.finishingTask.value)
  deps.window={pywebview:{api:{start_research_probe:async(...args)=>{calls.probe.push(args);return {id:'new-probe-task',status:'running'}},
    start_research_retest:async(...args)=>calls.whole.push(args),get_task:async()=>deps.task.value}}}
  deps.geoContentApi={get:async()=>({data:cycle({probes:[probe({source:'native_collector',native_verified:true})]})})}
  deps.applyCycle=(value,options)=>{calls.applied.push(options);deps.cycle.value=value}
  const names=['singleProbeAvailable','run','startSingleCaseProbe','poll']
  const functions=new Function(...Object.keys(deps),`
    let destroyed=false,contextEpoch=0,liveTaskId='',handledTask='',pollPending=false;
    ${names.map(name=>`const ${name}=${journey.expression(name)};`).join('\n')}
    return {${names.join(',')},epoch:()=>contextEpoch++,destroy:()=>destroyed=true};
  `)(...Object.values(deps))
  return {...deps,...functions,calls}
}
const renderComponent=async value=>{
  const template=compileTemplate({source:component.descriptor.template.content,filename:component.filename.pathname,id:'single-probe-render',compilerOptions:{bindingMetadata:component.script.bindings}})
  assert.deepEqual(template.errors,[])
  const code=template.code.replace(/import \{([^}]+)\} from "vue"/g,(_all,imports)=>`const {${imports.split(',').map(item=>{const [name,alias]=item.trim().split(/\s+as\s+/);return alias?`${name}:${alias}`:name}).join(',')}}=Vue;`).replace('export function render','return function render')
  const render=new Function('Vue',code)(Vue)
  const app=Vue.createSSRApp({props:['cycle','busy','canStart'],setup:props=>componentSetup(props),render},{cycle:value,busy:false,canStart:true})
  for(const [name,tag] of [['el-select','select'],['el-option','option'],['el-button','button'],['el-tag','span']])app.component(name,{props:['disabled','value','label'],setup:(props,{slots})=>()=>Vue.h(tag,{disabled:props.disabled,value:props.value},slots.default?.()||props.label)})
  return renderToString(app)
}

test('single-case selection requires a deliberate original question and never emits altered question text',()=>{
  const page=componentHarness()
  page.requestProbe();assert.deepEqual(page.events,[])
  page.selectedCaseId.value='q02';page.requestProbe()
  assert.deepEqual(page.events,[['probe',{cycleId:'cycle-a',caseId:'q02'}]])
  page.props.busy=true;page.requestProbe();assert.equal(page.events.length,1)
  page.props.busy=false;page.props.canStart=false;page.requestProbe();assert.equal(page.events.length,1)
  page.props.canStart=true;page.selectedCaseId.value='unknown';page.requestProbe();assert.equal(page.events.length,1)
})

test('missing or duplicate original questions and foreign-cycle observations cannot be selected as this cycle',()=>{
  const page=componentHarness(cycle({baseline:{cases:[...cases,cases[1]]},probes:[probe(),probe({id:'other',cycle_id:'cycle-b'}),probe({id:'multi',selected_count:3})]}))
  assert.equal(page.cases.value.some(item=>item.id==='q02'),false)
  assert.equal(page.observations.value.length,1)
  page.props.cycle.baseline=null;assert.deepEqual(page.cases.value,[])
})

test('presence is not correctness or citation and incomplete, capacity, private and missing fields remain unknown',()=>{
  const page=componentHarness()
  assert.equal(page.presence(side()),'未出现');assert.equal(page.presence(side({website_presence:true})),'出现')
  for(const error of ['service_capacity_unavailable','background_task_not_final','private_account_context','human_verification_required',null]){
    const value=side({answer_evaluable:false,answer_error:error,website_presence:false,official_citation_present:false})
    assert.equal(page.presence(value),'未知');assert.equal(page.observed(value,'official_citation_present'),'未知')
    assert.match(page.unavailable(value),/未取得|尚未|排除|不能按/)
  }
  assert.equal(page.presence(side({website_presence:null})),'未知')
  assert.equal(page.presence(side({website_presence:'false'})),'未知')
  assert.equal(page.observed(side({website_presence:true}),'official_citation_present'),'未知')
})

test('research import remains explicit, URL-provided questions never become URL-free and unknown conditions stay unknown',()=>{
  const page=componentHarness()
  assert.equal(page.origin(probe({native_verified:true})),'研究结果导入')
  assert.equal(page.origin(probe({source:'native_collector',native_verified:true})),'软件采集')
  assert.equal(page.origin(probe({source:'native_collector',native_verified:false})),'采集来源待确认')
  assert.equal(page.questionScope(probe()),'原题未提供官网地址')
  assert.equal(page.questionScope(probe({website_in_question:true})),'原题提供了官网地址')
  assert.equal(page.questionScope(probe({category:'website_guided'})),'原题提供了官网地址')
  assert.match(page.questionScope(probe({website_in_question:null})),/待确认/)
  assert.match(page.conditions(probe({conditions:{same_question:true,client_mode_match:null,model_label_match:false}})),/界面是否一致未知.*模型标签不同/)
})

test('source rows preserve actual unknown URLs and do not turn answer-domain presence into links',()=>{
  const page=componentHarness(),value=side({website_presence:true,citations:[{title:'真实引用标题',url:null},{title:'不安全链接',url:'javascript:alert(1)'},{title:'本地链接',url:'http://127.0.0.1/private'},{title:'公开条目',url:'https://example.org/page'}],displayed_sources:null})
  const rows=page.sourceRows(value)
  assert.equal(rows.length,4);assert.equal(rows[0].url,null);assert.equal(rows[1].url,null);assert.equal(rows[2].url,null)
  assert.equal(rows[3].url,'https://example.org/page')
  assert.deepEqual(page.sourceRows(side({website_presence:true})),[])
})

test('external reports keep their unknown actual question and collection conditions distinct from a cycle anchor',()=>{
  const page=componentHarness(cycle({facts:[fact],probes:[externalProbe()]})),value=externalProbe({native_verified:true})
  assert.equal(page.observations.value.length,1)
  assert.equal(page.origin(value),'用户外部反馈')
  assert.equal(page.sides(value).length,1);assert.equal(page.sides(value)[0].label,'用户提供原文')
  assert.match(page.questionScope(value),/实际提问.*未知/)
  assert.equal(page.presence(value.after,value),'出现')
  assert.equal(page.observed(value.after,'official_citation_present'),'未知')
  assert.match(page.conditions({...value,conditions:{same_question:true,client_mode_match:true,model_label_match:true}}),/问题一致性未知.*界面是否一致未知.*模型是否一致未知/)
})

test('website text and native provenance alone never acknowledge public business facts',()=>{
  const page=componentHarness(cycle({facts:[fact]}))
  for(const value of [probe(),probe({source:'native_collector',native_verified:true}),externalProbe({business_relationship_review:null})]){
    assert.match(page.businessStage(value),/待核对/)
  }
})

test('a domain-present answer with an exact fact-backed negative review explains the actual acceptance gap',()=>{
  const page=componentHarness(cycle({facts:[fact]})),value=externalProbe()
  assert.equal(page.businessStage(value),'官网地址出现，但公开业务资料未被识别（助手核对）')
  assert.equal(page.businessStage({...value,business_relationship_review:businessReview({by:'operator'})}),'官网地址出现，但公开业务资料未被识别（人工核对）')
  assert.equal(page.businessReview(value).quote,rejectedQuote)
  assert.doesNotMatch(page.businessStage(value),/没有能力|引用成功|GEO成功/)
})

test('missing quote, unconfirmed facts, unsupported reviewer or incomplete evidence remains pending',()=>{
  const page=componentHarness(cycle({facts:[fact,{id:'unconfirmed',text:'尚未确认资料',confirmed:false}]})),value=externalProbe()
  for(const review of [null,businessReview({status:'unknown'}),businessReview({by:'automatic'}),businessReview({quote:'原答没有这句话'}),
    businessReview({quote:''}),businessReview({reference_fact_ids:[]}),businessReview({reference_fact_ids:['unconfirmed']}),businessReview({reference_fact_ids:['removed']}),businessReview({reference_fact_ids:['fact-ai','fact-ai']})]){
    assert.match(page.businessStage({...value,business_relationship_review:review}),/待核对/)
  }
  assert.match(page.businessStage({...value,after:{...value.after,answer_evaluable:false}}),/待核对/)
  assert.match(page.businessStage({...value,after:{...value.after,answer_error:'service_capacity_unavailable'}}),/待核对/)
  assert.match(page.businessStage({...value,source:'native_collector',native_verified:true}),/待核对/)
})

test('positive exact-text review is attributed and still cannot become overall optimization or delivery proof',()=>{
  const page=componentHarness(cycle({facts:[fact]})),quote='原文说明示例组织提供协作AI产品与模型接入服务。'
  const value=probe({after:side({response:quote,website_presence:true}),business_relationship_review:businessReview({status:'acknowledged',by:'operator',quote})})
  assert.match(page.businessStage(value),/原文已提及.*人工核对/)
  assert.match(page.businessStage(value),/不代表整体优化已达标/)
  assert.doesNotMatch(page.businessStage(value),/GEO成功|引用成功|业务正确率/)
})

test('one explicit confirmation sends only the original case once and keeps whole-round/history data',async()=>{
  const page=journeyHarness(),decision=deferred(),oldHistory=page.recoveryTask.value
  page.ElMessageBox.confirm=(...args)=>{page.calls.confirms.push(args);return decision.promise}
  const first=page.startSingleCaseProbe({cycleId:'cycle-a',caseId:'q02'})
  await page.startSingleCaseProbe({cycleId:'cycle-a',caseId:'q02'})
  assert.equal(page.calls.confirms.length,1);assert.deepEqual(page.calls.probe,[])
  decision.resolve();await first
  assert.deepEqual(page.calls.probe,[['cycle-a','q02']]);assert.deepEqual(page.calls.whole,[])
  assert.equal(page.taskId.value,'new-probe-task');assert.equal(page.taskKind.value,'probe')
  assert.equal(page.recoveryTask.value,oldHistory);assert.equal(page.cycle.value.baseline.cases.length,3)
  assert.deepEqual(page.cycle.value.retests,[])
})

test('wrong case, foreign cycle and unavailable capability never request or send a probe',async()=>{
  for(const change of [p=>p.cycleId.value='cycle-b',p=>p.step.value=2,p=>delete p.window.pywebview.api.start_research_probe,p=>delete p.cycle.value.probes,p=>p.cycle.value.baseline.cases=[cases[0]]]){
    const page=journeyHarness();change(page);await page.startSingleCaseProbe({cycleId:'cycle-a',caseId:'q02'})
    assert.deepEqual(page.calls.confirms,[]);assert.deepEqual(page.calls.probe,[])
  }
})

test('cancelled confirmation and changed context cannot send or replace the current task',async()=>{
  const cancelled=journeyHarness();cancelled.ElMessageBox.confirm=async()=>{throw new Error('cancel')}
  await cancelled.startSingleCaseProbe({cycleId:'cycle-a',caseId:'q02'});assert.deepEqual(cancelled.calls.probe,[])
  for(const change of [p=>p.epoch(),p=>p.destroy(),p=>p.step.value=2,p=>p.taskId.value='other-task',p=>p.cycle.value.baseline={cases:[{...cases[1],question:'Changed original question'}]}]){
    const page=journeyHarness(),decision=deferred();page.ElMessageBox.confirm=()=>decision.promise
    const pending=page.startSingleCaseProbe({cycleId:'cycle-a',caseId:'q02'});change(page);decision.resolve();await pending
    assert.deepEqual(page.calls.probe,[])
  }
})

test('foreign or missing acknowledgements are not accepted and late responses do not cross scope',async()=>{
  for(const result of [null,{}, {id:'old-task',status:'running'},{id:'new',status:'completed'},{id:'new',status:'running',kind:'run'},{id:'new',status:'running',cycle_id:'cycle-b'}]){
    const page=journeyHarness();page.window.pywebview.api.start_research_probe=async()=>result
    await page.startSingleCaseProbe({cycleId:'cycle-a',caseId:'q02'})
    assert.equal(page.taskId.value,'old-task');assert.equal(page.recoveryTask.value.id,'old-failed-retest')
    assert.match(page.error.value,/尚未确认/)
  }
  const page=journeyHarness(),response=deferred();page.window.pywebview.api.start_research_probe=()=>response.promise
  const pending=page.startSingleCaseProbe({cycleId:'cycle-a',caseId:'q02'});await Promise.resolve();await Promise.resolve()
  page.epoch();page.taskId.value='different-task';response.resolve({id:'new-probe',status:'running'});await pending
  assert.equal(page.taskId.value,'different-task')
})

test('completed probe polling refreshes this cycle without replacing baseline or adding a whole retest',async()=>{
  const page=journeyHarness();await page.startSingleCaseProbe({cycleId:'cycle-a',caseId:'q02'})
  page.window.pywebview.api.get_task=async()=>({id:'new-probe-task',kind:'probe',status:'completed',result:{probe:probe()}})
  await page.poll()
  assert.deepEqual(page.calls.applied,[{keepEditor:true}]);assert.equal(page.cycle.value.probes[0].source,'native_collector')
  assert.equal(page.cycle.value.baseline.cases.length,3);assert.deepEqual(page.cycle.value.retests,[])
})

test('actual compiled UI shows old-to-new presence, research import, raw business text and unknown citations safely',async()=>{
  const example=cycle({probes:[probe({after:side({response:'官网 example.org 展示产品与技术服务。<script>unsafe()</script>',website_presence:true})})]})
  const markup=await renderComponent(example)
  assert.match(markup,/研究结果导入/);assert.match(markup,/未出现 → 出现/)
  assert.match(markup,/产品与技术服务/);assert.match(markup,/官网引用：未知/)
  assert.match(markup,/原题未提供官网地址/);assert.match(markup,/不代表业务描述正确/)
  assert.doesNotMatch(markup,/<script>/);assert.match(markup,/&lt;script&gt;/)
  for(const style of component.descriptor.styles)assert.deepEqual(compileStyle({source:style.content,filename:component.filename.pathname,id:'single-probe-render'}).errors,[])
  assert.match(journey.descriptor.template.content,/名称提及率/)
  assert.match(journey.descriptor.template.content,/<GeoSingleCaseProbe/)
  assert.match(journey.expression('primaryAction'),/startNative\('retest'/)
})

test('actual external UI shows the negative business review, original quote and unknown conditions without a false before-after claim',async()=>{
  const markup=await renderComponent(cycle({facts:[fact],probes:[externalProbe()]}))
  assert.match(markup,/用户外部反馈/);assert.match(markup,/关联 q02 · 外部反馈/)
  assert.match(markup,/官网地址出现，但公开业务资料未被识别（助手核对）/)
  assert.match(markup,/实际提问未提供，不能判定为严格同题对照/)
  assert.match(markup,/用户提供原文/);assert.match(markup,/界面未知/);assert.match(markup,/模型未知/)
  assert.match(markup,/交付能力与案例：未知/);assert.match(markup,/官网引用：未知/)
  assert.match(markup,new RegExp(rejectedQuote))
  assert.doesNotMatch(markup,/未出现 → 出现|沿用同一原题|已取得有效答案|原题未提供官网地址/)
})
