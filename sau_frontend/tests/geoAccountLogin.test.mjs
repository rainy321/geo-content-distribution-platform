import assert from 'node:assert/strict'
import fs from 'node:fs'
import test from 'node:test'
import { computed, defineComponent, h, reactive, ref } from 'vue'
import { parse, compileScript, compileTemplate } from '@vue/compiler-sfc'

const filename=new URL('../src/views/AccountManagement.vue',import.meta.url)
const {descriptor,errors}=parse(fs.readFileSync(filename,'utf8'),{filename:filename.pathname})
assert.deepEqual(errors,[])
const script=compileScript(descriptor,{id:'account-login-test'})
const source=descriptor.scriptSetup.content.replace(/^import .*$/gm,'')
const deferred=()=>{let resolve,reject;const promise=new Promise((done,fail)=>{resolve=done;reject=fail});return {promise,resolve,reject}}

function harness({overview=async()=>({data:{platforms:[],planned_platforms:[],accounts:[],summary:{}}}),constructorError=false}={}) {
  const sources=[],unmount=[],messages=[],reads=[]
  class FakeEventSource {
    constructor(url,options){if(constructorError)throw Error('unavailable');this.url=url;this.options=options;this.closed=false;sources.push(this)}
    close(){this.closed=true}
    emit(data){this.onmessage?.({data})}
    fail(){this.onerror?.({})}
  }
  const dependencies={computed,defineComponent,h,reactive,ref,onMounted(){},onBeforeUnmount:callback=>unmount.push(callback),
    ElButton:{},ElIcon:{},ElMessage:{success:value=>messages.push(value),error:value=>messages.push(value)},
    accountApi:{getMediaAccounts:()=>{reads.push('read');return overview()},checkMediaAccount:async()=>({data:{}})},
    apiUrl:path=>`/backend${path}`,EventSource:FakeEventSource}
  const page=new Function(...Object.keys(dependencies),source+`;return {PlatformCard,openLogin,startLogin,retryLogin,closeLoginDialog,requestLoginDialogClose,resetLoginDialog,fetchOverview,checkAccount,overview,loading,loaded,loginStage,loginDialogVisible,loginForm,loginFailureReason,loginFailureCode,loginStageTitle,loginStageMessage,pendingLogin,qrCodeData};`)(...Object.values(dependencies))
  const open=(type=9,name='知乎测试账号',accountId=null)=>{page.openLogin({type,name:type===9?'知乎':'其他平台'},accountId?{id:accountId,account_name:name}:null);page.loginForm.accountName=name;page.startLogin();return sources.at(-1)}
  return {...page,sources,messages,reads,open,stop:()=>unmount.forEach(close=>close())}
}

test('actual account login distinguishes waiting for the service, browser opening and a loaded manual-login page',()=>{
  const page=harness(),stream=page.open()
  assert.equal(page.loginStage.value,'connecting');assert.match(page.loginStageMessage.value,/尚未确认浏览器已打开/)
  stream.emit('LOGIN_OPENING');assert.equal(page.loginStage.value,'opening');assert.match(page.loginStageMessage.value,/尚未确认平台登录页已打开/)
  stream.emit('MANUAL_LOGIN');assert.equal(page.loginStage.value,'manual');assert.match(page.loginStageMessage.value,/知乎登录页已打开/)
  assert.match(page.loginStageMessage.value,/保持窗口打开/);assert.match(page.loginStageMessage.value,/最多等待10分钟/)
  assert.match(page.loginStageMessage.value,/检测到登录后会自动保存/)
  assert.deepEqual(stream.options,{withCredentials:true});page.closeLoginDialog()
  const other=page.open(7,'其他平台测试');other.emit('MANUAL_LOGIN')
  assert.match(page.loginStageMessage.value,/平台登录页已打开/);assert.match(page.loginStageMessage.value,/保持窗口打开/)
  assert.doesNotMatch(page.loginStageMessage.value,/10分钟/);page.stop()
})

test('an unverified stored account is presented for review rather than conclusively expired',()=>{
  const page=harness(),platform={name:'知乎',type:9,priority:'P0',connected_count:0,accounts:[{
    id:27,account_name:'已保存但未验证',status:'expired',last_checked_at:null}]}
  const render=page.PlatformCard.setup({platform,checkingIds:new Set()},{emit(){}})
  const text=value=>typeof value==='string'?value:Array.isArray(value)?value.map(text).join(' '):value&&typeof value==='object'?text(value.children):''
  const visible=text(render())
  assert.match(visible,/需要确认/);assert.match(visible,/请检测登录，必要时重新登录/)
  assert.doesNotMatch(visible,/已失效/)
  assert.match(descriptor.template.content,/<p>需要处理<\/p>/)
  page.stop()
})

