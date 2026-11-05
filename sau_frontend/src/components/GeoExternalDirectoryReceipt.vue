<template>
  <section class="external-directory" aria-label="API指南针回执" data-testid="apiznz-directory-receipt">
    <header>
      <div><strong>API指南针回执</strong><p>登记已在官方页面提交的申请及实际回执。</p></div>
      <el-button :disabled="busy" @click="openPanel">{{ record?.receipt ? '查看已有回执' : '登记已有回执' }}</el-button>
    </header>
    <p v-if="record?.receipt" class="receipt-summary"><el-tag type="info" size="small">用户确认已提交 · 待审核</el-tag> <span>公开收录待核验 · GEO 效果待复测</span></p>
    <el-dialog v-model="visible" class="geo-directory-dialog" modal-class="geo-directory-modal" align-center
      title="API指南针 · 已有回执" width="min(680px, 94vw)" :close-on-click-modal="false" :show-close="!saving" :close-on-press-escape="!saving">
      <p class="hint">请先核对当时提交的产品资料，再登记平台显示的结果。保存只写入当前项目的本地工作台。</p>
      <p v-if="!loading && overview && !supported" class="hint" role="status">{{ supportReason }}</p>
      <p v-if="historyRecord" class="hint">这份记录来自之前的目标或轮次，仅供查看。</p>
      <p v-if="contextRefreshAllowed" class="hint">品牌或平台资料已更新。确认前请重新核对，旧资料仍保存在历史里。</p>
      <el-form v-if="editable" label-position="top" @submit.prevent="reviewFields">
        <div class="receipt-grid">
          <el-form-item label="服务名称"><el-input v-model="form.name" :disabled="locked" :maxlength="120" /></el-form-item>
          <el-form-item label="服务域名"><el-select v-model="form.domain" :disabled="locked" placeholder="选择当时提交的官方域名">
            <el-option v-for="host in officialHosts" :key="host" :value="host" :label="host" />
          </el-select></el-form-item>
        </div>
        <el-form-item label="当时提交的公开介绍"><el-input v-model="form.intro" :disabled="locked" type="textarea" :rows="3" :maxlength="2000" /></el-form-item>
        <el-form-item label="API 基址（当时填写才录入）"><el-input v-model="form.api_base" :disabled="locked" :maxlength="500" placeholder="公开 HTTPS 地址，例如 https://api.example.com/v1" /></el-form-item>
        <p class="hint">域名来自本轮品牌资料。这里只登记公开产品资料，联系方式留在平台页面。</p>
        <el-checkbox v-model="fieldsConfirmed" :disabled="locked">这四项与当时提交的公开资料一致</el-checkbox>
      </el-form>
      <dl v-else-if="record" class="receipt-fields" aria-label="已核对的目录资料">
        <template v-for="[key,label] in fieldLabels" :key="key">
          <dt>{{ label }}</dt><dd>{{ record.fields[key] || '未填写' }}</dd>
        </template>
      </dl>
      <el-form v-if="receiptReady" label-position="top" @submit.prevent="saveReceipt">
        <el-button v-if="record.can_edit" text :disabled="locked" @click="editReviewedFields">更正已核对资料</el-button>
        <div class="receipt-grid">
          <el-form-item label="时间精度"><el-select v-model="receipt.precision" :disabled="locked">
            <el-option label="精确到分钟" value="minute"/><el-option label="精确到秒" value="second"/>
          </el-select></el-form-item>
          <el-form-item label="看到回执的时间（北京时间）">
            <input v-model="receipt.observedAt" :disabled="locked" :type="receipt.precision==='second' ? 'text' : 'datetime-local'"
              :placeholder="receipt.precision==='second' ? '2026-11-05T14:03:25' : ''" step="60" aria-label="看到回执的时间（北京时间）" />
          </el-form-item>
        </div>
        <el-form-item label="当时看到的回执提示"><el-input v-model="receipt.note" :disabled="locked" type="textarea" :rows="2" :maxlength="1000" placeholder="写下页面实际提示，例如：提交成功。请勿填写邮箱或个人资料。" /></el-form-item>
        <el-form-item label="回执截图或文件">
          <input type="file" :disabled="locked" aria-label="选择本机回执证据文件" @change="readEvidence"/>
          <p class="hint">文件只在本机计算校验值。也可以粘贴已有校验值。</p>
          <el-input v-model="receipt.digest" :disabled="locked || hashing" aria-label="回执文件校验值" maxlength="64" placeholder="64 位文件校验值（SHA-256）" />
          <p v-if="proofError" class="error" role="alert">{{ proofError }}</p>
        </el-form-item>
        <el-checkbox v-model="receipt.confirmed" :disabled="locked || hashing">我在平台看到了这份申请的成功回执</el-checkbox>
      </el-form>
      <section v-if="record?.receipt" class="receipt-result" aria-label="已保存的目录回执">
        <strong>用户确认已提交 · 待审核</strong>
        <p>观察时间 {{ formatReceiptTime(record.receipt) }}</p>
        <p>{{ record.receipt.note }}</p>
        <p class="hint">平台未提供编号。公开收录、搜索发现与 GEO 复测需分别核验。</p>
        <a :href="listingUrl" target="_blank" rel="noopener noreferrer">查看公开目录</a>
      </section>
      <p v-if="uncertain" class="hint" role="status">上次保存结果待核对。请先刷新本地记录。</p>
      <p v-if="error" class="error" role="alert">{{ error }}</p>
      <template #footer><div class="receipt-actions">
        <el-button :loading="loading" :disabled="saving" @click="refresh">刷新记录</el-button>
        <el-button v-if="editable" type="primary" :loading="saving" :disabled="!canReview" @click="reviewFields">确认资料</el-button>
        <el-button v-if="receiptReady" type="primary" :loading="saving" :disabled="!canSaveReceipt" @click="saveReceipt">保存已有回执</el-button>
        <el-button :disabled="saving" @click="visible=false">关闭</el-button>
      </div></template>
    </el-dialog>
  </section>
