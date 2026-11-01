<template>
  <section class="builtin-registration" aria-label="内置平台人工发布登记" data-testid="builtin-publication-registration">
    <div class="registration-heading"><h4>内置平台人工发布登记</h4><el-button :disabled="busy || saving || loading || !available" @click="openRegistration">登记人工发布</el-button></div>
    <p class="registration-hint">已在知乎手动完成发布后，可把公开链接关联到本轮稿件和原任务。用户登记，未独立核验；保存不会发布、重发或改写原任务结果，也不证明被索引、被 AI 引用或 GEO 有效。</p>
    <p v-if="!available" class="registration-hint">需要本轮已就绪的知乎稿件，以及已尝试、待处理或执行未完成的真实原任务。运行中、排队中和演示任务不能登记。</p>
    <ol v-if="records.length" class="registration-records">
      <li v-for="item in records" :key="item.id"><el-tag type="info" size="small">用户登记，未独立核验</el-tag><p>{{ item.title }} · 原任务 #{{ item.jobId }}</p><p>平台显示时间：{{ platformMinute(item.publishedAt) }}（北京时间，{{ item.precision==='minute'?'精确到分钟':'精度待核对' }}）</p><p v-if="item.note">{{ item.note }}</p><p v-if="item.olderContent" class="registration-hint">这条登记对应当时稿件，当前内容已修改；原记录保留。</p><a :href="item.url" target="_blank" rel="noopener noreferrer">查看登记链接</a></li>
    </ol>
    <el-dialog v-model="visible" title="登记内置平台人工发布" width="min(620px, 92vw)" :close-on-click-modal="false" :close-on-press-escape="!saving" :show-close="!saving" @closed="dialogClosed">
      <p class="registration-hint">先在平台核对文章、账号和显示时间。这里只保存用户登记，未独立核验。</p>
      <el-form label-position="top" @submit.prevent="saveRegistration">
        <el-form-item label="本轮已就绪稿件"><el-select :model-value="selectedArticleId" aria-label="人工发布登记稿件" :disabled="fieldsDisabled" @change="changeArticle"><el-option v-for="article in articles" :key="article.id" :value="article.id" :label="`${article.title} · 知乎`" :disabled="!article.supported"/></el-select></el-form-item>
        <el-form-item label="关联的真实原任务"><el-select :model-value="selectedJobId" aria-label="人工发布登记原任务" :disabled="fieldsDisabled" @change="changeJob"><el-option v-for="job in selectedJobs" :key="job.id" :value="job.id" :label="job.label"/></el-select></el-form-item>
        <p v-if="loading" role="status" class="registration-hint">正在读取当前稿件与原任务，请稍候核对。</p>
        <p v-if="snapshot" class="registration-hint">已读取：{{ snapshot.accountName }} · 原任务 #{{ snapshot.jobId }}。请确认这就是实际发布账号。</p>
        <el-form-item label="知乎公开文章链接"><el-input v-model="form.url" :disabled="inputDisabled" maxlength="1800" placeholder="https://zhuanlan.zhihu.com/p/文章编号"/></el-form-item>
        <el-form-item label="平台显示发布时间（北京时间，精确到分钟）"><input v-model="form.publishedAt" class="platform-time" type="datetime-local" step="60" aria-label="平台显示发布时间（北京时间）" :disabled="inputDisabled"/><p class="registration-hint">只填写平台实际显示的日期和分钟；无需补秒，不使用软件保存时间。</p></el-form-item>
        <el-form-item label="补充说明（可选）"><el-input v-model="form.note" :disabled="inputDisabled" type="textarea" :rows="3" maxlength="1000" show-word-limit/></el-form-item>
        <el-checkbox v-model="form.confirmed" :disabled="inputDisabled">我已核对发布账号、公开链接、平台显示时间及文章标题和全文，确认对应这篇稿件和原任务；这是用户登记，未独立核验</el-checkbox>
      </el-form>
      <p v-if="snapshotIssue" class="registration-warning" role="alert">{{ snapshotIssue }}</p>
      <p v-if="saveError" class="registration-warning" role="alert">{{ saveError }}</p>
      <p v-if="attemptPayload && saveError && !snapshotIssue" class="registration-hint">重试会沿用同一份登记。需要修改时，请关闭后重新核对。</p>
      <template #footer><el-button :disabled="saving" @click="visible=false">关闭</el-button><el-button v-if="!snapshot && !loading" :disabled="busy || saving" @click="loadSelection">重新读取核对信息</el-button><el-button type="primary" :loading="saving" :disabled="!canSave" @click="saveRegistration">{{ attemptPayload && saveError ? '重试保存这次登记' : '保存人工发布登记' }}</el-button></template>
    </el-dialog>
  </section>
