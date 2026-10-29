<template>
  <section class="optimization-progress" aria-label="本项目优化进展" data-testid="optimization-progress">
    <header><div><h2>优化进展<span v-if="page"> · {{ page.identity.name }}</span></h2><p>记录每一步改动和同题回答变化，便于向负责人交接。</p></div><div class="progress-actions"><el-button text :disabled="busy || pending" @click="load">刷新</el-button><el-button plain :disabled="busy || pending || !page" :loading="exporting" @click="exportAll">导出本项目进展</el-button><el-button type="primary" plain :disabled="busy || blocked || pending || !page" data-testid="progress-open" @click="open">记录一次推进</el-button></div></header>
    <p v-if="loading && !page" class="progress-hint" role="status">正在读取本项目进展…</p>
    <p v-if="error || checkpointError" class="progress-error" role="alert">{{ error || checkpointError }}</p>
    <p v-if="notice" class="progress-hint" role="status">{{ notice }}</p>
    <template v-if="page"><div class="progress-counts"><span>回答改善 <strong>{{ page.countsCurrent.observed_improvement }}</strong> 条</span><span>尚未观察到 <strong>{{ page.countsCurrent.not_observed }}</strong> 条</span><span>基础准备 <strong>{{ page.countsCurrent.preparation }}</strong> 项</span></div>
      <p v-if="latestCurrentImprovement" class="progress-action-text" data-testid="progress-latest"><strong>最近改善：</strong>{{ latestCurrentImprovement.direction }} · {{ latestCurrentImprovement.action_summary.slice(0,100) }}{{ latestCurrentImprovement.action_summary.length>100 ? '…' : '' }}</p>
      <p v-if="!page.countsCurrent.total" class="progress-empty">当前对象还没有进展记录。可以先登记官网、资料维护等准备工作，取得两条回答后再对照摘录。</p>
      <p v-if="page.countsHistory.total || page.countsUnknown.total" class="progress-hint">另保留历史对象 {{ page.countsHistory.total }} 条、范围待核对 {{ page.countsUnknown.total }} 条，单独归档，不计当前对象改善。</p>
      <details v-for="group in recordGroups" :key="group.key" class="progress-record-group"><summary>查看{{ group.label }} · 本页 {{ group.records.length }} 条</summary>
      <ol class="progress-records"><li v-for="item in group.records" :key="item.id"><div class="progress-record-title"><strong>{{ item.direction }}</strong><span>{{ progressStatusLabel(item.status) }}</span><small>运营方记录</small><small v-if="item.evidence_kind==='user_reported_included'">含用户提供的回答</small></div><p class="progress-hint">对象：{{ item.project_identity?.name || '范围未知' }}{{ item.project_identity?.website ? ` · ${item.project_identity.website}` : '' }} · {{ identityBasis(item.project_identity_basis) }}<br>操作：{{ item.action_at ? journeyDate(item.action_at) : '时间未提供' }} · 录入：{{ journeyDate(item.recorded_at) }}</p><p class="progress-action-text">{{ item.action_summary.slice(0,240) }}{{ item.action_summary.length>240 ? '…' : '' }}</p><details v-if="item.action_summary.length>240"><summary>展开完整改动说明</summary><p class="progress-action-text">{{ item.action_summary }}</p></details>
        <details v-if="item.before || item.after" class="progress-evidence"><summary>查看前后精确摘录 · {{ item.before?.case_id || item.after?.case_id }}</summary><div class="progress-pair"><article v-for="side in ['before','after']" :key="side"><strong>{{ side==='before' ? '之前' : '之后' }}</strong><template v-if="item[side]"><p class="progress-hint">{{ progressSourceLabel(item[side]) }} · {{ progressContextNote(item[side]) }}</p><blockquote>{{ item[side].quote }}</blockquote><p class="progress-hint">录入：{{ journeyDate(item[side].recorded_at) }}<br>采集：{{ item[side].collection_time_known ? journeyDate(item[side].collected_at) : '未知' }}<br>证据 {{ item[side].probe_id }}</p></template><p v-else class="progress-hint">未关联回答。</p></article></div></details>
        <p v-if="item.review_note" class="progress-hint progress-action-text">核对说明：{{ item.review_note }}</p></li></ol></details>
      <el-button v-if="page.hasMore" text :disabled="busy || pending" @click="loadMore">再显示20条</el-button>
      <p class="progress-hint">这里记录同题回答变化与基础准备，不是排名、因果结论或成功率。</p>
    </template>
    <el-dialog v-model="visible" title="记录一次优化推进" class="optimization-progress-dialog" width="min(820px, calc(100vw - 24px))" top="12px" append-to-body :close-on-click-modal="false" :close-on-press-escape="false" :before-close="close">
      <p class="progress-hint">本次填写对象：{{ formIdentity?.name || '尚未核对' }} · {{ formIdentity?.website || '官网范围未核对' }}。只追加历史，不改写原回答。</p><p v-if="error || checkpointError" class="progress-error" role="alert">{{ error || checkpointError }}</p><p v-if="identityMismatch" class="progress-error">当前对象范围已变化，原填写和请求保留。请先导出旧填写，再重新核对。<el-button text :disabled="pending || uncertain" @click="recheckIdentity">按当前对象重新核对</el-button></p><p v-if="uncertain" class="progress-error">保存结果未确认，原请求已保留。再次核对只重试同一次软件保存，不会发送 AI 问题。</p>
      <el-form label-position="top" @submit.prevent="save"><div class="progress-pair"><el-form-item label="优化方向"><el-input v-model="form.direction" :disabled="locked" :maxlength="200" aria-label="优化方向" placeholder="如官网身份、业务识别、企业资料"/></el-form-item><el-form-item label="这次记录"><el-select v-model="form.status" :disabled="locked" aria-label="进展类型"><el-option label="基础准备" value="preparation"/><el-option label="观察到回答改善（人工记录）" value="observed_improvement"/><el-option label="尚未观察到改善" value="not_observed"/></el-select></el-form-item></div>
        <el-form-item label="做了什么改动"><el-input v-model="form.actionSummary" :disabled="locked" type="textarea" :rows="3" :maxlength="3000" aria-label="本次改动" placeholder="写清楚这次实际修改或维护的内容。"/></el-form-item>
        <el-form-item label="实际操作时间（不知道就留空）"><el-input v-model="form.actionAt" :disabled="locked" type="datetime-local" aria-label="实际操作时间"/></el-form-item>
        <template v-if="form.status!=='preparation'"><p class="progress-hint">选择本项目同一计划、同一道题的前后两条回答，再从完整原文复制精确摘录。采样条件未知仍会保留未知。</p><div class="progress-pair"><article v-for="side in ['before','after']" :key="side" class="progress-editor-evidence"><el-form-item :label="side==='before' ? '之前的回答' : '之后的回答'"><el-select v-model="form[side==='before'?'beforeProbeId':'afterProbeId']" :disabled="locked" :aria-label="side==='before'?'之前的回答':'之后的回答'" placeholder="明确选择一条"><el-option v-for="option in choices(side)" :key="option.probe_id" :value="option.probe_id" :label="optionLabel(option)"/></el-select></el-form-item>
            <template v-if="selected(side)"><p class="progress-hint">{{ selected(side).question }}<br>{{ progressSourceLabel(selected(side)) }} · {{ progressContextNote(selected(side)) }}</p><p v-if="selected(side).excluded_from_improvement" class="progress-error">这条回答被排除或不完整，不能用于登记改善。</p><el-button text :disabled="locked" :loading="reading[side]" @click="readRaw(side)">读取这条完整原文</el-button><pre v-if="raws[side]" class="progress-raw">{{ raws[side].raw }}</pre><p v-else class="progress-hint">尚未读取完整原文；列表预览不能代替摘录校验。</p></template>
            <el-form-item :label="side==='before'?'之前的精确摘录':'之后的精确摘录'"><el-input v-model="form[side==='before'?'beforeQuote':'afterQuote']" :disabled="locked" type="textarea" :rows="4" :maxlength="2000" :aria-label="side==='before'?'之前的精确摘录':'之后的精确摘录'" placeholder="保留原文措辞与换行，不改写回答。"/></el-form-item></article></div>
          <p v-if="!options.length" class="progress-hint">这个项目尚无可关联回答，可先登记基础准备；回答保存并核对后再追加观察。</p><el-button v-if="optionsHasMore" text :disabled="locked" @click="moreOptions">再读取100条回答选择</el-button>
        </template><p v-else class="progress-hint">基础准备单独记录，不计为回答改善；之前填写的摘录保留，但不会随这条准备记录发送。</p>
        <el-form-item label="核对说明 / 下一步（可选）"><el-input v-model="form.reviewNote" :disabled="locked" type="textarea" :rows="3" :maxlength="3000" aria-label="进展核对说明"/></el-form-item>
      </el-form><p class="progress-hint">未保存填写仅在本次应用内暂存；关闭软件前可导出填写。完整回答只用于当前窗口核对，不进入草稿或报告下载。</p>
      <template #footer><p v-if="error || checkpointError" class="progress-error progress-footer-error" role="alert">{{ error || checkpointError }}</p><div class="progress-actions"><el-button text :disabled="pending" @click="exportDraft">导出当前填写</el-button><el-button :disabled="pending" @click="close">暂存并关闭</el-button><el-button type="primary" :disabled="busy || blocked || pending" :loading="saving" data-testid="progress-save" @click="save">{{ uncertain ? '核对同一次保存' : '追加进展记录' }}</el-button></div></template>
    </el-dialog>
  </section>
