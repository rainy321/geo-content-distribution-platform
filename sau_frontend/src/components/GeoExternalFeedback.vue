<template>
  <section class="external-feedback" aria-label="外部回答反馈" data-testid="external-feedback">
    <div class="external-feedback-heading"><div><strong>手机或同事的回答，也能记录</strong><p>外部原文单独保存，采集过程与引用链接仍需核对。</p></div><el-button :disabled="busy || pending || !scope" data-testid="external-feedback-open" @click="openCreate">录入外部回答</el-button></div>
    <p v-if="!scope" class="external-hint">这份记录缺少可核对的问题计划，请先打开完整的搜索基线。</p>
    <el-alert v-if="checkpointError" :title="checkpointError" type="warning" :closable="false" show-icon/>
    <el-button v-if="checkpointError" text @click="exportDrafts">导出当前反馈填写</el-button>
    <details v-if="records.length" class="external-records"><summary>已保存 {{ records.length }} 条外部回答</summary>
      <article v-for="record in records.slice(0, recordLimit)" :key="record.id" class="external-record" data-testid="external-feedback-record">
        <div><strong>{{ record.case_id }} · {{ journeyDate(record.created_at) }}</strong><span>用户提供，未原生核验</span></div>
        <p>{{ excerpt(record.after?.response, 110) }}</p>
        <div class="external-actions"><el-button text @click="openRead(record)">查看原文</el-button><el-button text :disabled="busy || pending" @click="openContext(record)">补充提问条件</el-button><GeoBusinessReview :cycle="cycle" :probe="record" :busy="busy || pending" @updated="emit('updated',$event)" @busy="emit('busy',$event)"/></div>
      </article>
      <el-button v-if="records.length > recordLimit" text @click="recordLimit += 5">再显示 5 条</el-button>
    </details>

    <el-dialog v-model="createVisible" title="录入外部回答" class="external-feedback-dialog" width="min(760px, calc(100vw - 24px))" top="5vh" append-to-body :close-on-click-modal="false" :close-on-press-escape="false" :before-close="closeCreate">
      <p class="external-hint">先选择本轮关联问题，再粘贴实际取得的回答。不清楚的条件保留“未知”。</p>
      <el-alert v-if="error" :title="error" type="warning" :closable="false" show-icon data-testid="external-feedback-error"/>
      <p v-if="checkpointError" class="external-hint">{{ checkpointError }} <el-button text @click="exportDrafts">导出填写</el-button></p>
      <el-form label-position="top" @submit.prevent="saveCreate">
        <el-form-item label="本轮关联问题"><el-select v-model="form.caseId" :disabled="locked" aria-label="外部反馈关联问题" data-testid="external-feedback-case"><el-option v-for="item in cases" :key="item.id" :label="`${item.id} · ${excerpt(item.question, 70)}`" :value="item.id"/></el-select></el-form-item>
        <div v-if="selectedCase" class="external-original-question"><p>{{ selectedCase.question }}</p><el-button text :disabled="pending" @click="copyQuestion">复制原题</el-button><small>{{ copyMessage }}</small></div>
        <el-form-item label="回答原文"><el-input v-model="form.raw" type="textarea" :rows="9" :disabled="locked" placeholder="完整粘贴对方取得的回答，保留否定结论及来源文字。" aria-label="外部回答原文" data-testid="external-feedback-answer"/></el-form-item>
        <details class="external-optional"><summary>补充采集条件（可选）</summary>
          <el-form-item label="实际发送的问题"><el-radio-group v-model="form.questionMode" :disabled="locked"><el-radio-button value="unknown">题文未提供</el-radio-button><el-radio-button value="same">确认与上面原题相同</el-radio-button><el-radio-button value="custom">粘贴实际题文</el-radio-button></el-radio-group></el-form-item>
          <el-input v-if="form.questionMode === 'custom'" v-model="form.question" :disabled="locked" type="textarea" :rows="3" :maxlength="4000" placeholder="只填对方实际发出的题文" aria-label="实际提问题文"/>
          <div class="external-fields"><el-form-item label="实际界面"><el-select v-model="form.clientMode" :disabled="locked" aria-label="外部回答界面"><el-option label="未知" value="unknown"/><el-option label="普通豆包对话" value="consumer"/><el-option label="豆包工作" value="work"/></el-select></el-form-item><el-form-item label="模型名称"><el-input v-model="form.model" :disabled="locked" :maxlength="100" placeholder="不知道就留空"/></el-form-item></div>
          <div class="external-fields"><el-form-item label="是否为完整最终回答"><el-select v-model="form.completeness" :disabled="locked" aria-label="外部回答完整性"><el-option label="未知" value="unknown"/><el-option label="我确认是完整最终回答" value="complete"/><el-option label="只取得部分回答" value="partial"/></el-select></el-form-item><el-form-item label="实际采集时间"><el-input v-model="form.collectedAt" :disabled="locked" type="datetime-local" aria-label="外部回答采集时间"/></el-form-item></div>
          <el-form-item label="回答附带的来源链接"><el-input v-model="form.sourceLinks" :disabled="locked" type="textarea" :rows="3" placeholder="从回答的来源面板复制，一行一个公开网址，最多20条。只有平台名时可留空。" aria-label="外部回答来源链接"/></el-form-item>
          <p class="external-hint">这里保存用户提供的链接；回答正文中的官网文字不等于引用，网址可打开也不代表已被软件核实。</p>
          <el-form-item label="备注"><el-input v-model="form.note" :disabled="locked" type="textarea" :rows="2" :maxlength="1000" placeholder="例如由同事使用手机取得；不要填写密码或验证码。"/></el-form-item>
        </details>
      </el-form>
      <p class="external-hint">关联题不证明实际同题。保存外部反馈不会改写原搜索基线，也不会把未知条件算成软件已验证。</p>
      <template #footer><el-button text :disabled="pending || !form.raw.trim()" @click="exportDrafts">导出填写</el-button><el-button :disabled="pending" @click="closeCreate(() => { createVisible = false })">取消</el-button><el-button type="primary" :loading="pending" :disabled="busy || !selectedCase || !form.raw.trim()" data-testid="external-feedback-save" @click="saveCreate">{{ uncertain ? '核对并重试保存' : '保存外部反馈' }}</el-button></template>
    </el-dialog>

    <el-dialog v-model="contextVisible" title="补充这条反馈的提问条件" class="external-feedback-dialog" width="min(760px, calc(100vw - 24px))" top="5vh" append-to-body :close-on-click-modal="false" :close-on-press-escape="false" :before-close="closeContext">
      <p class="external-hint">这是对已保存原文追加的用户声明，原回答、旧声明和未知的模型信息仍保留。</p>
      <blockquote class="external-readback">{{ excerpt(contextProbe?.after?.response, 220) }}</blockquote>
      <el-alert v-if="contextError" :title="contextError" type="warning" :closable="false" show-icon/>
      <p v-if="checkpointError" class="external-hint">{{ checkpointError }} <el-button text @click="exportDrafts">导出填写</el-button></p>
      <el-form label-position="top"><el-form-item label="实际发送的题文"><el-input v-model="contextForm.question" :disabled="contextLocked" type="textarea" :rows="4" :maxlength="4000" aria-label="条件声明实际题文"/></el-form-item>
        <div class="external-fields"><el-form-item label="是否新建空白对话"><el-select v-model="contextForm.newConversation" :disabled="contextLocked" aria-label="条件声明是否新对话"><el-option label="请选择，不确定则暂不补充" :value="'unknown'"/><el-option label="是" :value="true"/><el-option label="否，已有对话" :value="false"/></el-select></el-form-item><el-form-item label="是否为该对话的首条提问"><el-select v-model="contextForm.firstMessage" :disabled="contextLocked" aria-label="条件声明是否首问"><el-option label="请选择，不确定则暂不补充" :value="'unknown'"/><el-option label="是" :value="true"/><el-option label="否" :value="false"/></el-select></el-form-item></div>
        <el-form-item label="你掌握的条件说明"><el-input v-model="contextForm.confirmation" :disabled="contextLocked" type="textarea" :rows="3" :maxlength="3000" placeholder="例如：同事确认新建空白对话，第一条只发了上面的原题。" aria-label="提问条件说明"/></el-form-item>
        <el-checkbox v-model="contextForm.confirmed" :disabled="contextLocked">我确认这些条件来自实际掌握的信息，不确定的部分暂不补充</el-checkbox>
      </el-form><template #footer><el-button text :disabled="contextPending" @click="exportDrafts">导出填写</el-button><el-button :disabled="contextPending" @click="closeContext(() => { contextVisible = false })">取消</el-button><el-button type="primary" :disabled="busy" :loading="contextPending" data-testid="external-context-save" @click="saveContext">{{ contextUncertain ? '核对并重试保存' : '保存条件声明' }}</el-button></template>
    </el-dialog>

    <el-dialog v-model="readVisible" title="已保存的外部回答原文" class="external-feedback-dialog" width="min(760px, calc(100vw - 24px))" top="5vh" append-to-body><p class="external-readback">{{ readProbe?.after?.response }}</p><p class="external-hint">用户提供原文 · {{ journeyDate(readProbe?.created_at) }}。采集过程未由软件核验。</p><div v-if="readProbe?.external_source_links?.length"><h4>用户提供的来源地址（尚未核实）</h4><p v-for="(link,index) in readProbe.external_source_links" :key="index"><a v-if="publicationReviewUrl(link.url)" :href="publicationReviewUrl(link.url)" target="_blank" rel="noopener noreferrer">{{ link.title || link.url }}</a><span v-else>网址不能公开打开，请核对原记录。</span></p></div></el-dialog>
  </section>
