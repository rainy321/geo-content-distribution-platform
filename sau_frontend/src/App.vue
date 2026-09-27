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
            <img class="logo-mark" src="/geo-favicon.svg" alt="" aria-hidden="true">
            <div v-show="!sidebarCollapsed" class="logo-copy">
              <strong>GEO</strong>
              <span>VISIBILITY OS</span>
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
            <el-menu-item index="/geo-automation">
              <el-icon><SetUp /></el-icon>
              <span>一键 GEO</span>
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
            <el-menu-item index="/cost-ledger">
              <el-icon><Tickets /></el-icon>
              <span>成本账单</span>
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
              <span class="workspace-label">
                <small>GEO PRODUCT OS</small>
                <strong>{{ currentPageTitle }}</strong>
              </span>
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
          <section
            v-if="publishingWorkspaceVisible && reviewAlerts.length"
            class="global-review-alerts"
            aria-label="平台状态提醒"
          >
            <div class="review-alert-heading">
              <div>
                <small>PLATFORM MONITOR / {{ reviewAlerts.length }} UNREAD</small>
                <strong>平台状态有新结果</strong>
              </div>
              <button type="button" @click="openAlertDestination(reviewAlerts[0])">
                查看详情
              </button>
            </div>
            <article
              v-for="alert in reviewAlerts.slice(0, 3)"
              :key="alert.id"
              :class="['global-review-alert', `severity-${alert.severity || 'warning'}`]"
              :role="alert.severity === 'critical' || alert.severity === 'error' ? 'alert' : 'status'"
            >
              <span class="review-alert-mark">{{ alert.category === 'published' ? '✓' : '!' }}</span>
              <div>
                <strong>{{ alert.title }}</strong>
                <p>{{ alert.message }}</p>
              </div>
              <div class="review-alert-actions">
                <a
                  v-if="safeAlertResultUrl(alert)"
                  :href="safeAlertResultUrl(alert)"
                  target="_blank"
                  rel="noopener noreferrer"
                >打开平台作品链接</a>
                <button type="button" @click="openAlertDestination(alert)">
                  {{ alert.category === 'account_risk' ? '处理账号' : '查看任务' }}
                </button>
                <button type="button" class="acknowledge" @click="acknowledgeReviewAlert(alert)">
                  我知道了
                </button>
              </div>
            </article>
            <p v-if="reviewAlerts.length > 3" class="review-alert-more">
              另有 {{ reviewAlerts.length - 3 }} 条未读提醒，可进入发布中心查看。
            </p>
          </section>
          <router-view v-slot="{ Component }">
            <KeepAlive include="AiVisibility,ContentCreation,GeoAutomation">
              <component :is="Component" />
            </KeepAlive>
          </router-view>
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
import { publishApi } from '@/api/publish'
import {
  HomeFilled, User, DataAnalysis,
  Fold, Picture, Upload, CollectionTag, MagicStick, DocumentCopy, Aim, Setting,
  View, Tickets, UserFilled, ArrowDown, SetUp
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
const reviewAlerts = ref([])
let reviewAlertTimer = null
const notifiedReviewAlertIds = new Set()

// 当前激活的菜单项
const activeMenu = computed(() => {
  return route.path
})

const pageTitles = {
  '/': '品牌可见度工作台',
  '/account-management': '媒体账号',
  '/projects': '品牌项目',
  '/geo-automation': '一键 GEO',
  '/content-create': 'AI 内容创作',
  '/content-library': '内容库',
  '/geo-optimize': 'GEO 优化',
  '/ai-visibility': 'AI 可见度',
  '/cost-ledger': '成本账单',
  '/material-management': '素材管理',
  '/publish-center': '发布中心',
  '/settings': '系统设置',
  '/about': '关于'
}
const currentPageTitle = computed(() => pageTitles[route.path] || '内容运营工作区')

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
    if (!authState.required || authState.authenticated) startReviewAlertPolling()
  }
}

const handleAuthenticated = () => {
  authState.required = true
  authState.authenticated = true
  authState.setupRequired = false
  authState.error = ''
  startReviewAlertPolling()
}

const handleAuthRequired = () => {
  authState.loading = false
  authState.required = true
  authState.authenticated = false
  stopReviewAlertPolling()
  reviewAlerts.value = []
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
    stopReviewAlertPolling()
    reviewAlerts.value = []
  } catch (error) {
    ElMessage.error(error?.response?.data?.msg || error?.message || '退出失败')
  }
}

