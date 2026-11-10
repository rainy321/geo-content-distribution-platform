import assert from 'node:assert/strict'
import fs from 'node:fs'
import test from 'node:test'
import {ref,watch,nextTick} from 'vue'
import {parse,compileScript} from '@vue/compiler-sfc'
import * as automatic from '../src/utils/geoAutomaticCollection.js'
import * as sessions from '../src/utils/geoSessionScope.js'
import {baselineWritingOptions} from '../src/utils/geoWritingOptions.js'
import {sameBrand,profileLines,restoreEntityDraftFields,articleSnapshot} from '../src/utils/geoJourney.js'
import {scopedRecoveryCheckpoint} from '../src/utils/geoTaskRecovery.js'
import {savedCycleContinuation,savedCycleFactEdits} from '../src/utils/geoSavedCycles.js'

const file=new URL('../src/views/GeoJourney.vue',import.meta.url)
const {descriptor}=parse(fs.readFileSync(file,'utf8'))
const compiled=compileScript(descriptor,{id:'doubao-profile-scope'})
const declarations=compiled.scriptSetupAst.flatMap(node=>node.type==='VariableDeclaration'?node.declarations:[])
const expression=name=>{const node=declarations.find(item=>item.id.name===name);assert.ok(node,name);return descriptor.scriptSetup.content.slice(node.init.start,node.init.end)}
const action=(name,scope)=>new Function('scope',`with(scope){return (${expression(name)})}`)(scope)
const owner={id:7,name:'合成服务',website:'https://example.org/service',entity_type:'product',description:'公开信息'}
const native=()=>({id:'native-a',project_id:7,report_ref:'report-a',profile:owner,drafts:[],analysis:{briefs:[]},channels:{},facts:[],
  baseline:{profile:owner,execution:{client_mode:'work',profile_mode:'consumer',mode_policy:'available_web',requested_model_label:''},cases:[{id:'q01',client_mode:'work'}]}})

function harness(){
  const calls=[],saved=[]
  const api={plan_automatic:async(...args)=>calls.push(['plan',...args]),start_automatic_run:async(...args)=>calls.push(['run',...args]),
    open_login:async options=>calls.push(['login',options]),start_run:async(...args)=>calls.push(['explicit',...args])}
  const scope={...automatic,...sessions,accountSessionSlot:ref('primary'),singleProbeSelection:ref(null),probeSelectionVersion:ref(0),error:ref(''),baselineWritingOptions,savedCycleContinuation,savedCycleFactEdits,sameBrand,
    window:{pywebview:{api}},ElMessageBox:{confirm:async()=>{}},project:ref(owner),projectId:ref(7),profileDirty:ref(false),editorDirty:ref(false),
    formProfile:()=>owner,provider:ref('doubao'),nativeAvailable:ref(true),busy:ref(false),model:ref(''),clientMode:ref('consumer'),
    automaticProfileMode:ref('consumer'),automaticProfileModeExplicit:ref(false),collectionPath:ref('automatic'),
    restoring:false,run:work=>work(),startNative:async(_kind,work)=>work(),syncCycleRoute:async()=>{},persist:()=>saved.push({mode:scope.automaticProfileMode.value,actual:scope.clientMode.value}),
    cycle:ref(null),cycleId:ref(''),report:ref(null),reportId:ref(''),historyReportId:ref(''),reportOrigin:ref('history'),reports:ref([]),
    plan:ref(null),planConfirmed:ref(false),replacementSourceId:ref(null),briefId:ref(''),platform:ref(''),articleId:ref(null),
    factRows:ref([]),recoveryDraft:ref(null),recoveryFacts:ref(null),recoveryTask:ref(null),taskId:ref(''),taskKind:ref(''),step:ref(1)}
  scope.applyCycle=value=>{scope.cycle.value=value;scope.cycleId.value=value.id}
  for(const name of ['selectSessionMode','selectCollectionMode','selectAccountSession','options','retestSessionOptions','continueSavedCycle','resetBaseline','makePlan','collectBaseline','login','useHistory','resetRecords'])scope[name]=action(name,scope)
  return {...scope,calls,saved,scope}
}