</template>
<script setup>
import {computed,onUnmounted,reactive,ref,watch} from 'vue'
import {ElMessage} from 'element-plus'
import {geoOptimizationProgressApi} from '@/api/geoOptimizationProgress'
import {geoContentApi} from '@/api/geoContent'
import {optimizationProgressSession,emptyProgressForm,progressProjectKey,progressPage,progressVerifyIdentities,progressCountSnapshot,progressPayload,progressRaw,progressReceipt,progressRequestSha,progressStatusLabel,progressSourceLabel,progressContextNote,comparableProgressEvidence,progressExportView,progressError,ProgressInputError} from '@/utils/geoOptimizationProgress'
import {downloadOptimizationProgress} from '@/utils/geoOptimizationProgressExport'
import {externalWriteUncertain} from '@/utils/geoExternalFeedback'
import {exportFeedbackDraft,feedbackCapacityMessage,feedbackConflictMessage} from '@/utils/geoFeedbackSession'
import {journeyDate} from '@/utils/geoJourney'
const props=defineProps({project:{type:Object,required:true},busy:Boolean,blocked:Boolean})
const emit=defineEmits(['busy'])
const visible=ref(false),loading=ref(false),saving=ref(false),exporting=ref(false),uncertain=ref(false),page=ref(null),records=ref([]),options=ref([]),optionsHasMore=ref(false),nextProbeOffset=ref(null)
const form=reactive(emptyProgressForm()),raws=reactive({before:null,after:null}),reading=reactive({before:false,after:false}),error=ref(''),notice=ref(''),checkpointError=ref('')
const formIdentity=ref(null),formIdentitySha=ref(null)
const pending=computed(()=>loading.value||saving.value||exporting.value||reading.before||reading.after),locked=computed(()=>props.busy||props.blocked||pending.value||uncertain.value)
const owner=optimizationProgressSession.owner('progress'),clone=value=>JSON.parse(JSON.stringify(value)),key=()=>progressProjectKey(props.project)
let generation=0,destroyed=false,paused=false,currentKey=null,token=null,version=null,frozen=null,autoReadStarted=false
const rawReads={before:0,after:0},active=(epoch,scope)=>!destroyed&&epoch===generation&&scope===key()
const draft=()=>({form:clone(form),identity:clone(formIdentity.value),identitySha:formIdentitySha.value,frozen:clone(frozen),uncertain:uncertain.value||Boolean(saving.value&&frozen)})
const hasDraft=()=>Object.entries(form).some(([name,value])=>value!==(name==='status'?'preparation':''))
const identityMismatch=computed(()=>Boolean(page.value&&formIdentitySha.value!==page.value.identitySha&&(hasDraft()||visible.value)))
const latestCurrentImprovement=computed(()=>records.value.find(item=>item.current_entity_match===true&&item.status==='observed_improvement')||null)
const recordGroups=computed(()=>[{key:'current',label:'当前对象记录',records:records.value.filter(item=>item.current_entity_match===true)},
  {key:'history',label:'历史对象归档（不计当前改善）',records:records.value.filter(item=>item.current_entity_match===false)},
  {key:'unknown',label:'范围待核对归档（不计当前改善）',records:records.value.filter(item=>item.current_entity_match===null)}].filter(group=>group.records.length))
