import assert from 'node:assert/strict'
import fs from 'node:fs'
import test from 'node:test'
import * as Vue from 'vue'
import {renderToString} from '@vue/server-renderer'
import {parse,compileScript,compileTemplate,compileStyle} from '@vue/compiler-sfc'
import * as destinationAvailability from '../src/utils/geoDestinationAvailability.js'

const apiSource=fs.readFileSync(new URL('../src/api/geoDirectoryApplications.js',import.meta.url),'utf8')
const exported=[...apiSource.matchAll(/export const (\w+)/g)].map(item=>item[1])
const apiModule=http=>new Function('http',apiSource.replace(/^import .*$/m,'').replaceAll('export const ','const ')+`;return {${exported.join(',')}}`)(http)
const helpers=apiModule({})
const hash='a'.repeat(64),reviewHash='b'.repeat(64)
const fields=(changes={})=>({name:'示例协作服务',url:'https://example.org/',rate:'GLM 示例：输入 4.8 元 / 百万 Token',
  source:'https://example.org/pricing/ ，已确认的公开售价说明',models:['GLM'],contact:'',notes:'',...changes})
const target=(changes={})=>({id:'target-a',key:'target_target-a',revision:2,config_sha256:'c'.repeat(64),config:{source_scope:'https://grokcode.cn/api-transit',name:'GrokCode'},...changes})
const cycle=(changes={})=>({id:'cycle-a',project_id:5,profile:{name:'示例协作服务',entity_type:'product',website:'https://example.org/'},destinations:[target()],...changes})
const record=(changes={})=>({id:'application-a',adapter:helpers.directoryAdapter,project_id:5,cycle_id:'cycle-a',target_id:'target-a',target_revision:2,
  revision:1,fields:fields(),review_sha256:reviewHash,stage:'draft',reviewed:false,open_nonce:null,receipt:null,
  source:'software_prepared',evidence_level:'software_prepared',can_edit:true,can_review:true,can_open:false,can_inspect:false,
  can_record_external_receipt:true,can_refresh_context:false,session_count:0,submit_intent:null,read_only:false,submit_attempted:false,window_open:false,native_task_id:null,...changes})
const overview=(applications=[],changes={})=>({supported:true,reason:'',adapter:helpers.directoryAdapter,entry_url:helpers.directoryEntry,
  context_sha256:hash,field_limits:helpers.directoryLimits,model_options:['GLM'],applications,...changes})
const deferred=()=>{let resolve,reject;const promise=new Promise((done,fail)=>{resolve=done;reject=fail});return {promise,resolve,reject}}
const settle=async()=>{for(let i=0;i<12;i++)await Promise.resolve()}
const storage=()=>{const values=new Map();return {getItem:key=>values.get(key)||null,setItem:(key,value)=>values.set(key,value),removeItem:key=>values.delete(key),values}}

const filename=new URL('../src/components/GeoDirectoryApplication.vue',import.meta.url)
const {descriptor,errors}=parse(fs.readFileSync(filename,'utf8'),{filename:filename.pathname});assert.deepEqual(errors,[])
const compiled=compileScript(descriptor,{id:'directory-application-test'})
let source=descriptor.scriptSetup.content
for(const node of compiled.scriptSetupAst.filter(node=>node.type==='ImportDeclaration').reverse())source=source.slice(0,node.start)+source.slice(node.end)
const names=compiled.scriptSetupAst.flatMap(node=>node.type==='VariableDeclaration'?node.declarations:[]).map(node=>node.id.name).filter(Boolean)
const pages=[]
test.afterEach(()=>{while(pages.length)pages.pop().stop()})
function harness({initial=[],overrides={},nativeOverrides={},store=storage(),value=cycle()}={}){
  const props=Vue.reactive({cycle:value,target:value.destinations[0],task:{},busy:false,nativeAvailable:true}),calls=[],mounted=[],unmounted=[],timers=[],exposed={},scope=Vue.effectScope()
  let server=overview(initial)
  const api={
    list:async(...args)=>{calls.push(['list',...args]);return {data:server}},
    save:async(id,payload)=>{calls.push(['save',id,payload]);server={...server,applications:[record({adapter:helpers.directoryAdapterForTarget(props.target),fields:structuredClone(payload.fields),revision:(server.applications[0]?.revision || 0)+1})]};return {data:server.applications[0]}},
    review:async(id,application,payload)=>{calls.push(['review',id,application,payload]);server={...server,applications:[{...server.applications[0],revision:server.applications[0].revision+1,stage:'reviewed',reviewed:true,can_review:false,can_open:true,open_nonce:'open-nonce-a'}]};return {data:server.applications[0]}},
    refreshContext:async(id,application,payload)=>{calls.push(['refresh-context',id,application,payload]);const frozen=server.applications[0].session_count>0;server={...server,applications:[{...server.applications[0],target_revision:props.target.revision,revision:server.applications[0].revision+1,fields:structuredClone(payload.fields),review_sha256:'d'.repeat(64),context_sha256:server.context_sha256,stage:'draft',reviewed:false,open_nonce:null,can_edit:!frozen,can_review:true,can_open:false,can_refresh_context:false,read_only:frozen}]};return {data:server.applications[0]}},
    externalReceipt:async(id,application,payload)=>{calls.push(['external',id,application,payload]);server={...server,applications:[{...server.applications[0],revision:server.applications[0].revision+1,stage:payload.status,receipt:{intake_uid:payload.intake_uid??null,evidence_sha256:payload.evidence_sha256,observed_at:payload.observed_at,observed_at_precision:payload.observed_at_precision,note:payload.note,owner_reported:true,public_listing_verified:false},geo_effect_evidence:false,can_edit:false,can_review:false,can_open:false,can_inspect:true,can_record_external_receipt:false,read_only:true,evidence_level:'owner_reported',source:'operator_assisted'}]};return {data:server.applications[0]}},
    ...overrides}
  const native={
    open_directory_application:async(...args)=>{calls.push(['open',...args]);server={...server,applications:[{...server.applications[0],stage:'awaiting_human_confirmation',can_edit:false,can_review:false,can_open:false,can_inspect:false,open_nonce:null,window_open:true,native_task_id:'native-app-a',read_only:true}]};return {id:'native-app-a',status:'running',kind:'directory_application',cycle_id:props.cycle.id,directory_application_id:'application-a'}},
    check_directory_application_session:async id=>{calls.push(['check',id]);return {id,status:'running',check_requested:true}},
    close_directory_application_session:async id=>{calls.push(['close',id]);return {id,status:'running',close_requested:true}},
    inspect_directory_application:async(...args)=>{calls.push(['inspect',...args]);return {id:'native-inspect-a',status:'running',kind:'directory_application_inspect',cycle_id:props.cycle.id,directory_application_id:'application-a'}},
    ...nativeOverrides}
  const deps={...helpers,computed:Vue.computed,reactive:Vue.reactive,ref:Vue.ref,watch:Vue.watch,onMounted:fn=>mounted.push(fn),onUnmounted:fn=>unmounted.push(fn),
    defineProps:()=>props,defineExpose:value=>Object.assign(exposed,value),window:{pywebview:{api:native},localStorage:store,navigator:{clipboard:{writeText:async text=>calls.push(['clipboard',text])}}},setInterval:fn=>{timers.push(fn);return timers.length},clearInterval:()=>{},geoDirectoryApplicationsApi:api}
  const page=scope.run(()=>new Function(...Object.keys(deps),source+`;return {${names.join(',')}}`)(...Object.values(deps)))
  const result={...page,props,calls,api,native,store,timers,exposed,server:()=>server,setServer:value=>{server=value},
    mount:async()=>{for(const fn of mounted)fn();await settle()},fill:changes=>Object.assign(page.form,fields(changes)),
    stop:()=>{for(const fn of unmounted)fn();scope.stop()}}
  pages.push(result);return result
}

const journeySource=fs.readFileSync(new URL('../src/views/GeoJourney.vue',import.meta.url),'utf8')
const journeyDescriptor=parse(journeySource).descriptor,journeyScript=compileScript(journeyDescriptor,{id:'directory-journey-logic'})
const journeyDeclarations=journeyScript.scriptSetupAst.flatMap(node=>node.type==='VariableDeclaration'?node.declarations:[])
const journeyExpression=name=>{const node=journeyDeclarations.find(node=>node.id.name===name);assert.ok(node,name);return journeyDescriptor.scriptSetup.content.slice(node.init.start,node.init.end)}
function journeyHarness({directory=true,oldDraft=null,analysisReady=true,nextTick=async()=>{}}={}){
  const calls=[],configured=target({publication_gate:{allowed:true,code:'allowed'},config:{...target().config,enabled:true}})
  const value=cycle({destinations:[configured],analysis:{method:analysisReady?'ai_api':'rule_observations_not_ai',briefs:[{id:'brief-a',topic:'示例选题'}]},channels:{zhihu:'知乎',[configured.key]:'GrokCode'},drafts:oldDraft?[structuredClone(oldDraft)]:[],facts:[]})
  const state={cycle:Vue.ref(value),cycleId:Vue.ref(value.id),projectId:Vue.ref(value.project_id),project:Vue.ref({id:value.project_id,...value.profile}),step:Vue.ref(2),
    platform:Vue.ref(directory?configured.key:'zhihu'),activeDraft:Vue.ref(oldDraft),articleId:Vue.ref(oldDraft?.article_id||null),briefId:Vue.ref('brief-a'),
    busy:Vue.ref(false),profileDirty:Vue.ref(false),needsReplacement:Vue.ref(false),publishedDraft:Vue.ref(false),reviewedDraft:Vue.ref(oldDraft?.status==='ready'),
    factsSaved:Vue.ref(true),lengthInvalid:Vue.ref(false),lengthReview:Vue.ref({}),reviewConfirmed:Vue.ref(false),editor:Vue.reactive({title:'原文章',content:'原正文',summary:'',tags:[]}),
    externalCycle:Vue.ref(false),usableReport:Vue.ref(true),report:Vue.ref({}),plan:Vue.ref(null),planConfirmed:Vue.ref(false),collectionPath:Vue.ref('automatic'),automaticReady:Vue.ref(true),
    nativeAvailable:Vue.ref(true),capabilitiesLoaded:Vue.ref(true),publisherPaused:Vue.ref(false),targetPublisher:Vue.ref(false),ownedDraft:Vue.ref(false),publisherName:Vue.ref('知乎'),
    hasPublication:Vue.ref(false),feedbackTotals:Vue.ref({withAnswers:0}),provider:Vue.ref('api'),working:Vue.ref(false),error:Vue.ref(''),
    form:{name:value.profile.name,website:value.profile.website,description:'示例介绍'},publication:{url:'',publishedAt:null,confirmed:false},
    directoryPanel:Vue.ref({openPanel:()=>calls.push(['directory-panel'])})}
  const deps={...helpers,...destinationAvailability,...state,computed:Vue.computed,nextTick,contextEpoch:0,destroyed:false,
    destinationSupportsFill:()=>true,confirmedFacts:()=>[],options:()=>({}),run:async action=>action(),
    startNative:async(kind,action)=>{calls.push(['native',kind]);return action()},
    window:{pywebview:{api:{generate_research:async(...args)=>calls.push(['native-generate',...args]),open_destination:async(...args)=>calls.push(['generic-login',...args])}}},
    geoContentApi:{generate:async(...args)=>{calls.push(['generate',...args]);return {data:{cycle:state.cycle.value,article_id:77}}},get:async()=>({data:state.cycle.value}),publication:async(...args)=>{calls.push(['article-receipt',...args]);return {data:state.cycle.value}}},
    articleApi:{updateArticle:async(...args)=>calls.push(['review-article',...args])},applyCycle:()=>{},loadEditor:()=>calls.push(['load-editor']),loadAccounts:async()=>calls.push(['accounts']),
    saveFacts:async()=>calls.push(['save-facts']),saveBrand:async()=>{},createCycle:async()=>{},collectBaseline:async()=>{},makePlan:async()=>{},
    nativeCycleCapability:()=>true,focusDiagnosticSection:()=>{},router:{push:async(...args)=>calls.push(['route',...args])},
    ElMessage:{success:()=>{}},ElMessageBox:{confirm:async()=>{}},openDetails:()=>calls.push(['details'])}
  const names=['needsAnalysis','selectedTarget','directoryTarget','directoryReady','directoryDrafts','channelOptionLabel','platformUnprepared','primaryLabel','primaryDisabled','actionHint',
    'canVisit','stepComplete','openDirectoryPanel','requireArticleChannel','generateResearch','reviewArticle','openDestinationLogin','openPublisher','recordPublication','replaceTargetDraft','continuePublication','primaryAction']
  const body=names.map(name=>`const ${name}=${journeyExpression(name)};`).join('\n')+`;return {${names.join(',')}};`
  const page=new Function(...Object.keys(deps),body)(...Object.values(deps))
  return {...state,...page,calls,configured}
}

