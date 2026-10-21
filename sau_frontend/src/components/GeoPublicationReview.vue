<template>
  <section class="publication-review" aria-label="平台结果核对" data-testid="publication-review">
    <div class="review-heading"><h3>平台结果核对</h3><el-button :disabled="busy || saving || !available" @click="openReview">登记平台结果</el-button></div>
    <p class="review-hint">记录你在平台实际看到的审核和公开状态。这是运营方观察，软件尚未独立核验；保存不会发布或重发文章。</p>
    <p v-if="blockedCount" class="review-warning" role="status">{{ blockedCount }} 条提交记录待审核或审核未通过，已暂时排除发布后效果统计，原记录保留。</p>
    <p v-if="!articles.length" class="review-hint">生成目标平台稿件后，可在这里登记平台结果。</p>
    <p v-else-if="!available" class="review-hint">当前版本尚未提供稿件核对信息，请升级软件后使用。</p>
    <p v-if="observations.length" class="review-hint">观察按时间保留。“暂不确定”不会取消之前的待审核或拒绝记录；需要重新核对相应公开文章。</p>
    <ol v-if="observations.length" class="observation-list">
      <li v-for="item in observations" :key="item.id">
        <div class="observation-heading"><el-tag :type="item.state==='rejected' ? 'danger' : item.state==='pending' ? 'warning' : 'info'" size="small">{{ item.label }}</el-tag><span>{{ item.sourceLabel }} · {{ date(item.createdAt) }}</span></div>
        <p class="observation-title">{{ item.title }} · {{ item.targetName }}</p>
        <p v-if="item.note" class="observation-note">{{ item.note }}</p>
        <p v-if="item.olderContent" class="review-hint">这条记录对应当时稿件，当前内容已修改，请重新核对当前版本。</p>
        <a v-if="item.url" :href="item.url" target="_blank" rel="noopener noreferrer">查看记录链接</a>
      </li>
    </ol>
    <p v-else class="review-hint">还没有平台结果记录。提交后先查看平台审核状态，再返回登记。</p>
    <el-dialog v-model="visible" title="登记平台结果" width="min(620px, 92vw)" :close-on-click-modal="false" :close-on-press-escape="!saving" :show-close="!saving" @closed="dialogClosed">
      <p class="review-hint">先在平台核对这篇文章。提交成功回执、审核通过和公开可查看是不同的结果。</p>
      <el-form label-position="top" @submit.prevent="saveObservation">
        <el-form-item label="核对哪篇文章"><el-select :model-value="selectedArticleId" aria-label="核对文章" :disabled="fieldsDisabled" @change="changeArticle"><el-option v-for="article in articles" :key="article.id" :value="article.id" :label="`${article.title} · ${article.targetName}`" :disabled="!article.supported"/></el-select></el-form-item>
        <el-form-item label="平台当前显示"><el-select v-model="form.state" aria-label="平台当前结果" :disabled="fieldsDisabled"><el-option v-for="state in publicationReviewStates" :key="state.value" :value="state.value" :label="state.label"/></el-select></el-form-item>
        <el-form-item :label="form.state==='publicly_visible' ? '公开文章链接' : '文章链接（可选）'"><el-input v-model="form.url" :disabled="fieldsDisabled" placeholder="https://平台/具体文章" maxlength="1800"/></el-form-item>
        <el-form-item label="平台原因或补充说明（可选）"><el-input v-model="form.note" :disabled="fieldsDisabled" type="textarea" :rows="3" maxlength="400" show-word-limit placeholder="按平台实际提示记录，例如审核未通过的原因"/></el-form-item>
        <el-checkbox v-model="form.confirmed" :disabled="fieldsDisabled">我已在平台核对，这是当前看到的结果</el-checkbox>
        <template v-if="form.state==='publicly_visible'"><p class="review-hint">请用访客视角打开这条文章链接；只看到提交回执时，不要选择“公开可查看”。</p><el-checkbox v-model="form.contentConfirmed" :disabled="fieldsDisabled">公开页的标题和全文与这篇当前稿件一致</el-checkbox></template>
      </el-form>
      <p v-if="snapshotIssue" class="review-warning" role="alert">{{ snapshotIssue }}</p>
      <p v-if="saveError" class="review-warning" role="alert">{{ saveError }}</p>
      <p v-if="attemptPayload && saveError && !snapshotIssue" class="review-hint">重试会保存同一条观察，不新增投稿。需要改动填写内容时，请关闭后重新核对。</p>
      <template #footer><el-button :disabled="saving" @click="visible=false">关闭</el-button><el-button type="primary" :loading="saving" :disabled="!canSave" @click="saveObservation">{{ attemptPayload && saveError ? '重试保存这次记录' : '保存核对记录' }}</el-button></template>
    </el-dialog>
  </section>
</template>

<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { geoContentApi } from '@/api/geoContent'
import { buildPublicationReviewPayload, createPublicationReviewSnapshot, publicationObservationViews,
  publicationReviewArticles, publicationReviewAvailable, publicationReviewError, publicationReviewResultMatchesScope,
  publicationReviewSnapshotIssue, publicationReviewStates, savePublicationReviewAttempt } from '@/utils/geoPublicationReview'

