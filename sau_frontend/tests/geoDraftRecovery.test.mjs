import assert from 'node:assert/strict'
import fs from 'node:fs'
import test from 'node:test'
import {parse,compileScript,compileTemplate,compileStyle} from '@vue/compiler-sfc'
import {createDraftRecoveryFlow,draftRecoveryBridgeReady,draftRecoveryMessage,draftRecoveryPreview,
  draftRecoveryScope,draftRecoveryState,recoveredDraftArticle,recoveredDraftResult,sameDraftRecoveryScope} from '../src/utils/geoDraftRecovery.js'

const cycle=()=>({id:'cycle-a',revision:3,profile:{name:'合成产品'},facts:[{id:'f01',text:'已核对事实',source_url:'https://example.org/',confirmed:true}],
  analysis:{briefs:[{id:'brief-a',topic:'合成选题'}]},channels:{zhihu:'知乎'},destinations:[],drafts:[]})
const task=()=>({id:'failed-draft',kind:'draft',status:'failed',cycle_id:'cycle-a'})
const preview=()=>({available:true,preview_sha256:'a'.repeat(64),brief_id:'brief-a',platform:'zhihu',
  draft:{title:'已有稿件',summary:'概述',content:'据已确认的公开资料整理的完整正文。'.repeat(10),tags:['主题']},already_saved:false})
const result=()=>({cycle_id:'cycle-a',article_id:12,already_saved:false,reused_existing_response:true})
const deferred=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no});return {promise,resolve,reject}}
function harness(changes={}) {
  const current={cycle:cycle(),task:task(),selection:'brief-a|zhihu'},calls=[],applied=[],busy=[]
  const state=draftRecoveryState(),api={preview_research_draft:async(...args)=>{calls.push(['preview',...args]);return preview()},
    recover_research_draft:async(...args)=>{calls.push(['save',...args]);return result()},...changes.api}
  let blocked=false
  const flow=createDraftRecoveryFlow({state,getScope:()=>draftRecoveryScope(current.cycle,current.task,current.selection),getApi:()=>api,
    isBlocked:()=>blocked,loadCycle:changes.loadCycle||(async id=>{calls.push(['load',id]);return current.cycle}),
    applyCycle:value=>applied.push(value),busy:value=>busy.push(value)})
  return {flow,state,current,calls,api,applied,busy,block:value=>{blocked=value}}
}

test('only a terminal same-cycle draft with exact bridge capabilities can offer local recovery',()=>{
  const local=draftRecoveryScope(cycle(),task(),'brief-a|zhihu')
  assert.ok(local)
  for(const variant of [{...task(),kind:'research'},{...task(),status:'running'},{...task(),status:'completed'},
    {...task(),cycle_id:'cycle-b'},{...task(),id:'../private'}])assert.equal(draftRecoveryScope(cycle(),variant),null)
  assert.equal(draftRecoveryScope({...cycle(),revision:'3'},task()),null)
  for(const api of [null,{}, {preview_research_draft(){}},{recover_research_draft(){}},
    {generate_research(){},open_login(){}}])assert.equal(draftRecoveryBridgeReady(api),false)
  assert.equal(draftRecoveryBridgeReady({preview_research_draft(){},recover_research_draft(){}}),true)
  assert.equal(sameDraftRecoveryScope(local,draftRecoveryScope(cycle(),task(),'brief-a|zhihu')),true)
  for(const change of [c=>{c.revision++},c=>{c.facts[0].text='新资料'},c=>{c.profile.name='另一品牌'},c=>{c.analysis.briefs[0].topic='新选题'}]){
    const value=cycle();change(value)
    assert.equal(sameDraftRecoveryScope(local,draftRecoveryScope(value,task(),'brief-a|zhihu')),false)
  }
  assert.equal(sameDraftRecoveryScope(local,draftRecoveryScope(cycle(),task(),'brief-b|zhihu')),false)
})