test('Journey directory stages need no article, facts, account, or article review and the footer only opens the existing panel',async()=>{
  const page=journeyHarness();page.briefId.value='';page.factsSaved.value=false
  for(const step of [2,3]){
    page.step.value=step;assert.equal(page.primaryLabel.value,'打开目录申请');assert.equal(page.primaryDisabled.value,false)
    assert.equal(page.canVisit(3),true);assert.equal(page.stepComplete(2),false);assert.equal(page.stepComplete(3),false)
    await page.primaryAction()
  }
  assert.deepEqual(page.calls,[['directory-panel'],['directory-panel']]);assert.equal(page.activeDraft.value,null)
  assert.deepEqual(page.cycle.value.drafts,[]);assert.match(page.actionHint.value,/待审核不计作已收录或 GEO 成功/)
})

test('Journey persisted directory selection and old articles cannot enter any article side effect',async()=>{
  const original={article_id:12,platform:target().key,brief_id:'brief-a',title:'此前的原稿',content:'此前保存的正文',status:'ready',jobs:[{id:39,demo:false,status:'need_action'}]}
  const page=journeyHarness({oldDraft:original}),before=JSON.parse(JSON.stringify(page.cycle.value.drafts))
  assert.equal(page.directoryTarget.value.id,'target-a');assert.equal(page.directoryDrafts.value.length,1)
  for(const action of [()=>page.generateResearch('draft'),page.reviewArticle,page.openDestinationLogin,page.openPublisher,page.recordPublication,page.replaceTargetDraft]){
    await assert.rejects(action,/目录资料/)
  }
  await assert.rejects(()=>page.continuePublication({article_id:12,id:39}),/目录资料/)
  await page.primaryAction();assert.deepEqual(page.calls,[['directory-panel']])
  assert.deepEqual(page.cycle.value.drafts,before);assert.deepEqual(original.title,'此前的原稿')
  page.provider.value='doubao';await assert.rejects(()=>page.generateResearch('draft'),/目录资料/)
  assert.equal(page.calls.some(call=>['native','generate','review-article','article-receipt','generic-login','route'].includes(call[0])),false)
  const legacy=journeyHarness();legacy.platform.value='GrokCode';assert.equal(legacy.directoryTarget.value.id,'target-a')
  await legacy.primaryAction();assert.deepEqual(legacy.calls,[['directory-panel']])
  await assert.rejects(()=>legacy.generateResearch('draft'),/目录资料/)
  assert.equal(helpers.directoryTargetForChannel({...legacy.cycle.value,destinations:[]},'GrokCode'),null)
  assert.equal(helpers.directoryTargetForChannel({...legacy.cycle.value,destinations:[legacy.configured,target({id:'target-b',key:'target_target-b'})]},'GrokCode'),null)
})

test('Journey keeps source analysis prerequisites and other article channels continue generating their normal draft',async()=>{
  const pending=journeyHarness({analysisReady:false});assert.equal(pending.directoryReady.value,false)
  assert.equal(pending.primaryLabel.value,'分析来源并制定选题');assert.equal(pending.canVisit(3),false)
  await pending.primaryAction();assert.equal(pending.calls.find(call=>call[0]==='generate')[2].kind,'analysis')
  assert.equal(pending.calls.some(call=>call[0]==='directory-panel'),false)
  await assert.rejects(()=>pending.generateResearch('draft'),/目录资料/)
  const article=journeyHarness({directory:false});assert.equal(article.directoryTarget.value,null)
  assert.equal(article.primaryLabel.value,'生成平台草稿');assert.equal(article.primaryDisabled.value,false)
  await article.primaryAction();assert.deepEqual(article.calls.find(call=>call[0]==='generate'),['generate','cycle-a',{kind:'draft',brief_id:'brief-a',platform:'zhihu'}])
  assert.equal(article.calls.some(call=>call[0]==='directory-panel'),false)
  pending.cycle.value={...pending.cycle.value,project_id:6};assert.equal(pending.directoryReady.value,false)
  const incomplete=journeyHarness();incomplete.cycle.value={...incomplete.cycle.value,analysis:undefined};assert.equal(incomplete.directoryReady.value,false)
})

test('Journey directory panel entry respects current project/target and an unresolved use review is not an article route',async()=>{
  const waiting=deferred(),page=journeyHarness({nextTick:()=>waiting.promise}),opening=page.openDirectoryPanel()
  page.cycleId.value='different-cycle';waiting.resolve();await opening;assert.deepEqual(page.calls,[])
  const blocked=journeyHarness();blocked.configured.publication_gate={allowed:false,code:'research_only',reason:'此平台仅用于来源研究，请先核对用途'}
  assert.match(blocked.actionHint.value,/仅用于来源研究/)
  await blocked.primaryAction();assert.deepEqual(blocked.calls,[['directory-panel']])
  const component=harness({initial:[record({stage:'submitted_pending_review',can_edit:false,can_open:false,can_review:false,receipt:{intake_uid:'lead-old'},read_only:true})]});await component.mount()
  assert.deepEqual(Object.keys(component.exposed),['openPanel']);component.exposed.openPanel()
  assert.equal(component.visible.value,true);assert.equal(component.record.value.receipt.intake_uid,'lead-old')
  assert.equal(component.calls.some(call=>['save','review','open','external'].includes(call[0])),false)
})

test('Journey renders the shared directory card before article content and leaves old articles read-only',async()=>{
  const find=node=>node?.type===1&&node.props?.some(prop=>prop.name==='key'&&prop.value?.content==='directory')?node:(node?.children||[]).map(find).find(Boolean)
  const node=find(journeyDescriptor.template.ast);assert.ok(node)
  const page=journeyHarness({oldDraft:{article_id:12,platform:target().key,title:'旧稿',content:'原文',status:'ready'}})
  const component={render:Vue.compile(node.loc.source.replace('v-else-if=','v-if=')),setup:()=>({...page,...helpers,...destinationAvailability,task:{},nativeAvailable:true,selectContent:()=>{},applyReviewCycle:()=>{},openDetails:()=>{},goFeedback:()=>{}})}
  const app=Vue.createSSRApp(component)
  for(const name of ['el-form-item','el-select','el-option','el-button','el-alert'])app.component(name,{setup:(_,{slots})=>()=>Vue.h('div',slots.default?.())})
  for(const name of ['GeoDirectoryApplication','GeoExternalDirectoryReceipt','DestinationUseReview'])app.component(name,{setup:()=>()=>Vue.h('div',{'data-component':name},name)})
  const html=await renderToString(app)
  assert.match(html,/本渠道使用目录资料，不需要文章/);assert.match(html,/data-component="GeoDirectoryApplication"/);assert.match(html,/data-component="DestinationUseReview"/)
  assert.match(html,/旧文章及原任务仍保留/)
  assert.doesNotMatch(html,/文章标题|journey-article-content|DestinationSessionCard|公开文章链接|保存审稿|生成平台草稿/)
  assert.ok(journeySource.indexOf('key="directory"')<journeySource.indexOf('key="content"'))
  assert.deepEqual(compileTemplate({source:journeyDescriptor.template.content,filename:'GeoJourney.vue',id:'directory-journey-main'}).errors,[])
})

