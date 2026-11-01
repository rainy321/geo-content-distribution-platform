<template>
  <section class="directory-application" aria-label="GrokCode目录申请" data-testid="directory-application">
    <header><div><strong>GrokCode 目录申请</strong><p>单独登记目录线索，由你在平台最终确认；待审核或结果未知都不表示已收录。</p></div><el-button :disabled="loading" @click="openPanel">{{ record ? '查看目录申请' : '目录申请' }}</el-button></header>
    <p v-if="record" class="directory-summary"><el-tag type="info" size="small">{{ directoryStage(record) }}</el-tag><span>{{ evidenceLabel }}{{ record.receipt?.intake_uid ? ` · 回执 ${record.receipt.intake_uid}` : '' }}</span></p>
    <p v-if="error && !visible" class="directory-error" role="alert">{{ error }}</p>
    <el-dialog v-model="visible" class="geo-directory-dialog" modal-class="geo-directory-modal" align-center title="GrokCode 目录申请" width="min(720px, 94vw)" :close-on-click-modal="false" :show-close="!localBusy" :close-on-press-escape="!localBusy">
      <div class="directory-content" tabindex="0" role="region" aria-label="目录申请资料与预览">
      <p class="directory-intro">只填写对客售价与公开联系入口，不上传成本、利润或原表。软件辅助填写；登录或安全验证在同一平台窗口正常完成，最终提交由你在平台确认。</p>
      <p v-if="!supported" class="directory-notice" role="status">{{ supportReason }}</p>
      <p v-if="historyRecord" class="directory-notice">这是本项目此前轮次或目标的原申请，可查看，不能从当前目标重新申请。</p>
      <p v-if="outdatedRecord || contextRefreshAllowed" class="directory-notice">资料或平台配置已更新，原申请仍绑定当时的资料。{{ contextRefreshFrozen ? '此前窗口已受控结束且本次未提交；公开内容保持不变，可采用当前配置后重新审阅。' : '尚未打开平台的草稿，可采用当前配置和下方公开内容后重新核对。' }}</p>
      <div class="directory-toolbar"><el-button :loading="loading" :disabled="localBusy" @click="refresh">刷新原申请</el-button><el-button v-if="dirty" :disabled="localBusy" @click="discardLocalEdits">放弃本机编辑并载入已保存内容</el-button></div>
      <p v-if="dirty" class="directory-hint">本机有未保存编辑，刷新会保留。保存草稿后，再核对实际要公开的内容。</p>
      <p v-if="editConflict" class="directory-notice" role="alert">已保存的资料或目标配置有变化，本机编辑仍保留。请先核对已保存内容，再决定是否重新填写。</p>
      <p v-if="storageWarning" class="directory-notice" role="status">{{ storageWarning }}</p>
      <el-form v-if="!record || record.can_edit || contextRefreshEditable" label-position="top" @submit.prevent="saveDraft">
        <div class="directory-pair"><el-form-item label="服务名称"><el-input v-model="form.name" :disabled="editorDisabled" :maxlength="limits.name" placeholder="填写本次要登记的服务名称"/></el-form-item><el-form-item label="公开服务网址"><el-select v-model="form.url" :disabled="editorDisabled" :fit-input-width="true" placeholder="选择本轮已登记的官方入口"><el-option v-for="url in officialUrls" :key="url" :label="url" :value="url" :title="url"/></el-select><p class="directory-hint">要登记的入口须在本轮客户官方地址中；缺少时先在品牌资料补充。</p></el-form-item></div>
        <el-form-item label="对客价格与计价单位"><el-input v-model="form.rate" :disabled="editorDisabled" type="textarea" :rows="2" :maxlength="limits.rate" show-word-limit placeholder="只填写已确认的对客售价，写清币种、输入/输出及计价单位"/></el-form-item>
        <el-form-item label="公开资料来源"><el-input v-model="form.source" :disabled="editorDisabled" type="textarea" :rows="2" :maxlength="limits.source" placeholder="填写已核对的公开价格页或说明；资料未知请先核对"/></el-form-item>
        <el-form-item label="涉及的模型类型（选一项）"><el-checkbox-group v-model="form.models" :max="1" :disabled="editorDisabled"><el-checkbox v-for="model in modelOptions" :key="model" :value="model">{{ model }}</el-checkbox></el-checkbox-group><p class="directory-hint">这里只选择目录申请类型，不表示模型能力或可用情况已经核验。</p></el-form-item>
        <div class="directory-pair"><el-form-item label="公开联系入口（可选）"><el-input v-model="form.contact" :disabled="editorDisabled" :maxlength="limits.contact" placeholder="公开售后页或联系入口，可留空"/></el-form-item><el-form-item label="补充说明（可选）"><el-input v-model="form.notes" :disabled="editorDisabled" :maxlength="limits.notes" type="textarea" :rows="2"/></el-form-item></div>
      </el-form>
      <section v-if="supported || record" class="directory-snapshot" aria-label="将公开的申请内容">
        <h4>{{ record?.read_only && !contextRefreshEditable ? '本次申请记录的公开内容' : '将公开给平台的内容' }}</h4><dl><template v-for="[key,label] in fieldLabels" :key="key"><dt>{{ label }}</dt><dd>{{ key==='models' ? previewFields.models.join('、') || '尚未选择' : previewFields[key] || '未填写' }}</dd></template></dl>
      </section>
      <details v-if="dirty && record?.read_only && !contextRefreshEditable" class="directory-local-copy"><summary>查看保留的本机未保存编辑</summary><dl><template v-for="[key,label] in fieldLabels" :key="key"><dt>{{ label }}</dt><dd>{{ key==='models' ? form.models.join('、') : form[key] || '未填写' }}</dd></template></dl></details>
      <template v-if="record">
        <p class="directory-status" role="status"><strong>{{ directoryStage(record) }}</strong><br/>{{ stageDetail }}</p>
        <p class="directory-hint">{{ evidenceLabel }}。这条目录申请不计作文章发布或 GEO 成功。</p>
        <section v-if="record.receipt" class="directory-receipt" aria-label="目录申请回执"><h4>已保留的回执</h4><p v-if="record.receipt.intake_uid">回执编号：{{ record.receipt.intake_uid }}</p><p>观察时间：{{ receiptTime(record.receipt) }}</p><p v-if="record.receipt.note">{{ record.receipt.note }}</p><p class="directory-hint">回执接收、公开收录、被搜索或 AI 采用分别核对；不会再次申请。</p></section>
      </template>
      <p v-if="closeRequested" class="directory-notice" role="status">已请求结束原窗口，正在等待读回结果；这条请求本身不代表未提交。</p>
      <p v-else-if="record?.window_open || ownedInspectTask" class="directory-hint">请用“结束当前窗口”请求软件受控结束。直接关闭浏览器或连接中断不代表未提交，是否可继续以原申请读回状态为准。</p>
      <p v-if="record?.can_open && !nativeReady" class="directory-hint">打开平台需要更新后的桌面软件。已保存草稿仍会保留。</p>
      <el-collapse v-if="record?.can_record_external_receipt" v-model="receiptExpanded" class="directory-external"><el-collapse-item name="receipt" title="已经在软件外提交？登记已有回执">
        <p class="directory-hint">只登记这份申请的既有结果，不发送或重复提交。登记来源为用户声明，软件未独立核验。</p>
        <el-form label-position="top" @submit.prevent="saveExternalReceipt">
          <el-form-item label="已有回执编号"><el-input v-model="external.intakeUid" :disabled="receiptDisabled" maxlength="160"/></el-form-item>
          <el-form-item label="当时看到的结果"><el-select v-model="external.status" :disabled="receiptDisabled"><el-option label="平台已接收，待审核" value="submitted_pending_review"/><el-option label="已在目录看到收录（用户登记）" value="accepted"/></el-select></el-form-item>
          <div class="directory-pair"><el-form-item label="观察时间精度"><el-select v-model="external.precision" :disabled="receiptDisabled"><el-option label="精确到分钟" value="minute"/><el-option label="精确到秒" value="second"/></el-select></el-form-item><el-form-item label="实际观察时间（北京时间）"><input v-model="external.observedAt" :disabled="receiptDisabled" :type="external.precision==='second'?'text':'datetime-local'" :placeholder="external.precision==='second'?'2026-11-02T02:31:00':''" step="60" aria-label="已有回执的实际观察时间（北京时间）" class="directory-time"/></el-form-item></div>
          <p class="directory-hint">按实际记录填写，不使用当前保存时间。精确到秒时按示例明确填写秒数，不补秒。</p>
          <el-form-item label="回执来源与核对说明"><el-input v-model="external.note" :disabled="receiptDisabled" maxlength="1000" type="textarea" :rows="2" placeholder="说明在哪个正常平台页面看到回执及如何确认对应本次申请"/></el-form-item>
          <el-checkbox v-model="external.confirmed" :disabled="receiptDisabled">我已核对这是上方申请的真实既有回执；仅作用户登记，不再次申请</el-checkbox>
          <div class="directory-actions"><el-button :loading="savingReceipt" :disabled="!canSaveReceipt" @click="saveExternalReceipt">保存已有回执</el-button></div>
        </el-form>
      </el-collapse-item></el-collapse>
      <p v-if="uncertain" class="directory-notice" role="alert">上次操作的结果尚未确认，请先刷新原申请或查看原平台。软件不会自动重试。</p>
      <p v-if="error" class="directory-error" role="alert">{{ error }}</p>
      </div>
      <template #footer><div class="directory-footer">
        <p v-if="record?.can_review || record?.can_open" class="directory-confirm"><el-checkbox v-model="confirmed" :disabled="localBusy || dirty || editConflict || !currentRecord">我已逐项核对上方公开内容，只含获准公开的售价和联系入口；由我在平台最终确认</el-checkbox></p>
        <p v-if="contextRefreshAllowed" class="directory-confirm"><el-checkbox v-model="contextConfirmed" :disabled="localBusy || loading">采用当前平台配置和上方公开内容，保留原版本为历史并撤销旧确认，再重新核对</el-checkbox></p>
        <div class="directory-actions directory-main-actions" data-testid="directory-primary-actions" role="group" aria-label="目录申请主要操作">
          <el-button v-if="!record || record.can_edit" type="primary" :loading="saving" :disabled="!canSave" @click="saveDraft">保存草稿</el-button>
          <el-button v-if="record?.can_review" :loading="reviewing" :disabled="!canReview" @click="reviewDraft">确认这份申请资料</el-button>
          <el-button v-if="contextRefreshAllowed" :loading="refreshingContext" :disabled="!canRefreshContext" @click="refreshDraftContext">采用当前目标配置重新核对</el-button>
          <el-button v-if="record?.can_open" type="primary" :loading="opening" :disabled="!canOpen" @click="openWindow">打开平台并辅助填写</el-button>
          <el-button v-if="record?.window_open" :loading="checking" :disabled="!canCheck" @click="checkWindow">检查当前窗口</el-button>
          <el-button v-if="record?.window_open || ownedInspectTask" :loading="closing" :disabled="!canClose" @click="closeWindow">结束当前窗口</el-button>
          <el-button v-if="record?.can_inspect" :loading="inspecting" :disabled="!canInspect" @click="inspectRecord">仅打开平台查看</el-button>
          <el-button :disabled="localBusy" @click="visible=false">关闭</el-button>
        </div>
      </div></template>
    </el-dialog>
  </section>
