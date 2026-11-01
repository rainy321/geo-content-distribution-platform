import assert from 'node:assert/strict'
import fs from 'node:fs'
import test from 'node:test'
import { computed,effectScope,nextTick,reactive,ref,watch } from 'vue'
import { parse,compileScript,compileStyle,compileTemplate } from '@vue/compiler-sfc'
import * as registration from '../src/utils/geoBuiltinPublication.js'
import { publicationReviewArticles,publicationReviewAvailable } from '../src/utils/geoPublicationReview.js'

const hash='a'.repeat(64),review='b'.repeat(64),confirmation='c'.repeat(64)
const publicUrl='https://zhuanlan.zhihu.com/p/2100208460726116757'
const jobRow=(changes={})=>({id:39,platform:'zhihu',status:'need_action',demo:0,result_url:'',finished_at:'2026-11-01 11:00:00',...changes})
const draft=(changes={})=>({article_id:12,platform:'zhihu',title:'合成测试稿件',content:'已审稿的全文',tags:['测试'],status:'ready',article_sha256:hash,jobs:[jobRow()],...changes})
const cycle=(changes={})=>({id:'cycle-a',project_id:5,drafts:[draft()],reported_publications:[],publications:[],moderation_observations:[],blocked_publications:[],...changes})
const article=(changes={})=>({id:12,project_id:5,title:'合成测试稿件',content:'已审稿的全文',tags:['测试'],status:'ready',review_fingerprint:review,...changes})
const job=(changes={})=>({job_id:39,article_id:12,account_id:3,account_name:'测试账号',platform:'zhihu',status:'need_action',demo:false,started_at:'2026-11-01 10:50:00',authorization_bound:true,confirmation_snapshot:confirmation,...changes})
const form=(changes={})=>({url:publicUrl,publishedAt:'2026-11-01T19:40',note:'  平台显示发布成功，未检查访客视角  ',confirmed:true,...changes})
const capture=current=>registration.captureBuiltinPublicationSelection(current,12,39)
const snapshot=current=>registration.createBuiltinPublicationSnapshot(capture(current),current,article(),job())
const saved=(current,payload,changes={})=>({...current,reported_publications:[{id:'record-a',kind:'builtin_job',article_id:12,job_id:39,account_id:3,
  platform:'zhihu',target_id:null,article_sha256:hash,job_confirmation_snapshot:confirmation,url:payload.url,published_at:'2026-11-01T11:40+00:00',published_at_precision:'minute',published_at_basis:'operator_reported_platform_time',
  evidence_level:'owner_reported',note:payload.note,review_fingerprint:review,...changes}]})
const deferred=()=>{let resolve,reject;const promise=new Promise((done,fail)=>{resolve=done;reject=fail});return {promise,resolve,reject}}

test('only this cycle ready Zhihu articles and attempted real stopped jobs are candidates',()=>{
  const current=cycle({drafts:[draft(),draft({article_id:13,status:'draft'}),draft({article_id:14,platform:'target-a'}),draft({article_id:15,platform:'toutiao'}),
    draft({article_id:16,jobs:[jobRow({id:40,demo:1}),jobRow({id:41,status:'processing'}),jobRow({id:42,status:'queued'}),jobRow({id:43,status:'scheduled'}),jobRow({id:44,status:'success'})]}),
    draft({article_id:17,article_sha256:undefined}),draft({article_id:18,status:'published',jobs:[jobRow({id:45,status:'failed'})]})]})
  assert.deepEqual(registration.builtinPublicationArticles(current).map(item=>[item.id,item.supported]),[[12,true],[16,false],[17,false],[18,true]])
  assert.deepEqual(registration.builtinPublicationArticles(current)[0].jobs.map(item=>item.id),[39])
  assert.throws(()=>registration.captureBuiltinPublicationSelection(current,16,40),/请选择/)
})

test('loading confirmation freezes project, cycle, content version and the original task',()=>{
  const current=cycle(),selection=capture(current),opened=snapshot(current)
  assert.equal(Object.isFrozen(selection),true);assert.equal(Object.isFrozen(opened),true)
  assert.equal(opened.accountId,3);assert.equal(opened.expectedReviewFingerprint,review)
  for(const changed of [cycle({id:'cycle-b'}),cycle({project_id:6})])assert.match(registration.builtinPublicationSnapshotIssue(opened,changed),/切换品牌/)
  for(const changed of [cycle({drafts:[draft({content:'修改稿'})]}),cycle({drafts:[draft({jobs:[jobRow({status:'processing'})]})]})]){
    assert.match(registration.builtinPublicationSnapshotIssue(opened,changed),/已变化/)
  }
  for(const value of [article({project_id:6}),article({content:'不同全文'}),article({review_fingerprint:undefined}),article({status:'draft'}),article({tags:[]})]){
    assert.throws(()=>registration.createBuiltinPublicationSnapshot(selection,current,value,job()),/稿件核对信息/)
  }
  for(const value of [job({demo:true}),job({started_at:null}),job({authorization_bound:false}),job({account_id:null}),job({job_id:40}),
    job({article_id:13}),job({platform:'toutiao'}),job({status:'processing'}),job({confirmation_snapshot:undefined})]){
    assert.throws(()=>registration.createBuiltinPublicationSnapshot(selection,current,article(),value),/真实原任务/)
  }
})