const contentSource=fs.readFileSync(new URL('../src/views/GeoContentLoop.vue',import.meta.url),'utf8'),contentDescriptor=parse(contentSource).descriptor
const contentScript=compileScript(contentDescriptor,{id:'directory-detailed-logic'}),contentDeclarations=contentScript.scriptSetupAst.flatMap(node=>node.type==='VariableDeclaration'?node.declarations:[])
const contentExpression=name=>{const node=contentDeclarations.find(node=>node.id.name===name);assert.ok(node,name);return contentDescriptor.scriptSetup.content.slice(node.init.start,node.init.end)}
function contentHarness({directory=true,analysisReady=true}={}){
  const calls=[],messages=[],configured=target({publication_gate:{allowed:true,code:'allowed'},config:{...target().config,enabled:true}})
  const old={article_id:12,platform:configured.key,status:'ready',title:'目录渠道旧稿',content:'旧正文',provenance:{destination:{id:configured.id},brief:{case_ids:[]},used_fact_ids:[]},jobs:[]}
  const article={article_id:13,platform:'zhihu',status:'ready',title:'知乎原稿',content:'原正文',provenance:{brief:{case_ids:[]},used_fact_ids:[]},jobs:[]}
  const value=cycle({destinations:[configured],facts:[{text:'已核对事实',source_url:'https://example.org/',confirmed:true}],analysis:{method:analysisReady?'ai_api':'rule_observations_not_ai',briefs:[{id:'brief-a'}]},drafts:[old,article],channels:{zhihu:'知乎',[configured.key]:'GrokCode'}})
  const state={cycle:Vue.ref(value),cycleId:Vue.ref(value.id),platform:Vue.ref(directory?configured.key:'zhihu'),briefId:Vue.ref('brief-a'),task:Vue.ref({status:'idle'}),working:Vue.ref(false),confirmed:Vue.ref(true),factsText:Vue.ref('已核对事实 | https://example.org/'),
    publication:Vue.ref({article_id:12,published_at:'2026-11-02T02:31',confirmed:true,url:'https://example.org/'}),publicationVisible:Vue.ref(false),cycles:Vue.ref([]),contentRecovery:Vue.ref(null),recoveryUnreadable:Vue.ref(false),nativeAvailable:Vue.ref(true)}
  const deps={...helpers,...destinationAvailability,...state,computed:Vue.computed,cycleScope:()=>({id:value.id,epoch:0}),sameCycleScope:()=>true,options:()=>({}),
    rememberContentRecovery:()=>{},storeContentRecovery:()=>{},applyCycle:()=>true,destinationSupportsFill:()=>true,destinationPreparationText:()=>'',destinationContinuationText:()=>'',
    window:{pywebview:{api:{generate_research:async(...args)=>{calls.push(['native-generate',...args]);return {id:'task-a',status:'running'}},stop_task:()=>calls.push(['stop']),open_destination:async(...args)=>{calls.push(['generic-open',...args]);return {id:'task-a',status:'running'}}}}},
    geoContentApi:{generate:async(...args)=>{calls.push(['generate',...args]);return {data:{cycle:value}}},list:async()=>({data:[]}),publication:async(...args)=>{calls.push(['article-receipt',...args]);return {data:value}}},
    ElMessage:{info:message=>messages.push(message),error:message=>messages.push(message),success:message=>messages.push(message)},ElMessageBox:{confirm:async()=>calls.push(['confirm'])},router:{push:value=>calls.push(['route',value])}}
  const names=['directoryTask','factsValue','factsStoredText','canDraft','selectedDirectoryTarget','articleCycle','draftDestination','generate','stop','selectDestination','openPublication','savePublication','openDestination','fillDestination','edit','legacyChannel','publish']
  const body=names.map(name=>`const ${name}=${contentExpression(name)};`).join('\n')+`;return {${names.join(',')}};`
  const page=new Function(...Object.keys(deps),body)(...Object.values(deps))
  return {...state,...page,calls,messages,configured,old,article}
}

test('detailed content blocks directory draft generation in both planning states and keeps old directory articles read-only',async()=>{
  for(const analysisReady of [true,false]){
    const page=contentHarness({analysisReady}),before=JSON.parse(JSON.stringify(page.cycle.value.drafts))
    assert.equal(page.canDraft.value,false);assert.equal(page.selectedDirectoryTarget.value.id,'target-a')
    await page.generate('draft','api');await page.generate('draft','doubao');page.edit(page.old);page.publish(page.old);page.openPublication(page.old);await page.savePublication()
    await page.fillDestination(page.old);await page.openDestination({target_id:'target-a',article_id:12})
    assert.deepEqual(page.calls,[]);assert.deepEqual(page.cycle.value.drafts,before)
    assert.deepEqual(page.articleCycle.value.drafts.map(item=>item.article_id),[13]);assert.ok(page.messages.every(message=>/目录|只读/.test(message)))
  }
  const article=contentHarness({directory:false});assert.equal(article.canDraft.value,true);await article.generate('draft','api')
  assert.deepEqual(article.calls.find(call=>call[0]==='generate'),['generate','cycle-a',{kind:'draft',brief_id:'brief-a',platform:'zhihu'}])
  article.edit(article.article);assert.equal(article.calls.some(call=>call[0]==='route'),true)
})

test('detailed content directory tasks cannot use generic Stop while other task kinds keep that action',async()=>{
  const page=contentHarness()
  for(const kind of ['directory_application','directory_application_inspect']){page.task.value={kind,status:'running'};page.stop()}
  assert.deepEqual(page.calls,[]);assert.equal(page.messages.length,2)
  page.task.value={kind:'run',status:'running'};page.stop();assert.deepEqual(page.calls,[['stop']])
  const find=node=>node?.type===1&&node.tag==='el-card'&&node.loc.source.includes('data-testid="content-directory-task"')?node:(node?.children||[]).map(find).find(Boolean)
  const card=find(contentDescriptor.template.ast);assert.ok(card)
  const render=Vue.compile(card.loc.source)
  const output=async (kind,status='running')=>{
    const app=Vue.createSSRApp({render,setup:()=>({nativeAvailable:true,directoryTask:helpers.directoryTaskView({kind}),task:{kind,status},busy:status==='running',nativeBusy:status==='running',writingContextPending:false,clientMode:'consumer',model:'',externalCycle:false,
      cycle:null,contentRecovery:null,loginScope:null,cycleId:'cycle-a',working:false,recoveredDraftArticle:()=>false,currentTaskMessage:'',recoveryStorageError:'',selectWritingMode:()=>{},selectWritingModel:()=>{},login:()=>{},stop:()=>{}})})
    for(const name of ['el-card','el-select','el-option','el-input','el-button','DoubaoPreparation','DoubaoTaskRecovery'])app.component(name,{setup:(_,{slots})=>()=>Vue.h(name==='el-button'?'button':'div',slots.default?.())})
    return renderToString(app)
  }
  for(const kind of ['directory_application','directory_application_inspect']){
    const html=await output(kind);assert.match(html,/原目录申请卡“结束当前窗口”/);assert.doesNotMatch(html,/停止任务<\/button>|豆包写作会话|登录豆包/)
  }
  assert.match(await output('run'),/停止任务<\/button>/)
  for(const kind of ['directory_application','directory_application_inspect']){
    const html=await output(kind,'completed');assert.match(html,/豆包写作会话/);assert.match(html,/登录豆包/);assert.doesNotMatch(html,/data-testid="content-directory-task"/)
  }
  assert.deepEqual(compileTemplate({source:contentDescriptor.template.content,filename:'GeoContentLoop.vue',id:'directory-detailed'}).errors,[])
})

test('directory modal keeps one gated primary action set and review checkboxes in its viewport footer',async()=>{
  const dialog=descriptor.template.ast.children.find(node=>node.type===1).children.find(node=>node.type===1&&node.tag==='el-dialog')
  assert.ok(dialog.props.some(prop=>prop.name==='modal-class'&&prop.value.content==='geo-directory-modal'))
  const footer=dialog.children.find(node=>node.type===1&&node.tag==='template'&&node.props.some(prop=>prop.type===7&&prop.name==='slot'&&prop.arg?.content==='footer'));assert.ok(footer)
  const all=node=>[node,...(node.children||[]).flatMap(all)],actions=['saveDraft','reviewDraft','refreshDraftContext','openWindow','checkWindow','closeWindow','inspectRecord']
  for(const action of actions){
    const matching=all(dialog).filter(node=>node.type===1&&node.tag==='el-button'&&node.props.some(prop=>prop.type===7&&prop.name==='on'&&prop.exp?.content===action))
    assert.equal(matching.length,1);assert.ok(all(footer).includes(matching[0]))
  }
  for(const field of ['confirmed','contextConfirmed'])assert.ok(all(footer).some(node=>node.type===1&&node.tag==='el-checkbox'&&node.props.some(prop=>prop.name==='model'&&prop.exp?.content===field)))
  const page=harness({initial:[record({can_inspect:true})]});await page.mount();page.visible.value=true
  const render=Vue.compile(descriptor.template.content)
  const output=async()=>{
    const app=Vue.createSSRApp({render,setup:()=>({...page,...helpers})})
    for(const name of [...new Set(descriptor.template.content.match(/el-[a-z-]+/g))])app.component(name,{props:['disabled','modelValue'],setup:(props,{slots})=>()=>name==='el-dialog'?Vue.h('div',[Vue.h('div',{'data-region':'body'},slots.default?.()),Vue.h('footer',slots.footer?.())]):Vue.h(name==='el-button'?'button':name==='el-checkbox'?'label':'div',{disabled:props.disabled},slots.default?.())})
    return renderToString(app)
  }
  const before=await output(),footerHtml=before.slice(before.indexOf('<footer'))
  assert.match(footerHtml,/<button[^>]*disabled[^>]*>确认这份申请资料<\/button>/);assert.match(footerHtml,/我已逐项核对/)
  assert.doesNotMatch(before.slice(0,before.indexOf('<footer')),/<button[^>]*>保存草稿<\/button>|<button[^>]*>确认这份申请资料<\/button>/)
  page.confirmed.value=true;assert.doesNotMatch((await output()).slice(before.indexOf('<footer')),/<button[^>]*disabled[^>]*>确认这份申请资料<\/button>/)
  const css=compileStyle({source:descriptor.styles[0].content,filename:'GeoDirectoryApplication.vue',id:'directory-modal',scoped:true});assert.deepEqual(css.errors,[])
  assert.match(css.code,/max-height:calc\(100dvh - 32px\)/);assert.match(css.code,/overflow-y:auto/);assert.match(css.code,/\.geo-directory-modal \.geo-directory-dialog \.el-dialog__footer/)
})

