import assert from 'node:assert/strict'
import fs from 'node:fs'
import test from 'node:test'
import { parse,compileScript,compileTemplate,compileStyle } from '@vue/compiler-sfc'
import { computed,reactive,ref,watch,nextTick } from 'vue'
import { buildPublicationReviewPayload,createPublicationReviewSnapshot,newPublicationObservationId,
  publicationObservationViews,publicationReviewArticles,publicationReviewAvailable,publicationReviewError,
  publicationReviewResultMatchesScope,publicationReviewSnapshotIssue,publicationReviewUrl,savePublicationReviewAttempt } from '../src/utils/geoPublicationReview.js'

const hash='a'.repeat(64),changedHash='b'.repeat(64),id='c'.repeat(32)
const cycle=(overrides={})=>({id:'cycle-a',drafts:[{article_id:5,title:'技术稿件',article_sha256:hash,provenance:{destination:{id:'target-a',revision:3}}}],
  destinations:[{id:'target-a',config:{name:'示例平台'}}],moderation_observations:[],blocked_publications:[],...overrides})
const form=(overrides={})=>({state:'rejected',note:'平台提示：广告营销',url:'',confirmed:true,contentConfirmed:false,...overrides})
const record=(snapshot,payload)=>({id:snapshot.observationId,article_id:snapshot.articleId,target_id:snapshot.targetId,state:payload.state,
  article_sha256:payload.expected_article_sha256,source:'operator_reported',content_confirmed:payload.content_confirmed===true,note:payload.note,url:payload.url||''})
const resultCycle=(snapshot,payload)=>cycle({moderation_observations:[record(snapshot,payload)]})

test('only this cycle target drafts with current content support review; old backends need an upgrade',()=>{
  const current=cycle()
  assert.equal(publicationReviewAvailable(current),true)
  assert.equal(publicationReviewArticles(current)[0].supported,true)
  for(const value of [cycle({moderation_observations:undefined}),cycle({blocked_publications:undefined}),
    cycle({drafts:[{article_id:5,title:'旧版本稿件',provenance:{destination:{id:'target-a'}}}]}),
    cycle({destinations:[{id:'target-b'}]})])assert.equal(publicationReviewAvailable(value),false)
  assert.deepEqual(publicationReviewArticles(cycle({destinations:[]})),[])
  assert.throws(()=>createPublicationReviewSnapshot(cycle({moderation_observations:undefined}),5,id),/升级/)
})

test('an opened article version is immutable and does not silently follow a prop refresh',()=>{
  const current=cycle(),snapshot=createPublicationReviewSnapshot(current,5,id)
  assert.equal(Object.isFrozen(snapshot),true)
  current.drafts[0].article_sha256=changedHash
  assert.equal(snapshot.expectedArticleSha256,hash)
  assert.match(publicationReviewSnapshotIssue(snapshot,current),/内容已更新/)
  assert.throws(()=>buildPublicationReviewPayload(snapshot,form(),current),/内容已更新/)
  assert.throws(()=>buildPublicationReviewPayload(snapshot,form(),cycle({id:'cycle-b'})),/切换品牌/)
  const changedTarget=cycle({drafts:[{article_id:5,article_sha256:hash,provenance:{destination:{id:'target-b'}}}],destinations:[{id:'target-b'}]})
  assert.match(publicationReviewSnapshotIssue(snapshot,changedTarget),/目标平台已变化/)
})

test('a deliberate article choice keeps the operation identity while capturing only the selected version',()=>{
  const current=cycle()
  current.drafts.push({article_id:6,title:'另一稿',article_sha256:changedHash,provenance:{destination:{id:'target-a'}}})
  const first=createPublicationReviewSnapshot(current,5,id)
  const selected=createPublicationReviewSnapshot(current,6,first.observationId)
  assert.equal(selected.observationId,first.observationId)
  assert.equal(selected.expectedArticleSha256,changedHash)
  assert.equal(first.expectedArticleSha256,hash)
})

