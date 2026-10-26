import assert from 'node:assert/strict'
import fs from 'node:fs'
import test from 'node:test'
import * as Vue from 'vue'
import { renderToString } from '@vue/server-renderer'
import { parse,compileScript,compileTemplate,compileStyle } from '@vue/compiler-sfc'
import * as helpers from '../src/utils/geoSourceMaintenance.js'
import { journeyDate } from '../src/utils/geoJourney.js'

const hash='a'.repeat(64),name='示例协作科技有限公司'
const fact={id:'fact-web',text:'示例公司官网及协作业务。',confirmed:true,source_url:'https://example.org/about'}
const record=(changes={})=>({id:'maintenance-web',cycle_id:'cycle-a',company_name:name,module:'website',module_label:'企业官网',
  proposal:'公司官网为 https://example.org/ 。',proposal_sha256:hash,stage:'draft',message:'',read_only:false,can_edit:true,can_open:true,
  receipt:null,updated_at:'2026-10-27T10:00:00+08:00',native_task_id:null,...changes})
const overview=(changes={})=>({supported:true,reason:'',platform:'qcc',entry_url:helpers.qccMaintenanceEntry,company_name:name,context_sha256:hash,
  modules:[{key:'website',label:'企业官网',proposal:'公司官网为 https://example.org/ 。',max_length:500,reference_fact_ids:['fact-web']},
    {key:'introduction',label:'企业简介',proposal:'示例公司提供团队协作服务。',max_length:500,reference_fact_ids:['fact-web']}],tasks:[],...changes})
const cycle=(changes={})=>({id:'cycle-a',baseline:{profile:{entity_type:'company',name,website:'https://example.org/'}},facts:[fact],source_maintenance:overview(),...changes})
const filename=new URL('../src/components/GeoSourceMaintenance.vue',import.meta.url)
const {descriptor,errors}=parse(fs.readFileSync(filename,'utf8'),{filename:filename.pathname})
assert.deepEqual(errors,[])
const script=compileScript(descriptor,{id:'source-maintenance-test'})
const journeyFilename=new URL('../src/views/GeoJourney.vue',import.meta.url)
const journeySource=fs.readFileSync(journeyFilename,'utf8'),journeyDescriptor=parse(journeySource).descriptor
const journeyScript=compileScript(journeyDescriptor,{id:'source-maintenance-journey-test'})
const journeyExpression=name=>{
  const declaration=journeyScript.scriptSetupAst.flatMap(node=>node.type==='VariableDeclaration'?node.declarations:[]).find(node=>node.id.name===name)
  assert.ok(declaration,name)
  return journeyDescriptor.scriptSetup.content.slice(declaration.init.start,declaration.init.end)
}
const names=script.scriptSetupAst.flatMap(node=>node.type==='VariableDeclaration'?node.declarations:[]).map(node=>node.id.name)
let body=descriptor.scriptSetup.content
for(const node of script.scriptSetupAst.filter(node=>node.type==='ImportDeclaration').reverse())body=body.slice(0,node.start)+body.slice(node.end)
const deferred=()=>{let resolve;const promise=new Promise(done=>resolve=done);return {promise,resolve}}
const setup=(props,api,native={},emit=()=>{})=>{
  const lifecycle={mounted:[],unmounted:[]},timers=[]
  const deps={computed:Vue.computed,ref:Vue.ref,watch:Vue.watch,onMounted:fn=>lifecycle.mounted.push(fn),onUnmounted:fn=>lifecycle.unmounted.push(fn),
    defineProps:()=>props,defineEmits:()=>emit,geoSourceMaintenanceApi:api,window:{pywebview:{api:native}},journeyDate,...helpers,
    setInterval:fn=>{timers.push(fn);return timers.length},clearInterval:()=>{}}
  const instance=new Function(...Object.keys(deps),body+`;return {${names.join(',')}};`)(...Object.values(deps))
  instance.syncProposal()
  return {...helpers,...instance,lifecycle,timers}
}
const harness=(value=cycle(),overrides={})=>{
  const props=Vue.reactive({cycle:value,busy:false,nativeAvailable:true}),calls={list:[],save:[],open:[],check:[],emitted:[]}
  let server=value.source_maintenance
  const api={list:async id=>{calls.list.push(id);return {data:server}},save:async(id,payload)=>{calls.save.push([id,payload]);server={...server,tasks:[record({module:payload.module,proposal:payload.proposal})]};return {data:server.tasks[0]}},...overrides.api}
  const native={get_task:async()=>({}),open_source_maintenance:async(...args)=>{calls.open.push(args);return {id:'native-open',status:'running'}},
    check_source_maintenance_session:async(...args)=>{calls.check.push(args);return {id:'native-check',status:'running'}},...overrides.native}
  const instance=setup(props,api,native,(...args)=>calls.emitted.push(args));calls.emitted=[]
  return {props,calls,api,native,setServer:value=>{server=value},...instance}
}
const render=async(value,{module='website',nativeAvailable=true}={})=>{
  const template=compileTemplate({source:descriptor.template.content,filename:filename.pathname,id:'source-maintenance-render',compilerOptions:{bindingMetadata:script.bindings}})
  assert.deepEqual(template.errors,[])
  const code=template.code.replace(/import \{([^}]+)\} from "vue"/g,(_all,imports)=>`const {${imports.split(',').map(item=>{const [name,alias]=item.trim().split(/\s+as\s+/);return alias?`${name}:${alias}`:name}).join(',')}}=Vue;`).replace('export function render','return function render')
  const compiled=new Function('Vue',code)(Vue)
  const app=Vue.createSSRApp({props:['cycle','busy','nativeAvailable'],setup:props=>{const instance=setup(props,{list:async()=>({data:value.source_maintenance}),save:async()=>({})},{open_source_maintenance:()=>{},check_source_maintenance_session:()=>{},get_task:()=>{}});instance.selectModule(module);return instance},render:compiled},{cycle:value,busy:false,nativeAvailable})
  for(const [tag,htmlTag] of [['el-button','button'],['el-tag','span'],['el-input','textarea']])app.component(tag,{props:['disabled','modelValue','loading'],setup:(props,{slots})=>()=>Vue.h(htmlTag,{disabled:props.disabled},slots.default?.()||props.modelValue)})
  return renderToString(app)
}