test('canonical Zhihu article IDs stay exact strings and minute precision never invents seconds',()=>{
  assert.equal(registration.builtinPublicationUrl(publicUrl),publicUrl)
  for(const value of ['https://zhuanlan.zhihu.com/p/2100208460726116757/edit',publicUrl+'?source=test',publicUrl+'#body',publicUrl+'/',
    'https://www.zhihu.com/question/1','http://zhuanlan.zhihu.com/p/1','https://user:password@zhuanlan.zhihu.com/p/1','https://zhuanlan.zhihu.com:443/p/1',
    'https://zhuanlan.zhihu.com/p/0','https://zhuanlan.zhihu.com/p/01','https://zhuanlan.zhihu.com/p/1e20','https://zhuanlan.zhihu.com/p/NaN'])assert.equal(registration.builtinPublicationUrl(value),'')
  const current=cycle(),payload=registration.buildBuiltinPublicationPayload(snapshot(current),form(),current)
  assert.equal(payload.url,publicUrl);assert.equal(payload.published_at,'2026-11-01T19:40+08:00');assert.equal(payload.published_at_precision,'minute')
  assert.equal(Object.isFrozen(payload),true);assert.equal(payload.note,'平台显示发布成功，未检查访客视角')
  assert.equal(registration.builtinPublicationMinute('2028-02-29T19:40'),'2028-02-29T19:40+08:00')
  for(const value of ['0000-11-01T19:40','2026-02-29T19:40','2026-11-31T19:40','2026-11-01T24:00','2026-11-01T19:40:00','2026-11-01T19:40Z'])assert.equal(registration.builtinPublicationMinute(value),'')
  for(const value of [form({confirmed:false}),form({confirmed:'true'}),form({note:'x'.repeat(1001)}),form({publishedAt:''}),form({url:publicUrl+'/edit'})]){
    assert.throws(()=>registration.buildBuiltinPublicationPayload(snapshot(current),value,current))
  }
  assert.deepEqual(Object.keys(payload),['kind','article_id','job_id','account_id','expected_review_fingerprint','expected_confirmation_snapshot','url','published_at','published_at_precision','confirmed','note'])
})

test('only the normal registration API is called and acknowledgement binds the unchanged original task',async()=>{
  const current=cycle(),opened=snapshot(current),payload=registration.buildBuiltinPublicationPayload(opened,form(),current),calls=[]
  const api={publication:async(...args)=>{calls.push(args);return {data:saved(current,payload)}},createJob:()=>assert.fail('must not publish'),retryJob:()=>assert.fail('must not retry')}
  const result=await registration.saveBuiltinPublicationAttempt(api,opened,payload)
  assert.deepEqual(calls,[['cycle-a',payload]])
  assert.deepEqual(result.publications,[]);assert.equal(result.drafts[0].jobs[0].status,'need_action');assert.equal(result.drafts[0].jobs[0].result_url,'')
  for(const changed of [{id:null},{article_id:13},{job_id:40},{account_id:4},{platform:'toutiao'},{target_id:'target-a'},
    {review_fingerprint:'d'.repeat(64)},{job_confirmation_snapshot:'d'.repeat(64)}, {evidence_level:'publisher_reported_success'},
    {published_at:'2026-11-01T11:41+00:00'},{published_at_precision:'second'},{url:publicUrl+'/edit'},{note:'不同备注'}]){
    await assert.rejects(registration.saveBuiltinPublicationAttempt({publication:async()=>({data:saved(current,payload,changed)})},opened,payload),/尚未确认/)
  }
  assert.equal(registration.builtinPublicationResultMatchesScope(opened,opened,current,result),true)
  assert.equal(registration.builtinPublicationResultMatchesScope(opened,opened,cycle({project_id:6}),result),false)
  assert.equal(registration.builtinPublicationResultMatchesScope(opened,snapshot(current),current,result),false)
})

