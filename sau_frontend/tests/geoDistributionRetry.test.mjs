import assert from 'node:assert/strict'
import fs from 'node:fs'
import test from 'node:test'
import { computed, effectScope, reactive, ref, watch } from 'vue'
import { parse, compileScript, compileTemplate } from '@vue/compiler-sfc'
import * as state from '../src/utils/geoDistributionState.js'

const filename=new URL('../src/views/DistributionCenter.vue',import.meta.url)
const {descriptor,errors}=parse(fs.readFileSync(filename,'utf8'),{filename:filename.pathname})
assert.deepEqual(errors,[])
const compiled=compileScript(descriptor,{id:'distribution-state-test'})
const source=descriptor.scriptSetup.content.replace(/^import .*$/gm,'')
const deferred=()=>{let resolve,reject;const promise=new Promise((done,fail)=>{resolve=done;reject=fail});return {promise,resolve,reject}}
const settle=async()=>{for(let i=0;i<8;i++)await Promise.resolve()}
const job=(changes={})=>({job_id:37,article_id:8,article_title:'合成测试文章',account_id:3,platform:'zhihu',status:'failed',demo:false,confirmation_snapshot:'a'.repeat(64),...changes})
const policy=(changes={})=>({allowed:true,reason_code:'allowed',reference_at:'2026-10-25T08:00:00Z',next_allowed_at:null,remaining_new:1,reserved_24h:0,safe_daily_new:1,...changes})
const overview=(params={},changes={})=>({reference_mode:params.publish_at?'scheduled':'now',reference_at:params.publish_at||'2026-10-25T08:00:00Z',execution_mode:'real',
  platforms:[],accounts:[{id:3,platform_key:'zhihu',status:'connected',publish_policy:policy({reference_at:params.publish_at||'2026-10-25T08:00:00Z'})}],...changes})

function harness({confirm=async()=>{},retry=async()=>({data:job({status:'queued'})}),execute=async()=>({data:job({status:'need_action',message:'请核对平台登录状态'})}),accounts=async params=>({data:overview(params)}),getJobs=async()=>({data:{items:[job()],pagination:{page:1,page_size:20,total:1,total_pages:1}}}),create=async payload=>({data:job({status:'queued',...payload})}),getArticle=async id=>({data:{id,project_id:1,status:'ready',tags:[],review_fingerprint:'b'.repeat(64)}}),images=async()=>({data:{items:[]}})}={}) {
  const calls=[],confirms=[],messages=[],unmount=[],mount=[],timers=[],scope=effectScope(),route=reactive({query:{}})
  const dependencies={computed,reactive,ref,watch,...state,useRoute:()=>route,useRouter:()=>({push(){},replace:async value=>{route.query=value.query||{}}}),
    onMounted:callback=>mount.push(callback),onBeforeUnmount:callback=>unmount.push(callback),
    window:{setTimeout:callback=>{timers.push(callback);return timers.length},clearTimeout(){}},
    publishApi:{getPlatforms:async()=>({data:[{key:'zhihu',name:'知乎',delivery_state:'verified',content_mode:'article',supports_auto_execute:true,safe_daily_new:1,min_interval_hours:6}]}),
      getJobs:async params=>{calls.push(['get-jobs',{...params}]);return getJobs(params)},
      retryJob:async id=>{calls.push(['retry',id]);return retry(id)},executeRealJob:async (id,token)=>{calls.push(['execute-real',id,token]);return execute(id,token)},
      executeDemoJob:async id=>{calls.push(['execute-demo',id]);return {data:job({demo:true,status:'success'})}},
      createJob:async payload=>{calls.push(['create',payload]);return create(payload)}},
    articleApi:{getArticles:async()=>({data:{items:[{id:8,project_id:1,status:'ready',title:'合成稿',tags:[],review_fingerprint:'b'.repeat(64)}]}}),getArticle,
      recommendImages:images,generateCover:async()=>({data:{file_path:'cover.png',filename:'封面'}})},
    accountApi:{getMediaAccounts:async params=>{calls.push(['accounts',{...params}]);return accounts(params)}},
    materialApi:{getMaterialPreviewUrl:value=>`/preview/${value}`},
    ElMessageBox:{confirm:async(...args)=>{confirms.push(args);return confirm(...args)}},
    ElMessage:Object.fromEntries(['success','warning','info','error'].map(kind=>[kind,message=>messages.push({kind,message})])),console:{error(){}}}
  const page=scope.run(()=>new Function(...Object.keys(dependencies),source+`;return {jobs,jobFilters,jobPagination,jobsLoading,jobsRefreshing,jobsLoadError,lastSyncedAt,
    selectedArticleId,articles,selectedPlatforms,accountSelections,accountOverview,accountsStatus,quotaScopeKey,accountsLoadedKey,quotaView,quotaBlock,hasExhaustedQuota,
    scheduleEnabled,publishAt,autoExecuteAtDue,riskAcknowledged,dispatching,dispatchError,canDispatch,dispatchButtonLabel,selectedImage,selectedVideo,busyJobIds,jobActionErrors,
    fetchJobs,fetchAccounts,fetchArticles,dispatchArticle,executeRealJob,retryJob,checkPlatformResult,handleScheduleToggle,formatDate,selectAccount:setAccountSelection};`)(...Object.values(dependencies)))
  page.jobs.value=[job()]
  return {...page,calls,confirms,messages,route,timers,mount:async()=>{for(const open of mount)await open();await settle()},
    ready:async()=>{for(const open of mount)await open();page.selectedArticleId.value=8;page.selectedPlatforms.value=['zhihu'];await settle();page.riskAcknowledged.value=true},
    stop:()=>{for(const close of unmount)close();scope.stop()}}
}