test('company and cycle identity are mandatory; product maintenance stays explicitly not applicable',async()=>{
  const product=cycle({baseline:{profile:{entity_type:'product',name:'示例协作产品'}}}),page=harness(product)
  assert.equal(page.view.value.supported,false)
  await page.refresh();await page.saveProposal();await page.openWindow(record())
  assert.deepEqual(page.calls.list,[]);assert.deepEqual(page.calls.save,[]);assert.deepEqual(page.calls.open,[])
  const html=await render(product)
  assert.match(html,/适用于公司档案/);assert.doesNotMatch(html,/source-maintenance-save|source-maintenance-open-/)
  for(const change of [{platform:'other'},{company_name:'别的公司'},{context_sha256:'unknown'}])assert.equal(helpers.sourceMaintenanceOverview(cycle(),overview(change)).supported,false)
})

test('modules and tasks remain isolated by company and module while prior-cycle originals are visible',()=>{
  const value=overview({tasks:[record(),record({id:'foreign',cycle_id:'cycle-b'}),record({id:'other-company',company_name:'另一公司'}),record({id:'bad-module',module:'article'})]})
  assert.deepEqual(helpers.sourceMaintenanceOverview(cycle(),value).tasks.map(item=>item.id),['maintenance-web','foreign'])
  assert.equal(helpers.sourceMaintenanceCanOpen(value.tasks[1],cycle()),false)
  const duplicates=overview({tasks:[record(),record()],modules:[...overview().modules,overview().modules[0]]})
  assert.deepEqual(helpers.sourceMaintenanceOverview(cycle(),duplicates).tasks,[])
  assert.equal(helpers.sourceMaintenanceOverview(cycle(),duplicates).modules.some(item=>item.key==='website'),false)
})

test('save payload contains only reviewed public text and confirmed-fact bindings',()=>{
  const value=cycle(),payload=helpers.sourceMaintenancePayload(value,value.source_maintenance,'introduction','经核对：示例公司提供协作服务。')
  assert.deepEqual(payload,{module:'introduction',proposal:'经核对：示例公司提供协作服务。',expected_context_sha256:hash,reference_fact_ids:['fact-web']})
  assert.doesNotMatch(JSON.stringify(payload),/contact|phone|upload|entry_url|javascript|personal|file_path/)
  for(const ids of [[],['missing'],['fact-web','fact-web']]){
    const source=overview();source.modules[1].reference_fact_ids=ids
    assert.throws(()=>helpers.sourceMaintenancePayload(value,source,'introduction','公开简介。'))
  }
  const source=overview({tasks:[record()]})
  assert.equal(helpers.sourceMaintenancePayload(value,source,'website','已核对的官网说明。').task_id,'maintenance-web')
  assert.throws(()=>helpers.sourceMaintenancePayload(value,source,'website','😀'.repeat(501)),/500/)
})

