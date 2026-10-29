import assert from 'node:assert/strict'
import fs from 'node:fs'
import test from 'node:test'
import * as Vue from 'vue'
import {renderToString} from '@vue/server-renderer'
import {parse,compileScript,compileTemplate,compileStyle} from '@vue/compiler-sfc'
import {optimizationPlanView,optimizationPlanSelection} from '../src/utils/geoOptimizationPlan.js'

const cases=[{id:'q01',category:'discovery',question:'怎样选择适合小团队的协作工具？'},
  {id:'q02',category:'brand_identity',question:'示例协作平台提供哪些公开业务？'},
  {id:'q03',category:'website_guided',question:'请按 https://example.org/product 的公开资料核对能力。'}]
const goalKeys=['natural_discovery','identity_accuracy','official_source_visibility']
const fixture=()=>{
  const briefs=cases.map((item,index)=>({id:`b0${index+1}`,topic:['团队选型指南','业务与运营关系','官网公开能力'][index],case_ids:[item.id],source_ids:[`s00${index+1}`]}))
  return {id:'cycle-a',test_plan:{cases:structuredClone(cases)},analysis:{briefs},observations:{sources:cases.map((item,index)=>({id:`s00${index+1}`,case_id:item.id}))},opportunities:briefs.map((brief,index)=>({id:`opportunity-${index+1}`,source_ids:brief.source_ids})),
    optimization_plan:{schema_version:'geo_optimization_plan_v1',questions:cases.map((item,index)=>({case_id:item.id,category:item.category,question:item.question,goal:goalKeys[index],label:'服务端目标',action_ids:[briefs[index].id]})),
      actions:briefs.map((brief,index)=>({...brief,goals:[{key:goalKeys[index],label:'服务端目标'}],gap:`缺口${index+1}：当前回答的公开依据不足。`,change_summary:`改动${index+1}：补充已核对的能力与使用范围。`,writing_requirements:['以已确认事实解释适用场景。'],evidence_needed:['核对公开功能页与说明日期。'],
        source_routes:[{id:`opportunity-${index+1}`,label:'已有来源线索',surface:'owned_site',source_ids:brief.source_ids}],
        acceptance_checks:[{case_id:cases[index].id,question:cases[index].question,goal:goalKeys[index],label:'服务端目标',signal:'按原题观察后续回答是否采用有依据的内容；未知仍保留未知。'}]})),
      coverage:{planned_questions:3,questions_with_actions:3,uncovered_case_ids:[]},needs_refresh:false,note:'这是问题安排，完成后仍需观察实际回答。'}}
}
const load=path=>{
  const filename=new URL(path,import.meta.url),source=fs.readFileSync(filename,'utf8'),{descriptor,errors}=parse(source,{filename:filename.pathname})
  assert.deepEqual(errors,[])
  const script=compileScript(descriptor,{id:path})
  const declarations=script.scriptSetupAst.flatMap(node=>node.type==='VariableDeclaration'?node.declarations:[])
  return {filename,source,descriptor,script,names:declarations.map(item=>item.id.name),expression:name=>{
    const declaration=declarations.find(item=>item.id.name===name);assert.ok(declaration,name)
    return descriptor.scriptSetup.content.slice(declaration.init.start,declaration.init.end)
  }}
}
const component=load('../src/components/GeoOptimizationPlan.vue')
const setup=(props,emit=()=>{})=>{
  const dependencies={computed:Vue.computed,optimizationPlanView,optimizationPlanSelection,defineProps:()=>props,defineEmits:()=>emit}
  return new Function(...Object.keys(dependencies),component.descriptor.scriptSetup.content.replace(/^import .*$/gm,'')+`;return {${component.names.join(',')}};`)(...Object.values(dependencies))
}
const render=async(props)=>{
  const template=compileTemplate({source:component.descriptor.template.content,filename:component.filename.pathname,id:'optimization-plan',compilerOptions:{bindingMetadata:component.script.bindings}})
  assert.deepEqual(template.errors,[])
  const code=template.code.replace(/import \{([^}]+)\} from "vue"/g,(_all,imports)=>`const {${imports.split(',').map(item=>{const [name,alias]=item.trim().split(/\s+as\s+/);return alias?`${name}:${alias}`:name}).join(',')}}=Vue;`).replace('export function render','return function render')
  const app=Vue.createSSRApp({props:['cycle','selectedBriefId','busy'],setup:input=>setup(input),render:new Function('Vue',code)(Vue)},props)
  return renderToString(app)
}

