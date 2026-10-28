import assert from 'node:assert/strict'
import fs from 'node:fs'
import test from 'node:test'
import * as Vue from 'vue'
import {renderToString} from '@vue/server-renderer'
import {createHash} from 'node:crypto'
import {parse,compileScript,compileTemplate,compileStyle} from '@vue/compiler-sfc'
import * as search from '../src/utils/geoSearchOnboarding.js'
import {createFeedbackSession} from '../src/utils/geoFeedbackSession.js'
import {externalWriteUncertain} from '../src/utils/geoExternalFeedback.js'
import {journeyDate} from '../src/utils/geoJourney.js'
import * as portal from '../src/utils/geoSearchPortal.js'

const clone=value=>JSON.parse(JSON.stringify(value))
const cycle=(changes={})=>({id:'cycle-a',project_id:4,revision:2,test_plan:{plan_sha256:'a'.repeat(64)},
  profile:{name:'合成产品',entity_type:'product',website:'https://example.org/product',official_urls:[]},...changes})
const view=(owner=cycle(),changes={})=>({supported:true,reason:'',cycle_id:owner.id,project_id:owner.project_id,revision:owner.revision,
  plan_sha256:owner.test_plan.plan_sha256,site_origin:'https://example.org',sitemap_url:'https://example.org/sitemap.xml',
  identity_sha256:'b'.repeat(64),context_sha256:'c'.repeat(64),providers:Object.entries(search.searchProviders).map(([id,item])=>({id,name:item.name,
    entry_url:item.entry,submission_url:item.submit,help_url:item.help,availability_note:'登录后权限未知',record:null})),limitations:[],...changes})
const record=(body,digest,revision=body.expected_provider_revision+1)=>({provider:body.provider,site_origin:'https://example.org',identity_sha256:'b'.repeat(64),
  scope:{cycle_id:'cycle-a',project_id:4,plan_sha256:'a'.repeat(64),site_origin:'https://example.org',profile:cycle().profile},
  revision,progress:clone(body.progress),request_sha256:digest,evidence_source:'user_reported_platform_progress',software_verified:false,
  software_submitted:false,updated_at:'2026-10-28T08:00:00+00:00'})
const savedView=(body,digest,owner=cycle())=>{const result=view(owner);result.providers.find(item=>item.id===body.provider).record=record(body,digest);return result}
const response=async(body,owner=cycle())=>{const digest=await search.searchRequestSha(body);return {code:200,data:{overview:savedView(body,digest,owner),
  receipt:{provider:body.provider,revision:body.expected_provider_revision+1,identity_sha256:'b'.repeat(64),request_sha256:digest,idempotent:false}}}}
const deferred=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no});return {promise,resolve,reject}}
const flush=async()=>{await Promise.resolve();await Vue.nextTick();await Promise.resolve()}
const waitFor=async(predicate)=>{for(let index=0;index<40&&!predicate();index++)await new Promise(resolve=>setTimeout(resolve,2));assert.ok(predicate(),'asynchronous request did not start')}