test('every non-draft and ambiguous state fails closed for edits and new applications',()=>{
  for(const stage of ['opening','awaiting_login','awaiting_human_verification','needs_manual_input','confirmation_ready','submitted_pending_review','submission_unknown','needs_manual_review','fee_required_stopped','window_closed','browser_rejected','unknown']){
    const original=record({stage,read_only:false,can_edit:true})
    assert.equal(helpers.sourceMaintenanceEditable(original),false,stage)
    assert.throws(()=>helpers.sourceMaintenancePayload(cycle(),overview({tasks:[original]}),'website','新说明。'),/原申请/,stage)
  }
  assert.equal(helpers.sourceMaintenanceCanOpen(record({stage:'unknown'}),cycle()),false)
  assert.equal(helpers.sourceMaintenanceCanOpen(record({stage:'browser_rejected'}),cycle()),false)
  assert.equal(helpers.sourceMaintenanceReadOnly(record({read_only:undefined})),true)
  assert.throws(()=>helpers.sourceMaintenancePayload(cycle(),overview({tasks:[record(),record({id:'second'})]}),'website','新说明。'),/原申请/)
})

test('restart loads persisted progress without creating or opening anything',async()=>{
  const pending=record({stage:'submitted_pending_review',read_only:true,can_edit:false,native_task_id:'native-old'})
  const page=harness(cycle({source_maintenance:overview({tasks:[pending]})}))
  await page.refresh()
  assert.equal(page.moduleTasks.value[0].stage,'submitted_pending_review');assert.equal(page.canSave.value,false)
  assert.deepEqual(page.calls.list,['cycle-a']);assert.deepEqual(page.calls.save,[]);assert.deepEqual(page.calls.open,[])
  page.selectModule('introduction');assert.equal(page.moduleTasks.value.length,0);assert.equal(page.canSave.value,true)
})

test('saving refreshes original scope, preserves edited text and blocks duplicate clicks',async()=>{
  const wait=deferred(),page=harness(cycle(),{api:{save:async(id,payload)=>{page.calls.save.push([id,payload]);return wait.promise}}})
  page.proposal.value='本人已核对的公司公开官网说明。'
  const first=page.saveProposal();await Promise.resolve();await page.saveProposal()
  assert.equal(page.calls.save.length,1);assert.equal(page.calls.save[0][1].proposal,'本人已核对的公司公开官网说明。')
  assert.deepEqual(page.calls.emitted,[['busy',true]])
  wait.resolve({data:record()});await first
  assert.deepEqual(page.calls.emitted,[['busy',true],['busy',false]])
  page.props.busy=true;await page.saveProposal();assert.equal(page.calls.save.length,1)
})

test('fresh submitted state prevents a stale draft save and an uncertain write needs manual refresh',async()=>{
  const page=harness(cycle());page.setServer(overview({tasks:[record({stage:'submission_unknown',read_only:true,can_edit:false})]}))
  await page.saveProposal();assert.equal(page.calls.save.length,0);assert.match(page.error.value,/原申请/)
  const failing=harness(cycle(),{api:{save:async()=>{throw new Error('连接结束，保存回执未知。')}}})
  await failing.saveProposal();assert.equal(failing.saveUncertain.value,true);assert.equal(failing.canSave.value,false)
  await failing.saveProposal();assert.equal(failing.saveUncertain.value,true)
  await failing.refresh();assert.equal(failing.saveUncertain.value,false)
})

test('ordinary open and check pass only exact IDs, and submitted state opens only the original record',async()=>{
  const pending=record({stage:'submitted_pending_review',read_only:true,can_edit:false,native_task_id:'native-old'}),page=harness(cycle({source_maintenance:overview({tasks:[pending]})}))
  await page.openWindow(pending)
  page.setServer(overview({tasks:[{...pending,window_open:true}]}));await page.refresh();await page.checkWindow(page.moduleTasks.value[0])
  assert.deepEqual(page.calls.open,[['cycle-a','maintenance-web']]);assert.deepEqual(page.calls.check,[['native-old']]);assert.deepEqual(page.calls.save,[])
  const html=await render(page.props.cycle)
  assert.match(html,/只读核对原申请/);assert.match(html,/待平台审核/);assert.doesNotMatch(html,/source-maintenance-save/)
  assert.deepEqual(page.calls.emitted.filter(item=>item[0]==='task').map(item=>item[1].source_task_id),['maintenance-web','maintenance-web'])
})