test('preview data admits only bounded article fields and never displays native paths as an error',()=>{
  const raw=preview(),safe=draftRecoveryPreview({...raw,evidence_path:'C:/private/account',raw:'private raw'})
  assert.equal(safe.draft.title,raw.draft.title)
  assert.doesNotMatch(JSON.stringify(safe),/private|evidence_path/)
  raw.draft.tags[0]='changed';assert.deepEqual(safe.draft.tags,['主题'])
  for(const value of [{...preview(),preview_sha256:'broken'}, {...preview(),draft:{...preview().draft,title:''}},
    {...preview(),draft:{...preview().draft,content:'x'.repeat(30001)}},{...preview(),already_saved:true,article_id:'12'},
    {...preview(),draft:{...preview().draft,tags:[{}]}}])assert.throws(()=>draftRecoveryPreview(value))
  for(const message of ['C:\\Users\\account\\secret','/home/user/private','Traceback account','https://account.example/login?secret=yes']){
    assert.doesNotMatch(draftRecoveryMessage(message),/secret|private|account/)
  }
  assert.equal(draftRecoveryMessage('资料已经变化，请重新核对。'),'资料已经变化，请重新核对。')
  assert.equal(draftRecoveryPreview({available:false,message:'C:/private'}).available,false)
  assert.equal(recoveredDraftResult(result(),draftRecoveryScope(cycle(),task())),true)
  for(const value of [{...result(),cycle_id:'cycle-b'},{...result(),article_id:'12'},
    {...result(),reused_existing_response:false},{...result(),already_saved:undefined},{...result(),error:'blocked'}]){
    assert.equal(recoveredDraftResult(value,draftRecoveryScope(cycle(),task())),false)
  }
})

test('a saved draft replaces the current error explanation only with exact same-cycle recovery lineage',()=>{
  const value={...cycle(),drafts:[{article_id:12,provenance:{recovered_from_task_id:'failed-draft'}}]}
  assert.equal(recoveredDraftArticle(value,task()),12)
  for(const changed of [{...task(),cycle_id:'cycle-b'},{...task(),id:'different-task'},
    {...task(),kind:'research'},{...task(),status:'running'}])assert.equal(recoveredDraftArticle(value,changed),null)
  assert.equal(recoveredDraftArticle({...value,drafts:[{article_id:12,provenance:{}}]},task()),null)
  assert.equal(recoveredDraftArticle({...value,drafts:[{article_id:'12',provenance:{recovered_from_task_id:'failed-draft'}}]},task()),null)
})

test('a reviewed preview saves the exact existing response once and refreshes the same cycle',async()=>{
  const page=harness()
  await page.flow.inspect()
  assert.deepEqual(page.calls,[['preview','failed-draft','cycle-a']])
  await page.flow.save();assert.equal(page.calls.length,1)
  page.state.confirmed=true;await page.flow.save()
  assert.deepEqual(page.calls,[['preview','failed-draft','cycle-a'],['save','failed-draft','cycle-a','a'.repeat(64)],['load','cycle-a']])
  assert.equal(page.state.savedArticleId,12);assert.equal(page.state.confirmed,false)
  assert.equal(page.applied.length,1);assert.deepEqual(page.busy,[true,false])
  await page.flow.save();assert.equal(page.calls.length,3)
  assert.doesNotMatch(JSON.stringify(page.calls),/generate|start_run|publish|login/)
})

test('double clicks and busy/unsaved-fact gates cannot start another local save',async()=>{
  const pending=deferred(),page=harness({api:{recover_research_draft:async(...args)=>{page.calls.push(['save',...args]);return pending.promise}}})
  await page.flow.inspect();page.state.confirmed=true;page.block(true)
  await page.flow.save();assert.equal(page.calls.length,1)
  page.block(false);const first=page.flow.save();await page.flow.save()
  assert.equal(page.calls.filter(row=>row[0]==='save').length,1)
  pending.resolve(result());await first
  assert.deepEqual(page.busy,[true,false]);assert.equal(page.state.savedArticleId,12)
})

test('cycle, material, task or selection changes discard a late preview and its review confirmation',async()=>{
  for(const change of [page=>{page.current.cycle.id='cycle-b'},page=>{page.current.cycle.revision++},
    page=>{page.current.task.id='new-task'},page=>{page.current.selection='other-brief|zhihu'},page=>{page.current.cycle.facts[0].text='unsaved server update'}]){
    const pending=deferred();let calls=0
    const page=harness({api:{preview_research_draft:()=>++calls===1?pending.promise:Promise.resolve({available:false,message:'资料已变化'})}})
    const reading=page.flow.inspect();change(page);await page.flow.inspect()
    pending.resolve(preview());await reading
    assert.equal(page.state.preview,null);assert.equal(page.state.confirmed,false);assert.equal(page.applied.length,0)
  }
})

