import assert from 'node:assert/strict'
import fs from 'node:fs'
import test from 'node:test'
import { computed, ref } from 'vue'
import { parse, compileScript, compileTemplate, compileStyle } from '@vue/compiler-sfc'
import { doubaoTaskRecovery, scopedRecoveryCheckpoint } from '../src/utils/geoTaskRecovery.js'

const filename=new URL('../src/views/GeoJourney.vue',import.meta.url)
const {descriptor,errors}=parse(fs.readFileSync(filename,'utf8'),{filename:filename.pathname})
assert.deepEqual(errors,[])
const compiled=compileScript(descriptor,{id:'journey-research-resume'})
const expression=name=>{
  const declaration=compiled.scriptSetupAst.flatMap(node=>node.type==='VariableDeclaration'?node.declarations:[]).find(node=>node.id.name===name)
  assert.ok(declaration,name)
  return descriptor.scriptSetup.content.slice(declaration.init.start,declaration.init.end)
}
const deferred=()=>{let resolve;const promise=new Promise(done=>resolve=done);return {promise,resolve}}
const sourceTask=(changes={})=>({id:'source-retest',kind:'run',status:'failed',cycle_id:'cycle-a',
  resume_research_retest:{can_resume:true,pending_case_ids:['q02','q03'],message:'继续未发送题，已发送题不会重发'},...changes})
const harness=(source=sourceTask())=>{
  const calls={reads:[],resumes:[],wholeRounds:[],confirms:[],persist:[]}
  const checkpoint={id:'source-retest',kind:'retest',status:'need_action',cycle_id:'cycle-a',recovery:{question_submitted:true}}
  const values={computed,step:ref(4),cycle:ref({id:'cycle-a'}),cycleId:ref('cycle-a'),nativeAvailable:ref(true),retestResume:ref(null),
    visibleRecoveryInfo:ref({kind:'retest',taskId:'source-retest',requiresConfirmation:true}),
    task:ref(source),taskId:ref('source-retest'),taskKind:ref('retest'),recoveryTask:ref(checkpoint),working:ref(false),error:ref('')}
  values.nativeBusy=computed(()=>values.task.value.status==='running')
  values.busy=computed(()=>values.working.value||values.nativeBusy.value)
  values.recoveryInfo=values.visibleRecoveryInfo
  values.persist=()=>calls.persist.push({taskId:values.taskId.value,recoveryTask:values.recoveryTask.value})
  values.ElMessageBox={confirm:async(...args)=>calls.confirms.push(args)}
  values.window={pywebview:{api:{
    get_task:async id=>{calls.reads.push(id);return source},
    resume_research_retest:async(...args)=>{calls.resumes.push(args);return {id:'continued-retest',status:'running'}},
    start_research_retest:async(...args)=>{calls.wholeRounds.push(args);return {id:'new-whole-round',status:'running'}}
  }}}
  const names=['resumableRetest','canResumeRetest','refreshRetestResume','run','resumeUnsentRetest','startNative']
  const actions=new Function(...Object.keys(values),`
    let destroyed=false,contextEpoch=0,retestResumeRead=0,liveTaskId='',handledTask='';
    ${names.map(name=>`const ${name}=${expression(name)};`).join('\n')}
    return {${names.join(',')},epoch:()=>contextEpoch++,destroy:()=>destroyed=true};
  `)(...Object.values(values))
  return {...values,...actions,calls,checkpoint}
}

test('an external or explicitly disabled cycle cannot resume a native request even if a stale task matches its ID',async()=>{
  for(const value of [{id:'cycle-a',baseline_source:'user_reported_external'},{id:'cycle-a',capabilities:{native_retest:false}}]){
    const page=harness();page.cycle.value=value
    await page.refreshRetestResume()
    assert.equal(page.canResumeRetest.value,false)
    await page.resumeUnsentRetest()
    assert.equal(page.calls.resumes.length,0);assert.equal(page.calls.wholeRounds.length,0)
  }
})