test('fresh window permissions and global busy block stale or duplicate browser actions',async()=>{
  const initial=record(),wait=deferred(),page=harness(cycle({source_maintenance:overview({tasks:[initial]})}),{native:{open_source_maintenance:async(...args)=>{page.calls.open.push(args);return wait.promise}}})
  const first=page.openWindow(initial);await Promise.resolve();await page.openWindow(initial)
  assert.equal(page.calls.open.length,1)
  wait.resolve({id:'native-once',status:'running'});await first
  page.props.busy=true;await page.openWindow(initial);assert.equal(page.calls.open.length,1)
  page.props.busy=false;page.setServer(overview({tasks:[record({can_open:false})]}));await page.openWindow(initial)
  assert.equal(page.calls.open.length,1);assert.match(page.error.value,/状态已变化/)
})

test('an open official window locks other journey work while its own checks remain available',async()=>{
  const active=record({stage:'waiting_verification',window_open:true,read_only:true,can_edit:false,native_task_id:'native-existing'})
  const page=harness(cycle({source_maintenance:overview({tasks:[active]})}))
  assert.equal(page.sessionBusy.value,true);assert.equal(page.canOpen(active),false);assert.equal(page.canCheck(active),true);assert.equal(page.canSave.value,false)
  await page.checkWindow(active)
  assert.deepEqual(page.calls.check,[['native-existing']]);assert.equal(page.calls.open.length,0)
  assert.equal(page.calls.emitted.some(item=>item[0]==='busy'&&item[1]===false),false)
  page.setServer(overview({tasks:[{...active,stage:'window_closed',window_open:false}]}));await page.refresh()
  assert.equal(page.sessionBusy.value,false);assert.deepEqual(page.calls.emitted.at(-1),['busy',false])
  const html=await render(cycle({source_maintenance:overview({tasks:[active]})}))
  assert.match(html,/拖动滑块完成拼图/);assert.match(html,/先正常关闭企查查窗口/);assert.match(html,/检查当前窗口/)
})

test('a native ACK before window_open still polls the pending original until its check becomes available',async()=>{
  const original=record({stage:'submitted_pending_review',read_only:true,can_edit:false,window_open:false})
  const page=harness(cycle({source_maintenance:overview({tasks:[original]})}))
  await page.openWindow(original)
  assert.equal(page.calls.open.length,1)
  // The parent has the running ACK while the first saved row still says closed.
  page.props.nativeTaskActive=true
  assert.equal(page.canOpen(original),false)
  const delayed={...original,native_task_id:'native-open',window_open:false}
  page.setServer(overview({tasks:[delayed]}))
  const reads=page.calls.list.length
  await page.poll()
  assert.equal(page.calls.list.length,reads+1)
  assert.equal(page.canCheck(page.moduleTasks.value[0]),false)
  // A later local read sees the same pending application with its window ready.
  page.setServer(overview({tasks:[{...delayed,window_open:true}]}))
  await page.poll()
  assert.equal(page.calls.list.length,reads+2)
  assert.equal(page.canCheck(page.moduleTasks.value[0]),true)
  page.native.check_source_maintenance_session=async id=>{page.calls.check.push([id]);return {id,status:'running'}}
  await page.checkWindow(page.moduleTasks.value[0])
  assert.deepEqual(page.calls.check,[['native-open']])
  assert.equal(page.calls.open.length,1)
  page.props.nativeTaskActive=false
  page.setServer(overview({tasks:[delayed]}))
  await page.poll()
  assert.equal(page.sessionBusy.value,false)
  assert.equal(page.moduleTasks.value[0].stage,'submitted_pending_review')
})

test('research receipt imports are explicit and a closed imported record has no live-window check',async()=>{
  const imported=record({stage:'submitted_pending_review',read_only:true,can_edit:false,source:'research_assisted_browser',software_native_task:false,window_open:false,
    native_task_id:'old-native',receipt:{source_receipt_sha256:hash}}),page=harness(cycle({source_maintenance:overview({tasks:[imported]})}))
  assert.equal(page.canSave.value,false);assert.equal(page.canCheck(imported),false)
  const html=await render(page.props.cycle)
  assert.match(html,/研究流程回执导入（非软件原生提交）/);assert.match(html,/脱敏回执已保留/)
  assert.doesNotMatch(html,/source-maintenance-check-website|source-maintenance-save/)
})

