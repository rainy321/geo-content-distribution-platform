import assert from 'node:assert/strict'
import fs from 'node:fs'
import test from 'node:test'
import * as Vue from 'vue'
import {renderToString} from '@vue/server-renderer'
import {parse,compileScript,compileTemplate} from '@vue/compiler-sfc'
import {planningScopeView,planningRequestScope,planningTouchesDraft,latestPlanningScopeNotice,historicalPlanningDrafts} from '../src/utils/geoPlanningScope.js'
import {planningFeedbackNotice} from '../src/utils/geoPlanningFeedback.js'

const oldBriefs=()=>Array.from({length:6},(_,i)=>({id:`b0${i+1}`,case_ids:[`q0${i%3+1}`],topic:`原选题${i+1}`,source_ids:[],outline:['原依据']}))
const draft=(articleId,brief)=>({article_id:articleId,cycle_id:'cycle-a',brief_id:brief.id,platform:'zhihu',title:`原稿${articleId}`,content:`原正文${articleId}`,provenance:{brief:structuredClone(brief)}})
function cycle(){
  const briefs=oldBriefs(),cases=[1,2,3].map(i=>({id:`q0${i}`,question:`冻结原题 ${i}？`}))
  return {id:'cycle-a',project_id:5,test_plan:{cases},observations:{cases:cases.map(row=>({...row,usable:true}))},facts:[{id:'f01',text:'已有事实'}],
    analysis:{method:'ai_api',briefs},drafts:[draft(14,briefs[1]),draft(13,briefs[3])],channels:{zhihu:'知乎'},destinations:[]}
}
function archivedCycle(){
  const value=cycle(),prior=structuredClone(value.analysis)
  value.analysis={...value.analysis,method:'mixed_scope_planning',briefs:prior.briefs.filter(row=>!row.case_ids.includes('q02')).concat([{id:'b07',case_ids:['q02'],topic:'本次新选题'}]),
    history:[{operation_id:'run-a',created_at:'2026-11-06T08:00:00Z',analysis_scope:{case_ids:['q02']},analysis:prior}],
    latest_scope:{mode:'selected_case',case_ids:['q02'],brief_ids:['b07']}}
  return value
}
test('all questions remain the explicit default and only analysis carries a selected scope',()=>{
  const value=cycle(),before=structuredClone(value)
  assert.equal(planningScopeView(value).selectedCaseId,'')
  assert.equal(planningRequestScope(value,'','analysis'),undefined)
  assert.deepEqual(planningRequestScope(value,'q02','analysis'),{case_ids:['q02']})
  assert.equal(planningRequestScope(value,'q02','draft'),undefined)
  assert.deepEqual(value,before)
  const view=planningScopeView(value,'q02')
  assert.equal(view.retainedBriefCount,4);assert.equal(view.replacedBriefCount,2);assert.equal(view.maxBriefs,2)
  assert.equal(view.selectedQuestion.question,'冻结原题 2？');assert.match(view.message,/其他 4 条选题保留/)
  assert.match(view.message,/不代表整体 GEO 优化通过/)
})
test('invalid, unusable, mixed, no-capacity and full-history scopes stop before a request',()=>{
  const variants=[['q99',cycle()],['q02',{...cycle(),observations:{cases:[]}}]]
  const mixed=cycle();mixed.analysis.briefs[1].case_ids=['q02','q01'];variants.push(['q02',mixed])
  const full=cycle();full.analysis.briefs.forEach(row=>{row.case_ids=['q01']});variants.push(['q02',full])
  const history=cycle();history.analysis.history=Array.from({length:20},()=>({}));variants.push(['q02',history],['',history])
  for(const [selected,value]of variants){const before=structuredClone(value);assert.equal(planningScopeView(value,selected).allowed,false);assert.throws(()=>planningRequestScope(value,selected,'analysis'));assert.deepEqual(value,before)}
  const one=cycle();one.analysis.briefs[4].case_ids=['q01'];assert.equal(planningScopeView(one,'q02').maxBriefs,1)
  assert.match(planningScopeView(mixed,'q02').blockedReason,/不能自动拆分/)
  assert.match(planningScopeView(full,'q02').blockedReason,/无新增容量/)
})
test('selected q02 does not consume q01 local editing and partial status is never whole-project current',()=>{
  const value=cycle()
  assert.equal(planningTouchesDraft(value,'q02',value.drafts[1]),false)
  assert.equal(planningTouchesDraft(value,'q02',value.drafts[0]),true)
  assert.equal(planningTouchesDraft(value,'',value.drafts[1]),true)
  const partial=archivedCycle();partial.feedback_diagnostics={planning_feedback:{available_count:6,context_status:'partial',needs_update:true}}
  const notice=planningFeedbackNotice(partial)
  assert.match(notice.message,/仅更新 q02/);assert.match(notice.message,/其他原题选题保留/);assert.match(notice.message,/不代表整体 GEO 效果/)
  assert.equal(notice.canReplan,true)
  partial.feedback_diagnostics.planning_feedback.available_count=0
  assert.match(planningFeedbackNotice(partial).message,/仅更新 q02/)
  partial.analysis.latest_scope={mode:'all_cases',case_ids:['q01','q02','q03']}
  assert.match(latestPlanningScopeNotice(partial),/按全部原题策划/)
  assert.doesNotMatch(latestPlanningScopeNotice(partial),/最近仅更新/)
})
test('only exact archived original brief and same-cycle article identify a historical draft',()=>{
  const value=archivedCycle(),before=structuredClone(value)
  const items=historicalPlanningDrafts(value)
  assert.deepEqual(items.map(item=>item.draft.article_id),[14]);assert.equal(items[0].brief.id,'b02')
  assert.equal(value.analysis.briefs.find(row=>row.id==='b07').id,'b07');assert.deepEqual(value,before)
  const forged=structuredClone(value);forged.drafts[0].provenance.brief.topic='别的选题';assert.equal(historicalPlanningDrafts(forged).length,0)
  const foreign=structuredClone(value);foreign.drafts[0].cycle_id='another-cycle';assert.equal(historicalPlanningDrafts(foreign).length,0)
})