test('late save/refresh results cannot modify another cycle and pending state releases its own lock',async()=>{
  const pending=deferred(),page=harness({api:{recover_research_draft:()=>pending.promise}})
  await page.flow.inspect();page.state.confirmed=true;const saving=page.flow.save()
  page.current.cycle=cycle();page.current.cycle.id='cycle-b'
  page.current.task={...task(),id:'new-draft',cycle_id:'cycle-b'}
  await page.flow.inspect()
  pending.resolve(result());await saving
  assert.equal(page.applied.length,0);assert.equal(page.state.savedArticleId,null)
  assert.deepEqual(page.busy,[true,false]);assert.equal(page.state.saving,false)
  const loading=deferred(),other=harness({loadCycle:()=>loading.promise})
  await other.flow.inspect();other.state.confirmed=true;const savingOther=other.flow.save()
  await Promise.resolve();other.current.selection='other|zhihu';loading.resolve(cycle());await savingOther
  assert.equal(other.applied.length,0)
})

test('uncertain save results require a read-only check and do not automatically send again',async()=>{
  const page=harness({api:{recover_research_draft:async()=>{throw Error('C:/private/account')}}})
  await page.flow.inspect();page.state.confirmed=true;await page.flow.save()
  assert.equal(page.state.uncertain,true);assert.equal(page.state.confirmed,false)
  assert.doesNotMatch(page.state.error,/private|account/)
  page.state.confirmed=true;await page.flow.save();assert.deepEqual(page.busy,[true,false])
  page.api.preview_research_draft=async()=>({...preview(),already_saved:true,article_id:12})
  await page.flow.inspect()
  assert.equal(page.state.savedArticleId,12);assert.equal(page.applied.length,1)
  assert.equal(page.calls.filter(row=>row[0]==='save').length,0)
})

test('a confirmed local save remains saved when the list refresh fails; no duplicate save is offered',async()=>{
  const page=harness({loadCycle:async()=>{throw Error('private localhost path')}})
  await page.flow.inspect();page.state.confirmed=true;await page.flow.save()
  assert.equal(page.state.savedArticleId,12);assert.match(page.state.error,/稿件已保存/)
  assert.doesNotMatch(page.state.error,/private|localhost/)
  page.state.confirmed=true;await page.flow.save()
  assert.equal(page.calls.filter(row=>row[0]==='save').length,1)
})

test('unmount discards pending replies and releases only the component busy owner',async()=>{
  const pending=deferred(),page=harness({api:{recover_research_draft:()=>pending.promise}})
  await page.flow.inspect();page.state.confirmed=true;const saving=page.flow.save()
  page.flow.dispose();pending.resolve(result());await saving
  assert.equal(page.applied.length,0);assert.deepEqual(page.busy,[true,false])
  assert.equal(page.state.savedArticleId,null)
})

test('actual recovery component and parent compile, keep text preview inert and preserve existing cycle update semantics',()=>{
  const file=new URL('../src/components/GeoDraftRecovery.vue',import.meta.url)
  const {descriptor,errors}=parse(fs.readFileSync(file,'utf8'),{filename:file.pathname})
  assert.deepEqual(errors,[]);compileScript(descriptor,{id:'existing-ai-draft'})
  assert.deepEqual(compileTemplate({source:descriptor.template.content,filename:file.pathname,id:'existing-ai-draft'}).errors,[])
  assert.deepEqual(compileStyle({source:descriptor.styles[0].content,id:'existing-ai-draft',scoped:true}).errors,[])
  assert.match(descriptor.template.content,/已核对这份稿件/)
  assert.match(descriptor.template.content,/保存已有稿件，不重新生成/)
  assert.match(descriptor.template.content,/<pre>\{\{ state.preview.draft.content \}\}<\/pre>/)
  assert.doesNotMatch(descriptor.template.content,/v-html/)
  assert.doesNotMatch(descriptor.scriptSetup.content,/generate_research|start_run|publish\(/)
  const parent=new URL('../src/views/GeoContentLoop.vue',import.meta.url),parentDescriptor=parse(fs.readFileSync(parent,'utf8')).descriptor
  assert.match(parentDescriptor.template.content,/<GeoDraftRecovery[^]*?:selection-key="`\$\{briefId\}\|\$\{platform\}`" @updated="applyCycle" @busy="setFeedbackBusy"/)
  assert.match(parentDescriptor.template.content,/:facts-current="confirmed && factsText===factsStoredText\(cycle.facts\)"/)
  assert.deepEqual(compileTemplate({source:parentDescriptor.template.content,filename:parent.pathname,id:'parent-existing-draft'}).errors,[])
})
