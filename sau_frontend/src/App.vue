<template>
  <div id="app">
    <div v-if="authState.loading" class="auth-bootstrap" aria-live="polite">
      <span class="bootstrap-mark">G</span>
      <strong>正在确认工作区访问状态</strong>
    </div>
    <div v-else-if="authState.error" class="auth-bootstrap auth-bootstrap-error" role="alert">
      <span class="bootstrap-mark">!</span>
      <strong>无法连接运营工作区</strong>
      <small>{{ authState.error }}</small>
      <el-button type="primary" @click="loadAuthStatus">重新连接</el-button>
    </div>
    <AccessGate
      v-else-if="authState.required && !authState.authenticated"
      :setup-required="authState.setupRequired"
      @authenticated="handleAuthenticated"
    />
    <el-container v-else>
      <el-aside :width="sidebarCollapsed ? '68px' : '224px'">
        <div class="sidebar">
          <div class="logo">
            <div class="logo-mark" aria-hidden="true"><span /></div>
            <div v-show="!sidebarCollapsed" class="logo-copy">
              <strong>GEO</strong>
              <span>CONTENT ENGINE</span>
            </div>
          </div>
          <el-menu
            :router="true"
            :default-active="activeMenu"
            :collapse="sidebarCollapsed"
            class="sidebar-menu"
            background-color="#ffffff"
            text-color="#55575b"
            active-text-color="#ffffff"
          >
            <el-menu-item index="/">
              <el-icon><HomeFilled /></el-icon>
              <span>首页</span>
            </el-menu-item>
            <el-menu-item v-if="publishingWorkspaceVisible" index="/account-management">
              <el-icon><User /></el-icon>
              <span>媒体账号</span>
            </el-menu-item>
            <el-menu-item index="/projects">
              <el-icon><CollectionTag /></el-icon>
              <span>品牌项目</span>
            </el-menu-item>
            <el-menu-item index="/content-create">
              <el-icon><MagicStick /></el-icon>
              <span>AI 内容创作</span>
            </el-menu-item>
            <el-menu-item index="/content-library">
              <el-icon><DocumentCopy /></el-icon>
              <span>内容库</span>
            </el-menu-item>
            <el-menu-item index="/geo-optimize">
              <el-icon><Aim /></el-icon>
              <span>GEO 优化</span>
            </el-menu-item>
            <el-menu-item index="/ai-visibility">
              <el-icon><View /></el-icon>
              <span>AI 可见度</span>
            </el-menu-item>
            <el-menu-item index="/material-management">
              <el-icon><Picture /></el-icon>
              <span>素材管理</span>
            </el-menu-item>
            <el-menu-item v-if="publishingWorkspaceVisible" index="/publish-center">
              <el-icon><Upload /></el-icon>
              <span>发布中心</span>
            </el-menu-item>
            <el-menu-item index="/settings">
              <el-icon><Setting /></el-icon>
              <span>系统设置</span>
            </el-menu-item>
            <el-menu-item index="/about">
              <el-icon><DataAnalysis /></el-icon>
              <span>关于</span>
            </el-menu-item>
          </el-menu>
        </div>
      </el-aside>
      <el-container>
        <el-header>
          <div class="header-content">
            <div class="header-left">
              <button class="toggle-sidebar" type="button" aria-label="折叠或展开侧边导航" @click="toggleSidebar">
                <el-icon><Fold /></el-icon>
              </button>
              <span class="workspace-label">内容运营工作区</span>
            </div>
            <div class="header-right">
              <DesktopStatusBar @status="handleDesktopStatus" />
              <el-dropdown v-if="authState.required" trigger="click" @command="handleOperatorCommand">
                <button class="operator-button" type="button" aria-label="管理员账户菜单">
                  <el-icon><UserFilled /></el-icon>
                  <span>管理员</span>
                  <el-icon class="operator-chevron"><ArrowDown /></el-icon>
                </button>
                <template #dropdown>
                  <el-dropdown-menu>
                    <el-dropdown-item v-if="authState.mode === 'desktop_local'" command="change-password">
                      修改本机口令
                    </el-dropdown-item>
                    <el-dropdown-item command="logout" divided>退出工作区</el-dropdown-item>
                  </el-dropdown-menu>
                </template>
              </el-dropdown>
            </div>
          </div>
        </el-header>
        <el-main>
          <router-view />
        </el-main>
      </el-container>
    </el-container>

    <el-dialog
      v-model="passwordDialog.visible"
      title="修改本机管理员口令"
      width="min(460px, calc(100vw - 32px))"
      destroy-on-close
      @closed="resetPasswordDialog"
    >
      <el-form label-position="top" @submit.prevent="changePassword">
        <el-form-item label="当前口令">
          <el-input
            v-model="passwordDialog.current"
            type="password"
            autocomplete="current-password"
            show-password
          />
        </el-form-item>
        <el-form-item label="新口令">
          <el-input
            v-model="passwordDialog.next"
            type="password"
            autocomplete="new-password"
            placeholder="至少 12 个字符"
            show-password
          />
        </el-form-item>
        <el-form-item label="确认新口令">
          <el-input
            v-model="passwordDialog.confirmation"
            type="password"
            autocomplete="new-password"
            show-password
            @keyup.enter="changePassword"
          />
        </el-form-item>
        <p class="password-dialog-hint">软件只更新本机加盐哈希，不保存可找回的明文口令。</p>
        <p v-if="passwordDialog.error" class="password-dialog-error" role="alert">{{ passwordDialog.error }}</p>
      </el-form>
      <template #footer>
        <el-button @click="passwordDialog.visible = false">取消</el-button>
        <el-button type="primary" :loading="passwordDialog.submitting" :disabled="!canChangePassword" @click="changePassword">
          更新口令
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import AccessGate from '@/components/AccessGate.vue'
import DesktopStatusBar from '@/components/DesktopStatusBar.vue'
import { authApi } from '@/api/auth'
import {
  HomeFilled, User, DataAnalysis,
  Fold, Picture, Upload, CollectionTag, MagicStick, DocumentCopy, Aim, Setting,
  View, UserFilled, ArrowDown
} from '@element-plus/icons-vue'

