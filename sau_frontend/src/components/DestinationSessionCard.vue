<template>
  <section class="destination-session" aria-label="平台接入准备" data-testid="destination-session-card">
    <strong class="preparation-title">打开平台前，先准备好</strong>
    <ol class="preparation-list"><li>{{ preparation.login }}</li><li>{{ preparation.verification }}</li><li>{{ preparation.binding }}</li><li v-if="preparation.editor">{{ preparation.editor }}</li></ol>
    <p class="capability-note">{{ preparation.capability }}</p>
    <Transition name="destination-status" mode="out-in">
      <div v-if="session" :key="`${session.taskId}:${session.state}`" class="session-state" role="status" aria-live="polite" data-testid="destination-session-state">
        <strong>{{ session.title }}</strong><p>{{ session.detail }}</p>
        <p v-if="session.contentWriteAttempted">本次已尝试写入内容，复检只读取状态。请核对平台可能自动保存的草稿，不要重复填写。</p>
      </div>
    </Transition>
    <p v-if="session?.active && checkAvailable" class="continuation-note">在专用窗口完成后保持窗口打开，再点“{{ session.checkLabel }}”。软件保留同一任务{{ session.contentWriteAttempted ? '，不会重复填写。' : task.kind==='destination_fill' ? '；检查通过后继续原来的填写。' : '，不会填写文章。' }}</p>
    <p v-else-if="nativeAvailable && !checkAvailable" class="continuation-note">当前桌面版本不支持窗口复检，请按原专用窗口提示正常完成操作；原任务记录保留，升级后可使用新的检查流程。</p>
    <div class="session-actions">
      <el-button v-if="session?.active && checkAvailable" type="primary" :loading="checking" :disabled="!session.canCheck || checking || checkRequested" @click="check">{{ session.checkLabel }}</el-button>
      <el-button v-else :disabled="busy || !nativeAvailable" @click="$emit('open')">打开平台登录 / 注册</el-button>
      <span v-if="checking || checkRequested" class="check-note" role="status">{{ checking ? '正在申请检查原任务…' : '已申请检查原任务，等待窗口状态更新。' }}</span>
    </div>
    <p v-if="checkError" class="check-error" role="alert">{{ checkError }}</p>
    <p v-if="!nativeAvailable" class="continuation-note">专用平台窗口需使用桌面软件；稿件与平台配置会保留。</p>
  </section>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { destinationPreparation, destinationSessionView, requestDestinationSessionCheck } from '@/utils/geoDestinationSession'

const props = defineProps({cycleId:{type:String,required:true},target:{type:Object,required:true},task:{type:Object,default:()=>({})},busy:Boolean,nativeAvailable:Boolean})
const emit = defineEmits(['open','checked'])
const checking = ref(false), checkRequested = ref(false), checkError = ref('')
const preparation = computed(()=>destinationPreparation(props.target))
const session = computed(()=>destinationSessionView(props.task,props.cycleId,props.target.id))
const checkAvailable = computed(()=>props.nativeAvailable && typeof window.pywebview?.api?.check_destination_session === 'function')
watch(()=>`${props.cycleId}:${props.target.id}:${session.value?.taskId}`,()=>{checkRequested.value=false;checkError.value='';checking.value=false})
watch([()=>session.value?.state,()=>props.task.updated_at,()=>session.value?.active],()=>{checkRequested.value=false;checkError.value=''})
const check = async () => {
  if(checking.value)return
  const scope = `${props.cycleId}:${props.target.id}:${session.value?.taskId}`
  const observedAt = props.task.updated_at
  checking.value=true;checkError.value='';checkRequested.value=false
  try {
    await requestDestinationSessionCheck(window.pywebview?.api,props.task,props.cycleId,props.target.id)
    if(scope !== `${props.cycleId}:${props.target.id}:${session.value?.taskId}`)return
    checkRequested.value=props.task.updated_at===observedAt;emit('checked')
  } catch(error) {if(scope === `${props.cycleId}:${props.target.id}:${session.value?.taskId}`)checkError.value=error.message || '检查未完成，原任务记录已保留。'}
  finally {if(scope === `${props.cycleId}:${props.target.id}:${session.value?.taskId}`)checking.value=false}
}
</script>

<style scoped>
.destination-session{padding:18px 20px;margin:16px 0;border:1px solid var(--geo-border,#dce6e2);border-radius:12px;background:var(--geo-surface,#fff);font-size:13px;line-height:1.8}.preparation-title{font-size:14px;color:var(--geo-ink,#173e3b)}.preparation-list{padding-left:20px;margin:10px 0;color:var(--geo-text,#405752)}.preparation-list li+li{margin-top:4px}.capability-note,.continuation-note,.check-note{color:var(--geo-muted,#63736f);font-size:12px}.session-state{margin:14px 0;padding:12px 14px;border-radius:8px;background:#eef6f3}.session-state strong{color:var(--geo-primary,#0a756e)}.session-state p{margin:4px 0 0}.session-actions{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-top:12px}.check-error{margin-top:10px;color:#a6412d}.destination-status-enter-active,.destination-status-leave-active{transition:opacity 160ms ease,transform 160ms ease}.destination-status-enter-from,.destination-status-leave-to{opacity:0;transform:translateY(4px)}@media(prefers-reduced-motion:reduce){.destination-status-enter-active,.destination-status-leave-active{transition:none}}@media(max-width:600px){.destination-session{padding:14px 16px}}
</style>
