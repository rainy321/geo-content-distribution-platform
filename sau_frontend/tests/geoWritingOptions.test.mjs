import assert from 'node:assert/strict'
import fs from 'node:fs'
import test from 'node:test'
import { parse, compileScript } from '@vue/compiler-sfc'
import { computed, ref, watch } from 'vue'
import { baselineWritingOptions, createWritingPreferences } from '../src/utils/geoWritingOptions.js'
import { doubaoTaskRecovery, scopedRecoveryCheckpoint } from '../src/utils/geoTaskRecovery.js'

const storage = () => {
  const values = new Map()
  return {getItem:key=>values.get(key) || null,setItem:(key,value)=>values.set(key,value)}
}
const cycle = (id, mode='work', model='') => ({id,baseline:{execution:{client_mode:mode,requested_model_label:model}},
  facts:[],analysis:{briefs:[{id:`${id}-brief`}]},channels:{zhihu:'知乎'}})

test('writing defaults use the baseline request without turning observed model labels into a request',()=>{
  assert.deepEqual(baselineWritingOptions(cycle('a','work','  模型 A  ')),{client_mode:'work',model_label:'模型 A'})
  assert.deepEqual(baselineWritingOptions({baseline:{execution:{client_mode:'work',requested_model_label:null,model_label:'Observed A'},cases:[{environment:{model_label:'Observed B'}}]}}),{client_mode:'work',model_label:''})
  assert.deepEqual(baselineWritingOptions({baseline:{execution:{client_mode:'unknown',requested_model_label:{label:'unsafe'}}}}),{client_mode:'consumer',model_label:''})
  assert.deepEqual(baselineWritingOptions(cycle('a','unknown','Work-only model')),{client_mode:'consumer',model_label:''})
  assert.deepEqual(baselineWritingOptions(null),{client_mode:'consumer',model_label:''})
})

test('explicit per-cycle choices survive refresh and reopening without leaking into other cycles',()=>{
  const local=storage(),preferences=createWritingPreferences(local)
  assert.deepEqual(preferences.get('a',cycle('a','work','Model A')),{client_mode:'work',model_label:'Model A'})
  assert.equal(preferences.set('a',{client_mode:'consumer',model_label:''}),true)
  assert.deepEqual(preferences.get('a',cycle('a','work','New baseline model')),{client_mode:'consumer',model_label:''})
  assert.deepEqual(preferences.get('b',cycle('b','work','Model B')),{client_mode:'work',model_label:'Model B'})
  assert.deepEqual(createWritingPreferences(local).get('a',cycle('a','work','Model A')),{client_mode:'consumer',model_label:''})
  const copied=preferences.get('a',cycle('a'))
  copied.client_mode='work'
  assert.equal(preferences.get('a',cycle('a')).client_mode,'consumer')
})

test('invalid or unavailable storage preserves safe defaults and current explicit choices',()=>{
  const unavailable={getItem(){throw Error('denied')},setItem(){throw Error('full')}}
  const preferences=createWritingPreferences(unavailable)
  assert.equal(preferences.get('a',cycle('a')).client_mode,'work')
  assert.equal(preferences.set('a',{client_mode:'work',model_label:'manual'}),true)
  assert.equal(preferences.get('a',cycle('a')).model_label,'manual')
  assert.equal(preferences.set('a',{client_mode:'unexpected',model_label:'manual'}),false)
  assert.equal(preferences.set('',{client_mode:'work',model_label:'manual'}),false)
  for(const stored of ['broken JSON',JSON.stringify({client_mode:'unsafe',model_label:'x'}),JSON.stringify({client_mode:'work',model_label:{}})]) {
    assert.deepEqual(createWritingPreferences({getItem:()=>stored}).get('a',cycle('a','consumer','Good')),{client_mode:'consumer',model_label:'Good'})
  }
})

const filename=new URL('../src/views/GeoContentLoop.vue',import.meta.url)
const {descriptor,errors}=parse(fs.readFileSync(filename,'utf8'),{filename:filename.pathname})
assert.deepEqual(errors,[])
const script=compileScript(descriptor,{id:'content-writing-options-test'})
const source=descriptor.scriptSetup.content
const declaration=name=>script.scriptSetupAst.flatMap(node=>node.type==='VariableDeclaration'?node.declarations:[]).find(node=>node.id.name===name)
const expression=name=>{const node=declaration(name);assert.ok(node,name);return source.slice(node.init.start,node.init.end)}
const modeWatcher=script.scriptSetupAst.find(node=>node.type==='ExpressionStatement'&&node.expression?.callee?.name==='watch'&&
  node.expression.arguments[0]?.type==='ArrowFunctionExpression'&&source.slice(node.expression.arguments[0].start,node.expression.arguments[0].end)==='()=>cycleId.value')