</template>

<script setup>
import { computed, onUnmounted, reactive, ref, watch } from 'vue'
import { apiznzAdapter, apiznzEntry, directoryDigest, directoryOfficialUrls, directoryProjectRecord,
  directoryReceiptTime, directoryRecordCurrent, directorySafeMessage, directoryScope, geoDirectoryApplicationsApi } from '@/api/geoDirectoryApplications'

const props=defineProps({cycle:{type:Object,required:true},target:{type:Object,required:true},busy:Boolean})
const fieldLabels=Object.entries({name:'服务名称',domain:'服务域名',intro:'公开介绍',api_base:'API 基址'})
const emptyReceipt=()=>({precision:'minute',observedAt:'',note:'',digest:'',confirmed:false})
const visible=ref(false),loading=ref(false),saving=ref(false),uncertain=ref(false),error=ref('')
const overview=ref(null),record=ref(null),fieldsConfirmed=ref(false),editingReviewed=ref(false),hashing=ref(false),proofError=ref('')
const form=reactive({name:'',domain:'',intro:'',api_base:''}),receipt=reactive(emptyReceipt())
const scope=computed(()=>directoryScope(props.cycle,props.target))
const scopeKey=computed(()=>JSON.stringify(scope.value))
const supported=computed(()=>overview.value?.supported===true&&directoryDigest(overview.value.context_sha256))
const supportReason=computed(()=>directorySafeMessage(overview.value?.reason,'请检查当前项目的目录网址和商业用途配置。'))
const locked=computed(()=>props.busy||loading.value||saving.value||uncertain.value)
const contextRefreshAllowed=computed(()=>Boolean(record.value?.can_refresh_context===true&&
  record.value.target_id===scope.value?.targetId&&record.value.cycle_id===scope.value?.cycleId&&
  ['draft','reviewed'].includes(record.value.stage)&&!record.value.receipt))
