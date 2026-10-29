import assert from 'node:assert/strict'
import fs from 'node:fs'
import test from 'node:test'
import {computed,reactive,ref,watch} from 'vue'
import {createRouter,createMemoryHistory} from 'vue-router'
import {parse,compileScript,compileTemplate,compileStyle} from '@vue/compiler-sfc'
import {articleSnapshot,sameBrand,sameResearchBrief} from '../src/utils/geoJourney.js'
import {isExternalCycle,nativeCycleCapability} from '../src/utils/geoExternalBaseline.js'
import {savedCycleContinuation,savedCycleFactEdits} from '../src/utils/geoSavedCycles.js'
import {baselineWritingOptions} from '../src/utils/geoWritingOptions.js'
const load=path=>{
  const filename=new URL(path,import.meta.url),{descriptor,errors}=parse(fs.readFileSync(filename,'utf8'),{filename:filename.pathname})
  assert.deepEqual(errors,[])
  const compiled=compileScript(descriptor,{id:path}),declarations=compiled.scriptSetupAst.flatMap(node=>node.type==='VariableDeclaration'?node.declarations:[])
  return {filename,descriptor,compiled,expression:name=>{
    const declaration=declarations.find(node=>node.id.name===name);assert.ok(declaration,name)
    return descriptor.scriptSetup.content.slice(declaration.init.start,declaration.init.end)
  }}
}
const journey=load('../src/views/GeoJourney.vue'),search=load('../src/views/GeoSearchTests.vue'),content=load('../src/views/GeoContentLoop.vue')
const run=(page,name,values)=>Function(...Object.keys(values),`return (${page.expression(name)})`)(...Object.values(values))
const profile={name:'合成公开服务有限公司',website:'https://example.org/company',entity_type:'company',description:'公开说明'}
const external=(changes={})=>({id:'external-a',project_id:7,profile,baseline:null,baseline_source:'user_reported_external',
  external_baseline:{kind:'geo_external_baseline_v1'},capabilities:{native_probe:false,native_retest:false,native_metrics:false,editorial_research:true,work_drafting:true},
  test_plan:{cases:[{id:'q01',question:'原题'}]},facts:[],analysis:{method:'user_reported_external_editorial_clues',briefs:[]},drafts:[],channels:{owned_web:'官网'},...changes})
const clone=value=>JSON.parse(JSON.stringify(value))
const deferred=()=>{let resolve;const promise=new Promise(done=>resolve=done);return {promise,resolve}}
const settle=async predicate=>{for(let i=0;i<100&&!predicate();i++)await new Promise(setImmediate);assert.ok(predicate())}
const routedPages=[]
test.afterEach(()=>{while(routedPages.length)routedPages.pop().destroy()})

