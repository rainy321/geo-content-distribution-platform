import assert from 'node:assert/strict'
import fs from 'node:fs'
import test from 'node:test'
import { reactive, ref, computed } from 'vue'
import { parse, compileScript, compileTemplate, compileStyle } from '@vue/compiler-sfc'
import { destinationEnabled, writingChannelEnabled, inactiveDestinationMessage } from '../src/utils/geoDestinationAvailability.js'

const target = (state='configured', enabled=true) => ({id:'t1',key:'target_t1',state,config:{name:'合成社区',enabled},capability:'configured_fill'})
const cycle = value => ({id:'cycle-a',channels:{owned_web:'官网',zhihu:'知乎',target_t1:'合成社区'},destinations:[value],facts:[{text:'已核对事实',confirmed:true}],analysis:{briefs:[]}})
const source = path => {
  const filename=new URL(path,import.meta.url),{descriptor,errors}=parse(fs.readFileSync(filename,'utf8'),{filename:filename.pathname})
  assert.deepEqual(errors,[])
  const script=compileScript(descriptor,{id:path}),content=descriptor.scriptSetup.content
  return {filename,descriptor,expression:name=>{
    const declaration=script.scriptSetupAst.flatMap(node=>node.type==='VariableDeclaration'?node.declarations:[]).find(node=>node.id.name===name)
    assert.ok(declaration,name)
    return content.slice(declaration.init.start,declaration.init.end)
  }}
}
const destinations=source('../src/components/GeoDestinations.vue')
const content=source('../src/views/GeoContentLoop.vue')
const journey=source('../src/views/GeoJourney.vue')

test('availability handles legacy enabled defaults and rejects every stopped state or invalid enabled flag',()=>{
  assert.equal(destinationEnabled(target()),true)
  assert.equal(destinationEnabled({config:{}}),true)
  for(const value of [undefined,null,{state:'disabled',config:{}},{state:'archived',config:{}},{state:'deleted',config:{}},target('configured',false),target('editor_ready',null),target('configured','true')])assert.equal(destinationEnabled(value),false)
})

test('writing choices resolve the actual target and never authorize a missing target through a generic label',()=>{
  const value=cycle(target('configured',false))
  assert.equal(writingChannelEnabled(value,'target_t1'),false)
  assert.equal(writingChannelEnabled(value,'owned_web'),true)
  assert.equal(writingChannelEnabled(value,'zhihu'),true)
  assert.equal(writingChannelEnabled(value,'unknown'),false)
  value.destinations=[]
  assert.equal(writingChannelEnabled(value,'target_t1'),false)
  value.destinations=[target()]
  assert.equal(writingChannelEnabled(value,'target_t1'),true)
})

const destinationHarness = (value,confirm=async()=>{}) => {
  const props=reactive({cycle:cycle(value),busy:false}),emitted=[],form=reactive({}),editing=ref(null),show=ref(false),calls=[]
  const functions=new Function('props','emit','form','editing','show','destinationEnabled','writingChannelEnabled','ElMessageBox','geoContentApi','ElMessage',`
    const defaults=${destinations.expression('defaults')};
    return {configure:(${destinations.expression('configure')}),select:(${destinations.expression('selectTarget')}),open:(${destinations.expression('openTarget')}),review:(${destinations.expression('review')})}
  `)(props,(...args)=>emitted.push(args),form,editing,show,destinationEnabled,writingChannelEnabled,{confirm},
    {reviewDestination:async(...args)=>calls.push(args),get:async()=>({data:props.cycle})},{error(){}})
  return {...functions,props,emitted,form,editing,show,calls}
}

test('real platform actions cannot select, open or restore a disabled target while its configuration remains editable',async()=>{
  for(const value of [target('configured',false),target('disabled'),target('archived'),target('deleted')]){
    const page=destinationHarness(value)
    page.select(value.key);page.open(value);await page.review(value)
    assert.deepEqual(page.emitted,[]);assert.deepEqual(page.calls,[])
    page.configure(value.config,value)
    assert.equal(page.show.value,true)
    assert.equal(page.form.enabled,false)
    assert.equal(page.editing.value.id,value.id)
    page.form.enabled=true
    assert.equal(destinationEnabled(page.props.cycle.destinations[0]),false,'editing the toggle alone does not change the saved record')
  }
})