const historyRecord=computed(()=>Boolean(record.value&&!contextRefreshAllowed.value&&
  (!directoryRecordCurrent(record.value,scope.value)||record.value.current_context===false)))
const editable=computed(()=>supported.value&&(!record.value||contextRefreshAllowed.value||
  !historyRecord.value&&record.value.can_edit===true&&(record.value.stage==='draft'||record.value.stage==='reviewed'&&editingReviewed.value)))
const receiptReady=computed(()=>Boolean(supported.value&&directoryRecordCurrent(record.value,scope.value)&&
  record.value.stage==='reviewed'&&record.value.reviewed===true&&record.value.can_record_external_receipt===true&&!record.value.receipt&&!editingReviewed.value))
const listingUrl=computed(()=>`https://www.apiznz.com/site/lists?kw=${encodeURIComponent(record.value?.fields?.domain || '')}`)
const officialHosts=computed(()=>[...new Set(directoryOfficialUrls(props.cycle).map(url=>new URL(url).hostname))])
const observedAt=computed(()=>directoryReceiptTime(receipt.observedAt,receipt.precision))
const canReview=computed(()=>editable.value&&!locked.value&&fieldsConfirmed.value&&form.name.trim()&&
  officialHosts.value.includes(form.domain)&&form.intro.trim()&&validApiBase(form.api_base.trim(),form.domain))
const canSaveReceipt=computed(()=>receiptReady.value&&!locked.value&&!hashing.value&&receipt.confirmed&&
  observedAt.value&&receipt.note.trim()&&directoryDigest(receipt.digest.trim().toLowerCase()))
let evidenceVersion=0,epoch=0,disposed=false

const validApiBase=(value,domain)=>{
  if(!value)return true
  if(/[\\\s?#]/.test(value))return false
  try {
    const url=new URL(value)
    return url.protocol==='https:'&&url.hostname===domain&&!url.username&&!url.password&&!url.port
  } catch {return false}
}
const current=(selected,version)=>!disposed&&version===epoch&&selected?.adapter===apiznzAdapter
const reviewedPayload=value=>({expected_revision:value.revision,expected_review_sha256:value.review_sha256,confirmed:true})
const readFields=value=>Object.fromEntries(fieldLabels.map(([key])=>[key,typeof value?.[key]==='string'?value[key]:'']))
const sameFields=(left,right)=>fieldLabels.every(([key])=>left?.[key]===right[key])
const formatReceiptTime=value=>{
  const date=new Date(value.observed_at)
  if(!Number.isFinite(date.getTime()))return '待核对'
  const time=new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',
    hour:'2-digit',minute:'2-digit',...(value.observed_at_precision==='second'?{second:'2-digit'}:{}),hourCycle:'h23'}).format(date)
  return `${time}（北京时间）`
}
const safeRecord=value=>directoryProjectRecord(value,scope.value)&&value.can_open===false&&value.can_inspect===false&&
  fieldLabels.every(([key])=>typeof value.fields?.[key]==='string')&&
  (!value.receipt||value.stage==='submitted_pending_review'&&value.receipt.intake_uid===null&&value.receipt.owner_reported===true&&
    directoryDigest(value.receipt.evidence_sha256)&&value.receipt.public_listing_verified===false&&value.geo_effect_evidence===false)
