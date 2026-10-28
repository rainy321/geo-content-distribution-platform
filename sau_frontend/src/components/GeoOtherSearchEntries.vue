<template>
  <section class="other-search" aria-label="其他搜索入口" data-testid="other-search-entries">
    <header><div><h3>其他搜索入口</h3><p>在百度、头条官方平台继续官网接入，回到这里保存你实际看到的进度。</p></div><el-button text :disabled="loading || saving || busy" @click="refresh">刷新进度</el-button></header>
    <p v-if="error || checkpointError" class="search-warning" role="alert">{{ error || checkpointError }}</p>
    <p v-if="notice" class="search-hint" role="status">{{ notice }}</p>
    <p v-if="loading" class="search-hint">正在读取这轮官网进度…</p>
    <p v-else-if="view && !view.supported" class="search-hint">{{ view.reason }}</p>
    <template v-if="view?.supported">
      <div class="search-provider-tabs" role="group" aria-label="搜索平台"><button v-for="(item,id) in searchProviders" :key="id" type="button" :aria-pressed="provider===id" :disabled="loading || saving || busy" @click="provider=id">{{ item.name }}</button></div>
      <p class="search-hint search-value">官网：{{ view.site_origin }} <el-button text :disabled="!siteAddress" @click="copySite('website')">复制官网地址</el-button><el-button v-if="provider==='baidu'" text :disabled="!siteDomain" @click="copySite('domain')">复制域名</el-button> · {{ searchRecord(view,provider) ? '已有用户登记进度' : '进度未知，尚未登记' }}</p>
      <p v-if="siteCopyError" class="search-warning" role="alert">{{ siteCopyError }}</p><p v-else-if="siteCopyNotice" class="search-hint" role="status">{{ siteCopyNotice }}</p>
      <details class="search-guide" open><summary>添加、验证与提交官网</summary>
        <ol><li v-if="provider==='baidu'">添加前先核对百度账号的实名认证状态。可在百度 App“账号管理 → 身份认证”查看，未实名可能影响站点验证权限；证件资料只在百度官方页面提交。切换账号后，请重新核对网站添加、验证和提交状态，再更新本页记录。</li>
          <li>使用自己的日常浏览器登录<a :href="searchProviders[provider].entry" target="_blank" rel="noopener noreferrer" @click="openPortal($event,'entry')">{{ searchProviders[provider].name }}</a>，添加 {{ view.site_origin }}/。<template v-if="provider==='baidu'">登录后进入“用户中心 → 站点管理 → 添加网站”。首次添加如要求完善账户信息，请在百度页面填写，无需将联系方式填入本软件。协议与域名分栏时，协议选 HTTPS，域名栏只填主机名，可使用上方“复制域名”。若出现 www 主站建议，请按实际官网主地址核对后选择。</template></li>
          <li>按平台说明准备网站所有权验证材料，交网站维护者部署，再返回平台执行验证。看到验证成功后，才在下方登记“验证通过”。<a :href="searchProviders[provider].help" target="_blank" rel="noopener noreferrer" @click="openPortal($event,'help')">查看官方帮助</a></li>
          <li>验证通过后，前往<a :href="searchProviders[provider].submit" target="_blank" rel="noopener noreferrer" @click="openPortal($event,'submit')">官方提交入口</a>提交站点地图或实际页面，再登记平台结果。</li></ol>
        <div v-if="portalUrl" class="search-value" data-testid="search-portal-fallback"><p v-if="portalError" class="search-warning" role="alert">{{ portalError }}</p><p v-else class="search-hint" role="status">{{ portalMessage }}</p><p>{{ portalUrl }} <el-button text @click="copyPortal">复制入口地址</el-button></p></div>
        <p class="search-value">{{ view.sitemap_url }} <el-button text :disabled="locked" @click="copySitemap">复制站点地图地址</el-button></p>
        <p class="search-hint">{{ view.providers.find(item=>item.id===provider)?.availability_note }}</p>
      </details>
      <div class="search-form">
        <label :for="`${owner}-${provider}-added`">该平台是否已添加官网</label><select :id="`${owner}-${provider}-added`" v-model="forms[provider].site_added" :disabled="locked"><option :value="null">未知 / 尚未核对</option><option :value="true">我确认已添加</option><option :value="false">我确认尚未添加</option></select>
        <label :for="`${owner}-${provider}-verified`">平台是否显示网站验证通过</label><select :id="`${owner}-${provider}-verified`" v-model="forms[provider].ownership_confirmed" :disabled="locked"><option :value="null">未知 / 尚未核对</option><option :value="true">我确认平台显示验证通过</option><option :value="false">尚未通过或已失效</option></select>
        <label :for="`${owner}-${provider}-sitemap`">站点地图提交状态</label><select :id="`${owner}-${provider}-sitemap`" v-model="forms[provider].sitemap_status" :disabled="locked"><option v-for="status in sitemapStatuses" :key="status" :value="status">{{ searchSitemapLabel(status) }}</option></select>
        <p class="search-hint">此项只记录站点地图结果；单个页面的提交结果可写在备注。平台未提供地图提交入口时，保留“未知”或“尚未提交”。</p>
        <label :for="`${owner}-${provider}-note`">备注（可选，最多500字）</label><textarea :id="`${owner}-${provider}-note`" v-model="forms[provider].note" rows="2" maxlength="500" :disabled="locked" placeholder="记录平台显示的处理提示；无需填写账号、密码或验证文件。"/>
      </div>
      <p v-if="forms[provider].site_added===false && forms[provider].ownership_confirmed===true" class="search-warning">尚未添加官网与网站验证已通过互相矛盾，请按平台当前显示核对后保存。</p>
      <p v-if="uncertain[provider]" class="search-warning">保存回执尚未确认，填写已保留。重试只核对同一次软件保存，不会向平台重复提交。</p>
      <div class="search-buttons"><el-button type="primary" :loading="saving===provider" :disabled="busy || loading || Boolean(saving)" data-testid="other-search-save" @click="save">{{ uncertain[provider] ? '核对并重试保存' : '保存用户确认的进度' }}</el-button><el-button :disabled="busy || loading || Boolean(saving)" @click="adoptSaved">使用已保存的进度</el-button><el-button text :disabled="Boolean(saving)" @click="exportDrafts">导出当前填写</el-button></div>
      <p v-if="stoppedUnknownCount" class="search-warning">本次应用保留 {{ stoppedUnknownCount }} 条已停止重试、回执仍未知的记录，可随当前填写导出；关闭软件前请自行保留。</p>
      <p v-if="searchRecord(view,provider)?.updated_at" class="search-hint">上次登记：{{ journeyDate(searchRecord(view,provider).updated_at) }} · 用户声明，未独立核验</p>
      <p class="search-hint">网站验证、提交、抓取和索引是不同阶段。这些进度不证明豆包采用，也不表示 GEO 优化成功。</p>
    </template>
  </section>
