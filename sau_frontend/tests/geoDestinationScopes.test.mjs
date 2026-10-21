import assert from 'node:assert/strict'
import fs from 'node:fs'
import test from 'node:test'
import { computed, effectScope, reactive, ref, watch } from 'vue'
import { parse, compileScript } from '@vue/compiler-sfc'
import * as availability from '../src/utils/geoDestinationAvailability.js'
import * as use from '../src/utils/geoDestinationUse.js'
import { sameBrand, articleSnapshot } from '../src/utils/geoJourney.js'
const deferred=()=>{let resolve;const promise=new Promise(done=>resolve=done);return {promise,resolve}}
const target=id=>({id,key:`target_${id}`,revision:1,state:'need_action',blocked_reason:'原尝试待核对',config:{name:id,enabled:true},publication_gate:{allowed:true,code:'allowed'}})
const cycle=(id='a')=>({id,destinations:[target('t1'),target('t2')]})
const source=path=>{
  const filename=new URL(path,import.meta.url),{descriptor}=parse(fs.readFileSync(filename,'utf8'))
  const script=compileScript(descriptor,{id:path})
  return {body:descriptor.scriptSetup.content.replace(/^import .*$/gm,''),expression:name=>{
    const declaration=script.scriptSetupAst.flatMap(node=>node.type==='VariableDeclaration'?node.declarations:[]).find(node=>node.id.name===name)
    return descriptor.scriptSetup.content.slice(declaration.init.start,declaration.init.end)
  }}
}
const destinations=source('../src/components/GeoDestinations.vue'),journey=source('../src/views/GeoJourney.vue')
const destinationHarness=api=>{
  const props=reactive({cycle:cycle(),busy:false}),events=[],scope=effectScope()
  const deps={reactive,ref,watch,defineProps:()=>props,defineEmits:()=>((...args)=>events.push(args)),geoContentApi:api,
    ElMessage:{error(){},success(){}},ElMessageBox:{confirm:async()=>{}},...availability,...use}
  const actions=scope.run(()=>new Function(...Object.keys(deps),destinations.body+';return {events,review,eventRows,showEvents};')(...Object.values(deps)))
  return {props,emitted:events,stop:()=>scope.stop(),...actions,deps}
}

test('only the latest selected platform can populate the attempt history',async()=>{
  const first=deferred(),second=deferred(),page=destinationHarness({destination:(_cycle,id)=>id==='t1'?first.promise:second.promise})
  try {
    const old=page.events(page.props.cycle.destinations[0]),recent=page.events(page.props.cycle.destinations[1])
    second.resolve({data:{target:{id:'t2',events:[{id:'recent'}]}}});await recent
    first.resolve({data:{target:{id:'t1',events:[{id:'old'}]}}});await old
    assert.deepEqual(page.eventRows.value,[{id:'recent'}]);assert.equal(page.showEvents.value,true)
  }finally{page.stop()}
})

test('a history response cannot reopen a dialog after cycle A to B to A',async()=>{
  const response=deferred(),page=destinationHarness({destination:()=>response.promise})
  try {
    const pending=page.events(page.props.cycle.destinations[0])
    page.props.cycle=cycle('b');page.props.cycle=cycle('a')
    response.resolve({data:{target:{id:'t1',events:[{id:'old'}]}}});await pending
    assert.equal(page.showEvents.value,false);assert.deepEqual(page.eventRows.value,[])
  }finally{page.stop()}
})

test('a restore confirmation cannot transfer to a different cycle sharing the same target',async()=>{
  const confirm=deferred(),calls=[],page=destinationHarness({reviewDestination:async(...args)=>calls.push(args)})
  page.deps.ElMessageBox.confirm=()=>confirm.promise
  try {
    const pending=page.review(page.props.cycle.destinations[0]);page.props.cycle=cycle('b')
    confirm.resolve();await pending
    assert.deepEqual(calls,[]);assert.deepEqual(page.emitted,[])
  }finally{page.stop()}
})

test('busy state and a changed block reason require a fresh restore confirmation',async()=>{
  for(const change of [page=>page.props.busy=true,page=>page.props.cycle.destinations[0].blocked_reason='新尝试待核对']){
    const confirm=deferred(),calls=[],page=destinationHarness({reviewDestination:async(...args)=>calls.push(args)})
    page.deps.ElMessageBox.confirm=()=>confirm.promise
    try{const pending=page.review(page.props.cycle.destinations[0]);change(page);confirm.resolve();await pending;assert.deepEqual(calls,[])}finally{page.stop()}
  }
})

