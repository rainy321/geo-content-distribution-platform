import assert from 'node:assert/strict'
import fs from 'node:fs'
import test from 'node:test'
import { computed, effectScope, reactive, ref, watch } from 'vue'
import { parse, compileScript, compileTemplate, compileStyle } from '@vue/compiler-sfc'
import * as uses from '../src/utils/geoDestinationUse.js'
import { destinationPublicationGate, destinationCanPrepare, writingChannelEnabled } from '../src/utils/geoDestinationAvailability.js'

const approved=()=>({id:'t1',key:'target_t1',revision:2,state:'configured',config:{name:'合成发布平台',style:'具体技术内容',title_limit:80,publication_use:'editorial',rules_url:'https://publisher.example.org/rules',rule_review:{state:'suitable',operator_confirmed:true,note:'保留实际依据'}},publication_gate:{allowed:true,code:'allowed'}})
const cycle=(id='cycle-a')=>({id,project_id:4,channels:{target_t1:'合成平台'},destinations:[]})
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b});return {promise,resolve,reject}}
const component=path=>{
  const filename=new URL(path,import.meta.url),{descriptor,errors}=parse(fs.readFileSync(filename,'utf8'),{filename:filename.pathname})
  assert.deepEqual(errors,[]);compileScript(descriptor,{id:path})
  return {descriptor,filename,body:descriptor.scriptSetup.content.replace(/^import .*$/gm,'')}
}
const fields=component('../src/components/DestinationUseFields.vue')
const review=component('../src/components/DestinationUseReview.vue')
const fieldsHarness=(policy=null)=>{
  const props=reactive({modelValue:approved().config,policy,disabled:false}),events=[]
  const actions=new Function('defineProps','defineEmits','computed',...Object.keys(uses),fields.body+';return {changeUse,changeRules,confirm,changeNote,restriction};')(
    ()=>props,()=>((name,value)=>{events.push({name,value});props.modelValue=value}),computed,...Object.values(uses))
  return {props,events,...actions}
}
const reviewHarness=(api)=>{
  const props=reactive({cycle:cycle(),target:approved(),busy:false}),events=[],scope=effectScope()
  const actions=scope.run(()=>new Function('defineProps','defineEmits','computed','ref','watch','geoContentApi',...Object.keys(uses),review.body+';return {open,save,form,visible,saving,snapshot,scopeChanged,saveError};')(
    ()=>props,()=>((...args)=>events.push(args)),computed,ref,watch,{saveDestination:api},...Object.values(uses)))
  return {props,events,stop:()=>scope.stop(),...actions}
}

test('legacy or malformed client configuration never substitutes for a server use decision',()=>{
  for(const gate of [undefined,{allowed:true},{allowed:'true',code:'allowed'},{allowed:true,code:'research_only'}, {allowed:false,code:'allowed'}]){
    const target={...approved(),publication_gate:gate}
    assert.equal(destinationCanPrepare(target),false)
  }
  const target=approved();assert.equal(destinationCanPrepare(target),true)
  target.config.enabled=false
  assert.equal(destinationPublicationGate(target).code,'inactive')
  assert.equal(destinationCanPrepare(target),false)
})

test('a research target remains visible but is not a writable channel before use review',()=>{
  const target={...approved(),publication_gate:{allowed:false,code:'research_only',reason:'先保留为来源参考'}},value={...cycle(),destinations:[target]}
  assert.equal(writingChannelEnabled(value,target.key),false)
  assert.equal(destinationPublicationGate(target).reason,'先保留为来源参考')
  assert.deepEqual(uses.destinationUseValues({}),{publication_use:'research_only',rules_url:'',rule_review:{state:'unknown',operator_confirmed:false,note:''}})
})

test('changing use or rules clears an old confirmation without mutating saved configuration',()=>{
  const editor=fieldsHarness(),saved=editor.props.modelValue
  editor.changeUse('commercial')
  assert.equal(saved.rule_review.operator_confirmed,true)
  assert.equal(editor.props.modelValue.rule_review.operator_confirmed,false)
  assert.equal(editor.props.modelValue.rule_review.state,'unknown')
  editor.confirm(true);assert.equal(editor.props.modelValue.rule_review.state,'suitable')
  editor.changeRules('https://publisher.example.org/new-rules')
  assert.equal(editor.props.modelValue.rule_review.operator_confirmed,false)
  assert.equal(editor.props.modelValue.rule_review.note,'保留实际依据')
})