test('default selections stay unknown and payload has no URL, token, index or GEO fields',()=>{
  const progress=search.emptySearchProgress(),body=search.searchPayload(view(),'baidu',progress,0)
  assert.deepEqual(progress,{site_added:null,ownership_confirmed:null,sitemap_status:'unknown',note:''})
  assert.deepEqual(Object.keys(body).sort(),['expected_context_sha256','expected_plan_sha256','expected_provider_revision','expected_revision','progress','project_id','provider'])
  assert.equal(body.expected_revision,2);assert.equal(body.expected_provider_revision,0)
  assert.throws(()=>search.searchPayload(view(),'arbitrary',progress,0))
  for(const changes of [{site_added:1},{ownership_confirmed:'true'},{site_added:false,ownership_confirmed:true},
    {sitemap_status:'indexed'},{note:'字'.repeat(501)},{native_verified:true}])assert.equal(search.validSearchProgress({...progress,...changes}),false)
})
test('exact whitespace and explicit false survive and canonical request digest matches Python JSON contract',async()=>{
  const body=search.searchPayload(view(),'toutiao',{site_added:false,ownership_confirmed:null,sitemap_status:'unknown',note:'  中文\r\n未核对  '},0)
  const expected='{"expected_context_sha256":"'+ 'c'.repeat(64)+'","expected_plan_sha256":"'+'a'.repeat(64)+'","expected_provider_revision":0,"expected_revision":2,"progress":{"note":"  中文\\r\\n未核对  ","ownership_confirmed":null,"site_added":false,"sitemap_status":"unknown"},"project_id":4,"provider":"toutiao"}'
  assert.equal(await search.searchRequestSha(body),createHash('sha256').update(expected).digest('hex'))
  assert.equal(body.progress.note,'  中文\r\n未核对  ')
})
test('fixed official links and exact cycle/project/plan bind every overview',()=>{
  assert.equal(search.searchOverview(cycle(),view()).supported,true)
  for(const change of [{cycle_id:'cycle-b'},{project_id:5},{plan_sha256:'d'.repeat(64)},{revision:1},{site_origin:'https://foreign.example.org'}])assert.throws(()=>search.searchOverview(cycle(),view(cycle(),change)))
  const spoof=view();spoof.providers[0].entry_url='javascript:alert(1)';assert.throws(()=>search.searchOverview(cycle(),spoof))
  const doubled=view();doubled.providers[1]=clone(doubled.providers[0]);assert.throws(()=>search.searchOverview(cycle(),doubled))
  assert.equal(search.searchScopeKey(cycle()),search.searchScopeKey(cycle({revision:3})))
  assert.notEqual(search.searchScopeKey(cycle()),search.searchScopeKey(cycle({profile:{...cycle().profile,website:'https://example.org/another-product'}})))
})

test('website and domain copy values come only from the validated origin and preserve the actual www choice',()=>{
  const validated=search.searchOverview(cycle(),view())
  assert.equal(search.searchSiteCopyText(validated,'website'),'https://example.org/')
  assert.equal(search.searchSiteCopyText(validated,'domain'),'example.org')
  const wwwOwner=cycle({profile:{...cycle().profile,website:'https://www.example.org/products/one?source=profile'}})
  const www=search.searchOverview(wwwOwner,view(wwwOwner,{site_origin:'https://www.example.org',sitemap_url:'https://www.example.org/sitemap.xml'}))
  assert.equal(search.searchSiteCopyText(www,'website'),'https://www.example.org/')
  assert.equal(search.searchSiteCopyText(www,'domain'),'www.example.org')
  for(const site_origin of ['https://example.org/path','https://example.org/','https://example.org?query=one',
      'https://example.org#fragment','https://user:secret@example.org','https://example.org:444','http://example.org','javascript:alert(1)',null]){
    assert.equal(search.searchSiteCopyText({...validated,site_origin},'website'),'')
    assert.equal(search.searchSiteCopyText({...validated,site_origin},'domain'),'')
  }
  for(const change of [{supported:false},{identity_sha256:null},{context_sha256:'unknown'}])assert.equal(search.searchSiteCopyText({...validated,...change},'website'),'')
  assert.equal(search.searchSiteCopyText(validated,'https://arbitrary.example/'),'')
})
test('only exact user-statement receipts can confirm a frozen request; newer provider revisions never roll back',async()=>{
  const body=search.searchPayload(view(),'baidu',search.emptySearchProgress(),0),digest=await search.searchRequestSha(body),reply=await response(body)
  assert.equal(search.searchReceipt(reply,view(),body,digest).providers[0].record.revision,1)
  for(const change of [{software_verified:true},{software_submitted:true},{evidence_source:'native'},
      {request_sha256:'d'.repeat(64)},{progress:{...body.progress,note:'different'}},{scope:{cycle_id:'other'}}]){
    const bad=clone(reply);Object.assign(bad.data.overview.providers[0].record,change);assert.throws(()=>search.searchReceipt(bad,view(),body,digest))
  }
  const current=reply.data.overview,earlier=view();assert.equal(search.searchCanApply(earlier,current),false)
})

