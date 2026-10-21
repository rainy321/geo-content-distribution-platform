import assert from 'node:assert/strict'
import fs from 'node:fs'
import test from 'node:test'
import { parse, compileScript } from '@vue/compiler-sfc'
import { computed, ref, watch, nextTick } from 'vue'
import { isDestinationTask } from '../src/utils/geoDestinationSession.js'

const filename=new URL('../src/views/GeoContentLoop.vue',import.meta.url)
const {descriptor,errors}=parse(fs.readFileSync(filename,'utf8'),{filename:filename.pathname})
assert.deepEqual(errors,[])
const script=compileScript(descriptor,{id:'content-login-scope-test'})
const source=descriptor.scriptSetup.content
const declaration=name=>script.scriptSetupAst.flatMap(node=>node.type==='VariableDeclaration'?node.declarations:[]).find(node=>node.id.name===name)
const expression=name=>{const node=declaration(name);assert.ok(node);return source.slice(node.init.start,node.init.end)}
const summarize=new Function(`return (${expression('scopedLoginMessage')})`)()
const login={id:'login-here',kind:'login',status:'completed',result:{requested_client_mode:'consumer',
  login_observation:{client_mode:'work',login_required:false,challenge:true},session_saved:true,message:'private legacy message'}}
const scope={taskId:'login-here',cycleId:'cycle-a'}

test('the content page shows its own unbound login observation safely',()=>{
  const summary=summarize(login,scope,'cycle-a')
  assert.match(summary,/所选 \/ 实际界面不一致/)
  assert.match(summary,/豆包对话.*豆包工作/)
  assert.match(summary,/尚未核验/)
  assert.match(summary,/仍需安全验证/)
  assert.doesNotMatch(summary,/private legacy|会话已保存|已登录/)
  const unknown=summarize({...login,result:{message:'已登录 private legacy message'}},scope,'cycle-a')
  assert.match(unknown,/登录状态未知/)
  assert.match(unknown,/验证状态未知/)
  assert.doesNotMatch(unknown,/private legacy|已登录/)
  assert.equal(summarize(login,scope,'cycle-a',true),'')
})

test('another cycle, another task and a new content operation cannot inherit login status',()=>{
  for(const [current,binding,cycle] of [[login,null,'cycle-a'],[login,scope,'cycle-b'],
    [{...login,id:'another-login'},scope,'cycle-a'],[{...login,kind:'draft'},scope,'cycle-a'],
    [login,{cycleId:'cycle-a'},'cycle-a']])assert.equal(summarize(current,binding,cycle),'')
  const task=ref(login),loginScope=ref(scope),cycleId=ref('cycle-a'),working=ref(false),nativeBusy=computed(()=>task.value.status==='running')
  const message=new Function('computed','scopedLoginMessage','task','loginScope','cycleId','isDestinationTask','nativeBusy','working',
    `return (${expression('currentTaskMessage')})`)(computed,summarize,task,loginScope,cycleId,isDestinationTask,nativeBusy,working)
  assert.match(message.value,/所选 \/ 实际/)
  working.value=true
  assert.doesNotMatch(message.value,/所选 \/ 实际/)
  working.value=false
  task.value={...login,status:'running'}
  assert.match(message.value,/关闭这个单独登录窗口/)
  task.value={id:'new-content',kind:'draft',status:'running',cycle_id:'cycle-a',message:'本轮新内容任务'}
  assert.equal(message.value,'本轮新内容任务')
  task.value={...login,id:'foreign-login',status:'running'}
  assert.match(message.value,/另一个桌面任务/)
})

test('the actual cycle watcher clears the old login binding before returning to that cycle',async()=>{
  const statement=script.scriptSetupAst.find(node=>node.type==='ExpressionStatement'&&
    node.expression?.callee?.name==='watch'&&node.expression.arguments[0]?.name==='cycleId')
  assert.ok(statement)
  const cycleId=ref('cycle-a'),loginScope=ref(scope)
  const install=new Function('watch','cycleId','loginScope',`let loginRequest=0;const stop=${source.slice(statement.start,statement.end).replace(/;$/,'')};return {stop,requests:()=>loginRequest}`)
  const observed=install(watch,cycleId,loginScope)
  cycleId.value='cycle-b';await nextTick()
  assert.equal(loginScope.value,null)
  assert.equal(observed.requests(),1)
  cycleId.value='cycle-a';await nextTick()
  assert.equal(loginScope.value,null)
  assert.equal(observed.requests(),2)
  observed.stop()
})