test('a late restore mutation response does not request or emit another cycle',async()=>{
  const response=deferred(),calls=[],page=destinationHarness({reviewDestination:()=>response.promise,get:async id=>{calls.push(id);return {data:cycle(id)}}})
  try {
    const pending=page.review(page.props.cycle.destinations[0]);await Promise.resolve()
    page.props.cycle=cycle('b');response.resolve({});await pending
    assert.deepEqual(calls,[]);assert.deepEqual(page.emitted,[])
  }finally{page.stop()}
})

const draft=(id=5,brief='b01',title='原稿',content='原正文')=>({article_id:id,brief_id:brief,platform:'target_t1',title,content,summary:'',tags:[]})
const contentCycle=(drafts=[draft()],briefs=[{id:'b01'},{id:'b02'}])=>({id:'cycle-a',project_id:1,profile:{name:'示例软件',website:'https://software.example.org/',entity_type:'product'},analysis:{briefs},channels:{target_t1:'合成平台'},facts:[{id:'f1',text:'事实',source_url:'',confirmed:true}],drafts})
const editorHarness=(edited=true)=>{
  const value=contentCycle(),cycle=ref(value),articleId=ref(5),briefId=ref('b01'),platform=ref('target_t1')
  const matchingDrafts=computed(()=>cycle.value.drafts.filter(item=>item.brief_id===briefId.value && item.platform===platform.value))
  const activeDraft=computed(()=>matchingDrafts.value.find(item=>item.article_id===articleId.value)||null)
  const editor=reactive({title:edited?'本机未保存稿':value.drafts[0].title,content:edited?'本机修改正文':value.drafts[0].content,summary:'',tags:[]})
  const editorDirty=computed(()=>activeDraft.value && (editor.title!==activeDraft.value.title || editor.content!==activeDraft.value.content))
  const deps={cycle,articleId,briefId,platform,matchingDrafts,activeDraft,editor,editorDirty,articleSnapshot,sameBrand,
    projectId:ref(1),project:ref(value.profile),cycleId:ref(value.id),recoveryDraft:ref(null),error:ref(''),factRows:ref([]),reviewConfirmed:ref(true)}
  const action=new Function(...Object.keys(deps),`const loadEditor=${journey.expression('loadEditor')};return (${journey.expression('applyCycle')});`)(...Object.values(deps))
  return {...deps,action}
}

test('a removed active article opens the server replacement and keeps local edits in a recovery copy',()=>{
  const page=editorHarness();page.action(contentCycle([draft(6,'b01','另一篇服务端稿','另一正文')]),{keepEditor:true})
  assert.equal(page.articleId.value,6);assert.equal(page.editor.title,'另一篇服务端稿')
  assert.equal(page.recoveryDraft.value.article_id,5);assert.equal(page.recoveryDraft.value.editor.title,'本机未保存稿')
  assert.match(page.error.value,/服务端文章或选题已变化/)
})

test('a replaced brief cannot bind the old edited text to a different article',()=>{
  const page=editorHarness();page.action(contentCycle([draft(6,'b02','新选题文章','新正文')],[{id:'b02'}]),{keepEditor:true})
  assert.equal(page.briefId.value,'b02');assert.equal(page.articleId.value,6);assert.equal(page.editor.title,'新选题文章')
  assert.equal(page.recoveryDraft.value.editor.content,'本机修改正文')
})

test('changed server text preserves local changes instead of silently overwriting either version',()=>{
  const page=editorHarness();page.action(contentCycle([draft(5,'b01','服务端新版本','新正文')]),{keepEditor:true})
  assert.equal(page.editor.title,'服务端新版本');assert.equal(page.recoveryDraft.value.editor.title,'本机未保存稿')
  assert.equal(page.recoveryDraft.value.baseline.title,'原稿')
})

test('the unchanged active article keeps local edits while a clean editor refreshes to new server text',()=>{
  const edited=editorHarness();edited.action(contentCycle(),{keepEditor:true})
  assert.equal(edited.editor.title,'本机未保存稿');assert.equal(edited.recoveryDraft.value,null)
  const clean=editorHarness(false);clean.action(contentCycle([draft(5,'b01','服务端新版本','新正文')]),{keepEditor:true})
  assert.equal(clean.editor.title,'服务端新版本');assert.equal(clean.recoveryDraft.value,null)
})
