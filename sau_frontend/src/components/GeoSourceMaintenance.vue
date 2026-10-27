<template>
  <section class="source-maintenance" aria-label="企业公开资料补正" data-testid="source-maintenance">
    <header><div><h3>补正企业公开资料</h3><p>把已核对的公司资料整理给企查查，补充企业官网或简介。</p></div><el-button v-if="company" text :disabled="locked" @click="refresh">刷新进度</el-button></header>
    <p v-if="!company" class="maintenance-hint" data-testid="source-maintenance-not-applicable">{{ view.reason }}</p>
    <template v-else>
      <p class="maintenance-identity">{{ view.company_name || cycle.baseline?.profile?.name }}</p>
      <p class="maintenance-hint">软件准备公开说明并辅助填写；姓名、联系方式、证明材料及最终提交，只在官方浏览器由你处理。</p>
      <p class="maintenance-hint">费用和材料要求以当前官方提示为准。提交、审核通过、公开更新与搜索采用分别核对，未完成不计为 GEO 优化成功。</p>
      <p v-if="!view.supported" class="maintenance-notice" role="status">{{ view.reason }}</p>
      <p v-if="!available" class="maintenance-hint">当前版本无法读取资料补正任务，请使用支持该功能的桌面版本；已有记录仍保留。</p>
      <p v-if="sessionBusy" class="maintenance-notice" role="status">企查查专用窗口正在使用桌面任务。完成当前人工步骤后检查窗口；要继续豆包等其他任务，请先正常关闭企查查窗口，原进度会保留。</p>
      <div class="maintenance-modules" role="group" aria-label="选择企业资料补正模块"><button v-for="(label,key) in sourceMaintenanceModules" :key="key" type="button" :class="{selected:selectedModule===key}" :aria-pressed="selectedModule===key" :disabled="locked" @click="selectModule(key)">{{ label }}</button></div>
      <p v-if="loading" class="maintenance-hint" role="status" aria-live="polite">正在读取已保存的原任务…</p>
      <p v-if="error" class="maintenance-error" role="alert">{{ error }}</p>
      <div v-if="contextIssue" class="maintenance-notice" role="status">已确认资料有变化，原编辑仍保留。请重新载入公开说明并核对后保存。<el-button text :disabled="locked" @click="syncProposal">重新载入公开说明</el-button></div>
      <div v-if="!moduleTasks.length && !selectedDefinition" class="maintenance-hint">请先在本轮核对并保存这个模块所需的公开事实，再准备补正说明。</div>
      <template v-else-if="moduleTasks.length<=1 && selectedDefinition && (!selectedRecord || selectedRecord.cycle_id===cycle.id && editable(selectedRecord))">
        <label :for="editorId" class="maintenance-label">{{ sourceMaintenanceModules[selectedModule] }} · 拟提交的公开补正说明</label>
        <el-input :id="editorId" v-model="proposal" type="textarea" :rows="5" :maxlength="selectedDefinition.max_length" show-word-limit :disabled="locked || !view.supported" data-testid="source-maintenance-proposal"/>
        <p class="maintenance-hint">{{ selectedDefinition.reference_fact_ids?.length ? `依据本轮 ${selectedDefinition.reference_fact_ids.length} 条已确认资料准备。` : '' }}请本人核对后保存。可合理改写公开事实；新增事实请先返回内容区核对。不要填写联系人、电话或其他个人信息。</p>
        <el-button :disabled="!canSave" :loading="saving" data-testid="source-maintenance-save" @click="saveProposal">{{ selectedRecord ? '保存补正说明' : '创建补正任务' }}</el-button>
      </template>
      <p v-if="moduleTasks.length>1" class="maintenance-notice">这个模块有多条历史记录，请分别核对原任务；当前不创建或改写申请。</p>
      <article v-for="record in moduleTasks" :key="record.id" class="maintenance-record" data-testid="source-maintenance-record">
        <div class="maintenance-record-heading"><strong>{{ sourceMaintenanceModules[record.module] }}</strong><el-tag :type="stage(record).tone" size="small" effect="plain">{{ stage(record).label }}</el-tag><span>{{ date(record.updated_at) }}</span></div>
        <p v-if="record.source==='research_assisted_browser'" class="maintenance-notice" data-testid="source-maintenance-research-import">研究流程回执导入（非软件原生提交）</p>
        <p v-if="record.cycle_id!==cycle.id" class="maintenance-notice">同一公司在另一轮实验已有此模块的原任务。请从搜索基线的“继续已有内容”返回原实验核对，不要重新申请。</p>
        <div class="maintenance-stage" role="status" aria-live="polite"><p>{{ stage(record).hint }}</p><p v-if="record.message">{{ record.message }}</p></div>
        <p v-if="readOnly(record)" class="maintenance-hint">原申请公开说明已锁定。重新打开只核对原进度，不重新填写或提交。</p>
        <details><summary>查看原任务公开说明</summary><p class="maintenance-proposal">{{ record.proposal }}</p></details>
        <p v-if="record.stage==='submitted_pending_review'" class="maintenance-hint">已保留原提交状态；待审核不代表公开资料已更新，搜索是否采用仍未知。</p>
        <p v-if="record.receipt" class="maintenance-hint">脱敏回执已保留，联系人信息不在软件内展示。</p>
        <div class="maintenance-actions"><el-button v-if="canOpen(record)" :disabled="locked || !nativeReady" :loading="opening===record.id" :data-testid="`source-maintenance-open-${record.module}`" @click="openWindow(record)">{{ readOnly(record) ? '只读核对原申请' : '打开官方窗口并辅助填写' }}</el-button><el-button v-if="canCheck(record)" :disabled="locked" :loading="checking===record.id" :data-testid="`source-maintenance-check-${record.module}`" @click="checkWindow(record)">检查当前窗口</el-button></div>
        <p v-if="record.stage==='confirmation_ready'" class="maintenance-notice">在官方窗口手动提交后，再点“检查当前窗口”。软件没有最终提交按钮。</p>
      </article>
      <p v-if="view.supported && !moduleTasks.length" class="maintenance-hint">这个模块还没有已保存的任务。保存说明不会自动提交申请。</p>
      <p v-if="nativeReady" class="maintenance-hint">登录或验证完成后保持官方窗口打开，再检查当前窗口；关闭软件后可回来读取同一任务。</p>
      <p v-else class="maintenance-hint">打开和检查官方专用窗口需要桌面软件，网页端仍可查看已保存的进度。</p>
      <a :href="qccMaintenanceEntry" target="_blank" rel="noopener noreferrer">查看企查查官方资料补正入口</a>
    </template>
  </section>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { geoSourceMaintenanceApi } from '@/api/geoSourceMaintenance'