</template>

<script setup>
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { geoDirectoryApplicationsApi, directoryAdapter, directoryEntry, directoryFields, directoryFieldsKey, directoryInitialFields, directoryOfficialUrls,
  directoryFieldsIssue, directoryLimits, directoryScope, directoryScopeKey, directoryProjectRecord, directoryRecordBelongs, directoryRecordCurrent,
  directoryDigest, directoryIdentifier, directoryReceiptTime, directorySafeMessage, directoryStage } from '@/api/geoDirectoryApplications'

const props=defineProps({cycle:{type:Object,required:true},target:{type:Object,required:true},task:{type:Object,default:()=>({})},busy:Boolean,nativeAvailable:Boolean})
const visible=ref(false),loading=ref(false),saving=ref(false),reviewing=ref(false),opening=ref(false),checking=ref(false),closing=ref(false),inspecting=ref(false),savingReceipt=ref(false),refreshingContext=ref(false)
const closeRequestedTask=ref('')
const overview=ref(null),record=ref(null),form=reactive(directoryFields()),dirty=ref(false),confirmed=ref(false),error=ref(''),uncertain=ref(false)
const editConflict=ref(false),storageWarning=ref(''),editorScope=ref(directoryScope(props.cycle,props.target)),editorContext=ref(''),editorBase=ref(null)
const contextConfirmed=ref(false)
const receiptExpanded=ref([]),external=reactive({intakeUid:'',status:'submitted_pending_review',observedAt:'',precision:'minute',note:'',confirmed:false})
let epoch=0,readVersion=0,disposed=false,syncing=false,polling=false,timer,pendingEditor=null
const localBusy=computed(()=>saving.value||reviewing.value||opening.value||checking.value||closing.value||inspecting.value||savingReceipt.value||refreshingContext.value)
const scope=computed(()=>directoryScope(props.cycle,props.target))
const supported=computed(()=>Boolean(scope.value && overview.value?.supported===true && overview.value.adapter===directoryAdapter && directoryDigest(overview.value.context_sha256)))
const supportReason=computed(()=>directorySafeMessage(overview.value?.reason,'目录申请服务尚未就绪，请刷新或更新软件后使用。'))
const limits=computed(()=>({...directoryLimits,...Object.fromEntries(Object.entries(overview.value?.field_limits || {}).filter(([key,value])=>Object.hasOwn(directoryLimits,key)&&Number.isSafeInteger(value)&&value>0&&value<=directoryLimits[key]))}))
const modelOptions=computed(()=>(overview.value?.model_options || []).filter(item=>typeof item==='string' && item.length>0 && item.length<=80))
const officialUrls=computed(()=>directoryOfficialUrls(props.cycle))
const currentRecord=computed(()=>directoryRecordCurrent(record.value,scope.value))
const historyRecord=computed(()=>Boolean(record.value && (record.value.cycle_id!==scope.value?.cycleId || record.value.target_id!==scope.value?.targetId)))
const outdatedRecord=computed(()=>Boolean(record.value && !historyRecord.value && record.value.target_revision!==scope.value?.targetRevision))
const contextRefreshAllowed=computed(()=>Boolean(record.value&&directoryRecordBelongs(record.value,scope.value)&&!historyRecord.value&&record.value.can_refresh_context===true&&
  ['draft','reviewed','closed_not_submitted'].includes(record.value.stage)&&record.value.submit_attempted!==true&&!record.value.submit_intent&&!record.value.receipt&&!record.value.window_open&&Number.isSafeInteger(record.value.session_count)&&record.value.session_count>=0))
