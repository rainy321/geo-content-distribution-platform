<template>
  <div id="app">
    <div v-if="authState.loading" class="auth-bootstrap" aria-live="polite">
      <span class="bootstrap-mark">G</span>
      <strong>正在连接工作区</strong>
    </div>
    <div v-else-if="authState.error" class="auth-bootstrap auth-bootstrap-error" role="alert">
      <span class="bootstrap-mark">!</span>
      <strong>无法连接工作区</strong>
      <small>{{ authState.error }}</small>
      <el-button type="primary" @click="loadAuthStatus">重新连接</el-button>
    </div>
    <AccessGate
      v-else-if="authState.required && !authState.authenticated"
      @authenticated="handleAuthenticated"
    />
    <el-container v-else class="workspace-shell" :class="{ 'is-sidebar-collapsed': sidebarCollapsed }">
      <el-aside class="workspace-sidebar" :width="sidebarCollapsed ? '68px' : '224px'">
        <nav class="sidebar" aria-label="工作区导航">
          <router-link to="/journey" class="logo" aria-label="GEO 内容引擎，开始优化">
            <div class="logo-mark">G</div>
            <div v-show="!sidebarCollapsed" class="logo-copy">
              <strong>GEO 内容引擎</strong>
              <span>品牌的 AI 搜索工作区</span>
            </div>
          </router-link>
          <el-menu
            router
            :default-active="activeMenu"
            :collapse="sidebarCollapsed"
            :default-openeds="openMenuGroups"
            class="sidebar-menu"
            background-color="#102b31"
            text-color="#c8d8da"
            active-text-color="#a4ebe4"
          >
            <el-menu-item index="/journey" class="journey-menu-item">
              <el-icon><Guide /></el-icon>
              <template #title>开始优化</template>
            </el-menu-item>
            <el-menu-item-group class="workflow-menu-group">
              <template #title>工作流程</template>
              <el-menu-item v-for="item in workflowMenu" :key="item.path" :index="item.path">
                <el-icon><component :is="item.icon" /></el-icon>
                <template #title>{{ item.label }}</template>
              </el-menu-item>
            </el-menu-item-group>
            <el-sub-menu index="management" class="management-menu-group">
              <template #title>
                <el-icon><Grid /></el-icon>
                <span>管理工具</span>
              </template>
              <el-menu-item v-for="item in managementMenu" :key="item.path" :index="item.path">
                <el-icon><component :is="item.icon" /></el-icon>
                <template #title>{{ item.label }}</template>
              </el-menu-item>
            </el-sub-menu>
          </el-menu>
          <div v-if="!sidebarCollapsed" class="sidebar-footer">发现来源，让内容找到去处</div>
        </nav>
      </el-aside>
      <el-container class="workspace-body">
        <el-header class="workspace-header">
          <div class="header-content">
            <div class="header-left">
              <button
                class="toggle-sidebar"
                type="button"
                :aria-label="sidebarCollapsed ? '展开导航' : '收起导航'"
                :aria-expanded="!sidebarCollapsed"
                @click="toggleSidebar"
              >
                <el-icon><component :is="sidebarCollapsed ? Expand : Fold" /></el-icon>
              </button>
              <nav class="workspace-breadcrumb" aria-label="当前位置">
                <router-link to="/journey" class="workspace-label">GEO 工作区</router-link>
                <el-icon aria-hidden="true"><ArrowRight /></el-icon>
                <span class="current-page" aria-current="page">{{ currentPage.label }}</span>
              </nav>
            </div>
            <span class="header-context">{{ currentPage.group }}</span>
          </div>
        </el-header>
        <el-main ref="workspaceMain" class="workspace-main">
          <div class="route-stage">
            <router-view v-slot="{ Component, route: currentRoute }">
              <Transition name="page" @before-enter="resetPageScroll">
                <component :is="Component" :key="currentRoute.path" />
              </Transition>
            </router-view>
          </div>
        </el-main>
      </el-container>
    </el-container>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import AccessGate from '@/components/AccessGate.vue'
import { authApi } from '@/api/auth'
import {
  HomeFilled, User, DataAnalysis, Fold, Expand, Picture, Upload,
  CollectionTag, MagicStick, DocumentCopy, Aim, Setting, Guide,
  Grid, InfoFilled, ArrowRight
} from '@element-plus/icons-vue'