const sfc=name=>{
  const filename=new URL(`../${name}`,import.meta.url),source=fs.readFileSync(filename,'utf8'),descriptor=parse(source).descriptor,script=compileScript(descriptor,{id:name})
  const declaration=key=>script.scriptSetupAst.flatMap(node=>node.type==='VariableDeclaration'?node.declarations:[]).find(node=>node.id.name===key)
  return {descriptor,script,expression:key=>{const node=declaration(key);assert.ok(node,key);return descriptor.scriptSetup.content.slice(node.init.start,node.init.end)}}
}
const journey=sfc('src/views/GeoJourney.vue'),content=sfc('src/views/GeoContentLoop.vue')
function generator(page,selected='',kindProvider='api'){
  const calls=[],errors=[],value=cycle(),state={cycle:Vue.ref(value),cycleId:Vue.ref(value.id),planningCaseId:Vue.ref(selected),provider:Vue.ref(kindProvider),
    briefId:Vue.ref('b04'),platform:Vue.ref('zhihu'),articleId:Vue.ref(13),working:Vue.ref(false),task:Vue.ref({status:'idle'}),cycles:Vue.ref([]),
    contentRecovery:Vue.ref(null),recoveryUnreadable:Vue.ref(false)}
  const deps={...state,planningRequestScope,writingChannelEnabled:()=>true,selectedTarget:Vue.ref(null),destinationPublicationGate:()=>({reason:''}),
    requireArticleChannel:()=>{},directoryTargetForChannel:()=>null,options:()=>({client_mode:'consumer'}),cycleScope:()=>({id:value.id}),sameCycleScope:()=>true,
    startNative:async(_kind,fn)=>fn(),window:{pywebview:{api:{generate_research:async(...args)=>{calls.push(['native',...args]);return {id:'task-a',status:'running'}}}}},
    geoContentApi:{generate:async(...args)=>{calls.push(['api',...args]);return {data:{cycle:value}}},list:async()=>({data:[]})},
    applyCycle:(...args)=>{calls.push(['apply',...args]);return true},loadEditor:()=>{},rememberContentRecovery:()=>{},storeContentRecovery:()=>{},
    ElMessage:{success:()=>{},info:()=>{},error:message=>errors.push(message)},ElMessageBox:{confirm:async()=>{}}}
  const fn=new Function(...Object.keys(deps),`return (${page.expression(page===journey?'generateResearch':'generate')})`)(...Object.values(deps))
  return {...state,calls,errors,invoke:kind=>page===journey?fn(kind):fn(kind,kindProvider)}
}
test('both actual page generators send exact selected scope for native/API analysis only',async()=>{
  for(const page of [journey,content])for(const provider of ['api','doubao'])for(const selected of ['', 'q02']){
    const h=generator(page,selected,provider);await h.invoke('analysis')
    const request=h.calls.find(row=>row[0]===(provider==='api'?'api':'native'));assert.ok(request)
    if(provider==='api'){
      assert.deepEqual(request[2].analysis_scope,selected?{case_ids:['q02']}:undefined)
      if(page===journey)assert.equal(h.calls.find(row=>row[0]==='apply')[2].keepEditor,true)
    }else{assert.equal(request.length,selected?7:6);assert.deepEqual(request[6],selected?{case_ids:['q02']}:undefined)}
    const d=generator(page,'q02',provider);await d.invoke('draft');const normal=d.calls.find(row=>row[0]===(provider==='api'?'api':'native'))
    if(provider==='api')assert.equal(Object.hasOwn(normal[2],'analysis_scope'),false);else assert.equal(normal.length,6)
  }
})
test('both actual generators stop invalid scopes without calling any AI bridge or API',async()=>{
  for(const page of [journey,content])for(const provider of ['api','doubao']){
    const h=generator(page,'q99',provider)
    if(page===journey)await assert.rejects(()=>h.invoke('analysis'));else await h.invoke('analysis')
    assert.equal(h.calls.some(row=>['api','native'].includes(row[0])),false)
  }
})
test('each page cycle watcher resets selection without sharing brief selection or another cycle',async()=>{
  for(const page of [journey,content]){
    const source=page.descriptor.scriptSetup.content
    const statement=page.script.scriptSetupAst.find(node=>node.type==='ExpressionStatement'&&node.expression?.callee?.name==='watch'
      &&node.expression.arguments[0]?.name==='cycleId'&&source.slice(node.start,node.end).includes('planningCaseId'))
    assert.ok(statement)
    const cycleId=Vue.ref('cycle-a'),planningCaseId=Vue.ref('q02'),briefId=Vue.ref('b04')
    const stop=new Function('watch','cycleId','planningCaseId',`return ${source.slice(statement.start,statement.end).replace(/;$/,'')}`)(Vue.watch,cycleId,planningCaseId)
    cycleId.value='cycle-b';assert.equal(planningCaseId.value,'');assert.equal(briefId.value,'b04');stop()
  }
})
test('actual historical edit uses original article14 without rebinding current article13 or new b07',async()=>{
  const value=archivedCycle(),cycleRef=Vue.ref(value),calls=[],state={briefId:'b04',articleId:13,editor:'未保存正文13'}
  const deps={historicalPlanningDrafts,cycle:cycleRef,busy:Vue.ref(false),saveError:Vue.ref(''),directoryTargetForChannel:()=>null,persist:()=>calls.push('persist'),router:{push:async path=>calls.push(path)}}
  const edit=new Function(...Object.keys(deps),`return (${journey.expression('editHistoricalDraft')})`)(...Object.values(deps))
  await edit(value.drafts[0]);assert.deepEqual(calls,['persist',{path:'/content-create',query:{articleId:14}}]);assert.deepEqual(state,{briefId:'b04',articleId:13,editor:'未保存正文13'})
  const blocked=new Function(...Object.keys(deps),`return (${journey.expression('editHistoricalDraft')})`)(...Object.values({...deps,directoryTargetForChannel:()=>({id:'directory'})}))
  calls.length=0;await blocked(value.drafts[0]);await edit({article_id:999});assert.deepEqual(calls,[])
  const cannotPersist=new Function(...Object.keys(deps),`return (${journey.expression('editHistoricalDraft')})`)(...Object.values({...deps,persist:()=>{deps.saveError.value='本机保存失败'}}))
  await cannotPersist(value.drafts[0]);assert.deepEqual(calls,[])
})

