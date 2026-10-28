<template>
  <aside class="external-baseline-entry" aria-label="手机首轮观察" data-testid="external-baseline-entry">
    <div><strong>普通网页暂不可用，也能先记录手机回答</strong><p>先固定本轮原题，再保存你实际取得的原文。用户提供的观察与软件采集分开。</p></div>
    <el-button plain :disabled="busy || blocked || !project?.id" data-testid="external-baseline-open" @click="open">在手机记录首轮回答</el-button>
    <el-dialog v-model="visible" title="在手机记录首轮回答" class="external-baseline-dialog" width="min(800px, calc(100vw - 24px))" top="12px" append-to-body :close-on-click-modal="false" :close-on-press-escape="false" :before-close="close">
      <ol class="external-baseline-steps" aria-label="记录步骤"><li :aria-current="!frozenPlan ? 'step' : undefined">核对并固定原题</li><li :aria-current="frozenPlan && !form.raw.trim() ? 'step' : undefined">在手机提问</li><li :aria-current="frozenPlan && form.raw.trim() ? 'step' : undefined">保存实际原文</li></ol>
      <p class="external-baseline-hint">{{ project?.name }} · {{ project?.website || '官网未提供' }}</p>
      <el-alert v-if="error || checkpointError" :title="error || checkpointError" type="warning" :closable="false" show-icon/>
      <p v-if="notice" class="external-baseline-hint" role="status">{{ notice }}</p>
      <p v-if="uncertain" class="external-baseline-warning">保存结果尚未确认，原请求已保留。重试只核对同一次软件保存，不会向豆包发送问题。</p>
      <template v-if="!frozenPlan">
        <details class="external-baseline-restore"><summary>继续已固定原题</summary><p class="external-baseline-hint">手机测试中途关闭软件后，可以继续同一份原题；不会再次固定或替换当前填写。</p><el-button text :disabled="Boolean(pending)" @click="readStored">读取这个项目的已固定原题</el-button>
          <p v-if="storedRead && !storedPlans.length" class="external-baseline-hint">这个项目没有尚待录入首条回答的已固定原题。</p>
          <ul v-if="storedPlans.length" class="external-baseline-stored"><li v-for="item in storedPlans" :key="item.id"><span>{{ journeyDate(item.created_at) }} · {{ item.profile.name }} · {{ item.plan.cases.length }} 题{{ item.current_profile_matches ? '' : ' · 资料已变化，只能查看' }}</span><el-button text :disabled="Boolean(pending)" @click="chooseStored(item)">查看并继续这份原题</el-button></li></ul>
          <el-button v-if="storedNext!==null" text :disabled="Boolean(pending)" @click="readStored(storedNext)">再显示20份</el-button>
        </details>
        <div class="external-baseline-plan-choice"><span>准备新的原题</span><el-radio-group v-model="useExisting" :disabled="locked || Boolean(prepared)"><el-radio-button :value="false">固定问题模板</el-radio-button><el-radio-button v-if="candidateAvailable" :value="true">沿用页面已有计划</el-radio-button></el-radio-group></div>
        <p class="external-baseline-hint">{{ useExisting ? '已有计划会重新核对品牌范围；它是用户提供的原题，生成来源未独立核验。' : '固定问题模板，未调用AI。行业资料不足时题目较通用，请核对是否适合当前业务。' }}</p>
        <el-button v-if="!prepared" :loading="pending==='prepare'" :disabled="locked" @click="prepare">准备并查看原题</el-button>
        <template v-if="prepared"><article v-for="item in prepared.plan.cases" :key="item.id" class="external-baseline-question"><strong>{{ item.id }} · {{ category(item.category) }}</strong><p>{{ item.question }}</p></article><p class="external-baseline-hint">固定后才开放复制和录入。修改题目需要另准备一轮，不会改写这份原题。</p></template>
      </template>
      <template v-else>
        <p class="external-baseline-hint">原题已固定并保存到软件 · {{ frozenPlan.plan.planner.method==='template_not_ai' ? '固定问题模板，未调用AI' : '用户提供的原题，生成来源未核验' }}</p>
        <p v-if="frozenPlan.current_profile_matches===false" class="external-baseline-warning">当前品牌资料与这份原题不同。旧题可查看，不能向这一轮保存新原文；请保留原资料或另准备一轮。</p>
        <el-form label-position="top" @submit.prevent="submit">
          <el-form-item label="这条回答对应哪道原题"><el-select v-model="form.caseId" :disabled="locked" aria-label="手机首轮关联原题"><el-option v-for="item in frozenPlan.plan.cases" :key="item.id" :label="`${item.id} · ${category(item.category)}`" :value="item.id"/></el-select></el-form-item>
          <div v-if="selectedCase" class="external-baseline-question"><strong>{{ selectedCase.id }} · {{ category(selectedCase.category) }}</strong><p>{{ selectedCase.question }}</p><el-button text :disabled="Boolean(pending) || copyPending" @click="copyQuestion">复制这道原题</el-button><small role="status">{{ copyMessage }}</small><p class="external-baseline-hint">{{ frozenPlan.case_markers[selectedCase.id].question_with_url ? '本题原文含网址或官网域名提示，不能作为无网址提示的独立观察。' : '原题文字未包含已登记网址；是否还提供了额外背景、新对话及首条条件仍需用户明确说明。' }}</p></div>
          <p class="external-baseline-hint">请在手机中自行提问，再粘贴实际回答。复制原题不会自动证明实际同题或独立采样条件。</p>
          <el-form-item label="实际回答原文"><el-input v-model="form.raw" type="textarea" :rows="8" :disabled="locked" aria-label="手机首轮回答原文" placeholder="保留完整原文、否定结论及来源文字；不要填写密码或验证码。"/></el-form-item>
          <details class="external-baseline-optional"><summary>补充实际采样条件（不知道就保持未知）</summary>
            <el-form-item label="实际发送的题文"><el-radio-group v-model="form.questionMode" :disabled="locked"><el-radio-button value="unknown">未提供</el-radio-button><el-radio-button value="same">确认与原题完全相同</el-radio-button><el-radio-button value="custom">粘贴实际题文</el-radio-button></el-radio-group></el-form-item>
            <el-input v-if="form.questionMode==='custom'" v-model="form.question" type="textarea" :rows="3" :maxlength="4000" :disabled="locked" aria-label="手机实际提问题文"/>
            <div class="external-baseline-fields"><el-form-item label="实际界面"><el-select v-model="form.clientMode" :disabled="locked" aria-label="手机实际界面"><el-option label="未知" value="unknown"/><el-option label="普通豆包对话（用户确认）" value="consumer"/><el-option label="豆包工作（用户确认）" value="work"/></el-select></el-form-item><el-form-item label="回答完整性"><el-select v-model="form.completeness" :disabled="locked" aria-label="手机回答完整性"><el-option label="未知" value="unknown"/><el-option label="确认是完整最终回答" value="complete"/><el-option label="只取得部分回答" value="partial"/></el-select></el-form-item></div>
            <div class="external-baseline-fields"><el-form-item label="模型名称（可选）"><el-input v-model="form.model" :maxlength="100" :disabled="locked" placeholder="未知就留空"/></el-form-item><el-form-item label="实际取得时间（可选）"><el-input v-model="form.collectedAt" type="datetime-local" :disabled="locked"/></el-form-item></div>
            <el-form-item label="回答附带的来源链接（可选）"><el-input v-model="form.sourceLinks" type="textarea" :rows="2" :disabled="locked" placeholder="一行一个公开网址；只有来源名称时留空，不能猜网址。"/></el-form-item>
            <el-form-item label="备注（可选）"><el-input v-model="form.note" type="textarea" :rows="2" :maxlength="1000" :disabled="locked"/></el-form-item>
          </details>
        </el-form>
        <p class="external-baseline-hint">本次先保存原文。是否空白新对话、首条提问等独立条件仍未知，保存后可在“补充提问条件”登记用户声明。</p>
      </template>
      <p class="external-baseline-hint">记录来自用户提供，软件未独立核验。缺失条件不算成立，不进入原生提及率，也不证明收录、引用或GEO效果。未保存原文只在本次应用内保留，关闭软件前请导出。</p>
      <template #footer>
        <div v-if="prepared && !frozenPlan" class="external-baseline-confirmation" data-testid="external-baseline-confirmation"><el-checkbox v-model="confirmed" :disabled="locked">已核对这些问题适合当前对象，确认固定本轮原题</el-checkbox></div>
        <div class="external-baseline-footer"><el-button v-if="(prepared || frozenPlan) && !form.raw && !uncertain" text :disabled="busy || blocked || Boolean(pending)" data-testid="external-baseline-reselect" @click="backToPlans">重新选择原题</el-button><el-button text :disabled="Boolean(pending)" @click="exportDraft">导出当前填写</el-button><el-button :disabled="Boolean(pending)" @click="close">暂存并关闭</el-button><el-button v-if="frozenPlan || prepared" type="primary" :loading="pending==='freeze'||pending==='create'" :disabled="busy || blocked || Boolean(pending) || frozenPlan?.current_profile_matches===false || (!frozenPlan && !confirmed) || (frozenPlan && (!selectedCase || !form.raw.trim()))" data-testid="external-baseline-save" @click="submit">{{ uncertain ? '核对并重试同一次保存' : frozenPlan ? '保存手机观察并继续' : '确认并固定原题' }}</el-button></div>
      </template>
    </el-dialog>
  </aside>
