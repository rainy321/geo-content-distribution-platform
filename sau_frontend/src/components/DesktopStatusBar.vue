<template>
  <div
    v-if="status.desktop"
    :class="['desktop-status', `is-${publishingState}`]"
    :aria-label="`桌面运行状态：${publishingLabel}`"
  >
    <span class="desktop-signal" aria-hidden="true" />
    <el-tooltip :content="publishingHint" placement="bottom">
      <div class="desktop-copy">
        <strong>桌面工作区</strong>
        <span>{{ status.content_engine || '内容引擎' }} · {{ publishingLabel }}</span>
      </div>
    </el-tooltip>
    <el-tooltip content="打开本机数据目录" placement="bottom">
      <button class="data-button" type="button" @click="openDataDirectory">
        <el-icon><FolderOpened /></el-icon>
        <span>本机数据</span>
      </button>
    </el-tooltip>
    <el-tooltip content="创建本地数据库备份" placement="bottom">
      <button class="backup-button" type="button" :disabled="backingUp" @click="createBackup">
        <el-icon :class="{ spinning: backingUp }"><Refresh /></el-icon>
        <span>{{ backingUp ? '备份中' : '立即备份' }}</span>
      </button>
    </el-tooltip>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { FolderOpened, Refresh } from '@element-plus/icons-vue'

const emit = defineEmits(['status'])

const backingUp = ref(false)

const status = reactive({
  desktop: false,
  version: '',
  data_directory: '',
  content_engine: '',
  real_publishing_authorized: false,
  real_publishing_enabled: false,
  publishing_runtime: {
    ready: false,
    browser_ready: false,
    missing_modules: [],
    browser_probe_error: ''
  }
})

const publishingState = computed(() => {
  if (!status.real_publishing_authorized) return 'locked'
  if (status.real_publishing_enabled && status.publishing_runtime?.ready) return 'enabled'
  return 'blocked'
})

const publishingLabel = computed(() => ({
  enabled: '真实发布已开启',
  blocked: '运行时未就绪',
  locked: '发布锁定'
}[publishingState.value]))

const publishingHint = computed(() => {
  if (publishingState.value === 'enabled') {
    return '真实发布已授权且组件就绪；每个任务仍需绑定账号并单独确认。'
  }
  if (publishingState.value === 'locked') {
    return '当前是安全构建，媒体账号与真实发布功能未授权。'
  }
  const runtime = status.publishing_runtime || {}
  if (runtime.browser_ready === false) return '真实发布已授权，但未找到 Google Chrome。安装后请重启软件。'
  if (Array.isArray(runtime.missing_modules) && runtime.missing_modules.length) {
    return `真实发布已授权，但缺少发布组件：${runtime.missing_modules.join('、')}`
  }
  return runtime.browser_probe_error || '真实发布已授权，但发布组件探针未通过。请检查本机运行环境。'
})

const loadDesktopStatus = async () => {
  const api = window.pywebview?.api
  if (!api?.get_runtime_status) return
  try {
    Object.assign(status, await api.get_runtime_status())
    emit('status', {
      ...status,
      publishing_runtime: { ...(status.publishing_runtime || {}) }
    })
  } catch (error) {
    console.warn('无法读取桌面运行状态', error)
  }
}

const openDataDirectory = async () => {
  const result = await window.pywebview?.api?.open_data_directory?.()
  if (result?.ok) {
    ElMessage.success(result.message || '已打开本机数据目录')
  } else {
    ElMessage.error(result?.message || '无法打开本机数据目录')
  }
}

const createBackup = async () => {
  if (backingUp.value) return
  backingUp.value = true
  try {
    const result = await window.pywebview?.api?.create_database_backup?.()
    if (result?.ok) {
      ElMessage.success(result.message || '本地备份已创建')
    } else {
      ElMessage.error(result?.message || '无法创建本地备份')
    }
  } catch (error) {
    ElMessage.error(error?.message || '无法创建本地备份')
  } finally {
    backingUp.value = false
  }
}

onMounted(() => {
  window.addEventListener('pywebviewready', loadDesktopStatus)
  loadDesktopStatus()
})

onBeforeUnmount(() => {
  window.removeEventListener('pywebviewready', loadDesktopStatus)
})
</script>

<style lang="scss" scoped>
.desktop-status {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 9px;
  padding: 5px 6px 5px 10px;
  border: 1px solid var(--geo-line);
  border-radius: 10px;
  background: #fff;
}

.desktop-signal {
  width: 7px;
  height: 7px;
  flex: 0 0 7px;
  border-radius: 50%;
  background: #32674d;
  box-shadow: 0 0 0 4px rgba(50, 103, 77, 0.1);
}

.desktop-status.is-enabled {
  border-color: #d39a47;
  background: #fff8ec;

  .desktop-signal {
    background: #b66d16;
    box-shadow: 0 0 0 4px rgba(182, 109, 22, 0.12);
  }
}

.desktop-status.is-blocked {
  border-color: #d6b678;
  background: #fffbf2;

  .desktop-signal {
    background: #9b742e;
    box-shadow: 0 0 0 4px rgba(155, 116, 46, 0.11);
  }
}

.desktop-copy {
  display: grid;
  min-width: 0;
  gap: 1px;

  strong {
    color: var(--geo-ink);
    font-size: 11px;
    line-height: 1.2;
  }

  span {
    max-width: 150px;
    overflow: hidden;
    color: var(--geo-muted);
    font: 500 9px/1.2 "Cascadia Mono", Consolas, monospace;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
}

.data-button,
.backup-button {
  display: inline-flex;
  height: 30px;
  align-items: center;
  gap: 5px;
  padding: 0 9px;
  border: 0;
  border-radius: 7px;
  background: var(--geo-ink);
  color: #fff;
  cursor: pointer;
  font-size: 11px;

  &:hover {
    background: #2b2c2f;
  }

  &:focus-visible {
    outline: 2px solid #32674d;
    outline-offset: 2px;
  }

  &:disabled {
    cursor: wait;
    opacity: 0.65;
  }
}

.backup-button {
  background: #fff;
  color: var(--geo-ink);
  border: 1px solid var(--geo-line);

  &:hover:not(:disabled) {
    background: var(--geo-soft);
  }
}

.spinning { animation: status-spin 0.9s linear infinite; }
@keyframes status-spin { to { transform: rotate(360deg); } }

@media (max-width: 900px) {
  .desktop-copy,
  .data-button span,
  .backup-button span {
    display: none;
  }
}
</style>