assert.ok(modeWatcher)
const loginWatcher=script.scriptSetupAst.find(node=>node.type==='ExpressionStatement'&&node.expression?.callee?.name==='watch'&&node.expression.arguments[0]?.name==='cycleId')
assert.ok(loginWatcher)

const harness = (api,local=storage()) => {
  const names=['applyWritingOptions','rememberWritingOptions','selectWritingMode','selectWritingModel','cycleScope','sameCycleScope','recoveryStorageKey','loadContentRecovery','storeContentRecovery','rememberContentRecovery','contentRecovery','applyCycle','loadCycle','options','generate','login','poll','retest']
  const make=new Function('ref','computed','watch','createWritingPreferences','geoContentApi','doubaoTaskRecovery','scopedRecoveryCheckpoint','local',`
    const cycleId=ref(''),cycle=ref(null),clientMode=ref('consumer'),model=ref(''),factsText=ref(''),confirmed=ref(false),factsConflict=ref(''),briefId=ref(''),platform=ref('zhihu'),publicationVisible=ref(false),working=ref(false),task=ref({status:'idle'}),loginScope=ref(null),cycles=ref([]),nativeAvailable=ref(true);
    const recoveryTask=ref(null),recoveryStorageError=ref(''),recoveryUnreadable=ref(false),recoveryMemory=new Map();
    const busy=computed(()=>working.value||task.value.status==='running');
    const writingPreferences=createWritingPreferences();
    const factsStoredText=facts=>facts.map(f=>f.text).join('');
    let cycleEpoch=0,cycleLoadRequest=0,loginRequest=0,pollRequest=0,handledTask,destroyed=false;
    const messages=[],ElMessage={success(){},error:value=>messages.push(value)};
    const ElMessageBox={confirm:async()=>{}};
    const reload=async()=>{};
    let window={localStorage:local,pywebview:{api:{}}};
    ${names.map(name=>`const ${name}=${expression(name)};`).join('\n')}
    const stopMode=${source.slice(modeWatcher.start,modeWatcher.end).replace(/;$/,'')};
    const stopLogin=${source.slice(loginWatcher.start,loginWatcher.end).replace(/;$/,'')};
    const stop=()=>{destroyed=true;cycleEpoch++;stopMode();stopLogin()};
    return {cycleId,cycle,clientMode,model,task,loginScope,working,factsText,confirmed,factsConflict,recoveryTask,recoveryStorageError,recoveryUnreadable,contentRecovery,messages,ElMessageBox,cycleScope,sameCycleScope,applyCycle,loadCycle,selectWritingMode,selectWritingModel,generate,login,poll,retest,stop,
      native:value=>{window.pywebview.api=value}};
  `)
  return make(ref,computed,watch,createWritingPreferences,api,doubaoTaskRecovery,scopedRecoveryCheckpoint,local)
}
const deferred = () => {let resolve;const promise=new Promise(done=>{resolve=done});return {promise,resolve}}
const interrupted=(id='failed-a',cycle_id='a')=>({id,kind:'draft',cycle_id,status:'need_action',message:'private account text',
  recovery:{failure_reason:'login_required',question_submission_attempted:null,question_submitted:null,
    requested_client_mode:'work',raw:'private evidence'}})

test('the actual content flow initializes WORK plus requested model and preserves explicit changes on refresh',()=>{
  const page=harness({})
  page.cycleId.value='a';page.applyCycle(cycle('a','work','Model A'))
  assert.equal(page.clientMode.value,'work');assert.equal(page.model.value,'Model A')
  page.selectWritingModel('Manual A')
  page.applyCycle(cycle('a','work','Changed default'))
  assert.equal(page.model.value,'Manual A')
  page.selectWritingMode('consumer')
  assert.equal(page.model.value,'')
  page.cycleId.value='b'
  assert.equal(page.cycle.value,null)
  page.applyCycle(cycle('b','work','Model B'))
  assert.equal(page.clientMode.value,'work');assert.equal(page.model.value,'Model B')
  page.cycleId.value='a';page.applyCycle(cycle('a','work','Model A'))
  assert.equal(page.clientMode.value,'consumer');assert.equal(page.model.value,'')
  page.stop()
})