test('directory API stays separate from article publishing and passes CAS data unchanged',async()=>{
  const calls=[],http={get:async(...args)=>calls.push(['get',...args]),post:async(...args)=>calls.push(['post',...args])}
  const api=apiModule(http).geoDirectoryApplicationsApi,payload={target_id:'target-a',expected_context_sha256:hash,fields:fields()}
  await api.list('cycle-a','target-a');await api.get('cycle-a','application-a');await api.save('cycle-a',payload)
  await api.review('cycle-a','application-a',{expected_revision:1,expected_review_sha256:reviewHash,confirmed:true})
  await api.refreshContext('cycle-a','application-a',{expected_revision:1,expected_review_sha256:reviewHash,expected_context_sha256:hash,confirmed:true})
  await api.externalReceipt('cycle-a','application-a',{intake_uid:'lead-1'})
  assert.deepEqual(calls.map(call=>call[1]),[
    '/api/geo-content/cycles/cycle-a/directory-applications','/api/geo-content/cycles/cycle-a/directory-applications/application-a',
    '/api/geo-content/cycles/cycle-a/directory-applications','/api/geo-content/cycles/cycle-a/directory-applications/application-a/review',
    '/api/geo-content/cycles/cycle-a/directory-applications/application-a/refresh-context',
    '/api/geo-content/cycles/cycle-a/directory-applications/application-a/external-receipt'])
  assert.equal(calls[2][2],payload);assert.ok(calls.every(call=>call.at(-1).suppressGlobalError===true))
})

test('only the exact configured GrokCode source route is recognized and records stay project/target bound',()=>{
  assert.equal(helpers.isGrokCodeDirectory(target()),true)
  assert.equal(helpers.isGrokCodeDirectory(target({config:{source_scope:'https://grokcode.cn/api-transit/'}})),true)
  for(const url of ['https://evil.grokcode.cn/api-transit','https://grokcode.cn.evil.org/api-transit','http://grokcode.cn/api-transit',
    'https://user:pass@grokcode.cn/api-transit','https://grokcode.cn/api-transit/other','https://grokcode.cn/api-transit?q=x']){
    assert.equal(helpers.isGrokCodeDirectory(target({config:{source_scope:url}})),false)
  }
  const selected=helpers.directoryScope(cycle(),target())
  assert.equal(helpers.directoryRecordCurrent(record(),selected),true)
  assert.equal(helpers.directoryRecordBelongs(record({project_id:6}),selected),false)
  assert.equal(helpers.directoryRecordBelongs(record({target_id:'target-b'}),selected),false)
  assert.equal(helpers.directoryRecordCurrent(record({cycle_id:'old-cycle'}),selected),false)
  assert.notEqual(helpers.directoryScopeKey(cycle(),target()),helpers.directoryScopeKey(cycle({project_id:6}),target()))
})

test('a draft is saved, explicitly reviewed, then opened through only the exact native snapshot',async()=>{
  const page=harness();await page.mount();page.fill();assert.equal(page.canOpen.value,false)
  await page.saveDraft();assert.equal(page.dirty.value,false);assert.equal(page.canReview.value,false)
  page.confirmed.value=true;await page.reviewDraft();assert.equal(page.canOpen.value,true)
  assert.deepEqual(page.calls.find(call=>call[0]==='review').slice(1),['cycle-a','application-a',{expected_revision:1,expected_review_sha256:reviewHash,confirmed:true}])
  await page.openWindow()
  assert.deepEqual(page.calls.find(call=>call[0]==='open'),['open','cycle-a','application-a',reviewHash,'open-nonce-a'])
  assert.equal(page.record.value.stage,'awaiting_human_confirmation');assert.equal(page.canOpen.value,false)
  assert.equal(page.calls.some(call=>['publish','retry','execute','submit'].includes(call[0])),false)
})

test('editing even a reviewed draft removes local authorization and saves against the reviewed revision',async()=>{
  const page=harness({initial:[record()]});await page.mount();page.confirmed.value=true;await page.reviewDraft()
  assert.equal(page.canOpen.value,true);page.form.rate='另一份已确认公开售价说明'
  assert.equal(page.confirmed.value,false);assert.equal(page.canOpen.value,false);assert.equal(page.dirty.value,true)
  await page.refresh();assert.equal(page.editConflict.value,false)
  await page.saveDraft();assert.equal(page.calls.findLast(call=>call[0]==='save')[2].revision,2)
  assert.equal(page.record.value.reviewed,false);assert.equal(page.record.value.open_nonce,null)
  assert.equal(page.canOpen.value,false)
})

test('refresh and reopening the component preserve scoped unsaved edits without restoring confirmation',async()=>{
  const store=storage(),first=harness({initial:[record()],store});await first.mount()
  first.form.notes='本机未保存的公开补充说明';first.confirmed.value=true
  await first.refresh();assert.equal(first.form.notes,'本机未保存的公开补充说明');assert.equal(first.dirty.value,true)
  const second=harness({initial:[record()],store});await second.mount()
  assert.equal(second.form.notes,'本机未保存的公开补充说明');assert.equal(second.dirty.value,true);assert.equal(second.confirmed.value,false)
  const other=harness({store,value:cycle({project_id:6})});await other.mount()
  assert.equal(other.form.notes,'');assert.equal(other.confirmed.value,false)
})

test('first application starts from only this cycle brand name and offers existing official URLs without choosing an API or price',async()=>{
  const value=cycle({profile:{name:'本轮品牌',website:'https://example.org/',official_urls:['https://api.example.org/','https://example.org/','https://api.example.org/docs/']}})
  const page=harness({value});await page.mount()
  assert.deepEqual(helpers.directoryFields(page.form),helpers.directoryFields({name:'本轮品牌'}))
  assert.deepEqual(page.officialUrls.value,['https://example.org/','https://api.example.org/','https://api.example.org/docs/'])
  assert.equal(page.dirty.value,false);await page.refresh();assert.equal(page.form.name,'本轮品牌');assert.equal(page.form.url,'')
  assert.equal(page.form.rate,'');assert.equal(page.form.source,'');assert.deepEqual(page.form.models,[])
  page.form.url='https://api.example.org/';await page.refresh();assert.equal(page.form.url,'https://api.example.org/');assert.equal(page.dirty.value,true)
})

test('local draft edits and an existing server record take priority over first-time defaults',async()=>{
  const store=storage(),first=harness({store});await first.mount();first.form.name='本人正在编辑的服务别名';first.form.url='https://example.org/'
  const cached=harness({store,value:cycle()});await cached.mount()
  assert.equal(cached.form.name,'本人正在编辑的服务别名');assert.equal(cached.form.url,'https://example.org/');assert.equal(cached.dirty.value,true)
  const saved=harness({initial:[record({fields:fields({name:'已保存的本轮服务别名',url:'https://example.org/'})})]});await saved.mount()
  assert.equal(saved.form.name,'已保存的本轮服务别名');assert.equal(saved.form.rate,fields().rate);assert.deepEqual(saved.form.models,['GLM'])
  await saved.refresh();assert.equal(saved.form.name,'已保存的本轮服务别名');assert.equal(saved.dirty.value,false)
})

test('first-time defaults and URL candidates never transfer across customers and return only with their own unsaved copy',async()=>{
  const page=harness();await page.mount();page.form.name='客户A未保存别名';page.form.url='https://example.org/'
  page.setServer(overview([]));page.props.cycle=cycle({id:'cycle-b',project_id:6,profile:{name:'客户B',website:'https://b.example.org/',official_urls:['https://api.b.example.org/']}});page.props.target=page.props.cycle.destinations[0];await settle()
  assert.equal(page.form.name,'客户B');assert.equal(page.form.url,'');assert.deepEqual(page.officialUrls.value,['https://b.example.org/','https://api.b.example.org/'])
  assert.equal(page.dirty.value,false);assert.equal(page.form.rate,'');assert.deepEqual(page.form.models,[])
  page.props.cycle=cycle();page.props.target=page.props.cycle.destinations[0];await settle()
  assert.equal(page.form.name,'客户A未保存别名');assert.equal(page.form.url,'https://example.org/');assert.equal(page.dirty.value,true)
})

test('a newer server revision keeps the local copy and blocks stale save/review/open',async()=>{
  const page=harness({initial:[record()]});await page.mount();page.form.notes='保留本机编辑'
  page.setServer(overview([record({revision:2,fields:fields({notes:'别处更新'})})]));await page.refresh()
  assert.equal(page.form.notes,'保留本机编辑');assert.equal(page.editConflict.value,true)
  assert.equal(page.canSave.value,false);assert.equal(page.canReview.value,false);assert.equal(page.canOpen.value,false)
  await page.discardLocalEdits();assert.equal(page.form.notes,'别处更新');assert.equal(page.dirty.value,false)
})

test('unchanged read-only polls preserve explicit confirmation, but a changed review revokes it',async()=>{
  const page=harness({initial:[record()]});await page.mount();page.visible.value=true;page.confirmed.value=true
  await page.poll();assert.equal(page.confirmed.value,true)
  await page.reviewDraft();assert.equal(page.canOpen.value,true)
  await page.poll();assert.equal(page.canOpen.value,true)
  page.setServer(overview([record({revision:3,stage:'reviewed',reviewed:true,can_open:true,open_nonce:'new-nonce'})]));await page.poll()
  assert.equal(page.confirmed.value,false);assert.equal(page.canOpen.value,false)
})

test('target revision changes preserve unsaved fields and refresh-context explicitly invalidates the old review',async()=>{
  const page=harness({initial:[record({stage:'reviewed',reviewed:true,open_nonce:'old-nonce',can_open:true})]});await page.mount()
  page.form.notes='尚未保存但必须保留的公开说明';page.visible.value=true
  const revisedTarget=target({revision:3,config_sha256:'e'.repeat(64)}),nextCycle=cycle({destinations:[revisedTarget]})
  page.setServer(overview([record({stage:'reviewed',reviewed:true,open_nonce:null,can_edit:false,can_review:false,can_open:false,can_refresh_context:true,read_only:true})],{context_sha256:'f'.repeat(64)}))
  page.props.cycle=nextCycle;page.props.target=revisedTarget;await settle()
  assert.equal(page.form.notes,'尚未保存但必须保留的公开说明');assert.equal(page.dirty.value,true)
  assert.equal(page.outdatedRecord.value,true);assert.equal(page.contextRefreshEditable.value,true);assert.equal(page.canSave.value,false)
  assert.equal(page.canRefreshContext.value,false);page.contextConfirmed.value=true
  await page.refreshDraftContext()
  const payload=page.calls.find(call=>call[0]==='refresh-context')[3]
  assert.deepEqual(payload,{expected_revision:1,expected_review_sha256:reviewHash,expected_context_sha256:'f'.repeat(64),confirmed:true,fields:fields({notes:'尚未保存但必须保留的公开说明'})})
  assert.equal(page.record.value.target_revision,3);assert.equal(page.record.value.reviewed,false);assert.equal(page.record.value.open_nonce,null)
  assert.equal(page.dirty.value,false);assert.equal(page.confirmed.value,false);assert.equal(page.contextConfirmed.value,false);assert.equal(page.canOpen.value,false)
  page.confirmed.value=true;await page.reviewDraft();await page.openWindow()
  assert.deepEqual(page.calls.find(call=>call[0]==='open'),['open','cycle-a','application-a','d'.repeat(64),'open-nonce-a'])
})