test('native acknowledgements enter the normal persisted journey task without replacing drafts or recovery',()=>{
  const calls=[],deps={cycleId:Vue.ref('cycle-a'),cycle:Vue.ref(cycle()),task:Vue.ref({id:'previous'}),taskId:Vue.ref('previous'),taskKind:Vue.ref('draft'),persist:()=>calls.push('persist')}
  const adopt=new Function(...Object.keys(deps),`let liveTaskId='',handledTask='previous';return (${journeyExpression('adoptSourceMaintenanceTask')});`)(...Object.values(deps))
  const valid={id:'native-source',status:'running',kind:'source_maintenance',cycle_id:'cycle-a',source_task_id:'maintenance-web'}
  for(const invalid of [{...valid,cycle_id:'other'}, {...valid,id:'file:///unsafe'}, {...valid,status:'completed'}, {...valid,kind:'draft'}])adopt(invalid)
  assert.equal(deps.taskId.value,'previous');assert.deepEqual(calls,[])
  adopt(valid);assert.equal(deps.taskId.value,'native-source');assert.equal(deps.taskKind.value,'source_maintenance')
  assert.match(deps.task.value.message,/企查查/);assert.deepEqual(calls,['persist'])
  deps.cycle.value=cycle({baseline:{profile:{name:'示例产品',entity_type:'product'}}});adopt({...valid,id:'other-native'})
  assert.equal(deps.taskId.value,'native-source')
})

test('source-task completion refreshes maintenance progress while preserving the current editor',async()=>{
  const updated=cycle({source_maintenance:overview({tasks:[record({stage:'submitted_pending_review',read_only:true,can_edit:false})]})}),applied=[],gets=[]
  const deps={nativeAvailable:Vue.ref(true),taskId:Vue.ref('native-source'),taskKind:Vue.ref('source_maintenance'),task:Vue.ref({status:'running'}),
    finishingTask:Vue.ref(false),cycleId:Vue.ref('cycle-a'),error:Vue.ref(''),replacementSourceId:Vue.ref(null),recoveryTask:Vue.ref(null),
    window:{pywebview:{api:{get_task:async()=>({id:'native-source',kind:'source_maintenance',cycle_id:'cycle-a',status:'completed',result:{source_maintenance:updated.source_maintenance.tasks[0]}})}}},
    scopedRecoveryCheckpoint:()=>null,geoContentApi:{get:async id=>{gets.push(id);return {data:updated}}},applyCycle:(...args)=>applied.push(args),persist:()=>{}}
  const poll=new Function(...Object.keys(deps),`let destroyed=false,contextEpoch=0,pollPending=false,handledTask='';return (${journeyExpression('poll')});`)(...Object.values(deps))
  await poll();assert.deepEqual(gets,['cycle-a']);assert.equal(applied[0][0],updated);assert.deepEqual(applied[0][1],{keepEditor:true});assert.equal(deps.error.value,'')
  assert.match(journeyDescriptor.template.content,/task.kind==='source_maintenance' \? '企查查资料补正进行中'/)
})

test('a delayed previous-company result cannot be saved into the current company context',async()=>{
  const wait=deferred(),page=harness(cycle(),{api:{list:()=>wait.promise}})
  page.proposal.value='已核对的公开说明。'
  const pending=page.saveProposal()
  page.props.cycle=cycle({id:'another-cycle',baseline:{profile:{entity_type:'company',name:'另一示例公司'}}})
  wait.resolve({data:overview()});await pending;await Vue.nextTick()
  assert.equal(page.calls.save.length,0);assert.equal(page.view.value.supported,false)
})

test('late reads and writes cannot replace another company cycle or open its browser',async()=>{
  const wait=deferred(),page=harness(cycle({source_maintenance:overview({tasks:[record()]})}),{api:{list:()=>wait.promise}})
  const opening=page.openWindow(record());page.props.cycle=cycle({id:'cycle-b'})
  wait.resolve({data:overview({tasks:[record()]})});await opening;await Vue.nextTick()
  assert.equal(page.calls.open.length,0);assert.equal(page.props.cycle.id,'cycle-b');assert.equal(page.canSave.value,false)
  assert.equal(page.moduleTasks.value[0].cycle_id,'cycle-a');assert.equal(page.canOpen(page.moduleTasks.value[0]),false)
})