const contextRefreshEditable=computed(()=>contextRefreshAllowed.value&&record.value.session_count===0&&['draft','reviewed'].includes(record.value.stage))
const contextRefreshFrozen=computed(()=>contextRefreshAllowed.value&&!contextRefreshEditable.value)
const nativeReady=computed(()=>props.nativeAvailable && typeof window.pywebview?.api?.open_directory_application==='function')
const editorDisabled=computed(()=>props.busy||localBusy.value||loading.value||!supported.value||Boolean(record.value&&!contextRefreshEditable.value&&(!currentRecord.value||record.value.can_edit!==true||record.value.read_only===true||record.value.submit_attempted===true||record.value.submit_intent||record.value.receipt||record.value.session_count!==0||!['draft','reviewed'].includes(record.value.stage)))||editConflict.value&&!contextRefreshEditable.value||uncertain.value)
const canSave=computed(()=>!editorDisabled.value && !editConflict.value && (!record.value||currentRecord.value&&record.value.can_edit===true&&record.value.read_only!==true) && !directoryFieldsIssue(form,limits.value,modelOptions.value) && Boolean(dirty.value||!record.value))
const canRefreshContext=computed(()=>Boolean(supported.value&&contextRefreshAllowed.value&&!props.busy&&!localBusy.value&&!loading.value&&!uncertain.value&&contextConfirmed.value&&!directoryFieldsIssue(contextRefreshFrozen.value?record.value.fields:form,limits.value,modelOptions.value)))
const canReview=computed(()=>Boolean(supported.value&&currentRecord.value&&record.value.can_review===true&&record.value.submit_attempted!==true&&!record.value.submit_intent&&!record.value.receipt&&['draft','reviewed','closed_not_submitted'].includes(record.value.stage)&&!props.busy&&!localBusy.value&&!loading.value&&!dirty.value&&!editConflict.value&&!uncertain.value&&confirmed.value))
const canOpen=computed(()=>Boolean(supported.value&&currentRecord.value&&record.value.can_open===true&&record.value.stage==='reviewed'&&record.value.submit_attempted!==true&&!record.value.submit_intent&&!record.value.receipt&&record.value.reviewed===true&&directoryIdentifier(record.value.open_nonce)&&nativeReady.value&&!props.busy&&!localBusy.value&&!loading.value&&!dirty.value&&!editConflict.value&&!uncertain.value&&confirmed.value))
const ownedWindowTask=computed(()=>Boolean(record.value&&props.task?.status==='running'&&props.task.kind==='directory_application'&&directoryIdentifier(props.task.id)&&props.task.id===record.value.native_task_id&&props.task.cycle_id===scope.value?.cycleId&&props.task.directory_application_id===record.value.id))
const ownedInspectTask=computed(()=>Boolean(directoryProjectRecord(record.value,scope.value)&&props.task?.status==='running'&&props.task.kind==='directory_application_inspect'&&directoryIdentifier(props.task.id)&&props.task.cycle_id===scope.value.cycleId&&props.task.directory_application_id===record.value.id))
const closeRequested=computed(()=>Boolean(closeRequestedTask.value&&props.task?.id===closeRequestedTask.value&&props.task.status==='running'))
const canClose=computed(()=>Boolean(props.nativeAvailable&&typeof window.pywebview?.api?.close_directory_application_session==='function'&&!localBusy.value&&!uncertain.value&&!closeRequested.value&&props.task?.target_id===record.value?.target_id&&
  (ownedWindowTask.value&&directoryRecordBelongs(record.value,scope.value)&&record.value.cycle_id===scope.value.cycleId&&record.value.window_open===true||ownedInspectTask.value&&props.task.progress?.directory_application?.window_open===true)))
