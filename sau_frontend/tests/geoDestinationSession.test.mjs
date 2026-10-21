import assert from 'node:assert/strict'
import test from 'node:test'
import { destinationContinuationText, destinationPreparation, destinationPreparationText, destinationSessionView, destinationSupportsFill, matchesDestinationTask, requestDestinationSessionCheck } from '../src/utils/geoDestinationSession.js'

const task = (session={}, values={}) => ({id:'live-task',kind:'destination_login',cycle_id:'cycle-a',target_id:'target-a',status:'running',progress:{destination_session:{state:'login_required',can_check:true,window_open:true,...session}},...values})
const view = value => destinationSessionView(value,'cycle-a','target-a')

test('checks only the matching live platform task, never a different project or a Doubao task',async()=>{
  let calls=0
  const api={check_destination_session:async()=>{calls++;return {}}}
  for(const values of [{cycle_id:'cycle-b'},{target_id:'target-b'},{kind:'login'},{kind:'destination'},{id:''}]) {
    const current=task({},values)
    assert.equal(matchesDestinationTask(current,'cycle-a','target-a'),false)
    assert.equal(view(current),null)
    await assert.rejects(requestDestinationSessionCheck(api,current,'cycle-a','target-a'),/不可复检/)
  }
  assert.equal(calls,0)
})

test('restart and terminal records never resume a stale can_check flag',async()=>{
  for(const status of ['needs_review','stopped','failed','completed','idle']) {
    const current=task({state:'editor_ready',editor_ready:true},{status})
    assert.equal(view(current).active,false)
    assert.equal(view(current).canCheck,false)
    await assert.rejects(requestDestinationSessionCheck({},current,'cycle-a','target-a'),/不可复检/)
  }
  assert.equal(view(task({state:'editor_ready',editor_ready:true},{status:'needs_review'})).state,'unknown')
})

test('a closed or checking window disables checks even when the old observation was actionable',()=>{
  for(const session of [{window_open:false},{window_open:'true'},{can_check:false},{can_check:'true'},{state:'opening'},{state:'checking'},{state:'window_closed'}]) {
    assert.equal(view(task(session)).canCheck,false,JSON.stringify(session))
  }
  const closed=view(task({state:'window_closed'},{status:'completed'}))
  assert.match(closed.detail,/不能证明已登录或已发布/)
})

test('legacy saved session and filled results remain unknown, without leaking account text',()=>{
  for(const state of ['session_saved','filled']) {
    const current=task({}, {status:'completed',progress:undefined,message:'private account data',result:{state,message:'private account data'}})
    const session=view(current)
    assert.equal(session.state,'unknown')
    assert.equal(session.canCheck,false)
    assert.doesNotMatch(JSON.stringify(session),/private account data/)
  }
})

test('login, verification, binding, editor and unknown states stay distinct without rendering raw messages',()=>{
  const titles=new Set()
  for(const state of ['login_required','challenge','binding_required','editor_ready','action_required','unrecognized']) {
    const session=view(task({state,editor_ready:true,binding_kind:'phone',message:'private account data',phone:'private phone'}))
    titles.add(session.title)
    assert.doesNotMatch(JSON.stringify(session),/private account data|private phone/)
    if(state==='binding_required')assert.match(session.detail,/手机号绑定/)
  }
  assert.equal(titles.size,6)
  assert.equal(view(task({state:'editor_ready',editor_ready:false})).state,'unknown')
  assert.doesNotMatch(view(task({state:'binding_required',binding_kind:'private account name'})).detail,/private account name/)
})

test('CSDN welcome example is described separately from an empty editor and never as published',()=>{
  const session=view(task({state:'editor_ready',editor_ready:true,body_state:'welcome_example'}))
  assert.match(session.title,/首次示例/)
  assert.match(session.detail,/已审稿填写任务/)
  assert.doesNotMatch(session.detail,/空白|已发布|登录成功/)
})

test('checking after a write only reads state, with no repeat-fill action',async()=>{
  const current=task({state:'action_required',content_write_attempted:true},{kind:'destination_fill'})
  assert.equal(view(current).checkLabel,'检查当前窗口')
  assert.equal(view(current).contentWriteAttempted,true)
  const calls=[]
  await requestDestinationSessionCheck({check_destination_session:async(...args)=>{calls.push(args);return {id:'live-task',status:'running',check_requested:true}}},current,'cycle-a','target-a')
  assert.deepEqual(calls,[['live-task']])
})

test('a check acknowledgement never updates the observed login or editor state',async()=>{
  const current=task({state:'challenge'})
  const before=structuredClone(current)
  const acknowledgement=await requestDestinationSessionCheck({check_destination_session:async()=>({id:'live-task',status:'running',check_requested:true})},current,'cycle-a','target-a')
  assert.equal(acknowledgement.check_requested,true)
  assert.deepEqual(current,before)
  assert.equal(view(current).state,'challenge')
})

test('missing optional bridge capability and stale acknowledgements do not claim success',async()=>{
  const current=task()
  await assert.rejects(requestDestinationSessionCheck({},current,'cycle-a','target-a'),/当前桌面版本.*升级/)
  for(const result of [{id:'another-task',status:'running',check_requested:true},{id:'live-task',status:'completed',check_requested:true},{id:'live-task',status:'running'},{error:'原任务已结束'}]) {
    await assert.rejects(requestDestinationSessionCheck({check_destination_session:async()=>result},current,'cycle-a','target-a'))
  }
  assert.match(destinationContinuationText({}),/不支持窗口复检/)
  assert.doesNotMatch(destinationContinuationText({}),/“检查并继续”/)
})

test('platform preparation names possible CSDN bindings only for verified host identity',()=>{
  for(const url of ['https://blog.csdn.net/','https://editor.csdn.net/md/','https://passport.csdn.net/login']) {
    const text=destinationPreparationText({config:{login_url:url}})
    assert.match(text,/微信或手机号绑定/)
    assert.match(text,/可能.*安全验证/)
    assert.match(text,/以当前账号提示为准/)
  }
  for(const url of ['https://blog.csdn.net.attacker.test/','https://attacker.test/?next=blog.csdn.net','https://blog.csdn.net@attacker.test/','http://blog.csdn.net/']) {
    assert.doesNotMatch(destinationPreparationText({config:{name:'CSDN',login_url:url}}),/CSDN 发文/)
  }
})

test('fixed platform assistance does not require customer CSS and login-only targets do not promise filling',()=>{
  assert.equal(destinationSupportsFill({capability:'platform_assisted_fill',config:{title_selector:'',body_selector:''}}),true)
  assert.equal(destinationSupportsFill({capability:'configured_fill'}),true)
  for(const target of [undefined,{capability:'login_export'},{config:{title_selector:'input'}}])assert.equal(destinationSupportsFill(target),false)
  const csdn={capability:'platform_assisted_fill',config:{editor_url:'https://editor.csdn.net/md/'}}
  assert.match(destinationPreparationText(csdn),/其他已有草稿会保留并暂停填写/)
  assert.match(destinationPreparation(csdn).capability,/最终提交由你/)
  assert.match(destinationPreparation({capability:'login_export'}).capability,/登录与导出/)
  assert.doesNotMatch(destinationPreparation({capability:'login_export'}).editor,/替换/)
})