test('public visibility requires an actual article link, explicit content match and platform check',()=>{
  const current=cycle(),snapshot=createPublicationReviewSnapshot(current,5,id)
  for(const value of [form({confirmed:false}),form({confirmed:'true'}),form({state:'publicly_visible'}),
    form({state:'publicly_visible',url:'https://example.com/article',contentConfirmed:false}),
    form({state:'publicly_visible',url:'javascript:alert(1)',contentConfirmed:true})]) {
    assert.throws(()=>buildPublicationReviewPayload(snapshot,value,current))
  }
  const payload=buildPublicationReviewPayload(snapshot,form({state:'publicly_visible',url:'https://example.com/article',contentConfirmed:true}),current)
  assert.equal(payload.content_confirmed,true)
  assert.equal(payload.expected_article_sha256,hash)
  assert.equal(payload.observation_id,id)
  assert.equal(Object.isFrozen(payload),true)
  assert.equal(Object.hasOwn(buildPublicationReviewPayload(snapshot,form(),current),'content_confirmed'),false)
})

test('observation fields are bounded and unknown state cannot be smuggled into a record',()=>{
  const current=cycle(),snapshot=createPublicationReviewSnapshot(current,5,id)
  for(const value of [form({state:'success'}),form({note:'x'.repeat(401)}),form({note:{account:'private'}})]) {
    assert.throws(()=>buildPublicationReviewPayload(snapshot,value,current))
  }
  const payload=buildPublicationReviewPayload(snapshot,form({state:'unknown',note:'  等待核对  '}),current)
  assert.equal(payload.note,'等待核对')
  assert.equal(payload.state,'unknown')
  assert.equal(Object.hasOwn(payload,'url'),false)
})

test('safe article links never execute scripts, carry credentials, or open local endpoints',()=>{
  for(const value of ['javascript:alert(1)','http://example.com/article','https://user:password@example.com/article',
    'https://127.0.0.1/article','https://10.0.0.2/article','https://internal.local/article','https://[::ffff:127.0.0.1]/article'])assert.equal(publicationReviewUrl(value),'')
  assert.equal(publicationReviewUrl('https://example.com/article'),'https://example.com/article')
})

test('observation identities use secure randomness, without an insecure timestamp fallback',()=>{
  assert.equal(newPublicationObservationId({randomUUID:()=> '12345678-1234-1234-1234-123456789abc'}),'12345678123412341234123456789abc')
  assert.equal(newPublicationObservationId({getRandomValues:bytes=>bytes.fill(12)}),'0c'.repeat(16))
  assert.throws(()=>newPublicationObservationId({}),/无法准备核对记录/)
})

test('a save acknowledgement must include the same article, operation and content observation',async()=>{
  const current=cycle(),snapshot=createPublicationReviewSnapshot(current,5,id),payload=buildPublicationReviewPayload(snapshot,form(),current)
  for(const response of [cycle({id:'cycle-b'}),cycle(),cycle({moderation_observations:[{...record(snapshot,payload),article_id:99}]}),
    cycle({moderation_observations:[{...record(snapshot,payload),article_sha256:changedHash}]})]) {
    await assert.rejects(savePublicationReviewAttempt({moderation:async()=>({data:response})},snapshot,payload),/尚未确认/)
  }
  const saved=await savePublicationReviewAttempt({moderation:async()=>({data:resultCycle(snapshot,payload)})},snapshot,payload)
  assert.equal(saved.moderation_observations[0].state,'rejected')
})

test('late replies from a switched cycle, reopened form or changed article never apply',()=>{
  const current=cycle(),snapshot=createPublicationReviewSnapshot(current,5,id),payload=buildPublicationReviewPayload(snapshot,form(),current)
  const result=resultCycle(snapshot,payload)
  assert.equal(publicationReviewResultMatchesScope(snapshot,snapshot,current,result),true)
  assert.equal(publicationReviewResultMatchesScope(snapshot,snapshot,cycle({id:'cycle-b'}),result),false)
  assert.equal(publicationReviewResultMatchesScope(snapshot,createPublicationReviewSnapshot(current,5,'d'.repeat(32)),current,result),false)
  assert.equal(publicationReviewResultMatchesScope(snapshot,snapshot,cycle({drafts:[{...current.drafts[0],article_sha256:changedHash}]}),result),false)
  assert.equal(publicationReviewResultMatchesScope(null,null,null,null),false)
})