const identityBasis=value=>({stored_at_record:'记录时保留的对象',derived_frozen_profile:'由原题范围核对',derived_unchanged_project:'由未变项目资料核对',unknown:'范围未知'}[value]||'范围未知')
const checkedPage=async(project,reply,settings)=>progressVerifyIdentities(progressPage(project,reply,settings))
const remember=()=>{
  if(paused||!currentKey||!frozen&&!hasDraft())return true
  const prior=optimizationProgressSession.read(currentKey)
  if(prior&&token===null){checkpointError.value=feedbackConflictMessage;return false}
  let result=optimizationProgressSession.write(currentKey,draft(),token,version)
  if(result.reason==='stale'&&!optimizationProgressSession.read(currentKey))result=optimizationProgressSession.write(currentKey,draft())
  if(!result.ok){checkpointError.value=result.reason==='capacity'?feedbackCapacityMessage:feedbackConflictMessage;return false}
  token=result.token;version=result.version;checkpointError.value='';return true
}
const install=value=>{page.value=value;records.value=value.records;options.value=value.options;optionsHasMore.value=value.optionsHasMore;nextProbeOffset.value=value.nextProbeOffset
  if(!hasDraft()&&!frozen){formIdentity.value=clone(value.identity);formIdentitySha.value=value.identitySha}}