const canCheck=computed(()=>Boolean(record.value&&currentRecord.value&&record.value.window_open===true&&directoryIdentifier(record.value.native_task_id)&&props.nativeAvailable&&typeof window.pywebview?.api?.check_directory_application_session==='function'&&!localBusy.value&&(!props.busy||ownedWindowTask.value)))
const canInspect=computed(()=>Boolean(record.value&&directoryProjectRecord(record.value,scope.value)&&record.value.can_inspect===true&&props.nativeAvailable&&typeof window.pywebview?.api?.inspect_directory_application==='function'&&!props.busy&&!localBusy.value))
const receiptDisabled=computed(()=>props.busy||localBusy.value||dirty.value||editConflict.value||uncertain.value||!currentRecord.value)
const canSaveReceipt=computed(()=>Boolean(record.value?.can_record_external_receipt===true&&!record.value.window_open&&!record.value.receipt&&['draft','reviewed','submission_unknown','closed_not_submitted'].includes(record.value.stage)&&!receiptDisabled.value&&external.confirmed&&external.intakeUid.trim()&&external.note.trim()&&directoryReceiptTime(external.observedAt,external.precision)))
const previewFields=computed(()=>record.value?.read_only&&!contextRefreshEditable.value?directoryFields(record.value.fields):directoryFields(form))
const fieldLabels=[['name','服务名称'],['url','公开服务网址'],['rate','对客价格与单位'],['source','公开资料来源'],['models','模型类型'],['contact','公开联系入口'],['notes','补充说明']]
const evidenceLabel=computed(()=>record.value?.evidence_level==='owner_reported'?'用户登记，未独立核验':record.value?.evidence_level==='prepared_only'?'软件内资料草稿，尚未提交':record.value?.evidence_level==='native_observed_receipt'?'软件窗口观察回执，收录另行核对':record.value?.source?.startsWith('software')?'软件辅助操作记录，收录待核对':'记录来源待核对')
const stageDetail=computed(()=>({draft:'先保存并逐项核对公开内容，再打开平台。',reviewed:'已核对保存的这份资料；在平台最终确认前仍可检查。',opening:'请等待同一平台窗口打开，不要再次申请。',
  awaiting_human_verification:'请在原平台窗口正常完成当前登录或安全验证，保持同一窗口，再检查当前窗口。',needs_manual_review:'页面与公开字段尚未完整核对，请先查看原窗口。本提示不能确认提交结果；以原申请和回执为准，不要重新申请。',
  awaiting_human_confirmation:'请在同一平台窗口正常完成验证、核对并最终确认，再返回检查当前窗口。',submitted_pending_review:'平台已接收申请，原回执保留；请等待审核，不要再次申请。',
  submission_unknown:'尚未确认平台是否收到，请先查看原平台与原回执，不要重新申请。',window_closed:'原窗口已结束，不能据此认定未提交；请先查看原平台。',
  closed_not_submitted:'软件已完成受控结束，并确认本次未提交。只能重新核对同一份资料，不能据此另改内容重复申请。',rejected:'请核对平台实际说明。原申请保留，不能直接重新申请。',accepted:'记录已收录状态，仍需按记录来源核对，不表示已被搜索或 AI 采用。'}[record.value?.stage] || '请先刷新原申请并核对平台实际状态。'))