const props=defineProps({cycle:{type:Object,required:true},busy:Boolean})
const emit=defineEmits(['updated'])
const visible=ref(false),saving=ref(false),snapshot=ref(null),attemptPayload=ref(null),saveError=ref('')
const selectedArticleId=ref(null),form=reactive({state:'unknown',url:'',note:'',confirmed:false,contentConfirmed:false})
let requestVersion=0
const articles=computed(()=>publicationReviewArticles(props.cycle))
const available=computed(()=>publicationReviewAvailable(props.cycle))
const observations=computed(()=>publicationObservationViews(props.cycle))
const blockedCount=computed(()=>(props.cycle?.blocked_publications || []).filter(item=>['pending','rejected'].includes(item.moderation_state)).length)
const fieldsDisabled=computed(()=>props.busy || saving.value || Boolean(attemptPayload.value))
const snapshotIssue=computed(()=>snapshot.value?publicationReviewSnapshotIssue(snapshot.value,props.cycle):'')
const canSave=computed(()=>available.value && snapshot.value && !props.busy && !saving.value && !snapshotIssue.value &&
  form.confirmed && (form.state!=='publicly_visible' || Boolean(form.url.trim() && form.contentConfirmed)))
const date=value=>{const time=new Date(value);return Number.isFinite(time.getTime())?time.toLocaleString('zh-CN',{timeZone:'Asia/Hong_Kong',hour12:false}):'时间待确认'}
const resetForm=()=>{Object.assign(form,{state:'unknown',url:'',note:'',confirmed:false,contentConfirmed:false});snapshot.value=null;attemptPayload.value=null;selectedArticleId.value=null;saveError.value=''}
const openReview=()=>{
  if(props.busy || saving.value || !available.value)return
  resetForm()
  const article=articles.value.find(item=>item.supported)
  try {snapshot.value=createPublicationReviewSnapshot(props.cycle,article.id);selectedArticleId.value=article.id;visible.value=true}
  catch(error){saveError.value=publicationReviewError(error)}
}
const changeArticle=id=>{
  if(fieldsDisabled.value)return
  try {snapshot.value=createPublicationReviewSnapshot(props.cycle,id,snapshot.value.observationId);selectedArticleId.value=id;Object.assign(form,{state:'unknown',url:'',note:'',confirmed:false,contentConfirmed:false});saveError.value=''}
  catch(error){saveError.value=publicationReviewError(error)}
}
const saveObservation=async()=>{
  if(!canSave.value)return
  const opened=snapshot.value,version=++requestVersion
  try {
    if(!attemptPayload.value)attemptPayload.value=buildPublicationReviewPayload(opened,form,props.cycle)
    saving.value=true;saveError.value=''
    const result=await savePublicationReviewAttempt(geoContentApi,opened,attemptPayload.value)
    if(version!==requestVersion || opened!==snapshot.value || opened.cycleId!==props.cycle?.id)return
    if(!publicationReviewResultMatchesScope(opened,snapshot.value,props.cycle,result)) {
      saveError.value='观察已保存，但当前稿件已更新；请刷新资料后重新核对。'
      return
    }
    emit('updated',result);visible.value=false
  } catch(error) {
    if(version===requestVersion && opened===snapshot.value && opened.cycleId===props.cycle?.id)saveError.value=publicationReviewError(error)
  } finally {if(version===requestVersion)saving.value=false}
}
const dialogClosed=()=>{if(!saving.value){requestVersion++;resetForm()}}
watch(()=>props.cycle?.id,()=>{requestVersion++;visible.value=false;saving.value=false;resetForm()})
</script>

<style scoped>
.publication-review{margin:20px 0;padding:18px 20px;border:1px solid var(--geo-border,#dce6e2);border-radius:12px;background:var(--geo-surface,#fff);font-size:13px;line-height:1.8}.review-heading,.observation-heading{display:flex;align-items:center;gap:10px;flex-wrap:wrap}.review-heading{justify-content:space-between}.review-heading h3{margin:0;font-size:15px;color:var(--geo-ink,#173e3b)}.review-hint{margin:8px 0;color:var(--geo-muted,#63736f);font-size:12px}.review-warning{margin:10px 0;color:#a6412d;font-size:13px}.observation-list{list-style:none;margin:14px 0 0;padding:0;max-height:420px;overflow-y:auto}.observation-list li{padding:12px 0;border-top:1px solid var(--geo-border,#dce6e2)}.observation-heading>span{font-size:11px;color:var(--geo-muted,#63736f)}.observation-title,.observation-note{margin:7px 0;overflow-wrap:anywhere}.observation-title{font-weight:500}.observation-note{white-space:pre-wrap}.publication-review a{color:var(--geo-primary,#0a756e)}.el-select{width:100%}.el-checkbox{display:flex;height:auto;min-height:32px;margin:8px 0;max-width:100%}.el-checkbox:deep(.el-checkbox__label){white-space:normal;line-height:1.7}@media(max-width:600px){.publication-review{padding:14px 16px}}
</style>
