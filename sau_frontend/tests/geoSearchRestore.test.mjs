import assert from 'node:assert/strict'
import fs from 'node:fs'
import test from 'node:test'
import { computed,effectScope,nextTick,ref,watch } from 'vue'
import { parse,compileScript,compileTemplate,compileStyle } from '@vue/compiler-sfc'
import * as identity from '../src/utils/geoJourney.js'
import * as restore from '../src/utils/geoSearchRestore.js'
import { doubaoTaskRecovery } from '../src/utils/geoTaskRecovery.js'

const profile=(suffix='a')=>({id:suffix==='a'?1:2,name:'同名软件',entity_type:'product',website:`https://tongyuye.com/products/${suffix}`})
const plan=(owner=profile())=>({schema_version:'1.0',platform:'doubao_web',profile:owner,plan_sha256:'a'.repeat(64),cases:[{id:'q01',category:'discovery',question:'测试问题'}]})
const report=(owner=profile(),run_id='run-a')=>({schema_version:'1.0',kind:'geo_search_test_report',profile:owner,run_id,status:'stopped',stop_reason:'session_redirected_to_logout',plan_sha256:'a'.repeat(64),execution:{client_mode:'work'},cohorts:{},cases:[{id:'q01',category:'discovery',question:'测试问题',status:'need_action',failure_reason:'session_redirected_to_logout',assessment:{},citations:[],displayed_sources:[]}]})
const task=(owner=profile(),overrides={})=>({id:'task-a',kind:'run',status:'completed',result:{report:report(owner)},...overrides})
const deferred=()=>{let resolve,reject;const promise=new Promise((done,fail)=>{resolve=done;reject=fail});return {promise,resolve,reject}}

test('same-name and same-domain records still require exact entity type and complete website identity',()=>{
  const current=profile(),value=task()
  assert.equal(restore.searchTaskMatchesProject(value,current),true)
  for(const owner of [profile('b'),{...current,entity_type:'company'},{...current,website:'https://tongyuye.com'},
    {...current,website:''}])assert.equal(restore.searchTaskMatchesProject(task(owner),current),false)
  assert.equal(restore.searchTaskMatchesProject({id:'failed',client_name:current.name,kind:'plan',status:'failed'},current),false)
  const binding={taskId:'failed',project:restore.searchProjectSnapshot(current)}
  assert.equal(restore.searchTaskMatchesProject({id:'failed',kind:'plan',status:'failed'},current,binding),true)
  assert.equal(restore.searchTaskMatchesProject(task(profile('b'),{id:'failed'}),current,binding),false)
  assert.equal(restore.searchTaskMatchesProject({id:'failed'}, {...current,id:8},binding),false)
})

test('saved null cannot hide a matching task report and an explicit report remains preferred',()=>{
  const current=profile(),selected=report(profile('b'),'chosen-history'),value=task()
  assert.equal(restore.restoredSearchRecords({saved:{report:null},project:current,task:value}).report,value.result.report)
  assert.equal(restore.restoredSearchRecords({saved:{report:null},project:current,task:value,currentReport:selected,explicitReport:true}).report,selected)
  assert.equal(restore.restoredSearchRecords({saved:{report:report(profile('b'))},project:current,task:task(profile('b')),currentReport:selected}).report,null)
  assert.equal(restore.restoredSearchRecords({saved:{plan:plan(profile('b'))},project:current}).plan,null)
})

test('a report-only context accepts only its explicit local task ID and unchanged full identity',()=>{
  const owner=profile(),binding={taskId:'task-a',project:null,profile:restore.searchProjectSnapshot(owner),intent:7}
  assert.equal(restore.searchTaskMatchesContext(task(),null,binding,7),true)
  assert.equal(restore.searchTaskMatchesContext(task(),null,null,7),false)
  assert.equal(restore.searchTaskMatchesContext(task(owner,{id:'global-latest'}),null,binding,7),false)
  assert.equal(restore.searchTaskMatchesContext(task(),null,binding,8),false)
  assert.equal(restore.searchTaskMatchesContext(task(profile('b')),null,binding,7),false)
  assert.equal(restore.searchTaskMatchesContext(task(),null,{...binding,profile:{...owner,website:''}},7),false)
})

test('completed native tasks preserve stopped report semantics without inventing submission evidence',()=>{
  const value=task(),source=restore.searchTaskRecoverySource(value),recovery=doubaoTaskRecovery(source)
  assert.equal(source.status,'stopped');assert.equal(value.status,'completed')
  assert.equal(recovery.reason,'session_redirected_to_logout');assert.equal(recovery.submission,'unknown')
  assert.equal(recovery.requiresConfirmation,true);assert.equal(recovery.clientMode,'work')
  assert.equal(restore.searchTaskRecoverySource({...value,status:'running'}).status,'running')
})

