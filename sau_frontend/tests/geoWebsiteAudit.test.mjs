import assert from 'node:assert/strict'
import fs from 'node:fs'
import test from 'node:test'
import * as Vue from 'vue'
import {parse,compileScript,compileTemplate,compileStyle} from '@vue/compiler-sfc'
import * as audit from '../src/utils/geoWebsiteAudit.js'
import {externalWriteUncertain} from '../src/utils/geoExternalFeedback.js'
import {publicationReviewUrl} from '../src/utils/geoPublicationReview.js'
import {journeyDate} from '../src/utils/geoJourney.js'
const view=(changes={})=>({supported:true,context_sha256:'b'.repeat(64),audit_revision:0,
  allowed_pages:[{id:'page-1',url:'https://example.org/about'}],history:[],latest:null,...changes})
const cycle=(changes={})=>({id:'cycle-a',project_id:4,revision:2,test_plan:{plan_sha256:'a'.repeat(64)},website_audit:view(),...changes})
const record=(changes={})=>({id:'audit-1',cycle_id:'cycle-a',project_id:4,cycle_revision:2,plan_sha256:'a'.repeat(64),
  context_sha256:'b'.repeat(64),revision:1,selected_page_ids:['page-1'],evidence_source:'software_public_http',index_status:'unknown',ai_adoption:'unknown',status:'completed',pages:[],...changes})