const safeAlertResultUrl = alert => {
  const value = String(alert?.result_url || '').trim()
  if (!value) return ''
  try {
    const parsed = new URL(value)
    return parsed.protocol === 'https:' ? parsed.toString() : ''
  } catch {
    return ''
  }
}

const openAlertDestination = alert => {
  if (alert?.category === 'account_risk') {
    router.push({
      path: '/account-management',
      query: alert.account_id ? { focus_account_id: String(alert.account_id) } : {}
    })
    return
  }
  router.push({
    path: '/publish-center',
    query: alert?.publish_job_id ? { focus_job_id: String(alert.publish_job_id) } : {}
  })
}

const fetchReviewAlerts = async () => {
  if (
    authState.loading
    || (authState.required && !authState.authenticated)
    || !publishingWorkspaceVisible.value
  ) return
  try {
    const response = await publishApi.getReviewMonitor({
      unread_only: true,
      alert_limit: 10
    })
    const nextAlerts = Array.isArray(response.data?.alerts) ? response.data.alerts : []
    reviewAlerts.value = nextAlerts
    nextAlerts.slice(0, 3).forEach(alert => {
      if (notifiedReviewAlertIds.has(alert.id)) return
      notifiedReviewAlertIds.add(alert.id)
      const options = {
        message: alert.title || '平台状态有变化',
        duration: 8000,
        showClose: true
      }
      if (alert.severity === 'critical' || alert.severity === 'error') {
        ElMessage.error(options)
      } else if (alert.category === 'published') {
        ElMessage.success(options)
      } else {
        ElMessage.warning(options)
      }
    })
  } catch (error) {
    // This background read must never interrupt the current workspace flow.
    console.error('平台状态提醒同步失败:', error)
  }
}

const stopReviewAlertPolling = () => {
  if (reviewAlertTimer) window.clearInterval(reviewAlertTimer)
  reviewAlertTimer = null
}

const startReviewAlertPolling = () => {
  stopReviewAlertPolling()
  void fetchReviewAlerts()
  reviewAlertTimer = window.setInterval(fetchReviewAlerts, 30_000)
}

