<template>
  <section v-if="available" class="draft-recovery" aria-label="已有稿件恢复" data-testid="draft-recovery">
    <div class="recovery-heading"><h3>{{ state.preview ? 'AI已返回稿件' : '检查已生成稿件' }}</h3><el-tag v-if="state.savedArticleId" type="success">已存入内容库</el-tag></div>
    <p class="hint">核对原请求已经返回的稿件，再保存到本轮内容库。此操作不会重新向豆包提问，也不会发布文章。</p>
    <p v-if="state.loading" class="hint" role="status">正在核对已有回答与本轮资料…</p>
    <template v-if="state.preview">
      <p class="draft-title">{{ state.preview.draft.title }}</p>
      <p class="hint">{{ channelLabel }} · 原选题：{{ briefLabel }}</p>
      <details><summary>查看这份稿件</summary><p>{{ state.preview.draft.summary }}</p><pre>{{ state.preview.draft.content }}</pre><p v-if="state.preview.draft.tags.length" class="hint">标签：{{ state.preview.draft.tags.join('、') }}</p></details>
      <p v-if="state.savedArticleId" class="hint" role="status">稿件已保存，请在下方继续审稿。原回答与失败记录保留。</p>
      <template v-else-if="!state.uncertain">
        <p v-if="!factsCurrent" class="warning" role="status">当前资料还有未保存的改动，请先保存并核对事实资料，再检查这份稿件。</p>
        <el-checkbox v-model="state.confirmed" :disabled="fieldsDisabled || !factsCurrent">已核对这份稿件</el-checkbox>
        <div class="recovery-actions"><el-button type="primary" :loading="state.saving" :disabled="!canSave" @click="flow.save">保存已有稿件，不重新生成</el-button></div>
      </template>
    </template>
    <p v-if="state.unavailable" class="warning" role="status">{{ state.unavailable }}</p>
    <p v-if="state.error" class="warning" role="status">{{ state.error }}</p>
    <el-button v-if="!state.preview || state.uncertain || state.error" :disabled="busy || state.loading || state.saving" @click="flow.inspect">{{ state.uncertain || state.savedArticleId ? '检查保存结果' : '重新检查已有稿件' }}</el-button>
  </section>
</template>

<script setup>
import {computed,onUnmounted,reactive,useId,watch} from 'vue'
import {geoContentApi} from '@/api/geoContent'
import {createDraftRecoveryFlow,draftRecoveryBridgeReady,draftRecoveryScope,draftRecoveryState} from '@/utils/geoDraftRecovery'

const props=defineProps({cycle:{type:Object,required:true},task:{type:Object,required:true},busy:Boolean,
  nativeAvailable:Boolean,factsCurrent:Boolean,selectionKey:{type:String,default:''}})
const emit=defineEmits(['updated','busy'])
const owner=`draft-recovery:${useId()}`,state=reactive(draftRecoveryState())
const api=()=>window.pywebview?.api
const scope=computed(()=>draftRecoveryScope(props.cycle,props.task,props.selectionKey))
const available=computed(()=>props.nativeAvailable && draftRecoveryBridgeReady(api()) && Boolean(scope.value))
const fieldsDisabled=computed(()=>props.busy || state.loading || state.saving)
const canSave=computed(()=>available.value && state.preview && state.confirmed && props.factsCurrent
  && !fieldsDisabled.value && !state.savedArticleId && !state.uncertain)
const channelLabel=computed(()=>props.cycle.channels?.[state.preview?.platform] || '原稿渠道')
const briefLabel=computed(()=>props.cycle.analysis?.briefs?.find(item=>item.id===state.preview?.brief_id)?.topic || '原任务选题')
const flow=createDraftRecoveryFlow({state,getScope:()=>available.value?scope.value:null,getApi:api,
  isBlocked:()=>props.busy || !props.factsCurrent,
  loadCycle:async id=>(await geoContentApi.get(id)).data,
  applyCycle:value=>emit('updated',value),busy:active=>emit('busy',{owner,active})})
watch(()=>available.value?JSON.stringify(scope.value):'',()=>flow.inspect(),{immediate:true,flush:'sync'})
onUnmounted(flow.dispose)
</script>

<style scoped>
.draft-recovery{min-width:0;margin:16px 0;padding:18px 20px;border:1px solid #c9dfdc;border-radius:12px;background:#f4faf8;line-height:1.8}.recovery-heading,.recovery-actions{display:flex;align-items:center;gap:12px;flex-wrap:wrap}.recovery-heading h3{margin:0;font-size:17px}.hint{color:#59696e;font-size:13px}.draft-title{font-weight:600}.warning{color:#855c2a;font-size:13px}.draft-recovery details{min-width:0;overflow-wrap:anywhere}.draft-recovery summary{cursor:pointer;width:fit-content}.draft-recovery summary:focus-visible{outline:2px solid #087f78;outline-offset:4px}.draft-recovery pre{max-width:100%;white-space:pre-wrap;overflow-wrap:anywhere;font-family:inherit;font-size:14px}.recovery-actions{margin-top:10px}@media(max-width:600px){.draft-recovery{padding:14px}.recovery-actions>.el-button{max-width:100%;height:auto;white-space:normal;line-height:1.6}}
</style>