async function routedJourney({url='/journey?project=7&cycle=native-old&focus=feedback&source_task=old-task',storage=new Map(),database=new Map(),router:existingRouter}={}){
  const router=existingRouter||createRouter({history:createMemoryHistory(),routes:[{path:'/journey',component:{}},{path:'/geo-content',component:{}},{path:'/elsewhere',component:{}}]})
  router.onError(()=>{})
  if(!existingRouter){await router.push(url);await router.isReady()}
  const route=new Proxy({},{get:(_target,key)=>router.currentRoute.value[key]})
  const brief={id:'b01',topic:'合成原主题'},oldDraft={article_id:5,brief_id:'b01',platform:'owned_web',title:'旧稿标题',content:'旧稿正文',provenance:{brief}}
  const original={id:'native-old',project_id:7,report_ref:'native-report',profile,baseline:{profile,run_id:'native-run',cases:[{id:'q01',question:'原题'}]},analysis:{briefs:[brief]},drafts:[oldDraft],channels:{owned_web:'官网'},facts:[]}
  const persisted=[],reads=[]
  const values={computed,watch,projectId:ref(7),project:ref({id:7,...profile}),sameBrand,sameResearchBrief,articleSnapshot,isExternalCycle,savedCycleContinuation,savedCycleFactEdits,baselineWritingOptions,
    route,router,cycle:ref(original),cycleId:ref(original.id),profileDirty:ref(false),editorDirty:ref(true),editor:reactive({title:'未保存合成标题',content:'未保存合成正文',tags:['合成标签']}),
    recoveryDraft:ref(null),recoveryFacts:ref(null),error:ref(''),briefId:ref('b01'),platform:ref('owned_web'),articleId:ref(5),factRows:ref([{text:'未保存的合成事实',source_url:'',confirmed:false}]),
    report:ref(original.baseline),reportId:ref(original.report_ref),historyReportId:ref(original.report_ref),reportOrigin:ref('history'),plan:ref({plan_sha256:'a'.repeat(64)}),planConfirmed:ref(true),replacementSourceId:ref(9),
    clientMode:ref('work'),model:ref('合成模型'),step:ref(1),working:ref(false),reviewConfirmed:ref(false),sourceTaskFocus:ref('old-task')}
  values.busy=computed(()=>values.working.value)
  values.matchingDrafts=computed(()=>values.cycle.value.drafts.filter(item=>item.brief_id===values.briefId.value&&item.platform===values.platform.value))
  values.activeDraft=computed(()=>values.matchingDrafts.value.find(item=>item.article_id===values.articleId.value)||null)
  values.persist=()=>{const saved={project_id:values.projectId.value,cycleId:values.cycleId.value,recoveryDraft:clone(values.recoveryDraft.value),recoveryFacts:clone(values.recoveryFacts.value)};persisted.push(saved);storage.set(saved.project_id,saved)}
  const names=['loadEditor','applyCycle','syncCycleRoute','run','continueSavedCycle','adoptExternalCycle','chooseProject']
  const actions=Function(...Object.keys(values),`let restoring=false,destroyed=false,contextEpoch=0,pendingDiagnosticSection='maintenance';const stop=watch([projectId,cycleId],()=>contextEpoch++,{flush:'sync'});${names.map(name=>`const ${name}=${journey.expression(name)};`).join('\n')}return {${names.join(',')},epoch:()=>contextEpoch,isDestroyed:()=>destroyed,focus:()=>pendingDiagnosticSection,destroy:()=>{destroyed=true;stop()}};`)(...Object.values(values))
  const actualApply=actions.applyCycle
  const mount=async({get}={})=>{
    const node=journey.compiled.scriptSetupAst.find(node=>node.type==='ExpressionStatement'&&node.expression?.callee?.name==='onMounted')
    const callback=node.expression.arguments[0],expression=journey.descriptor.scriptSetup.content.slice(callback.start,callback.end)
    values.editorDirty.value=false
    const deps={...values,...actions,window:{addEventListener:()=>{}},projects:ref([]),nativeMethodsReady:()=>true,nativeAvailable:ref(true),provider:ref('doubao'),projectApi:{getProjects:async()=>({data:[values.project.value]})},lastJourneyProject:()=>null,
      chooseProject:async id=>{values.projectId.value=id;const saved=storage.get(id);if(saved?.cycleId){actualApply(database.get(saved.cycleId));values.recoveryDraft.value=clone(saved.recoveryDraft);values.recoveryFacts.value=clone(saved.recoveryFacts)}},
      geoContentApi:{get:async id=>{reads.push(id);if(get)return get(id);assert.ok(database.has(id),'read an existing cycle only');return {data:database.get(id)}}},activateNative:()=>true}
    Object.defineProperties(deps,{contextEpoch:{get:actions.epoch},destroyed:{get:actions.isDestroyed}})
    await Function('deps',`with(deps){let initialized=false,bridgeTimer,pendingDiagnosticSection=null;return (${expression})();}`)(deps)
  }
  const page={...values,...actions,mount,persisted,reads,storage,database};routedPages.push(page);return page
}