test('actual full SFC real retry requeues only the original job and never implicitly executes it',async()=>{
  const page=harness();try{await page.retryJob(job());assert.deepEqual(page.calls.filter(value=>!['get-jobs'].includes(value[0])),[['retry',37]])
    assert.equal(page.confirms.length,0);assert.match(page.messages[0].message,/重新排队.*真实发布仍需再次确认/);assert.equal(page.busyJobIds.value.has(37),false)
  }finally{page.stop()}
})

test('retry and execution latch before waiting; cancellation sends nothing and releases the job lock',async()=>{
  const pending=deferred(),page=harness({retry:()=>pending.promise})
  try{const first=page.retryJob(job());await page.retryJob(job());assert.equal(page.calls.filter(value=>value[0]==='retry').length,1)
    pending.resolve({data:job({status:'queued'})});await first;assert.equal(page.busyJobIds.value.has(37),false)
  }finally{page.stop()}
  const decision=deferred(),executing=harness({confirm:()=>decision.promise})
  try{executing.jobs.value=[job({status:'queued'})];const first=executing.executeRealJob(job({status:'queued'}));await executing.executeRealJob(job({status:'queued'}))
    assert.equal(executing.confirms.length,1);decision.reject('cancel');await first
    assert.equal(executing.calls.filter(value=>value[0]==='execute-real').length,0);assert.deepEqual(executing.messages,[]);assert.equal(executing.busyJobIds.value.has(37),false)
  }finally{executing.stop()}
})

test('failed actions retain a readable inline explanation without claiming the platform was not submitted',async()=>{
  for(const action of ['executeRealJob','retryJob']){
    const fail=async()=>{throw Error('Playwright raw private exception')},page=harness({execute:fail,retry:fail})
    try{await page[action](job());assert.equal(page.busyJobIds.value.has(37),false)
      assert.match(page.jobActionErrors.value[37],/尚未确认/);assert.doesNotMatch(page.jobActionErrors.value[37],/Playwright|private|尚未提交/)
      assert.equal(page.messages.some(value=>value.kind==='success'),false)
    }finally{page.stop()}
  }
})

test('a changed task or route while confirmation is open cannot execute the old task',async()=>{
  for(const change of [page=>{page.jobs.value=[job({status:'processing'})]},page=>{page.route.query={journey_project:'2',article_id:'9',platform:'zhihu'}}]){
    const decision=deferred(),page=harness({confirm:()=>decision.promise})
    try{page.jobs.value=[job({status:'queued'})];const pending=page.executeRealJob(job({status:'queued'}));change(page);decision.resolve();await pending
      assert.equal(page.calls.filter(value=>value[0]==='execute-real').length,0)
    }finally{page.stop()}
  }
})

