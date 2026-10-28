import assert from 'node:assert/strict'
import fs from 'node:fs'
import test from 'node:test'
import { reactive, ref } from 'vue'
import { parse,compileScript,compileTemplate,compileStyle } from '@vue/compiler-sfc'
import { createSavedCyclesReader, savedCycleLabel, savedCycleMatches, savedCycleRows, savedCycleContinuation, savedCycleFactEdits } from '../src/utils/geoSavedCycles.js'
import { baselineWritingOptions } from '../src/utils/geoWritingOptions.js'

const project=(id=1,website='https://tongyuye.com/products/software')=>({id,name:'同于野软件',entity_type:'product',website})
const row=(id='cycle-a',project_id=1,report_ref='report-a',draft_count=3)=>({id,project_id,report_ref,draft_count,updated_at:'2026-10-21T09:00:00+08:00'})
const full=(listed=row(),profile=project())=>({...listed,profile,baseline:{profile,run_id:listed.report_ref},analysis:{briefs:[]},drafts:[{article_id:1,title:'同轮文章'}]})
const deferred=()=>{let resolve,reject;const promise=new Promise((done,fail)=>{resolve=done;reject=fail});return {promise,resolve,reject}}
const continuationValue=(id='cycle-a')=>({...full(row(id)),baseline:{profile:project(),run_id:'original-run',cases:[{id:'q01',question:'原问题'}],execution:{client_mode:'work',requested_model_label:'实际请求选项'}},facts:[{id:'f01',text:'已确认事实',source_url:'https://tongyuye.com/products/software',confirmed:true}],analysis:{briefs:[{id:'b01'},{id:'b02'}]},channels:{target_t1:'合成社区'},drafts:[{article_id:6,brief_id:'b02',platform:'target_t1'},{article_id:5,brief_id:'b01',platform:'target_t1'}]})
const externalRow=()=>({...row('external-cycle',1,'external-baseline:frozen-a'),baseline_source:'user_reported_external',baseline_kind:'geo_external_baseline_v1'})
const externalValue=()=>({...continuationValue('external-cycle'),...externalRow(),baseline:null,profile:Object.freeze(project()),
  test_plan:Object.freeze({cases:[{id:'q01',question:'原问题'}]}),external_baseline:{kind:'geo_external_baseline_v1',observations:[]},
  capabilities:{native_probe:false,native_retest:false,native_metrics:false}})

test('continuing saved content restores that exact baseline and an article from its own brief/channel',()=>{
  const value=continuationValue(),selection=savedCycleContinuation(value,project())
  assert.equal(selection.report,value.baseline);assert.equal(selection.cycle,value);assert.equal(selection.reportId,value.report_ref)
  assert.deepEqual([selection.briefId,selection.platform,selection.articleId],['b02','target_t1',6])
  assert.equal(value.baseline.cases[0].question,'原问题')
  for(const change of [{project_id:2},{baseline:{...value.baseline,profile:project(1,'https://tongyuye.com/products/other')}},{baseline:{...value.baseline,cases:undefined}}])assert.throws(()=>savedCycleContinuation({...value,...change},project()),/阻止混用/)
})

test('external saved content continues with its scoped plan and drafts without inventing a native report',()=>{
  const value=externalValue(),selection=savedCycleContinuation(value,project())
  assert.equal(selection.cycle,value);assert.equal(selection.report,null);assert.equal(selection.reportId,'')
  assert.deepEqual([selection.briefId,selection.platform,selection.articleId],['b02','target_t1',6])
  assert.equal(value.report_ref,'external-baseline:frozen-a');assert.equal(value.test_plan.cases[0].question,'原问题')
  for(const change of [{project_id:2},{profile:project(1,'https://tongyuye.com/products/other')},
    {baseline_source:'native_report'},{external_baseline:{kind:'unknown'}},{test_plan:{}}]){
    assert.throws(()=>savedCycleContinuation({...value,...change},project()),/阻止混用/)
  }
})

test('unchanged saved or suggested facts do not create false recovery warnings',()=>{
  const value=continuationValue(),rows=value.facts.map(({text,source_url,confirmed})=>({text,source_url,confirmed}))
  assert.equal(savedCycleFactEdits(value,rows,project()),false)
  assert.equal(savedCycleFactEdits(value,[{...rows[0],text:'尚未保存的新内容'}],project()),true)
  const suggested={facts:[],fact_candidates:{facts:[{id:'old',text:'上轮候选',source_url:'',confirmed:true}]}}
  assert.equal(savedCycleFactEdits(suggested,[{text:'上轮候选',source_url:'',confirmed:false}],project()),false)
  assert.equal(savedCycleFactEdits({facts:[]},[{text:'业务简介',source_url:'',confirmed:false}],{description:'业务简介'}),false)
})

