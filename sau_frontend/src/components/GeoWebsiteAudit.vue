<template>
  <section class="website-audit" aria-label="官网公开内容检测" data-testid="website-audit">
    <header><div><h3>官网内容能被读取吗？</h3><p>检查已登记官方页面的公开内容，定位发文前的资料缺口。</p></div><div class="audit-actions"><el-button text :disabled="pending || checking" @click="refresh">检查原记录</el-button><el-button :disabled="busy || pending || checking || !view?.supported || !selected.length || view?.latest?.status==='running' && !uncertain" :loading="pending" data-testid="website-audit-run" @click="run">{{ uncertain ? '核对同次检测' : view?.history?.length ? '再次检测' : '检测官网' }}</el-button></div></header>
    <p v-if="!view || !view.supported" class="audit-hint">{{ view?.reason || '当前版本尚未提供官网检测，请更新软件。' }}</p>
    <p v-if="error" class="audit-error" role="alert">{{ error }}</p>
    <template v-if="view?.supported">
      <details class="audit-options"><summary>本次检查 {{ selected.length }} 个已登记页面</summary><el-checkbox-group v-model="selected" :disabled="pending || uncertain || checking"><el-checkbox v-for="page in view.allowed_pages" :key="page.id" :value="page.id">{{ page.url }}</el-checkbox></el-checkbox-group><p class="audit-hint">最多5页，只读取服务端内容；页面内的新链接不会自动抓取。</p></details>
      <div v-if="latest" class="audit-result"><div class="audit-result-title"><strong>{{ auditStatus(latest.status) }}{{ latest.current_scope===false ? ' · 历史资料范围' : '' }}</strong><span>{{ journeyDate(latest.finished_at || latest.started_at) }}</span></div><p v-if="latest.status==='running'" class="audit-hint">正在等待同一次检测，通常不超过45秒。请检查原记录，不必再点检测。</p><p v-if="latest.status==='interrupted'" class="audit-hint">原检测结果未知。你可以明确再次检测，原记录仍保留。</p>
        <article v-for="page in latest.pages || []" :key="page.page_id" class="audit-page"><p><a v-if="safeUrl(page.requested_url)" :href="safeUrl(page.requested_url)" target="_blank" rel="noopener noreferrer">{{ page.requested_url }}</a><span v-else>已登记页面</span><small>HTTP {{ page.http_status ?? '未知' }}</small></p><p class="audit-page-title">{{ page.title || '本次未取得页面标题' }}</p><div class="audit-facts"><span>名称文字：{{ auditNamePresence(page.name_text_match) }}</span><span>noindex：{{ auditPresence(page.robots_noindex_observed) }}</span><span>JSON-LD：{{ page.json_ld ? `${page.json_ld.script_count} 段` : '未知' }}</span></div><details v-if="page.findings?.length"><summary>{{ page.findings.length }} 项待核对</summary><p v-for="(item,index) in page.findings" :key="index" class="audit-hint">{{ item.message }}</p></details><details><summary>查看页面检查明细</summary><p class="audit-hint">描述：{{ page.meta_description || '未取得' }}</p><p class="audit-hint">canonical：{{ page.canonical_urls?.join('、') || '未观察到' }}</p><p class="audit-hint">状态：{{ page.status }} · {{ page.bytes ?? '未知' }} 字节；服务端HTML观察，未执行JavaScript。</p><p v-if="page.sha256" class="audit-hash">内容SHA256：{{ page.sha256 }}</p></details></article>
      </div>
      <p v-else class="audit-hint">尚无当前资料范围的检测记录。</p>
      <details v-if="view.history?.length > (view.latest ? 1 : 0)" class="audit-history"><summary>查看 {{ view.history.length }} 次历史检测</summary><p v-for="item in view.history" :key="item.id">{{ journeyDate(item.started_at) }} · {{ auditStatus(item.status) }}{{ item.current_scope===false ? ' · 历史资料范围' : '' }} <el-button text size="small" @click="historicalId=item.id">查看这次</el-button></p></details><el-button v-if="historicalId" text size="small" @click="historicalId=null">返回当前范围</el-button>
      <p class="audit-hint audit-limitation">读取到网页、名称或结构化数据，不代表已索引或被 AI 采用。检测不会修改官网、提交搜索或自动发布。</p>
    </template>
  </section>