test('all platform observations remain visible; a later unknown cannot erase an earlier rejection',()=>{
  const current=cycle(),snapshot=createPublicationReviewSnapshot(current,5,id),rejected=record(snapshot,buildPublicationReviewPayload(snapshot,form(),current))
  current.moderation_observations=[{...rejected,id:'newer',state:'unknown',created_at:'2026-10-21T10:00:00Z'},rejected]
  const views=publicationObservationViews(current)
  assert.deepEqual(views.map(item=>item.state),['unknown','rejected'])
  assert.equal(views[1].sourceLabel,'运营方观察')
  current.drafts[0].article_sha256=changedHash
  assert.equal(publicationObservationViews(current)[1].olderContent,true)
  current.moderation_observations.unshift({...rejected,id:'bad',article_id:99})
  assert.equal(publicationObservationViews(current).length,2)
})

test('legacy and incomplete public observations do not masquerade as independently verified approval',()=>{
  const current=cycle(),snapshot=createPublicationReviewSnapshot(current,5,id),payload=buildPublicationReviewPayload(snapshot,form(),current)
  const base={...record(snapshot,payload),state:'publicly_visible',url:'https://example.com/article'}
  for(const row of [base,{...base,source:'unknown',content_confirmed:true},{...base,content_confirmed:true,url:'javascript:alert(1)'}]) {
    assert.equal(publicationObservationViews({...current,moderation_observations:[row]})[0].state,'unknown')
  }
  const view=publicationObservationViews({...current,moderation_observations:[{...base,content_confirmed:true}]})[0]
  assert.equal(view.state,'publicly_visible')
  assert.equal(view.sourceLabel,'运营方观察')
  assert.doesNotMatch(view.label,/核验通过|审核通过|发布成功/)
})

test('missing backend capability and transport uncertainty have actionable plain language',()=>{
  assert.match(publicationReviewError({response:{status:404}}),/升级/)
  assert.match(publicationReviewError(new Error('Network Error')),/保存结果待确认/)
  assert.doesNotMatch(publicationReviewError(new Error('expected_article_sha256 invalid')),/sha256|hash/)
})

const filename=new URL('../src/components/GeoPublicationReview.vue',import.meta.url)
const {descriptor,errors}=parse(fs.readFileSync(filename,'utf8'),{filename:filename.pathname})
assert.deepEqual(errors,[])
const script=compileScript(descriptor,{id:'publication-review-test'})
const source=descriptor.scriptSetup.content
const expression=name=>{const declaration=script.scriptSetupAst.flatMap(node=>node.type==='VariableDeclaration'?node.declarations:[]).find(node=>node.id.name===name);assert.ok(declaration);return source.slice(declaration.init.start,declaration.init.end)}
const createSaveHarness=api=>{
  const props=reactive({cycle:cycle(),busy:false}),snapshot=ref(createPublicationReviewSnapshot(props.cycle,5,id)),attemptPayload=ref(null),
    saving=ref(false),saveError=ref(''),visible=ref(true),formValue=reactive(form()),emitted=[]
  const available=computed(()=>publicationReviewAvailable(props.cycle)),snapshotIssue=computed(()=>snapshot.value?publicationReviewSnapshotIssue(snapshot.value,props.cycle):'')
  const canSave=new Function('computed','available','snapshot','props','saving','snapshotIssue','form',`return (${expression('canSave')})`)(computed,available,snapshot,props,saving,snapshotIssue,formValue)
  const harness=new Function('props','snapshot','attemptPayload','saving','saveError','visible','form','canSave','geoContentApi',
    'buildPublicationReviewPayload','savePublicationReviewAttempt','publicationReviewResultMatchesScope','publicationReviewError','emit',
    `let requestVersion=0;return {save:(${expression('saveObservation')}),invalidate:()=>requestVersion++}`)(
      props,snapshot,attemptPayload,saving,saveError,visible,formValue,canSave,api,
      buildPublicationReviewPayload,savePublicationReviewAttempt,publicationReviewResultMatchesScope,publicationReviewError,(...args)=>emitted.push(args))
  return {...harness,props,snapshot,attemptPayload,saving,saveError,visible,form:formValue,emitted}
}

