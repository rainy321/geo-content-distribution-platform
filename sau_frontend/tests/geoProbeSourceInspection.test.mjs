import assert from 'node:assert/strict'
import fs from 'node:fs'
import {createHash} from 'node:crypto'
import test from 'node:test'
import * as Vue from 'vue'
import {renderToString} from '@vue/server-renderer'
import {parse,compileScript,compileTemplate} from '@vue/compiler-sfc'
import * as inspection from '../src/utils/geoProbeSourceInspection.js'
import * as sessions from '../src/utils/geoSessionScope.js'
import {journeyDate} from '../src/utils/geoJourney.js'
import {publicationReviewUrl} from '../src/utils/geoPublicationReview.js'

const sha=text=>createHash('sha256').update(text).digest('hex')
const cycleId='a'.repeat(32),probeId='b'.repeat(32),taskId='c'.repeat(32),rawSha='d'.repeat(64),planSha='e'.repeat(64)
const response='公开工商资料提到示例组织，但仍未核验该产品的官方入口。'
const expected={plan_sha256:planSha,revision:2,response_sha256:sha(response),raw_result_sha256:rawSha}
const probe=()=>({id:probeId,cycle_id:cycleId,source:'native_collector',native_verified:true,case_id:'q02',
  selected_count:1,category:'brand_identity',question:'示例产品是什么，官方入口在哪里？',plan_sha256:planSha,
  execution:{session_slot:'secondary',client_mode:'consumer'},conditions:{account_independence_verified:false},
  evidence:{result_sha256:rawSha},before:{response:'旧观察'},after:{response,answer_evaluable:true,
    client_mode:'consumer',model_label:'快速',session_slot:'secondary',collected_at:'2026-11-11T09:00:00+08:00',
    official_source_present:null,official_citation_present:null,collection_status:'partial',citations:[{title:'旧引用未解析',url:null}],displayed_sources:[]},
  source_inspection:{available:true,expected:{...expected},session_slot:'secondary',client_mode:'consumer',question_sent:false}})
const cycle=()=>({id:cycleId,revision:2,project_id:5,profile:{website:'https://example.org'},
  test_plan:{plan_sha256:planSha},baseline:{cases:[{id:'q02',question:probe().question}]},probes:[probe()],facts:[]})
const later=()=>({kind:'later_read_only_source_view',integrity_passed:true,new_answer_collected:false,question_sent:false,
  source_collection_complete:true,
  inspection_result_sha256:'f'.repeat(64),bound_original_raw_sha256:rawSha,original_answer_timestamp:'2026-11-11T09:00:00+08:00',
  inspected_at:'2026-11-11T12:00:00+08:00',source_assessment:{official_source_present:true,official_citation_present:null},
  displayed_sources:[{title:'后来看到的公开产品页',url:'https://example.org/product/'}],citations:[{title:'引用网址仍未知',url:null}],
  address_evidence:[{url:'https://example.org/product/',in_sources:true,in_citations:null}]})