import { journeyDate } from '@/utils/geoJourney'
import { qccMaintenanceEntry, sourceMaintenanceModules, sourceMaintenanceCompany, sourceMaintenanceOverview,
  sourceMaintenanceReadOnly, sourceMaintenanceEditable, sourceMaintenanceTask, sourceMaintenancePayload, sourceMaintenanceCanOpen,
  sourceMaintenanceNativeId, sourceMaintenanceStage, sourceMaintenanceError } from '@/utils/geoSourceMaintenance'

const props=defineProps({cycle:{type:Object,required:true},busy:Boolean,nativeAvailable:Boolean,nativeTaskActive:Boolean,focusTaskId:String})
const emit=defineEmits(['busy','task'])
const overview=ref(props.cycle.source_maintenance||null),selectedModule=ref('website'),proposal=ref(''),edited=ref(false),editorContext=ref(null)
const loading=ref(false),saving=ref(false),opening=ref(''),checking=ref(''),error=ref(''),saveUncertain=ref(false)
let scopeVersion=0,readVersion=0,disposed=false,pollTimer=null,polling=false
const company=computed(()=>sourceMaintenanceCompany(props.cycle))
const view=computed(()=>sourceMaintenanceOverview(props.cycle,overview.value))
const available=computed(()=>company.value&&typeof geoSourceMaintenanceApi.list==='function'&&Boolean(props.cycle.source_maintenance))
const nativeReady=computed(()=>props.nativeAvailable&&typeof window.pywebview?.api?.open_source_maintenance==='function'
  &&typeof window.pywebview?.api?.check_source_maintenance_session==='function'&&typeof window.pywebview?.api?.get_task==='function')
const localBusy=computed(()=>saving.value||Boolean(opening.value)||Boolean(checking.value))
const sessionBusy=computed(()=>props.nativeTaskActive||view.value.tasks.some(item=>item.window_open===true))
const locked=computed(()=>props.busy||localBusy.value||loading.value)
const moduleTasks=computed(()=>view.value.tasks.filter(item=>item.module===selectedModule.value))
const selectedRecord=computed(()=>sourceMaintenanceTask(view.value,selectedModule.value))
const selectedDefinition=computed(()=>view.value.modules.find(item=>item.key===selectedModule.value)||null)
const editorId=computed(()=>`maintenance-proposal-${props.cycle.id}-${selectedModule.value}`)
const contextIssue=computed(()=>Boolean(edited.value&&editorContext.value&&view.value.context_sha256!==editorContext.value))
const canSave=computed(()=>!locked.value&&!sessionBusy.value&&!saveUncertain.value&&!contextIssue.value&&available.value&&view.value.supported
  &&Boolean(selectedDefinition.value)&&moduleTasks.value.length<=1&&(!selectedRecord.value||selectedRecord.value.cycle_id===props.cycle.id&&editable(selectedRecord.value))
  &&Boolean(proposal.value.trim())&&Array.from(proposal.value).length<=selectedDefinition.value.max_length)
