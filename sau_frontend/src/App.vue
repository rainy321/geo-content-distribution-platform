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
            <el-menu-item index="/account-management">
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
            <el-menu-item index="/material-management">
              <el-icon><Picture /></el-icon>
              <span>素材管理</span>
            </el-menu-item>
            <el-menu-item index="/publish-center">
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
              <!-- 账号信息已移除 -->
            </div>
          </div>
        </el-header>
        <el-main>
          <router-view />
        </el-main>
      </el-container>
    </el-container>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { useRoute } from 'vue-router'
import AccessGate from '@/components/AccessGate.vue'
import { authApi } from '@/api/auth'
import {
  HomeFilled, User, DataAnalysis,
  Fold, Picture, Upload, CollectionTag, MagicStick, DocumentCopy, Aim, Setting
} from '@element-plus/icons-vue'

const route = useRoute()
const authState = reactive({
  loading: true,
  required: false,
  authenticated: false,
  error: ''
})

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
    }
  }
}

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

  .el-main {
    padding: 12px;
  }

  .el-aside + .el-container {
    width: calc(100vw - 68px);
    max-width: calc(100vw - 68px);
  }
}
</style>
