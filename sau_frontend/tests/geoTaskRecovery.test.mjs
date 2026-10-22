import assert from 'node:assert/strict'
import fs from 'node:fs'
import test from 'node:test'
import { computed, ref } from 'vue'
import { parse, compileScript, compileTemplate, compileStyle } from '@vue/compiler-sfc'
import { doubaoTaskRecovery, recoveryCheckpoint, scopedRecoveryCheckpoint } from '../src/utils/geoTaskRecovery.js'

const failed=(changes={})=>({id:'failed-draft',kind:'draft',status:'need_action',cycle_id:'cycle-a',
  recovery:{failure_reason:'session_redirected_to_logout',question_submission_attempted:false,question_submitted:false,
    requested_client_mode:'work',requested_model_label:''},...changes})

test('an old logout error becomes an actionable explanation without inventing submission proof',()=>{
  const recovery=doubaoTaskRecovery(failed({status:'failed',recovery:undefined,
    message:'豆包未返回完整内容（session_redirected_to_logout），原始证据已保存，未自动重试'}))
  assert.equal(recovery.title,'豆包登录已失效');assert.equal(recovery.canLogin,true)
  assert.equal(recovery.submission,'unknown');assert.equal(recovery.requiresConfirmation,true)
  assert.match(recovery.submissionText,/可能重复提问/)
  assert.doesNotMatch(recovery.title+recovery.description,/session_redirected_to_logout|已确认登录/)
})

test('only two explicit false flags prove no submission; attempted and sent remain distinct',()=>{
  assert.equal(doubaoTaskRecovery(failed()).submission,'not_submitted')
  assert.equal(doubaoTaskRecovery(failed()).requiresConfirmation,false)
  for(const flags of [{question_submission_attempted:true,question_submitted:false},
    {question_submission_attempted:'false',question_submitted:0},
    {question_submission_attempted:false,question_submitted:null}]){
    const recovery=doubaoTaskRecovery(failed({recovery:{failure_reason:'login_required',...flags}}))
    assert.equal(recovery.submission,'unknown');assert.equal(recovery.requiresConfirmation,true)
  }
  const sent=doubaoTaskRecovery(failed({recovery:{failure_reason:'login_required',question_submitted:true}}))
  assert.equal(sent.submission,'submitted');assert.match(sent.submissionText,/再次提问/)
  const unknown=doubaoTaskRecovery(failed({recovery:{failure_reason:'login_required',
    question_submission_attempted:null,question_submitted:null},
    progress:{question_submission_attempted:false,question_submitted:false}}))
  assert.equal(unknown.submission,'unknown');assert.equal(unknown.requiresConfirmation,true)
})

test('live, successful, foreign platform and unknown failures never get a stale login recovery',()=>{
  for(const value of [null,failed({status:'running'}),failed({status:'completed'}),failed({kind:'destination_fill'}),
    failed({kind:'login'}),failed({id:'../bad'}),failed({recovery:{failure_reason:'unknown_failure'},message:'untrusted private text'})]){
    assert.equal(doubaoTaskRecovery(value),null)
  }
  const timeout=doubaoTaskRecovery(failed({recovery:{failure_reason:'background_task_timeout'}}))
  assert.equal(timeout.canLogin,false);assert.equal(timeout.submission,'unknown')
})

test('a recovery checkpoint survives a separate login but contains no raw evidence or account text',()=>{
  const value=recoveryCheckpoint(failed({message:'private-account',evidence_path:'C:/private/evidence',
    recovery:{...failed().recovery,evidence_path:'C:/private/evidence',raw:'private-account'}}))
  assert.equal(doubaoTaskRecovery(JSON.parse(JSON.stringify(value))).submission,'not_submitted')
  assert.doesNotMatch(JSON.stringify(value),/private-account|evidence/)
  assert.equal(scopedRecoveryCheckpoint(value,'cycle-b'),null)
  assert.equal(scopedRecoveryCheckpoint(value,''),null)
  assert.deepEqual(scopedRecoveryCheckpoint(value,'cycle-a'),value)
})

test('a completed worker with an interrupted report still needs recovery and never proves an unsent question',()=>{
  const recovery=doubaoTaskRecovery({id:'partial-run',kind:'run',status:'completed',result:{report:{
    status:'stopped',stop_reason:'session_redirected_to_logout',execution:{client_mode:'work'}}}})
  assert.equal(recovery.canLogin,true);assert.equal(recovery.clientMode,'work')
  assert.equal(recovery.submission,'unknown');assert.equal(recovery.requiresConfirmation,true)
  const closed=doubaoTaskRecovery(failed({recovery:{failure_reason:'browser_error:TargetClosedError',question_submitted:true}}))
  assert.match(closed.title,/窗口已结束/);assert.equal(closed.submission,'submitted')
  for(const reason of ['task_failed_submission_unknown','submission_state_unknown','collector_exception_submission_unknown']){
    const value=doubaoTaskRecovery(failed({recovery:{failure_reason:reason,question_submission_attempted:null,question_submitted:null}}))
    assert.equal(value.submission,'unknown');assert.equal(value.requiresConfirmation,true);assert.equal(value.canLogin,false)
  }
})