</template>
<script setup>
import {computed,onUnmounted,reactive,ref,watch} from 'vue'
import {ElMessageBox} from 'element-plus'
import {geoSearchOnboardingApi} from '@/api/geoSearchOnboarding'
import {searchOnboardingSession,searchStoppedReceipts,searchProviders,searchSiteCopyText,emptySearchProgress,searchProgressEqual,searchCheckpointKey,searchScopeKey,searchRecord,searchSitemapLabel,searchOverview,searchPayload,searchRequestSha,searchSavedRequest,searchReceipt,searchCanApply} from '@/utils/geoSearchOnboarding'
import {externalWriteUncertain} from '@/utils/geoExternalFeedback'
import {exportFeedbackDraft} from '@/utils/geoFeedbackSession'
import {journeyDate} from '@/utils/geoJourney'
import {openSearchPortal,searchPortalUrl,searchPortalErrorMessage} from '@/utils/geoSearchPortal'
const props=defineProps({cycle:{type:Object,required:true},busy:Boolean}),emit=defineEmits(['busy'])
const view=ref(null),provider=ref('baidu'),loading=ref(false),saving=ref(''),error=ref(''),notice=ref(''),checkpointError=ref('')
const forms=reactive({baidu:emptySearchProgress(),toutiao:emptySearchProgress()}),uncertain=reactive({baidu:false,toutiao:false})
const owner=searchOnboardingSession.owner('search'),sitemapStatuses=['unknown','not_submitted','pending','processed','failed']
const stoppedVersion=ref(0),stoppedUnknownCount=computed(()=>{stoppedVersion.value;return searchStoppedReceipts.list(view.value).length})
const locked=computed(()=>props.busy||loading.value||Boolean(saving.value)||uncertain[provider.value])
const tokens={},versions={},bases={},frozen={},digests={}
const portalUrl=ref(''),portalMessage=ref(''),portalError=ref(''),portalOpening=ref(false)
const siteAddress=computed(()=>searchSiteCopyText(view.value,'website')),siteDomain=computed(()=>searchSiteCopyText(view.value,'domain'))
const siteCopyNotice=ref(''),siteCopyError=ref('')
let generation=0,disposed=false,paused=false,readSequence=0
let portalSequence=0
let siteCopySequence=0
const clone=value=>JSON.parse(JSON.stringify(value))
const active=(epoch,scope)=>!disposed&&epoch===generation&&scope===searchScopeKey(props.cycle)
const message=cause=>cause?.response?.data?.msg||cause?.message||'进度尚未确认，请核对原记录。'
const resetSiteCopy=()=>{siteCopySequence++;siteCopyNotice.value='';siteCopyError.value=''}
const copySite=async kind=>{
  if(disposed||kind==='domain'&&provider.value!=='baidu')return
  const text=searchSiteCopyText(view.value,kind)
  if(!text)return
  const epoch=generation,scope=searchScopeKey(props.cycle),id=provider.value,origin=view.value.site_origin,sequence=++siteCopySequence
  const current=()=>active(epoch,scope)&&provider.value===id&&sequence===siteCopySequence&&view.value?.supported===true&&view.value.site_origin===origin
  siteCopyNotice.value='';siteCopyError.value=''
  try{await navigator.clipboard.writeText(text);if(current())siteCopyNotice.value=kind==='domain'?'域名已复制，不含协议或路径。':'官网地址已复制。'}
  catch{if(current())siteCopyError.value='复制未完成，请选择上面的官网地址手动复制。'}
}
const openPortal=async(event,purpose)=>{
  if(disposed)return
  if(portalOpening.value){event.preventDefault();return}
  const id=provider.value,pending=openSearchPortal(event,id,purpose)
  if(!pending)return
  const sequence=++portalSequence
  portalUrl.value=searchPortalUrl(id,purpose);portalError.value='';portalMessage.value='正在请求系统浏览器；也可以复制地址自行打开。';portalOpening.value=true
  try{await pending;if(!disposed&&sequence===portalSequence)portalMessage.value='已请求系统默认浏览器打开。若没有出现窗口，请复制地址到日常浏览器；登录和页面状态需自行核对。'}
  catch(cause){if(!disposed&&sequence===portalSequence){portalMessage.value='';portalError.value=searchPortalErrorMessage(cause)}}
  finally{if(!disposed&&sequence===portalSequence)portalOpening.value=false}
}
const copyPortal=async()=>{
  const sequence=portalSequence,url=portalUrl.value
  try{await navigator.clipboard.writeText(url);if(!disposed&&sequence===portalSequence){portalError.value='';portalMessage.value='入口地址已复制，请粘贴到日常浏览器。'}}
  catch{if(!disposed&&sequence===portalSequence)portalError.value='复制未完成，请选择并复制上面的完整地址，到日常浏览器打开。'}
}
const remember=id=>{
  if(paused||!view.value?.supported)return true
  if(!frozen[id]&&searchProgressEqual(forms[id],searchRecord(view.value,id)?.progress||emptySearchProgress())&&!tokens[id])return true
  const key=searchCheckpointKey(view.value,id),value={form:clone(forms[id]),base:bases[id],frozen:frozen[id]||null,digest:digests[id]||null,uncertain:uncertain[id]||saving.value===id&&Boolean(frozen[id])}
  if(!tokens[id]&&searchOnboardingSession.read(key)){checkpointError.value='同一官网的进度草稿已在另一入口更新。请先导出当前填写，再重新打开核对；原草稿未覆盖。';return false}
  let result=searchOnboardingSession.write(key,value,tokens[id]??null,versions[id]??null)
  if(result.reason==='stale'&&!searchOnboardingSession.read(key))result=searchOnboardingSession.write(key,value)
  if(!result.ok){checkpointError.value='本次应用的进度草稿容量已满或另一入口已更新。当前填写仍保留在窗口，离开前请导出，原记录未覆盖。';return false}
  tokens[id]=result.token;versions[id]=result.version;checkpointError.value='';return true
}
const rememberAll=()=>{let ok=true;for(const id of Object.keys(searchProviders))ok=remember(id)&&ok;return ok}
const assignSaved=id=>{
  Object.assign(forms[id],clone(searchRecord(view.value,id)?.progress||emptySearchProgress()));bases[id]=searchRecord(view.value,id)?.revision||0
  frozen[id]=null;digests[id]=null;uncertain[id]=false;delete tokens[id];delete versions[id]
}
const applyLoaded=next=>{
  if(!searchCanApply(next,view.value))throw new Error('收到较早的平台进度，当前填写保留，请刷新核对。')
  const initialRead=!view.value,retainFailed={},preserved=[]
  if(!initialRead)for(const id of Object.keys(searchProviders))retainFailed[id]=!remember(id)
  view.value=next;paused=true
  try{for(const id of Object.keys(searchProviders)){
    const key=searchCheckpointKey(next,id),saved=searchOnboardingSession.read(key)
    if(saved?.value.frozen&&searchSavedRequest(next,saved.value.frozen,saved.value.digest)){
      const ownUnedited=!retainFailed[id]&&tokens[id]===saved.token&&versions[id]===saved.version
        &&searchProgressEqual(forms[id],saved.value.frozen.progress)&&bases[id]===saved.value.frozen.expected_provider_revision
        &&JSON.stringify(frozen[id])===JSON.stringify(saved.value.frozen)
      searchOnboardingSession.confirmed(key,saved.token,saved.value.frozen)
      if(initialRead||ownUnedited){assignSaved(id);notice.value='已从软件保存记录核对到原进度。'}
      else{preserved.push(id);notice.value='已核对另一入口的保存记录；当前未保存填写及原版本保持，请核对后决定是否使用已保存进度。'}
      continue
    }
    if(retainFailed[id])continue
    if(saved){Object.assign(forms[id],saved.value.form);bases[id]=saved.value.base;frozen[id]=saved.value.frozen;digests[id]=saved.value.digest;uncertain[id]=saved.value.uncertain;tokens[id]=saved.token;versions[id]=saved.version}
    else assignSaved(id)
  }}finally{paused=false}
  for(const id of preserved)remember(id)
}
const refresh=async()=>{
  if(disposed||loading.value||saving.value)return
  const epoch=generation,scope=searchScopeKey(props.cycle),sequence=++readSequence,cycle=clone(props.cycle)
  loading.value=true;error.value=''
  try{const reply=await geoSearchOnboardingApi.get(cycle.id);if(!active(epoch,scope)||sequence!==readSequence)return;const next=searchOverview(cycle,reply?.data);if(next.supported)applyLoaded(next);else view.value=next}
  catch(cause){if(active(epoch,scope))error.value=message(cause)}
  finally{if(active(epoch,scope))loading.value=false}
}
const save=async()=>{
  if(disposed||props.busy||loading.value||saving.value||!view.value?.supported)return
  const id=provider.value,epoch=generation,scope=searchScopeKey(props.cycle),captured=clone(view.value),wasUnknown=uncertain[id]
  let body,digest,token,key,attempted=false,originalForm
  try{body=clone(frozen[id]||searchPayload(captured,id,forms[id],bases[id]));originalForm=clone(forms[id])}catch(cause){error.value=message(cause);return}
  saving.value=id;error.value='';notice.value=''
  try{
    digest=digests[id]||await searchRequestSha(body)
    if(!active(epoch,scope))return
    const previous=frozen[id];frozen[id]=body;digests[id]=digest
    if(!remember(id)){frozen[id]=previous;throw new Error(checkpointError.value)}
    token=tokens[id];key=searchCheckpointKey(captured,id);attempted=true
    const reply=await geoSearchOnboardingApi.save(captured.cycle_id,body),next=searchReceipt(reply,captured,body,digest)
    searchOnboardingSession.confirmed(key,token,body)
    if(!active(epoch,scope))return
    paused=true
    try{if(searchCanApply(next,view.value))view.value=next;assignSaved(id)}finally{paused=false}
    notice.value='用户确认的进度已保存；平台操作与索引状态仍需分别核对。'
  }catch(cause){
    const unknown=wasUnknown||attempted&&externalWriteUncertain(cause)
    const settled=attempted&&searchOnboardingSession.settle(key,token,body,{form:originalForm,base:body.expected_provider_revision,frozen:unknown?body:null,digest:unknown?digest:null,uncertain:unknown})
    if(active(epoch,scope)){if(settled)versions[id]=searchOnboardingSession.read(key)?.version??versions[id];uncertain[id]=unknown;if(!unknown){frozen[id]=null;digests[id]=null}error.value=message(cause)}
  }finally{if(active(epoch,scope)){saving.value='';remember(id)}}
}
const adoptSaved=async()=>{
  if(props.busy||loading.value||saving.value||!view.value?.supported)return
  const epoch=generation,scope=searchScopeKey(props.cycle),id=provider.value,wasUnknown=uncertain[id]
  try{
    await ElMessageBox.confirm(wasUnknown?'原保存回执仍未知。继续将停止重试原请求，用当前读取的进度替换本地填写；不表示原请求成功或失败。原请求的不确定记录会在本次应用内保留，请在关闭软件前导出。':'将用当前已读取的保存记录替换这一平台的未保存填写。需要保留时，请先导出。','使用已保存进度',{confirmButtonText:'使用保存记录',cancelButtonText:'保留当前填写'})
    if(!active(epoch,scope))return
    const key=searchCheckpointKey(view.value,id),prior=searchOnboardingSession.read(key)
    if(prior&&(prior.token!==tokens[id]||prior.version!==versions[id]))throw new Error('另一入口已更新草稿，请先导出当前填写再重新打开核对。')
    if(wasUnknown){if(!frozen[id]||!searchStoppedReceipts.keep(view.value,frozen[id],digests[id]))throw new Error('回执未知的原请求记录容量已满，当前填写与原请求仍保留，请先导出并核对。');stoppedVersion.value++}
    const progress=clone(searchRecord(view.value,id)?.progress||emptySearchProgress()),base=searchRecord(view.value,id)?.revision||0
    if(prior){const result=searchOnboardingSession.write(key,{form:progress,base,frozen:null,digest:null,uncertain:false},prior.token,prior.version);if(!result.ok)throw new Error('草稿未能保留，请先导出当前填写。');tokens[id]=result.token;versions[id]=result.version}
    paused=true;Object.assign(forms[id],progress);bases[id]=base;frozen[id]=null;digests[id]=null;uncertain[id]=false;paused=false
    if(wasUnknown)notice.value='已使用读取到的进度；原请求结果仍未知，不确定记录已保留，本次操作没有发送保存请求。'
  }catch(cause){paused=false;if(active(epoch,scope)&&cause instanceof Error)error.value=message(cause)}
}
const copySitemap=async()=>{const epoch=generation,scope=searchScopeKey(props.cycle);try{await navigator.clipboard.writeText(view.value.sitemap_url);if(active(epoch,scope))notice.value='站点地图地址已复制。'}catch{if(active(epoch,scope))error.value='复制未完成，请直接选择上面的地址。'}}
const exportDrafts=()=>exportFeedbackDraft({cycle_id:view.value?.cycle_id,site_origin:view.value?.site_origin,forms:clone(forms),frozen:clone(frozen),stopped_unknown_requests:searchStoppedReceipts.list(view.value)},'other-search-progress')
watch(forms,rememberAll,{deep:true,flush:'sync'})
watch(provider,()=>{portalSequence++;portalUrl.value='';portalMessage.value='';portalError.value='';portalOpening.value=false;resetSiteCopy()},{flush:'sync'})
watch(()=>searchScopeKey(props.cycle),()=>{
  resetSiteCopy()
  portalSequence++;portalUrl.value='';portalMessage.value='';portalError.value='';portalOpening.value=false
  rememberAll();paused=true;generation++;readSequence++;view.value=null;loading.value=false;saving.value='';error.value='';notice.value='';checkpointError.value=''
  for(const id of Object.keys(searchProviders)){Object.assign(forms[id],emptySearchProgress());uncertain[id]=false;delete tokens[id];delete versions[id];delete bases[id];delete frozen[id];delete digests[id]}
  paused=false;void refresh()
},{immediate:true,flush:'sync'})
watch(()=>props.cycle?.revision,()=>{if(view.value&&!loading.value&&!saving.value)void refresh()})
watch([loading,saving],()=>emit('busy',{owner,active:loading.value||Boolean(saving.value)}),{immediate:true,flush:'sync'})
onUnmounted(()=>{rememberAll();disposed=true;generation++;emit('busy',{owner,active:false})})
</script>
<style scoped>
.other-search{margin:18px 0;padding:18px;border:1px solid var(--geo-border,#dce6e2);border-radius:12px;background:#fff}.other-search header{display:flex;align-items:flex-start;justify-content:space-between;gap:12px}.other-search h3{margin:0;font-size:15px}.other-search header p,.search-hint,.search-guide{font-size:12px;line-height:1.8;color:var(--geo-muted,#60747a)}.search-provider-tabs,.search-buttons{display:flex;gap:8px;flex-wrap:wrap;margin:12px 0}.search-provider-tabs button{padding:8px 12px;border:1px solid #cedcd7;border-radius:7px;background:#f6faf8;color:inherit;cursor:pointer}.search-provider-tabs button[aria-pressed=true]{border-color:#0a756e;color:#0a756e}.search-provider-tabs button:focus-visible{outline:2px solid #0a756e;outline-offset:2px}.search-guide li{margin:8px 0}.search-value{overflow-wrap:anywhere}.search-form{display:grid;gap:7px;font-size:12px}.search-form select,.search-form textarea{width:100%;box-sizing:border-box;padding:9px 10px;margin:0 0 8px;border:1px solid #cedcd7;border-radius:6px;background:#fff;font:inherit}.search-warning{font-size:12px;line-height:1.8;color:#9a612e;background:#fff7eb;padding:10px}.other-search a{color:#0a756e}@media(max-width:650px){.other-search{padding:14px}.other-search header{flex-wrap:wrap}.search-buttons .el-button{margin-left:0}}
.search-guide ol{padding-left:20px}.search-guide li{display:list-item;list-style:decimal}
</style>