const filename=new URL('../src/components/GeoBuiltinPublicationRegistration.vue',import.meta.url)
const {descriptor,errors}=parse(fs.readFileSync(filename,'utf8'),{filename:filename.pathname})
assert.deepEqual(errors,[])
const compiled=compileScript(descriptor,{id:'builtin-publication-test'})
let source=descriptor.scriptSetup.content
for(const node of compiled.scriptSetupAst.filter(node=>node.type==='ImportDeclaration').reverse())source=source.slice(0,node.start)+source.slice(node.end)
function harness({getArticle=async()=>({data:article()}),getJob=async()=>({data:job()}),publication}={}){
  const props=reactive({cycle:cycle(),busy:false}),emitted=[],calls=[],unmount=[],scope=effectScope()
  let page
  const deps={...registration,computed,reactive,ref,watch,onBeforeUnmount:callback=>unmount.push(callback),defineProps:()=>props,defineEmits:()=>((...args)=>emitted.push(args)),
    articleApi:{getArticle:async id=>{calls.push(['article',id]);return getArticle(id)}},publishApi:{getJob:async id=>{calls.push(['job',id]);return getJob(id)}},
    geoContentApi:{publication:async(...args)=>{calls.push(['publication',...args]);return publication?publication(...args):{data:saved(props.cycle,args[1])}}}}
  page=scope.run(()=>new Function(...Object.keys(deps),source+`;return {visible,loading,saving,snapshot,attemptPayload,saveError,selectedArticleId,selectedJobId,form,canSave,
    openRegistration,changeArticle,changeJob,saveRegistration,dialogClosed,platformMinute};`)(...Object.values(deps)))
  return {...page,props,calls,emitted,stop:()=>{for(const close of unmount)close();scope.stop()},fill:()=>Object.assign(page.form,form())}
}

test('actual component loads only current article/task reads then saves once through registration',async()=>{
  let page
  page=harness({publication:async(id,payload)=>({data:saved(page.props.cycle,payload)})})
  try {
    await page.openRegistration();assert.deepEqual(page.calls,[['article',12],['job',39]])
    assert.equal(page.canSave.value,false);page.fill();await page.saveRegistration()
    assert.equal(page.emitted.length,1);assert.equal(page.visible.value,false)
    assert.deepEqual(page.calls.map(call=>call[0]),['article','job','publication'])
    assert.equal(page.calls[2][2].url,publicUrl);assert.equal(page.calls[2][2].published_at,'2026-11-01T19:40+08:00')
    assert.match(page.platformMinute('2026-11-01T11:40+00:00'),/19:40$/)
  } finally {page.stop()}
})

test('actual component blocks duplicate saves and retries uncertain response with the identical payload',async()=>{
  let count=0,page
  const response=deferred()
  page=harness({publication:async(id,payload)=>{if(++count===1)return response.promise;return {data:saved(page.props.cycle,payload)}}})
  try {
    await page.openRegistration();page.fill();page.props.busy=true;await page.saveRegistration();assert.equal(count,0)
    page.props.busy=false;const first=page.saveRegistration();await page.saveRegistration();assert.equal(count,1)
    response.reject(new Error('Network Error'));await first
    assert.match(page.saveError.value,/保存结果待确认/);const payload=page.attemptPayload.value
    page.form.url='https://zhuanlan.zhihu.com/p/999';page.form.publishedAt='2026-11-01T20:00'
    await page.saveRegistration();assert.equal(count,2)
    const calls=page.calls.filter(call=>call[0]==='publication');assert.equal(calls[0][2],payload);assert.equal(calls[1][2],payload)
    assert.equal(calls[1][2].url,publicUrl);assert.equal(page.emitted.length,1)
  } finally {page.stop()}
})

test('project/cycle A to B to A while loading discards late confirmation reads',async()=>{
  const response=deferred(),page=harness({getArticle:()=>response.promise})
  try {
    const opening=page.openRegistration();assert.equal(page.loading.value,true)
    page.props.cycle=cycle({id:'cycle-b',project_id:6});page.props.cycle=cycle()
    response.resolve({data:article()});await opening;await nextTick()
    assert.equal(page.visible.value,false);assert.equal(page.snapshot.value,null);assert.equal(page.emitted.length,0)
    assert.equal(page.calls.filter(call=>call[0]==='publication').length,0)
  } finally {page.stop()}
})

test('same-cycle content changes while reading confirmation require another deliberate review',async()=>{
  const response=deferred(),page=harness({getArticle:()=>response.promise})
  try {
    const opening=page.openRegistration();page.props.cycle=cycle({drafts:[draft({content:'读取时有后续修改'})]})
    response.resolve({data:article()});await opening
    assert.equal(page.visible.value,true);assert.equal(page.snapshot.value,null);assert.match(page.saveError.value,/已变化/)
    page.fill();assert.equal(page.canSave.value,false)
  } finally {page.stop()}
})