const source=fs.readFileSync(new URL('../src/components/GeoOtherSearchEntries.vue',import.meta.url),'utf8')
const {descriptor,errors}=parse(source),script=compileScript(descriptor,{id:'other-search'})
assert.deepEqual(errors,[])
const names=script.scriptSetupAst.filter(row=>row.type==='VariableDeclaration').flatMap(row=>row.declarations).flatMap(row=>row.id.type==='Identifier'?[row.id.name]:[])
const body=descriptor.scriptSetup.content.replace(/^import .*$/gm,'')
function harness(api={},session=createFeedbackSession({entries:16,bytes:128*1024}),owner=cycle(),stopped=search.createSearchStoppedReceipts(),browser={}){
  const props=Vue.reactive({cycle:owner,busy:false}),events=[],unmount=[],effect=Vue.effectScope()
  const deps={computed:Vue.computed,reactive:Vue.reactive,ref:Vue.ref,watch:Vue.watch,...search,...portal,...browser,searchOnboardingSession:session,searchStoppedReceipts:stopped,
    onUnmounted:fn=>unmount.push(fn),defineProps:()=>props,defineEmits:()=>((...args)=>events.push(args)),externalWriteUncertain,
    exportFeedbackDraft:()=>{},journeyDate,ElMessageBox:{confirm:async()=>{}},
    geoSearchOnboardingApi:{get:async id=>({code:200,data:view(id==='cycle-a'?owner:cycle({id,project_id:5}))}),save:async(_id,body)=>response(body),...api}}
  const methods=effect.run(()=>new Function(...Object.keys(deps),body+`;return {${names.join(',')}}`)(...Object.values(deps)))
  return {...methods,props,events,session,stopped,stop(){unmount.forEach(fn=>fn());effect.stop()}}
}