test('actual work history keeps its consumer profile through continue, login, new questions and a new automatic run',async()=>{
  const page=harness(),record=native(),prior=JSON.stringify(record)
  await page.continueSavedCycle(record)
  assert.equal(page.clientMode.value,'work')
  assert.equal(page.automaticProfileMode.value,'consumer')
  assert.deepEqual(page.retestSessionOptions(),{client_mode:'work',model_label:'',profile_mode:'consumer',mode_policy:'available_web'})
  page.step.value=2;await page.login()
  assert.deepEqual(page.calls.pop(),['login',{client_mode:'work',model_label:'',profile_mode:'consumer',mode_policy:'available_web'}])
  await page.resetBaseline(false)
  assert.equal(page.cycle.value,null);assert.equal(page.clientMode.value,'work');assert.equal(page.automaticProfileMode.value,'consumer')
  page.step.value=1;await page.login();await page.makePlan()
  page.plan.value={profile:owner,cases:[{id:'synthetic-question'}]};await page.collectBaseline()
  assert.deepEqual(page.calls[0],['login',{client_mode:'consumer',model_label:''}])
  assert.deepEqual(page.calls[1].at(-1),{profile_mode:'consumer',model_label:''})
  assert.deepEqual(page.calls[2].at(-1),{profile_mode:'consumer',model_label:''})
  assert.equal(JSON.stringify(record),prior)
})

test('explicit automatic profile choice survives history and repeat while explicit actual-mode selection stays separate',async()=>{
  const page=harness();page.selectCollectionMode('work')
  assert.equal(page.clientMode.value,'consumer');assert.equal(page.automaticProfileModeExplicit.value,true)
  await page.continueSavedCycle(native());assert.equal(page.automaticProfileMode.value,'work');assert.equal(page.clientMode.value,'work')
  page.plan.value={cases:[{id:'original'}]};await page.resetBaseline(true);await page.collectBaseline()
  assert.equal(page.calls.at(-1).at(-1).profile_mode,'work')
  page.collectionPath.value='explicit';page.selectCollectionMode('consumer');await page.collectBaseline()
  assert.equal(page.calls.at(-1)[0],'explicit');assert.equal(page.calls.at(-1).at(-1).client_mode,'consumer')
  assert.equal(page.automaticProfileMode.value,'work')
  page.resetRecords();assert.equal(page.automaticProfileMode.value,'consumer');assert.equal(page.automaticProfileModeExplicit.value,false)
})

test('old storage recovers the recorded profile ahead of actual work; new explicit storage and older explicit runs remain compatible',()=>{
  assert.deepEqual(automatic.restoreAutomaticProfileChoice({clientMode:'work'},native()),{mode:'consumer',explicit:false})
  assert.deepEqual(automatic.restoreAutomaticProfileChoice({automaticProfileMode:'work',automaticProfileModeExplicit:true,clientMode:'consumer'},native()),{mode:'work',explicit:true})
  assert.deepEqual(automatic.restoreAutomaticProfileChoice({clientMode:'work'}),{mode:'work',explicit:false})
  assert.equal(automatic.automaticProfileForRecord({execution:{client_mode:'work'}}),'work')
  assert.equal(automatic.automaticProfileForRecord({execution:{client_mode:'work',mode_policy:'available_web'}}),'consumer')
  assert.deepEqual(automatic.restoreAutomaticProfileChoice({automaticProfileMode:'invalid',clientMode:'invalid'}),{mode:'consumer',explicit:false})
})

