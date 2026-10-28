import assert from 'node:assert/strict'
import test from 'node:test'
import {openSearchPortal,searchPortalUrl,searchPortalErrorMessage} from '../src/utils/geoSearchPortal.js'
import {searchProviders} from '../src/utils/geoSearchOnboarding.js'

const click=()=>({prevented:0,preventDefault(){this.prevented++}})
const receipt=(provider,purpose)=>({launch_requested:true,provider,purpose,url:searchPortalUrl(provider,purpose)})

test('web links retain ordinary href behavior without invoking a native or scripted window',()=>{
  const event=click()
  assert.equal(openSearchPortal(event,'baidu','entry',{location:{hash:'#/journey',search:''}}),null)
  assert.equal(event.prevented,0)
})

test('desktop clicks cancel target blank and send only the fixed provider and purpose',async()=>{
  for(const provider of Object.keys(searchProviders))for(const purpose of ['entry','help','submit']){
    const event=click(),calls=[]
    const host={pywebview:{api:{open_search_portal:async(...args)=>{calls.push(args);return receipt(provider,purpose)}}}}
    const pending=openSearchPortal(event,provider,purpose,host)
    assert.equal(event.prevented,1)
    assert.deepEqual(await pending,receipt(provider,purpose))
    assert.deepEqual(calls,[[provider,purpose]])
  }
})

test('desktop bridge not ready or old installed version gives a readable copy fallback',async()=>{
  for(const host of [{location:{hash:'#/journey?desktop=1'}},{location:{search:'?desktop=1'}},{pywebview:{api:{}}}]){
    const event=click()
    await assert.rejects(openSearchPortal(event,'baidu','entry',host),/复制地址/)
    assert.equal(event.prevented,1)
  }
})

test('OS refusal, bridge rejection and ambiguous receipts never claim a launch',async()=>{
  for(const result of [{launch_requested:false,reason:'browser_request_rejected'},undefined,{success:true},
      {...receipt('baidu','entry'),provider:'toutiao'},{...receipt('baidu','entry'),url:'https://example.org/'}]){
    await assert.rejects(openSearchPortal(click(),'baidu','entry',{pywebview:{api:{open_search_portal:async()=>result}}}),/复制地址/)
  }
  await assert.rejects(openSearchPortal(click(),'baidu','entry',{pywebview:{api:{open_search_portal:async()=>{throw new Error('bridge offline')}}}}),{code:'browser_request_unconfirmed'})
})

test('identifiers cannot supply arbitrary URLs or inherited object properties',async()=>{
  const calls=[],host={pywebview:{api:{open_search_portal:async(...args)=>calls.push(args)}}}
  for(const provider of ['https://example.org/','unknown','__proto__','constructor',null,[],['baidu'],{}]){
    assert.equal(searchPortalUrl(provider,'entry'),null)
    await assert.rejects(openSearchPortal(click(),provider,'entry',host),/复制地址/)
  }
  for(const purpose of ['file:///C:/Windows/System32/cmd.exe','unknown','entry --arg',null,[],{}]){
    assert.equal(searchPortalUrl('baidu',purpose),null)
    await assert.rejects(openSearchPortal(click(),'baidu',purpose,host),{code:'invalid_search_portal'})
  }
  assert.deepEqual(calls,[])
})

test('bridge rejection and malicious result errors expose only short fixed reasons without paths or tracebacks',async()=>{
  const detail='Traceback: C:/SyntheticCustomer/private/profile.json\nRuntimeError: sensitive local detail '+ 'x'.repeat(500)
  const methods=[async()=>{throw new Error(detail)},()=>{throw new Error(detail)},
    async()=>({launch_requested:false,error:detail}),async()=>({launch_requested:false,reason:detail,error:detail}),
    async()=>({launch_requested:false,reason:'browser_request_rejected',error:detail})]
  for(const method of methods){
    await assert.rejects(openSearchPortal(click(),'baidu','entry',{pywebview:{api:{open_search_portal:method}}}),cause=>{
      const message=searchPortalErrorMessage(cause)
      assert.equal(cause.message,message);assert.ok(message.length<150)
      assert.doesNotMatch(message,/SyntheticCustomer|Traceback|RuntimeError|sensitive/)
      assert.match(message,/复制地址/)
      return true
    })
  }
  assert.equal(searchPortalErrorMessage(new Error(detail)),searchPortalErrorMessage({code:'__proto__',message:detail}))
})

test('an unresponsive bridge remains unknown after timeout and late receipt, with no automatic reopening',async context=>{
  context.mock.timers.enable({apis:['setTimeout']})
  let calls=0,resolve
  const native=new Promise(done=>{resolve=done})
  const pending=openSearchPortal(click(),'baidu','entry',{pywebview:{api:{open_search_portal:()=>{calls++;return native}}}})
  const rejected=assert.rejects(pending,cause=>{
    assert.equal(cause.code,'browser_request_timeout');assert.match(cause.message,/打开状态未知/)
    assert.doesNotMatch(cause.message,/失败|已打开/)
    return true
  })
  await Promise.resolve()
  context.mock.timers.tick(8000)
  await rejected
  resolve(receipt('baidu','entry'));await Promise.resolve();await Promise.resolve()
  context.mock.timers.tick(60000)
  assert.equal(calls,1)
})