const route = useRoute()
const router = useRouter()
const authState = reactive({
  loading: true,
  required: false,
  authenticated: false,
  setupRequired: false,
  mode: 'disabled',
  error: ''
})
const passwordDialog = reactive({
  visible: false,
  current: '',
  next: '',
  confirmation: '',
  submitting: false,
  error: ''
})
const canChangePassword = computed(() => Boolean(
  passwordDialog.current
  && passwordDialog.next
  && passwordDialog.confirmation
  && !passwordDialog.submitting
))
const desktopMode = ref(false)
const desktopStatusResolved = ref(false)
const desktopRealPublishingAuthorized = ref(false)
const publishingWorkspaceVisible = computed(() => (
  !desktopMode.value || desktopRealPublishingAuthorized.value
))

// 当前激活的菜单项
const activeMenu = computed(() => {
  return route.path
})

// 侧边栏折叠状态
const isCollapse = ref(false)
const isSmallScreen = ref(false)
let smallScreenQuery = null

const sidebarCollapsed = computed(() => isCollapse.value || isSmallScreen.value)

const syncSmallScreen = (event) => {
  isSmallScreen.value = event.matches
}

// 切换侧边栏折叠状态
const toggleSidebar = () => {
  isCollapse.value = !isCollapse.value
}

const loadAuthStatus = async () => {
  authState.loading = true
  authState.error = ''
  try {
    const response = await authApi.getStatus()
    authState.required = Boolean(response.data?.required)
    authState.authenticated = Boolean(response.data?.authenticated)
    authState.setupRequired = Boolean(response.data?.setup_required)
    authState.mode = String(response.data?.mode || 'disabled')
    desktopMode.value = desktopMode.value || authState.mode === 'desktop_local'
  } catch (error) {
    authState.error = error?.response?.data?.msg || error?.message || '检查后端服务后重试'
  } finally {
    authState.loading = false
  }
}

const handleAuthenticated = () => {
  authState.required = true
  authState.authenticated = true
  authState.setupRequired = false
  authState.error = ''
}