const reply=(item=record())=>({code:200,data:{audit:item,overview:view({audit_revision:1,latest:item,history:[item]})}})
const deferred=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no});return {promise,resolve,reject}}
test('website checks accept only bounded configured page IDs under the current cycle scope',()=>{
  const snapshot=audit.auditSnapshot(cycle(),view(),['page-1'])
  assert.deepEqual(snapshot.payload.page_ids,['page-1']);assert.ok(!Object.hasOwn(snapshot.payload,'url'))
  for(const selected of [[],['outside'],['page-1','page-1'],['https://example.org']])assert.throws(()=>audit.auditSnapshot(cycle(),view(),selected))
  assert.throws(()=>audit.auditSnapshot(cycle(),view({context_sha256:'c'.repeat(64)}),['page-1']))
  assert.throws(()=>audit.auditSnapshot(cycle(),view({allowed_pages:[{id:'page-1',url:'javascript:alert(1)'}]}),['page-1']))
})
test('a website receipt cannot claim indexing/adoption or belong to another reservation',()=>{
  const snapshot=audit.auditSnapshot(cycle(),view(),['page-1'])
  assert.equal(audit.auditReceipt(reply(),snapshot).audit_revision,1)
  for(const changed of [{cycle_id:'cycle-b'},{project_id:5},{revision:9},{selected_page_ids:['other']},{index_status:'indexed'},{ai_adoption:'yes'}]){
    assert.equal(audit.auditRunMatches(record(changed),snapshot),false)
    assert.throws(()=>audit.auditReceipt(reply(record(changed)),snapshot))
  }
})
const source=fs.readFileSync(new URL('../src/components/GeoWebsiteAudit.vue',import.meta.url),'utf8')
const {descriptor}=parse(source),script=compileScript(descriptor,{id:'website-audit'})
const names=script.scriptSetupAst.filter(row=>row.type==='VariableDeclaration').flatMap(row=>row.declarations).flatMap(row=>row.id.type==='Identifier'?[row.id.name]:[])
const body=descriptor.scriptSetup.content.replace(/^import .*$/gm,'')
function harness(api={}){
  const props=Vue.reactive({cycle:cycle(),busy:false}),events=[],effect=Vue.effectScope(),unmount=[]
  const deps={computed:Vue.computed,onUnmounted:callback=>unmount.push(callback),ref:Vue.ref,watch:Vue.watch,...audit,externalWriteUncertain,publicationReviewUrl,journeyDate,
    defineProps:()=>props,defineEmits:()=>((...args)=>events.push(args)),geoWebsiteAuditApi:{get:async()=>({code:200,data:view()}),run:async()=>reply(),...api}}
  const methods=effect.run(()=>new Function(...Object.keys(deps),body+`;return {${names.join(',')}}`)(...Object.values(deps)))
  return {...methods,props,events,stop(){unmount.forEach(fn=>fn());effect.stop()}}
}
test('the panel does not fetch websites on mount and rapid clicks create one explicit run',async()=>{
  const result=deferred(),calls=[],page=harness({run:(...args)=>{calls.push(args);return result.promise}})
  assert.equal(calls.length,0)
  const pending=page.run();await page.run();assert.equal(calls.length,1)
  result.resolve(reply());await pending
  assert.equal(page.view.value.latest.status,'completed');assert.equal(page.uncertain.value,false);page.stop()
})
test('lost acknowledgement retries only the same immutable reservation',async()=>{
  const calls=[],page=harness({run:async(...args)=>{calls.push(args);if(calls.length===1)throw new Error('network');return reply()}})
  await page.run();assert.equal(page.uncertain.value,true)
  page.selected.value=[];await page.run()
  assert.deepEqual(calls[0][1],calls[1][1]);assert.equal(page.view.value.audit_revision,1);page.stop()
})
test('read-only reconciliation finds the same completed run without visiting the site again',async()=>{
  let writes=0;const page=harness({run:async()=>{writes++;throw new Error('network')},get:async()=>({code:200,data:reply().data.overview})})
  await page.run();assert.equal(page.uncertain.value,true);await page.refresh()
  assert.equal(page.uncertain.value,false);assert.equal(writes,1);assert.equal(page.latest.value.status,'completed');page.stop()
})
test('A to B to A and revised facts cannot apply an old website receipt',async()=>{
  const result=deferred(),page=harness({run:()=>result.promise})
  const pending=page.run();page.props.cycle=cycle({id:'cycle-b'});await Vue.nextTick();page.props.cycle=cycle();await Vue.nextTick()
  result.resolve(reply());await pending;assert.equal(page.view.value.latest,null);page.stop()
  const other=deferred(),changed=harness({run:()=>other.promise});const request=changed.run()
  changed.props.cycle=cycle({revision:3,website_audit:view({context_sha256:'c'.repeat(64)})});await Vue.nextTick()
  other.resolve(reply());await request;assert.equal(changed.view.value.context_sha256,'c'.repeat(64));assert.equal(changed.view.value.latest,null);changed.stop()
})
test('running checks wait for the saved record, and stale 4xx does not spin into a new request',async()=>{
  let calls=0;const page=harness({run:async()=>{calls++;throw {response:{status:400,data:{msg:'资料变化'}}}}})
  page.view.value.latest=record({status:'running'});await page.run();assert.equal(calls,0)
  page.view.value.latest=null;await page.run();assert.equal(calls,1);assert.equal(page.uncertain.value,false);assert.equal(page.error.value,'资料变化');page.stop()
})
test('website panel compiles and presents technical findings without publishing or adoption scores',()=>{
  assert.deepEqual(compileTemplate({source:descriptor.template.content,filename:'GeoWebsiteAudit.vue',id:'website-audit',compilerOptions:{bindingMetadata:script.bindings}}).errors,[])
  assert.deepEqual(compileStyle({source:descriptor.styles[0].content,id:'website-audit'}).errors,[])
  assert.ok(source.includes('不代表已索引或被 AI 采用'));assert.ok(!source.includes('v-html'))
  assert.ok(!source.includes('overall_success'));assert.ok(!source.includes('window.open'))
})
test('one prior-scope check remains inspectable after a facts or analysis revision changes the current scope',()=>{
  const page=harness();const old=record({current_scope:false})
  page.view.value=view({latest:null,history:[old]});page.historicalId.value=old.id
  assert.equal(page.latest.value.id,old.id);assert.equal(page.latest.value.current_scope,false)
  assert.ok(source.includes('view.history?.length > (view.latest ? 1 : 0)'))
  assert.equal(audit.auditNamePresence({body_text_present:false,body_literal_substring_present:true}),'文字出现，需核对上下文')
  page.stop()
})