const continuationHarness=()=>{
  const filename=new URL('../src/views/GeoJourney.vue',import.meta.url),{descriptor}=parse(fs.readFileSync(filename,'utf8'))
  const compiled=compileScript(descriptor,{id:'journey-continuation'}),declaration=compiled.scriptSetupAst.flatMap(node=>node.type==='VariableDeclaration'?node.declarations:[]).find(node=>node.id.name==='continueSavedCycle')
  const expression=descriptor.scriptSetup.content.slice(declaration.init.start,declaration.init.end)
  const values={run:async action=>action(),savedCycleContinuation,savedCycleFactEdits,baselineWritingOptions,
    project:ref(project()),profileDirty:ref(false),cycleId:ref('cycle-a'),cycle:ref(continuationValue()),editorDirty:ref(true),
    editor:reactive({title:'本机未保存修改',content:'本机正文',summary:'',tags:['本机标签']}),
    recoveryDraft:ref(null),recoveryFacts:ref(null),articleId:ref(5),activeDraft:ref({article_id:5}),
    articleSnapshot:article=>({article_id:article.article_id}),factRows:ref([{text:'已确认事实',source_url:'https://tongyuye.com/products/software',confirmed:true}]),
    report:ref(null),reportId:ref(''),historyReportId:ref(''),reportOrigin:ref(''),plan:ref({old:true}),planConfirmed:ref(true),replacementSourceId:ref(9),
    briefId:ref('b01'),platform:ref('target_t1'),clientMode:ref('consumer'),model:ref('本机自选模型'),step:ref(1)}
  const calls=[];values.applyCycle=(value,options)=>{calls.push(options);values.cycle.value=value;values.cycleId.value=value.id}
  const action=new Function(...Object.keys(values),`let restoring=false;return (${expression});`)(...Object.values(values))
  return {...values,calls,action}
}

test('continuing the same cycle keeps the active article and unsaved editor instead of selecting its newest draft',async()=>{
  const page=continuationHarness();await page.action(continuationValue())
  assert.equal(page.articleId.value,5);assert.equal(page.briefId.value,'b01');assert.equal(page.editor.title,'本机未保存修改')
  assert.equal(page.recoveryDraft.value,null);assert.deepEqual(page.calls,[{keepEditor:true}]);assert.equal(page.model.value,'本机自选模型')
  assert.equal(page.report.value.run_id,'original-run');assert.equal(page.step.value,2)
})

test('changing a saved cycle preserves unsaved edits and uses the selected original baseline/model options',async()=>{
  const page=continuationHarness();page.recoveryDraft.value={editor:{title:'更早的本机副本'}}
  await page.action(continuationValue('cycle-b'))
  assert.equal(page.articleId.value,6);assert.equal(page.recoveryDraft.value.article_id,5);assert.equal(page.recoveryDraft.value.editor.title,'本机未保存修改')
  assert.equal(page.recoveryDraft.value.previous.editor.title,'更早的本机副本');assert.equal(page.recoveryFacts.value,null)
  assert.deepEqual(page.calls,[{keepEditor:false}]);assert.equal(page.clientMode.value,'work');assert.equal(page.model.value,'实际请求选项')
  assert.equal(page.plan.value,null);assert.equal(page.report.value.run_id,'original-run')
})

test('a cross-project continuation fails before changing local drafts, report or progress',async()=>{
  const page=continuationHarness();await assert.rejects(page.action({...continuationValue(),project_id:8}),/阻止混用/)
  assert.equal(page.cycleId.value,'cycle-a');assert.equal(page.report.value,null);assert.equal(page.recoveryDraft.value,null);assert.deepEqual(page.calls,[])
})
function harness(api) {
  let current=project(),busy=false,state={}
  const emitted=[],updates=[]
  const reader=createSavedCyclesReader({api,getProject:()=>current,isBusy:()=>busy,
    onState:value=>{state=value;updates.push(value)},onSelected:value=>emitted.push(value)})
  return {reader,emitted,updates,get state(){return state},set project(value){current=value},set busy(value){busy=value},
    change(value){current=value;return reader.changeProject(value)}}
}

test('old list rows use exact project IDs and preserve each cycle report/count without inventing identity',()=>{
  const current=project(),values=savedCycleRows([row(),row('cycle-b',2),row('cycle-c','1'),row('cycle-d',1,'older-report',0)],current)
  assert.deepEqual(values.map(item=>[item.id,item.report_ref,item.draft_count]),[['cycle-a','report-a',3],['cycle-d','older-report',0]])
  assert.equal(Object.hasOwn(values[0],'website'),false)
  assert.match(savedCycleLabel(values[0]),/基线报告 report-a.*3 篇稿件/)
  assert.match(savedCycleLabel(values[1]),/0 篇稿件/)
  assert.match(savedCycleLabel({...row(),draft_count:null}),/稿件数待核对/)
  assert.throws(()=>savedCycleRows({},current))
  assert.deepEqual(savedCycleRows([row('same'),row('same'),row('../wrong'),{...row(),report_ref:null}],current),[])
})