test('adopting an external receipt replaces the old explicit query and survives actual history return and reload selection',async()=>{
  const page=await routedJourney(),value=external({report_ref:'external-baseline:synthetic-frozen'})
  page.database.set(value.id,value)
  await page.adoptExternalCycle(value)
  assert.equal(page.cycleId.value,value.id);assert.equal(page.report.value,null);assert.equal(page.step.value,2)
  assert.deepEqual({...page.route.query},{journey_project:'7',cycle:value.id})
  assert.equal(page.sourceTaskFocus.value,'');assert.equal(page.focus(),null)
  assert.equal(page.storage.get(7).cycleId,value.id)
  assert.equal(page.recoveryDraft.value.editor.title,'未保存合成标题');assert.equal(page.recoveryFacts.value.facts[0].text,'未保存的合成事实')
  await page.router.push({path:'/geo-content',query:{cycle:value.id,journey_project:'7'}})
  const returned=new Promise(resolve=>{const stop=page.router.afterEach(()=>{stop();resolve()})});page.router.back();await returned
  assert.equal(page.route.query.cycle,value.id);assert.equal(page.route.query.journey_project,'7')
  const reopened=await routedJourney({router:page.router,storage:page.storage,database:page.database});await reopened.mount()
  assert.equal(reopened.projectId.value,7);assert.equal(reopened.cycleId.value,value.id);assert.equal(reopened.cycle.value.baseline_source,'user_reported_external')
  assert.equal(reopened.recoveryDraft.value.editor.title,'未保存合成标题');assert.equal(reopened.recoveryFacts.value.facts[0].text,'未保存的合成事实')
  assert.deepEqual(reopened.reads,[value.id]);assert.equal(page.database.size,1)
})

test('canonical cycle reloads with drafts continue content while an actual details return or explicit feedback link remains distinct',async()=>{
  const brief={id:'b01',topic:'合成选题'},value=external({report_ref:'external-baseline:synthetic-frozen',analysis:{method:'configured_ai',briefs:[brief]},
    drafts:[{article_id:8,brief_id:'b01',platform:'owned_web',title:'合成已存标题',content:'合成已存正文',provenance:{brief}}]})
  for(const [url,expected] of [['/journey?journey_project=7&cycle=external-a',2],['/journey?journey_project=7',4]]){
    const database=new Map([[value.id,value]]),storage=new Map([[7,{cycleId:value.id,recoveryDraft:null,recoveryFacts:null}]])
    const page=await routedJourney({url,database,storage});await page.mount()
    assert.equal(page.cycleId.value,value.id);assert.equal(page.step.value,expected)
    assert.equal(page.cycle.value.baseline,null)
  }
})

test('explicit saved-cycle continuation synchronizes its own query and preserves same-cycle focus and local edits',async()=>{
  const page=await routedJourney(),value=external({report_ref:'external-baseline:synthetic-frozen'})
  page.database.set(value.id,value);await page.continueSavedCycle(value)
  assert.deepEqual({...page.route.query},{journey_project:'7',cycle:value.id});assert.equal(page.storage.get(7).cycleId,value.id)
  assert.equal(page.recoveryDraft.value.editor.title,'未保存合成标题')
  await page.router.replace({path:'/journey',query:{journey_project:'7',cycle:value.id,focus:'feedback'}})
  await page.continueSavedCycle(value);assert.equal(page.route.query.focus,'feedback')
  assert.equal(page.report.value,null);assert.equal(page.cycle.value.capabilities.native_retest,false)
})