const handleAuthRequired = () => {
  authState.loading = false
  authState.required = true
  authState.authenticated = false
}

const enforcePublishingRouteBoundary = () => {
  if (
    desktopStatusResolved.value
    && desktopMode.value
    && !desktopRealPublishingAuthorized.value
    && ['/account-management', '/publish-center'].includes(route.path)
  ) {
    ElMessage.warning('当前安全构建未授权媒体账号与真实发布功能')
    router.replace('/')
  }
}

const handleDesktopStatus = (status) => {
  desktopMode.value = Boolean(status?.desktop)
  desktopRealPublishingAuthorized.value = Boolean(status?.real_publishing_authorized)
  desktopStatusResolved.value = true
  enforcePublishingRouteBoundary()
}

watch(() => route.path, enforcePublishingRouteBoundary)

const resetPasswordDialog = () => {
  passwordDialog.current = ''
  passwordDialog.next = ''
  passwordDialog.confirmation = ''
  passwordDialog.submitting = false
  passwordDialog.error = ''
}

const handleOperatorCommand = async (command) => {
  if (command === 'change-password') {
    passwordDialog.visible = true
    return
  }
  if (command !== 'logout') return
  try {
    await authApi.logout()
    authState.authenticated = false
    authState.setupRequired = false
  } catch (error) {
    ElMessage.error(error?.response?.data?.msg || error?.message || '退出失败')
  }
}

const changePassword = async () => {
  if (!canChangePassword.value) return
  if (passwordDialog.next !== passwordDialog.confirmation) {
    passwordDialog.error = '两次输入的新口令不一致'
    return
  }
  passwordDialog.submitting = true
  passwordDialog.error = ''
  try {
    await authApi.changePassword(passwordDialog.current, passwordDialog.next)
    ElMessage.success('本机管理员口令已更新')
    passwordDialog.visible = false
  } catch (error) {
    passwordDialog.error = error?.response?.data?.msg || error?.message || '口令更新失败'
  } finally {
    passwordDialog.submitting = false
  }
}

onMounted(() => {
  window.addEventListener('geo-auth-required', handleAuthRequired)
  smallScreenQuery = window.matchMedia('(max-width: 760px)')
  isSmallScreen.value = smallScreenQuery.matches
  smallScreenQuery.addEventListener('change', syncSmallScreen)
  loadAuthStatus()
})

onBeforeUnmount(() => {
  window.removeEventListener('geo-auth-required', handleAuthRequired)
  smallScreenQuery?.removeEventListener('change', syncSmallScreen)
})
</script>

<style lang="scss" scoped>
@use '@/styles/variables.scss' as *;

#app {
  min-height: 100vh;
}

.auth-bootstrap {
  display: grid;
  min-height: 100vh;
  place-content: center;
  justify-items: center;
  gap: 13px;
  background: var(--geo-canvas);
  color: var(--geo-ink);

  .bootstrap-mark {
    display: grid;
    width: 46px;
    height: 46px;
    place-items: center;
    border: 1px solid var(--geo-ink);
    border-radius: 10px;
    background: var(--geo-ink);
    color: #fff;
    font: 800 22px/1 $font-display;
  }

  strong { font-size: 14px; }
  small { max-width: 420px; color: var(--geo-muted); text-align: center; }
}

.auth-bootstrap-error .bootstrap-mark {
  border-color: #e8a33a;
  color: #b76c00;
}

.el-container {
  height: 100vh;
  min-width: 0;
}

#app > .el-container {
  width: 100%;
  overflow: hidden;
}