const load=path=>{
  const filename=new URL(path,import.meta.url),{descriptor,errors}=parse(fs.readFileSync(filename,'utf8'))
  assert.deepEqual(errors,[])
  const script=compileScript(descriptor,{id:path}),declarations=script.scriptSetupAst.flatMap(n=>n.type==='VariableDeclaration'?n.declarations:[])
  return {filename,descriptor,script,names:declarations.map(n=>n.id.name),expression:name=>{const value=declarations.find(n=>n.id.name===name);assert.ok(value,name);return descriptor.scriptSetup.content.slice(value.init.start,value.init.end)}}
}
const component=load('../src/components/GeoSingleCaseProbe.vue'),journey=load('../src/views/GeoJourney.vue')
const setup=(props,emit=()=>{})=>{
  const deps={...inspection,...sessions,computed:Vue.computed,ref:Vue.ref,watch:Vue.watch,journeyDate,publicationReviewUrl,defineProps:()=>props,defineEmits:()=>emit}
  return new Function(...Object.keys(deps),component.descriptor.scriptSetup.content.replace(/^import .*$/gm,'')+`;return {${component.names.join(',')}}`)(...Object.values(deps))
}
const render=async value=>{
  const template=compileTemplate({source:component.descriptor.template.content,filename:component.filename.pathname,id:'probe-source-view',compilerOptions:{bindingMetadata:component.script.bindings}})
  assert.deepEqual(template.errors,[])
  const code=template.code.replace(/import \{([^}]+)\} from "vue"/g,(_all,list)=>`const {${list.split(',').map(v=>v.trim().replace(/\s+as\s+/,':')).join(',')}}=Vue;`).replace('export function render','return function render')
  const app=Vue.createSSRApp({props:['cycle','busy','canStart','canInspect'],setup:props=>setup(props),render:new Function('Vue',code)(Vue)},
    {cycle:value,busy:false,canStart:true,canInspect:true})
  for(const [name,tag] of [['el-button','button'],['el-select','select'],['el-option','option'],['el-tag','span']])app.component(name,{props:['disabled','label'],setup:(p,{slots})=>()=>Vue.h(tag,{disabled:p.disabled},slots.default?.()||p.label)})
  return renderToString(app)
}
const harness=()=>{
  const calls=[],confirm=[]
  const deps={...inspection,computed:Vue.computed,cycle:Vue.ref(cycle()),cycleId:Vue.ref(cycleId),step:Vue.ref(4),
    task:Vue.ref({status:'idle'}),taskId:Vue.ref('old-task'),taskKind:Vue.ref('probe'),working:Vue.ref(false),error:Vue.ref(''),
    unconfirmedNativeTask:Vue.ref(null),nativeAvailable:Vue.ref(true),persist:()=>{},
    ElMessageBox:{confirm:async(...args)=>confirm.push(args)}}
  deps.nativeBusy=Vue.computed(()=>deps.task.value.status==='running'||deps.unconfirmedNativeTask.value?.status==='running')
  deps.busy=Vue.computed(()=>deps.working.value||deps.nativeBusy.value)
  deps.window={pywebview:{api:{start_probe_source_inspection:async(...args)=>{calls.push(args);return {id:taskId,status:'running',kind:'probe_source_inspection',target_id:probeId,cycle_id:cycleId,session_slot:'secondary',requested_client_mode:'consumer'}}}}}
  const names=['run','probeSourceInspectionAvailable','startProbeSourceInspection']
  const functions=new Function(...Object.keys(deps),`let destroyed=false,contextEpoch=0,liveTaskId='',handledTask='';${names.map(name=>`const ${name}=${journey.expression(name)};`).join('\n')}return {${names.join(',')}}`)(...Object.values(deps))
  return {...deps,...functions,calls,confirm}
}
test('only complete native partial observations with exact four frozen hashes can expose source read',async()=>{
  const current=cycle(),item=current.probes[0]
  assert.deepEqual((await inspection.freezeProbeInspection(current,item.id)).expected,expected)
  for(const change of [{source:'research_prototype_import'},{native_verified:false},{cycle_id:'9'.repeat(32)},
    {after:{...item.after,answer_evaluable:false}},{evidence:{result_sha256:'0'.repeat(64)}},
    {source_inspection:{...item.source_inspection,expected:{...expected,url:'https://bad.example'}}}]){
    assert.equal(inspection.probeInspectionContext(current,{...item,...change}),null)
  }
  current.probes[0].after.response='Changed answer'
  await assert.rejects(()=>inspection.freezeProbeInspection(current,item.id),/冻结凭据/)
})
test('cancel and changed scope during confirmation never call native bridge',async()=>{
  for(const action of ['cancel','revision','response','raw']){
    const page=harness()
    page.ElMessageBox.confirm=async()=>{if(action==='cancel')throw Error('cancel');if(action==='revision')page.cycle.value.revision++;if(action==='response')page.cycle.value.probes[0].after.response='changed';if(action==='raw')page.cycle.value.probes[0].evidence.result_sha256='0'.repeat(64)}
    await page.startProbeSourceInspection({cycleId,probeId})
    assert.equal(page.calls.length,0,action)
    assert.equal(page.taskId.value,'old-task',action)
  }
})
test('source-only request uses original secondary slot, four expectations, strict target ACK and remains serial busy',async()=>{
  const page=harness()
  await page.startProbeSourceInspection({cycleId,probeId})
  assert.deepEqual(page.calls,[[cycleId,probeId,expected]])
  assert.equal(page.taskKind.value,'probe_source_inspection');assert.equal(page.task.value.session_slot,'secondary')
  assert.match(page.confirm[0][0],/不会重新提问.*不计为新的复测/)
  await page.startProbeSourceInspection({cycleId,probeId});assert.equal(page.calls.length,1)
  for(const change of [{session_slot:'primary'},{target_id:'9'.repeat(32)},{cycle_id:'8'.repeat(32)},{kind:'probe'}]){
    const bad=harness(),original=bad.window.pywebview.api.start_probe_source_inspection
    bad.window.pywebview.api.start_probe_source_inspection=async(...args)=>({...await original(...args),...change})
    await bad.startProbeSourceInspection({cycleId,probeId})
    assert.equal(bad.taskId.value,'old-task');assert.equal(bad.unconfirmedNativeTask.value.status,'running')
    assert.match(bad.error.value,/回执未确认/)
  }
})
test('historical button emits only probe identity and is unavailable for imported or busy rows',()=>{
  const props=Vue.reactive({cycle:cycle(),busy:false,canStart:true,canInspect:true}),events=[],page=setup(props,(...args)=>events.push(args))
  page.requestInspection(props.cycle.probes[0]);assert.deepEqual(events,[['inspect-sources',{cycleId,probeId}]])
  props.busy=true;page.requestInspection(props.cycle.probes[0]);assert.equal(events.length,1)
  props.busy=false;props.cycle.probes[0].native_verified=false;page.requestInspection(props.cycle.probes[0]);assert.equal(events.length,1)
})
test('compiled UI keeps original answer and unknown original sources; later evidence and times are independent',async()=>{
  const current=cycle(),item=current.probes[0],before=JSON.stringify(item.after)
  item.source_reinspection=later();delete item.source_inspection
  const html=await render(current)
  assert.match(html,/后来补采的来源/);assert.match(html,/补采时间/);assert.match(html,/原回答时间/)
  assert.match(html,/官网来源：未知/);assert.match(html,/后采官网来源：已观察到/)
  assert.match(html,/后采官网引用：未知/);assert.match(html,/引用网址仍未知/);assert.match(html,/后采地址核对/)
  assert.match(html,/账号是否独立未核验/);assert.doesNotMatch(html,/仅补采这条回答的来源/)
  assert.equal(JSON.stringify(item.after),before)
  for(const change of [{new_answer_collected:true},{integrity_passed:false},{bound_original_raw_sha256:'0'.repeat(64)},
    {original_answer_timestamp:'2025-01-01'},{inspected_at:'2025-01-01'}])assert.equal(inspection.sourceReinspectionView({...item,source_reinspection:{...later(),...change}}),null)
})
test('bound partial inspection remains explicitly incomplete and offers only a manual same-answer read',async()=>{
  const current=cycle(),item=current.probes[0]
  item.source_reinspection={...later(),source_collection_complete:false,collection_status:'partial',source_assessment:{official_source_present:true,official_citation_present:null}}
  const html=await render(current)
  assert.match(html,/原回答绑定：已核对/);assert.match(html,/尚未完整解析，未解析部分仍未知/)
  assert.match(html,/手动复查这条回答的未完整来源/);assert.match(html,/不会自动重复/)
  assert.ok(inspection.probeInspectionContext(current,item))
})
test('missing bridge disables capability and no source read is inferred from a normal probe method',async()=>{
  const page=harness();delete page.window.pywebview.api.start_probe_source_inspection
  page.window.pywebview.api.start_research_probe=()=>assert.fail('must not send question')
  assert.equal(page.probeSourceInspectionAvailable.value,false)
  await page.startProbeSourceInspection({cycleId,probeId});assert.equal(page.calls.length,0)
})
