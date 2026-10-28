<template>
  <aside class="doubao-session" aria-label="豆包写作会话" data-testid="doubao-session-controls">
    <div class="session-row"><strong>豆包写作会话</strong><el-select :model-value="clientMode" :disabled="busy" aria-label="写作豆包界面" @update:model-value="$emit('mode', $event)"><el-option label="豆包对话" value="consumer"/><el-option label="豆包工作" value="work"/></el-select><el-button :disabled="busy" @click="$emit('login')">登录豆包</el-button></div>
    <p>软件为两种界面选项分别保存专用会话；选项不会切换网页实际模式。需要时在专用窗口完成正常验证。</p>
    <details><summary>模型选项与登录说明</summary><el-input :model-value="model" :disabled="busy" placeholder="模型选项可留空，使用当前模型" aria-label="写作模型选项" @update:model-value="$emit('model', $event)"/><DoubaoPreparation/></details>
  </aside>
</template>

<script setup>
import DoubaoPreparation from './DoubaoPreparation.vue'
defineProps({clientMode: {type: String, default: 'consumer'}, model: {type: String, default: ''}, busy: Boolean})
defineEmits(['mode', 'model', 'login'])
</script>

<style scoped>
.doubao-session{padding:14px 16px;margin:12px 0 20px;border:1px solid var(--geo-border,#dce6e2);border-radius:10px;background:#f7faf9;font-size:12px;line-height:1.8;color:var(--geo-muted,#63736f)}.session-row{display:flex;align-items:center;gap:12px;flex-wrap:wrap}.session-row strong{color:var(--geo-text,#243c3c);font-size:13px}.session-row .el-select{width:150px}.doubao-session p{margin:6px 0}.doubao-session details{margin-top:6px}.doubao-session summary{cursor:pointer;width:fit-content}.doubao-session summary:focus-visible{outline:2px solid var(--geo-primary,#087f78);outline-offset:4px}.doubao-session details>.el-input{max-width:360px;margin-top:12px}@media(max-width:500px){.session-row{gap:8px}.session-row strong{width:100%}}
</style>
