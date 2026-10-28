import assert from 'node:assert/strict'
import fs from 'node:fs'
import test from 'node:test'
import {computed,ref} from 'vue'
import {parse,compileScript,compileTemplate,compileStyle} from '@vue/compiler-sfc'
import {articleSnapshot,sameBrand,sameResearchBrief} from '../src/utils/geoJourney.js'
import {isExternalCycle,nativeCycleCapability} from '../src/utils/geoExternalBaseline.js'
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
