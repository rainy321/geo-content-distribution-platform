import assert from 'node:assert/strict'
import test from 'node:test'
import fs from 'node:fs'
import {parse,compileScript,compileTemplate,compileStyle} from '@vue/compiler-sfc'
import {nextActions,diagnosticSection,focusDiagnosticSection} from '../src/utils/geoNextActions.js'
const action=(key='review_sources',changes={})=>({id:'action-1',key,label:'核对来源',reason:'先核对实际资料，再继续写作。',target:{cycle_id:'cycle-a'},evidence_ids:['source:1'],...changes})
const cycle=changes=>({id:'cycle-a',feedback_diagnostics:{schema_version:'geo_feedback_diagnostics_v1',cycle_id:'cycle-a',read_only:true,
  evidence:[{id:'source:1',label:'已保存的来源'}],attention_items:[{message:'来源网址未知',evidence_ids:['source:1','missing']}],actions:[action()],...changes}})
test('next steps remain scoped read-only navigation and retain only known evidence labels',()=>{
  const view=nextActions(cycle())
  assert.equal(view.actions[0].section,'sources');assert.deepEqual(view.attention[0].evidence_ids,['source:1'])
  assert.equal(diagnosticSection(view.actions[0],'cycle-a'),'sources')
  assert.equal(diagnosticSection(view.actions[0],'cycle-b'),null)
  assert.deepEqual(nextActions(cycle({cycle_id:'cycle-b'})).actions,[])
  assert.deepEqual(nextActions(cycle({read_only:false})).actions,[])
})
test('unrecognized commands, foreign targets, URL selectors and duplicate entries are not actionable',()=>{
  const view=nextActions(cycle({actions:[action(),action(),action('submit_publish',{id:'two'}),
    action('review_drafts',{id:'three',target:{cycle_id:'cycle-a',url:'https://example.org'}}),
    action('review_facts',{id:'four',target:{cycle_id:'cycle-b'}}),
    action('review_publication',{id:'five',target:{cycle_id:'cycle-a',article_id:9}})]}))
  assert.deepEqual(view.actions.map(item=>item.id),['action-1','five'])
  assert.equal(diagnosticSection({key:'constructor',target:{cycle_id:'cycle-a'}},'cycle-a'),null)
})
test('commands move focus only to a fixed local section; nothing clicks or submits',()=>{
  const calls=[],element={scrollIntoView:value=>calls.push(['scroll',value]),setAttribute:(...args)=>calls.push(['attribute',...args]),focus:value=>calls.push(['focus',value])}
  const root={querySelector:selector=>{calls.push(['query',selector]);return element}}
  assert.equal(focusDiagnosticSection('javascript:alert(1)',root),false)
  assert.deepEqual(calls,[])
  assert.equal(focusDiagnosticSection('facts',root),true)
  assert.equal(calls[0][1],'[data-geo-section="facts"]')
  assert.equal(calls.at(-1)[0],'focus')
  assert.equal(focusDiagnosticSection('facts',{querySelector:()=>null}),false)
})
test('a suggested secondary task opens its existing disclosure before moving focus',()=>{
  const disclosure={tagName:'DETAILS',open:false,parentElement:null},element={parentElement:disclosure,
    scrollIntoView(){assert.equal(disclosure.open,true)},setAttribute(){},focus(){}}
  assert.equal(focusDiagnosticSection('maintenance',{querySelector:()=>element}),true)
  assert.equal(disclosure.open,true)
})
test('a retained prior-cycle company request stays reachable only through the exact saved task binding',()=>{
  const current=cycle({actions:[action('resume_source_maintenance',{target:{cycle_id:'old-cycle',task_id:'task-1'}})]})
  current.profile={name:'示例公司有限公司',entity_type:'company'}
  current.source_maintenance={tasks:[{id:'task-1',cycle_id:'old-cycle',company_name:'示例公司有限公司'}]}
  const selected=nextActions(current).actions
  assert.equal(selected.length,1);assert.equal(diagnosticSection(selected[0],current),'maintenance')
  assert.equal(diagnosticSection(selected[0],'cycle-a'),null)
  for(const change of [{id:'other-task'},{cycle_id:'other-cycle'},{company_name:'其他公司有限公司'}]){
    const altered=structuredClone(current);Object.assign(altered.source_maintenance.tasks[0],change)
    assert.equal(nextActions(altered).actions.length,0)
  }
  assert.equal(nextActions({...current,profile:{...current.profile,entity_type:'product'}}).actions.length,0)
})
test('compact action panel compiles with three initial actions and a keyboard readable evidence disclosure',()=>{
  const source=fs.readFileSync(new URL('../src/components/GeoNextActions.vue',import.meta.url),'utf8')
  const {descriptor}=parse(source),script=compileScript(descriptor,{id:'next-actions'})
  const template=compileTemplate({source:descriptor.template.content,filename:'GeoNextActions.vue',id:'next-actions',compilerOptions:{bindingMetadata:script.bindings}})
  assert.deepEqual(template.errors,[])
  assert.deepEqual(compileStyle({source:descriptor.styles[0].content,id:'next-actions'}).errors,[])
  assert.ok(source.includes('expanded ? 6 : 3'));assert.ok(source.includes('<details'))
  assert.ok(!source.includes('v-html'));assert.ok(!source.includes('window.open'))
})