const filename=new URL('../src/views/GeoJourney.vue',import.meta.url)
const {descriptor}=parse(fs.readFileSync(filename,'utf8'))
const compiled=compileScript(descriptor,{id:'journey-recovery-test'})
const source=descriptor.scriptSetup.content
const expression=name=>{
  const node=compiled.scriptSetupAst.flatMap(value=>value.type==='VariableDeclaration'?value.declarations:[]).find(value=>value.id.name===name)
  assert.ok(node,name);return source.slice(node.init.start,node.init.end)
}
const deferred=()=>{let resolve;const promise=new Promise(done=>resolve=done);return {promise,resolve}}
const harness=()=>{
  const values={task:ref(failed()),taskId:ref('failed-draft'),taskKind:ref('draft'),recoveryTask:ref(recoveryCheckpoint(failed())),
    cycleId:ref('cycle-a'),projectId:ref(1),nativeAvailable:ref(true),finishingTask:ref(false),error:ref(''),
    cycle:ref({baseline:{execution:{client_mode:'work',requested_model_label:'原模型'}}}),
    step:ref(2),clientMode:ref('consumer'),model:ref('当前模型'),busy:ref(false),working:ref(false),
    report:ref(null),reports:ref([]),reportId:ref(''),reportOrigin:ref(''),historyReportId:ref(''),project:ref({}),
    plan:ref(null),planConfirmed:ref(false),replacementSourceId:ref(null),selectedTarget:ref(null),matchingDrafts:ref([]),articleId:ref(null),
    doubaoTaskRecovery,recoveryCheckpoint,scopedRecoveryCheckpoint,computed}
  const confirms=[],calls=[],applies=[]
  values.recoveryInfo=computed(()=>doubaoTaskRecovery(values.recoveryTask.value))
  values.ElMessageBox={confirm:async(...args)=>confirms.push(args)}
  values.persistCalls=[];values.persist=()=>values.persistCalls.push(true);values.sameBrand=()=>true;values.loadReports=async()=>{}
  values.loadEditor=()=>{};values.applyCycle=(...args)=>applies.push(args)
  values.geoContentApi={get:async()=>({data:{id:'cycle-a'}})}
  values.window={pywebview:{api:{get_task:async()=>values.task.value,open_login:async options=>{calls.push(options);return {id:'new-login',status:'running'}}}}}
  const names=['startNative','options','retestSessionOptions','login','selectSessionMode','restoreTaskRecovery','run','poll']
  const page=new Function(...Object.keys(values),`
    let liveTaskId='',handledTask='',pollPending=false,destroyed=false,contextEpoch=0;
    ${names.map(name=>`const ${name}=${expression(name)};`).join('\n')}
    return {${names.join(',')},epoch:()=>contextEpoch++,destroy:()=>destroyed=true};
  `)(...Object.values(values))
  return {...page,...values,confirms,calls,applies}
}

test('separate login does not rerun content or clear the original recovery warning',async()=>{
  const page=harness();await page.login()
  assert.deepEqual(page.calls,[{client_mode:'consumer',model_label:'当前模型'}])
  assert.equal(page.confirms.length,0);assert.equal(page.taskKind.value,'login')
  assert.equal(page.recoveryTask.value.id,'failed-draft')
})

test('feedback login uses the original retest session instead of unrelated writing choices',async()=>{
  const page=harness();page.step.value=4;await page.login()
  assert.deepEqual(page.calls,[{client_mode:'work',model_label:'原模型'}])
})

test('unknown or sent retries wait for an explicit decision and cancellation never sends',async()=>{
  const page=harness();page.recoveryTask.value=recoveryCheckpoint(failed({recovery:{failure_reason:'login_required',question_submitted:true}}))
  const decision=deferred();page.ElMessageBox.confirm=()=>decision.promise
  let sent=0;const pending=page.run(()=>page.startNative('draft',async()=>{sent++;return {id:'new-draft',status:'running'}}))
  assert.equal(sent,0);decision.resolve(Promise.reject('cancel'));await pending
  assert.equal(sent,0);assert.equal(page.error.value,'');assert.equal(page.taskId.value,'failed-draft')
  assert.equal(page.recoveryTask.value.id,'failed-draft')
})

test('confirmed retry starts exactly once and clears the old warning only after acknowledgement',async()=>{
  const page=harness();page.recoveryTask.value=recoveryCheckpoint(failed({recovery:{failure_reason:'login_required'}}))
  let sent=0
  await page.run(()=>page.startNative('draft',async()=>{sent++;return {id:'new-draft',status:'running'}}))
  assert.equal(page.confirms.length,1);assert.equal(sent,1);assert.equal(page.taskId.value,'new-draft')
  assert.equal(page.recoveryTask.value,null)
})