</template>

<script setup>
import {computed,onUnmounted,reactive,ref,watch} from 'vue'
import {ElMessage} from 'element-plus'
import {geoExternalBaselineApi} from '@/api/geoExternalBaseline'
import {emptyExternalFeedback,externalWriteUncertain} from '@/utils/geoExternalFeedback'
import {externalBaselineSession,externalBaselineDraftKey,externalPrepareBody,externalPrepared,externalFreezeBody,externalFreezeReceipt,externalCreateBody,externalCreateReceipt,externalOperationSha,externalStoredFrozen,ExternalBaselineInputError,externalBaselineError} from '@/utils/geoExternalBaseline'
import {exportFeedbackDraft,feedbackCapacityMessage,feedbackConflictMessage} from '@/utils/geoFeedbackSession'
import {sameBrand,journeyDate} from '@/utils/geoJourney'
const props=defineProps({project:Object,plan:Object,busy:Boolean,blocked:Boolean})
const emit=defineEmits(['created','busy'])
const visible=ref(false),pending=ref(''),prepared=ref(null),frozenPlan=ref(null),confirmed=ref(false),useExisting=ref(false),uncertain=ref(false)
const form=reactive(emptyExternalFeedback()),error=ref(''),notice=ref(''),copyMessage=ref(''),copyPending=ref(false),checkpointError=ref('')
const storedPlans=ref([]),storedRead=ref(false),storedNext=ref(null)
const candidateAvailable=computed(()=>Boolean(props.plan&&sameBrand(props.plan.profile,props.project)))
const selectedCase=computed(()=>frozenPlan.value?.plan.cases.find(row=>row.id===form.caseId)||null)
const locked=computed(()=>Boolean(pending.value)||uncertain.value||props.busy||props.blocked||frozenPlan.value?.current_profile_matches===false)
const owner=externalBaselineSession.owner('baseline'),clone=value=>JSON.parse(JSON.stringify(value))
const category=value=>({discovery:'自然选型',brand_identity:'品牌核验',website_guided:'官网核验'}[value]||'待核对题型')
const message=externalBaselineError
let generation=0,destroyed=false,paused=false,ownerKey=null,token=null,version=null,attempt=null,copySequence=0
const scopeKey=()=>externalBaselineDraftKey(props.project,props.plan)
const active=(epoch,key)=>!destroyed&&generation===epoch&&scopeKey()===key
const snapshot=()=>({prepared:clone(prepared.value),frozenPlan:clone(frozenPlan.value),form:clone(form),confirmed:confirmed.value,useExisting:useExisting.value,
  frozen:clone(attempt),uncertain:uncertain.value||Boolean(attempt&&['freeze','create'].includes(pending.value))})