test('a real-shaped timeout reaches same-cycle feedback recovery and enables only its unsent continuation',async()=>{
  const actualSource=sourceTask({failure_reason:'browser_error:TimeoutError',
    progress:{question_submission_attempted:true,question_submitted:false}})
  for(const variant of ['same-cycle','foreign-cycle','foreign-task','baseline-run','unknown-reason']){
    const source=variant==='unknown-reason'?{...actualSource,failure_reason:'browser_error:OtherTimeoutError'}:actualSource
    const page=harness(source),original=JSON.stringify(source)
    if(variant==='foreign-cycle')page.cycleId.value='cycle-b'
    if(variant==='foreign-task')page.taskId.value='another-task'
    if(variant==='baseline-run')page.taskKind.value='run'
    const bindings={computed,task:page.task,taskId:page.taskId,taskKind:page.taskKind,cycleId:page.cycleId,
      step:page.step,doubaoTaskRecovery,scopedRecoveryCheckpoint,recoveryTask:ref(null)}
    const visible=new Function(...Object.keys(bindings),`
      const currentRecovery=${expression('currentRecovery')};
      const recoveryInfo=${expression('recoveryInfo')};
      const recoveryStepKinds=${expression('recoveryStepKinds')};
      return (${expression('visibleRecoveryInfo')});
    `)(...Object.values(bindings))
    page.visibleRecoveryInfo.value=visible.value
    await page.refreshRetestResume()
    assert.equal(page.canResumeRetest.value,variant==='same-cycle',variant)
    if(variant==='same-cycle'){
      assert.equal(visible.value.reason,'browser_error:TimeoutError')
      assert.equal(visible.value.kind,'retest');assert.equal(visible.value.requiresConfirmation,true)
      await page.resumeUnsentRetest()
      assert.deepEqual(page.calls.resumes,[['source-retest','cycle-a']])
      assert.deepEqual(page.calls.wholeRounds,[])
    }else assert.deepEqual(page.calls.resumes,[])
    assert.equal(JSON.stringify(source),original)
  }
})

test('only explicit fresh same-cycle terminal run capability enables the resume action',async()=>{
  const invalid=[
    sourceTask({resume_research_retest:undefined}),
    sourceTask({resume_research_retest:{can_resume:false,pending_case_ids:['q02']}}),
    sourceTask({resume_research_retest:{can_resume:'true',pending_case_ids:['q02']}}),
    sourceTask({resume_research_retest:{can_resume:true,pending_case_ids:[]}}),
    sourceTask({resume_research_retest:{can_resume:true,pending_case_ids:'q02'}}),
    sourceTask({resume_research_retest:{can_resume:true,pending_case_ids:['../q02']}}),
    sourceTask({resume_research_retest:{can_resume:true,pending_case_ids:['q02','q02']}}),
    sourceTask({id:'other-task'}),sourceTask({cycle_id:'cycle-b'}),sourceTask({cycle_id:''}),
    sourceTask({kind:'draft'}),sourceTask({status:'running'}),sourceTask({status:'unknown'})
  ]
  for(const source of invalid){
    const page=harness(source)
    await page.refreshRetestResume()
    assert.equal(page.canResumeRetest.value,false,JSON.stringify(source))
    await page.resumeUnsentRetest()
    assert.deepEqual(page.calls.resumes,[])
    assert.equal(page.taskId.value,'source-retest')
    assert.equal(page.recoveryTask.value.id,'source-retest')
  }
  for(const status of ['completed','failed','stopped','need_action','needs_review']){
    const page=harness(sourceTask({status}));await page.refreshRetestResume()
    assert.equal(page.canResumeRetest.value,true,status)
  }
})