test('leaving a pending confirmation cannot start an operation on an old page',async()=>{
  const page=harness(),decision=deferred();page.recoveryTask.value=recoveryCheckpoint(failed({recovery:{failure_reason:'login_required'}}))
  page.ElMessageBox.confirm=()=>decision.promise
  let calls=0;const starting=page.run(()=>page.startNative('draft',async()=>{calls++;return {id:'new',status:'running'}}))
  page.destroy();decision.resolve();await starting
  assert.equal(calls,0);assert.equal(page.taskId.value,'failed-draft');assert.equal(page.error.value,'');assert.equal(page.persistCalls.length,0)
})

test('a late native acknowledgement cannot overwrite or persist a different context',async()=>{
  const page=harness(),response=deferred()
  const starting=page.run(()=>page.startNative('draft',()=>response.promise))
  page.epoch();page.task.value={id:'another-context-task',status:'running'}
  response.resolve({id:'old-native',status:'running'});await starting
  assert.equal(page.task.value.id,'another-context-task');assert.equal(page.recoveryTask.value.id,'failed-draft')
})

test('missing native acknowledgement preserves the recovery warning',async()=>{
  const page=harness();await page.run(()=>page.startNative('draft',async()=>({status:'running'})))
  assert.equal(page.taskId.value,'failed-draft');assert.equal(page.recoveryTask.value.id,'failed-draft')
  assert.match(page.error.value,/启动结果尚未确认/)
})

test('proved-unsent recovery needs no extra confirmation and unavailable bridge preserves warning',async()=>{
  const page=harness();await page.run(()=>page.startNative('draft',async()=>({error:'服务未就绪'})))
  assert.equal(page.confirms.length,0);assert.equal(page.taskId.value,'failed-draft')
  assert.equal(page.recoveryTask.value.id,'failed-draft');assert.equal(page.error.value,'服务未就绪')
})

test('writing session selection is available locally and cannot change while a task runs',()=>{
  const page=harness();page.selectSessionMode('consumer');assert.equal(page.model.value,'当前模型')
  page.selectSessionMode('work');assert.equal(page.model.value,'');assert.equal(page.clientMode.value,'work')
  page.busy.value=true;page.selectSessionMode('consumer');assert.equal(page.clientMode.value,'work')
})

test('a late old poll cannot overwrite a newly started task or another project context',async()=>{
  for(const change of [page=>page.taskId.value='new-task',page=>page.epoch(),page=>page.destroy()]){
    const page=harness(),pending=deferred();page.window.pywebview.api.get_task=()=>pending.promise
    const polling=page.poll();change(page);page.task.value={id:'new-task',status:'running'}
    pending.resolve(failed());await polling
    assert.equal(page.task.value.id,'new-task');assert.deepEqual(page.applies,[])
  }
})

test('failed draft polling keeps edited content and shows a recovery card instead of a raw red error',async()=>{
  const page=harness();await page.poll()
  assert.equal(page.error.value,'');assert.equal(page.recoveryTask.value.id,'failed-draft')
  assert.equal(page.applies.length,1);assert.deepEqual(page.applies[0][1],{keepEditor:true})
})

test('an old recoverable poll cannot hide a separate operation failure',async()=>{
  const page=harness(),response=deferred();page.window.pywebview.api.get_task=()=>response.promise
  const polling=page.poll()
  await page.run(async()=>{throw new Error('本次保存请求失败')})
  response.resolve(failed({message:'上次豆包任务中断'}));await polling
  assert.equal(page.error.value,'本次保存请求失败')
  assert.equal(page.recoveryTask.value.id,'failed-draft')
})

test('reopening reads the exact historical failed task without replacing the current successful worker',async()=>{
  const page=harness(),reads=[];page.recoveryTask.value=null
  page.task.value={id:'latest-success',kind:'draft',status:'completed'}
  page.window.pywebview.api.get_task=async id=>{reads.push(id);return failed()}
  await page.restoreTaskRecovery()
  assert.deepEqual(reads,['failed-draft']);assert.equal(page.recoveryTask.value.id,'failed-draft')
  assert.equal(page.task.value.id,'latest-success')
  const old=deferred();page.window.pywebview.api.get_task=()=>old.promise;page.recoveryTask.value=null
  const restoring=page.restoreTaskRecovery();page.epoch();old.resolve(failed());await restoring
  assert.equal(page.recoveryTask.value,null)
})

test('both recovery controls compile with keyboard labels, bounded layout and no implicit continuation',()=>{
  for(const component of ['DoubaoTaskRecovery','DoubaoSessionControls']){
    const {descriptor,errors}=parse(fs.readFileSync(new URL(`../src/components/${component}.vue`,import.meta.url),'utf8'))
    assert.deepEqual(errors,[])
    const script=compileScript(descriptor,{id:component})
    const template=compileTemplate({source:descriptor.template.content,filename:`${component}.vue`,id:component,compilerOptions:{bindingMetadata:script.bindings}})
    assert.deepEqual(template.errors,[])
    assert.deepEqual(compileStyle({source:descriptor.styles[0].content,filename:`${component}.vue`,id:component}).errors,[])
    assert.match(descriptor.template.content,/aria-label/)
    assert.doesNotMatch(descriptor.scriptSetup.content,/generate_research|start_run|setInterval/)
  }
})