const remember=()=>{
  if(paused||!ownerKey||(!prepared.value&&!frozenPlan.value&&!attempt&&!form.raw))return true
  const prior=externalBaselineSession.read(ownerKey)
  if(prior&&token===null){checkpointError.value=feedbackConflictMessage;return false}
  let next=externalBaselineSession.write(ownerKey,snapshot(),token,version)
  if(next.reason==='stale'&&!externalBaselineSession.read(ownerKey))next=externalBaselineSession.write(ownerKey,snapshot())
  if(!next.ok){checkpointError.value=next.reason==='capacity'?feedbackCapacityMessage:feedbackConflictMessage;return false}
  token=next.token;version=next.version;checkpointError.value='';return true
}
const open=()=>{if(props.busy||props.blocked||!props.project?.id)return;visible.value=true;error.value='';notice.value=''}
const close=done=>{if(pending.value)return;remember();visible.value=false;if(typeof done==='function')done()}
const prepare=async()=>{
  if(pending.value||uncertain.value||props.busy||props.blocked)return
  const epoch=generation,key=scopeKey(),project=clone(props.project)
  pending.value='prepare';error.value=''
  try{const body=externalPrepareBody(project,props.plan,useExisting.value),value=await externalPrepared(project,await geoExternalBaselineApi.prepare(body));if(!active(epoch,key))return;prepared.value=value;confirmed.value=false;remember()}
  catch(cause){if(active(epoch,key))error.value=message(cause)}finally{if(active(epoch,key))pending.value=''}
}
const readStored=async(offset=0)=>{
  if(pending.value||props.busy||props.blocked)return
  if(typeof offset!=='number')offset=0
  const epoch=generation,key=scopeKey(),project=clone(props.project);pending.value='read';error.value=''
  try{
    const reply=await geoExternalBaselineApi.list(project.id,offset),value=reply?.data
    if(reply?.code!==200||!Array.isArray(value?.plans)||value.plans.length>20||typeof value.has_more!=='boolean'
      ||value.has_more&&(!Number.isSafeInteger(value.next_offset)||value.next_offset!==offset+20))throw new ExternalBaselineInputError('已固定原题列表未核对。')
    const rows=await Promise.all(value.plans.map(item=>externalStoredFrozen(project,item)))
    if(!active(epoch,key))return
    storedPlans.value=offset?[...storedPlans.value,...rows]:rows;storedRead.value=true;storedNext.value=value.has_more?value.next_offset:null
  }catch(cause){if(active(epoch,key))error.value=message(cause)}finally{if(active(epoch,key))pending.value=''}
}
const chooseStored=item=>{
  if(pending.value||uncertain.value||props.busy||props.blocked)return
  if(form.raw||attempt||frozenPlan.value){error.value='当前填写保留，请先导出并继续这一轮；不能用另一份原题覆盖。';return}
  paused=true;frozenPlan.value=clone(item);prepared.value=null;confirmed.value=true;form.caseId='';paused=false;remember()
}
const backToPlans=()=>{
  if(pending.value||uncertain.value||form.raw||attempt||props.busy||props.blocked)return
  const prior=externalBaselineSession.read(ownerKey)
  if(prior&&(prior.token!==token||prior.version!==version)){checkpointError.value=feedbackConflictMessage;return}
  if(prior)externalBaselineSession.confirmed(ownerKey,token)
  const retained=Boolean(frozenPlan.value)
  paused=true;prepared.value=null;frozenPlan.value=null;confirmed.value=false;Object.assign(form,emptyExternalFeedback());token=null;version=null;paused=false
  error.value='';notice.value=retained?'已固定的原题仍保存在软件中，可从列表继续；尚未创建外部内容轮。':'已退出原题预览，可重新选择；尚未固定原题或保存回答。'
}
const submit=async()=>{
  if(pending.value||props.busy||props.blocked||frozenPlan.value?.current_profile_matches===false)return
  const kind=attempt?.kind||(frozenPlan.value?'create':'freeze'),epoch=generation,key=scopeKey(),project=clone(props.project),previousAttempt=attempt,previousUncertain=uncertain.value
  let writing,heldToken,heldVersion,sent=false
  pending.value=kind;error.value='';notice.value=''
  try{
    const body=attempt?.body|| (kind==='freeze'?externalFreezeBody(project,prepared.value,confirmed.value):await externalCreateBody(project,frozenPlan.value,form))
    const digest=attempt?.digest||await externalOperationSha(kind==='freeze'?'freeze_external_baseline_plan':'create_external_baseline_cycle',body)
    if(!active(epoch,key)||props.blocked)return
    writing={kind,body:clone(body),digest};attempt=writing
    if(!remember()){attempt=previousAttempt;throw new ExternalBaselineInputError(checkpointError.value)}
    heldToken=token;heldVersion=version;sent=true
    const reply=await geoExternalBaselineApi[kind](body)
    const value=kind==='freeze'?await externalFreezeReceipt(project,reply,body,digest):await externalCreateReceipt(project,reply,body,digest)
    const shared=externalBaselineSession.read(key)
    if(!shared||shared.token!==heldToken||shared.version!==heldVersion||JSON.stringify(shared.value.frozen)!==JSON.stringify(writing)){
      if(active(epoch,key)){uncertain.value=true;error.value='另一入口已更新这份草稿，已核对到保存回执；当前填写保留，请导出后重新打开核对。'}
      return
    }
    const settled=kind==='freeze'?externalBaselineSession.settle(key,heldToken,writing,{...shared.value,frozenPlan:value,frozen:null,uncertain:false}):externalBaselineSession.confirmed(key,heldToken,writing)
    if(!settled)throw new ExternalBaselineInputError('保存回执已返回，但本次应用草稿未能安全更新；原请求保留，请先导出。')
    if(!active(epoch,key))return
    paused=true;attempt=null;uncertain.value=false
    if(kind==='freeze'){frozenPlan.value=value;version=externalBaselineSession.read(key)?.version??version;notice.value='原题已固定并保存到软件，可逐题复制到手机。未确认采样条件仍保持未知。'}
    else{Object.assign(form,emptyExternalFeedback());prepared.value=null;frozenPlan.value=null;confirmed.value=false;token=null;version=null;visible.value=false}
    paused=false
    if(kind==='create')emit('created',value)
  }catch(cause){
    const unknown=Boolean(previousUncertain||sent&&externalWriteUncertain(cause));let settled=false
    if(sent){const shared=externalBaselineSession.read(key);if(shared?.token===heldToken&&shared.version===heldVersion)settled=externalBaselineSession.settle(key,heldToken,writing,{...shared.value,frozen:unknown?writing:null,uncertain:unknown})}
    if(active(epoch,key)){if(settled)version=externalBaselineSession.read(key)?.version??version;uncertain.value=unknown;if(!unknown)attempt=null;else attempt=writing||previousAttempt;error.value=message(cause)}
  }finally{if(active(epoch,key)){pending.value='';remember()}}
}
const copyQuestion=async()=>{
  if(!selectedCase.value||pending.value||copyPending.value)return
  const epoch=generation,key=scopeKey(),caseId=form.caseId,text=selectedCase.value.question,sequence=++copySequence;copyPending.value=true;copyMessage.value=''
  const current=()=>active(epoch,key)&&caseId===form.caseId&&sequence===copySequence
  try{await navigator.clipboard.writeText(text);if(current())copyMessage.value='已复制原题；实际采样条件仍需你确认。'}catch{if(current())copyMessage.value='复制未完成，可直接选择上面的完整原题。'}finally{if(active(epoch,key)&&sequence===copySequence)copyPending.value=false}
}
const exportDraft=()=>exportFeedbackDraft({project_id:props.project?.id,...snapshot()},'phone-external-baseline-draft')
watch([form,confirmed,useExisting],()=>remember(),{deep:true,flush:'sync'})
watch(()=>form.caseId,()=>{copyMessage.value=''},{flush:'sync'})
watch(scopeKey,()=>{
  remember();paused=true;generation++;ownerKey=scopeKey();pending.value='';visible.value=false;storedPlans.value=[];storedRead.value=false;storedNext.value=null
  error.value='';notice.value='';copyMessage.value='';copyPending.value=false;copySequence++;checkpointError.value=''
  const saved=externalBaselineSession.read(ownerKey);token=saved?.token??null;version=saved?.version??null
  prepared.value=saved?.value.prepared||null;frozenPlan.value=saved?.value.frozenPlan||null;confirmed.value=saved?.value.confirmed||false
  useExisting.value=saved?.value.useExisting??candidateAvailable.value;Object.assign(form,saved?.value.form||emptyExternalFeedback())
  attempt=saved?.value.frozen||null;uncertain.value=saved?.value.uncertain||Boolean(attempt);paused=false
},{immediate:true,flush:'sync'})
watch(pending,value=>emit('busy',{owner,active:Boolean(value)}),{flush:'sync'})
onUnmounted(()=>{remember();destroyed=true;generation++;emit('busy',{owner,active:false});if(checkpointError.value)ElMessage.info(checkpointError.value)})
</script>

