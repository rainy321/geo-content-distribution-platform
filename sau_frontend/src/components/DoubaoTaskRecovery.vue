<template>
  <aside class="doubao-recovery" role="status" aria-live="polite" aria-label="豆包任务恢复" data-testid="doubao-task-recovery">
    <div class="recovery-copy"><strong>{{ recovery.title }}</strong><p>{{ recovery.description }}</p><p class="submission-state">{{ recovery.submissionText }}</p></div>
    <el-button v-if="recovery.canLogin" :disabled="busy || !nativeAvailable" @click="$emit('login')">重新登录豆包</el-button>
    <details><summary>查看任务详情</summary><p>原因：{{ recovery.reason }} · {{ recovery.clientMode === 'work' ? '上次使用豆包工作' : recovery.clientMode === 'consumer' ? '上次使用豆包对话' : '上次界面尚未确认，请核对当前选项' }}</p><p>原始记录保留，不会自动重发。结束登录窗口不代表认证已核验。</p></details>
  </aside>
</template>

<script setup>
defineProps({recovery: {type: Object, required: true}, busy: Boolean, nativeAvailable: Boolean})
defineEmits(['login'])
</script>

<style scoped>
.doubao-recovery{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:12px 20px;align-items:start;padding:18px 20px;border:1px solid #e7d6b6;border-radius:12px;background:#fffaf1;color:var(--geo-text,#243c3c);font-size:13px;line-height:1.8}.recovery-copy{min-width:0}.recovery-copy strong{font-size:15px}.doubao-recovery p{margin:4px 0 0}.submission-state,.doubao-recovery details{color:var(--geo-muted,#63736f);font-size:12px}.doubao-recovery details{grid-column:1/-1;overflow-wrap:anywhere}.doubao-recovery summary{width:fit-content;cursor:pointer}.doubao-recovery summary:focus-visible{outline:2px solid var(--geo-primary,#087f78);outline-offset:4px}@media(max-width:650px){.doubao-recovery{grid-template-columns:minmax(0,1fr)}.doubao-recovery>.el-button{justify-self:start}}
</style>