</template>

<script setup>
import { computed, onUnmounted, reactive, ref, watch } from 'vue'
import GeoBusinessReview from '@/components/GeoBusinessReview.vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { geoExternalFeedbackApi } from '@/api/geoExternalFeedback'
import { externalFeedbackScope, externalFeedbackCases, emptyExternalFeedback, externalFeedbackPayload, externalContextPayload, externalFeedbackResponse, externalCycleCanApply, externalWriteUncertain } from '@/utils/geoExternalFeedback'
import { journeyDate } from '@/utils/geoJourney'
import { publicationReviewUrl } from '@/utils/geoPublicationReview'
import {feedbackSession,feedbackCheckpointKey,feedbackCapacityMessage,feedbackConflictMessage,exportFeedbackDraft} from '@/utils/geoFeedbackSession'

const props = defineProps({cycle: {type: Object, required: true}, busy: Boolean})
const emit = defineEmits(['updated','busy'])
const scope = computed(() => externalFeedbackScope(props.cycle))
const cases = computed(() => externalFeedbackCases(props.cycle))
const records = computed(() => (Array.isArray(props.cycle?.probes) ? props.cycle.probes : []).filter(row => row?.source === 'user_reported_external' && row.cycle_id === props.cycle.id && row.native_verified === false))
const form = reactive(emptyExternalFeedback()), selectedCase = computed(() => cases.value.find(row => row.id === form.caseId))
const createVisible = ref(false), pending = ref(false), uncertain = ref(false), error = ref(''), copyMessage = ref(''), recordLimit = ref(5)
const locked = computed(() => pending.value || uncertain.value)
const contextVisible = ref(false), contextPending = ref(false), contextUncertain = ref(false), contextError = ref(''), contextProbe = ref(null)
const contextForm = reactive({question: '', newConversation: 'unknown', firstMessage: 'unknown', confirmation: '', confirmed: false})
const contextLocked = computed(() => contextPending.value || contextUncertain.value)
const readVisible = ref(false), readProbe = ref(null)
const checkpointError=ref(''),busyOwner=feedbackSession.owner('external')
let generation = 0, destroyed = false, frozenCreate = null, frozenContext = null
let ownerScope=null,createToken=null,contextToken=null,createVersion=null,contextVersion=null,contextBase=null,checkpointPaused=false
const clone=value=>JSON.parse(JSON.stringify(value))
const checkpointFailure=reason=>{checkpointError.value=reason==='stale'?feedbackConflictMessage:feedbackCapacityMessage;return false}
const retain=(key,value,token,version)=>{
  let result=feedbackSession.write(key,value,token,version)
  if(result.reason==='stale'&&!feedbackSession.read(key))result=feedbackSession.write(key,value)
  if(!result.ok){checkpointFailure(result.reason);return null}
  checkpointError.value='';return result
}
const rememberCreate=(target=ownerScope)=>{
  if(checkpointPaused||!target)return true
  if(!frozenCreate&&!Object.entries(form).some(([key,value])=>value!==emptyExternalFeedback()[key]))return true
  const result=retain(feedbackCheckpointKey('external',target),{scope:target,form:clone(form),frozen:frozenCreate,uncertain:uncertain.value||pending.value},createToken,createVersion)
  if(result===null)return false
  createToken=result.token;createVersion=result.version;return true
}
const rememberContext=(target=ownerScope)=>{
  if(checkpointPaused||!target||!contextProbe.value?.id||contextProbe.value.cycle_id!==target.cycleId)return true
  if(contextToken===null&&!frozenContext&&JSON.stringify(contextForm)===contextBase)return true
  const result=retain(feedbackCheckpointKey('context',target,contextProbe.value.id),{scope:target,form:clone(contextForm),frozen:frozenContext,uncertain:contextUncertain.value||contextPending.value},contextToken,contextVersion)
  if(result===null)return false
  contextToken=result.token;contextVersion=result.version;return true
}
const exportDrafts=()=>exportFeedbackDraft({scope:ownerScope,external:{form:clone(form),frozen:frozenCreate},context:{probe_id:contextProbe.value?.id,form:clone(contextForm),frozen:frozenContext}},'geo-external-feedback-draft')
const sameScope = captured => !destroyed && captured?.generation === generation && scope.value?.cycleId === captured.scope.cycleId && scope.value?.projectId === captured.scope.projectId
const capture = body => ({scope: {...scope.value,plan:body.expected_plan_sha256,revision:body.expected_revision}, generation})
const excerpt = (text, max) => typeof text === 'string' ? text.slice(0, max) + (text.length > max ? '…' : '') : ''
const errorText = exception => exception?.response?.data?.msg || exception?.message || '保存未完成，请先核对原记录。'
const applySavedCycle = value => {
  if (externalCycleCanApply(value, props.cycle)) emit('updated', value)
  else ElMessage.info('反馈已保存，当前资料同时有更新，请刷新后查看完整记录。')
}
const openCreate = () => { if (!props.busy && !pending.value && scope.value) {
  const entry=feedbackSession.read(feedbackCheckpointKey('external',scope.value))
  if(!checkpointError.value&&entry&&(entry.token!==createToken||entry.version!==createVersion)){
    checkpointPaused=true;Object.assign(form,entry.value.form);frozenCreate=entry.value.frozen;uncertain.value=entry.value.uncertain;createToken=entry.token;createVersion=entry.version;checkpointPaused=false
  }
  createVisible.value = true; error.value = ''
} }
const copyQuestion = async () => {
  if (!selectedCase.value) return
  try { await navigator.clipboard.writeText(selectedCase.value.question); copyMessage.value = '已复制，可原样发给对方。' }
  catch { copyMessage.value = '未能复制，请直接选择上方原题文字复制。' }
}
const closeCreate = async done => {
  if (pending.value) return
  if (form.raw.trim()) {
    try { await ElMessageBox.confirm(checkpointError.value|| (uncertain.value ? '保存回执尚未确认。草稿在本次应用会话中保留，回来可核对同一内容；退出软件前请自行保留。' : '草稿在本次应用会话中保留，切换步骤后也可回来继续；退出软件前请先保存或自行保留。'), '关闭录入窗口', {confirmButtonText: '保留草稿并关闭', cancelButtonText: '继续填写'}) }
    catch { return }
  }
  done()
}
const saveCreate = async () => {
  if (pending.value || props.busy || !scope.value) return
  let body
  try { body = frozenCreate || externalFeedbackPayload(props.cycle, form) }
  catch (exception) { error.value = errorText(exception); return }
  const previousFrozen=frozenCreate
  frozenCreate = clone(body)
  if(!rememberCreate()){frozenCreate=previousFrozen;error.value=checkpointError.value;return}
  const captured = capture(body),key=feedbackCheckpointKey('external',captured.scope),token=createToken,originalDraft=clone(form),wasUncertain=uncertain.value
  pending.value = true; error.value = '';rememberCreate()
  try {
    const response = await geoExternalFeedbackApi.create(captured.scope.cycleId, body)
    const value = externalFeedbackResponse(response, captured.scope, body.case_id)
    if (value.probe.after?.response !== body.raw_response_text) throw new Error('保存回执与提交原文不一致，请先核对记录。')
    feedbackSession.confirmed(key,token,body)
    if (!sameScope(captured)) return
    applySavedCycle(value.cycle)
    checkpointPaused=true;Object.assign(form, emptyExternalFeedback()); frozenCreate = null; uncertain.value = false; createVisible.value = false;createToken=null;createVersion=null;checkpointPaused=false
    ElMessage.success('外部回答已保存，原基线保持。')
  } catch (exception) {
    const unknown=wasUncertain||externalWriteUncertain(exception)
    const settled=feedbackSession.settle(key,token,body,{scope:captured.scope,form:originalDraft,frozen:unknown?body:null,uncertain:unknown})
    if (!sameScope(captured)) return
    if(settled)createVersion=feedbackSession.read(key)?.version??createVersion
    uncertain.value = unknown
    if (!uncertain.value) frozenCreate = null
    error.value = uncertain.value ? '保存回执尚未确认。原文保留，重试会核对同一份内容；请勿另建重复反馈。' : errorText(exception)
  } finally { if (sameScope(captured)){pending.value = false;rememberCreate()} }
}
const openContext = probe => {
  if (props.busy || contextPending.value || !scope.value || !records.value.some(row => row.id === probe.id)) return
  const entry=feedbackSession.read(feedbackCheckpointKey('context',scope.value,probe.id)),saved=entry?.value
  contextToken=entry?.token??null;contextVersion=entry?.version??null;checkpointPaused=true
  contextProbe.value = probe; contextError.value = ''; contextUncertain.value = saved?.uncertain || false; frozenContext = saved?.frozen || null
  Object.assign(contextForm, saved?.form || {question: probe.observed_question || '', newConversation: 'unknown', firstMessage: 'unknown', confirmation: '', confirmed: false})
  contextBase=JSON.stringify(contextForm)
  checkpointPaused=false;rememberContext()
  contextVisible.value = true
}
const closeContext = done => { if (!contextPending.value) { rememberContext(); done() } }
const saveContext = async () => {
  if (contextPending.value || props.busy || !scope.value) return
  let body
  try { body = frozenContext || externalContextPayload(props.cycle, contextProbe.value, contextForm) }
  catch (exception) { contextError.value = errorText(exception); return }
  const previousFrozen=frozenContext
  frozenContext=clone(body)
  if(!rememberContext()){frozenContext=previousFrozen;contextError.value=checkpointError.value;return}
  const captured = capture(body), probeId = contextProbe.value.id,key=feedbackCheckpointKey('context',captured.scope,probeId),token=contextToken,wasUncertain=contextUncertain.value
  contextPending.value = true; contextError.value = '';rememberContext()
  try {
    const response = await geoExternalFeedbackApi.context(captured.scope.cycleId, probeId, body)
    const value = externalFeedbackResponse(response, captured.scope, body.case_id)
    if (value.probe.id !== probeId) throw new Error('条件保存回执不属于原反馈，请刷新核对。')
    const statement = value.probe.context_attestations?.at(-1)
    if (value.probe.response_sha256 !== body.expected_response_sha256 || !statement
      || Object.entries(body.attestation).some(([key, content]) => statement[key] !== content)) throw new Error('条件保存回执与这次声明不一致，请先核对原记录。')
    feedbackSession.confirmed(key,token,body)
    if (!sameScope(captured)) return
    applySavedCycle(value.cycle); contextVisible.value = false; contextUncertain.value = false; frozenContext = null
    contextProbe.value=null;contextToken=null;contextVersion=null
    ElMessage.success('提问条件已追加，原回答与旧声明保持。')
  } catch (exception) {
    const unknown=wasUncertain||externalWriteUncertain(exception)
    const settled=feedbackSession.settle(key,token,body,{scope:captured.scope,form:{question:body.attestation.question,newConversation:body.attestation.new_conversation,firstMessage:body.attestation.first_message,confirmation:body.attestation.confirmation_text,confirmed:true},frozen:unknown?body:null,uncertain:unknown})
    if (!sameScope(captured)) return
    if(settled)contextVersion=feedbackSession.read(key)?.version??contextVersion
    contextUncertain.value = unknown
    if (!contextUncertain.value) frozenContext = null
    contextError.value = contextUncertain.value ? '保存回执尚未确认，请保留这条声明并核对同一记录，避免另建反馈。' : errorText(exception)
  } finally { if (sameScope(captured)){contextPending.value = false;rememberContext()} }
}
const openRead = probe => { readProbe.value = probe; readVisible.value = true }
watch(() => feedbackCheckpointKey('external',scope.value), () => {
  rememberCreate();rememberContext();checkpointPaused=true
  generation++; pending.value = false; contextPending.value = false
  createVisible.value = false; contextVisible.value = false; readVisible.value = false
  error.value = ''; contextError.value = ''; copyMessage.value = ''; recordLimit.value = 5
  ownerScope=scope.value?{...scope.value}:null
  const entry=feedbackSession.read(feedbackCheckpointKey('external',ownerScope)),saved=entry?.value
  Object.assign(form, saved?.form || emptyExternalFeedback()); frozenCreate = saved?.frozen || null; uncertain.value = saved?.uncertain || false;createToken=entry?.token??null;createVersion=entry?.version??null
  contextProbe.value=null;contextToken=null;contextVersion=null;contextBase=null;frozenContext=null;contextUncertain.value=false;checkpointPaused=false
},{immediate:true,flush:'sync'})
watch(form,()=>rememberCreate(),{deep:true,flush:'sync'})
watch(contextForm,()=>rememberContext(),{deep:true,flush:'sync'})
watch([pending,contextPending],()=>emit('busy',{owner:busyOwner,active:pending.value||contextPending.value}),{flush:'sync'})
onUnmounted(() => { rememberCreate();rememberContext();destroyed = true; generation++;emit('busy',{owner:busyOwner,active:false});if(checkpointError.value)ElMessage.info(checkpointError.value) })
</script>