<style>
.external-baseline-entry{display:flex;align-items:center;justify-content:space-between;gap:16px;margin:18px 0;padding:16px 18px;border:1px solid var(--geo-border,#dce6e2);border-radius:10px;background:#f7faf8;font-size:13px}.external-baseline-entry p{margin:5px 0 0;color:var(--geo-muted,#63736f);font-size:12px;line-height:1.8}.external-baseline-dialog{display:flex;flex-direction:column;box-sizing:border-box;max-height:calc(100vh - 24px);max-height:calc(100dvh - 24px);margin:12px auto!important}.external-baseline-dialog .el-dialog__header,.external-baseline-dialog .el-dialog__footer{flex-shrink:0}.external-baseline-dialog .el-dialog__body{min-height:0;overflow-y:auto;overscroll-behavior:contain}.external-baseline-dialog .el-dialog__footer{background:var(--el-bg-color,#fff);border-top:1px solid #e4ece8}.external-baseline-dialog .el-select{width:100%}.external-baseline-steps{display:flex;gap:16px;flex-wrap:wrap;padding-left:20px;font-size:12px;color:#677b77}.external-baseline-steps li[aria-current=step]{font-weight:600;color:#0a756e}.external-baseline-hint{font-size:12px;line-height:1.8;color:var(--geo-muted,#63736f)}.external-baseline-warning{padding:10px 12px;background:#fff6e8;color:#875c25;font-size:12px;line-height:1.8}.external-baseline-plan-choice{display:flex;align-items:center;gap:14px;flex-wrap:wrap;margin-top:16px;font-size:12px}.external-baseline-question{margin:12px 0;padding:12px 14px;border-radius:8px;background:#f2f7f5;font-size:13px;line-height:1.9;overflow-wrap:anywhere}.external-baseline-question p{white-space:pre-wrap;margin:6px 0}.external-baseline-question small{font-size:12px;color:#63736f}.external-baseline-fields{display:grid;grid-template-columns:1fr 1fr;gap:16px}.external-baseline-optional{margin:14px 0}.external-baseline-optional summary,.external-baseline-restore summary{cursor:pointer;font-size:13px;color:#0a756e;margin-bottom:12px}.external-baseline-footer{display:flex;align-items:center;justify-content:flex-end;gap:8px;flex-wrap:wrap}.external-baseline-footer .el-button{margin-left:0}.external-baseline-stored{list-style:none;padding:0}.external-baseline-stored li{display:flex;align-items:center;justify-content:space-between;gap:8px;border-bottom:1px solid #e4ece8;font-size:12px}.external-baseline-dialog summary:focus-visible{outline:2px solid #0a756e;outline-offset:4px}@media(max-width:650px){.external-baseline-entry{align-items:flex-start;flex-direction:column}.external-baseline-fields{grid-template-columns:1fr}.external-baseline-stored li{align-items:flex-start;flex-direction:column}.external-baseline-footer{gap:6px}.external-baseline-dialog .el-dialog__body{padding-top:10px}}@media(prefers-reduced-motion:reduce){.external-baseline-dialog,.external-baseline-dialog *{scroll-behavior:auto!important;transition:none!important;animation:none!important}}
.external-baseline-confirmation{margin-bottom:12px;text-align:left}.external-baseline-confirmation .el-checkbox{height:auto;white-space:normal;align-items:flex-start}.external-baseline-confirmation .el-checkbox__input{margin-top:3px}.external-baseline-confirmation .el-checkbox__label{white-space:normal;line-height:1.6}
</style>