test('unmount during confirmation prevents execution and late retry acknowledgements cannot execute demos',async()=>{
  const decision=deferred(),page=harness({confirm:()=>decision.promise});const pending=page.executeRealJob(job());page.stop();decision.resolve();await pending
  assert.equal(page.calls.filter(value=>value[0]==='execute-real').length,0)
  const response=deferred(),retrying=harness({retry:()=>response.promise});retrying.jobs.value=[job({demo:true})]
  const retry=retrying.retryJob(job({demo:true}));retrying.stop();response.resolve({data:job({demo:true,status:'queued'})});await retry
  assert.equal(retrying.calls.filter(value=>value[0]==='execute-demo').length,0)
})

test('existing demo retry retains its explicit demo execution behavior',async()=>{
  const page=harness({retry:async()=>({data:job({demo:true,status:'queued'})})})
  try{page.jobs.value=[job({demo:true})];await page.retryJob(job({demo:true}));assert.deepEqual(page.calls.filter(value=>['retry','execute-demo'].includes(value[0])),[['retry',37],['execute-demo',37]])}
  finally{page.stop()}
})

test('quota legacy values and uncertain execution modes cannot authorize a real dispatch',async()=>{
  for(const change of [{execution_mode:undefined},{accounts:[{id:3,platform_key:'zhihu',status:'connected',publish_policy:{remaining_new:99}}]}]){
    const page=harness({accounts:async params=>({data:overview(params,change)})})
    try{await page.ready();assert.equal(page.canDispatch.value,false);assert.equal(page.quotaBlock.value.allowed,false)}finally{page.stop()}
  }
  const demo=harness({accounts:async params=>({data:overview(params,{execution_mode:'demo',accounts:[]})})})
  try{await demo.ready();assert.equal(demo.canDispatch.value,true);assert.match(demo.quotaView('zhihu').title,/演示.*不访问真实平台/)}finally{demo.stop()}
})

test('minimum interval, rolling cap and paused policies have distinct explanations and never permit new dispatch',async()=>{
  for(const [reason,text] of [['minimum_interval',/操作间隔/],['rolling_limit_reached',/24h.*上限/],['platform_delivery_paused',/暂停/]]){
    const page=harness({accounts:async params=>({data:overview(params,{accounts:[{id:3,platform_key:'zhihu',status:'connected',publish_policy:policy({allowed:false,reason_code:reason,remaining_new:0,next_allowed_at:'2026-10-26T08:00:00Z'})}]})})})
    try{await page.ready();assert.equal(page.canDispatch.value,false);assert.match(page.dispatchButtonLabel.value,text)
      assert.equal(page.quotaView('zhihu').nextAt,'2026-10-26T08:00:00Z')
    }finally{page.stop()}
  }
})

test('changing a planned time fetches its ISO reference, pauses dispatch and rejects stale A to B to A quota responses',async()=>{
  const responses=[],page=harness({accounts:params=>{const response=deferred();responses.push({params,...response});return response.promise}})
  try{
    page.scheduleEnabled.value=true;page.publishAt.value='2026-11-01T10:00:00';const first=page.fetchAccounts()
    page.publishAt.value='2026-11-02T10:00:00';const second=page.fetchAccounts()
    page.publishAt.value='2026-11-01T10:00:00';const third=page.fetchAccounts()
    assert.equal(page.quotaView('zhihu').allowed,false);assert.match(page.quotaView('zhihu').title,/正在核对/)
    assert.equal(responses[2].params.publish_at,new Date('2026-11-01T10:00:00').toISOString())
    responses[2].resolve({data:overview(responses[2].params)});await third
    responses[0].resolve({data:overview(responses[0].params,{accounts:[]})});await first
    responses[1].resolve({data:overview(responses[1].params,{accounts:[]})});await second
    assert.equal(page.accountOverview.value.accounts.length,1);assert.equal(page.accountsStatus.value,'ready')
    page.scheduleEnabled.value=false;const now=page.fetchAccounts();assert.deepEqual(responses[3].params,{})
    responses[3].resolve({data:overview()});await now
  }finally{page.stop()}
})