const renderGuide=async page=>{
  const template=compileTemplate({source:descriptor.template.content,filename:'GeoOtherSearchEntries.vue',id:'search-copy-render',compilerOptions:{bindingMetadata:script.bindings}})
  assert.deepEqual(template.errors,[])
  const code=template.code.replace(/import \{([^}]+)\} from "vue"/g,(_all,imports)=>`const {${imports.split(',').map(item=>{const [name,alias]=item.trim().split(/\s+as\s+/);return alias?`${name}:${alias}`:name}).join(',')}}=Vue;`).replace('export function render','return function render')
  const render=new Function('Vue',code)(Vue)
  const app=Vue.createSSRApp({props:['busy'],setup:()=>({...search,...portal,journeyDate,...page}),render},{busy:false})
  app.component('el-button',{props:['disabled'],setup:(props,{slots})=>()=>Vue.h('button',{disabled:props.disabled},slots.default?.())})
  return renderToString(app)
}
test('mount only reads software progress, rapid save clicks send one request and owner busy releases on unmount',async()=>{
  const done=deferred(),calls=[],page=harness({save:(...args)=>{calls.push(args);return done.promise}})
  assert.equal(page.events[0][1].active,true);await flush();assert.equal(calls.length,0)
  page.forms.baidu.note=' 我看到平台还在处理。 '
  const pending=page.save();await page.save();await waitFor(()=>calls.length===1)
  assert.equal(calls[0][1].progress.note,' 我看到平台还在处理。 ')
  done.resolve(await response(calls[0][1]));await pending
  assert.equal(page.view.value.providers[0].record.revision,1);assert.equal(page.session.stats().entries,0)
  page.stop();assert.deepEqual(page.events.at(-1),['busy',{owner:page.owner,active:false}])
})
test('unknown save remains frozen across route unmount and retry uses identical scope guards and original note',async()=>{
  const session=createFeedbackSession(),requests=[]
  const first=harness({save:async(...args)=>{requests.push(args);throw new Error('connection lost')}},session)
  await flush();first.forms.baidu.note='原填写\n完整保留';await first.save()
  assert.equal(first.uncertain.baidu,true);assert.equal(first.locked.value,true);first.stop()
  const next=harness({save:async(...args)=>{requests.push(args);return response(args[1],cycle({revision:3}))}},session,cycle({revision:3}))
  await flush();assert.equal(next.forms.baidu.note,'原填写\n完整保留');assert.equal(next.uncertain.baidu,true)
  await next.save();assert.deepEqual(requests[0][1],requests[1][1]);assert.equal(requests[1][1].expected_revision,2)
  assert.equal(next.uncertain.baidu,false);assert.equal(next.view.value.revision,3);assert.equal(session.stats().entries,0);next.stop()
})
test('readback confirms an unknown request without repeating any platform or software write',async()=>{
  const session=createFeedbackSession();let requested
  const first=harness({save:async(_id,body)=>{requested=clone(body);throw new Error('lost')}},session)
  await flush();first.forms.baidu.site_added=false;await first.save();first.stop()
  const saved=await response(requested);let writes=0
  const next=harness({get:async()=>({code:200,data:saved.data.overview}),save:async()=>{writes++;throw Error('unexpected')}},session)
  await flush();assert.equal(next.forms.baidu.site_added,false);assert.equal(next.uncertain.baidu,false)
  assert.equal(session.stats().entries,0);assert.equal(writes,0);assert.match(next.notice.value,/核对到原进度/);next.stop()
})
test('late save receipt after switching cycle cannot affect the new scope and may clear only its own saved checkpoint',async()=>{
  const done=deferred(),calls=[],page=harness({save:(...args)=>{calls.push(args);return done.promise}})
  await flush();page.forms.baidu.note='A草稿';const pending=page.save();await waitFor(()=>calls.length===1)
  page.props.cycle=cycle({id:'cycle-b',project_id:5});await flush()
  assert.equal(page.forms.baidu.note,'');assert.equal(page.view.value.cycle_id,'cycle-b')
  done.resolve(await response(calls[0][1]));await pending
  assert.equal(page.forms.baidu.note,'');assert.equal(page.view.value.cycle_id,'cycle-b');assert.equal(page.notice.value,'')
  assert.equal(page.session.stats().entries,0);page.stop()
})
test('A to B to A ignores an earlier read and restores unsaved drafts per exact website identity',async()=>{
  const late=deferred();let reads=0;const page=harness({get:async id=>{reads++;return reads===2?late.promise:{code:200,data:view(id==='cycle-a'?cycle():cycle({id,project_id:5}))}}})
  await flush();page.forms.baidu.note='未保存A';page.props.cycle=cycle({id:'cycle-b',project_id:5});await flush()
  page.props.cycle=cycle();await flush();assert.equal(page.forms.baidu.note,'未保存A')
  late.resolve({code:200,data:view(cycle({id:'cycle-b',project_id:5}))});await flush()
  assert.equal(page.view.value.cycle_id,'cycle-a');assert.equal(page.forms.baidu.note,'未保存A');page.stop()
})
test('definite 400 keeps editable draft and refreshing cannot silently upgrade its stale provider revision',async()=>{
  const calls=[];let current=view()
  const page=harness({get:async()=>({code:200,data:current}),save:async(...args)=>{calls.push(args);throw {response:{status:400,data:{msg:'进度已变化'}}}}})
  await flush();page.forms.baidu.note='我保留的新意见';await page.save()
  assert.equal(page.uncertain.baidu,false);assert.equal(page.forms.baidu.note,'我保留的新意见')
  const original=search.searchPayload(view(),'baidu',{...search.emptySearchProgress(),site_added:true},0)
  current=(await response(original)).data.overview;await page.refresh();await page.save()
  assert.equal(calls[1][1].expected_provider_revision,0);assert.equal(calls[1][1].progress.note,'我保留的新意见')
  await page.adoptSaved();assert.equal(page.forms.baidu.site_added,true);assert.equal(page.forms.baidu.note,'')
  const session=page.session;page.stop();const reopened=harness({get:async()=>({code:200,data:current})},session);await flush()
  assert.equal(reopened.forms.baidu.note,'');assert.equal(reopened.forms.baidu.site_added,true);reopened.stop()
})
test('capacity failure and another mounted editor retain original checkpoints and prevent unchecked saves',async()=>{
  const session=createFeedbackSession({entries:1,bytes:64});let writes=0
  const page=harness({save:async()=>{writes++}},session);await flush();page.forms.baidu.note='保留当前填写';await page.save()
  assert.equal(writes,0);assert.match(page.checkpointError.value,/容量|导出/);assert.equal(page.forms.baidu.note,'保留当前填写');page.stop()
  const shared=createFeedbackSession(),one=harness({},shared),two=harness({},shared);await flush()
  one.forms.baidu.note='先写入';two.forms.baidu.note='另一窗口';assert.match(two.checkpointError.value,/另一入口/)
  await two.save();assert.equal(search.searchRecord(two.view.value,'baidu'),null)
  assert.equal(shared.read(search.searchCheckpointKey(one.view.value,'baidu')).value.form.note,'先写入');one.stop();two.stop()
})
test('deliberate abandonment of an unknown stale retry retains the original uncertainty evidence without sending a request',async()=>{
  const requests=[];let revision=2
  const page=harness({get:async()=>({code:200,data:view(cycle({revision}))}),save:async(...args)=>{
    requests.push(args);if(requests.length===1)throw Error('lost receipt');if(requests.length===2)throw {response:{status:400,data:{msg:'资料版本已变化'}}};return response(args[1],cycle({revision}))
  }})
  await flush();page.forms.baidu.note='原请求需保留';await page.save();const original=clone(requests[0][1])
  revision=3;page.props.cycle=cycle({revision});await flush();await page.save()
  assert.equal(page.uncertain.baidu,true);assert.deepEqual(requests[1][1],original)
  const before=clone(page.view.value);await page.adoptSaved()
  assert.equal(requests.length,2);assert.deepEqual(page.view.value,before);assert.equal(page.uncertain.baidu,false)
  assert.equal(page.stoppedUnknownCount.value,1);assert.match(page.notice.value,/原请求结果仍未知/)
  const preserved=page.stopped.list(page.view.value)[0];assert.deepEqual(preserved.payload,original)
  assert.equal(preserved.status,'user_stopped_retry_result_unknown')
  page.forms.baidu.note='明确的新填写';await page.save();assert.equal(requests[2][1].expected_revision,3)
  assert.equal(page.stopped.list(page.view.value)[0].payload.progress.note,'原请求需保留');page.stop()
  const full=search.createSearchStoppedReceipts({entries:0,bytes:0}),blocked=harness({save:async()=>{throw Error('lost')}},undefined,cycle(),full)
  await flush();blocked.forms.baidu.note='仍需保留';await blocked.save();await blocked.adoptSaved()
  assert.equal(blocked.uncertain.baidu,true);assert.equal(blocked.forms.baidu.note,'仍需保留');assert.match(blocked.error.value,/容量已满/);blocked.stop()
})
test('settling another mounted editor receipt preserves both its saved note and this editor draft and base across remount',async()=>{
  const session=createFeedbackSession();let server=view();let writes=0
  const api={get:async()=>({code:200,data:server}),save:async(_id,body)=>{writes++;server=(await response(body)).data.overview;throw Error('lost receipt')}}
  const one=harness(api,session),two=harness(api,session);await flush()
  one.forms.baidu.note='入口一的原请求';two.forms.baidu.note='入口二的未保存填写'
  assert.match(two.checkpointError.value,/另一入口/);await one.save()
  assert.equal(one.uncertain.baidu,true);assert.equal(one.forms.baidu.note,'入口一的原请求')
  await two.refresh()
  assert.equal(writes,1);assert.equal(two.forms.baidu.note,'入口二的未保存填写');assert.equal(two.bases.baidu,0)
  assert.equal(search.searchRecord(two.view.value,'baidu').progress.note,'入口一的原请求')
  assert.equal(search.searchRecord(two.view.value,'baidu').revision,1)
  assert.match(two.notice.value,/当前未保存填写及原版本保持/)
  const checkpoint=session.read(search.searchCheckpointKey(two.view.value,'baidu'))
  assert.equal(checkpoint.value.form.note,'入口二的未保存填写');assert.equal(checkpoint.value.base,0);assert.equal(checkpoint.value.frozen,null)
  one.stop();two.stop()
  const reopened=harness(api,session);await flush()
  assert.equal(reopened.forms.baidu.note,'入口二的未保存填写');assert.equal(reopened.bases.baidu,0)
  assert.equal(search.searchRecord(reopened.view.value,'baidu').progress.note,'入口一的原请求')
  assert.equal(writes,1);reopened.stop()
})
test('a receipt matching this editor checkpoint cannot replace text edited after the request froze',async()=>{
  let server=view();const page=harness({get:async()=>({code:200,data:server}),save:async(_id,body)=>{server=(await response(body)).data.overview;throw Error('lost')}})
  await flush();page.forms.baidu.note='原请求';await page.save()
  // The real form is disabled while unknown; also protect against another programmatic editor.
  page.forms.baidu.note='后来编辑';await page.refresh()
  assert.equal(page.forms.baidu.note,'后来编辑');assert.equal(page.bases.baidu,0)
  assert.equal(search.searchRecord(page.view.value,'baidu').progress.note,'原请求');page.stop()
})
test('site copy uses only the overview origin, remains available during unknown saves, and never writes progress',async()=>{
  const copied=[],calls={get:0,save:0},page=harness({
    get:async()=>{calls.get++;return {data:view()}},save:async()=>{calls.save++;throw Error('lost receipt')}
  },undefined,cycle(),undefined,{navigator:{clipboard:{writeText:async text=>copied.push(text)}}})
  await flush();page.forms.baidu.note='原有填写';await page.save()
  assert.equal(page.uncertain.baidu,true)
  const before=clone({forms:page.forms,uncertain:page.uncertain,bases:page.bases,frozen:page.frozen,view:page.view.value,error:page.error.value,notice:page.notice.value})
  await page.copySite('website','https://arbitrary.example/');assert.equal(page.siteCopyNotice.value,'官网地址已复制。')
  await page.copySite('domain');assert.match(page.siteCopyNotice.value,/不含协议或路径/)
  assert.deepEqual(copied,['https://example.org/','example.org'])
  assert.deepEqual(clone({forms:page.forms,uncertain:page.uncertain,bases:page.bases,frozen:page.frozen,view:page.view.value,error:page.error.value,notice:page.notice.value}),before)
  assert.deepEqual(calls,{get:1,save:1})
  page.provider.value='toutiao';await page.copySite('domain');assert.equal(copied.length,2)
  await page.copySite('website');assert.equal(copied.at(-1),'https://example.org/');page.stop()
})