</template>
<script setup>
import {computed,onUnmounted,ref,watch} from 'vue'
import {geoWebsiteAuditApi} from '@/api/geoWebsiteAudit'
import {auditSnapshot,auditRunMatches,auditReceipt,auditStatus,auditPresence,auditNamePresence} from '@/utils/geoWebsiteAudit'
import {externalWriteUncertain} from '@/utils/geoExternalFeedback'
import {publicationReviewUrl} from '@/utils/geoPublicationReview'
import {journeyDate} from '@/utils/geoJourney'
const props=defineProps({cycle:{type:Object,required:true},busy:Boolean}),emit=defineEmits(['busy'])
const view=ref(null),selected=ref([]),pending=ref(false),checking=ref(false),uncertain=ref(false),error=ref(''),historicalId=ref(null)
const latest=computed(()=>historicalId.value ? view.value?.history?.find(item=>item.id===historicalId.value) : view.value?.latest),safeUrl=publicationReviewUrl
let generation=0,destroyed=false,frozen=null
const owner=()=>`${props.cycle?.id}:${props.cycle?.revision}:${props.cycle?.website_audit?.context_sha256}`
const active=(epoch,key)=>!destroyed&&epoch===generation&&key===owner()
const refresh=async()=>{
  if(pending.value||checking.value||!props.cycle?.website_audit)return
  const epoch=generation,key=owner();checking.value=true;error.value=''
  try{
    const reply=await geoWebsiteAuditApi.get(props.cycle.id)
    if(!active(epoch,key))return
    if(reply?.code!==200||reply.data?.context_sha256!==props.cycle.website_audit.context_sha256)throw new Error('资料版本有更新，请先刷新本轮。')
    view.value=reply.data
    if(frozen&&view.value.history?.some(item=>auditRunMatches(item,frozen))){uncertain.value=false;frozen=null}
  }catch(cause){if(active(epoch,key))error.value=cause.response?.data?.msg||cause.message||'未能读取原检测记录。'}
  finally{if(active(epoch,key))checking.value=false}
}
const run=async()=>{
  if(pending.value||checking.value||props.busy)return
  let captured
  try{captured=frozen||auditSnapshot(props.cycle,view.value,selected.value)}catch(cause){error.value=cause.message;return}
  if(!uncertain.value&&view.value?.latest?.status==='running')return
  const epoch=generation,key=owner();frozen=captured;pending.value=true;emit('busy',true);error.value=''
  try{
    const reply=await geoWebsiteAuditApi.run(captured.cycleId,captured.payload)
    if(!active(epoch,key))return
    const result=auditReceipt(reply,captured)
    if(result.context_sha256!==props.cycle.website_audit.context_sha256)throw new Error('检测收据已保存，但资料范围已变化，请刷新本轮查看。')
    view.value=result;historicalId.value=null;uncertain.value=false;frozen=null
  }catch(cause){if(active(epoch,key)){uncertain.value=externalWriteUncertain(cause);if(!uncertain.value)frozen=null;error.value=(cause.response?.data?.msg||cause.message||'检测回执未确认。')+(uncertain.value?' 请检查原记录；同次重试不会新建网站请求。':'')}}
  finally{if(active(epoch,key)){pending.value=false;emit('busy',false)}}
}
watch(owner,()=>{generation++;historicalId.value=null;view.value=props.cycle?.website_audit||null;selected.value=(view.value?.allowed_pages||[]).slice(0,5).map(page=>page.id);pending.value=false;checking.value=false;uncertain.value=false;frozen=null;error.value='';emit('busy',false)},{immediate:true})
onUnmounted(()=>{destroyed=true;generation++;emit('busy',false)})
</script>
<style scoped>
.website-audit{margin:18px 0;padding:20px;border:1px solid var(--geo-border,#dce6e2);border-radius:12px;background:#fff;scroll-margin-top:20px}.website-audit header{display:flex;align-items:flex-start;justify-content:space-between;gap:16px}.website-audit h3{font-size:15px;margin:0}.website-audit header p,.audit-hint{font-size:12px;color:var(--geo-muted,#60747a);line-height:1.8;margin:6px 0}.audit-actions{display:flex;gap:8px;white-space:nowrap}.audit-options{margin-top:12px}.website-audit summary{cursor:pointer;font-size:12px;color:var(--geo-primary,#0a756e);padding:7px 0}.audit-options :deep(.el-checkbox-group){display:grid;gap:8px;margin:10px 0}.audit-options :deep(.el-checkbox){height:auto;white-space:normal}.audit-options :deep(.el-checkbox__label){white-space:normal;overflow-wrap:anywhere;font-size:12px}.audit-result{margin-top:14px}.audit-result-title{display:flex;gap:12px;justify-content:space-between;font-size:13px}.audit-result-title span,.audit-page small{font-size:11px;color:var(--geo-muted,#60747a)}.audit-page{padding:12px 0;border-bottom:1px solid var(--geo-border,#dce6e2);font-size:12px;overflow-wrap:anywhere}.audit-page>p:first-child{display:flex;justify-content:space-between;gap:12px}.audit-page a{color:var(--geo-primary,#0a756e)}.audit-page-title{font-size:13px;font-weight:500;margin:10px 0}.audit-facts{display:flex;flex-wrap:wrap;gap:6px 18px;color:var(--geo-muted,#60747a);line-height:1.8}.audit-hash{font-size:11px;color:var(--geo-muted,#60747a);overflow-wrap:anywhere}.audit-error{font-size:12px;line-height:1.8;color:#9a612e;background:#fff7eb;padding:10px 12px;border-radius:8px}.audit-history{font-size:12px}.audit-limitation{margin-top:14px}@media(max-width:700px){.website-audit{padding:16px}.website-audit header{flex-direction:column;gap:8px}.audit-result-title{flex-wrap:wrap}.audit-page>p:first-child{flex-direction:column;gap:4px}}
</style>