const route = useRoute()
const workspaceMain = ref(null)
const resetPageScroll = () => { const element = workspaceMain.value?.$el; if (element) element.scrollTop = 0 }
const authState = reactive({ loading: true, required: false, authenticated: false, error: '' })
const workflowMenu = [
  { path: '/geo-tests', label: 'AI 搜索测试', icon: DataAnalysis },
  { path: '/geo-content', label: '采集驱动内容', icon: Aim },
  { path: '/content-library', label: '内容库', icon: DocumentCopy },
  { path: '/publish-center', label: '发布中心', icon: Upload }
]
const managementMenu = [
  { path: '/projects', label: '品牌项目', icon: CollectionTag },
  { path: '/account-management', label: '媒体账号', icon: User },
  { path: '/material-management', label: '素材管理', icon: Picture },
  { path: '/content-create', label: 'AI 内容创作', icon: MagicStick },
  { path: '/geo-optimize', label: 'GEO 优化', icon: Aim },
  { path: '/', label: '首页', icon: HomeFilled },
  { path: '/settings', label: '系统设置', icon: Setting },
  { path: '/about', label: '关于', icon: InfoFilled }
]
const activeMenu = computed(() => route.path)
const currentPage = computed(() => {
  if (route.path === '/journey') return { label: '开始优化', group: '从品牌到效果反馈' }
  const workflow = workflowMenu.find(item => item.path === route.path)
  if (workflow) return { ...workflow, group: '工作流程' }
  const management = managementMenu.find(item => item.path === route.path)
  return { label: management?.label || route.meta?.title || '工作区', group: '管理工具' }
})
const openMenuGroups = computed(() => managementMenu.some(item => item.path === route.path) ? ['management'] : [])
const isCollapse = ref(false)
const isSmallScreen = ref(false)
const mobileExpanded = ref(false)
let smallScreenQuery = null
const sidebarCollapsed = computed(() => isSmallScreen.value ? !mobileExpanded.value : isCollapse.value)
const syncSmallScreen = event => {
  isSmallScreen.value = event.matches
  mobileExpanded.value = false
}
const toggleSidebar = () => {
  if (isSmallScreen.value) mobileExpanded.value = !mobileExpanded.value
  else isCollapse.value = !isCollapse.value
}
watch(() => route.path, () => { mobileExpanded.value = false })