const renderComponent=async(name,props)=>{
  const page=sfc(`src/components/${name}.vue`),template=compileTemplate({source:page.descriptor.template.content,filename:name,id:name})
  assert.deepEqual(template.errors,[])
  const code=template.code.replace(/import \{([^}]+)\} from "vue"/g,(_all,imports)=>`const {${imports.split(',').map(value=>value.trim().replace(/\s+as\s+/,':')).join(',')}}=Vue;`).replace('export function render','return function render')
  const render=new Function('Vue',code)(Vue)
  const app=Vue.createSSRApp({setup:()=>({...props,view:planningScopeView(props.cycle,props.selectedCaseId||''),notice:latestPlanningScopeNotice(props.cycle),items:historicalPlanningDrafts(props.cycle),directoryTargetForChannel:()=>null}),render})
  app.component('el-button',{setup:(_props,{attrs,slots})=>()=>Vue.h('button',attrs,slots.default?.())})
  return renderToString(app)
}
test('real templates show full frozen question, explicit all/default, capacity and isolated historical plain text',async()=>{
  const defaultHtml=await renderComponent('GeoPlanningScope',{cycle:cycle(),selectedCaseId:'',busy:false,actionLabel:'按所选范围策划'})
  assert.match(defaultHtml,/option value(?:="")?>全部原题/)
  const html=await renderComponent('GeoPlanningScope',{cycle:cycle(),selectedCaseId:'q02',busy:false,actionLabel:'按所选范围策划'})
  assert.match(html,/原题：冻结原题 2？/);assert.match(html,/最多生成 2 条/);assert.match(html,/不代表整体 GEO 优化通过/)
  const history=archivedCycle();history.drafts[0].content='<img src=x onerror=alert(1)> 原正文14'
  const historical=await renderComponent('GeoHistoricalPlanningDrafts',{cycle:history,busy:false})
  assert.match(historical,/原选题 b02/);assert.match(historical,/编辑已有稿件/);assert.match(historical,/&lt;img/)
  assert.doesNotMatch(historical,/<img|data-article-id="13"|原选题 b07|打开目标平台|进入发布中心/)
  for(const page of [journey,content])assert.deepEqual(compileTemplate({source:page.descriptor.template.content,filename:'page.vue',id:'page',compilerOptions:{bindingMetadata:page.script.bindings}}).errors,[])
})