test('fixed backend failure codes give Chinese causes, including uppercase compatibility, without pretending a browser opened',()=>{
  const expected={browser_not_found:/未找到.*浏览器.*尚未打开/,browser_launch_failed:/浏览器未能启动/,
    browser_closed:/浏览器已关闭/,navigation_failed:/未能打开平台登录页/,login_timeout:/超时/,
    login_failed:/未完成/,component_missing:/缺少.*组件/,login_cancelled:/尚未确认会话保存/}
  for(const [code,copy] of Object.entries(expected))for(const wireCode of [code,code.toUpperCase()]){
    const page=harness(),stream=page.open();const late=stream.onmessage
    stream.emit('LOGIN_OPENING');stream.emit(`LOGIN_ERROR:${wireCode}`)
    assert.equal(page.loginStage.value,'failed');assert.equal(page.loginFailureCode.value,code)
    assert.match(page.loginStageMessage.value,copy);assert.equal(page.qrCodeData.value,'');assert.equal(stream.closed,true)
    late({data:'500'});assert.match(page.loginStageMessage.value,copy);page.stop()
  }
})

test('component hints and unknown long errors never become a QR code or expose raw diagnostics',()=>{
  const page=harness(),stream=page.open();stream.emit(`LOGIN_ERROR:${'private diagnostic '.repeat(30)}`)
  assert.equal(page.loginFailureCode.value,'login_failed');assert.equal(page.qrCodeData.value,'')
  assert.doesNotMatch(page.loginStageMessage.value,/private diagnostic/)
  page.retryLogin();const retried=page.sources.at(-1);retried.emit('COMPONENT_MISSING:playwright')
  assert.equal(page.loginStage.value,'failed');assert.match(page.loginStageMessage.value,/playwright/)
  page.retryLogin();page.sources.at(-1).emit('COMPONENT_MISSING:C:/private/account.json')
  assert.doesNotMatch(page.loginStageMessage.value,/private|account\.json/);page.stop()
})

test('only bounded image data or bare base64 is shown as QR content',()=>{
  const page=harness(),stream=page.open()
  stream.emit('network failed: '.repeat(50));assert.equal(page.qrCodeData.value,'')
  stream.emit('data:image/svg+xml;base64,'+'A'.repeat(120));assert.equal(page.qrCodeData.value,'')
  stream.emit('data:image/png;base64,'+'A'.repeat(120));assert.match(page.qrCodeData.value,/^data:image\/png;base64,/)
  page.closeLoginDialog();const next=page.open();next.emit('B'.repeat(120));assert.equal(page.qrCodeData.value,'data:image/png;base64,'+'B'.repeat(120))
  page.stop()
})

test('retry starts directly once, while rapid starts and repeated retry clicks cannot create parallel streams',()=>{
  const page=harness(),first=page.open();page.startLogin();assert.equal(page.sources.length,1)
  first.emit('LOGIN_ERROR:browser_not_found');page.retryLogin()
  assert.equal(page.sources.length,2);assert.equal(page.loginStage.value,'connecting')
  page.retryLogin();page.startLogin();assert.equal(page.sources.length,2);page.stop()
})

test('old failures and successes cannot change a reopened platform or close its captured current stream',()=>{
  const page=harness(),first=page.open(),lateMessage=first.onmessage,lateError=first.onerror
  page.openLogin({type:7,name:'今日头条'});assert.equal(first.closed,true)
  page.loginForm.accountName='头条测试';page.startLogin();const current=page.sources.at(-1)
  lateMessage({data:'500'});lateError({});lateMessage({data:'200'})
  assert.equal(page.loginStage.value,'connecting');assert.equal(page.loginForm.platformType,7)
  assert.equal(current.closed,false);assert.equal(page.reads.length,0);page.stop()
})