const acceptMutation=(value,fields,expectedId,stage)=>{
  if(!safeRecord(value)||!directoryRecordCurrent(value,scope.value)||!sameFields(value.fields,fields)||
    expectedId&&value.id!==expectedId||value.stage!==stage)throw new Error('保存结果与当前项目或申请不一致，请刷新记录。')
  record.value=value
}
const refresh=async()=>{
  const selected=scope.value,version=epoch
  if(!current(selected,version)||loading.value||saving.value)return
  loading.value=true;error.value=''
  try{
    const {data}=await geoDirectoryApplicationsApi.list(selected.cycleId,selected.targetId)
    if(!current(selected,version))return
    if(data?.adapter!==apiznzAdapter||data.entry_url!==apiznzEntry||!Array.isArray(data.applications)||
      data.applications.length>1||data.applications.some(item=>!safeRecord(item)))throw new Error('目录记录与当前项目不一致，请刷新记录。')
    overview.value=data
    record.value=data.applications[0]||null
    if(record.value)Object.assign(form,readFields(record.value.fields))
    fieldsConfirmed.value=false;editingReviewed.value=false;receipt.confirmed=false
    uncertain.value=false
  }catch(cause){
    if(current(selected,version)){error.value=directorySafeMessage(cause,'暂未读取到已有目录记录，请刷新后核对。');uncertain.value=true}
  }finally{if(current(selected,version))loading.value=false}
}
const openPanel=()=>{
  if(props.busy)return
  visible.value=true
  if(!overview.value)void refresh()
}
defineExpose({openPanel})
const editReviewedFields=()=>{
  if(locked.value||!receiptReady.value||record.value.can_edit!==true)return
  editingReviewed.value=true;fieldsConfirmed.value=false;receipt.confirmed=false
}

const saveLocally=async action=>{
  const selected=scope.value,version=epoch
  const active=()=>current(selected,version)
  if(!active())return
  saving.value=true;error.value=''
  try{await action(selected,active)}
  catch(cause){if(active()){uncertain.value=true;error.value=directorySafeMessage(cause,'本地保存结果待核对，请刷新记录。')}}
  finally{if(active())saving.value=false}
}
const reviewFields=async()=>{
  if(!canReview.value)return
  const fields=Object.fromEntries(fieldLabels.map(([key])=>[key,form[key].trim()]))
  await saveLocally(async (selected,active)=>{
    const existing=record.value
    if(contextRefreshAllowed.value){
      const {data}=await geoDirectoryApplicationsApi.refreshContext(selected.cycleId,existing.id,{...reviewedPayload(existing),
        expected_context_sha256:overview.value.context_sha256,fields})
      if(!active())return
      acceptMutation(data,fields,existing.id,'draft')
    }else if(!existing||!sameFields(existing.fields,fields)){
      const {data}=await geoDirectoryApplicationsApi.save(selected.cycleId,{target_id:selected.targetId,
        expected_context_sha256:overview.value.context_sha256,fields,
        ...(existing?{application_id:existing.id,revision:existing.revision}:{})})
      if(!active())return
      acceptMutation(data,fields,existing?.id,'draft')
    }
    const draft=record.value
    if(draft.stage==='reviewed'){editingReviewed.value=false;return}
    const {data}=await geoDirectoryApplicationsApi.review(selected.cycleId,draft.id,reviewedPayload(draft))
    if(!active())return
    if(data?.reviewed!==true)throw new Error('尚未确认资料已核对，请刷新记录。')
    acceptMutation(data,fields,draft.id,'reviewed')
    editingReviewed.value=false
  })
}
const saveReceipt=async()=>{
  if(!canSaveReceipt.value)return
  const draft=record.value
  const payload={...reviewedPayload(draft),status:'submitted_pending_review',observed_at:observedAt.value,
    observed_at_precision:receipt.precision,note:receipt.note.trim(),evidence_sha256:receipt.digest.trim().toLowerCase()}
  await saveLocally(async (selected,active)=>{
    const {data}=await geoDirectoryApplicationsApi.externalReceipt(selected.cycleId,draft.id,payload)
    if(!active())return
    const saved=data?.receipt
    if(saved?.evidence_sha256!==payload.evidence_sha256||saved.note!==payload.note||
      Date.parse(saved.observed_at)!==Date.parse(payload.observed_at)||saved.observed_at_precision!==payload.observed_at_precision||
      data.evidence_level!=='owner_reported')throw new Error('尚未确认回执已保存，请刷新记录。')
    acceptMutation(data,draft.fields,draft.id,payload.status)
    Object.assign(receipt,emptyReceipt())
  })
}
const readEvidence=async event=>{
  const file=event.target.files?.[0],selected=scope.value,scopeVersion=epoch
  event.target.value=''
  if(!file||locked.value||!receiptReady.value)return
  const version=++evidenceVersion
  receipt.digest='';proofError.value='';hashing.value=true
  try{
    if(file.size<=0||file.size>25*1024*1024)throw new Error('size')
    const hash=await globalThis.crypto.subtle.digest('SHA-256',await file.arrayBuffer())
    if(current(selected,scopeVersion)&&version===evidenceVersion)receipt.digest=Array.from(new Uint8Array(hash),byte=>byte.toString(16).padStart(2,'0')).join('')
  }catch{
    if(current(selected,scopeVersion)&&version===evidenceVersion)proofError.value='无法读取这份文件。请选择 25 MB 以内的回执文件，或粘贴已有校验值。'
  }finally{if(current(selected,scopeVersion)&&version===evidenceVersion)hashing.value=false}
}
watch(()=>fieldLabels.map(([key])=>form[key]),()=>{fieldsConfirmed.value=false},{flush:'sync'})
watch(()=>[receipt.precision,receipt.observedAt,receipt.note,receipt.digest],()=>{receipt.confirmed=false},{flush:'sync'})
watch(scopeKey,()=>{
  epoch++
  evidenceVersion++
  visible.value=false;loading.value=false;saving.value=false;uncertain.value=false;hashing.value=false
  error.value='';proofError.value='';overview.value=null;record.value=null;fieldsConfirmed.value=false;editingReviewed.value=false
  const profile=props.cycle.profile||props.cycle.baseline?.profile
  Object.assign(form,readFields({name:profile?.name,domain:officialHosts.value.length===1?officialHosts.value[0]:''}))
  Object.assign(receipt,emptyReceipt())
  void refresh()
},{immediate:true,flush:'sync'})
onUnmounted(()=>{evidenceVersion++;disposed=true})
</script>

