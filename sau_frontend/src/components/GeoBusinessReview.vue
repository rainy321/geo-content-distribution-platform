<template>
  <span class="business-review-entry"><el-button text :disabled="busy || loading || saving" data-testid="business-review-open" @click="open">核对业务识别</el-button><span v-if="checkpointError" role="alert">{{ checkpointError }} <el-button text @click="exportDraft">导出填写</el-button></span></span>
  <el-dialog v-model="visible" title="核对回答是否识别业务资料" class="business-review-dialog" width="760px" top="5vh" append-to-body :close-on-click-modal="false" :close-on-press-escape="false" :before-close="close">
    <p class="business-review-hint">对照这份回答与已核对资料，记录你的意见。原回答及历史意见会保留。</p>
    <el-alert v-if="error" :title="error" type="warning" :closable="false" show-icon data-testid="business-review-error"/>
    <p v-if="checkpointError" class="business-review-hint">{{ checkpointError }} <el-button text @click="exportDraft">导出填写</el-button></p>
    <p v-if="loading" role="status">正在读取原文版本与事实资料…</p>
    <template v-if="snapshot">
      <p class="business-review-hint">核对资料版本 {{ snapshot.revision }}；恢复的草稿保留原计划、原文摘要与事实版本，重新读取后需再次确认。</p><div class="business-answer"><strong>回答原文</strong><pre>{{ snapshot.raw }}</pre></div>
      <el-form label-position="top" @submit.prevent="save">
        <el-form-item label="你的核对意见"><el-select v-model="form.status" :disabled="locked" aria-label="业务资料识别意见"><el-option label="暂时无法判断" value="unknown"/><el-option label="原文识别了所选业务资料" value="acknowledged" :disabled="!snapshot.evaluable"/><el-option label="原文未识别所选业务资料" value="not_acknowledged" :disabled="!snapshot.evaluable"/></el-select></el-form-item>
        <el-form-item label="从上面回答复制精确摘录"><el-input v-model="form.quote" :disabled="locked" type="textarea" :rows="3" :maxlength="3000" aria-label="业务核对原文摘录" placeholder="保留原文措辞与换行，不要改写回答。"/></el-form-item>
        <el-form-item label="用哪些已核对的事实比较"><el-checkbox-group v-model="form.factIds" :disabled="locked" class="business-facts"><el-checkbox v-for="fact in snapshot.facts" :key="fact.id" :value="fact.id"><span>{{ fact.text }}</span><small v-if="fact.source_url">{{ fact.source_url }}</small></el-checkbox></el-checkbox-group><p v-if="!snapshot.facts.length" class="business-review-hint">本轮还没有已确认的事实，请先到内容区核对并保存事实资料。</p></el-form-item>
        <el-form-item label="备注（可选）"><el-input v-model="form.note" :disabled="locked" type="textarea" :rows="2" :maxlength="1000" aria-label="业务核对备注"/></el-form-item>
        <el-checkbox v-model="form.confirmed" :disabled="locked" class="business-confirm">我已对照精确原文和所选资料；这条意见不证明交付能力、事实准确性或 GEO 效果</el-checkbox>
      </el-form>
      <details v-if="snapshot.history.length" class="business-history"><summary>查看已保存的 {{ snapshot.history.length }} 条历史意见</summary><article v-for="(item,index) in snapshot.history" :key="index"><strong>{{ businessReviewStatus(item.status) }}</strong><span> · {{ item.by==='assistant' ? '助手' : '人工' }} · {{ journeyDate(item.reviewed_at) }}</span><blockquote>{{ item.quote }}</blockquote><p v-if="item.note">{{ item.note }}</p></article></details>
    </template>
    <template #footer><el-button text :disabled="loading || saving || !form.quote && !form.note && !form.factIds.length" @click="exportDraft">导出填写</el-button><el-button :disabled="loading || saving" @click="close(()=>{visible=false})">关闭</el-button><el-button :disabled="loading || saving || uncertain" @click="load">重新读取资料</el-button><el-button v-if="snapshot" type="primary" :disabled="busy || !snapshot.facts.length" :loading="saving" data-testid="business-review-save" @click="save">{{ uncertain ? '核对并重试保存' : '保存核对意见' }}</el-button></template>
  </el-dialog>