const acknowledgeReviewAlert = async alert => {
  if (!alert?.id) return
  const previous = reviewAlerts.value
  reviewAlerts.value = previous.filter(item => item.id !== alert.id)
  try {
    await publishApi.acknowledgeReviewAlert(alert.id)
  } catch (error) {
    reviewAlerts.value = previous
    ElMessage.error(error?.response?.data?.msg || '提醒确认未保存，请稍后重试')
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
  stopReviewAlertPolling()
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
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
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
  transition: width 180ms ease;
  
  .sidebar {
    display: flex;
    flex-direction: column;
    height: 100%;
    
    .logo {
      height: 72px;
      padding: 0 17px;
      display: flex;
      align-items: center;
      background-color: var(--geo-surface);
      border-bottom: 1px solid var(--geo-line);
      overflow: hidden;
      
      .logo-mark {
        width: 34px;
        height: 34px;
        flex: 0 0 34px;
        border-radius: 9px;
        filter: grayscale(1) contrast(1.25) brightness(0.75);
      }

      .logo-copy {
        display: flex;
        flex-direction: column;
        margin-left: 10px;
        white-space: nowrap;

        strong {
          color: var(--geo-ink);
          font-size: 21px;
          font-weight: 800;
          letter-spacing: -0.03em;
        }

        span {
          margin-top: 2px;
          color: #8b8d91;
          font: 600 8px/1.2 "Cascadia Mono", monospace;
          letter-spacing: 0.17em;
        }
      }
    }
    
    .sidebar-menu {
      border-right: none;
      flex: 1;
      padding: 14px 10px;
      
      .el-menu-item {
        display: flex;
        align-items: center;
        height: 45px;
        margin-bottom: 3px;
        padding: 0 13px !important;
        border-radius: 7px;
        color: #56585d;
        font-size: 13px;
        
        .el-icon {
          margin-right: 10px;
          font-size: 17px;
        }

        &.is-active {
          background: var(--geo-ink);
          color: #fff;
          box-shadow: none;

          &::after {
            position: absolute;
            right: 9px;
            width: 4px;
            height: 4px;
            border-radius: 50%;
            background: #fff;
            content: '';
          }
        }

        &:not(.is-active):hover {
          background: var(--geo-soft);
        }
      }
    }
  }
}

.el-aside + .el-container {
  width: 100%;
  max-width: 100%;
  min-width: 0;
  overflow: hidden;
}

.el-header {
  background-color: #fff;
  border-bottom: 1px solid var(--geo-line);
  box-shadow: none;
  padding: 0;
  height: 72px;
  
  .header-content {
    display: flex;
    justify-content: space-between;
    align-items: center;
    height: 100%;
    padding: 0 20px;
    
    .header-left {
      display: flex;
      align-items: center;
      gap: 16px;

      .toggle-sidebar {
        display: grid;
        width: 36px;
        height: 36px;
        place-items: center;
        border: 1px solid transparent;
        border-radius: 7px;
        color: $text-regular;
        font-size: 20px;
        
        &:hover {
          border-color: var(--geo-line);
          background: var(--geo-soft);
          color: $primary-color;
        }
      }

      .workspace-label {
        display: flex;
        flex-direction: column;
        gap: 4px;

        small {
          color: #96989c;
          font: 600 8px/1 "Cascadia Mono", monospace;
          letter-spacing: 0.14em;
        }

        strong {
          color: var(--geo-ink);
          font-size: 13px;
          font-weight: 650;
          letter-spacing: -0.01em;
        }
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
        border-radius: 8px;
        background: #fff;
        color: var(--geo-ink);
        cursor: pointer;
        font-size: 12px;

        &:hover,
        &:focus-visible {
          border-color: #b7b8ba;
          background: var(--geo-soft);
        }

        &:focus-visible {
          outline: 2px solid rgba(30, 99, 214, 0.25);
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

.global-review-alerts {
  position: sticky;
  top: 0;
  z-index: 40;
  display: grid;
  gap: 8px;
  padding: 12px 18px;
  border-bottom: 1px solid #f1b7ad;
  background: #fff8f6;
  box-shadow: 0 5px 18px rgba(72, 24, 17, 0.1);
}

.review-alert-heading,
.global-review-alert,
.review-alert-actions {
  display: flex;
  align-items: center;
}

.review-alert-heading {
  justify-content: space-between;
  gap: 16px;

  div {
    display: flex;
    align-items: baseline;
    gap: 10px;
  }

  small {
    color: #9f2d20;
    font: 700 9px/1 "Cascadia Mono", monospace;
    letter-spacing: 0.12em;
  }

  strong { color: #7a2018; font-size: 14px; }
  button { color: #7a2018; font-size: 12px; text-decoration: underline; }
}

.global-review-alert {
  gap: 12px;
  padding: 10px 12px;
  border: 1px solid #e4c36a;
  border-left: 4px solid #b78213;
  background: #fffdf3;

  &.severity-critical,
  &.severity-error {
    border-color: #efaaa1;
    border-left-color: #c92f23;
    background: #fff1ef;
  }

  &.severity-success {
    border-color: #99d2c1;
    border-left-color: #188663;
    background: #f2fbf8;
  }

  > div:not(.review-alert-actions) { min-width: 0; flex: 1; }
  strong { color: var(--geo-ink); font-size: 13px; }
  p { margin: 3px 0 0; color: #5e5552; font-size: 12px; line-height: 1.45; }
}

.review-alert-mark {
  display: grid;
  width: 24px;
  height: 24px;
  flex: 0 0 24px;
  place-items: center;
  border-radius: 50%;
  background: #9f2d20;
  color: #fff;
  font-weight: 800;
}

.severity-success .review-alert-mark { background: #188663; }

.review-alert-actions {
  flex: 0 0 auto;
  gap: 8px;

  a,
  button {
    padding: 5px 8px;
    border: 1px solid #b9a9a5;
    background: #fff;
    color: var(--geo-ink);
    font-size: 11px;
    text-decoration: none;
  }

  .acknowledge { border-color: transparent; background: transparent; color: #766a66; }
}

.review-alert-more { margin: 0; color: #75645f; font-size: 11px; }

.el-main {
  width: 100%;
  min-width: 0;
  background-color: $bg-color-page;
  padding: 0;
  overflow-x: hidden;
  overflow-y: auto;
}

@media (max-width: 760px) {
  .toggle-sidebar {
    display: none;
  }

  .workspace-label {
    small { display: none !important; }
    strong { font-size: 12px !important; }
  }

  .operator-button span,
  .operator-chevron {
    display: none;
  }

  .el-main {
    padding: 0;
  }

  .global-review-alert { align-items: flex-start; flex-wrap: wrap; }
  .review-alert-actions { width: 100%; padding-left: 36px; flex-wrap: wrap; }

  .el-aside + .el-container {
    width: calc(100vw - 68px);
    max-width: calc(100vw - 68px);
  }
}
</style>
