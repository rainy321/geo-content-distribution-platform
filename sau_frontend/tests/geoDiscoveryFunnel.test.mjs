import assert from 'node:assert/strict'
import fs from 'node:fs'
import test from 'node:test'
import * as Vue from 'vue'
import {renderToString} from '@vue/server-renderer'
import {parse,compileScript,compileTemplate,compileStyle} from '@vue/compiler-sfc'
import {discoveryFunnelView} from '../src/utils/geoDiscoveryFunnel.js'

const planSha='a'.repeat(64)
const fixture=()=>({
  id:'cycle-current',baseline_source:'native_report',baseline:{run_id:'native-run-current',plan_sha256:planSha},
  test_plan:{plan_sha256:planSha,cases:[
    {id:'q01',category:'discovery',question:'小团队怎样选择模型接入工具？'},
    {id:'q02',category:'brand_identity',question:'示例产品的官网和运营公司是什么？'},
    {id:'q03',category:'website_guided',question:'请核对 https://example.org/product 的公开能力。'}]},
  discovery_funnel:{schema_version:'geo_discovery_funnel_v1',supported:true,
    run_id:'native-run-current',plan_sha256:planSha,comparability:{matched:true},
    note:'只展示本轮可观察信号；给网址核验不等于自然推荐。',
    stages:[
      {key:'natural_discovery',state:'not_observed',note:'自然需求未提及本对象。',cases:[{case_id:'q01',client_mode:'consumer',model_label:'快速',official_citation_present:null}]},
      {key:'brand_search',state:'unknown',note:'点名题的来源未完整暴露，保留未知。',cases:[{case_id:'q02',client_mode:'consumer',model_label:'快速',official_citation_present:null}]},
      {key:'website_guided',state:'observed',note:'给网址题出现官网来源，正文引用未确认。',cases:[{case_id:'q03',client_mode:'consumer',model_label:'快速',official_citation_present:null}]}],
    priorities:[{key:'scenario_coverage',label:'补自然需求的场景内容',reason:'围绕原题补已核对的适用场景，然后同题复测。',case_ids:['q01'],action_key:'review_drafts'}]
  }
})

const load=path=>{
  const filename=new URL(path,import.meta.url),source=fs.readFileSync(filename,'utf8'),{descriptor,errors}=parse(source,{filename:filename.pathname})
  assert.deepEqual(errors,[])
  const script=compileScript(descriptor,{id:path})
  const declarations=script.scriptSetupAst.flatMap(node=>node.type==='VariableDeclaration'?node.declarations:[])
  return {filename,source,descriptor,script,names:declarations.map(item=>item.id.name)}
}
const component=load('../src/components/GeoDiscoveryFunnel.vue')
const setup=(props,emit=()=>{})=>{
  const dependencies={computed:Vue.computed,discoveryFunnelView,defineProps:()=>props,defineEmits:()=>emit}
  return new Function(...Object.keys(dependencies),component.descriptor.scriptSetup.content.replace(/^import .*$/gm,'')+`;return {${component.names.join(',')}};`)(...Object.values(dependencies))
}
const compiledRender=()=>{
  const template=compileTemplate({source:component.descriptor.template.content,filename:component.filename.pathname,id:'discovery-funnel',compilerOptions:{bindingMetadata:component.script.bindings}})
  assert.deepEqual(template.errors,[])
  const code=template.code.replace(/import \{([^}]+)\} from "vue"/g,(_all,imports)=>`const {${imports.split(',').map(item=>{const [name,alias]=item.trim().split(/\s+as\s+/);return alias?`${name}:${alias}`:name}).join(',')}}=Vue;`).replace('export function render','return function render')
  return new Function('Vue',code)(Vue)
}
const render=async props=>{
  const app=Vue.createSSRApp({props:['cycle','busy'],setup:input=>setup(input),render:compiledRender()},props)
  app.component('ElButton',{props:{disabled:Boolean,text:Boolean},setup:(input,{slots})=>()=>Vue.h('button',{disabled:input.disabled},slots.default?.())})
  return renderToString(app)
}