test('site copy refuses unvalidated values and clipboard failures show a fixed short reason',async()=>{
  let calls=0
  const detail='Traceback: C:/SyntheticCustomer/private/profile.json'
  const page=harness({},undefined,cycle(),undefined,{navigator:{clipboard:{writeText:async()=>{calls++;throw new Error(detail)}}}})
  await flush();await page.copySite('website')
  assert.equal(page.siteCopyError.value,'复制未完成，请选择上面的官网地址手动复制。')
  assert.doesNotMatch(page.siteCopyError.value,/SyntheticCustomer|Traceback/)
  const validated=clone(page.view.value)
  for(const change of [{supported:false},{site_origin:'https://arbitrary.example/path'},{context_sha256:'unknown'}]){
    page.view.value={...validated,...change};await page.copySite('website')
  }
  page.view.value=validated;await page.copySite('https://arbitrary.example/');assert.equal(calls,1);page.stop()
})

test('late clipboard success and failure cannot overwrite a new brand, platform or newer copy result',async()=>{
  const transitions=[
    page=>{page.provider.value='toutiao'},
    page=>{page.provider.value='toutiao';page.provider.value='baidu'},
    page=>{page.props.cycle=cycle({profile:{...cycle().profile,name:'另一品牌'}})},
    page=>{page.props.cycle=cycle({profile:{...cycle().profile,name:'另一品牌'}});page.props.cycle=cycle()},
    ()=>{}
  ]
  for(const transition of transitions)for(const rejected of [false,true]){
    const done=deferred();let calls=0
    const page=harness({},undefined,cycle(),undefined,{navigator:{clipboard:{writeText:async()=>{calls++;if(calls===1)await done.promise}}}})
    await flush();const old=page.copySite('website');transition(page);await flush()
    await page.copySite(page.provider.value==='baidu'?'domain':'website')
    const current=page.siteCopyNotice.value
    assert.ok(current);assert.equal(page.siteCopyError.value,'')
    if(rejected)done.reject(new Error('old clipboard failure'));else done.resolve()
    await old
    assert.equal(page.siteCopyNotice.value,current);assert.equal(page.siteCopyError.value,'');page.stop()
  }
  const done=deferred(),page=harness({},undefined,cycle(),undefined,{navigator:{clipboard:{writeText:()=>done.promise}}})
  await flush();const old=page.copySite('website');page.stop();done.resolve();await old
  assert.equal(page.siteCopyNotice.value,'');assert.equal(page.siteCopyError.value,'')
})

