import assert from 'node:assert/strict'
import fs from 'node:fs'
import test from 'node:test'
import { computed, ref } from 'vue'
import { parse, compileScript, compileTemplate } from '@vue/compiler-sfc'

const filename=new URL('../src/views/DistributionCenter.vue',import.meta.url)
const {descriptor,errors}=parse(fs.readFileSync(filename,'utf8'),{filename:filename.pathname})
assert.deepEqual(errors,[])
const compiled=compileScript(descriptor,{id:'distribution-retry-test'})
const source=descriptor.scriptSetup.content
const expression=name=>{
  const node=compiled.scriptSetupAst.flatMap(value=>value.type==='VariableDeclaration'?value.declarations:[]).find(value=>value.id.name===name)
  assert.ok(node,name);return source.slice(node.init.start,node.init.end)
}
const deferred=()=>{let resolve,reject;const promise=new Promise((done,fail)=>{resolve=done;reject=fail});return {promise,resolve,reject}}
const job=(changes={})=>({job_id:37,article_id:8,article_title:'合成测试文章',account_id:3,platform:'zhihu',status:'failed',demo:false,...changes})

function harness({confirm=async()=>{},retry=async()=>({data:job({status:'queued'})}),execute=async()=>({data:job({status:'need_action',message:'请核对平台登录状态'})})}={}){
  const calls=[],confirms=[],messages=[],busyJobIds=ref(new Set()),platforms=ref([{key:'zhihu',safe_daily_new:1,min_interval_hours:6}])
  const dependencies={busyJobIds,platforms,
    publishApi:{retryJob:async id=>{calls.push(['retry',id]);return retry(id)},executeRealJob:async id=>{calls.push(['execute-real',id]);return execute(id)},executeDemoJob:async id=>calls.push(['execute-demo',id])},
    ElMessageBox:{confirm:async(...args)=>{confirms.push(args);return confirm(...args)}},
    ElMessage:Object.fromEntries(['success','warning','info','error'].map(kind=>[kind,message=>messages.push({kind,message})])),
    platformName:key=>key,fetchJobs:async()=>calls.push(['refresh']),console:{error(){}}}
  const actions=new Function(...Object.keys(dependencies),`
    let pollingStopped=false;
    ${['markJobBusy','executeRealJob','retryJob'].map(name=>`const ${name}=${expression(name)};`).join('\n')}
    return {executeRealJob,retryJob,stop:()=>pollingStopped=true};
  `)(...Object.values(dependencies))
  return {...actions,busyJobIds,calls,confirms,messages}
}

test('a real retry only requeues the original job and never implicitly executes it',async()=>{
  const page=harness();await page.retryJob(job())
  assert.deepEqual(page.calls,[['retry',37],['refresh']]);assert.equal(page.confirms.length,0)
  assert.match(page.messages[0].message,/重新排队.*真实发布仍需再次确认/)
  assert.equal(page.busyJobIds.value.has(37),false)
})

test('retry latches before waiting so rapid clicks cannot enqueue or execute duplicates',async()=>{
  const pending=deferred(),page=harness({retry:()=>pending.promise})
  const first=page.retryJob(job());await page.retryJob(job())
  assert.deepEqual(page.calls,[['retry',37]]);assert.equal(page.busyJobIds.value.has(37),true)
  pending.resolve({data:job({status:'queued'})});await first
  assert.equal(page.busyJobIds.value.has(37),false)
})

test('real execution latches before confirmation and keeps the lock until the original request finishes',async()=>{
  const decision=deferred(),response=deferred(),page=harness({confirm:()=>decision.promise,execute:()=>response.promise})
  const first=page.executeRealJob(job({status:'queued'}));await page.executeRealJob(job({status:'queued'}))
  assert.equal(page.confirms.length,1);assert.deepEqual(page.calls,[]);assert.equal(page.busyJobIds.value.has(37),true)
  decision.resolve();await Promise.resolve();await Promise.resolve()
  assert.deepEqual(page.calls,[['execute-real',37]])
  await page.retryJob(job());assert.deepEqual(page.calls,[['execute-real',37]])
  response.resolve({data:job({status:'need_action',message:'请核对平台登录状态'})});await first
  assert.equal(page.busyJobIds.value.has(37),false)
})