test('discovery stages keep natural mention, brand sources and URL-guided sources separate without an effect score',()=>{
  const cycle=fixture(),before=structuredClone(cycle),view=discoveryFunnelView(cycle)
  assert.deepEqual(view.rows.map(row=>row.key),['natural_discovery','brand_search','website_guided'])
  assert.deepEqual(view.rows.map(row=>row.state),['not_observed','unknown','observed'])
  assert.deepEqual(view.rows.map(row=>row.cases[0].id),['q01','q02','q03'])
  assert.equal(view.rows[1].cases[0].question,cycle.test_plan.cases[1].question)
  assert.equal(view.rows[1].cases[0].mode,'普通对话')
  for(const key of ['score','success_rate','conversion_rate','improved']) assert.equal(Object.hasOwn(view,key),false)
  assert.deepEqual(cycle,before)
})

test('unknown source and citation values remain unknown instead of becoming not observed or false',()=>{
  const cycle=fixture()
  for(const value of [undefined,null,'false',0]){
    cycle.discovery_funnel.stages[1].state=value
    cycle.discovery_funnel.stages[1].cases[0].official_citation_present=value
    const row=discoveryFunnelView(cycle).rows[1]
    assert.equal(row.state,'unknown');assert.equal(row.stateLabel,'待核对')
    assert.equal(row.cases[0].citation,'正文引用：未知')
  }
  cycle.discovery_funnel.stages[1].state='observed'
  cycle.discovery_funnel.stages[1].cases[0].official_citation_present=false
  assert.equal(discoveryFunnelView(cycle).rows[1].cases[0].citation,'正文引用：未观察到')
  cycle.discovery_funnel.stages[1].cases[0].official_citation_present=true
  assert.equal(discoveryFunnelView(cycle).rows[1].cases[0].citation,'正文引用：有')
})

test('missing contracts, unsupported projections and mismatched run or report plan hide the diagnostic',()=>{
  const values=[null,{}, {...fixture(),id:null},{...fixture(),baseline:null},{...fixture(),discovery_funnel:null}]
  for(const change of [
    value=>{value.discovery_funnel.schema_version='future-schema'},
    value=>{value.discovery_funnel.supported=false},
    value=>{value.discovery_funnel.run_id='foreign-run'},
    value=>{value.baseline.run_id=''},
    value=>{value.discovery_funnel.plan_sha256='b'.repeat(64)},
    value=>{value.baseline.plan_sha256=''},
    value=>{value.discovery_funnel.stages={}}
  ]){const value=fixture();change(value);values.push(value)}
  for(const value of values) assert.equal(discoveryFunnelView(value),null)
})

test('changing the frozen test plan hides stale projections even when their baseline run still matches',()=>{
  for(const changedPlan of [null,{}, {...fixture().test_plan,plan_sha256:'b'.repeat(64)}]){
    const value=fixture();value.test_plan=changedPlan
    assert.equal(discoveryFunnelView(value),null)
  }
})

test('external observations, research imports and unknown baseline sources do not enter native discovery',()=>{
  for(const source of ['user_reported_external','research_import','external_report','',undefined]){
    const value=fixture();value.baseline_source=source
    assert.equal(discoveryFunnelView(value),null)
  }
  const value=fixture();value.probes=[{assessment:{official_source_present:true}}]
  value.discovery_funnel.stages[1].state='unknown'
  assert.equal(discoveryFunnelView(value).rows[1].state,'unknown')
})

test('unconfirmed or inconsistent conditions keep observations readable but provide no navigation priorities',async()=>{
  for(const matched of [false,null,undefined,'true']){
    const value=fixture();value.discovery_funnel.comparability={matched}
    const view=discoveryFunnelView(value)
    assert.equal(view.rows.length,3);assert.equal(view.matched,false)
    assert.deepEqual(view.priorities,[])
    const html=await render({cycle:value,busy:false})
    assert.match(html,/模式或模型条件不一致或尚未确认/)
    assert.ok(!html.includes('去处理'));assert.ok(!html.includes('<button'))
  }
})