test('context refresh cannot unlock unknown, intent, receipt or historical applications, even with contradictory capabilities',async()=>{
  for(const changes of [{session_count:-1},{submit_intent:{id:'intent'}},{receipt:{intake_uid:'lead-old'}},{cycle_id:'prior-cycle'},
    {target_id:'prior-target'},{stage:'submitted_pending_review'},{stage:'submission_unknown',session_count:1}]){
    const page=harness({initial:[record({can_refresh_context:true,read_only:true,can_edit:false,...changes})]});await page.mount()
    page.contextConfirmed.value=true;await page.refreshDraftContext();assert.equal(page.contextRefreshAllowed.value,false)
    assert.equal(page.calls.some(call=>call[0]==='refresh-context'),false)
  }
})

test('positive controlled-close context refresh keeps exact public fields and session history read-only before a fresh review',async()=>{
  const frozen=record({target_revision:1,stage:'closed_not_submitted',reviewed:false,can_edit:false,can_review:false,can_open:false,can_refresh_context:true,read_only:true,session_count:2})
  const page=harness({initial:[frozen]});await page.mount()
  assert.equal(page.contextRefreshAllowed.value,true);assert.equal(page.contextRefreshEditable.value,false);assert.equal(page.editorDisabled.value,true)
  assert.equal(page.previewFields.value.name,frozen.fields.name)
  page.form.rate='这份本机编辑不能代替原已打开申请';page.contextConfirmed.value=true
  await page.refreshDraftContext()
  assert.deepEqual(page.calls.find(call=>call[0]==='refresh-context')[3].fields,frozen.fields)
  assert.equal(page.record.value.session_count,2);assert.equal(page.record.value.read_only,true);assert.equal(page.record.value.can_edit,false)
  assert.equal(page.record.value.reviewed,false);assert.equal(page.record.value.open_nonce,null);assert.equal(page.canOpen.value,false)
  assert.equal(page.form.rate,'这份本机编辑不能代替原已打开申请');assert.equal(page.dirty.value,true)
  await page.discardLocalEdits();page.confirmed.value=true;await page.reviewDraft();assert.equal(page.canOpen.value,true)
  assert.deepEqual(page.record.value.fields,frozen.fields);assert.equal(page.record.value.session_count,2)
})

test('a public close stage alone cannot authorize context refresh or acknowledge a reset session history',async()=>{
  const locked=record({target_revision:1,stage:'closed_not_submitted',can_edit:false,can_review:false,can_refresh_context:false,read_only:true,session_count:1})
  const page=harness({initial:[locked]});await page.mount();page.contextConfirmed.value=true;assert.equal(page.canRefreshContext.value,false)
  const invalid=harness({initial:[{...locked,can_refresh_context:true}],overrides:{refreshContext:async()=>({data:record({revision:2,stage:'draft',reviewed:false,session_count:0,can_edit:true,read_only:false})})}})
  await invalid.mount();invalid.contextConfirmed.value=true;await invalid.refreshDraftContext()
  assert.equal(invalid.uncertain.value,true);assert.equal(invalid.record.value.session_count,1);assert.equal(invalid.record.value.stage,'closed_not_submitted')
})

test('pending, unknown and external receipts never authorize a second application even with contradictory flags',async()=>{
  for(const stage of ['submitted_pending_review','submission_unknown','accepted','rejected','window_closed']){
    const page=harness({initial:[record({stage,reviewed:true,open_nonce:'old-nonce',can_edit:true,can_review:true,can_open:true,read_only:true,can_inspect:true})]})
    await page.mount();page.confirmed.value=true;page.fill();await page.saveDraft();await page.reviewDraft();await page.openWindow()
    assert.equal(page.canSave.value,false);assert.equal(page.canReview.value,false);assert.equal(page.canOpen.value,false)
    assert.equal(page.calls.filter(call=>['save','review','open'].includes(call[0])).length,0)
    await page.inspectRecord();assert.equal(page.calls.filter(call=>call[0]==='inspect').length,1)
  }
})

test('only a server-confirmed closed-not-submitted record can review the same frozen fields again',async()=>{
  const page=harness({initial:[record({stage:'closed_not_submitted',can_edit:false,can_review:true,can_open:false,read_only:true})]});await page.mount()
  page.confirmed.value=true;assert.equal(page.canSave.value,false);assert.equal(page.canReview.value,true)
  await page.reviewDraft();assert.equal(page.calls.filter(call=>call[0]==='review').length,1)
  assert.equal(page.calls.some(call=>call[0]==='save'),false);assert.equal(page.record.value.read_only,true)
  assert.equal(page.canOpen.value,true);await page.openWindow();assert.equal(page.calls.filter(call=>call[0]==='open').length,1)
})

test('cross-cycle history remains visible but cannot be edited, reviewed, or opened again',async()=>{
  const page=harness({initial:[record({cycle_id:'prior-cycle',can_inspect:true})]});await page.mount()
  page.confirmed.value=true;page.fill();await page.saveDraft();await page.reviewDraft();await page.openWindow()
  assert.equal(page.historyRecord.value,true);assert.equal(page.calls.filter(call=>['save','review','open'].includes(call[0])).length,0)
  await page.inspectRecord();assert.equal(page.calls.at(-2)[0]==='inspect'||page.calls.at(-1)[0]==='inspect',true)
})

test('an existing project application on another target is shown only as history and cannot create a duplicate',async()=>{
  const page=harness({initial:[record({target_id:'original-target',stage:'submitted_pending_review',read_only:true,can_inspect:true,receipt:{intake_uid:'lead-original'}})]});await page.mount()
  assert.equal(page.record.value.target_id,'original-target');assert.equal(page.historyRecord.value,true);assert.equal(page.currentRecord.value,false)
  page.fill();page.confirmed.value=true;await page.saveDraft();await page.reviewDraft();await page.openWindow();await page.saveExternalReceipt()
  assert.equal(page.calls.some(call=>['save','review','open','external'].includes(call[0])),false)
  await page.inspectRecord();assert.deepEqual(page.calls.find(call=>call[0]==='inspect'),['inspect','cycle-a','application-a'])
})

test('external receipts preserve actual minute precision, explicit owner provenance, and never open the publisher',async()=>{
  const page=harness({initial:[record()]});await page.mount()
  Object.assign(page.external,{intakeUid:'lead-existing-1',observedAt:'2026-11-02T02:31',precision:'minute',note:'在平台原页面核对到相同服务网址的接收回执。',confirmed:true})
  await page.saveExternalReceipt()
  const payload=page.calls.find(call=>call[0]==='external')[3]
  assert.equal(payload.observed_at,'2026-11-02T02:31+08:00');assert.equal(payload.observed_at_precision,'minute')
  assert.equal(Object.hasOwn(payload,'evidence_sha256'),false)
  assert.equal(page.record.value.evidence_level,'owner_reported');assert.equal(page.record.value.source,'operator_assisted')
  assert.equal(page.record.value.stage,'submitted_pending_review');assert.equal(page.canOpen.value,false)
  assert.equal(page.evidenceLabel.value,'用户登记，未独立核验');assert.equal(page.calls.some(call=>call[0]==='open'),false)
  assert.doesNotMatch(page.receiptTime(page.record.value.receipt),/:00（/)
})

test('invalid or imprecise dates, missing notes, and mismatched receipt acknowledgements cannot appear saved',async()=>{
  assert.equal(helpers.directoryReceiptTime('2026-11-02T02:31:17','second'),'2026-11-02T02:31:17+08:00')
  assert.equal(helpers.directoryReceiptTime('2026-11-02T02:31:00','second'),'2026-11-02T02:31:00+08:00')
  for(const [value,precision] of [['2026-02-29T02:31','minute'],['2026-11-02T02:31','second'],['2026-11-02T02:31:00','minute']])assert.equal(helpers.directoryReceiptTime(value,precision),'')
  const page=harness({initial:[record()],overrides:{externalReceipt:async()=>({data:record({stage:'submitted_pending_review',evidence_level:'owner_reported',receipt:{intake_uid:'wrong'}})})}})
  await page.mount();Object.assign(page.external,{intakeUid:'lead-1',observedAt:'2026-11-02T02:31',note:'',confirmed:true})
  assert.equal(page.canSaveReceipt.value,false);page.external.note='平台原回执声明';page.external.confirmed=true;await page.saveExternalReceipt()
  assert.equal(page.record.value.stage,'draft');assert.equal(page.uncertain.value,true)
})

test('editing a receipt after confirmation requires a new confirmation and cannot silently change its provenance',async()=>{
  const page=harness({initial:[record()]});await page.mount()
  Object.assign(page.external,{intakeUid:'lead-1',observedAt:'2026-11-02T02:31:00',precision:'second',note:'平台原页面接收回执',confirmed:true})
  assert.equal(page.canSaveReceipt.value,true)
  for(const [key,value] of [['intakeUid','lead-2'],['observedAt','2026-11-02T02:31:01'],['note','平台原页面已核对的回执说明']]){
    page.external[key]=value;assert.equal(page.external.confirmed,false);await page.saveExternalReceipt()
    assert.equal(page.calls.some(call=>call[0]==='external'),false);page.external.confirmed=true
  }
})

test('duplicate save/review/native clicks stay latched until their acknowledgement',async()=>{
  let saves=0
  const saving=deferred(),page=harness({overrides:{save:()=>{saves++;return saving.promise}}});await page.mount();page.fill()
  const pending=page.saveDraft();await page.saveDraft();assert.equal(page.saving.value,true);assert.equal(saves,1)
  saving.resolve({data:record()});await pending;assert.equal(page.record.value.id,'application-a')
  const review=deferred(),reviewed=record({revision:2,stage:'reviewed',reviewed:true,can_review:false,can_open:true,open_nonce:'open-nonce-a'})
  page.api.review=()=>{page.calls.push(['review-deferred']);return review.promise}
  page.confirmed.value=true;const checking=page.reviewDraft();await page.reviewDraft()
  assert.equal(page.calls.filter(call=>call[0]==='review-deferred').length,1)
  page.setServer(overview([reviewed]));review.resolve({data:reviewed});await checking
  const native=deferred();page.native.open_directory_application=()=>{page.calls.push(['open-deferred']);return native.promise}
  const opening=page.openWindow();await page.openWindow()
  assert.equal(page.calls.filter(call=>call[0]==='open-deferred').length,1)
  native.resolve({id:'native-app-a',status:'running',kind:'directory_application',cycle_id:'cycle-a',target_id:'target-a',directory_application_id:'application-a'});await opening
})