test('same-company prior-cycle tasks remain visible and cannot become a new application',async()=>{
  const previous=record({cycle_id:'prior-cycle'}),page=harness(cycle({source_maintenance:overview({tasks:[previous]})}))
  assert.equal(page.moduleTasks.value.length,1);assert.equal(page.canSave.value,false);assert.equal(page.canOpen(previous),false);assert.equal(page.canCheck(previous),false)
  await page.saveProposal();await page.openWindow(previous);assert.equal(page.calls.save.length,0);assert.equal(page.calls.open.length,0)
  const html=await render(page.props.cycle)
  assert.match(html,/另一轮实验已有此模块的原任务/);assert.doesNotMatch(html,/source-maintenance-save|source-maintenance-open-/)
})

test('an edited proposal keeps its original fact context until the user reloads and reviews it',async()=>{
  const page=harness(cycle());page.proposal.value='原来已经核对的公开说明。'
  page.setServer(overview({context_sha256:'b'.repeat(64)}));await page.saveProposal()
  assert.equal(page.calls.save.length,0);assert.match(page.error.value,/已确认资料有变化/);assert.equal(page.contextIssue.value,true)
  assert.equal(page.proposal.value,'原来已经核对的公开说明。');assert.equal(page.canSave.value,false)
  page.syncProposal();await page.refresh();assert.equal(page.contextIssue.value,false);assert.equal(page.canSave.value,true)
})

test('login before any content write is not falsely described as a read-only application',async()=>{
  const login=record({stage:'awaiting_login',read_only:false,can_edit:false}),html=await render(cycle({source_maintenance:overview({tasks:[login]})}))
  assert.equal(helpers.sourceMaintenanceEditable(login),false);assert.equal(helpers.sourceMaintenanceReadOnly(login),false)
  assert.match(html,/打开官方窗口并辅助填写/);assert.doesNotMatch(html,/重新打开只核对原进度|source-maintenance-save/)
})

test('concrete human steps, rejection and unknown outcomes never become success claims',async()=>{
  for(const [state,expected] of [['awaiting_login',/正常登录/],['awaiting_human_verification',/安全验证|平台验证/],['needs_manual_input',/姓名.*联系方式.*官方浏览器/],['confirmation_ready',/软件不会点击最终提交/],['submission_unknown',/不要重复提交/],['fee_required_stopped',/不会付款/],['browser_rejected',/日常浏览器.*手动/]])assert.match(helpers.sourceMaintenanceStage(record({stage:state})).hint,expected,state)
  const unknown=cycle({source_maintenance:overview({tasks:[record({stage:'submission_unknown',read_only:true,can_edit:false})]})}),html=await render(unknown)
  assert.match(html,/提交结果待核对/);assert.match(html,/未完成不计为 GEO 优化成功/);assert.match(html,/费用和材料要求以当前官方提示为准/)
  assert.doesNotMatch(html,/保证.*免费|保证.*收录|GEO成功|自动最终提交|上传文件|type="file"/)
})

test('the editor escapes public text and the only external action link is the fixed official entry',async()=>{
  const source=overview({tasks:[record({stage:'confirmation_ready',read_only:true,can_edit:false,proposal:'<img src=x onerror=alert(1)>公开说明'})]})
  const html=await render(cycle({source_maintenance:source}))
  assert.match(html,/&lt;img/);assert.doesNotMatch(html,/<img/)
  assert.deepEqual([...html.matchAll(/href="([^"]+)"/g)].map(item=>item[1]),[helpers.qccMaintenanceEntry])
  assert.doesNotMatch(descriptor.template.content,/v-html|type="file"|v-model=.*contact|v-model=.*phone/)
})

test('the source-maintenance component compiles and stays separate from article publication',()=>{
  const template=compileTemplate({source:descriptor.template.content,filename:filename.pathname,id:'source-maintenance-test'})
  assert.deepEqual(template.errors,[])
  for(const style of descriptor.styles)assert.deepEqual(compileStyle({source:style.content,filename:filename.pathname,id:'source-maintenance-test',scoped:true}).errors,[])
  const journey=fs.readFileSync(new URL('../src/views/GeoJourney.vue',import.meta.url),'utf8')
  assert.equal((journey.match(/<GeoSourceMaintenance /g)||[]).length,2)
  const publication=journey.slice(journey.indexOf('key="publish"'),journey.indexOf('key="feedback"'))
  assert.doesNotMatch(publication,/<GeoSourceMaintenance/)
})