test('external list metadata and report references remain explicit and must agree with the selected cycle',async()=>{
  const listed=externalRow(),value=externalValue(),rows=savedCycleRows([listed],project())
  assert.equal(rows[0].report_ref,listed.report_ref)
  assert.equal(rows[0].baseline_source,'user_reported_external');assert.equal(rows[0].baseline_kind,'geo_external_baseline_v1')
  assert.match(savedCycleLabel(rows[0]),/手机或外部观察基线 external-baseline:frozen-a/)
  assert.doesNotMatch(savedCycleLabel(rows[0]),/基线报告/)
  assert.equal(savedCycleMatches(value,rows[0],project()),true)
  for(const change of [{report_ref:'different-external-ref'},{baseline_source:'native_report'},{baseline_kind:'geo_search_test_report'}]){
    assert.equal(savedCycleMatches(value,{...rows[0],...change},project()),false)
  }
  const page=harness({list:async()=>({data:[listed]}),get:async()=>({data:value})})
  await page.change(project());assert.equal(await page.reader.select(listed.id),value);assert.deepEqual(page.emitted,[value])
  const wrong=harness({list:async()=>({data:[{...listed,baseline_source:'native_report'}]}),get:async()=>({data:value})})
  await wrong.change(project());assert.equal(await wrong.reader.select(listed.id),null);assert.deepEqual(wrong.emitted,[])
})

test('a full cycle must match its exact listing, complete entity URL and baseline; no latest substitution',()=>{
  const listed=row(),current=project(),value=full(listed,current)
  assert.equal(savedCycleMatches(value,listed,current),true)
  for(const change of [{id:'other-cycle'},{project_id:2},{project_id:'1'},{report_ref:'new-report'},
    {profile:{...current,entity_type:'company'}},{profile:{...current,website:'https://tongyuye.com/products/other'}},
    {profile:{...current,name:'懂AI中转站'}},{baseline:{profile:{...current,website:'https://tongyuye.com'}}},
    {drafts:undefined},{analysis:{}}])assert.equal(savedCycleMatches({...value,...change},listed,current),false)
})

test('listing and changing the dropdown never fetch or select a cycle; one explicit action emits the complete object',async()=>{
  let gets=0
  const value=full(),page=harness({list:async()=>({data:[row()]}),get:async id=>{gets++;assert.equal(id,'cycle-a');return {data:value}}})
  await page.change(project())
  assert.equal(gets,0);assert.equal(page.emitted.length,0)
  assert.equal(await page.reader.select('not-listed'),null);assert.equal(gets,0)
  assert.equal(await page.reader.select('cycle-a'),value)
  assert.equal(gets,1);assert.equal(page.emitted[0],value)
})

test('project A to B to A cannot accept old list responses or overwrite new loading/error state',async()=>{
  const requests=[],page=harness({list:()=>{const request=deferred();requests.push(request);return request.promise}})
  const oldA=page.change(project()),b=page.change(project(2)),newA=page.change(project())
  requests[0].resolve({data:[row('old-a')]});await oldA
  assert.equal(page.state.loading,true);assert.deepEqual(page.state.items,[])
  requests[1].reject(Error('PRIVATE_B'));await b
  assert.equal(page.state.error,'');assert.equal(page.state.loading,true)
  requests[2].resolve({data:[row('new-a')]});await newA
  assert.deepEqual(page.state.items.map(item=>item.id),['new-a']);assert.equal(page.state.loading,false)
})

test('only the newest concurrent list request can populate saved cycle choices',async()=>{
  const requests=[],page=harness({list:()=>{const request=deferred();requests.push(request);return request.promise}})
  const first=page.change(project()),second=page.reader.refresh()
  requests[1].resolve({data:[row('new')]});await second
  requests[0].resolve({data:[row('old')]});await first
  assert.deepEqual(page.state.items.map(item=>item.id),['new'])
})

test('late cycle GETs including A to B to A cannot emit or clear a current selection',async()=>{
  const gets=[],page=harness({list:async()=>({data:[row(),row('cycle-b',2)]}),get:()=>{const request=deferred();gets.push(request);return request.promise}})
  await page.change(project());const old=page.reader.select('cycle-a')
  await page.change(project(2));const b=page.reader.select('cycle-b')
  await page.change(project());const newest=page.reader.select('cycle-a')
  gets[0].resolve({data:full()});await old
  gets[1].reject(Error('PRIVATE_B'));await b
  assert.equal(page.emitted.length,0);assert.equal(page.state.selecting,'cycle-a');assert.equal(page.state.error,'')
  const value=full();gets[2].resolve({data:value});await newest
  assert.deepEqual(page.emitted,[value]);assert.equal(page.state.selecting,'')
})

