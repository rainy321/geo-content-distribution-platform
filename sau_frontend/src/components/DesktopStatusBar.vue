<template>
  <div v-if="status.desktop" class="desktop-status" aria-label="桌面运行状态">
    <span class="desktop-signal" aria-hidden="true" />
    <div class="desktop-copy">
      <strong>桌面工作区</strong>
      <span>{{ status.content_engine }} · 发布锁定</span>
    </div>
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
import { onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { FolderOpened, Refresh } from '@element-plus/icons-vue'

const emit = defineEmits(['status'])

const backingUp = ref(false)

const status = reactive({
  desktop: false,
  version: '',
  data_directory: '',
  content_engine: '',
  real_publishing_enabled: false
})

const loadDesktopStatus = async () => {
  const api = window.pywebview?.api
  if (!api?.get_runtime_status) return
  try {
    Object.assign(status, await api.get_runtime_status())
    emit('status', { ...status })
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