const readOnly=record=>sourceMaintenanceReadOnly(record)
const editable=record=>sourceMaintenanceEditable(record)
const stage=record=>sourceMaintenanceStage(record)
const date=value=>journeyDate(value)
const canOpen=record=>!sessionBusy.value&&view.value.supported&&sourceMaintenanceCanOpen(record,props.cycle)
const canCheck=record=>Boolean(nativeReady.value&&sourceMaintenanceNativeId(record)&&record.cycle_id===props.cycle.id
  &&view.value.tasks.some(item=>item.id===record.id)&&record.window_open===true&&record.stage!=='window_closed'&&record.stage!=='browser_rejected')
const syncProposal=()=>{const record=selectedRecord.value;proposal.value=record?.proposal??selectedDefinition.value?.proposal??'';editorContext.value=view.value.context_sha256;edited.value=false}
const selectModule=key=>{if(locked.value||!Object.hasOwn(sourceMaintenanceModules,key))return;selectedModule.value=key;error.value='';syncProposal()}
const currentScope=()=>`${props.cycle.id}:${props.cycle.baseline?.profile?.entity_type}:${props.cycle.baseline?.profile?.name}`
const refreshOverview=async({manual=false}={})=>{
  if(!company.value||!available.value||disposed)return false
  const cycleId=props.cycle.id,scope=currentScope(),version=scopeVersion,read=++readVersion
  const result=await geoSourceMaintenanceApi.list(cycleId)
  if(disposed||version!==scopeVersion||scope!==currentScope()||read!==readVersion)return false
  overview.value=result.data
  if(manual||view.value.tasks.some(item=>item.module===selectedModule.value))saveUncertain.value=false
  if(!edited.value)syncProposal()
  return true
}
const refresh=async()=>{
  if(locked.value||!available.value)return
  const version=scopeVersion;loading.value=true;error.value=''
  try{await refreshOverview({manual:true})}catch(cause){if(version===scopeVersion&&!disposed)error.value=sourceMaintenanceError(cause)}
  finally{if(version===scopeVersion&&!disposed)loading.value=false}
}
const saveProposal=async()=>{
  if(!canSave.value)return
  const cycleId=props.cycle.id,module=selectedModule.value,text=proposal.value,version=scopeVersion,context=editorContext.value
  saving.value=true;error.value=''
  try{
    if(!await refreshOverview()||version!==scopeVersion)return
    if(context!==view.value.context_sha256)throw new Error('已确认资料有变化，请重新载入公开说明并核对后保存。')
    const payload=sourceMaintenancePayload(props.cycle,overview.value,module,text)
    await geoSourceMaintenanceApi.save(cycleId,payload)
    if(version!==scopeVersion||disposed)return
    edited.value=false;saveUncertain.value=false
    await refreshOverview()
  }catch(cause){if(version===scopeVersion&&!disposed){saveUncertain.value=true;error.value=sourceMaintenanceError(cause)}}
  finally{if(version===scopeVersion&&!disposed)saving.value=false}
}
const performWindowAction=async(record,check)=>{
  if(locked.value||!nativeReady.value||!canOpen(record)&&!check||check&&!canCheck(record))return
  const version=scopeVersion,cycleId=props.cycle.id,id=record.id,slot=check?checking:opening
  slot.value=id;error.value=''
  try{
    if(!await refreshOverview()||version!==scopeVersion)return
    const fresh=view.value.tasks.find(item=>item.id===id)
    if(!fresh||check&&!canCheck(fresh)||!check&&!canOpen(fresh))throw new Error('原任务状态已变化，请按最新进度核对。')
    const api=window.pywebview.api
    const result=check?await api.check_source_maintenance_session(sourceMaintenanceNativeId(fresh)):await api.open_source_maintenance(cycleId,id)
    if(version!==scopeVersion||disposed)return
    if(result?.error)throw new Error(result.error)
    if(!result||typeof result.id!=='string'||!/^[\w-]{1,100}$/.test(result.id)||result.status!=='running')throw new Error('窗口操作未取得确认，请先刷新原任务，不要重复打开或提交。')
    emit('task',{id:result.id,status:'running',kind:'source_maintenance',cycle_id:cycleId,source_task_id:id})
    await refreshOverview()
  }catch(cause){if(version===scopeVersion&&!disposed)error.value=sourceMaintenanceError(cause)}
  finally{if(version===scopeVersion&&!disposed)slot.value=''}
}
const openWindow=record=>performWindowAction(record,false)
const checkWindow=record=>performWindowAction(record,true)
const poll=async()=>{
  // The native ACK can arrive before window_open is saved. Keep reading the
  // local overview while this supported card is visible, including that gap.
  if(disposed||polling||locked.value||!nativeReady.value||!available.value||!view.value.supported)return
  polling=true
  try{await refreshOverview()}catch{ /* Keep the last persisted progress; polling never retries a write. */ }
  finally{polling=false}
}
watch(proposal,()=>{edited.value=true},{flush:'sync'})
watch(()=>localBusy.value||sessionBusy.value,value=>emit('busy',value),{immediate:true,flush:'sync'})
watch(()=>props.cycle.source_maintenance,value=>{overview.value=value||null;if(!edited.value)syncProposal()},{flush:'sync'})
watch(()=>[props.focusTaskId,overview.value],()=>{
  const record=view.value.tasks.find(item=>item.id===props.focusTaskId)
  if(!record||record.module===selectedModule.value||!Object.hasOwn(sourceMaintenanceModules,record.module))return
  if(edited.value){error.value='当前补正说明有未保存编辑，请先保存或保留内容，再查看另一模块的原请求。';return}
  if(!locked.value){selectedModule.value=record.module;syncProposal()}
},{immediate:true,flush:'post'})
watch(currentScope,()=>{scopeVersion++;readVersion++;overview.value=props.cycle.source_maintenance||null;selectedModule.value='website';loading.value=false;saving.value=false;opening.value='';checking.value='';error.value='';saveUncertain.value=false;syncProposal();void refresh()},{flush:'sync'})
onMounted(()=>{syncProposal();void refresh();pollTimer=setInterval(poll,1800)})
onUnmounted(()=>{disposed=true;scopeVersion++;readVersion++;clearInterval(pollTimer);emit('busy',false)})
</script>