test('the component retries an uncertain save with the identical frozen payload and operation',async()=>{
  const calls=[]
  const api={moderation:async(cycleId,payload)=>{calls.push({cycleId,payload});if(calls.length===1)throw new Error('Network Error');return {data:resultCycle(harness.snapshot.value,payload)}}}
  const harness=createSaveHarness(api)
  await harness.save()
  assert.match(harness.saveError.value,/保存结果待确认/)
  assert.equal(harness.emitted.length,0)
  harness.form.state='unknown'
  await harness.save()
  assert.equal(calls.length,2)
  assert.equal(calls[0].payload,calls[1].payload)
  assert.equal(calls[1].payload.observation_id,id)
  assert.equal(calls[1].payload.state,'rejected')
  assert.equal(harness.emitted.length,1)
  assert.equal(harness.visible.value,false)
})

test('the actual component ignores an old response and does not clear a new scope loading state',async()=>{
  let complete
  const api={moderation:()=>new Promise(resolve=>{complete=resolve})},harness=createSaveHarness(api)
  const opened=harness.snapshot.value
  const pending=harness.save()
  assert.equal(harness.saving.value,true)
  const payload=harness.attemptPayload.value
  harness.invalidate();harness.props.cycle=cycle({id:'cycle-b'});harness.snapshot.value=null
  complete({data:resultCycle(opened,payload)})
  await pending
  assert.equal(harness.emitted.length,0)
  assert.equal(harness.saving.value,true)
})

test('the actual save action blocks busy and duplicate in-flight clicks',async()=>{
  let calls=0,complete
  const api={moderation:()=>{calls++;return new Promise(resolve=>{complete=resolve})}},harness=createSaveHarness(api)
  harness.props.busy=true
  await harness.save()
  assert.equal(calls,0)
  harness.props.busy=false
  const pending=harness.save()
  await harness.save()
  assert.equal(calls,1)
  complete({data:resultCycle(harness.snapshot.value,harness.attemptPayload.value)})
  await pending
  assert.equal(harness.emitted.length,1)
})

test('the actual cycle watcher closes the old form but same-cycle refresh keeps its captured version',async()=>{
  const node=script.scriptSetupAst.find(item=>item.type==='ExpressionStatement'&&item.expression?.callee?.name==='watch')
  assert.ok(node)
  const props=reactive({cycle:cycle()}),visible=ref(true),saving=ref(true),snapshot=ref(createPublicationReviewSnapshot(props.cycle,5,id))
  const install=new Function('watch','props','visible','saving','resetForm',
    `let requestVersion=0;const stop=${source.slice(node.start,node.end).replace(/;$/,'')};return {stop,version:()=>requestVersion}`)
  const handler=install(watch,props,visible,saving,()=>{snapshot.value=null})
  props.cycle=cycle({drafts:[{...props.cycle.drafts[0],article_sha256:changedHash}]});await nextTick()
  assert.equal(visible.value,true)
  assert.equal(snapshot.value.expectedArticleSha256,hash)
  props.cycle=cycle({id:'cycle-b'});await nextTick()
  assert.equal(visible.value,false)
  assert.equal(saving.value,false)
  assert.equal(snapshot.value,null)
  assert.equal(handler.version(),1)
  handler.stop()
})

test('the component compiles and explains retained records without implementation terms',()=>{
  const template=compileTemplate({source:descriptor.template.content,filename:filename.pathname,id:'publication-review-test'})
  assert.deepEqual(template.errors,[])
  for(const style of descriptor.styles)assert.deepEqual(compileStyle({source:style.content,filename:filename.pathname,id:'publication-review-test',scoped:true}).errors,[])
  assert.match(descriptor.template.content,/原记录保留/)
  assert.match(descriptor.template.content,/暂不确定.*不会取消/)
  assert.doesNotMatch(descriptor.template.content,/observation_id|sha256|UUID|fingerprint/)
})