const loadAuthStatus = async () => {
  authState.loading = true
  authState.error = ''
  try {
    const response = await authApi.getStatus()
    authState.required = Boolean(response.data?.required)
    authState.authenticated = Boolean(response.data?.authenticated)
  } catch (error) {
    authState.error = error?.response?.data?.msg || error?.message || '检查后端服务后重试'
  } finally {
    authState.loading = false
  }
}
const handleAuthenticated = () => {
  authState.required = true
  authState.authenticated = true
  authState.error = ''
}
const handleAuthRequired = () => {
  authState.loading = false
  authState.required = true
  authState.authenticated = false
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

#app { min-height: 100vh; }
.auth-bootstrap {
  display: grid;
  min-height: 100vh;
  place-content: center;
  justify-items: center;
  gap: 14px;
  background: $bg-color-page;
  color: $text-primary;
  .bootstrap-mark {
    display: grid;
    width: 46px;
    height: 46px;
    place-items: center;
    border: 1px solid #39b8b2;
    border-radius: 12px;
    color: $primary-color;
    font: 800 22px/1 'Arial Narrow', sans-serif;
  }
  strong { font-size: 14px; }
  small { max-width: 420px; color: $text-secondary; text-align: center; }
}
.auth-bootstrap-error .bootstrap-mark { border-color: $warning-color; color: #9a5b18; }
.workspace-shell { height: 100vh; overflow: hidden; }
.workspace-sidebar {
  background: #102b31;
  color: #fff;
  height: 100vh;
  overflow: hidden;
  transition: width var(--geo-duration-panel) var(--geo-ease-out);
  flex-shrink: 0;
}
.sidebar { display: flex; flex-direction: column; height: 100%; }
.logo {
  display: flex;
  flex: 0 0 76px;
  align-items: center;
  padding: 0 16px;
  overflow: hidden;
  border-bottom: 1px solid rgba(255, 255, 255, 0.07);
  background: #0b2429;
  .logo-mark {
    display: grid;
    flex: 0 0 36px;
    width: 36px;
    height: 36px;
    place-items: center;
    border: 1px solid rgba(100, 214, 207, 0.6);
    border-radius: 10px;
    color: #a4ebe4;
    font: 700 19px/1 'Arial Narrow', sans-serif;
    box-shadow: inset 0 0 0 3px rgba(100, 214, 207, 0.05);
  }
  .logo-copy {
    display: flex;
    flex-direction: column;
    gap: 4px;
    margin-left: 11px;
    white-space: nowrap;
    strong { color: #f3fbfa; font-size: 15px; font-weight: 650; letter-spacing: 0.025em; }
    span { color: #a1b9bd; font-size: 10px; letter-spacing: 0.025em; }
  }
}
.sidebar-menu {
  flex: 1;
  min-height: 0;
  padding: 16px 0;
  overflow-x: hidden;
  overflow-y: auto;
  border-right: 0;
  scrollbar-width: thin;
  scrollbar-color: #395257 transparent;
  :deep(.el-menu-item), :deep(.el-sub-menu__title) {
    height: 44px;
    margin: 3px 10px;
    padding-left: 16px !important;
    border-radius: 9px;
    font-size: 13px;
    line-height: 44px;
    transition: color var(--geo-duration-fast), background-color var(--geo-duration-fast);
    .el-icon { margin-right: 11px; font-size: 18px; }
    &:hover { background-color: rgba(255, 255, 255, 0.055); }
    &.is-active { background: #21484c; color: #b0f1eb; }
  }
  :deep(.journey-menu-item) { margin-bottom: 14px; color: #e6f5f3; font-weight: 600; }
  :deep(.el-menu-item-group__title) {
    padding: 8px 26px 6px !important;
    color: #94adb2;
    font-size: 10px;
    line-height: 18px;
    letter-spacing: 0.08em;
  }
  :deep(.management-menu-group) {
    margin-top: 18px;
    padding-top: 10px;
    border-top: 1px solid rgba(255, 255, 255, 0.07);
    .el-menu { background-color: #0e272d; }
    .el-menu-item { font-size: 12px; }
  }
  &.el-menu--collapse {
    width: 68px;
    :deep(.el-menu-item-group__title) { height: 10px; overflow: hidden; padding: 0 !important; font-size: 0; }
    :deep(.el-menu-item), :deep(.el-sub-menu__title) { padding-left: 14px !important; }
    :deep(.el-menu-tooltip__trigger) { padding: 0 14px !important; }
    :deep(.el-sub-menu__icon-arrow) { display: none; }
  }
}
.sidebar-footer { padding: 16px 24px 20px; color: #91acb0; font-size: 10px; white-space: nowrap; }
.sidebar :deep(.el-menu-item:focus-visible),
.sidebar :deep(.el-sub-menu__title:focus-visible),
.logo:focus-visible { outline-color: #a4ebe4; outline-offset: -3px; }
.workspace-body { min-width: 0; flex-direction: column; }
.workspace-header {
  z-index: 2;
  flex-shrink: 0;
  height: 60px;
  padding: 0;
  border-bottom: 1px solid $border-light;
  background: $bg-color;
}
.header-content { display: flex; justify-content: space-between; align-items: center; height: 100%; padding: 0 24px; gap: 16px; }
.header-left { display: flex; align-items: center; gap: 16px; min-width: 0; }
.toggle-sidebar {
  display: grid;
  width: 34px;
  height: 34px;
  flex: 0 0 34px;
  place-items: center;
  border-radius: 8px;
  color: $text-secondary;
  font-size: 18px;
  transition: color var(--geo-duration-fast), background-color var(--geo-duration-fast), transform var(--geo-duration-normal) var(--geo-ease-spring);
  &:hover { background: $bg-color-page; color: $primary-color; }
  &:active { transform: scale(0.94); }
}
.workspace-breadcrumb { display: flex; align-items: center; gap: 12px; min-width: 0; font-size: 12px; }
.workspace-breadcrumb > .el-icon { flex-shrink: 0; font-size: 10px; color: #9babb0; }
.workspace-label { flex-shrink: 0; color: $text-secondary; }
.workspace-label:hover { color: $primary-color; }
.current-page { overflow: hidden; color: $text-primary; text-overflow: ellipsis; white-space: nowrap; font-weight: 550; }
.header-context { color: $text-secondary; font-size: 11px; white-space: nowrap; }
.workspace-main { padding: 28px 32px 40px; overflow-y: auto; background: $bg-color-page; }
.route-stage { position: relative; min-height: 100%; }
@media (min-width: 1600px) { .workspace-main { padding-left: 40px; padding-right: 40px; } }
@media (max-width: 1000px) {
  .workspace-main { padding: 24px; }
  .header-context { display: none; }
}
@media (max-width: 760px) {
  .workspace-sidebar { position: absolute; inset: 0 auto 0 0; z-index: 5; box-shadow: 6px 0 24px rgba(16, 43, 49, 0.14); }
  .workspace-body { margin-left: 68px; }
  .header-content { padding: 0 12px; }
  .header-left { gap: 8px; }
  .workspace-breadcrumb { gap: 8px; }
  .workspace-label { display: none; }
  .workspace-breadcrumb > .el-icon { display: none; }
  .workspace-main { padding: 18px 14px 28px; }
}
</style>