test('guide renders a website copy action for both providers and domain/protocol/www guidance only for Baidu',async()=>{
  const page=harness();await flush()
  const baidu=await renderGuide(page)
  assert.match(baidu,/>复制官网地址<\/button>/);assert.match(baidu,/>复制域名<\/button>/)
  assert.match(baidu,/协议选 HTTPS/);assert.match(baidu,/www 主站建议.*实际官网主地址核对/)
  page.provider.value='toutiao';await flush()
  const toutiao=await renderGuide(page)
  assert.match(toutiao,/>复制官网地址<\/button>/);assert.doesNotMatch(toutiao,/复制域名|协议选 HTTPS|www 主站建议/)
  assert.equal(page.props.cycle.profile.website,'https://example.org/product');page.stop()
})

test('native link fallback is immediate, repeated clicks open once, and opening does not save provider progress',async()=>{
  const done=deferred(),calls=[],writes=[],copied=[]
  const page=harness({save:async(...args)=>writes.push(args)},undefined,cycle(),undefined,{
    openSearchPortal:(event,id,purpose)=>portal.openSearchPortal(event,id,purpose,{pywebview:{api:{open_search_portal:async(...args)=>{calls.push(args);return done.promise}}}}),
    navigator:{clipboard:{writeText:async url=>copied.push(url)}}
  })
  await flush();page.forms.baidu.note='保留原填写'
  let prevented=0
  const pending=page.openPortal({preventDefault(){prevented++}},'entry')
  assert.equal(prevented,1);assert.equal(page.portalUrl.value,search.searchProviders.baidu.entry)
  await page.openPortal({preventDefault(){prevented++}},'entry');await flush()
  assert.equal(prevented,2);assert.deepEqual(calls,[['baidu','entry']])
  await page.copyPortal();assert.deepEqual(copied,[search.searchProviders.baidu.entry])
  done.resolve({launch_requested:true,provider:'baidu',purpose:'entry',url:search.searchProviders.baidu.entry});await pending
  assert.match(page.portalMessage.value,/已请求系统默认浏览器/);assert.match(page.portalMessage.value,/登录和页面状态需自行核对/)
  assert.equal(page.portalOpening.value,false);assert.deepEqual(writes,[])
  assert.equal(page.forms.baidu.note,'保留原填写');assert.equal(search.searchRecord(page.view.value,'baidu'),null);page.stop()
})