test('a quota read failure or mismatched response disables creation while retaining the original task',async()=>{
  let fail=false,wrong=false;const page=harness({accounts:async params=>{if(fail)throw Error('offline');return {data:overview(params,wrong?{reference_mode:'now'}:{})}}})
  try{await page.ready();assert.equal(page.canDispatch.value,true);fail=true;await page.fetchAccounts()
    assert.equal(page.canDispatch.value,false);assert.match(page.quotaView('zhihu').title,/读取失败/);assert.equal(page.jobs.value[0].job_id,37)
    fail=false;wrong=true;page.scheduleEnabled.value=true;page.publishAt.value='2026-11-01T10:00:00';await settle()
    assert.equal(page.accountsStatus.value,'error');assert.equal(page.canDispatch.value,false)
  }finally{page.stop()}
})

test('page and filter request ABA cannot replace the latest job list or clear the active loading indicator',async()=>{
  const responses=[],page=harness({getJobs:params=>{const response=deferred();responses.push({params,...response});return response.promise}})
  try{const old=page.fetchJobs(1);const b=page.fetchJobs(2);const recent=page.fetchJobs(1)
    responses[0].resolve({data:{items:[job({job_id:1})]}});await old;assert.equal(page.jobsLoading.value,true)
    responses[2].resolve({data:{items:[job({job_id:3})]}});await recent
    responses[1].resolve({data:{items:[job({job_id:2})]}});await b
    assert.equal(page.jobs.value[0].job_id,3);assert.equal(page.jobsLoading.value,false)
  }finally{page.stop()}
})

test('a silent poll failure is visible, preserves old tasks and blocks mutation until a successful refresh',async()=>{
  let fail=true;const page=harness({getJobs:async()=>{if(fail)throw Error('offline');return {data:{items:[job()]}}}})
  try{await page.fetchJobs(true);assert.equal(page.jobsLoadError.value,true);assert.equal(page.jobs.value[0].job_id,37)
    await page.retryJob(job());assert.equal(page.calls.some(value=>value[0]==='retry'),false)
    fail=false;await page.fetchJobs(true);assert.equal(page.jobsLoadError.value,false);await page.retryJob(job());assert.equal(page.calls.some(value=>value[0]==='retry'),true)
  }finally{page.stop()}
})

test('unmount drops pending accounts and jobs responses instead of repopulating a departed page',async()=>{
  const quota=deferred(),jobs=deferred(),page=harness({accounts:()=>quota.promise,getJobs:()=>jobs.promise})
  const reading=page.fetchAccounts(),polling=page.fetchJobs(true);page.stop()
  quota.resolve({data:overview()});jobs.resolve({data:{items:[job({job_id:999})]}});await Promise.all([reading,polling])
  assert.equal(page.accountOverview.value.accounts.length,0);assert.equal(page.jobs.value[0].job_id,37)
})

test('a repeated automatic-schedule click opens one confirmation and changed inputs cancel the old authorization',async()=>{
  const decision=deferred(),page=harness({confirm:()=>decision.promise})
  try{await page.ready();page.scheduleEnabled.value=true;page.publishAt.value='2026-11-01T10:00:00';await settle();page.autoExecuteAtDue.value=true
    const pending=page.dispatchArticle();await page.dispatchArticle();assert.equal(page.confirms.length,1)
    page.publishAt.value='2026-11-02T10:00:00';decision.resolve();await pending
    assert.equal(page.calls.filter(value=>value[0]==='create').length,0);assert.equal(page.dispatching.value,false)
  }finally{page.stop()}
})

test('an explicit ordinary schedule uses the same ISO time for quota and immutable job payload',async()=>{
  const page=harness()
  try{await page.ready();page.scheduleEnabled.value=true;page.publishAt.value='2026-11-01T10:00:00';await settle();await page.dispatchArticle()
    const payload=page.calls.find(value=>value[0]==='create')?.[1]
    assert.ok(payload);assert.equal(payload.publish_at,new Date('2026-11-01T10:00:00').toISOString());assert.equal(payload.auto_execute,false)
  }finally{page.stop()}
})

test('full template compiles with keyboard-accessible full message expansion and persistent action errors',()=>{
  assert.deepEqual(compileTemplate({source:descriptor.template.content,filename:filename.pathname,id:'distribution-state-test',compilerOptions:{bindingMetadata:compiled.bindings}}).errors,[])
  assert.match(descriptor.template.content,/class="job-message-details"><summary>展开任务说明<\/summary>/)
  assert.match(descriptor.template.content,/jobActionErrors\[job.job_id\].*role="alert"/)
  assert.match(descriptor.template.content,/job.status==='queued' && job.started_at \? '上次尝试'/)
})