test('canceling real execution sends nothing and releases only that job lock without a cancel error',async()=>{
  const page=harness({confirm:async()=>{throw 'cancel'}})
  await page.executeRealJob(job())
  assert.deepEqual(page.calls,[]);assert.deepEqual(page.messages,[]);assert.equal(page.busyJobIds.value.has(37),false)
  await page.retryJob(job());assert.deepEqual(page.calls,[['retry',37],['refresh']])
})

test('an execution or retry error releases its lock and never reports success',async()=>{
  for(const action of ['executeRealJob','retryJob']){
    const fail=async()=>{throw Error('synthetic service failure')},page=harness({execute:fail,retry:fail})
    await page[action](job());assert.equal(page.busyJobIds.value.has(37),false)
    assert.equal(page.messages.some(value=>value.kind==='success'),false)
  }
})

test('unmount during confirmation prevents execution; late retry acknowledgement cannot execute even a demo job',async()=>{
  const decision=deferred(),page=harness({confirm:()=>decision.promise})
  const first=page.executeRealJob(job());page.stop();decision.resolve();await first
  assert.deepEqual(page.calls,[]);assert.equal(page.busyJobIds.value.has(37),false)
  const response=deferred(),retrying=harness({retry:()=>response.promise})
  const next=retrying.retryJob(job({demo:true}));retrying.stop();response.resolve({data:job({demo:true,status:'queued'})});await next
  assert.deepEqual(retrying.calls,[['retry',37]]);assert.deepEqual(retrying.messages,[])
})

test('demo retry retains its existing explicit demo execution behavior',async()=>{
  const page=harness({retry:async()=>({data:job({demo:true,status:'queued'})})})
  await page.retryJob(job({demo:true}));assert.deepEqual(page.calls,[['retry',37],['execute-demo',37],['refresh']])
  assert.match(page.messages[0].message,/演示任务已重新执行/)
})

test('an exhausted reservation still blocks new dispatch and explains the existing-job route without relaxing the cap',()=>{
  const values={computed,manifestPlatforms:ref([{key:'zhihu'}]),selectedAccountFor:()=>({publish_policy:{remaining_new:0}}),
    selectedArticleId:ref(8),selectedArticle:ref({status:'ready'}),journeyContextMatches:ref(true),selectedPlatforms:ref(['zhihu']),
    imageRequired:ref(false),selectedImage:ref(null),autoImage:ref(true),videoRequired:ref(false),selectedVideo:ref(null),
    scheduleEnabled:ref(false),publishAt:ref(''),riskAcknowledged:ref(true),imageUploading:ref(false),videoUploading:ref(false),dispatching:ref(false),autoExecuteAtDue:ref(false)}
  const state=new Function(...Object.keys(values),`${['hasExhaustedQuota','canDispatch','dispatchButtonLabel'].map(name=>`const ${name}=${expression(name)};`).join('\n')};return {hasExhaustedQuota,canDispatch,dispatchButtonLabel};`)(...Object.values(values))
  assert.equal(state.hasExhaustedQuota.value,true);assert.equal(state.canDispatch.value,false)
  assert.match(state.dispatchButtonLabel.value,/24h 建议上限/)
  assert.match(descriptor.template.content,/v-if="hasExhaustedQuota"[^>]*>24h额度按本系统已有任务预留；重试请使用下方原任务，真实发布仍需再次确认。/)
  assert.match(descriptor.template.content,/job\.demo \? .*'重新检查 \/ 排队'/)
})

test('the minimally changed distribution template compiles',()=>{
  assert.deepEqual(compileTemplate({source:descriptor.template.content,filename:filename.pathname,id:'distribution-retry-test',compilerOptions:{bindingMetadata:compiled.bindings}}).errors,[])
})