const filename=new URL('../src/views/GeoSearchTests.vue',import.meta.url)
const {descriptor,errors}=parse(fs.readFileSync(filename,'utf8'),{filename:filename.pathname})
assert.deepEqual(errors,[]);compileScript(descriptor,{id:'geo-search-restore'})
const body=descriptor.scriptSetup.content.replace(/^import .*$/gm,'')
function harness({saved={project_id:1,plan:plan(),report:null},currentTask=task(),api={},reports=[{id:'saved-a',run_id:'run-a'}],reportApi={},confirm=async()=>{}}={}) {
  const messages=[],unmount=[],scope=effectScope()
  const native={get_workspace_state:async()=>saved,get_task:async()=>currentTask,list_tasks:async()=>[currentTask],save_workspace_state:async()=>({saved:true}),...api}
  const journeyImports=Object.fromEntries(['entityLabel','entityProfileFields','profileLines','reportEntityContext','restoreEntityDraftFields','sameBrand'].map(name=>[name,identity[name]]))
  const dependencies={ref,computed,watch,nextTick,...journeyImports,...restore,doubaoTaskRecovery,
    window:{pywebview:{api:native}},useRoute:()=>({query:{desktop:'1'}}),useRouter:()=>({push(){}}),
    ElMessage:{error:value=>messages.push(value),info:value=>messages.push(value),success:value=>messages.push(value)},ElMessageBox:{confirm},
    geoTestApi:{listReports:async()=>({data:reports}),getReport:async()=>({data:report()}),...reportApi},
    projectApi:{updateProject:async(id,fields)=>({data:{...profile(),...fields,id}})},geoContentApi:{},
    onMounted(){},onUnmounted:callback=>unmount.push(callback)}
  const actions=scope.run(()=>new Function(...Object.keys(dependencies),body+`;return {projects,projectId,project,report,plan,savedReports,selectedReportId,reportChoice,taskBinding,nativeTask,nativeHistory,nativeDetails,nativeStarting,nativeBusy,nativeMessage,taskRecovery,workspaceSaveError,restoreWorkspace,pollNative,startNative,showNativeTask,loadSavedReport,nativeStatusLabel,reportLoading};`)(...Object.values(dependencies)))
  actions.projects.value=[profile(),profile('b')]
  actions.savedReports.value=reports
  return {...actions,native,messages,stop:()=>{for(const close of unmount)close();scope.stop()}}
}

test('the actual restore function recovers a stopped report behind saved.report:null and shows the scoped login action',async()=>{
  const page=harness()
  try {
    await page.restoreWorkspace()
    assert.equal(page.projectId.value,1);assert.equal(page.report.value.run_id,'run-a')
    assert.equal(page.report.value.status,'stopped');assert.equal(page.selectedReportId.value,'saved-a')
    assert.equal(page.taskRecovery.value.title,'豆包登录已失效');assert.equal(page.nativeMessage.value,'')
    assert.equal(page.nativeStatusLabel(task()),'已停止');assert.equal(page.nativeBusy.value,false)
    await page.pollNative();assert.equal(page.report.value.run_id,'run-a')
  }finally{page.stop()}
})

test('an explicitly selected current report survives workspace restoration and terminal polling',async()=>{
  const chosen=report(profile('b'),'chosen-history'),page=harness()
  try {
    page.report.value=chosen;page.selectedReportId.value='chosen-id';page.reportChoice.value='explicit'
    await page.restoreWorkspace();await page.pollNative()
    assert.equal(page.report.value.run_id,'chosen-history');assert.equal(page.selectedReportId.value,'chosen-id')
  }finally{page.stop()}
})

test('a global latest task with another product path never populates the current project or a recovery card',async()=>{
  const page=harness({currentTask:task(profile('b'))})
  try {
    await page.restoreWorkspace();await page.pollNative()
    assert.equal(page.projectId.value,1);assert.equal(page.report.value,null)
    assert.equal(page.taskRecovery.value,null);assert.match(page.nativeMessage.value,/其他或未绑定对象/)
    assert.equal(page.plan.value.profile.website,profile().website)
  }finally{page.stop()}
})

test('an imported workspace without a stored project restores its own records, not a global latest result',async()=>{
  const standalone={...profile(),id:undefined,website:'https://standalone.example/products/reader'}
  const page=harness({saved:{project_id:null,plan:plan(standalone),report:report(standalone,'imported-workspace')},currentTask:task(standalone)})
  try {
    await page.restoreWorkspace();await page.pollNative()
    assert.equal(page.projectId.value,null);assert.equal(page.report.value.run_id,'imported-workspace')
    assert.equal(page.plan.value.profile.website,standalone.website)
    assert.equal(page.taskRecovery.value,null);assert.match(page.nativeMessage.value,/其他或未绑定对象/)
  }finally{page.stop()}
})