test('known restricted uses cannot be confirmed and research needs no account operation',()=>{
  for(const policy of [{commercial_restricted:true,note:'商业推广不适合此栏目'},{automation_restricted:true,note:'仅保留来源研究'}]){
    const editor=fieldsHarness(policy)
    editor.changeUse('commercial');const count=editor.events.length
    editor.confirm(true)
    assert.equal(editor.events.length,count)
    assert.equal(editor.props.modelValue.rule_review.operator_confirmed,false)
    editor.changeUse('research_only')
    assert.equal(editor.restriction.value,'')
    assert.equal(editor.props.modelValue.rule_review.state,'unknown')
  }
})

test('disabled form fields do not emit changes or confirmations',()=>{
  const editor=fieldsHarness();editor.props.disabled=true
  editor.changeUse('commercial');editor.changeRules('https://other.example.org/');editor.confirm(false);editor.changeNote('覆盖')
  assert.deepEqual(editor.events,[])
})

test('the use update preserves all unrelated destination settings and revision',()=>{
  const target=approved(),values=uses.destinationUseValues(target.config)
  values.publication_use='research_only';values.rule_review.note='只用于研究'
  const request=uses.destinationUseRequest(target,values)
  assert.equal(request.revision,2);assert.equal(request.config.title_limit,80);assert.equal(request.config.style,target.config.style)
  assert.equal(target.config.publication_use,'editorial')
  assert.notEqual(request.config.rule_review,target.config.rule_review)
})

test('ambiguous or active-content observed URLs never become clickable research links',()=>{
  for(const url of ['javascript:alert(1)','https://evil.example\\@cloud.tencent.com/developer/article/1','https://u:p@publisher.example.org/a','https://publisher.example.org:444/a','https://publisher.example.org/a/../rules','https://publisher.example.org/a/%2e%2e/rules','https://127.0.0.1/a','https://server.local/a'])assert.equal(uses.safeResearchLink(url),'',url)
  assert.equal(uses.safeResearchLink('https://publisher.example.org/article/1?q=x#part'),'https://publisher.example.org/article/1?q=x#part')
})

test('use review saves once and emits the returned whole cycle without starting a browser',async()=>{
  const calls=[],editor=reviewHarness(async(...args)=>{calls.push(args);return {data:{target:{id:'t1'},cycle:{...cycle(),revision:8}}}})
  try{editor.open();await editor.save();assert.equal(calls.length,1);assert.equal(calls[0][0],'cycle-a');assert.equal(calls[0][1],'t1');assert.equal(editor.events[0][1].revision,8);assert.equal(editor.visible.value,false)}finally{editor.stop()}
})

test('a late save for project A cannot update A after switching through B and back',async()=>{
  const response=deferred(),editor=reviewHarness(()=>response.promise)
  try{
    editor.open();const pending=editor.save()
    editor.props.cycle=cycle('cycle-b');editor.props.cycle=cycle('cycle-a')
    response.resolve({data:{target:{id:'t1'},cycle:{...cycle(),revision:8}}});await pending
    assert.deepEqual(editor.events,[]);assert.equal(editor.visible.value,false)
  }finally{editor.stop()}
})

test('a changed destination revision blocks an old use save before it reaches the API',async()=>{
  const calls=[],editor=reviewHarness(async()=>calls.push('called'))
  try{editor.open();editor.props.target.revision++;await editor.save();assert.equal(editor.scopeChanged.value,true);assert.deepEqual(calls,[])}finally{editor.stop()}
})

test('a failed use save remains reviewable and never retries itself',async()=>{
  let count=0;const editor=reviewHarness(async()=>{count++;throw new Error('连接中断，请刷新核对')})
  try{editor.open();await editor.save();assert.equal(count,1);assert.equal(editor.visible.value,true);assert.match(editor.saveError.value,/刷新核对/);assert.deepEqual(editor.events,[])}finally{editor.stop()}
})

test('both use components compile with reduced motion and bounded dialogs',()=>{
  for(const entry of [fields,review]){
    assert.deepEqual(compileTemplate({source:entry.descriptor.template.content,filename:entry.filename.pathname,id:'use-review'}).errors,[])
    for(const style of entry.descriptor.styles)assert.deepEqual(compileStyle({source:style.content,filename:entry.filename.pathname,id:'use-review'}).errors,[])
  }
  assert.match(fields.descriptor.styles[0].content,/prefers-reduced-motion/)
  assert.match(review.descriptor.template.content,/min\(600px,92vw\)/)
})