test('refresh invalidates a selected row checkpoint so an old report response cannot beat a newer list/GET',async()=>{
  const gets=[],page=harness({list:async()=>({data:[row()]}),get:()=>{const request=deferred();gets.push(request);return request.promise}})
  await page.change(project());const old=page.reader.select('cycle-a')
  await page.reader.refresh();const newest=page.reader.select('cycle-a')
  gets[0].resolve({data:full()});await old
  assert.equal(page.emitted.length,0);assert.equal(page.state.selecting,'cycle-a')
  gets[1].resolve({data:full()});await newest
  assert.equal(page.emitted.length,1)
})

test('busy state blocks GETs and returning responses; repeated in-flight clicks do not read twice',async()=>{
  let calls=0;const request=deferred(),page=harness({list:async()=>({data:[row()]}),get:()=>{calls++;return request.promise}})
  await page.change(project());page.busy=true
  await page.reader.select('cycle-a');assert.equal(calls,0)
  page.busy=false;const pending=page.reader.select('cycle-a')
  await page.reader.select('cycle-a');assert.equal(calls,1)
  page.busy=true;request.resolve({data:full()});await pending
  assert.equal(page.emitted.length,0)
})

test('busy invalidation remains effective after the competing action finishes',async()=>{
  const request=deferred(),page=harness({list:async()=>({data:[row()]}),get:()=>request.promise})
  await page.change(project());const pending=page.reader.select('cycle-a')
  page.busy=true;page.reader.invalidateSelection();page.busy=false
  request.resolve({data:full()});await pending
  assert.equal(page.emitted.length,0)
})

test('failed reads and mismatched report/entity never emit, expose private errors or create replacement cycles',async()=>{
  const page=harness({list:async()=>{throw Error('PRIVATE_ACCOUNT')},get:async()=>{throw Error('PRIVATE_ACCOUNT')},create:()=>assert.fail('must not create')})
  await page.change(project());assert.deepEqual(page.state.items,[]);assert.doesNotMatch(page.state.error,/PRIVATE/)
  const invalid=harness({list:async()=>({data:[row()]}),get:async()=>({data:full(row('cycle-a',1,'different-report'))})})
  await invalid.change(project());await invalid.reader.select('cycle-a')
  assert.equal(invalid.emitted.length,0);assert.match(invalid.state.error,/基线报告.*阻止混用/)
  const failed=harness({list:async()=>({data:[row()]}),get:async()=>{throw Error('PRIVATE_ACCOUNT')}})
  await failed.change(project());await failed.reader.select('cycle-a')
  assert.equal(failed.emitted.length,0);assert.equal(failed.state.items.length,1);assert.doesNotMatch(failed.state.error,/PRIVATE/)
})

test('unmounted readers do not update state, emit cycles, or start new requests',async()=>{
  const list=deferred(),page=harness({list:()=>list.promise,get:()=>assert.fail('not mounted')})
  const pending=page.change(project()),count=page.updates.length;page.reader.dispose()
  list.resolve({data:[row()]});await pending
  assert.equal(page.updates.length,count);await page.reader.refresh();await page.reader.select('cycle-a')
  assert.equal(page.emitted.length,0)
  const get=deferred(),loaded=harness({list:async()=>({data:[row()]}),get:()=>get.promise})
  await loaded.change(project());const selecting=loaded.reader.select('cycle-a'),observed=loaded.updates.length
  loaded.reader.dispose();get.resolve({data:full()});await selecting
  assert.equal(loaded.updates.length,observed);assert.equal(loaded.emitted.length,0)
})

test('the compact component compiles with complete project identity and explicit continuation',()=>{
  const filename=new URL('../src/components/JourneySavedCycles.vue',import.meta.url).pathname
  const {descriptor,errors}=parse(fs.readFileSync(new URL('../src/components/JourneySavedCycles.vue',import.meta.url),'utf8'),{filename})
  assert.deepEqual(errors,[]);compileScript(descriptor,{id:'saved-cycles-test'})
  assert.deepEqual(compileTemplate({source:descriptor.template.content,filename,id:'saved-cycles-test'}).errors,[])
  for(const style of descriptor.styles)assert.deepEqual(compileStyle({source:style.content,filename,id:'saved-cycles-test',scoped:true}).errors,[])
  assert.match(descriptor.template.content,/继续已有内容/);assert.match(descriptor.template.content,/project\.website/)
  assert.match(descriptor.template.content,/@click="reader\.select\(selectedId\)"/)
  assert.doesNotMatch(descriptor.template.content,/@change=.*select\(/)
  assert.match(descriptor.scriptSetup.content,/onUnmounted\(\(\)=>reader\.dispose\(\)\)/)
  assert.match(descriptor.scriptSetup.content,/flush:'sync'/)
})