.el-aside {
  flex: 0 0 auto;
  border-right: 1px solid var(--geo-line);
  background-color: var(--geo-surface);
  color: var(--geo-ink);
  height: 100vh;
  overflow: hidden;
  transition: width 0.3s;
  
  .sidebar {
    display: flex;
    flex-direction: column;
    height: 100%;
    
    .logo {
      height: 68px;
      padding: 0 14px;
      display: flex;
      align-items: center;
      background-color: var(--geo-surface);
      border-bottom: 1px solid var(--geo-line);
      overflow: hidden;
      
      .logo-mark {
        position: relative;
        width: 38px;
        height: 38px;
        flex: 0 0 38px;

        &::before,
        span {
          position: absolute;
          left: 9px;
          width: 13px;
          height: 27px;
          border-radius: 5px;
          background: var(--geo-ink);
          content: "";
          transform: rotate(36deg);
        }

        &::before { top: 3px; }

        span {
          right: 8px;
          bottom: 3px;
          left: auto;
          height: 21px;
        }
      }

      .logo-copy {
        display: flex;
        flex-direction: column;
        margin-left: 11px;
        white-space: nowrap;

        strong {
          color: var(--geo-ink);
          font-size: 19px;
          letter-spacing: -0.03em;
        }

        span {
          margin-top: 2px;
          color: var(--geo-muted);
          font: 600 9px/1.2 "Cascadia Mono", monospace;
          letter-spacing: 0.15em;
        }
      }
    }
    
    .sidebar-menu {
      border-right: none;
      flex: 1;
      padding: 12px 8px;
      
      .el-menu-item {
        display: flex;
        align-items: center;
        
        .el-icon {
          margin-right: 10px;
          font-size: 18px;
        }

        &.is-active {
          background: var(--geo-ink);
          color: #fff;
          box-shadow: none;
        }

        &:not(.is-active):hover {
          background: var(--geo-soft);
        }
      }
    }
  }
}

.el-aside + .el-container {
  width: auto;
  min-width: 0;
  flex: 1 1 0%;
  overflow: hidden;
}

.el-header {
  background-color: #fff;
  border-bottom: 1px solid var(--geo-line);
  box-shadow: none;
  padding: 0;
  height: 60px;
  
  .header-content {
    display: flex;
    justify-content: space-between;
    align-items: center;
    height: 100%;
    padding: 0 16px;
    
    .header-left {
      display: flex;
      align-items: center;
      gap: 16px;

      .toggle-sidebar {
        display: grid;
        width: 34px;
        height: 34px;
        place-items: center;
        border-radius: 8px;
        color: $text-regular;
        font-size: 20px;
        
        &:hover {
          background: var(--geo-soft);
          color: $primary-color;
        }
      }

      .workspace-label {
        color: var(--geo-muted);
        font-size: 13px;
        letter-spacing: 0.04em;
      }
    }
    
    .header-right {
      display: flex;
      align-items: center;
      gap: 9px;

      .user-dropdown {
        display: flex;
        align-items: center;
        cursor: pointer;
        
        .username {
          margin: 0 8px;
          color: $text-regular;
        }
        
        .el-icon {
          font-size: 12px;
          color: $text-secondary;
        }
      }

      .operator-button {
        display: inline-flex;
        height: 42px;
        align-items: center;
        gap: 6px;
        padding: 0 11px;
        border: 1px solid var(--geo-line);
        border-radius: 10px;
        background: #fff;
        color: var(--geo-ink);
        cursor: pointer;
        font-size: 12px;

        &:hover,
        &:focus-visible {
          border-color: #a9b5b5;
          background: var(--geo-soft);
        }

        &:focus-visible {
          outline: 2px solid rgba(13, 92, 99, 0.25);
          outline-offset: 2px;
        }

        .operator-chevron {
          color: var(--geo-muted);
          font-size: 10px;
        }
      }
    }
  }
}

.password-dialog-hint,
.password-dialog-error {
  margin: -2px 0 0;
  font-size: 12px;
  line-height: 1.55;
}

.password-dialog-hint { color: var(--geo-muted); }
.password-dialog-error { margin-top: 9px; color: #b42318; }

.el-main {
  width: 100%;
  min-width: 0;
  background-color: $bg-color-page;
  padding: 20px;
  overflow-x: hidden;
  overflow-y: auto;
}

@media (max-width: 760px) {
  .toggle-sidebar {
    display: none;
  }

  .workspace-label {
    font-size: 12px !important;
  }

  .operator-button span,
  .operator-chevron {
    display: none;
  }

  .el-main {
    padding: 12px;
  }

  .el-aside + .el-container {
    width: calc(100vw - 68px);
    max-width: calc(100vw - 68px);
  }
}
</style>
