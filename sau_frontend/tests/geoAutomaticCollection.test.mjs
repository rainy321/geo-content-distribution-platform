import assert from 'node:assert/strict'
import test from 'node:test'
import fs from 'node:fs'
import {parse,compileScript,compileTemplate} from '@vue/compiler-sfc'
import {ref} from 'vue'
import * as automatic from '../src/utils/geoAutomaticCollection.js'
import {externalSha} from '../src/utils/geoExternalBaseline.js'

const owner={id:7,name:'合成产品',entity_type:'product',website:'https://example.org/products/service'}
const frozen=async()=>{
  const profile={name:owner.name,entity_type:owner.entity_type,website:owner.website}
  const plan={schema_version:'1.0',platform:'doubao_web',profile,cases:['discovery','brand_identity','website_guided'].map((category,index)=>({id:`question-${index}`,category,question:`合成原题 ${index}。`,intent:'合成核验用途'}))}
  plan.plan_sha256=await externalSha(plan)
  return {id:'external-a',project_id:owner.id,profile,baseline:null,baseline_source:'user_reported_external',external_baseline:{kind:'geo_external_baseline_v1'},test_plan:plan}
}
test('automatic bridges are explicit capabilities and missing either cannot silently use the old run',()=>{
  for(const api of [null,{}, {plan_automatic(){}},{start_automatic_run(){}},{plan_with_doubao(){},start_run(){}}]){
    assert.equal(automatic.automaticBridgeReady(api),false);assert.throws(()=>automatic.requireAutomaticBridge(api),/请更新软件/)
  }
  const api={plan_automatic(){},start_automatic_run(){}}
  assert.equal(automatic.requireAutomaticBridge(api),api)
  assert.deepEqual(automatic.automaticCollectionOptions('consumer',' 模型 A '),{profile_mode:'consumer',model_label:'模型 A'})
  assert.deepEqual(automatic.automaticCollectionOptions('work'),{profile_mode:'work',model_label:''})
  for(const [profile,model] of [['auto',''],['work',null],['consumer','x'.repeat(101)]])assert.throws(()=>automatic.automaticCollectionOptions(profile,model))
})
test('an external cycle supplies an independently cloned exact frozen plan and a route without starting or comparing',async()=>{
  const cycle=await frozen(),prior=JSON.stringify(cycle),plan=await automatic.automaticPlanFromExternalCycle(cycle,owner)
  assert.deepEqual(plan,cycle.test_plan);assert.notEqual(plan,cycle.test_plan);assert.notEqual(plan.cases,cycle.test_plan.cases)
  assert.deepEqual(automatic.externalAutomaticRoute(cycle,owner),{path:'/geo-tests',query:{source_cycle:'external-a',journey_project:'7'}})
  assert.deepEqual(automatic.automaticSourceRequest({source_cycle:'external-a',journey_project:'7'}),{cycleId:'external-a',projectId:7})
  assert.equal(automatic.automaticSourceRequest({}),null);assert.equal(JSON.stringify(cycle),prior)
})
test('wrong projects, source kinds, mutated questions and malformed query identities never become a new automatic plan',async()=>{
  const value=await frozen()
  for(const mutate of [cycle=>{cycle.project_id=8},cycle=>{cycle.baseline_source='native_report'},cycle=>{cycle.baseline={}},
    cycle=>{cycle.test_plan.profile.website='https://other.example.net/'},cycle=>{cycle.test_plan.cases[0].question+=' 改动'},cycle=>{cycle.test_plan.cases[0].category='unknown'}]){
    const cycle=structuredClone(value);mutate(cycle);await assert.rejects(automatic.automaticPlanFromExternalCycle(cycle,owner))
  }
  for(const query of [{source_cycle:'../private',journey_project:'7'},{source_cycle:['external-a'],journey_project:'7'},
    {source_cycle:'external-a',journey_project:'0'},{source_cycle:'external-a',journey_project:[]},{source_cycle:'external-a',journey_project:'7.5'}])assert.throws(()=>automatic.automaticSourceRequest(query))
})
test('actual mode comes only from recorded case observations, never the selected profile or requested execution',()=>{
  assert.match(automatic.actualCollectionMode({execution:{client_mode:'consumer'},cases:[{client_mode:'work'}]}),/工作网页/)
  assert.match(automatic.actualCollectionMode({execution:{client_mode:'work'},cases:[{}]}),/未知/)
  assert.match(automatic.actualCollectionMode({cases:[{client_mode:'work'},{client_mode:'consumer'}]}),/不同网页模式/)
})
const filename=new URL('../src/views/GeoJourney.vue',import.meta.url),{descriptor}=parse(fs.readFileSync(filename,'utf8'))
const compiled=compileScript(descriptor,{id:'automatic-journey'}),declarations=compiled.scriptSetupAst.flatMap(node=>node.type==='VariableDeclaration'?node.declarations:[])
const expression=name=>{const node=declarations.find(item=>item.id.name===name);assert.ok(node,name);return descriptor.scriptSetup.content.slice(node.init.start,node.init.end)}
const action=(name,values)=>Function(...Object.keys(values),`return (${expression(name)})`)(...Object.values(values))
test('Journey automatic collection uses exactly the new two-argument run and explicit mode remains a deliberate choice',async()=>{
  const calls=[],plan=ref((await frozen()).test_plan),collectionPath=ref('automatic')
  const api={plan_automatic(){},start_automatic_run:(...args)=>calls.push(['automatic',...args]),start_run:(...args)=>calls.push(['explicit',...args])}
  const values={...automatic,window:{pywebview:{api}},plan,collectionPath,clientMode:ref('consumer'),model:ref('快速'),options:()=>({client_mode:'work',model_label:'指定'})}
  const run=action('collectBaseline',values);await run();assert.equal(calls[0].length,3)
  assert.deepEqual(calls[0],['automatic',plan.value,{profile_mode:'consumer',model_label:'快速'}])
  collectionPath.value='explicit';await run();assert.deepEqual(calls[1],['explicit',plan.value,{client_mode:'work',model_label:'指定'}])
})
test('opening an automatic baseline preserves and checkpoints unsaved Journey material before navigating',async()=>{
  const cycle=ref(await frozen()),editor={title:'未保存稿',content:'尚未保存的原文'},before=JSON.stringify(editor),calls=[]
  const values={...automatic,cycle,project:ref(owner),saveError:ref(''),persist:()=>calls.push(['persist',structuredClone(editor)]),router:{push:target=>calls.push(['route',target])},run:work=>work()}
  await action('openAutomaticBaseline',values)();assert.equal(JSON.stringify(editor),before)
  assert.equal(calls[0][0],'persist');assert.equal(calls[1][0],'route');assert.equal(calls[1][1].query.source_cycle,'external-a')
  calls.length=0;values.saveError.value='本机保存失败'
  await assert.rejects(action('openAutomaticBaseline',values)(),/先导出/);assert.equal(calls.length,1);assert.equal(JSON.stringify(editor),before)
})
test('the default Journey entry prioritizes automatic software collection and keeps phone evidence optional',()=>{
  assert.match(expression('collectionPath'),/ref\('automatic'\)/)
  assert.match(descriptor.template.content,/软件自动采集（当前可用网页界面）/)
  assert.match(descriptor.template.content,/<summary>补充验证（可选）<\/summary><GeoExternalBaseline/)
  assert.doesNotMatch(expression('actionHint'),/手机固定模板/)
  assert.deepEqual(compileTemplate({source:descriptor.template.content,filename:filename.pathname,id:'automatic-journey'}).errors,[])
})