test('choosing another original task reads that exact task and freezes its identity',async()=>{
  const page=harness({getJob:async id=>({data:job({job_id:id,status:id===40?'failed':'need_action'})})})
  try {
    page.props.cycle=cycle({drafts:[draft({jobs:[jobRow(),jobRow({id:40,status:'failed'})]})]})
    await page.openRegistration();assert.equal(page.snapshot.value.jobId,39)
    await page.changeJob(40);assert.equal(page.snapshot.value.jobId,40);assert.equal(page.form.confirmed,false)
    assert.deepEqual(page.calls.filter(call=>call[0]==='job'),[['job',39],['job',40]])
    page.fill();assert.equal(registration.buildBuiltinPublicationPayload(page.snapshot.value,page.form,page.props.cycle).job_id,40)
  } finally {page.stop()}
})

test('late save replies after project switch, reopen or unmount cannot update another view',async()=>{
  for(const action of ['switch','unmount']){
    const response=deferred(),page=harness({publication:()=>response.promise})
    try {
      await page.openRegistration();page.fill();const openedCycle=page.props.cycle,pending=page.saveRegistration(),payload=page.attemptPayload.value
      if(action==='switch'){
        page.props.cycle=cycle({id:'cycle-b',project_id:6});page.props.cycle=cycle();await page.openRegistration()
        assert.equal(page.visible.value,true);assert.equal(page.saving.value,false)
      } else page.stop()
      response.resolve({data:saved(openedCycle,payload)});await pending
      assert.equal(page.emitted.length,0)
      if(action==='switch')assert.equal(page.visible.value,true)
    } finally {page.stop()}
  }
})

test('same-cycle refreshed content blocks a saved acknowledgement and never replaces captured version',async()=>{
  const response=deferred(),page=harness({publication:()=>response.promise})
  try {
    await page.openRegistration();page.fill();const opened=page.snapshot.value,pending=page.saveRegistration(),payload=page.attemptPayload.value
    page.props.cycle=cycle({drafts:[draft({content:'后来改稿'})]})
    response.resolve({data:saved(cycle(),payload)});await pending
    assert.equal(page.snapshot.value,opened);assert.equal(page.emitted.length,0);assert.match(page.saveError.value,/已保存.*已变化/)
  } finally {page.stop()}
})

test('target workflow stays separate and both forms compile with explicit unverified labels',()=>{
  const current=cycle({drafts:[draft(),{article_id:21,title:'目标平台稿',article_sha256:hash,provenance:{destination:{id:'target-a'}}}],destinations:[{id:'target-a',config:{name:'目标平台'}}]})
  assert.deepEqual(publicationReviewArticles(current).map(item=>item.id),[21]);assert.equal(publicationReviewAvailable(current),true)
  assert.deepEqual(registration.builtinPublicationArticles(current).map(item=>item.id),[12])
  assert.deepEqual(compileTemplate({source:descriptor.template.content,filename:filename.pathname,id:'builtin-publication-test'}).errors,[])
  for(const style of descriptor.styles)assert.deepEqual(compileStyle({source:style.content,filename:filename.pathname,id:'builtin-publication-test',scoped:true}).errors,[])
  assert.match(descriptor.template.content,/用户登记，未独立核验/);assert.match(descriptor.template.content,/北京时间，精确到分钟/)
  const renderedText=descriptor.template.content.replace(/<[^>]*>/g,'').replace(/\{\{[\s\S]*?\}\}/g,'')
  assert.doesNotMatch(renderedText,/fingerprint|snapshot|sha256|API|UUID/)
  const parent=fs.readFileSync(new URL('../src/components/GeoPublicationReview.vue',import.meta.url),'utf8')
  assert.match(parent,/<GeoBuiltinPublicationRegistration/);assert.match(parent,/@click="saveObservation"/)
  assert.doesNotMatch(source,/createJob|retryJob|executeRealJob|executeDemoJob|checkResult/)
})

test('historical user registrations remain visible when the article is later edited or becomes unready',()=>{
  const current=cycle(),opened=snapshot(current),payload=registration.buildBuiltinPublicationPayload(opened,form(),current)
  const changed=saved(cycle({drafts:[draft({status:'draft',content:'后续改稿',article_sha256:'d'.repeat(64)})]}),payload)
  assert.deepEqual(registration.builtinPublicationArticles(changed),[])
  const records=registration.builtinPublicationRecords(changed)
  assert.equal(records.length,1);assert.equal(records[0].url,publicUrl);assert.equal(records[0].jobId,39);assert.equal(records[0].olderContent,true)
})