<style>
.external-feedback{margin:18px 0;padding:16px 18px;border:1px solid var(--geo-border,#dce6e2);border-radius:10px;background:#f9fbfa;font-size:13px;line-height:1.8}.external-feedback-heading{display:flex;align-items:center;justify-content:space-between;gap:16px}.external-feedback-heading p,.external-hint{margin:5px 0;color:var(--geo-muted,#63736f);font-size:12px;line-height:1.8}.external-feedback summary{cursor:pointer;color:var(--geo-primary,#0a756e)}.external-records{margin-top:14px}.external-record{padding:14px 0;border-top:1px solid var(--geo-border,#dce6e2)}.external-record>div:first-child{display:flex;gap:12px;align-items:center;flex-wrap:wrap}.external-record span{color:var(--geo-muted,#63736f);font-size:12px}.external-record p{white-space:pre-wrap;overflow-wrap:anywhere}.external-actions{display:flex;gap:8px;flex-wrap:wrap}.external-feedback-dialog{display:flex;flex-direction:column;max-height:90vh;box-sizing:border-box}.external-feedback-dialog .el-dialog__body{overflow-y:auto;min-height:0;overscroll-behavior:contain}.external-feedback-dialog .el-dialog__header,.external-feedback-dialog .el-dialog__footer{flex-shrink:0}.external-feedback-dialog .el-select{width:100%}.external-feedback-dialog .el-alert{margin-bottom:14px}.external-original-question{margin:0 0 18px;padding:12px 14px;background:#f2f7f5;border-radius:8px;white-space:pre-wrap;overflow-wrap:anywhere}.external-original-question p{margin:0 0 6px;line-height:1.9}.external-original-question small{font-size:12px;color:var(--geo-muted,#63736f)}.external-optional{margin:14px 0}.external-optional>summary{margin-bottom:14px;color:var(--geo-primary,#0a756e);cursor:pointer}.external-fields{display:grid;grid-template-columns:1fr 1fr;gap:16px}.external-readback{white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.9}.external-feedback-dialog blockquote.external-readback{margin:10px 0 18px;padding:12px 14px;border-left:3px solid var(--geo-border,#dce6e2);background:#f8faf9;font-size:12px}@media(max-width:650px){.external-feedback-heading{align-items:flex-start;flex-direction:column}.external-fields{grid-template-columns:1fr}.external-feedback-dialog .el-dialog__footer{display:flex;flex-wrap:wrap;justify-content:flex-end;gap:8px}.external-feedback-dialog .el-dialog__footer .el-button{margin-left:0}}
</style>