test('failed native launch and denied clipboard retain a readable full official URL',async()=>{
  const page=harness({},undefined,cycle(),undefined,{
    openSearchPortal:(event,id,purpose)=>portal.openSearchPortal(event,id,purpose,{pywebview:{api:{open_search_portal:async()=>({launch_requested:false,reason:'browser_request_rejected'})}}}),
    navigator:{clipboard:{writeText:async()=>{throw Error('denied')}}}
  })
  await flush();await page.openPortal({preventDefault(){}},'help')
  assert.match(page.portalError.value,/复制地址/);assert.equal(page.portalUrl.value,search.searchProviders.baidu.help)
  await page.copyPortal();assert.match(page.portalError.value,/选择并复制上面的完整地址/)
  assert.equal(page.portalUrl.value,search.searchProviders.baidu.help);page.stop()
})

test('switching providers discards late launch receipts without changing the new provider',async()=>{
  const done=deferred(),page=harness({},undefined,cycle(),undefined,{
    openSearchPortal:event=>{event.preventDefault();return done.promise}
  })
  await flush();const pending=page.openPortal({preventDefault(){}},'entry')
  page.provider.value='toutiao';await flush()
  assert.equal(page.portalUrl.value,'');assert.equal(page.portalOpening.value,false)
  done.reject(new Error('old Baidu failure'));await pending
  assert.equal(page.provider.value,'toutiao');assert.equal(page.portalError.value,'');assert.equal(page.portalMessage.value,'');page.stop()
})