test('actual project restore migrates old state and real persistence reopens the separate explicit profile choice',async()=>{
  const page=harness(),record=native(),stored=new Map([[7,{clientMode:'work',cycleId:record.id,reportId:record.report_ref,step:1}]])
  const scope=page.scope
  Object.assign(scope,{projects:ref([owner]),projectChoice:ref(7),form:{...owner},saveError:ref(''),error:ref(''),activeDraft:ref(null),editor:{title:'',content:'',tags:[]},
    profileLines,restoreEntityDraftFields,articleSnapshot,scopedRecoveryCheckpoint,entityType:value=>value.entity_type,
    readJourney:id=>stored.get(id)||{},writeJourney:(id,value)=>stored.set(id,JSON.parse(JSON.stringify(value))),
    loadReports:async()=>{scope.reports.value=[{id:record.report_ref,report:record.baseline}]},
    geoContentApi:{get:async id=>{assert.equal(id,record.id);return {data:record}}},
    canRestoreFactEdits:()=>false,canVisit:()=>true,restoreTaskRecovery:async()=>{},directoryTarget:ref(null),directoryReady:ref(true)})
  scope.persist=action('persist',scope)
  const choose=action('chooseProject',scope)
  scope.restoring=true // Prevent saving a simulated pre-mount page over its old stored snapshot.
  await choose(7)
  assert.equal(page.clientMode.value,'work');assert.equal(page.automaticProfileMode.value,'consumer')
  assert.equal(page.automaticProfileModeExplicit.value,false)
  page.selectCollectionMode('work');scope.persist()
  assert.equal(stored.get(7).automaticProfileMode,'work');assert.equal(stored.get(7).automaticProfileModeExplicit,true)
  assert.equal(stored.get(7).clientMode,'work')
  scope.restoring=true;page.automaticProfileMode.value='consumer';page.automaticProfileModeExplicit.value=false
  await choose(7)
  assert.equal(page.automaticProfileMode.value,'work');assert.equal(page.automaticProfileModeExplicit.value,true)
  assert.equal(page.clientMode.value,'work')
})

test('phone/external actual modes never become native profile defaults or native retest permissions',async()=>{
  const page=harness(),external={...native(),baseline_source:'user_reported_external',baseline:null,external_baseline:{kind:'geo_external_baseline_v1'},test_plan:{cases:[{id:'q01'}]},execution:{client_mode:'work',profile_mode:'work'}}
  assert.equal(automatic.automaticProfileForRecord(external),'consumer')
  assert.deepEqual(automatic.restoreAutomaticProfileChoice({},external),{mode:'consumer',explicit:false})
  page.cycle.value=external
  assert.throws(()=>page.retestSessionOptions(),/外部起始观察/)
  assert.equal(page.calls.length,0)
})

test('the actual selector displays and updates automatic scope without relabeling the observed work mode',()=>{
  const page=harness();page.clientMode.value='work'
  const component=action('ModelOptions',{...page.scope,defineComponent:value=>value,h:(type,props,children)=>({type,props:Array.isArray(props)?{}:props,children:children??(Array.isArray(props)?props:[])}),ElSelect:'select',ElOption:'option',ElInput:'input'})
  let select=component.setup()().children[0].children[1]
  assert.equal(select.props.modelValue,'consumer');select.props['onUpdate:modelValue']('work')
  assert.equal(page.automaticProfileMode.value,'work');assert.equal(page.clientMode.value,'work')
  page.collectionPath.value='explicit';select=component.setup()().children[0].children[1];select.props['onUpdate:modelValue']('consumer')
  assert.equal(page.clientMode.value,'consumer');assert.equal(page.automaticProfileMode.value,'work')
})

test('the actual Vue watcher revokes confirmed questions when only automatic profile changes with an empty model',async()=>{
  const page=harness()
  const node=compiled.scriptSetupAst.find(node=>node.type==='ExpressionStatement'&&node.expression?.callee?.name==='watch'&&
    node.expression.arguments[0]?.type==='ArrayExpression'&&node.expression.arguments[0].elements.some(item=>item.name==='collectionPath'))
  assert.ok(node)
  const watcher=descriptor.scriptSetup.content.slice(node.expression.start,node.expression.end)
  const stop=new Function('scope',`with(scope){return (${watcher})}`)({...page.scope,watch})
  try{
    page.planConfirmed.value=true
    page.selectCollectionMode('work')
    await nextTick()
    assert.equal(page.clientMode.value,'consumer');assert.equal(page.model.value,'')
    assert.equal(page.automaticProfileMode.value,'work');assert.equal(page.planConfirmed.value,false)
  }finally{stop()}
})