test('old reads and saves cannot apply after switching A to B to A or unmounting',async()=>{
  const response=deferred(),page=harness({overrides:{list:()=>response.promise}})
  page.openPanel();const oldCycle=page.props.cycle
  page.props.cycle=cycle({id:'cycle-b',project_id:6});page.props.target=page.props.cycle.destinations[0]
  page.props.cycle=oldCycle;page.props.target=oldCycle.destinations[0]
  page.stop();response.resolve({data:overview([record()])});await settle()
  assert.equal(page.record.value,null)
  const saving=deferred(),second=harness({overrides:{save:()=>saving.promise}});await second.mount();second.fill();const pending=second.saveDraft()
  second.props.cycle=cycle({id:'cycle-b',project_id:6});second.props.target=second.props.cycle.destinations[0]
  saving.resolve({data:record()});await pending;await settle()
  assert.notEqual(second.record.value?.project_id,5)
})

test('a foreign backend record, wrong native task or private error cannot replace a bound application',async()=>{
  const page=harness({initial:[record({reviewed:true,stage:'reviewed',can_open:true,open_nonce:'nonce-a'})],nativeOverrides:{
    open_directory_application:async()=>({id:'other-job',status:'running',kind:'destination_fill',cycle_id:'cycle-a',directory_application_id:'application-a'})}})
  await page.mount();page.confirmed.value=true;await page.openWindow();assert.equal(page.uncertain.value,true)
  assert.equal(page.record.value.stage,'reviewed')
  page.setServer(overview([record({project_id:99})]));await page.refresh();assert.equal(page.record.value.project_id,5)
  assert.doesNotMatch(helpers.directorySafeMessage(new Error('Playwright C:\\private\\cookies.db traceback snapshot nonce')),/Playwright|private|nonce|cookies/)
})

test('same-window checking uses only the stored native task and cannot create or resubmit an application',async()=>{
  const page=harness({initial:[record({stage:'awaiting_human_confirmation',window_open:true,native_task_id:'native-app-a',can_edit:false,can_review:false,read_only:true})]});await page.mount()
  page.props.busy=true;assert.equal(page.canCheck.value,false)
  page.props.task={id:'native-app-a',status:'running',kind:'directory_application',cycle_id:'cycle-a',target_id:'target-a',directory_application_id:'application-a'}
  assert.equal(page.canCheck.value,true);await page.checkWindow()
  assert.deepEqual(page.calls.find(call=>call[0]==='check'),['check','native-app-a'])
  for(const change of [{id:'other-task'},{kind:'destination_fill'},{cycle_id:'other-cycle'},{directory_application_id:'other-application'}]){
    page.props.task={id:'native-app-a',status:'running',kind:'directory_application',cycle_id:'cycle-a',target_id:'target-a',directory_application_id:'application-a',...change}
    assert.equal(page.canCheck.value,false)
  }
  assert.equal(page.calls.some(call=>['save','review','open','external'].includes(call[0])),false)
})

test('normal verification and manual field review have precise guidance without changing submission permissions',async()=>{
  for(const stage of ['awaiting_human_verification','needs_manual_review']){
    const page=harness({initial:[record({stage,window_open:true,native_task_id:'native-app-a',can_edit:false,can_review:false,can_open:false,read_only:true,session_count:1})]});await page.mount()
    page.props.task={id:'native-app-a',status:'running',kind:'directory_application',cycle_id:'cycle-a',target_id:'target-a',directory_application_id:'application-a'};page.props.busy=true
    assert.notEqual(helpers.directoryStage(page.record.value),'状态待核对');assert.match(page.stageDetail.value,/原平台窗口|原窗口/)
    if(stage==='awaiting_human_verification')assert.match(page.stageDetail.value,/正常完成当前登录或安全验证.*检查当前窗口/)
    else assert.match(page.stageDetail.value,/尚未完整核对.*不能确认提交结果.*原申请和回执/)
    page.confirmed.value=true;assert.equal(page.canReview.value,false);assert.equal(page.canOpen.value,false);assert.equal(page.canSave.value,false)
    assert.equal(page.canCheck.value,true);assert.equal(page.canClose.value,true)
    assert.equal(page.calls.some(call=>['open','review','save','external'].includes(call[0])),false)
  }
})

test('controlled close signals only the owned session once and waits for a server closure record before allowing review',async()=>{
  const active=record({stage:'awaiting_human_confirmation',window_open:true,native_task_id:'native-app-a',can_edit:false,can_review:false,read_only:true,session_count:1})
  const page=harness({initial:[active]});await page.mount();page.props.busy=true
  page.props.task={id:'native-app-a',status:'running',kind:'directory_application',cycle_id:'cycle-a',target_id:'target-a',directory_application_id:'application-a'}
  assert.equal(page.canClose.value,true);await page.closeWindow();await page.closeWindow()
  assert.deepEqual(page.calls.filter(call=>call[0]==='close'),[['close','native-app-a']])
  assert.equal(page.closeRequested.value,true);assert.equal(page.record.value.stage,'awaiting_human_confirmation')
  page.confirmed.value=true;assert.equal(page.canReview.value,false);assert.equal(page.canOpen.value,false)
  page.setServer(overview([{...active,stage:'closed_not_submitted',window_open:false,reviewed:false,can_review:true}]))
  page.props.task={...page.props.task,status:'completed'};page.props.busy=false;await page.refresh()
  assert.equal(page.record.value.stage,'closed_not_submitted');assert.equal(page.canReview.value,false)
  page.confirmed.value=true;assert.equal(page.canReview.value,true);assert.equal(page.calls.some(call=>['open','review','save','external'].includes(call[0])),false)
})

test('controlled close requires exact ownership; an inspection closes only its live read-only task',async()=>{
  const pending=record({stage:'submitted_pending_review',receipt:{intake_uid:'lead-original'},can_edit:false,can_review:false,read_only:true,window_open:true,native_task_id:'native-app-a'})
  const page=harness({initial:[pending]});await page.mount();page.props.busy=true
  const owned={id:'native-app-a',status:'running',kind:'directory_application',cycle_id:'cycle-a',target_id:'target-a',directory_application_id:'application-a'}
  for(const changes of [{id:'other-task'},{id:undefined},{kind:'destination_fill'},{cycle_id:'other-cycle'},{target_id:'other-target'},{directory_application_id:'other-app'},{status:'completed'}]){
    page.props.task={...owned,...changes};assert.equal(page.canClose.value,false);await page.closeWindow()
  }
  assert.equal(page.calls.some(call=>call[0]==='close'),false)
  page.setServer(overview([{...pending,window_open:false}]));await page.refresh()
  page.props.task={...owned,id:'native-inspect-a',kind:'directory_application_inspect',progress:{directory_application:{window_open:false}}}
  assert.equal(page.canClose.value,false)
  page.props.task.progress.directory_application.window_open=true
  assert.equal(page.canClose.value,true);await page.closeWindow()
  assert.deepEqual(page.calls.find(call=>call[0]==='close'),['close','native-inspect-a'])
  assert.equal(page.record.value.stage,'submitted_pending_review');assert.equal(page.record.value.receipt.intake_uid,'lead-original')
  assert.equal(page.canReview.value,false);assert.equal(page.canOpen.value,false)
})

test('controlled close remains available for the same owned stale revision, while another target or customer stays isolated',async()=>{
  const stale=record({target_revision:1,stage:'awaiting_human_confirmation',window_open:true,native_task_id:'native-app-a',can_edit:false,can_review:false,can_open:false,read_only:true,session_count:1})
  const page=harness({initial:[stale]});await page.mount();page.props.busy=true
  page.props.task={id:'native-app-a',status:'running',kind:'directory_application',cycle_id:'cycle-a',target_id:'target-a',directory_application_id:'application-a'}
  assert.equal(page.currentRecord.value,false);assert.equal(page.canClose.value,true)
  page.confirmed.value=true;assert.equal(page.canReview.value,false);assert.equal(page.canOpen.value,false);assert.equal(page.canCheck.value,false)
  await page.closeWindow();assert.deepEqual(page.calls.find(call=>call[0]==='close'),['close','native-app-a'])
  assert.equal(page.record.value.stage,'awaiting_human_confirmation')
  const secondTarget=target({id:'target-b'});page.props.cycle=cycle({destinations:[secondTarget]});page.props.target=secondTarget;await settle()
  assert.equal(page.record.value?.target_id,'target-a');assert.equal(page.canClose.value,false)
  page.props.cycle=cycle({id:'other-cycle',project_id:6});page.props.target=page.props.cycle.destinations[0];await settle()
  assert.equal(page.canClose.value,false);assert.equal(page.calls.filter(call=>call[0]==='close').length,1)
})

test('controlled close stays latched and a wrong or late acknowledgement cannot manufacture a no-submit result',async()=>{
  const ack=deferred(),active=record({stage:'awaiting_human_confirmation',window_open:true,native_task_id:'native-app-a',can_edit:false,can_review:false,read_only:true})
  const page=harness({initial:[active],nativeOverrides:{close_directory_application_session:id=>{page.calls.push(['close-deferred',id]);return ack.promise}}});await page.mount()
  page.props.task={id:'native-app-a',status:'running',kind:'directory_application',cycle_id:'cycle-a',target_id:'target-a',directory_application_id:'application-a'}
  const first=page.closeWindow();await page.closeWindow();assert.equal(page.closing.value,true)
  assert.equal(page.calls.filter(call=>call[0]==='close-deferred').length,1)
  ack.resolve({id:'wrong-task',status:'running',close_requested:true});await first
  assert.equal(page.uncertain.value,true);assert.equal(page.closeRequested.value,false);assert.equal(page.record.value.stage,'awaiting_human_confirmation')
  const late=deferred(),other=harness({initial:[active],nativeOverrides:{close_directory_application_session:()=>late.promise}});await other.mount()
  other.props.task={...page.props.task};const old=other.closeWindow()
  other.props.cycle=cycle({id:'other-cycle',project_id:6});other.props.target=other.props.cycle.destinations[0]
  other.setServer(overview([]));await settle();late.resolve({id:'native-app-a',status:'running',close_requested:true});await old
  assert.equal(other.closeRequestedTask.value,'');assert.notEqual(other.record.value?.project_id,5)
})