</template>

<script setup>
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { articleApi } from '@/api/article'
import { publishApi } from '@/api/publish'
import { geoContentApi } from '@/api/geoContent'
import { builtinPublicationArticles, builtinPublicationError, builtinPublicationRecords, builtinPublicationResultMatchesScope,
  builtinPublicationScope, builtinPublicationSnapshotIssue, buildBuiltinPublicationPayload, captureBuiltinPublicationSelection,
  createBuiltinPublicationSnapshot, saveBuiltinPublicationAttempt } from '@/utils/geoBuiltinPublication'

const props=defineProps({cycle:{type:Object,required:true},busy:Boolean})
const emit=defineEmits(['updated'])
const visible=ref(false),loading=ref(false),saving=ref(false),snapshot=ref(null),attemptPayload=ref(null),saveError=ref('')
const selectedArticleId=ref(null),selectedJobId=ref(null),form=reactive({url:'',publishedAt:'',note:'',confirmed:false})
let requestVersion=0
const articles=computed(()=>builtinPublicationArticles(props.cycle))
const available=computed(()=>articles.value.some(item=>item.supported))
const records=computed(()=>builtinPublicationRecords(props.cycle))
const selectedJobs=computed(()=>articles.value.find(item=>item.id===selectedArticleId.value)?.jobs || [])
const fieldsDisabled=computed(()=>props.busy || saving.value || loading.value || Boolean(attemptPayload.value))
const snapshotIssue=computed(()=>snapshot.value?builtinPublicationSnapshotIssue(snapshot.value,props.cycle):'')
const inputDisabled=computed(()=>fieldsDisabled.value || !snapshot.value || Boolean(snapshotIssue.value))
const canSave=computed(()=>available.value && Boolean(snapshot.value) && !props.busy && !saving.value && !loading.value &&
  !snapshotIssue.value && form.confirmed && Boolean(form.url.trim() && form.publishedAt))