test('restore rechecks the current configuration after the confirmation dialog',async()=>{
  let finish
  const page=destinationHarness(target(),()=>new Promise(resolve=>finish=resolve))
  const pending=page.review(page.props.cycle.destinations[0])
  page.props.cycle.destinations[0].config.enabled=false
  finish();await pending
  assert.deepEqual(page.calls,[])
  assert.deepEqual(page.emitted,[])
})

test('busy platform actions cannot open a login or select a writing target',()=>{
  const value=target(),page=destinationHarness(value)
  page.props.busy=true;page.select(value.key);page.open(value)
  assert.deepEqual(page.emitted,[])
  page.props.busy=false;page.select(value.key);page.open(value)
  assert.deepEqual(page.emitted,[['select',value.key],['open',{target_id:value.id}]])
})

test('the content page blocks stale buttons before native calls or a fill confirmation',async()=>{
  const calls=[],value=target('configured',false)
  const actions=new Function('cycle','destinationEnabled','inactiveDestinationMessage','ElMessage','draftDestination','cycleScope',`
    return {open:(${content.expression('openDestination')}),fill:(${content.expression('fillDestination')})}
  `)(ref(cycle(value)),destinationEnabled,inactiveDestinationMessage,{info:message=>calls.push(message)},()=>value,()=>({id:'cycle-a',epoch:0}))
  await actions.open({target_id:value.id})
  await actions.fill({article_id:1,provenance:{destination:{id:value.id}}})
  assert.deepEqual(calls,[inactiveDestinationMessage,inactiveDestinationMessage])
})

test('the guided journey blocks writing, login and publishing for a preserved stopped draft',async()=>{
  const value=target('configured',false),selectedTarget=ref(value),calls=[]
  const actions=new Function('cycle','platform','selectedTarget','writingChannelEnabled','destinationEnabled','inactiveDestinationMessage','run','geoContentApi','applyCycle','cycleId',`
    return {generate:(${journey.expression('generateResearch')}),login:(${journey.expression('openDestinationLogin')}),publish:(${journey.expression('openPublisher')})}
  `)(ref(cycle(value)),ref(value.key),selectedTarget,writingChannelEnabled,destinationEnabled,inactiveDestinationMessage,action=>action(),
    {get:async()=>{calls.push('read current cycle');return {data:cycle(value)}}},()=>{},ref('cycle-a'))
  await assert.rejects(actions.generate('draft'),/已停用/)
  assert.throws(actions.login,/已停用/)
  await assert.rejects(actions.publish(),/已停用/)
  assert.deepEqual(calls,['read current cycle'])
})

test('stopped selections disable draft generation without removing prior content',()=>{
  const value=cycle(target('configured',false)),current=ref(value)
  const canDraft=new Function('computed','cycle','confirmed','factsStoredText','factsValue','briefId','writingChannelEnabled','platform',`return (${content.expression('canDraft')})`)(
    computed,current,ref(true),values=>values[0].text,()=>value.facts,ref('b01'),writingChannelEnabled,ref('target_t1'))
  assert.equal(canDraft.value,false)
  current.value.destinations[0].config.enabled=true
  assert.equal(canDraft.value,true)
})

test('all changed views compile and keep moderation, editing, export and history accessible',()=>{
  for(const entry of [destinations,content,journey]){
    assert.deepEqual(compileTemplate({source:entry.descriptor.template.content,filename:entry.filename.pathname,id:entry.filename.pathname}).errors,[])
    for(const style of entry.descriptor.styles)assert.deepEqual(compileStyle({source:style.content,filename:entry.filename.pathname,id:entry.filename.pathname}).errors,[])
  }
  assert.match(destinations.descriptor.template.content,/编辑接入配置/)
  assert.match(destinations.descriptor.template.content,/尝试记录/)
  assert.match(content.descriptor.template.content,/@click="downloadArticle\(draft\)"/)
  assert.match(content.descriptor.template.content,/@click="edit\(draft\)"/)
  assert.match(content.descriptor.template.content,/<GeoPublicationReview/)
  assert.match(journey.descriptor.template.content,/<GeoPublicationReview/)
})