test('non-feedback, unbound, busy and older-bridge contexts cannot resume',async()=>{
  for(const change of [
    p=>p.step.value=2,p=>p.visibleRecoveryInfo.value.kind='run',p=>p.visibleRecoveryInfo.value.taskId='other-task',
    p=>p.visibleRecoveryInfo.value=null,p=>p.cycleId.value='',p=>p.nativeAvailable.value=false,
    p=>delete p.window.pywebview.api.resume_research_retest,
    p=>p.task.value={id:'another-active-task',status:'running'}
  ]){
    const page=harness();change(page);await page.refreshRetestResume()
    assert.equal(page.canResumeRetest.value,false)
    await page.resumeUnsentRetest();assert.deepEqual(page.calls.resumes,[])
  }
})

test('normal continuation rechecks the source and calls only the ID-based resume bridge once',async()=>{
  const source=sourceTask(),before=JSON.stringify(source),page=harness(source)
  await page.refreshRetestResume();assert.equal(page.canResumeRetest.value,true)
  await page.resumeUnsentRetest();await page.resumeUnsentRetest()
  assert.deepEqual(page.calls.reads,['source-retest','source-retest'])
  assert.deepEqual(page.calls.resumes,[['source-retest','cycle-a']])
  assert.deepEqual(page.calls.wholeRounds,[]);assert.deepEqual(page.calls.confirms,[])
  assert.equal(page.taskId.value,'continued-retest');assert.equal(page.taskKind.value,'retest')
  assert.equal(page.task.value.kind,'run');assert.equal(page.task.value.cycle_id,'cycle-a')
  assert.equal(page.recoveryTask.value,null);assert.equal(page.retestResume.value,null)
  assert.equal(JSON.stringify(source),before,'the original task and capability are not mutated')
})

test('a fresh refusal after a previously enabled button never sends or starts a whole round',async()=>{
  const page=harness();await page.refreshRetestResume()
  page.window.pywebview.api.get_task=async()=>sourceTask({resume_research_retest:{can_resume:false,pending_case_ids:[]}})
  await page.resumeUnsentRetest()
  assert.deepEqual(page.calls.resumes,[]);assert.deepEqual(page.calls.wholeRounds,[])
  assert.equal(page.taskId.value,'source-retest');assert.equal(page.recoveryTask.value.id,'source-retest')
  assert.equal(page.canResumeRetest.value,false);assert.match(page.error.value,/当前不能继续/)
})

test('the latest capability read wins and stale reads cannot reenable a consumed source',async()=>{
  const page=harness(),old=deferred(),recent=deferred();let count=0
  page.window.pywebview.api.get_task=()=>++count===1?old.promise:recent.promise
  const first=page.refreshRetestResume(),second=page.refreshRetestResume()
  recent.resolve(sourceTask({resume_research_retest:{can_resume:false,pending_case_ids:[]}}));await second
  old.resolve(sourceTask());await first
  assert.equal(page.canResumeRetest.value,false);assert.equal(page.retestResume.value,null)
})

test('a capability read cannot populate another cycle, source or destroyed view',async()=>{
  for(const change of [p=>p.epoch(),p=>p.cycleId.value='cycle-b',p=>p.visibleRecoveryInfo.value.taskId='other-task',p=>p.destroy()]){
    const page=harness(),response=deferred();page.window.pywebview.api.get_task=()=>response.promise
    const refreshing=page.refreshRetestResume();change(page);response.resolve(sourceTask());await refreshing
    assert.equal(page.retestResume.value,null)
  }
})

test('leaving while the fresh preflight is pending cannot send a continuation',async()=>{
  for(const change of [p=>p.epoch(),p=>p.step.value=2,p=>p.cycleId.value='cycle-b',p=>p.taskId.value='other-task',p=>p.destroy()]){
    const page=harness();await page.refreshRetestResume()
    const response=deferred();page.window.pywebview.api.get_task=()=>response.promise
    const resuming=page.resumeUnsentRetest();change(page);response.resolve(sourceTask());await resuming
    assert.deepEqual(page.calls.resumes,[]);assert.equal(page.recoveryTask.value.id,'source-retest')
  }
})

