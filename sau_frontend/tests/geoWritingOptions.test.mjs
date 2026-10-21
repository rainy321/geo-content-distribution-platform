import assert from 'node:assert/strict'
import fs from 'node:fs'
import test from 'node:test'
import { parse, compileScript } from '@vue/compiler-sfc'
import { ref, watch } from 'vue'
import { baselineWritingOptions, createWritingPreferences } from '../src/utils/geoWritingOptions.js'

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

const harness = api => {
  const names=['applyWritingOptions','rememberWritingOptions','selectWritingMode','selectWritingModel','cycleScope','sameCycleScope','applyCycle','loadCycle','options','generate','login','poll']
  const make=new Function('ref','watch','createWritingPreferences','geoContentApi',`
    const cycleId=ref(''),cycle=ref(null),clientMode=ref('consumer'),model=ref(''),factsText=ref(''),confirmed=ref(false),briefId=ref(''),platform=ref('zhihu'),publicationVisible=ref(false),working=ref(false),task=ref({status:'idle'}),loginScope=ref(null),cycles=ref([]),nativeAvailable=ref(true);
    const writingPreferences=createWritingPreferences();
    const factsStoredText=facts=>facts.map(f=>f.text).join('');
    let cycleEpoch=0,cycleLoadRequest=0,loginRequest=0,pollRequest=0,handledTask;
    const ElMessage={success(){},error(){}};
    const reload=async()=>{};
    let window={pywebview:{api:{}}};
    ${names.map(name=>`const ${name}=${expression(name)};`).join('\n')}
    const stopMode=${source.slice(modeWatcher.start,modeWatcher.end).replace(/;$/,'')};
    const stopLogin=${source.slice(loginWatcher.start,loginWatcher.end).replace(/;$/,'')};
    const stop=()=>{stopMode();stopLogin()};
    return {cycleId,cycle,clientMode,model,task,loginScope,working,cycleScope,sameCycleScope,applyCycle,loadCycle,selectWritingMode,selectWritingModel,generate,login,poll,stop,
      native:value=>{window.pywebview.api=value}};
  `)
  return make(ref,watch,createWritingPreferences,api)
}
const deferred = () => {let resolve;const promise=new Promise(done=>{resolve=done});return {promise,resolve}}

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