test('rejected and thrown route writes keep the acknowledged cycle, persistence and recovery copies without another save',async()=>{
  for(const thrown of [false,true]){
    const page=await routedJourney(),value=external({report_ref:'external-baseline:synthetic-frozen'})
    page.database.set(value.id,value)
    page.router.beforeEach(to=>{if(to.query.cycle===value.id){if(thrown)throw Error('PRIVATE_ROUTER_DETAIL');return false}})
    await page.adoptExternalCycle(value)
    assert.equal(page.route.query.cycle,'native-old');assert.equal(page.cycleId.value,value.id)
    assert.equal(page.storage.get(7).cycleId,value.id);assert.equal(page.database.size,1);assert.equal(page.working.value,false)
    assert.equal(page.recoveryDraft.value.editor.title,'未保存合成标题');assert.equal(page.recoveryFacts.value.facts[0].text,'未保存的合成事实')
    assert.match(page.error.value,/本轮内容已保存.*地址未能更新/);assert.doesNotMatch(page.error.value,/PRIVATE_ROUTER_DETAIL/)
  }
})

test('route settlement locks normal project switching and foreign or stale receipts cannot replace the selected project',async()=>{
  const page=await routedJourney(),gate=deferred(),value=external({report_ref:'external-baseline:synthetic-frozen'})
  page.router.beforeEach(to=>to.query.cycle===value.id?gate.promise:undefined)
  const adoption=page.adoptExternalCycle(value);await settle(()=>page.working.value)
  await page.chooseProject(8);assert.equal(page.projectId.value,7);assert.equal(page.cycleId.value,value.id)
  gate.resolve();await adoption
  const count=page.persisted.length
  await page.adoptExternalCycle(external({project_id:8}));assert.equal(page.persisted.length,count);assert.equal(page.route.query.journey_project,'7')
  page.profileDirty.value=true;await page.adoptExternalCycle(external({id:'later-receipt'}));assert.equal(page.cycleId.value,value.id)
  page.profileDirty.value=false;page.destroy();await page.adoptExternalCycle(external({id:'after-unmount'}));assert.equal(page.route.query.cycle,value.id)
})

test('equivalent canonical singleton query values do not report a failed save on duplicated navigation',async()=>{
  const page=await routedJourney(),value=external({report_ref:'external-baseline:synthetic-frozen'})
  await page.router.replace({path:'/journey',query:{journey_project:['7'],cycle:value.id}})
  await page.adoptExternalCycle(value)
  assert.equal(page.cycleId.value,value.id);assert.equal(page.error.value,'');assert.equal(page.working.value,false)
})

test('a late initial query read cannot override a newer explicit cycle selection, including A to B to A or unmount',async()=>{
  for(const change of ['new-cycle','aba','unmount']){
    const entry=external({id:'entry-cycle',report_ref:'external-baseline:entry'}),selected=external({id:'selected-cycle',report_ref:'external-baseline:selected'})
    const page=await routedJourney({url:'/journey?project=7&cycle=entry-cycle'}),read=deferred()
    const mounting=page.mount({get:()=>read.promise});await settle(()=>page.reads.length===1)
    if(change==='new-cycle')await page.adoptExternalCycle(selected)
    else if(change==='aba'){page.cycleId.value='intermediate';page.cycleId.value='native-old'}
    else page.destroy()
    read.resolve({data:entry});await mounting
    assert.equal(page.cycleId.value,change==='new-cycle'?selected.id:'native-old')
    assert.notEqual(page.cycleId.value,entry.id)
    if(change==='new-cycle')assert.equal(page.route.query.cycle,selected.id)
  }
})