test('a route project A to B to A drops old quota, job and article responses despite identical final query values',async()=>{
  const quota=deferred(),jobs=deferred(),article=deferred(),page=harness({accounts:()=>quota.promise,getJobs:()=>jobs.promise,getArticle:()=>article.promise})
  try{
    page.route.query={journey_project:'1',article_id:'8',platform:'zhihu'}
    const reading=page.fetchAccounts(),polling=page.fetchJobs(true),loading=page.fetchArticles()
    page.route.query={journey_project:'2',article_id:'9',platform:'zhihu'}
    page.route.query={journey_project:'1',article_id:'8',platform:'zhihu'}
    quota.resolve({data:overview()});jobs.resolve({data:{items:[job({job_id:999})]}});article.resolve({data:{id:8,project_id:1,status:'ready'}})
    await Promise.all([reading,polling,loading])
    assert.equal(page.accountOverview.value.accounts.length,0);assert.deepEqual(page.jobs.value,[]);assert.deepEqual(page.articles.value,[])
  }finally{page.stop()}
})

test('the live plan-time watcher automatically refreshes quota and does not keep a previous remaining value',async()=>{
  const page=harness()
  try{await page.ready();const before=page.calls.filter(value=>value[0]==='accounts').length
    page.scheduleEnabled.value=true;assert.equal(page.canDispatch.value,false)
    page.publishAt.value='2026-11-01T10:00:00';assert.match(page.quotaView('zhihu').title,/正在核对/)
    await settle();const after=page.calls.filter(value=>value[0]==='accounts')
    assert.equal(after.length,before+1);assert.equal(after.at(-1)[1].publish_at,new Date('2026-11-01T10:00:00').toISOString())
    assert.equal(page.canDispatch.value,true)
  }finally{page.stop()}
})

test('old article image recommendations cannot appear under a newer article, including A to B to A',async()=>{
  const pending=[],page=harness({images:()=>{const response=deferred();pending.push(response);return response.promise}})
  try{page.selectedArticleId.value=8;page.selectedArticleId.value=9;page.selectedArticleId.value=8
    pending[2].resolve({data:{items:[{file_path:'new-a.png',filename:'新版素材'}]}});await settle()
    pending[0].resolve({data:{items:[{file_path:'old-a.png',filename:'旧版素材'}]}})
    pending[1].resolve({data:{items:[{file_path:'old-b.png',filename:'他人素材'}]}});await settle()
    assert.equal(page.selectedImage.value.filename,'new-a.png')
  }finally{page.stop()}
})

test('stopping a scheduled confirmation by unmount never creates a new task',async()=>{
  const decision=deferred(),page=harness({confirm:()=>decision.promise})
  await page.ready();page.scheduleEnabled.value=true;page.publishAt.value='2026-11-01T10:00:00';await settle();page.autoExecuteAtDue.value=true
  const pending=page.dispatchArticle();page.stop();decision.resolve();await pending
  assert.equal(page.calls.filter(value=>value[0]==='create').length,0)
})

test('quota projections reject contradictory or foreign-time decisions instead of guessing they are allowed',()=>{
  for(const invalid of [null,{remaining_new:9},policy({allowed:'true'}),policy({reason_code:'minimum_interval'}),
    policy({remaining_new:-1}),policy({allowed:false,reason_code:'unknown'}),policy({reference_at:'invalid'})]){
    assert.equal(state.distributionQuota(invalid).allowed,false)
  }
  assert.equal(state.distributionQuota(policy(),'2026-10-26T08:00:00Z').state,'unknown')
  assert.equal(state.distributionReference(true,'broken').valid,false)
  assert.equal(state.distributionOverviewMatches(overview(),state.distributionReference(true,'2026-11-01T10:00:00')),false)
})

test('account API forwards read-only reference parameters while a default overview remains an unscoped GET',async()=>{
  const apiSource=fs.readFileSync(new URL('../src/api/account.js',import.meta.url),'utf8').replace(/^import .*$/gm,'').replace('export const accountApi','const accountApi')
  const calls=[],api=new Function('http',apiSource+';return accountApi;')({get:(...args)=>{calls.push(args);return Promise.resolve()}})
  await api.getMediaAccounts();await api.getMediaAccounts({publish_at:'2026-11-01T02:00:00.000Z'})
  assert.deepEqual(calls,[['/api/media-accounts',{}],['/api/media-accounts',{publish_at:'2026-11-01T02:00:00.000Z'}]])
})