const load=async()=>{
  if(pending.value||props.busy||!key())return
  const epoch=generation,scope=key(),project=clone(props.project);loading.value=true;error.value=''
  try{const value=await checkedPage(project,await geoOptimizationProgressApi.get(project.id));if(active(epoch,scope))install(value)}catch(cause){if(active(epoch,scope))error.value=cause instanceof ProgressInputError?progressError(cause):'进展暂未读到，已保存记录不受影响，可以再读取一次。'}finally{if(active(epoch,scope))loading.value=false}
}
const loadMore=async()=>{
  if(pending.value||props.busy||!page.value?.hasMore)return
  const epoch=generation,scope=key(),project=clone(props.project),offset=page.value.nextOffset,snapshot=page.value.snapshot,identitySha=page.value.identitySha,counts=progressCountSnapshot(page.value);loading.value=true;error.value=''
  try{const value=await checkedPage(project,await geoOptimizationProgressApi.get(project.id,{offset,snapshot_sequence:snapshot,expected_project_identity_sha256:identitySha,include_probes:false}),{offset,snapshot,identitySha,includeProbes:false});if(!active(epoch,scope))return
    if(progressCountSnapshot(value)!==counts)throw new ProgressInputError('历史对象归档范围已变化，已显示记录保留，请刷新后再看后续记录。')
    const ids=new Set(records.value.map(item=>item.id));if(value.records.some(item=>ids.has(item.id)))throw new ProgressInputError('列表出现重复页，请刷新后再看。')
    records.value.push(...value.records);page.value={...value,records:records.value}
  }catch(cause){if(active(epoch,scope))error.value=cause instanceof ProgressInputError?progressError(cause):'后续进展暂未读到，已显示记录保留。'}finally{if(active(epoch,scope))loading.value=false}
}
const moreOptions=async()=>{
  if(pending.value||props.busy||!optionsHasMore.value)return
  const epoch=generation,scope=key(),project=clone(props.project),probeOffset=nextProbeOffset.value,identitySha=page.value.identitySha;loading.value=true;error.value=''
  try{const value=await checkedPage(project,await geoOptimizationProgressApi.get(project.id,{probe_offset:probeOffset,expected_project_identity_sha256:identitySha}),{probeOffset,identitySha});if(!active(epoch,scope))return
    const seen=new Set(options.value.map(item=>item.probe_id));options.value.push(...value.options.filter(item=>!seen.has(item.probe_id)));optionsHasMore.value=value.optionsHasMore;nextProbeOffset.value=value.nextProbeOffset
  }catch(cause){if(active(epoch,scope))error.value=cause instanceof ProgressInputError?progressError(cause):'回答选择暂未读到，原填写保留。'}finally{if(active(epoch,scope))loading.value=false}
}
const selected=side=>options.value.find(item=>item.probe_id===form[side==='before'?'beforeProbeId':'afterProbeId'])||null
const choices=side=>side==='before'?options.value:options.value.filter(item=>comparableProgressEvidence(selected('before'),item))
const optionLabel=option=>`${option.case_id} · ${journeyDate(option.recorded_at)} 录入 · ${progressSourceLabel(option)} · ${option.probe_id.slice(-8)}`
const readRaw=async side=>{
  if(pending.value||props.busy||props.blocked||uncertain.value||!selected(side))return
  const epoch=generation,scope=key(),project=clone(props.project),option=clone(selected(side)),sequence=++rawReads[side];reading[side]=true;error.value=''
  const current=()=>active(epoch,scope)&&sequence===rawReads[side]&&selected(side)?.probe_id===option.probe_id
  try{const value=await progressRaw(project,option,(await geoContentApi.get(option.cycle_id)).data);if(current())raws[side]=value}catch(cause){if(current())error.value=cause instanceof ProgressInputError?progressError(cause):'完整原文暂未读到，原摘录保留，可重新读取。'}finally{if(current())reading[side]=false}
}
const recheckIdentity=()=>{if(pending.value||uncertain.value||frozen||!page.value)return;formIdentity.value=clone(page.value.identity);formIdentitySha.value=page.value.identitySha;raws.before=null;raws.after=null;error.value='';notice.value='已重新核对当前对象；原填写保留，请重新选择并读取当前范围的回答。';remember()}
const open=()=>{if(props.busy||props.blocked||pending.value||!page.value)return;if(!hasDraft()&&!frozen){formIdentity.value=clone(page.value.identity);formIdentitySha.value=page.value.identitySha}visible.value=true;error.value='';notice.value=''}
const close=done=>{if(pending.value)return;remember();visible.value=false;if(typeof done==='function')done()}
const exportDraft=()=>exportFeedbackDraft({project_id:props.project.id,...draft()},'optimization-progress-draft')
const save=async()=>{
  if(pending.value||props.busy||props.blocked)return
  const epoch=generation,scope=key(),project=clone(props.project),priorFrozen=frozen,priorUncertain=uncertain.value
  let writing,heldToken,heldVersion,sent=false;saving.value=true;error.value='';notice.value=''
  try{
    if(!frozen&&(!page.value||formIdentitySha.value!==page.value.identitySha))throw new ProgressInputError('当前对象范围已变化，原填写保留，请先导出并重新核对。')
    const value=frozen||progressPayload(project,form,options.value,raws,formIdentitySha.value),body=value.body,proof=value.proof,digest=frozen?.digest||await progressRequestSha(project,body)
    if(!active(epoch,scope))return
    writing={body:clone(body),proof:clone(proof),digest};frozen=writing
    if(!remember()){frozen=priorFrozen;throw new ProgressInputError(checkpointError.value)}
    heldToken=token;heldVersion=version;sent=true
    const record=progressReceipt(project,await geoOptimizationProgressApi.append(project.id,body),writing)
    await progressVerifyIdentities({identity:record.project_identity,identitySha:record.project_identity_sha256,records:[record]})
    const shared=optimizationProgressSession.read(scope)
    if(!shared||shared.token!==heldToken||shared.version!==heldVersion||JSON.stringify(shared.value.frozen)!==JSON.stringify(writing)){
      if(active(epoch,scope)){uncertain.value=true;error.value='另一入口已更新草稿，当前填写保留。已取得保存回执，请先导出后重新打开核对。'}return
    }
    if(!optimizationProgressSession.confirmed(scope,heldToken,writing))throw new ProgressInputError('保存回执已返回，草稿尚未安全结算，请先导出并核对原请求。')
    if(!active(epoch,scope))return
    paused=true;frozen=null;uncertain.value=false;Object.assign(form,emptyProgressForm());raws.before=null;raws.after=null;token=null;version=null;paused=false;visible.value=false
    records.value=[record,...records.value.filter(item=>item.id!==record.id)];notice.value='已追加这条进展，历史记录保留。'
    try{const refreshed=await checkedPage(project,await geoOptimizationProgressApi.get(project.id));if(active(epoch,scope))install(refreshed)}catch{if(active(epoch,scope))error.value='本条已保存，列表统计暂未刷新；可以点击刷新核对，不要重复录入。'}
  }catch(cause){
    const unknown=Boolean(priorUncertain||sent&&externalWriteUncertain(cause));let settled=false
    if(sent){const shared=optimizationProgressSession.read(scope);if(shared?.token===heldToken&&shared.version===heldVersion)settled=optimizationProgressSession.settle(scope,heldToken,writing,{...shared.value,frozen:unknown?writing:null,uncertain:unknown})}
    if(active(epoch,scope)){if(settled)version=optimizationProgressSession.read(scope)?.version??version;uncertain.value=unknown;frozen=unknown?(writing||priorFrozen):null;error.value=progressError(cause)}
  }finally{if(active(epoch,scope)){saving.value=false;remember()}}
}
const exportAll=async()=>{
  if(pending.value||props.busy)return
  const epoch=generation,scope=key(),project=clone(props.project);exporting.value=true;error.value=''
  try{let value=await checkedPage(project,await geoOptimizationProgressApi.get(project.id,{include_probes:false}),{includeProbes:false}),first=value,all=[...value.records],seen=new Set(all.map(item=>item.id))
    if(first.counts.total>2000)throw new ProgressInputError('本项目记录超过单次2000条，本次未生成全部报告，请分段保留。')
    while(value.hasMore&&all.length<2000){if(!active(epoch,scope))return;const offset=value.nextOffset;value=await checkedPage(project,await geoOptimizationProgressApi.get(project.id,{include_probes:false,offset,snapshot_sequence:first.snapshot,expected_project_identity_sha256:first.identitySha}),{includeProbes:false,offset,snapshot:first.snapshot,identitySha:first.identitySha})
      if(progressCountSnapshot(value)!==progressCountSnapshot(first))throw new ProgressInputError('导出期间历史对象归档范围发生变化，未生成报告，请重新读取后再导出。')
      for(const record of value.records){if(seen.has(record.id))throw new ProgressInputError('导出分页身份重复，未生成报告，请刷新后再试。');seen.add(record.id);all.push(record)}}
    if(!active(epoch,scope))return
    if(value.hasMore||all.length!==first.counts.total)throw new ProgressInputError('本项目记录超过单次2000条或分页尚未完整，本次未导出全部，请联系负责人分段保留。')
    downloadOptimizationProgress({...first.identity,id:project.id},progressExportView(first,all));notice.value=`已导出当前对象 ${first.countsCurrent.total} 条进展，历史与待核对 ${first.countsHistory.total+first.countsUnknown.total} 条单独归档。`
  }catch(cause){if(active(epoch,scope))error.value=cause instanceof ProgressInputError?progressError(cause):'报告暂未生成，软件记录仍保留，可以再试。'}finally{if(active(epoch,scope))exporting.value=false}
}
const maybeAutoRead=()=>{if(!autoReadStarted&&!props.busy&&key()){autoReadStarted=true;void load()}}
watch(form,remember,{deep:true,flush:'sync'})
for(const side of ['before','after'])watch(()=>form[side==='before'?'beforeProbeId':'afterProbeId'],()=>{rawReads[side]++;raws[side]=null;reading[side]=false},{flush:'sync'})
watch(key,()=>{remember();paused=true;generation++;currentKey=key();visible.value=false;loading.value=false;saving.value=false;exporting.value=false;page.value=null;records.value=[];options.value=[];optionsHasMore.value=false;nextProbeOffset.value=null;error.value='';notice.value='';checkpointError.value='';raws.before=null;raws.after=null;reading.before=false;reading.after=false;rawReads.before++;rawReads.after++;autoReadStarted=false
  const saved=optimizationProgressSession.read(currentKey);token=saved?.token??null;version=saved?.version??null;Object.assign(form,saved?.value.form||emptyProgressForm());formIdentity.value=saved?.value.identity||null;formIdentitySha.value=saved?.value.identitySha||null;frozen=saved?.value.frozen||null;uncertain.value=Boolean(saved?.value.uncertain||frozen);paused=false;maybeAutoRead()
},{immediate:true,flush:'sync'})
watch(()=>props.busy,()=>maybeAutoRead(),{flush:'sync'})
watch(pending,value=>emit('busy',{owner,active:Boolean(value)}),{immediate:true,flush:'sync'})
onUnmounted(()=>{remember();destroyed=true;generation++;raws.before=null;raws.after=null;emit('busy',{owner,active:false});if(checkpointError.value)ElMessage.info(checkpointError.value)})
</script>
<style>
.optimization-progress{margin:18px 0;padding:18px 20px;border:1px solid var(--geo-border,#dce6e2);border-radius:12px;background:var(--geo-surface,#fff)}.optimization-progress header{display:flex;align-items:flex-start;justify-content:space-between;gap:16px}.optimization-progress h2{font-size:16px;margin:0 0 5px}.optimization-progress header p{font-size:12px;color:var(--geo-muted,#63736f);margin:0;line-height:1.8}.progress-actions{display:flex;justify-content:flex-end;gap:8px;flex-wrap:wrap}.progress-actions .el-button{margin-left:0}.progress-counts{display:flex;flex-wrap:wrap;gap:12px 24px;margin:18px 0;font-size:13px;color:var(--geo-text,#234943)}.progress-counts strong{font-size:20px;margin:0 4px}.progress-hint{font-size:12px;color:var(--geo-muted,#63736f);line-height:1.8}.progress-error{font-size:12px;color:#916328;line-height:1.8}.progress-empty{padding:14px 0;color:var(--geo-muted,#63736f);font-size:13px;line-height:1.8}.progress-records{list-style:none;padding:0;margin:0}.progress-records>li{border-top:1px solid var(--geo-border,#dce6e2);padding:16px 0}.progress-record-title{display:flex;align-items:center;gap:8px;flex-wrap:wrap;font-size:13px}.progress-record-title>span,.progress-record-title>small{font-size:11px;background:#edf5f1;border-radius:4px;padding:3px 6px;color:#426a60}.progress-action-text{font-size:13px;line-height:1.8;white-space:pre-wrap;overflow-wrap:anywhere}.progress-evidence{margin:12px 0}.progress-evidence summary,.optimization-progress details>summary{font-size:12px;color:#0a756e;cursor:pointer}.progress-pair{display:grid;grid-template-columns:1fr 1fr;gap:16px}.progress-pair article{min-width:0}.progress-pair blockquote{font-size:13px;line-height:1.8;white-space:pre-wrap;overflow-wrap:anywhere;margin:8px 0;padding:10px 12px;background:#f4f8f6;border-left:2px solid #b7d7ca}.progress-editor-evidence{padding:12px;background:#f7faf8;border-radius:8px}.progress-raw{max-height:200px;overflow:auto;font:12px/1.8 inherit;white-space:pre-wrap;overflow-wrap:anywhere;background:#fff;border:1px solid #dce6e2;padding:10px}.optimization-progress-dialog{display:flex;flex-direction:column;box-sizing:border-box;max-height:calc(100vh - 24px);max-height:calc(100dvh - 24px);margin:12px auto!important}.optimization-progress-dialog .el-dialog__header,.optimization-progress-dialog .el-dialog__footer{flex-shrink:0}.optimization-progress-dialog .el-dialog__body{min-height:0;overflow-y:auto;overscroll-behavior:contain}.optimization-progress-dialog .el-dialog__footer{background:var(--el-bg-color,#fff);border-top:1px solid #e4ece8}.optimization-progress-dialog .el-select{width:100%}@media(max-width:650px){.optimization-progress{padding:14px}.optimization-progress header{flex-direction:column}.optimization-progress header>.progress-actions{justify-content:flex-start}.progress-pair{grid-template-columns:1fr}.progress-actions{gap:6px}}@media(prefers-reduced-motion:reduce){.optimization-progress-dialog,.optimization-progress-dialog *{scroll-behavior:auto!important;transition:none!important;animation:none!important}}
.optimization-progress .progress-hint,.optimization-progress-dialog .progress-hint{overflow-wrap:anywhere}.optimization-progress-dialog .progress-raw{font:inherit;font-size:12px;line-height:1.8}.progress-footer-error{margin:0 0 10px;text-align:left;overflow-wrap:anywhere}
</style>