test('the phone entry prefers the page plan and reuses only the exact current project cycle original plan',()=>{
  const frozenOriginal={plan_sha256:'a'.repeat(64),profile,cases:[{id:'q01',question:'已加载周期的原题'}]}
  const values={computed,project:ref({id:7,...profile}),projectId:ref(7),plan:ref(null),cycle:ref({id:'native-a',project_id:7,profile,test_plan:frozenOriginal}),sameBrand}
  const candidate=run(journey,'externalBaselinePlan',values)
  assert.deepEqual(candidate.value,frozenOriginal)
  values.plan.value={...frozenOriginal,plan_sha256:'b'.repeat(64)};assert.equal(candidate.value,values.plan.value)
  values.plan.value=null;values.cycle.value.project_id=8;assert.equal(candidate.value,null)
  values.cycle.value.project_id='7';assert.equal(candidate.value,null)
  values.cycle.value.project_id=7;values.cycle.value.profile={...profile,website:'https://example.org/another-product'};assert.equal(candidate.value,null)
  values.cycle.value=external({test_plan:frozenOriginal});assert.deepEqual(candidate.value,frozenOriginal)
  assert.equal(values.cycle.value.baseline_source,'user_reported_external');assert.equal(values.cycle.value.capabilities.native_retest,false)
  values.project.value=null;assert.equal(candidate.value,null)
  assert.match(journey.descriptor.template.content,/<GeoExternalBaseline\b[^>]*:plan="externalBaselinePlan"/)
  assert.match(journey.descriptor.template.content,/<GeoExternalBaseline\b[^>]*v-if="project && !externalCycle"/)
  assert.match(search.descriptor.template.content,/<GeoExternalBaseline\b[^>]*:plan="planMatchesProject \? plan : null"/)
})

test('the active external cycle never falls back to the previously selected positive native report',()=>{
  const cycle=ref(null),report=ref({run_id:'old-native',cohorts:{brand_mention_rate:1}})
  const evidence=run(journey,'evidenceReport',{computed,cycle,report})
  assert.equal(evidence.value,report.value)
  cycle.value=external();assert.equal(evidence.value,null)
  cycle.value={id:'malformed',baseline:null};assert.equal(evidence.value,null)
  cycle.value={baseline:{run_id:'native-current'}};assert.equal(evidence.value.run_id,'native-current')
})

test('external editorial clues require the same AI planning stage as rule observations without changing their source',()=>{
  const cycle=ref(external()),needsAnalysis=run(journey,'needsAnalysis',{computed,cycle})
  assert.equal(needsAnalysis.value,true)
  assert.equal(cycle.value.analysis.method,'user_reported_external_editorial_clues')
  cycle.value.analysis.method='rule_observations_not_ai';assert.equal(needsAnalysis.value,true)
  cycle.value.analysis.method='configured_ai';assert.equal(needsAnalysis.value,false)
})

test('applying a typed cycle clears all native report selectors and preserves its null baseline',()=>{
  const values={projectId:ref(7),project:ref({id:7,...profile}),sameBrand,sameResearchBrief,articleSnapshot,
    cycle:ref({id:'old-native'}),cycleId:ref('old-native'),activeDraft:ref(null),editorDirty:ref(false),editor:{tags:[]},
    recoveryDraft:ref(null),error:ref(''),briefId:ref('old-brief'),platform:ref('old-platform'),articleId:ref(9),
    matchingDrafts:ref([]),factRows:ref([]),report:ref({run_id:'old-native'}),reportId:ref('old-report'),historyReportId:ref('old-report'),reportOrigin:ref('fresh'),loadEditor:()=>{}}
  const apply=run(journey,'applyCycle',values),value=external();apply(value)
  assert.equal(values.cycle.value.id,value.id);assert.equal(values.cycle.value.baseline,null)
  assert.equal(values.report.value,null);assert.equal(values.reportId.value,'');assert.equal(values.historyReportId.value,'')
  assert.equal(values.reportOrigin.value,'external');assert.equal(values.articleId.value,null)
  assert.deepEqual(values.factRows.value,[{text:profile.description,source_url:'',confirmed:false}])
  assert.throws(()=>apply(external({project_id:8})),/阻止混用/)
})