test('an already-ended task can be processed after reload even when nativeBusy is false',async()=>{
  let gets=0;const page=harness({api:{get_task:async()=>{gets++;return task()}}})
  try {
    page.projectId.value=1;page.nativeTask.value=task();assert.equal(page.nativeBusy.value,false)
    await page.pollNative();await page.pollNative()
    assert.equal(gets,1);assert.equal(page.report.value.run_id,'run-a')
  }finally{page.stop()}
})

test('out-of-order polls cannot replace a newer native observation',async()=>{
  const first=deferred(),second=deferred();let calls=0
  const page=harness({api:{get_task:()=>++calls===1?first.promise:second.promise}})
  try {
    page.projectId.value=1;page.nativeTask.value=task(profile(),{status:'running'})
    const old=page.pollNative(),recent=page.pollNative()
    second.resolve(task());await recent
    first.resolve(task(profile(),{status:'running'}));await old
    assert.equal(page.nativeTask.value.status,'completed');assert.equal(page.nativeBusy.value,false)
    assert.equal(page.report.value.run_id,'run-a')
  }finally{page.stop()}
})

test('project A to B to A invalidates a pending poll and its report application',async()=>{
  const response=deferred(),page=harness({api:{get_task:()=>response.promise}})
  try {
    page.projectId.value=1;page.nativeTask.value=task(profile(),{status:'running'})
    const pending=page.pollNative();page.projectId.value=2;page.projectId.value=1
    response.resolve(task());await pending
    assert.equal(page.report.value,null);assert.equal(page.nativeTask.value.status,'running')
  }finally{page.stop()}
})

test('an old poll cannot override the task returned by a later explicit start',async()=>{
  const response=deferred(),page=harness({api:{get_task:()=>response.promise,open_login:async()=>({id:'new-login',kind:'login',status:'running'})}})
  try {
    page.projectId.value=1;page.nativeTask.value=task(profile(),{status:'running'})
    const pending=page.pollNative()
    page.nativeTask.value={id:'old-terminal',status:'completed'}
    await page.startNative('login');response.resolve(task());await pending
    assert.equal(page.nativeTask.value.id,'new-login');assert.equal(page.nativeBusy.value,true)
  }finally{page.stop()}
})

test('a report selected while a terminal poll is pending has priority over that task result',async()=>{
  const response=deferred(),chosen=report(profile(),'chosen-history')
  const page=harness({api:{get_task:()=>response.promise},reportApi:{getReport:async()=>({data:chosen})}})
  try {
    page.projectId.value=1;page.nativeTask.value=task(profile(),{status:'running'})
    const pending=page.pollNative();page.selectedReportId.value='chosen-id';await page.loadSavedReport()
    response.resolve(task());await pending
    assert.equal(page.report.value.run_id,'chosen-history');assert.equal(page.selectedReportId.value,'chosen-id')
  }finally{page.stop()}
})

test('same report ID A to B to A loads use the latest request, not only ID equality',async()=>{
  const requests=[],page=harness({reportApi:{getReport:()=>{const response=deferred();requests.push(response);return response.promise}}})
  try {
    page.projectId.value=1
    page.selectedReportId.value='a';const old=page.loadSavedReport()
    page.selectedReportId.value='b';const b=page.loadSavedReport()
    page.selectedReportId.value='a';const recent=page.loadSavedReport()
    requests[0].resolve({data:report(profile(),'old-a')});await old;assert.equal(page.reportLoading.value,true)
    requests[2].resolve({data:report(profile(),'new-a')});await recent
    requests[1].resolve({data:report(profile(),'old-b')});await b
    assert.equal(page.report.value.run_id,'new-a');assert.equal(page.reportLoading.value,false)
  }finally{page.stop()}
})

test('explicit historical task selection permits its own foreign report but does not silently switch the project',async()=>{
  const value=task(profile('b'),{id:'selected-history'}),page=harness({api:{get_task:async()=>value}})
  try {
    page.projectId.value=1;await page.showNativeTask('selected-history')
    assert.equal(page.report.value.profile.website,profile('b').website)
    assert.equal(page.projectId.value,1);assert.equal(page.reportChoice.value,'explicit')
  }finally{page.stop()}
})