test('closing waiting invalidates its callbacks immediately; animation completion is never wired to reset a new dialog',()=>{
  const page=harness(),first=page.open(),late=first.onmessage;let hidden=0
  page.requestLoginDialogClose(()=>hidden++)
  assert.equal(hidden,1);assert.equal(first.closed,true);assert.equal(page.loginDialogVisible.value,false)
  page.openLogin({type:9,name:'知乎'});page.loginForm.accountName='新会话'
  late({data:'200'});assert.equal(page.loginDialogVisible.value,true);assert.equal(page.loginForm.accountName,'新会话')
  assert.equal(page.loginStage.value,'idle');assert.equal(page.reads.length,0)
  assert.match(descriptor.template.content,/:before-close="requestLoginDialogClose"/)
  assert.doesNotMatch(descriptor.template.content,/@closed=|@update:model-value=|v-model="loginDialogVisible"/)
  assert.match(descriptor.template.content,/关闭面板会结束本次等待。原浏览器可能仍在运行/);page.stop()
})

test('stream failure closes automatic reconnection and reports uncertain completion instead of inventing a platform cause',()=>{
  const page=harness(),stream=page.open();stream.emit('MANUAL_LOGIN');stream.fail()
  assert.equal(page.loginStage.value,'failed');assert.equal(stream.closed,true)
  assert.equal(page.sources.length,1);assert.match(page.loginStageMessage.value,/登录结果尚未确认.*原浏览器可能仍在运行/)
  assert.equal(page.loginFailureCode.value,'');page.stop()
})

test('a successful terminal event refreshes accounts once and ignores later stream errors',async()=>{
  const page=harness(),stream=page.open(),lateError=stream.onerror,lateMessage=stream.onmessage
  stream.emit('data:image/png;base64,'+'A'.repeat(120));assert.notEqual(page.qrCodeData.value,'')
  stream.emit('200');lateError({});lateMessage({data:'500'});await Promise.resolve()
  assert.equal(page.loginStage.value,'success');assert.equal(stream.closed,true);assert.equal(page.reads.length,1)
  assert.equal(page.qrCodeData.value,'')
  page.stop()
})

test('unmount closes the stream and late callbacks cannot refresh or write login state',()=>{
  const page=harness(),stream=page.open(),late=stream.onmessage,error=stream.onerror
  page.stop();late({data:'200'});error({})
  assert.equal(stream.closed,true);assert.equal(page.reads.length,0);assert.equal(page.loginStage.value,'connecting')
})

test('account overview requests use the newest response and ignore pending results after unmount',async()=>{
  const responses=[],page=harness({overview:()=>{const value=deferred();responses.push(value);return value.promise}})
  const first=page.fetchOverview(),second=page.fetchOverview()
  responses[0].resolve({data:{accounts:[{id:'old'}]}});await first;assert.equal(page.loading.value,true)
  responses[1].resolve({data:{accounts:[{id:'new'}]}});await second;assert.equal(page.overview.accounts[0].id,'new')
  const closing=page.fetchOverview();page.stop();responses[2].resolve({data:{accounts:[{id:'after-unmount'}]}});await closing
  assert.equal(page.overview.accounts[0].id,'new')
})

test('constructor failure stays retryable, and empty input never makes a login request',()=>{
  const page=harness({constructorError:true});page.open();assert.equal(page.loginStage.value,'failed')
  assert.match(page.loginStageMessage.value,/无法连接本机登录服务/);page.stop()
  const invalid=harness();invalid.openLogin({type:9,name:'知乎'});invalid.startLogin()
  assert.equal(invalid.sources.length,0);assert.equal(invalid.loginStage.value,'idle');assert.equal(invalid.messages.length,1);invalid.stop()
})

test('existing-account reconnect preserves the bound account ID and encodes the visible remark',()=>{
  const page=harness(),stream=page.open(9,'研发 & 主账号',27),url=new URL(stream.url,'http://localhost')
  assert.equal(url.searchParams.get('type'),'9');assert.equal(url.searchParams.get('account_id'),'27')
  assert.equal(url.searchParams.get('id'),'研发 & 主账号');page.stop()
})

test('the account page template compiles with explicit waiting and retry controls',()=>{
  assert.deepEqual(compileTemplate({source:descriptor.template.content,filename:filename.pathname,id:'account-login-test',compilerOptions:{bindingMetadata:script.bindings}}).errors,[])
  assert.match(descriptor.template.content,/@click="retryLogin"/)
  assert.doesNotMatch(descriptor.template.content,/取消登录/)
})