</template>
<script setup>
import {computed,onUnmounted,reactive,ref,watch} from 'vue'
import {ElMessage,ElMessageBox} from 'element-plus'
import {geoBusinessReviewApi} from '@/api/geoBusinessReview'
import {businessReviewSnapshot,businessReviewPayload,businessReviewReceipt,businessReviewStatus,rawAnswerHash} from '@/utils/geoBusinessReview'
import {externalFeedbackScope,externalCycleCanApply,externalWriteUncertain} from '@/utils/geoExternalFeedback'
import {journeyDate} from '@/utils/geoJourney'
import {feedbackSession,feedbackCheckpointKey,feedbackCapacityMessage,feedbackConflictMessage,exportFeedbackDraft} from '@/utils/geoFeedbackSession'
const props=defineProps({cycle:{type:Object,required:true},probe:{type:Object,required:true},busy:Boolean})
const emit=defineEmits(['updated','busy'])
const visible=ref(false),loading=ref(false),saving=ref(false),uncertain=ref(false),error=ref(''),snapshot=ref(null)
const empty=()=>({status:'unknown',quote:'',factIds:[],note:'',confirmed:false}),form=reactive(empty())
const locked=computed(()=>loading.value||saving.value||uncertain.value)
const checkpointError=ref(''),busyOwner=feedbackSession.owner('business')
let generation=0,destroyed=false,frozen=null,loadedKey=null,currentKey=null,checkpointToken=null,checkpointVersion=null,checkpointPaused=false
const clone=value=>JSON.parse(JSON.stringify(value))
const owner=()=>feedbackCheckpointKey('business',externalFeedbackScope(props.cycle),props.probe?.id)
const active=(epoch,key)=>!destroyed&&epoch===generation&&key===owner()
const remember=()=>{
  if(checkpointPaused||!currentKey||!snapshot.value||!frozen&&!form.quote&&!form.note&&!form.factIds.length)return true
  const value={snapshot:clone(snapshot.value),form:clone(form),frozen,uncertain:uncertain.value||saving.value}
  let result=feedbackSession.write(currentKey,value,checkpointToken,checkpointVersion)
  if(result.reason==='stale'&&!feedbackSession.read(currentKey))result=feedbackSession.write(currentKey,value)
  if(!result.ok){checkpointError.value=result.reason==='stale'?feedbackConflictMessage:feedbackCapacityMessage;return false}
  checkpointToken=result.token;checkpointVersion=result.version;checkpointError.value='';return true
}
const exportDraft=()=>exportFeedbackDraft({snapshot:clone(snapshot.value),form:clone(form),frozen},'geo-business-review-draft')
const load=async()=>{
  if(loading.value||saving.value||uncertain.value)return
  const epoch=++generation,key=owner(),rawProbe={after:{response:props.probe?.after?.response}}
  loading.value=true;error.value=''
  try{
    const [reply,digest]=await Promise.all([geoBusinessReviewApi.get(props.cycle.id,props.probe.id),rawAnswerHash(rawProbe)])
    if(!active(epoch,key))return
    const next=businessReviewSnapshot(props.cycle,props.probe,reply?.data,digest)
    const sameFacts=JSON.stringify(snapshot.value?.facts)===JSON.stringify(next.facts)
    snapshot.value=next
    form.factIds=sameFacts?form.factIds.filter(id=>snapshot.value.facts.some(fact=>fact.id===id)):[]
    form.confirmed=false
    loadedKey=key;uncertain.value=false;frozen=null
    remember()
  }catch(cause){if(active(epoch,key))error.value=cause.response?.data?.msg||cause.message||'未能读取核对资料。'}
  finally{if(active(epoch,key))loading.value=false}
}
const open=async()=>{
  visible.value=true
  const entry=feedbackSession.read(owner())
  if(!checkpointError.value&&entry&&(entry.token!==checkpointToken||entry.version!==checkpointVersion)){
    checkpointPaused=true;Object.assign(form,entry.value.form);snapshot.value=entry.value.snapshot;frozen=entry.value.frozen;uncertain.value=entry.value.uncertain;checkpointToken=entry.token;checkpointVersion=entry.version;loadedKey=owner();checkpointPaused=false
  }
  if(!snapshot.value||loadedKey!==owner())await load()
}
const close=async done=>{
  if(loading.value||saving.value)return
  if(form.quote||form.note||form.factIds.length){try{await ElMessageBox.confirm(checkpointError.value||'这份填写在本次应用会话中保留，切换步骤后也可恢复；退出软件前请先保存或自行保留内容。','关闭核对窗口',{confirmButtonText:'暂时关闭',cancelButtonText:'继续填写'})}catch{return}}
  remember()
  done()
}
const save=async()=>{
  if(loading.value||saving.value||props.busy||!snapshot.value)return
  const epoch=generation,key=owner(),captured=JSON.parse(JSON.stringify(snapshot.value))
  let payload
  try{payload=frozen||businessReviewPayload(captured,form)}catch(cause){error.value=cause.message;return}
  const previousFrozen=frozen
  frozen=clone(payload)
  if(!remember()){frozen=previousFrozen;error.value=checkpointError.value;return}
  const token=checkpointToken,originalForm=clone(form),wasUncertain=uncertain.value
  saving.value=true;error.value='';remember()
  try{
    const reply=await geoBusinessReviewApi.save(captured.cycleId,captured.probeId,payload)
    const value=businessReviewReceipt(reply,captured,payload)
    feedbackSession.confirmed(key,token,payload)
    if(!active(epoch,key))return
    if(externalCycleCanApply(value,props.cycle))emit('updated',value)
    else ElMessage.info('核对意见已保存，资料同时有更新，请刷新本轮查看。')
    checkpointPaused=true;Object.assign(form,empty());snapshot.value=null;loadedKey=null;frozen=null;uncertain.value=false;visible.value=false;checkpointToken=null;checkpointVersion=null;checkpointPaused=false
    ElMessage.success('核对意见已保存，原文与历史意见保留。')
  }catch(cause){
    const unknown=wasUncertain||externalWriteUncertain(cause)
    const settled=feedbackSession.settle(key,token,payload,{snapshot:captured,form:originalForm,frozen:unknown?payload:null,uncertain:unknown})
    if(active(epoch,key)){if(settled)checkpointVersion=feedbackSession.read(key)?.version??checkpointVersion;uncertain.value=unknown;if(!uncertain.value)frozen=null;error.value=(cause.response?.data?.msg||cause.message||'未能确认保存结果。')+(uncertain.value?' 填写内容已保留；重试会核对同一次保存，不追加重复意见。':'')}
  }
  finally{if(active(epoch,key)){saving.value=false;remember()}}
}
watch(()=>owner(),key=>{
  remember();checkpointPaused=true;generation++;visible.value=false;loading.value=false;saving.value=false;error.value='';checkpointError.value=''
  const entry=feedbackSession.read(key),saved=entry?.value
  currentKey=key;checkpointToken=entry?.token??null;checkpointVersion=entry?.version??null;snapshot.value=saved?.snapshot||null;loadedKey=snapshot.value?key:null
  uncertain.value=saved?.uncertain||false;frozen=saved?.frozen||null;Object.assign(form,saved?.form||empty());checkpointPaused=false
},{immediate:true,flush:'sync'})
watch(form,remember,{deep:true,flush:'sync'})
watch([loading,saving],()=>emit('busy',{owner:busyOwner,active:loading.value||saving.value}),{flush:'sync'})
onUnmounted(()=>{remember();destroyed=true;generation++;emit('busy',{owner:busyOwner,active:false});if(checkpointError.value)ElMessage.info(checkpointError.value)})
</script>
<style>
.business-review-entry{display:inline-flex}.business-review-dialog{max-width:calc(100vw - 24px);max-height:90vh;display:flex;flex-direction:column;box-sizing:border-box}.business-review-dialog .el-dialog__body{overflow:auto;min-height:0;overscroll-behavior:contain}.business-review-dialog .el-dialog__header,.business-review-dialog .el-dialog__footer{flex-shrink:0}.business-review-dialog .el-select{width:100%}.business-review-hint{font-size:12px;color:var(--geo-muted,#60747a);line-height:1.8;margin:0 0 14px}.business-answer{padding:14px;background:#f4f8f7;border-radius:8px;margin:16px 0}.business-answer strong{font-size:12px}.business-answer pre{max-height:180px;overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere;font:12px/1.9 inherit;margin:8px 0 0}.business-facts{display:grid;gap:10px;width:100%}.business-facts .el-checkbox,.business-confirm.el-checkbox{height:auto;align-items:flex-start;white-space:normal;max-width:100%;line-height:1.8}.business-facts .el-checkbox__input,.business-confirm .el-checkbox__input{padding-top:4px}.business-facts .el-checkbox__label,.business-confirm .el-checkbox__label{white-space:normal;overflow-wrap:anywhere;line-height:1.8;font-size:12px}.business-facts small{display:block;color:var(--geo-muted,#60747a);font-size:11px}.business-history{margin:16px 0;font-size:12px;line-height:1.8}.business-history summary{cursor:pointer;color:var(--geo-primary,#0a756e)}.business-history article{padding:12px 0;border-bottom:1px solid var(--geo-border,#dce6e2)}.business-history span{color:var(--geo-muted,#60747a)}.business-history blockquote{white-space:pre-wrap;overflow-wrap:anywhere;margin:8px 0;padding:8px 12px;border-left:2px solid var(--geo-border,#dce6e2)}
</style>