test('an optimization plan separates discovery, business identity and official-source goals without an effect score',()=>{
  const value=fixture(),view=optimizationPlanView(value,'b02')
  assert.equal(view.selected.id,'b02')
  assert.deepEqual(view.actions.map(item=>item.goals[0].label),['自然需求发现','品牌与业务识别','官网资料采用'])
  assert.equal(view.questionsWithActions,3)
  assert.equal(view.selected.checks[0].question,cases[1].question)
  assert.deepEqual(view.selected.sourceRoutes[0].sourceIds,['s002'])
  assert.equal(Object.hasOwn(view,'score'),false)
  assert.equal(Object.hasOwn(view,'success_rate'),false)
  assert.deepEqual(value,fixture())
})

test('older or malformed backend plans hide, rather than synthesizing an optimization result',()=>{
  for(const value of [null,{}, {...fixture(),optimization_plan:null},{...fixture(),optimization_plan:{schema_version:'unknown'}},
    {...fixture(),test_plan:{cases:{}}},{...fixture(),analysis:{briefs:{}}}]) assert.equal(optimizationPlanView(value),null)
  const value=fixture();value.optimization_plan.actions=[]
  assert.equal(optimizationPlanView(value),null)
})

test('stale questions, foreign brief IDs and changed brief topics cannot become selectable current actions',()=>{
  const value=fixture();value.optimization_plan.questions[0].question='后来改写的问题'
  value.optimization_plan.actions[1].id='foreign-brief'
  value.analysis.briefs[2].topic='另一个新选题'
  assert.equal(optimizationPlanView(value),null)
  assert.equal(optimizationPlanSelection(null,'b01'),null)
})

test('coverage is the actual question arrangement count and unmatched sources are not invented',()=>{
  const value=fixture();value.optimization_plan.actions.pop();value.optimization_plan.coverage.questions_with_actions=100
  value.optimization_plan.actions[0].source_routes.push({id:'unrelated',label:'不是本题的来源',surface:'community_candidate',source_ids:['s002','missing']})
  const view=optimizationPlanView(value)
  assert.equal(view.questionsWithActions,2);assert.deepEqual(view.uncoveredCaseIds,['q03'])
  assert.equal(view.actions[0].sourceRoutes.length,1)
  assert.equal(view.actions.some(action=>action.id==='b03'),false)
})

test('selection emits only an existing brief and keeps the displayed action controlled by the parent',()=>{
  const props=Vue.reactive({cycle:fixture(),selectedBriefId:'b01',busy:false}),events=[],page=setup(props,(...args)=>events.push(args))
  page.select('b02');assert.deepEqual(events,[['select','b02']])
  assert.equal(page.view.value.selected.id,'b01')
  props.selectedBriefId='b02';assert.equal(page.view.value.selected.id,'b02')
  props.busy=true;page.select('b03');props.busy=false;page.select('javascript:send()')
  assert.equal(events.length,1)
  props.cycle={...fixture(),id:'cycle-b',optimization_plan:null}
  page.select('b01');assert.equal(page.view.value,null);assert.equal(events.length,1)
})

test('domain, documentation and unknown source route keys stay bound to existing read-only opportunities',()=>{
  const value=fixture(),routeIds=['qcc.com','example.org:docs','unknown:source-group']
  routeIds.forEach((id,index)=>{
    value.opportunities[index].id=id
    value.optimization_plan.actions[index].source_routes[0].id=id
  })
  assert.deepEqual(optimizationPlanView(value).actions.map(action=>action.sourceRoutes[0].id),routeIds)
  value.optimization_plan.actions[0].source_routes[0].id='not-in-this-cycle.example'
  value.opportunities[1].source_ids=['s003']
  assert.equal(optimizationPlanView(value).actions[0].sourceRoutes.length,0)
  assert.equal(optimizationPlanView(value).actions[1].sourceRoutes.length,0)
})