test('component never renders raw bridge details even if an unexpected rejection contains a local traceback',async()=>{
  const detail='Traceback: C:/SyntheticCustomer/private/profile.json\nRuntimeError: sensitive detail'
  const page=harness({},undefined,cycle(),undefined,{openSearchPortal:event=>{event.preventDefault();return Promise.reject(new Error(detail))}})
  await flush();await page.openPortal({preventDefault(){}},'entry')
  assert.equal(page.portalError.value,portal.searchPortalErrorMessage(null))
  assert.doesNotMatch(page.portalError.value,/SyntheticCustomer|Traceback|RuntimeError|sensitive/)
  assert.equal(page.portalUrl.value,search.searchProviders.baidu.entry);page.stop()
})

test('component keeps timeout unknown with copy fallback and never automatically opens again after a late receipt',async context=>{
  context.mock.timers.enable({apis:['setTimeout']})
  const done=deferred(),calls=[],page=harness({},undefined,cycle(),undefined,{
    openSearchPortal:(event,id,purpose)=>portal.openSearchPortal(event,id,purpose,{pywebview:{api:{open_search_portal:async(...args)=>{calls.push(args);return done.promise}}}})
  })
  await flush();const pending=page.openPortal({preventDefault(){}},'entry');await flush()
  context.mock.timers.tick(8000);await pending
  const unknown=page.portalError.value
  assert.match(unknown,/打开状态未知/);assert.doesNotMatch(unknown,/失败|已打开/)
  assert.equal(page.portalOpening.value,false);assert.equal(page.portalUrl.value,search.searchProviders.baidu.entry)
  done.resolve({launch_requested:true,provider:'baidu',purpose:'entry',url:search.searchProviders.baidu.entry});await flush()
  context.mock.timers.tick(60000);await flush()
  assert.deepEqual(calls,[['baidu','entry']]);assert.equal(page.portalError.value,unknown);assert.equal(page.portalMessage.value,'');page.stop()
})

test('guide template and both page integrations compile with fixed official links and no file/token submission',()=>{
  assert.deepEqual(compileTemplate({source:descriptor.template.content,filename:'GeoOtherSearchEntries.vue',id:'other-search',compilerOptions:{bindingMetadata:script.bindings}}).errors,[])
  assert.deepEqual(compileStyle({source:descriptor.styles[0].content,id:'other-search'}).errors,[])
  assert.ok(source.includes('不证明豆包采用'));assert.ok(source.includes('未知 / 尚未核对'))
  assert.ok(!source.includes('v-html'));assert.ok(!source.includes('type="file"'));assert.ok(!source.includes('localStorage'));assert.ok(!source.includes('sessionStorage'))
  for(const name of ['GeoJourney','GeoContentLoop']){
    const {descriptor:parent}=parse(fs.readFileSync(new URL(`../src/views/${name}.vue`,import.meta.url),'utf8'))
    const compiled=compileScript(parent,{id:name});assert.ok(parent.template.content.includes('<GeoOtherSearchEntries'))
    assert.deepEqual(compileTemplate({source:parent.template.content,filename:name+'.vue',id:name,compilerOptions:{bindingMetadata:compiled.bindings}}).errors,[])
  }
})