test('late continuation acknowledgement cannot overwrite another operation context',async()=>{
  const page=harness();await page.refreshRetestResume()
  const response=deferred();page.window.pywebview.api.resume_research_retest=(...args)=>{page.calls.resumes.push(args);return response.promise}
  const resuming=page.resumeUnsentRetest();await Promise.resolve();await Promise.resolve()
  page.epoch();page.taskId.value='different-current-task'
  response.resolve({id:'continued-retest',status:'running'});await resuming
  assert.equal(page.calls.resumes.length,1);assert.equal(page.taskId.value,'different-current-task')
  assert.equal(page.recoveryTask.value.id,'source-retest')
})

test('ambiguous or foreign acknowledgement preserves the original checkpoint and disables replay',async()=>{
  for(const result of [null,{}, {id:'source-retest',status:'running'}, {id:'../bad',status:'running'},
    {id:'new',status:'completed'},{id:'new',status:'running',kind:'draft'},
    {id:'new',status:'running',cycle_id:'cycle-b'},
    {id:'new',status:'running',resumed_from_task_id:'other-source'}]){
    const page=harness();await page.refreshRetestResume()
    page.window.pywebview.api.resume_research_retest=async(...args)=>{page.calls.resumes.push(args);return result}
    await page.resumeUnsentRetest();await page.resumeUnsentRetest()
    assert.equal(page.calls.resumes.length,1);assert.equal(page.taskId.value,'source-retest')
    assert.equal(page.recoveryTask.value.id,'source-retest');assert.equal(page.retestResume.value,null)
  }
})

test('server refusal leaves original evidence and never falls back to a new full experiment',async()=>{
  const page=harness();await page.refreshRetestResume()
  page.window.pywebview.api.resume_research_retest=async()=>({error:'此源任务已被续采，请查看最新任务'})
  await page.resumeUnsentRetest()
  assert.match(page.error.value,/已被续采/);assert.equal(page.taskId.value,'source-retest')
  assert.equal(page.recoveryTask.value.id,'source-retest');assert.deepEqual(page.calls.wholeRounds,[])
})

test('separate completed login still fetches the exact original retest capability',async()=>{
  const page=harness();page.task.value={id:'completed-login',kind:'login',status:'completed'}
  page.taskId.value='completed-login';page.taskKind.value='login'
  await page.refreshRetestResume();assert.equal(page.canResumeRetest.value,true)
  assert.deepEqual(page.calls.reads,['source-retest'])
  await page.resumeUnsentRetest();assert.equal(page.taskKind.value,'retest')
  assert.deepEqual(page.calls.resumes,[['source-retest','cycle-a']])
})

test('a deliberate whole new retest still uses its original repeat confirmation',async()=>{
  const page=harness();await page.refreshRetestResume()
  await page.startNative('retest',()=>page.window.pywebview.api.start_research_retest(page.cycleId.value))
  assert.equal(page.calls.confirms.length,1);assert.deepEqual(page.calls.resumes,[])
  assert.deepEqual(page.calls.wholeRounds,[['cycle-a']])
})

test('capability is not persisted and the accessible action compiles without an automatic resume watcher',()=>{
  assert.doesNotMatch(expression('persist'),/retestResume|resume_research_retest/)
  assert.doesNotMatch(expression('refreshRetestResume'),/\.resume_research_retest\(/)
  assert.match(descriptor.template.content,/v-if="canResumeRetest"/)
  assert.match(descriptor.template.content,/继续未发送题，已发送题不会重发/)
  assert.match(descriptor.template.content,/data-testid="journey-resume-retest" @click="resumeUnsentRetest"/)
  const template=compileTemplate({source:descriptor.template.content,filename:filename.pathname,id:'journey-research-resume',compilerOptions:{bindingMetadata:compiled.bindings}})
  assert.deepEqual(template.errors,[])
  for(const style of descriptor.styles)assert.deepEqual(compileStyle({source:style.content,filename:filename.pathname,id:'journey-research-resume'}).errors,[])
})