test('duplicate starts and canceled unknown-submission restarts do not issue extra operations',async()=>{
  const response=deferred();let calls=0
  const page=harness({api:{open_login:()=>{calls++;return response.promise}}})
  try {
    page.projectId.value=1;const first=page.startNative('login');await page.startNative('login')
    assert.equal(calls,1);response.resolve({id:'new-login',kind:'login',status:'running'});await first
  }finally{page.stop()}
  let runs=0;const canceled=harness({api:{start_run:async()=>{runs++;return {id:'must-not-start'}}},confirm:async()=>{throw Error('cancel')}})
  try {
    canceled.projectId.value=1;canceled.nativeTask.value=task();canceled.plan.value=plan();canceled.report.value=report()
    await canceled.startNative('run');assert.equal(runs,0);assert.equal(canceled.nativeStarting.value,false)
    assert.equal(canceled.nativeTask.value.id,'task-a');assert.equal(canceled.report.value.run_id,'run-a')
    assert.deepEqual(canceled.messages,[])
  }finally{canceled.stop()}
})

test('explicit report-only planning and running still receive their own results without binding global tasks',async()=>{
  let current={id:'plan-only',kind:'plan',status:'running'}
  const page=harness({api:{plan_with_doubao:async()=>current,start_run:async()=>({id:'run-only',kind:'run',status:'running'}),get_task:async()=>current}})
  try {
    page.report.value=report();page.reportChoice.value='explicit'
    await page.startNative('plan');assert.equal(page.project.value,undefined)
    current={id:'plan-only',kind:'plan',status:'completed',result:{plan:plan()}}
    await page.pollNative();assert.equal(page.plan.value.profile.website,profile().website)
    await page.startNative('run');assert.equal(page.report.value,null)
    current=task(profile(),{id:'run-only'})
    await page.pollNative();assert.equal(page.report.value.run_id,'run-a')
    assert.equal(page.taskRecovery.value.title,'豆包登录已失效')
    current=task(profile(),{id:'global-latest',result:{report:report(profile(),'foreign-global-run')}})
    page.nativeTask.value={id:'run-only',kind:'run',status:'running'}
    await page.pollNative();assert.equal(page.report.value.run_id,'run-a')
    assert.equal(page.taskRecovery.value.taskId,'run-only')
    assert.match(page.nativeMessage.value,/其他或未绑定对象/)
  }finally{page.stop()}
})

test('choosing another report invalidates a report-only local task and its recovery warning',async()=>{
  const response=deferred(),chosen=report(profile('b'),'chosen-history')
  const page=harness({api:{start_run:async()=>({id:'run-only',kind:'run',status:'running'}),get_task:()=>response.promise},reportApi:{getReport:async()=>({data:chosen})}})
  try {
    page.plan.value=plan();await page.startNative('run')
    const pending=page.pollNative();page.selectedReportId.value='chosen-id';await page.loadSavedReport()
    response.resolve(task(profile(),{id:'run-only'}));await pending
    assert.equal(page.report.value.run_id,'chosen-history');assert.equal(page.taskRecovery.value,null)
  }finally{page.stop()}
})

test('a terminal report-only recovery warning reacts immediately to a newer explicit report choice',async()=>{
  const response=deferred(),page=harness({api:{start_run:async()=>({id:'run-only',kind:'run',status:'running'}),get_task:async()=>task(profile(),{id:'run-only'})},reportApi:{getReport:()=>response.promise}})
  try {
    page.plan.value=plan();await page.startNative('run');await page.pollNative()
    assert.equal(page.taskRecovery.value.taskId,'run-only')
    page.selectedReportId.value='chosen-id';const pending=page.loadSavedReport()
    assert.equal(page.taskRecovery.value,null)
    response.resolve({data:report(profile('b'),'chosen-history')});await pending
    assert.equal(page.report.value.run_id,'chosen-history');assert.equal(page.taskRecovery.value,null)
  }finally{page.stop()}
})

test('unmounted and manually switched workspaces cannot apply a delayed restore',async()=>{
  for(const unmount of [false,true]) {
    const response=deferred(),page=harness({api:{get_workspace_state:()=>response.promise}})
    const pending=page.restoreWorkspace()
    if(unmount)page.stop();else page.projectId.value=2
    response.resolve({project_id:1,report:report(),plan:plan()});await pending
    assert.equal(page.report.value,null);if(!unmount){assert.equal(page.projectId.value,2);page.stop()}
  }
})

test('search recovery compiles with an explicit login-only action and guarded task/report state',()=>{
  assert.deepEqual(compileTemplate({source:descriptor.template.content,filename:filename.pathname,id:'geo-search-restore'}).errors,[])
  for(const style of descriptor.styles)assert.deepEqual(compileStyle({source:style.content,filename:filename.pathname,id:'geo-search-restore',scoped:true}).errors,[])
  assert.match(descriptor.template.content,/<DoubaoTaskRecovery/)
  assert.match(descriptor.template.content,/@login="startNative\('login'\)"/)
  assert.doesNotMatch(descriptor.template.content,/Boolean\(window\.pywebview/)
})