<style scoped>
.source-maintenance{margin:22px 0;padding:22px 24px;border:1px solid var(--geo-border,#dce6e2);border-radius:12px;background:#fff;font-size:13px;line-height:1.8}.source-maintenance header,.maintenance-record-heading,.maintenance-actions{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}.source-maintenance h3{margin:0;font-size:16px;color:var(--geo-ink,#173e3b)}.source-maintenance header p,.maintenance-hint{margin:8px 0;color:var(--geo-muted,#63736f);font-size:12px}.maintenance-identity{margin:10px 0;font-weight:600;overflow-wrap:anywhere}.maintenance-modules{display:flex;gap:8px;margin:18px 0 14px}.maintenance-modules button{padding:9px 18px;border:1px solid var(--geo-border,#dce6e2);border-radius:8px;background:#fff;color:var(--geo-muted,#63736f);font-size:13px;transition:background-color 180ms ease,color 180ms ease,border-color 180ms ease}.maintenance-modules button.selected{color:var(--geo-primary,#0a756e);border-color:#afd2c8;background:#eff7f3}.maintenance-modules button:disabled{cursor:default;opacity:.65}.maintenance-modules button:focus-visible{outline:2px solid var(--geo-primary,#0a756e);outline-offset:3px}.maintenance-label{display:block;margin:10px 0;font-size:12px;font-weight:500}.maintenance-record{padding-top:18px;margin-top:20px;border-top:1px solid var(--geo-border,#dce6e2)}.maintenance-record-heading{justify-content:flex-start}.maintenance-record-heading>span:last-child{color:var(--geo-muted,#63736f);font-size:11px}.maintenance-stage{margin:13px 0;padding:12px 15px;background:#f3f8f5;border-radius:8px;font-size:12px}.maintenance-stage p+p{margin-top:7px}.maintenance-proposal{white-space:pre-wrap;overflow-wrap:anywhere;margin:10px 0;padding:12px;background:#fafcfb;border-radius:7px}.source-maintenance details{margin:12px 0}.source-maintenance summary{cursor:pointer;width:fit-content;color:var(--geo-primary,#0a756e);font-size:12px}.maintenance-actions{justify-content:flex-start;margin-top:14px}.maintenance-notice{margin:12px 0;padding:11px 14px;background:#fff7ec;border-radius:7px;color:#825c2b;font-size:12px}.maintenance-error{margin:12px 0;color:#a6412d}.source-maintenance a{display:inline-block;margin-top:14px;font-size:12px;color:var(--geo-primary,#0a756e)}@media(max-width:650px){.source-maintenance{padding:18px 16px}.maintenance-actions{align-items:stretch;flex-direction:column}.maintenance-actions .el-button{margin-left:0}.maintenance-modules button{flex:1}}@media(prefers-reduced-motion:reduce){.maintenance-modules button{transition:none}}
</style>