test('out-of-order cycle responses, including A to B to A, cannot restore old data or overwrite pending user choices',async()=>{
  const requests=[]
  const page=harness({get:id=>{const request=deferred();requests.push({id,...request});return request.promise}})
  page.cycleId.value='a';const oldA=page.loadCycle()
  page.cycleId.value='b';const b=page.loadCycle()
  page.cycleId.value='a';const newA=page.loadCycle()
  page.selectWritingMode('consumer');page.selectWritingModel('Explicit while loading')
  requests[2].resolve({data:cycle('a','work','Current A')});await newA
  requests[0].resolve({data:cycle('a','work','Stale A')});await oldA
  requests[1].resolve({data:cycle('b','work','Stale B')});await b
  assert.equal(page.cycleId.value,'a')
  assert.equal(page.cycle.value.baseline.execution.requested_model_label,'Current A')
  assert.equal(page.clientMode.value,'consumer');assert.equal(page.model.value,'Explicit while loading')
  page.stop()
})

test('an old native generation response cannot replace the current cycle task',async()=>{
  const request=deferred(),page=harness({})
  page.native({generate_research:()=>request.promise})
  page.cycleId.value='a';page.applyCycle(cycle('a'))
  const generation=page.generate('draft','doubao')
  page.cycleId.value='b';page.task.value={id:'task-for-b',kind:'login',status:'running'}
  request.resolve({id:'old-generation',kind:'draft',cycle_id:'a',status:'running'});await generation
  assert.equal(page.cycleId.value,'b');assert.equal(page.task.value.id,'task-for-b')
  page.stop()
})

test('an old login response cannot replace the current task after switching away and back',async()=>{
  const request=deferred(),page=harness({})
  page.native({open_login:()=>request.promise})
  page.cycleId.value='a';page.applyCycle(cycle('a'));const login=page.login()
  page.cycleId.value='b';page.cycleId.value='a';page.task.value={id:'newer-task',status:'running'}
  request.resolve({id:'old-login',kind:'login',status:'running'});await login
  assert.equal(page.task.value.id,'newer-task');assert.equal(page.loginScope.value,null)
  page.stop()
})

test('login cannot open a default profile while the selected cycle is still loading',async()=>{
  let calls=0
  const page=harness({})
  page.native({open_login:async()=>{calls++;return {id:'login',kind:'login',status:'running'}}})
  page.cycleId.value='a'
  await page.login()
  assert.equal(calls,0)
  page.applyCycle(cycle('a','work','Requested'))
  await page.login()
  assert.equal(calls,1)
  page.stop()
})

test('a pending task poll cannot replace a newer task returned by a user action',async()=>{
  const request=deferred(),page=harness({})
  page.native({get_task:()=>request.promise})
  page.task.value={id:'old-task',status:'completed'}
  const poll=page.poll()
  page.task.value={id:'new-task',status:'running'}
  request.resolve({id:'old-task',status:'completed'});await poll
  assert.equal(page.task.value.id,'new-task');assert.equal(page.task.value.status,'running')
  page.stop()
})

test('task polling keeps the newest observation when response order changes',async()=>{
  const requests=[],page=harness({})
  page.native({get_task:()=>{const request=deferred();requests.push(request);return request.promise}})
  page.task.value={id:'task-a',status:'running'}
  const first=page.poll(),second=page.poll()
  requests[1].resolve({id:'task-b',status:'running'});await second
  requests[0].resolve({id:'task-a',status:'running'});await first
  assert.equal(page.task.value.id,'task-b')
  page.stop()
})

test('platform saves and refreshes preserve unsaved facts in the same cycle',()=>{
  const page=harness({}),value={...cycle('a'),facts:[{text:'已保存的事实'}]}
  page.cycleId.value='a';page.applyCycle(value)
  page.factsText.value='尚未保存的新资料';page.confirmed.value=false
  page.applyCycle({...value,destinations:[{id:'updated-platform'}]})
  assert.equal(page.factsText.value,'尚未保存的新资料');assert.equal(page.confirmed.value,false)
  assert.equal(page.factsConflict.value,'')
  page.stop()
})

test('a concurrent server fact update retains input and requires a fresh review',()=>{
  const page=harness({}),value={...cycle('a'),facts:[{text:'原资料'}]}
  page.cycleId.value='a';page.applyCycle(value)
  page.factsText.value='我的未保存资料';page.confirmed.value=true
  const server={...value,facts:[{text:'其他页面的新资料'}]}
  page.applyCycle(server)
  assert.equal(page.factsText.value,'我的未保存资料');assert.equal(page.confirmed.value,false)
  assert.match(page.factsConflict.value,/输入未丢失/)
  page.applyCycle({...server,facts:[{text:'我的未保存资料'}]},{factsSaved:true})
  assert.equal(page.factsText.value,'我的未保存资料');assert.equal(page.confirmed.value,true)
  assert.equal(page.factsConflict.value,'')
  page.stop()
})