test('attempt time formatting preserves explicit UTC microseconds and treats legacy SQL fractions as UTC',()=>{
  const original=process.env.TZ, page=harness()
  try{
    process.env.TZ='Asia/Hong_Kong'
    const timestamp='2026-10-25T02:03:04.123456+00:00'
    const expected=new Intl.DateTimeFormat('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false,timeZone:'Asia/Hong_Kong'}).format(new Date(timestamp))
    assert.equal(page.formatDate(timestamp),expected)
    assert.equal(page.formatDate('2026-10-25 02:03:04.123456'),expected)
    assert.equal(page.formatDate('2026-10-25 02:03:04'),expected)
    assert.notEqual(page.formatDate('2026-10-25 02:03:04',false),expected)
  }finally{if(original===undefined)delete process.env.TZ;else process.env.TZ=original;page.stop()}
})

test('a queued retry with a new start, article title or attachment cannot inherit an earlier confirmation',async()=>{
  for(const change of [{started_at:'2026-10-25T12:00:00.000002+00:00',article_title:'第二版重试稿'},
    {images:['different.png']},{video:'different.mp4'},{publish_at:'2026-10-26T12:00:00Z'}, {auto_execute:true}]){
    const decision=deferred(),page=harness({confirm:()=>decision.promise})
    try{
      const original=job({status:'queued',started_at:'2026-10-24T12:00:00.000001+00:00',article_title:'第一版已审稿',images:[],video:''})
      page.jobs.value=[original];const pending=page.executeRealJob(page.jobs.value[0])
      page.jobs.value=[{...original,...change}];decision.resolve();await pending
      assert.equal(page.calls.filter(value=>value[0]==='execute-real').length,0)
    }finally{page.stop()}
  }
})

test('a same-object queued to processing to queued transition invalidates the old confirmation even when all final fields match',async()=>{
  const decision=deferred(),page=harness({confirm:()=>decision.promise})
  try{
    page.jobs.value=[job({status:'queued'})]
    const pending=page.executeRealJob(page.jobs.value[0])
    page.jobs.value[0].status='processing';page.jobs.value[0].status='failed';page.jobs.value[0].status='queued'
    decision.resolve();await pending
    assert.equal(page.calls.filter(value=>value[0]==='execute-real').length,0)
  }finally{page.stop()}
})

test('unchanged polling keeps a pending confirmation valid while changed and restored ready-article content cancels create authorization',async()=>{
  const decision=deferred(),page=harness({confirm:()=>decision.promise})
  try{
    page.jobs.value=[job({status:'queued'})];const pending=page.executeRealJob(page.jobs.value[0])
    page.jobs.value=[job({status:'queued'})];decision.resolve();await pending
    assert.equal(page.calls.filter(value=>value[0]==='execute-real').length,1)
  }finally{page.stop()}
  for(const restore of [false,true]){
    const decision=deferred(),creating=harness({confirm:()=>decision.promise})
    try{
      await creating.ready();creating.articles.value[0].content='第一版已审正文'
      creating.scheduleEnabled.value=true;creating.publishAt.value='2026-11-01T10:00:00';await settle();creating.autoExecuteAtDue.value=true
      const pending=creating.dispatchArticle();creating.articles.value[0].content='第二版不同正文'
      if(restore)creating.articles.value[0].content='第一版已审正文'
      decision.resolve();await pending
      assert.equal(creating.calls.filter(value=>value[0]==='create').length,0)
    }finally{creating.stop()}
  }
})

test('real dispatch and execution serialize the exact server snapshots observed before confirmation',async()=>{
  const page=harness()
  try{
    await page.ready();await page.dispatchArticle()
    const payload=page.calls.find(value=>value[0]==='create')?.[1]
    assert.equal(payload.expected_review_fingerprint,'b'.repeat(64))
    page.jobs.value=[job({status:'queued'})];await page.executeRealJob(page.jobs.value[0])
    assert.equal(page.calls.find(value=>value[0]==='execute-real')?.[2],'a'.repeat(64))
  }finally{page.stop()}
  const apiSource=fs.readFileSync(new URL('../src/api/publish.js',import.meta.url),'utf8').replace(/^import .*$/gm,'').replace('export const publishApi','const publishApi')
  const calls=[],api=new Function('http',apiSource+';return publishApi;')({post:(...args)=>{calls.push(args);return Promise.resolve()}})
  await api.executeRealJob(37,'c'.repeat(64));await api.createJob({article_id:8,expected_review_fingerprint:'d'.repeat(64)})
  assert.deepEqual(calls,[['/api/publish/jobs/37/execute-real',{confirm:true,expected_confirmation_snapshot:'c'.repeat(64)}],
    ['/api/publish',{article_id:8,expected_review_fingerprint:'d'.repeat(64)}]])
})

test('missing real snapshots require a refresh without a POST, while explicit demo creation remains token-free',async()=>{
  const page=harness()
  try{
    await page.ready();delete page.articles.value[0].review_fingerprint
    assert.equal(page.canDispatch.value,false);assert.match(page.dispatchButtonLabel.value,/重新读取.*稿件/)
    await page.dispatchArticle();assert.equal(page.calls.some(value=>value[0]==='create'),false)
    page.jobs.value=[job({status:'queued',confirmation_snapshot:undefined})]
    await page.executeRealJob(page.jobs.value[0]);assert.equal(page.confirms.length,0)
    assert.equal(page.calls.some(value=>value[0]==='execute-real'),false)
    assert.match(page.jobActionErrors.value[37],/刷新任务列表.*重新核对/)
  }finally{page.stop()}
  const demo=harness({accounts:async params=>({data:overview(params,{execution_mode:'demo',accounts:[]})}),create:async payload=>({data:job({...payload,demo:true,status:'queued'})})})
  try{
    await demo.ready();delete demo.articles.value[0].review_fingerprint;await demo.dispatchArticle()
    const payload=demo.calls.find(value=>value[0]==='create')?.[1]
    assert.ok(payload);assert.equal(Object.hasOwn(payload,'expected_review_fingerprint'),false)
  }finally{demo.stop()}
})

test('a snapshot conflict keeps the original job and provides inline guidance without retrying or creating another task',async()=>{
  const conflict=async()=>{throw {response:{status:409,data:{msg:'任务确认后已更新，请重新核对'}}}}
  const page=harness({execute:conflict})
  try{
    page.jobs.value=[job({status:'queued'})];await page.executeRealJob(page.jobs.value[0])
    assert.equal(page.jobs.value[0].job_id,37);assert.equal(page.jobs.value[0].status,'queued')
    assert.match(page.jobActionErrors.value[37],/刷新原任务和稿件.*重新核对/)
    assert.equal(page.calls.filter(value=>value[0]==='execute-real').length,1)
    assert.equal(page.calls.some(value=>['retry','create'].includes(value[0])),false)
  }finally{page.stop()}
  const creating=harness({create:conflict})
  try{
    await creating.ready();await creating.dispatchArticle();await creating.dispatchArticle()
    assert.equal(creating.calls.filter(value=>value[0]==='create').length,1)
    assert.match(creating.dispatchError.value,/稿件.*已更新.*重新读取.*不会自动重试/)
  }finally{creating.stop()}
})

test('a changed server snapshot alone cancels pending task or article confirmation and never substitutes a newer token',async()=>{
  const decision=deferred(),page=harness({confirm:()=>decision.promise})
  try{
    page.jobs.value=[job({status:'queued'})];const pending=page.executeRealJob(page.jobs.value[0])
    page.jobs.value[0].confirmation_snapshot='e'.repeat(64);decision.resolve();await pending
    assert.equal(page.calls.some(value=>value[0]==='execute-real'),false)
  }finally{page.stop()}
  const review=deferred(),creating=harness({confirm:()=>review.promise})
  try{
    await creating.ready();creating.scheduleEnabled.value=true;creating.publishAt.value='2026-11-01T10:00:00';await settle();creating.autoExecuteAtDue.value=true
    const pending=creating.dispatchArticle();creating.articles.value[0].review_fingerprint='f'.repeat(64)
    review.resolve();await pending;assert.equal(creating.calls.some(value=>value[0]==='create'),false)
  }finally{creating.stop()}
})