test('directory task banners offer the owned close path while other tasks keep their existing Stop control',async()=>{
  const journey=fs.readFileSync(new URL('../src/views/GeoJourney.vue',import.meta.url),'utf8'),ast=parse(journey).descriptor.template.ast
  const find=node=>node?.type===1&&node.props?.some(prop=>prop.name==='class'&&prop.value?.content==='task-progress')?node:(node?.children||[]).map(find).find(Boolean)
  const progress=find(ast);assert.ok(progress)
  const render=Vue.compile(progress.loc.source)
  const output=async kind=>{
    const app=Vue.createSSRApp({setup:()=>({nativeBusy:true,task:{kind},directoryTask:helpers.directoryTaskView({kind}),isDestinationTask:()=>false,stopTask:()=>{}}),render})
    for(const name of ['el-icon','Loading','el-button'])app.component(name,{setup:(_,{slots})=>()=>Vue.h(name==='el-button'?'button':'span',slots.default?.())})
    return renderToString(app)
  }
  for(const kind of ['directory_application','directory_application_inspect']){
    const html=await output(kind);assert.doesNotMatch(html,/<button/);assert.match(html,/原目录申请卡“结束当前窗口”/);assert.match(html,/不代表未提交/)
  }
  assert.match(await output('run'),/<button[^>]*>停止<\/button>/)
})

test('directory native task guidance does not reuse destination or Doubao instructions',()=>{
  assert.equal(helpers.directoryTaskView({kind:'destination_fill'}),null);assert.equal(helpers.directoryTaskView({kind:'run'}),null)
  const opening=helpers.directoryTaskView({kind:'directory_application'}),inspect=helpers.directoryTaskView({kind:'directory_application_inspect'})
  assert.match(opening.title,/目录申请/);assert.match(opening.detail,/同一平台窗口/);assert.match(inspect.detail,/只读/)
  assert.doesNotMatch(opening.detail+inspect.detail,/豆包|写稿|检查并继续/)
  const journey=fs.readFileSync(new URL('../src/views/GeoJourney.vue',import.meta.url),'utf8'),journeyDescriptor=parse(journey).descriptor
  compileScript(journeyDescriptor,{id:'directory-journey-test'})
  assert.deepEqual(compileTemplate({source:journeyDescriptor.template.content,filename:'GeoJourney.vue',id:'directory-journey-test'}).errors,[])
  assert.match(journey,/directoryTask\s*\? directoryTask\.detail/)
})

test('actual API搜 prefill task banners in both views defer to card capability and never urge final confirmation',async()=>{
  const task={id:'native-apisou-prefill',kind:'directory_application',status:'running',cycle_id:'cycle-a',
    directory_application_id:'apisou-application',progress:{directory_application:{stage:'awaiting_human_confirmation',window_open:true}}}
  const directoryTask=helpers.directoryTaskView(task)
  assert.match(directoryTask.detail,/是否支持最终提交，以原卡显示能力为准/)
  assert.match(directoryTask.detail,/仅预填窗口暂不能提交/)
  assert.doesNotMatch(directoryTask.detail,/核对公开内容并最终确认|正常完成验证/)
  const page=journeyHarness()
  page.cycle.value.destinations=[{...page.configured,config:{...page.configured.config,source_scope:helpers.apisouHome}}]
  for(const step of [2,3]){page.step.value=step;assert.match(page.actionHint.value,/此窗口仅预填核对，暂不能提交/);assert.doesNotMatch(page.actionHint.value,/由你在平台最终确认/)}
  const journeyDescriptor=parse(fs.readFileSync(new URL('../src/views/GeoJourney.vue',import.meta.url),'utf8')).descriptor
  const find=(node,predicate)=>node?.type===1&&predicate(node)?node:(node?.children||[]).map(child=>find(child,predicate)).find(Boolean)
  const sections=[find(journeyDescriptor.template.ast,node=>node.props?.some(prop=>prop.name==='class'&&prop.value?.content==='task-progress')),
    find(contentDescriptor.template.ast,node=>node.tag==='el-card'&&node.loc.source.includes('data-testid="content-directory-task"'))]
  for(const section of sections){
    assert.ok(section)
    const render=Vue.compile(section.loc.source)
    const app=Vue.createSSRApp({render,setup:()=>({task,directoryTask,nativeAvailable:true,nativeBusy:true,busy:true,
      isDestinationTask:()=>false,stopTask:()=>{},writingContextPending:false,externalCycle:false,cycle:null})})
    for(const name of ['el-icon','Loading','el-button','el-card','el-option','el-select','el-input','DoubaoPreparation','DoubaoTaskRecovery'])app.component(name,{setup:(_,{slots})=>()=>Vue.h(name==='el-button'?'button':'div',slots.default?.())})
    const html=await renderToString(app)
    assert.match(html,/仅预填窗口暂不能提交/);assert.doesNotMatch(html,/核对公开内容并最终确认/)
  }
  const heading=find(journeyDescriptor.template.ast,node=>node.props?.some(prop=>prop.name==='class'&&prop.value?.content==='section-heading')&&node.loc.source.includes('本渠道使用目录资料'))
  assert.ok(heading)
  const headerApp=Vue.createSSRApp({render:Vue.compile(heading.loc.source),setup:()=>({step:2,directoryTarget:apisouTarget(),isApiznzDirectory:helpers.isApiznzDirectory,isApisouDirectory:helpers.isApisouDirectory})})
  const header=await renderToString(headerApp)
  assert.match(header,/不要求价格/);assert.match(header,/此窗口仅预填核对，暂不能提交/)
  assert.doesNotMatch(header,/对客售价|由你在平台最终确认/)
})

test('the target card retains first-time purpose review and separates directory actions from article controls',async()=>{
  const parent=fs.readFileSync(new URL('../src/components/GeoDestinations.vue',import.meta.url),'utf8'),parentDescriptor=parse(parent).descriptor
  const render=Vue.compile(parentDescriptor.template.content)
  const output=async(sourceScope,allowed)=>{
    const configured=target({config:{...target().config,source_scope:sourceScope},state:'preparing',blocked_reason:''})
    const context={cycle:cycle({opportunities:[],destinations:[configured]}),task:{},busy:false,nativeAvailable:true,show:false,showEvents:false,form:{},editing:null,eventRows:[],
      ...helpers,states:{},surfaces:{},destinationEnabled:()=>true,destinationCanPrepare:()=>allowed,inactiveDestinationMessage:'',configure:()=>{},selectTarget:()=>{},events:()=>{},review:()=>{}}
    const app=Vue.createSSRApp({setup:()=>context,render})
    for(const name of [...new Set(parent.match(/el-[a-z-]+/g))])app.component(name,{props:['modelValue','disabled'],setup:(props,{slots})=>()=>name==='el-dialog'||name==='el-table'?null:Vue.h(name==='el-button'?'button':'div',{disabled:props.disabled},slots.default?.())})
    for(const name of ['GeoDirectoryApplication','GeoExternalDirectoryReceipt','DestinationUseReview','DestinationSessionCard','DestinationUseFields'])app.component(name,{setup:()=>()=>Vue.h('div',{'data-component':name},name)})
    return renderToString(app)
  }
  const initial=await output('https://grokcode.cn/api-transit',false)
  assert.match(initial,/data-component="DestinationUseReview"/);assert.match(initial,/data-component="GeoDirectoryApplication"/)
  for(const html of [initial,await output('https://grokcode.cn/api-transit',true)]){
    assert.doesNotMatch(html,/data-component="DestinationSessionCard"|选择此平台写稿|检查后恢复/);assert.match(html,/查看旧连接记录/)
  }
  const external=await output(helpers.apiznzHome,true)
  assert.match(external,/data-component="GeoExternalDirectoryReceipt"/)
  assert.doesNotMatch(external,/data-component="GeoDirectoryApplication"|data-component="DestinationSessionCard"|选择此平台写稿|检查后恢复/)
  const article=await output('https://article.example.org/',true)
  assert.match(article,/data-component="DestinationSessionCard"/);assert.match(article,/选择此平台写稿/);assert.match(article,/检查后恢复/)
  assert.doesNotMatch(article,/data-component="GeoDirectoryApplication"/)
})

test('model choices come from the bound backend overview and unavailable desktop bridges stay disabled',async()=>{
  const page=harness();page.setServer(overview([],{model_options:['GPT']}));await page.mount();page.fill({models:['GPT']})
  assert.equal(page.canSave.value,true);page.form.models=['GLM'];assert.equal(page.canSave.value,false)
  const old=harness({initial:[record({stage:'reviewed',reviewed:true,can_open:true,open_nonce:'nonce-a'})],nativeOverrides:{open_directory_application:undefined}})
  await old.mount();old.confirmed.value=true;assert.equal(old.canOpen.value,false)
})

test('new and existing target components compile with readable narrow layouts and no upload or machine details',()=>{
  assert.deepEqual(compileTemplate({source:descriptor.template.content,filename:filename.pathname,id:'directory-application-test'}).errors,[])
  for(const style of descriptor.styles)assert.deepEqual(compileStyle({source:style.content,filename:filename.pathname,id:'directory-application-test',scoped:true}).errors,[])
  const renderedText=descriptor.template.content.replace(/<[^>]*>/g,'').replace(/\{\{[\s\S]*?\}\}/g,'')
  assert.doesNotMatch(renderedText,/review_sha|context_sha|nonce|fingerprint|Traceback|CSS|schema/)
  assert.doesNotMatch(descriptor.template.content,/<el-upload/)
  assert.match(descriptor.template.content,/<el-form-item v-if="isApisou" label="实际凭据的 SHA-256"><input type="file"/)
  assert.match(descriptor.styles[0].content,/prefers-reduced-motion/)
  const parent=fs.readFileSync(new URL('../src/components/GeoDestinations.vue',import.meta.url),'utf8')
  assert.match(parent,/<GeoDirectoryApplication v-if="isGrokCodeDirectory\(target\) \|\| isApisouDirectory\(target\)"/)
  assert.match(parent,/<DestinationSessionCard/);assert.match(parent,/@click="review\(target\)"/)
})