test('the detail page cancellation keeps the failed task and sends no uncertain repeat',async()=>{
  const page=harness({});page.cycleId.value='a';page.applyCycle(cycle('a'))
  page.task.value={id:'failed',kind:'draft',cycle_id:'a',status:'need_action',recovery:{failure_reason:'login_required',question_submitted:true}}
  let calls=0;page.native({generate_research:async()=>{calls++;return {id:'new',status:'running'}}})
  page.ElMessageBox.confirm=async()=>{throw 'cancel'}
  await page.generate('draft','doubao')
  assert.equal(calls,0);assert.equal(page.task.value.id,'failed');assert.equal(page.working.value,false)
  page.stop()
})

test('rapid detail-page generation clicks start only one native request',async()=>{
  const page=harness({}),response=deferred();page.cycleId.value='a';page.applyCycle(cycle('a'))
  let calls=0;page.native({generate_research:()=>{calls++;return response.promise}})
  const first=page.generate('draft','doubao');await page.generate('draft','doubao')
  assert.equal(calls,1);response.resolve({id:'new-draft',status:'running'});await first
  assert.equal(page.task.value.id,'new-draft')
  page.stop()
})

test('failed content keeps its typed warning across login, cancellation and reopening without touching unsaved facts',async()=>{
  const local=storage(),page=harness({},local);page.cycleId.value='a';page.applyCycle(cycle('a'))
  page.factsText.value='我的未保存资料';page.confirmed.value=false;page.task.value=interrupted()
  let generated=0;page.native({open_login:async()=>({id:'separate-login',kind:'login',status:'completed'}),
    generate_research:async()=>{generated++;return {id:'new-draft',status:'running'}}})
  await page.login()
  assert.equal(page.task.value.id,'separate-login');assert.equal(page.contentRecovery.value.taskId,'failed-a')
  assert.equal(page.contentRecovery.value.submission,'unknown')
  const saved=local.getItem('geo-content-recovery:v1:a');assert.doesNotMatch(saved,/private|raw|account|evidence/)
  page.ElMessageBox.confirm=async()=>{throw 'cancel'}
  await page.generate('draft','doubao')
  assert.equal(generated,0);assert.equal(page.task.value.id,'separate-login')
  assert.equal(page.contentRecovery.value.taskId,'failed-a');assert.deepEqual(page.messages,[])
  assert.equal(page.factsText.value,'我的未保存资料');assert.equal(page.confirmed.value,false)
  page.stop()
  const reopened=harness({},local);reopened.cycleId.value='a';reopened.applyCycle(cycle('a'))
  assert.equal(reopened.contentRecovery.value.taskId,'failed-a');assert.equal(reopened.contentRecovery.value.requiresConfirmation,true)
  reopened.stop()
})

test('content recovery is cleared only after a confirmed new native task, never an error or missing acknowledgement',async()=>{
  const local=storage(),page=harness({},local);page.cycleId.value='a';page.applyCycle(cycle('a'));page.task.value=interrupted()
  page.native({open_login:async()=>({id:'separate-login',kind:'login',status:'completed'}),generate_research:async()=>({error:'启动失败'})})
  await page.login();await page.generate('draft','doubao')
  assert.equal(page.contentRecovery.value.taskId,'failed-a')
  page.native({generate_research:async()=>({})});await page.generate('draft','doubao')
  assert.equal(page.contentRecovery.value.taskId,'failed-a')
  page.native({generate_research:async()=>({id:'confirmed-new-draft',kind:'draft',cycle_id:'a',status:'running'})})
  await page.generate('draft','doubao');assert.equal(page.contentRecovery.value,null)
  assert.deepEqual(JSON.parse(local.getItem('geo-content-recovery:v1:a')),{version:1,checkpoint:null})
  page.stop()
  const reopened=harness({},local);reopened.cycleId.value='a';assert.equal(reopened.contentRecovery.value,null);reopened.stop()
})