const receiptTime=value=>{
  const date=new Date(value?.observed_at),precision=value?.observed_at_precision
  if(!Number.isFinite(date.getTime())||!['minute','second'].includes(precision))return '时间或精度待核对'
  return new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',...(precision==='second'?{second:'2-digit'}:{}),hour12:false}).format(date)+`（北京时间，精确到${precision==='second'?'秒':'分钟'}）`
}
const cacheKey=value=>value?`geo-directory-editor-v1:${JSON.stringify(value)}`:''
const persistEditor=()=>{
  if(!dirty.value||!editorScope.value)return
  try {window.localStorage?.setItem(cacheKey(editorScope.value),JSON.stringify({fields:directoryFields(form),context:editorContext.value,base:editorBase.value}));storageWarning.value=''}
  catch {storageWarning.value='本机编辑暂未自动保留，请先保存草稿后再关闭。'}
}
const clearCache=()=>{try{window.localStorage?.removeItem(cacheKey(editorScope.value))}catch{ /* The server draft is authoritative after an acknowledged save. */ }}
const syncEditor=()=>{
  syncing=true;Object.assign(form,record.value?directoryFields(record.value.fields):directoryInitialFields(props.cycle));syncing=false;dirty.value=false;confirmed.value=false;contextConfirmed.value=false
  editorContext.value=overview.value?.context_sha256 || '';editorBase.value=record.value?{id:record.value.id,revision:record.value.revision}:null
  editConflict.value=false;clearCache()
}
const restoreEditor=()=>{
  try {
    const cached=JSON.parse(window.localStorage?.getItem(cacheKey(editorScope.value)) || 'null')
    if(!cached || !directoryDigest(cached.context) || !cached.fields || Object.keys(cached.fields).sort().join(',')!=='contact,models,name,notes,rate,source,url')return
    if(Object.entries(cached.fields).some(([key,value])=>key==='models'?!Array.isArray(value)||value.length>1:typeof value!=='string'||value.length>directoryLimits[key]*2))return
    syncing=true;Object.assign(form,directoryFields(cached.fields));syncing=false;dirty.value=true;editorContext.value=cached.context;editorBase.value=cached.base || null
  } catch { /* An unavailable or malformed local copy cannot replace a server record. */ }
}
const acceptOverview=value=>{
  if(!scope.value || value?.adapter!==directoryAdapter || value.entry_url!==directoryEntry || !Array.isArray(value.applications) || value.applications.length>1 ||
    value.applications.some(item=>!directoryProjectRecord(item,scope.value)))throw new Error('目录申请资料与当前项目或目标不一致，请刷新原申请。')
  const next=value.applications[0] || null
  const authorizationChanged=record.value?.stage!==next?.stage||record.value?.reviewed!==next?.reviewed||record.value?.open_nonce!==next?.open_nonce
  overview.value=value;record.value=next
  if(!dirty.value && (authorizationChanged||editorContext.value!==value.context_sha256||JSON.stringify(editorBase.value)!==JSON.stringify(next?{id:next.id,revision:next.revision}:null)||directoryFieldsKey(form)!==directoryFieldsKey(next?next.fields:directoryInitialFields(props.cycle))))syncEditor()
  else if(editorContext.value!==value.context_sha256 || JSON.stringify(editorBase.value)!==JSON.stringify(next?{id:next.id,revision:next.revision}:null))editConflict.value=true
}
const refreshOverview=async()=>{
  const selected=directoryScopeKey(props.cycle,props.target),version=epoch,read=++readVersion
  if(!scope.value||disposed)return false
  const result=await geoDirectoryApplicationsApi.list(scope.value.cycleId,scope.value.targetId)
  if(disposed||version!==epoch||read!==readVersion||selected!==directoryScopeKey(props.cycle,props.target))return false
  acceptOverview(result.data);return true
}
const refresh=async()=>{
  if(loading.value||localBusy.value||!scope.value)return
  const version=epoch;loading.value=true;error.value=''
  try {if(await refreshOverview()&&!disposed&&version===epoch)uncertain.value=false}
  catch(cause){if(version===epoch&&!disposed)error.value=directorySafeMessage(cause)}
  finally {if(version===epoch&&!disposed)loading.value=false}
}
const openPanel=()=>{visible.value=true;if(!overview.value)void refresh()}
defineExpose({openPanel})
const discardLocalEdits=async()=>{
  if(localBusy.value||loading.value)return
  const version=epoch;loading.value=true;error.value=''
  try {if(await refreshOverview()&&version===epoch&&!disposed)syncEditor()}
  catch(cause){if(version===epoch&&!disposed)error.value=directorySafeMessage(cause)}
  finally{if(version===epoch&&!disposed)loading.value=false}
}
const mutation=async(action)=>{
  const selected=directoryScopeKey(props.cycle,props.target),version=epoch
  error.value=''
  try {await action(()=>!disposed&&version===epoch&&selected===directoryScopeKey(props.cycle,props.target))}
  catch(cause){if(!disposed&&version===epoch){uncertain.value=true;error.value=directorySafeMessage(cause)}}
}
const setRecord=(value,expectedFields,expectedId)=>{
  if(!directoryRecordCurrent(value,scope.value) || expectedFields && directoryFieldsKey(value.fields)!==expectedFields || expectedId && value.id!==expectedId)throw new Error('保存结果与当前申请不一致，请刷新原申请。')
  record.value=value;overview.value={...overview.value,applications:[value]}
  if(!dirty.value)editorBase.value={id:value.id,revision:value.revision}
}
const saveDraft=async()=>{
  if(!canSave.value)return
  const version=epoch,selected=scope.value,fields=directoryFields(form)
  for(const key of ['name','url','rate','source','contact','notes'])fields[key]=fields[key].trim()
  const payload=Object.freeze({target_id:selected.targetId,expected_context_sha256:editorContext.value,fields,
    ...(record.value?{application_id:record.value.id,revision:record.value.revision}:{})})
  saving.value=true
  await mutation(async current=>{const result=await geoDirectoryApplicationsApi.save(selected.cycleId,payload);if(!current())return;setRecord(result.data,directoryFieldsKey(fields),payload.application_id);syncEditor();uncertain.value=false})
  if(version===epoch&&!disposed)saving.value=false
}
const reviewDraft=async()=>{
  if(!canReview.value)return
  const version=epoch,selected=scope.value,opened=record.value,fieldsKey=directoryFieldsKey(opened.fields)
  reviewing.value=true
  await mutation(async current=>{
    const result=await geoDirectoryApplicationsApi.review(selected.cycleId,opened.id,{expected_revision:opened.revision,expected_review_sha256:opened.review_sha256,confirmed:true})
    if(!current())return;if(result.data?.reviewed!==true)throw new Error('尚未确认资料核对结果，请刷新原申请。');setRecord(result.data,fieldsKey,opened.id);uncertain.value=false
  })
  if(version===epoch&&!disposed)reviewing.value=false
}
const refreshDraftContext=async()=>{
  if(!canRefreshContext.value)return
  const version=epoch,selected=scope.value,opened=record.value,frozen=contextRefreshFrozen.value,fields=directoryFields(frozen?opened.fields:form),retainLocalEdits=frozen&&dirty.value
  if(!frozen)for(const key of ['name','url','rate','source','contact','notes'])fields[key]=fields[key].trim()
  const payload={expected_revision:opened.revision,expected_review_sha256:opened.review_sha256,
    expected_context_sha256:overview.value.context_sha256,confirmed:true,fields}
  refreshingContext.value=true
  await mutation(async current=>{
    const result=await geoDirectoryApplicationsApi.refreshContext(selected.cycleId,opened.id,payload);if(!current())return
    if(result.data?.stage!=='draft'||result.data?.reviewed!==false||result.data?.open_nonce)throw new Error('尚未确认新资料已重新载入，请刷新原申请。')
    if(frozen&&(result.data.session_count!==opened.session_count||result.data.can_edit!==false||result.data.read_only!==true))throw new Error('申请原公开内容的只读状态尚未确认，请刷新原申请。')
    setRecord(result.data,directoryFieldsKey(fields),opened.id)
    if(retainLocalEdits){confirmed.value=false;contextConfirmed.value=false;editConflict.value=true;persistEditor()}else syncEditor()
    uncertain.value=false
  })
  if(version===epoch&&!disposed)refreshingContext.value=false
}
const windowAction=async kind=>{
  if(kind==='open'&&!canOpen.value||kind==='check'&&!canCheck.value||kind==='inspect'&&!canInspect.value)return
  const slot=kind==='open'?opening:kind==='check'?checking:inspecting,version=epoch,selected=scope.value,opened=record.value
  slot.value=true
  await mutation(async current=>{
    const api=window.pywebview.api
    const result=kind==='open'?await api.open_directory_application(selected.cycleId,opened.id,opened.review_sha256,opened.open_nonce):
      kind==='check'?await api.check_directory_application_session(opened.native_task_id):await api.inspect_directory_application(selected.cycleId,opened.id)
    if(!current())return
    if(result?.error)throw new Error(result.error)
    if(!directoryIdentifier(result?.id)||result.status!=='running'||kind==='check'&&result.id!==opened.native_task_id||
      kind!=='check'&&(result.kind!==(kind==='open'?'directory_application':'directory_application_inspect')||
        result.cycle_id!==selected.cycleId||result.directory_application_id!==opened.id))throw new Error('窗口操作结果尚未确认，请先刷新原申请，不要重复打开。')
    await refreshOverview();if(current())uncertain.value=false
  })
  if(version===epoch&&!disposed)slot.value=false
}
const openWindow=()=>windowAction('open')
const checkWindow=()=>windowAction('check')
const inspectRecord=()=>windowAction('inspect')
const closeWindow=async()=>{
  if(!canClose.value)return
  const version=epoch,nativeId=props.task.id
  closing.value=true
  await mutation(async current=>{
    const result=await window.pywebview.api.close_directory_application_session(nativeId);if(!current())return
    if(result?.error)throw new Error(result.error)
    if(result?.id!==nativeId||result.status!=='running'||result.close_requested!==true)throw new Error('窗口结束请求尚未确认，请刷新原申请；不能据此认定未提交。')
    closeRequestedTask.value=nativeId
    await refreshOverview();if(current())uncertain.value=false
  })
  if(version===epoch&&!disposed)closing.value=false
}
const saveExternalReceipt=async()=>{
  if(!canSaveReceipt.value)return
  const version=epoch,selected=scope.value,opened=record.value,fieldsKey=directoryFieldsKey(opened.fields)
  const payload=Object.freeze({expected_revision:opened.revision,expected_review_sha256:opened.review_sha256,confirmed:true,
    status:external.status,intake_uid:external.intakeUid.trim(),observed_at:directoryReceiptTime(external.observedAt,external.precision),
    observed_at_precision:external.precision,note:external.note.trim()})
  savingReceipt.value=true
  await mutation(async current=>{
    const result=await geoDirectoryApplicationsApi.externalReceipt(selected.cycleId,opened.id,payload);if(!current())return
    const saved=result.data,receipt=saved?.receipt
    if(!receipt||receipt.intake_uid!==payload.intake_uid||receipt.observed_at_precision!==payload.observed_at_precision||
      Date.parse(receipt.observed_at)!==Date.parse(payload.observed_at)||receipt.note!==payload.note||saved.stage!==payload.status||saved.evidence_level!=='owner_reported')throw new Error('尚未确认回执已保存，请刷新原申请。')
    setRecord(saved,fieldsKey,opened.id);external.confirmed=false;receiptExpanded.value=[];uncertain.value=false
  })
  if(version===epoch&&!disposed)savingReceipt.value=false
}
const poll=async()=>{
  if(disposed||polling||loading.value||localBusy.value||!scope.value||!visible.value&&!record.value?.window_open)return
  polling=true
  try {await refreshOverview()}catch { /* Polling never retries a write or replaces a local edit. */ }
  finally {polling=false}
}
watch(()=>directoryFieldsKey(form),()=>{if(syncing)return;dirty.value=true;confirmed.value=false;contextConfirmed.value=false;external.confirmed=false;persistEditor()},{flush:'sync'})
watch(()=>[external.precision,external.status,external.intakeUid,external.observedAt,external.note],()=>{external.confirmed=false},{flush:'sync'})
watch(()=>directoryScopeKey(props.cycle,props.target),()=>{
  const nextScope=scope.value,candidate=dirty.value&&editorScope.value?{scope:editorScope.value,fields:directoryFields(form),context:editorContext.value,base:editorBase.value,visible:visible.value}:pendingEditor
  const retain=Boolean(candidate&&nextScope&&['cycleId','projectId','targetId'].every(key=>candidate.scope[key]===nextScope[key]))
  persistEditor();pendingEditor=nextScope?null:candidate;epoch++;readVersion++;visible.value=retain?candidate.visible:false;overview.value=null;record.value=null;editorScope.value=nextScope
  for(const flag of [loading,saving,reviewing,opening,checking,closing,inspecting,savingReceipt,refreshingContext,confirmed,contextConfirmed,uncertain])flag.value=false
  closeRequestedTask.value=''
  dirty.value=retain;editorContext.value=retain?candidate.context:'';editorBase.value=retain?candidate.base:null
  syncing=true;Object.assign(form,retain?candidate.fields:directoryFields());syncing=false
  editConflict.value=Boolean(retain);error.value='';storageWarning.value=''
  Object.assign(external,{intakeUid:'',status:'submitted_pending_review',observedAt:'',precision:'minute',note:'',confirmed:false});receiptExpanded.value=[]
  if(retain)persistEditor();else restoreEditor();void refresh()
},{flush:'sync'})
onMounted(()=>{restoreEditor();void refresh();timer=setInterval(poll,1800)})
onUnmounted(()=>{persistEditor();disposed=true;epoch++;readVersion++;clearInterval(timer)})
</script>