test('current action renders readable requirements and exact fixed questions; evidence is collapsed and text escaped',async()=>{
  const value=fixture();value.optimization_plan.actions[1].gap='<img src=x onerror=alert(1)>'
  const html=await render({cycle:value,selectedBriefId:'b02',busy:false})
  assert.match(html,/已安排 3 道原题/);assert.match(html,/品牌与业务识别/)
  assert.match(html,/业务与运营关系/);assert.match(html,/示例协作平台提供哪些公开业务/)
  assert.match(html,/核对公开功能页/);assert.match(html,/来源编号 s002/)
  assert.match(html,/&lt;img src=x onerror=alert\(1\)&gt;/);assert.ok(!html.includes('<img'))
  assert.ok(!/<details[^>]*\bopen(?:[\s=>])/.test(html))
  assert.ok(!html.includes('缺口1'));assert.ok(!html.includes('缺口3'))
  assert.ok(!component.source.includes('v-html'));assert.ok(!component.source.includes('window.open'))
})

test('long summaries remain available in the disclosure while the initial plan stays compact',async()=>{
  const value=fixture();value.optimization_plan.actions[0].gap='合成缺口说明'.repeat(100)
  const html=await render({cycle:value,selectedBriefId:'b01',busy:false})
  assert.match(html,/完整缺口/);assert.match(html,/…/)
  assert.deepEqual(compileStyle({source:component.descriptor.styles[0].content,id:'optimization-plan'}).errors,[])
  assert.match(component.descriptor.styles[0].content,/prefers-reduced-motion:reduce/)
  assert.match(component.descriptor.styles[0].content,/max-width:600px/)
})

test('Journey promotes the plan before folded secondary actions and keeps the existing unsaved-editor confirmation',async()=>{
  const journey=load('../src/views/GeoJourney.vue'),source=journey.source
  assert.equal((source.match(/<GeoOptimizationPlan\b/g)||[]).length,1)
  assert.ok(source.indexOf('<GeoOptimizationProgress')<source.indexOf('<GeoOptimizationPlan'))
  assert.ok(source.indexOf('<GeoOptimizationPlan')<source.indexOf('<GeoNextActions'))
  assert.match(source,/<details v-if="cycle && step===2 && hasOptimizationPlan"[^>]*><summary>其他推进事项<\/summary>/)
  assert.match(source,/@select="selectContent\('brief',\$event\)"/)
  const briefId={value:'b01'},dependencies={busy:{value:false},editorDirty:{value:true},briefId,articleId:{value:9},platform:{value:'owned_web'},loadEditor:()=>assert.fail('must not load a different article on cancelled selection'),ElMessageBox:{confirm:async()=>{throw new Error('cancel')}}}
  const selectContent=new Function(...Object.keys(dependencies),`return (${journey.expression('selectContent')})`)(...Object.values(dependencies))
  await selectContent('brief','b02');assert.equal(briefId.value,'b01')
  dependencies.editorDirty.value=false;await selectContent('brief','b02');assert.equal(briefId.value,'b02')
})

test('both existing pages compile and the research plan follows analysis before destination or fact editing',()=>{
  for(const path of ['../src/views/GeoJourney.vue','../src/views/GeoContentLoop.vue']){
    const page=load(path),template=compileTemplate({source:page.descriptor.template.content,filename:page.filename.pathname,id:path,compilerOptions:{bindingMetadata:page.script.bindings}})
    assert.deepEqual(template.errors,[])
    if(path.includes('GeoContentLoop')){
      assert.equal((page.source.match(/<GeoOptimizationPlan\b/g)||[]).length,1)
      assert.ok(page.source.indexOf('research-briefs')<page.source.indexOf('<GeoOptimizationPlan'))
      assert.ok(page.source.indexOf('<GeoOptimizationPlan')<page.source.indexOf('<GeoDestinations'))
      assert.match(page.source,/@select="briefId=\$event"/)
    }
  }
})