test('API指南针 target routing and external receipt workflow stay local and scoped',async()=>{
  const apiznz=target({id:'target-external',key:'target_target-external',config:{name:'API指南针',source_scope:helpers.apiznzHome}})
  const current=cycle({destinations:[target(),apiznz]})
  assert.equal(helpers.directoryTargetForChannel(current,apiznz.key),apiznz)
  assert.equal(helpers.directoryTargetForChannel(current,'GrokCode'),current.destinations[0])
  assert.equal(helpers.isApiznzDirectory({...apiznz,config:{source_scope:'https://www.apiznz.com.evil.example/'}}),false)
  assert.equal(helpers.isApiznzDirectory({...apiznz,config:{source_scope:'https://www.apiznz.com/site/lists?kw=example'}}),false)
  const {descriptor}=parse(fs.readFileSync(new URL('../src/components/GeoExternalDirectoryReceipt.vue',import.meta.url),'utf8'))
  const script=compileScript(descriptor,{id:'directory-receipt-only'})
  assert.deepEqual(compileTemplate({source:descriptor.template.content,filename:'GeoExternalDirectoryReceipt.vue',id:'directory-receipt-only'}).errors,[])
  let source=descriptor.scriptSetup.content
  for(const node of script.scriptSetupAst.filter(node=>node.type==='ImportDeclaration').reverse())source=source.slice(0,node.start)+source.slice(node.end)
  const props=Vue.reactive({cycle:current,target:apiznz,busy:false}),calls=[]
  let stored=null
  const api={
    list:async (...args)=>{calls.push(['list',...args]);return {data:overview(stored?[stored]:[],{adapter:helpers.apiznzAdapter,entry_url:helpers.apiznzEntry})}},
    save:async(cycleId,payload)=>{calls.push(['save',cycleId,payload]);stored=record({adapter:helpers.apiznzAdapter,target_id:apiznz.id,fields:payload.fields});return {data:stored}},
    review:async(cycleId,id,payload)=>{calls.push(['review',cycleId,id,payload]);stored={...stored,revision:2,stage:'reviewed',reviewed:true};return {data:stored}},
    externalReceipt:async(cycleId,id,payload)=>{
      calls.push(['receipt',cycleId,id,payload])
      const {observed_at,observed_at_precision,note,evidence_sha256}=payload
      stored={...stored,stage:payload.status,can_record_external_receipt:false,evidence_level:'owner_reported',geo_effect_evidence:false,
        receipt:{intake_uid:null,owner_reported:true,public_listing_verified:false,observed_at,observed_at_precision,note,evidence_sha256}}
      return {data:stored}
    }
  }
  const scope=Vue.effectScope(),deps={...helpers,computed:Vue.computed,reactive:Vue.reactive,ref:Vue.ref,watch:Vue.watch,
    onUnmounted:()=>{},defineProps:()=>props,defineExpose:()=>{},geoDirectoryApplicationsApi:api}
  const page=scope.run(()=>new Function(...Object.keys(deps),source+';return {form,receipt,fieldsConfirmed,canReview,canSaveReceipt,reviewFields,readEvidence,saveReceipt,refresh,record};')(...Object.values(deps)))
  try{
    await settle()
    Object.assign(page.form,{intro:'统一接入与用量管理',api_base:'https://example.org/v1?token=secret'})
    page.fieldsConfirmed.value=true
    assert.equal(page.canReview.value,false)
    page.form.api_base='https://example.org/v1'
    assert.equal(page.fieldsConfirmed.value,false)
    page.fieldsConfirmed.value=true
    await page.reviewFields()
    assert.equal(page.record.value.stage,'reviewed')
    Object.assign(page.receipt,{observedAt:'2026-11-04T15:57',note:'提交成功',confirmed:true})
    assert.equal(page.canSaveReceipt.value,false)
    const file=new Blob(['abc'])
    await page.readEvidence({target:{files:[file],value:'local-file'}})
    assert.equal(page.receipt.digest,'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad')
    assert.equal(page.receipt.confirmed,false)
    page.receipt.confirmed=true
    await Promise.all([page.saveReceipt(),page.saveReceipt()])
    assert.equal(page.record.value.stage,'submitted_pending_review')
    const receipts=calls.filter(call=>call[0]==='receipt')
    assert.equal(receipts.length,1)
    assert.deepEqual(receipts[0].slice(1),['cycle-a','application-a',{expected_revision:2,expected_review_sha256:reviewHash,confirmed:true,
      status:'submitted_pending_review',observed_at:'2026-11-04T15:57+08:00',observed_at_precision:'minute',note:'提交成功',
      evidence_sha256:'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad'}])
    await page.refresh()
    assert.equal(page.record.value.receipt.public_listing_verified,false)
    assert.equal(page.record.value.geo_effect_evidence,false)
    props.cycle=cycle({id:'other-cycle',project_id:6,destinations:[apiznz]})
    await settle()
    assert.equal(page.record.value,null)
  }finally{scope.stop()}
})

const apisouTarget=()=>target({config:{name:'API搜',source_scope:helpers.apisouHome}})
const apisouValue=changes=>helpers.apisouFields({name:'示例协作服务',url:'https://example.org/',...changes})
const apisouOverview=applications=>overview(applications,{adapter:helpers.apisouAdapter,entry_url:helpers.apisouEntry,
  field_limits:helpers.apisouLimits,field_required:['name','url'],prefill_only:true,native_submission_supported:false})
function apisouHarness(){const item=apisouTarget(),page=harness({value:cycle({destinations:[item]})});page.setServer(apisouOverview([]));return page}

test('API搜 is a separate exact-origin directory with optional model defaults and no article/price requirement',async()=>{
  assert.equal(helpers.directoryAdapterForTarget(apisouTarget()),helpers.apisouAdapter)
  for(const url of ['https://apisou.com/','https://www.apisou.com.evil.example/','https://www.apisou.com/submit','https://www.apisou.com/?x=1'])assert.equal(helpers.isApisouDirectory(target({config:{source_scope:url}})),false)
  const value=apisouValue()
  assert.equal(helpers.directoryFieldsIssue(value,helpers.apisouLimits,[],helpers.apisouAdapter),'')
  assert.deepEqual(value.model_lines,[]);assert.deepEqual(value.modalities,[]);assert.equal(value.contact,'');assert.equal(value.type,'商业')
  assert.equal(Object.hasOwn(value,'rate'),false)
  assert.notEqual(helpers.directoryFieldsIssue({...value,contact:'private@example.org'},helpers.apisouLimits,[],helpers.apisouAdapter),'')
  const page=apisouHarness();await page.mount();Object.assign(page.form,value)
  assert.equal(page.canSave.value,true);assert.equal(page.isApisou.value,true)
  assert.match(page.stageDetail.value,/刷新|保存/)
  await page.saveDraft();assert.equal(page.record.value.adapter,helpers.apisouAdapter)
  assert.deepEqual(page.calls.find(row=>row[0]==='save')[2].fields,value)
  page.confirmed.value=true;await page.reviewDraft();page.confirmed.value=true
  await page.copyDirectoryValue('fields')
  assert.match(page.calls.find(row=>row[0]==='clipboard')[1],/公开服务网址：https:\/\/example.org\//)
  assert.match(page.copyNotice.value,/尚未发送申请/)
  assert.equal(page.canOpen.value,true)
  await page.openWindow();assert.equal(page.calls.filter(row=>row[0]==='open').length,1)
  assert.match(page.stageDetail.value,/不放行最终申请/)
  assert.equal(page.calls.filter(row=>row[0]==='external').length,0)
})

test('API搜 existing receipt requires real evidence SHA, never adds UID or native/GEO success',async()=>{
  const page=apisouHarness();await page.mount();Object.assign(page.form,apisouValue());await page.saveDraft()
  page.confirmed.value=true;await page.reviewDraft()
  Object.assign(page.external,{observedAt:'2026-01-01T10:21',precision:'minute',note:'本人已在正常平台看到本次申请成功提示',confirmed:true})
  assert.equal(page.canSaveReceipt.value,false)
  page.external.evidenceSha='f'.repeat(64);page.external.confirmed=true;assert.equal(page.canSaveReceipt.value,true)
  await page.saveExternalReceipt()
  const payload=page.calls.find(row=>row[0]==='external')[3]
  assert.equal(Object.hasOwn(payload,'intake_uid'),false);assert.equal(payload.evidence_sha256,'f'.repeat(64))
  assert.equal(page.record.value.receipt.intake_uid,null);assert.equal(page.record.value.evidence_level,'owner_reported')
  assert.equal(page.record.value.receipt.public_listing_verified,false);assert.equal(page.record.value.geo_effect_evidence,false)
  assert.equal(page.canOpen.value,false);assert.equal(page.calls.filter(row=>row[0]==='open').length,0)
})

test('changing a target adapter cannot retain another directory schema or local authorization',async()=>{
  const page=apisouHarness();await page.mount();Object.assign(page.form,apisouValue({note:'Keep only API搜 local draft'}))
  const grok=target();page.setServer(overview([]));page.props.cycle=cycle({destinations:[grok]});page.props.target=grok;await settle()
  assert.equal(page.isApisou.value,false);assert.equal(Object.hasOwn(page.form,'model_lines'),false)
  assert.equal(page.form.notes,'');assert.equal(page.confirmed.value,false)
  const before=page.calls.filter(row=>row[0]==='clipboard').length
  await page.copyDirectoryValue('fields');assert.equal(page.calls.filter(row=>row[0]==='clipboard').length,before)
})

test('API搜 evidence file is hashed locally and its bytes/name/path never enter a receipt request',async()=>{
  const page=apisouHarness();await page.mount();Object.assign(page.form,apisouValue());await page.saveDraft()
  page.confirmed.value=true;await page.reviewDraft()
  const bytes=new TextEncoder().encode('synthetic already-existing receipt')
  const expected=Array.from(new Uint8Array(await globalThis.crypto.subtle.digest('SHA-256',bytes)),byte=>byte.toString(16).padStart(2,'0')).join('')
  const event={target:{value:'local-file',files:[{name:'not-uploaded.txt',size:bytes.length,arrayBuffer:async()=>bytes.buffer}]}}
  await page.readReceiptEvidence(event)
  assert.equal(event.target.value,'');assert.equal(page.external.evidenceSha,expected)
  assert.equal(page.calls.filter(row=>row[0]==='external').length,0)
  assert.equal(JSON.stringify(page.external).includes('not-uploaded'),false)
})