test('native single-question probes and full retests refuse typed cycles while native legacy remains supported',()=>{
  const cycle=ref(external({probes:[]})),values={computed,cycle,nativeAvailable:ref(true),window:{pywebview:{api:{start_research_probe:()=>assert.fail('must not send')}}}}
  const probeAllowed=run(journey,'singleProbeAvailable',values)
  const retestOptions=run(journey,'retestSessionOptions',{cycle})
  assert.equal(probeAllowed.value,false);assert.throws(()=>retestOptions(),/外部起始观察/)
  cycle.value={baseline:{execution:{client_mode:'work',requested_model_label:'示例模型'}},probes:[]}
  assert.equal(probeAllowed.value,true);assert.deepEqual(retestOptions(),{client_mode:'work',model_label:'示例模型'})
  cycle.value.capabilities={native_probe:false,native_retest:false}
  assert.equal(probeAllowed.value,false);assert.throws(()=>retestOptions(),/原生复测/)
})

test('external primary actions navigate to content or feedback without starting a new native request',async()=>{
  const calls=[],step=ref(1),values={externalCycle:ref(true),cycle:ref(external()),step,primaryDisabled:ref(false),busy:ref(false),profileDirty:ref(false),
    nativeCycleCapability,nextTick:async()=>{},focusDiagnosticSection:section=>calls.push(section),run:()=>assert.fail('no native workflow'),
    report:ref(null),plan:ref(null),makePlan:()=>assert.fail('no new plan')}
  const action=run(journey,'primaryAction',values)
  await action();assert.equal(step.value,2);assert.deepEqual(calls,[])
  step.value=4;await action();assert.equal(step.value,4);assert.deepEqual(calls,['feedback'])
})

test('Search Tests routes only an exact saved-project receipt to Journey and never installs an external answer as a report',()=>{
  const calls=[],values={externalProfilePending:ref(false),isExternalCycle,projectId:ref(7),project:ref({id:7,...profile}),sameBrand,
    router:{push:value=>calls.push(value)}}
  const open=Function(...Object.keys(values),`let destroyed=false;return (${search.expression('openExternalCycle')})`)(...Object.values(values))
  open(external());assert.deepEqual(calls,[{path:'/journey',query:{journey_project:'7',cycle:'external-a'}}])
  open(external({project_id:8}));open(external({profile:{...profile,website:'https://example.net/'}}));assert.equal(calls.length,1)
  values.externalProfilePending.value=true;open(external());assert.equal(calls.length,1)
})

test('detailed content can open a null-baseline cycle and refuses native report comparisons without disabling work drafting',()=>{
  const cycle=ref(external()),externalCycle=run(content,'externalCycle',{computed,cycle,isExternalCycle})
  const nativeRetestAllowed=run(content,'nativeRetestAllowed',{computed,cycle,nativeCycleCapability})
  const matching=run(content,'matchingRetests',{computed,cycle,nativeRetestAllowed,reports:ref([{name:profile.name,run_id:'old-native'}])})
  assert.equal(externalCycle.value,true);assert.equal(nativeRetestAllowed.value,false);assert.deepEqual(matching.value,[])
  assert.equal(cycle.value.capabilities.work_drafting,true)
  const retest=content.descriptor.template.content
  assert.match(retest,/<template v-if="nativeRetestAllowed">/)
  assert.match(retest,/v-if="nativeAvailable"[^>]*@click="generate\('draft','doubao'\)"/)
})

test('both entry pages and the detailed view compile with a separate first-mobile entry and an explicit source summary',()=>{
  for(const page of [journey,search,content]){
    assert.deepEqual(compileTemplate({source:page.descriptor.template.content,filename:page.filename.pathname,id:'typed-entry-test'}).errors,[])
    for(const style of page.descriptor.styles)assert.deepEqual(compileStyle({source:style.content,filename:page.filename.pathname,id:'typed-entry-test',scoped:style.scoped}).errors,[])
  }
  for(const page of [journey,search])assert.match(page.descriptor.template.content,/<GeoExternalBaseline\b[^>]*@busy=/)
  assert.match(journey.descriptor.template.content,/<article v-if="!externalCycle"><span>取得回答的复测/)
  assert.match(journey.descriptor.template.content,/externalBaselineSummary\(cycle\)\.text/)
  assert.match(content.descriptor.template.content,/user_reported_external_editorial_clues/)
})