test('per-cycle checkpoints do not leak and A to B to A rejects a late poll before it can replace a warning',async()=>{
  const local=storage(),page=harness({},local);page.cycleId.value='a';page.applyCycle(cycle('a'));page.task.value=interrupted()
  page.native({open_login:async()=>({id:'login-a',kind:'login',status:'completed'})});await page.login()
  page.cycleId.value='b';page.applyCycle(cycle('b'));assert.equal(page.contentRecovery.value,null)
  page.task.value=interrupted('failed-b','b');page.native({open_login:async()=>({id:'login-b',kind:'login',status:'completed'})});await page.login()
  page.cycleId.value='a';page.applyCycle(cycle('a'));assert.equal(page.contentRecovery.value.taskId,'failed-a')
  const response=deferred();page.native({get_task:()=>response.promise});const pending=page.poll()
  page.cycleId.value='b';assert.equal(page.contentRecovery.value.taskId,'failed-b')
  page.cycleId.value='a';response.resolve(interrupted('late-old-a'));await pending
  assert.equal(page.contentRecovery.value.taskId,'failed-a');assert.equal(page.task.value.id,'login-b')
  assert.equal(JSON.parse(local.getItem('geo-content-recovery:v1:a')).checkpoint.id,'failed-a')
  page.stop()
})

test('unavailable or malformed checkpoint storage never claims persistence or proves an unsent request',async()=>{
  for(const local of [{getItem(){throw Error('denied')},setItem(){throw Error('denied')}},
    {getItem:()=>'{broken',setItem(){throw Error('full')}},
    {getItem:()=>JSON.stringify({version:1,checkpoint:interrupted('foreign','b')}),setItem(){}}]){
    const page=harness({},local);page.cycleId.value='a';page.applyCycle(cycle('a'))
    assert.equal(page.recoveryUnreadable.value,true);assert.match(page.recoveryStorageError.value,/未能读取/)
    let sent=0;page.native({generate_research:async()=>{sent++;return {id:'must-not-send'}}})
    page.ElMessageBox.confirm=async()=>{throw 'cancel'};await page.generate('draft','doubao')
    assert.equal(sent,0);assert.equal(page.contentRecovery.value,null);page.stop()
  }
  const page=harness({},{getItem:()=>null,setItem(){throw Error('full')}})
  page.cycleId.value='a';page.applyCycle(cycle('a'));page.task.value=interrupted()
  page.native({open_login:async()=>({id:'separate-login',kind:'login',status:'completed'})});await page.login()
  assert.equal(page.contentRecovery.value.taskId,'failed-a');assert.match(page.recoveryStorageError.value,/未能保存/)
  page.cycleId.value='b';page.cycleId.value='a';assert.equal(page.contentRecovery.value.taskId,'failed-a')
  assert.match(page.recoveryStorageError.value,/未能保存/);page.stop()
})

test('rapid login clicks preserve the warning and open only one separate login task',async()=>{
  const response=deferred(),page=harness({});page.cycleId.value='a';page.applyCycle(cycle('a'));page.task.value=interrupted()
  let opened=0;page.native({open_login:()=>{opened++;return response.promise}})
  const first=page.login();await page.login();assert.equal(opened,1)
  response.resolve({id:'separate-login',kind:'login',status:'completed'});await first
  assert.equal(page.contentRecovery.value.taskId,'failed-a');page.stop()
})

test('unmounting during a poll prevents late checkpoint and task replacement',async()=>{
  const local=storage(),response=deferred(),page=harness({},local)
  page.cycleId.value='a';page.applyCycle(cycle('a'));page.task.value={id:'initial',status:'running'}
  page.native({get_task:()=>response.promise});const pending=page.poll();page.stop()
  response.resolve(interrupted());await pending
  assert.equal(page.task.value.id,'initial');assert.equal(local.getItem('geo-content-recovery:v1:a'),null)
})

test('confirmed retests explain repeated submission, latch duplicate clicks and clear the old warning only on acknowledgement',async()=>{
  const response=deferred(),page=harness({});page.cycleId.value='a';page.applyCycle(cycle('a'));page.task.value=interrupted()
  let started=0,confirmation='';page.native({start_research_retest:()=>{started++;return response.promise}})
  page.ElMessageBox.confirm=async text=>{confirmation=text}
  const first=page.retest();await Promise.resolve();await page.retest()
  assert.equal(started,1);assert.match(confirmation,/发送尚未确认|可能重复提问/);assert.match(confirmation,/会再次提问/)
  assert.equal(page.contentRecovery.value.taskId,'failed-a')
  response.resolve({id:'new-retest',kind:'retest',cycle_id:'a',status:'running'});await first
  assert.equal(page.contentRecovery.value,null);assert.equal(page.task.value.id,'new-retest');page.stop()
})