test('actions use only known original cases and allowed keys, with the target constructed from the current cycle',()=>{
  const value=fixture()
  value.discovery_funnel.priorities=[
    {...value.discovery_funnel.priorities[0],action_key:'review_webmaster',case_ids:['q02','q03'],target:{cycle_id:'foreign-cycle'},cycle_id:'foreign-cycle'},
    {key:'foreign-case',label:'外部问题',reason:'不属于本轮',case_ids:['q99'],action_key:'review_facts'},
    {key:'unknown-action',label:'外部操作',reason:'未接入动作',case_ids:['q01'],action_key:'publish_now'}]
  const view=discoveryFunnelView(value)
  assert.equal(view.priorities.length,1)
  assert.deepEqual(view.priorities[0].action,{key:'review_webmaster',target:{cycle_id:'cycle-current'}})
  assert.ok(!JSON.stringify(view.priorities).includes('foreign-cycle'))
  const props=Vue.reactive({cycle:value,busy:false}),state=setup(props)
  props.cycle={...structuredClone(value),id:'cycle-next'}
  assert.deepEqual(state.view.value.priorities[0].action.target,{cycle_id:'cycle-next'})
  for(const ids of [[],['q02','q99'],[2],null]){
    value.discovery_funnel.priorities=[{key:'invalid',case_ids:ids,action_key:'review_facts'}]
    assert.deepEqual(discoveryFunnelView(value).priorities,[])
  }
})

test('stage rows cannot substitute a different category or a question not in the frozen plan',()=>{
  const value=fixture()
  value.discovery_funnel.stages[0].cases.push({case_id:'q02',client_mode:'work',model_label:'伪造跨类行'},{case_id:'q99'})
  value.discovery_funnel.stages.push({key:'unknown_stage',state:'observed',cases:[{case_id:'q01'}]})
  value.discovery_funnel.stages.push({...value.discovery_funnel.stages[0],state:'observed'})
  const view=discoveryFunnelView(value)
  assert.equal(view.rows.length,3);assert.equal(view.rows[0].state,'not_observed')
  assert.deepEqual(view.rows[0].cases.map(row=>row.id),['q01'])
  assert.ok(!JSON.stringify(view).includes('伪造跨类行'))
})

test('Vue SSR escapes dynamic questions, notes and action copy, preserving collapsed evidence and visible unknowns',async()=>{
  const value=fixture(),attack='<img src=x onerror=alert(1)>'
  value.test_plan.cases[1].question=attack
  value.discovery_funnel.stages[1].note=attack
  value.discovery_funnel.note='<script>alert(2)</script>'
  value.discovery_funnel.priorities[0].label=attack
  value.discovery_funnel.priorities[0].reason=attack
  const html=await render({cycle:value,busy:false})
  assert.match(html,/&lt;img src=x onerror=alert\(1\)&gt;/)
  assert.match(html,/&lt;script&gt;alert\(2\)&lt;\/script&gt;/)
  assert.ok(!html.includes('<img'));assert.ok(!html.includes('<script>'))
  assert.match(html,/待核对/);assert.match(html,/正文引用：未知/)
  assert.match(html,/给网址能阅读，都不等于自然推荐或优化成功/)
  assert.ok(!/<details[^>]*\bopen(?:[\s=>])/.test(html))
  assert.ok(!component.source.includes('v-html'));assert.ok(!component.source.includes('window.open'))
})

test('busy disables every action button while keeping current observations available',async()=>{
  const value=fixture()
  value.discovery_funnel.priorities.push({key:'identity',label:'核对身份依据',reason:'先检查已确认事实。',case_ids:['q02'],action_key:'review_facts'})
  const idle=await render({cycle:value,busy:false}),busy=await render({cycle:value,busy:true})
  assert.equal((idle.match(/<button\b/g)||[]).length,2)
  assert.ok(!/<button[^>]*\bdisabled(?:[\s=>])/.test(idle))
  assert.equal((busy.match(/<button\b[^>]*\bdisabled(?:[\s=>])/g)||[]).length,2)
  assert.match(busy,/自然需求中的品牌提及/);assert.match(busy,/去处理/)
})

test('both workflow pages compile with a single shared diagnostic and controlled navigation',()=>{
  for(const path of ['../src/views/GeoJourney.vue','../src/views/GeoContentLoop.vue']){
    const page=load(path)
    const entries=page.source.match(/<GeoDiscoveryFunnel\b[\s\S]*?\/>/g)||[]
    assert.equal(entries.length,1)
    for(const binding of [':cycle="cycle"',':busy="busy"','@navigate="navigateDiagnostic"']) assert.ok(entries[0].includes(binding),binding)
    const template=compileTemplate({source:page.descriptor.template.content,filename:page.filename.pathname,id:path,compilerOptions:{bindingMetadata:page.script.bindings}})
    assert.deepEqual(template.errors,[])
  }
  assert.deepEqual(compileStyle({source:component.descriptor.styles[0].content,id:'discovery-funnel'}).errors,[])
})