<style scoped>
.external-directory{margin:14px 0;padding:16px;border:1px solid var(--geo-border,#dce6e2);border-radius:10px;background:var(--geo-surface,#fff);font-size:13px;line-height:1.7}
.external-directory header,.receipt-actions,.receipt-summary{display:flex;align-items:center;flex-wrap:wrap;gap:8px}
.external-directory header{justify-content:space-between}.external-directory header p,.hint{margin:6px 0;color:var(--geo-muted,#59696e);font-size:12px;line-height:1.7}
.receipt-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:0 16px}.receipt-grid :deep(.el-select){width:100%}
.receipt-grid input,.receipt-fields,.receipt-result{box-sizing:border-box;min-width:0;width:100%;padding:10px;border:1px solid var(--geo-border,#dce6e2);border-radius:6px}
input[type=file]{max-width:100%;min-width:0}.receipt-fields{display:grid;grid-template-columns:110px minmax(0,1fr);gap:6px 12px}
.receipt-fields dt{color:var(--geo-muted,#59696e)}.receipt-fields dd,.receipt-result p{margin:0;white-space:pre-wrap;overflow-wrap:anywhere}
.receipt-actions{justify-content:flex-end}.receipt-actions .el-button{margin:0;max-width:100%;white-space:normal}
.error{color:#a6412d;overflow-wrap:anywhere}:deep(.el-checkbox){height:auto;min-height:28px;max-width:100%}:deep(.el-checkbox__label){white-space:normal}
@media(max-width:560px){.receipt-grid,.receipt-fields{grid-template-columns:1fr}.receipt-fields dd{margin-bottom:6px}}
</style>