<style scoped>
:global(.geo-directory-modal .el-overlay-dialog){display:flex;align-items:center;justify-content:center;box-sizing:border-box;padding:16px;overflow:hidden}
:global(.geo-directory-modal .geo-directory-dialog){display:flex;flex-direction:column;min-width:0;max-width:calc(100vw - 32px);max-height:calc(100vh - 32px);max-height:calc(100dvh - 32px);margin:0;padding:0;overflow:hidden}
:global(.geo-directory-modal .geo-directory-dialog .el-dialog__header){flex:0 0 auto;margin:0;padding:16px 20px 12px;border-bottom:1px solid var(--el-border-color-lighter,#ebeef5)}
:global(.geo-directory-modal .geo-directory-dialog .el-dialog__body){flex:1 1 auto;min-height:0;overflow-y:auto;overflow-x:hidden;overscroll-behavior:contain;padding:16px 20px}
:global(.geo-directory-modal .geo-directory-dialog .el-dialog__footer){display:block;flex:0 0 auto;min-width:0;padding:12px 20px 16px;border-top:1px solid var(--el-border-color-lighter,#ebeef5);text-align:left}
.directory-footer{min-width:0;font-size:13px;line-height:1.7}.directory-footer .directory-confirm{margin:0 0 10px}.directory-footer :deep(.el-checkbox){height:auto;min-height:28px;max-width:100%;margin-right:0}.directory-footer :deep(.el-checkbox__label){white-space:normal;line-height:1.7;overflow-wrap:anywhere}.directory-footer .directory-main-actions{justify-content:flex-end;margin:0}.directory-main-actions :deep(.el-button){max-width:100%;min-width:0;white-space:normal;height:auto;min-height:34px;overflow-wrap:anywhere}
@media(max-width:650px){.directory-main-actions{display:grid!important;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}.directory-main-actions :deep(.el-button){width:100%;margin:0}.directory-footer{font-size:12px}:global(.geo-directory-modal .geo-directory-dialog .el-dialog__header){padding:14px 16px 10px}:global(.geo-directory-modal .geo-directory-dialog .el-dialog__body){padding:14px 16px}:global(.geo-directory-modal .geo-directory-dialog .el-dialog__footer){padding:10px 16px 14px}}
.directory-application{margin:14px 0;padding:16px;border:1px solid var(--geo-border,#dce6e2);border-radius:10px;background:var(--geo-surface,#fff);font-size:13px;line-height:1.8}.directory-application header,.directory-summary,.directory-actions,.directory-toolbar{display:flex;align-items:center;gap:10px;flex-wrap:wrap}.directory-application header{justify-content:space-between}.directory-application header p,.directory-hint,.directory-intro{margin:7px 0;color:var(--geo-muted,#63736f);font-size:12px;line-height:1.8}.directory-summary{margin:10px 0 0}.directory-summary span{overflow-wrap:anywhere}.directory-content{font-size:13px;line-height:1.8;min-width:0;overflow-wrap:anywhere}.directory-pair{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:0 18px}.directory-toolbar{margin:14px 0}.directory-toolbar .el-button,.directory-actions .el-button{margin-left:0}.directory-snapshot,.directory-receipt{padding:14px 16px;border:1px solid var(--geo-border,#dce6e2);border-radius:8px;margin:16px 0;background:var(--geo-surface,#fff)}.directory-snapshot h4,.directory-receipt h4{margin:0 0 10px;font-size:14px}.directory-content dl{margin:0;display:grid;grid-template-columns:140px minmax(0,1fr);gap:10px 16px;font-size:13px}.directory-content dt{color:var(--geo-muted,#63736f)}.directory-content dd{margin:0;white-space:pre-wrap;overflow-wrap:anywhere}.directory-notice,.directory-status{margin:12px 0;padding:12px 14px;border-radius:7px;background:#fff7ec;color:#825c2b;overflow-wrap:anywhere}.directory-status{background:#f3f8f5;color:var(--geo-ink,#173e3b)}.directory-error{margin:12px 0;color:#a6412d;overflow-wrap:anywhere}.directory-actions{margin:14px 0}.directory-time{width:100%;box-sizing:border-box;max-width:100%;min-width:0;padding:8px 12px;border:1px solid var(--el-border-color,#dcdfe6);border-radius:4px;background:var(--el-fill-color-blank,#fff);color:var(--el-text-color-regular,#606266);font:inherit}.directory-content :deep(.el-select){width:100%}.directory-content :deep(.el-checkbox){height:auto;min-height:32px;max-width:100%}.directory-content :deep(.el-checkbox__label){white-space:normal;line-height:1.7}.directory-local-copy{margin:12px 0}.directory-local-copy summary{cursor:pointer;color:var(--geo-primary,#0a756e)}.directory-external{margin-top:18px}:deep(.el-dialog__footer){display:flex;justify-content:flex-end;flex-wrap:wrap;gap:8px}:deep(.el-dialog__footer .el-button){margin:0}@media(max-width:650px){.directory-pair{grid-template-columns:1fr}.directory-content dl{grid-template-columns:1fr;gap:3px}.directory-content dd{margin-bottom:10px}.directory-actions{align-items:stretch;flex-direction:column}.directory-actions .el-button{white-space:normal;height:auto;min-height:34px}}@media(prefers-reduced-motion:reduce){.directory-application *,.directory-content :deep(*),.directory-footer :deep(*){scroll-behavior:auto;transition:none!important;animation:none!important}}
</style>