const platformMinute=value=>{const time=new Date(value);return Number.isFinite(time.getTime())?new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).format(time):'时间待核对'}
const resetForm=()=>{Object.assign(form,{url:'',publishedAt:'',note:'',confirmed:false});snapshot.value=null;attemptPayload.value=null;saveError.value=''}
const loadSelection=async()=>{
  if(props.busy || saving.value || loading.value || attemptPayload.value || !visible.value)return
  const version=++requestVersion,scope=builtinPublicationScope(props.cycle)
  resetForm();loading.value=true
  try {
    const selection=captureBuiltinPublicationSelection(props.cycle,selectedArticleId.value,selectedJobId.value)
    const [article,job]=await Promise.all([articleApi.getArticle(selection.articleId),publishApi.getJob(selection.jobId)])
    if(version!==requestVersion || scope!==builtinPublicationScope(props.cycle) || !visible.value)return
    snapshot.value=createBuiltinPublicationSnapshot(selection,props.cycle,article?.data,job?.data)
  } catch(error) {
    if(version===requestVersion && scope===builtinPublicationScope(props.cycle) && visible.value)saveError.value=builtinPublicationError(error)
  } finally {if(version===requestVersion)loading.value=false}
}
const openRegistration=async()=>{
  if(props.busy || saving.value || loading.value || !available.value)return
  resetForm();const article=articles.value.find(item=>item.supported)
  selectedArticleId.value=article.id;selectedJobId.value=article.jobs[0].id;visible.value=true
  await loadSelection()
}
const changeArticle=async id=>{
  if(fieldsDisabled.value)return
  selectedArticleId.value=id;selectedJobId.value=articles.value.find(item=>item.id===id)?.jobs[0]?.id || null
  await loadSelection()
}
const changeJob=async id=>{if(fieldsDisabled.value)return;selectedJobId.value=id;await loadSelection()}
const saveRegistration=async()=>{
  if(!canSave.value)return
  const opened=snapshot.value,version=++requestVersion
  try {
    if(!attemptPayload.value)attemptPayload.value=buildBuiltinPublicationPayload(opened,form,props.cycle)
    saving.value=true;saveError.value=''
    const result=await saveBuiltinPublicationAttempt(geoContentApi,opened,attemptPayload.value)
    if(version!==requestVersion || opened!==snapshot.value || !visible.value)return
    if(!builtinPublicationResultMatchesScope(opened,snapshot.value,props.cycle,result)) {
      saveError.value='登记已保存，但当前稿件或原任务已变化；请刷新资料后重新核对。';return
    }
    emit('updated',result);visible.value=false
  } catch(error) {
    if(version===requestVersion && opened===snapshot.value && !builtinPublicationSnapshotIssue(opened,props.cycle))saveError.value=builtinPublicationError(error)
  } finally {if(version===requestVersion)saving.value=false}
}
const dialogClosed=()=>{if(!saving.value){requestVersion++;loading.value=false;resetForm();selectedArticleId.value=null;selectedJobId.value=null}}
watch(()=>builtinPublicationScope(props.cycle),()=>{requestVersion++;visible.value=false;loading.value=false;saving.value=false;resetForm();selectedArticleId.value=null;selectedJobId.value=null},{flush:'sync'})
onBeforeUnmount(()=>{requestVersion++})
</script>

<style scoped>
.builtin-registration{margin-top:16px;border-top:1px solid var(--geo-border,#dce6e2);padding-top:14px}.registration-heading{display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap}.registration-heading h4{margin:0;font-size:14px}.registration-hint{margin:8px 0;font-size:12px;color:var(--geo-muted,#63736f);line-height:1.8}.registration-warning{margin:10px 0;color:#a6412d;overflow-wrap:anywhere}.registration-records{list-style:none;padding:0;margin:12px 0;max-height:360px;overflow-y:auto}.registration-records li{border-top:1px solid var(--geo-border,#dce6e2);padding:12px 0;overflow-wrap:anywhere}.registration-records p{margin:6px 0;white-space:pre-wrap}.registration-records a{color:var(--geo-primary,#0a756e)}.el-select{width:100%}.platform-time{box-sizing:border-box;max-width:100%;width:100%;min-width:0;border:1px solid var(--el-border-color,#dcdfe6);border-radius:4px;padding:8px 12px;font:inherit;color:var(--el-text-color-regular,#606266);background:var(--el-fill-color-blank,#fff)}.platform-time:disabled{opacity:.65}.el-checkbox{display:flex;height:auto;min-height:32px;margin:8px 0;max-width:100%}.el-checkbox:deep(.el-checkbox__label){white-space:normal;line-height:1.7}:deep(.el-dialog__footer){display:flex;flex-wrap:wrap;justify-content:flex-end;gap:8px}:deep(.el-dialog__footer .el-button){margin:0;white-space:normal;height:auto;min-height:32px}@media(max-width:600px){.registration-heading .el-button{margin:0}.registration-hint{overflow-wrap:anywhere}}
</style>
