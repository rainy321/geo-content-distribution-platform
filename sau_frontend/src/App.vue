<template>
  <div id="app">
    <el-container>
      <el-aside :width="sidebarCollapsed ? '68px' : '224px'">
        <div class="sidebar">
          <div class="logo">
            <div class="logo-mark">G</div>
            <div v-show="!sidebarCollapsed" class="logo-copy">
              <strong>GEO 内容引擎</strong>
              <span>CONTENT OPERATIONS</span>
            </div>
          </div>
          <el-menu
            :router="true"
            :default-active="activeMenu"
            :collapse="sidebarCollapsed"
            class="sidebar-menu"
            background-color="#102b31"
            text-color="#fff"
            active-text-color="#64d6cf"
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
              <el-icon class="toggle-sidebar" @click="toggleSidebar"><Fold /></el-icon>
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
import { ref, computed, onBeforeUnmount, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import {
  HomeFilled, User, DataAnalysis,
  Fold, Picture, Upload, CollectionTag, MagicStick, DocumentCopy, Aim, Setting
} from '@element-plus/icons-vue'

const route = useRoute()

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

onMounted(() => {
  smallScreenQuery = window.matchMedia('(max-width: 760px)')
  isSmallScreen.value = smallScreenQuery.matches
  smallScreenQuery.addEventListener('change', syncSmallScreen)
})

onBeforeUnmount(() => {
  smallScreenQuery?.removeEventListener('change', syncSmallScreen)
})
</script>

<style lang="scss" scoped>
@use '@/styles/variables.scss' as *;

#app {
  min-height: 100vh;
}

.el-container {
  height: 100vh;
}

.el-aside {
  background-color: #102b31;
  color: #fff;
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
      background-color: #0b2429;
      border-bottom: 1px solid rgba(255, 255, 255, 0.08);
      overflow: hidden;
      
      .logo-mark {
        width: 38px;
        height: 38px;
        flex: 0 0 38px;
        display: grid;
        place-items: center;
        border: 1px solid rgba(100, 214, 207, 0.65);
        border-radius: 11px 3px 11px 3px;
        color: #9de7e2;
        font: 700 19px/1 "Arial Narrow", "Microsoft YaHei", sans-serif;
        box-shadow: inset 0 0 0 3px rgba(100, 214, 207, 0.08);
      }

      .logo-copy {
        display: flex;
        flex-direction: column;
        margin-left: 11px;
        white-space: nowrap;

        strong {
          color: #f4fbfa;
          font-size: 15px;
          letter-spacing: 0.04em;
        }

        span {
          margin-top: 2px;
          color: rgba(208, 232, 230, 0.55);
          font: 600 9px/1.2 "Cascadia Mono", monospace;
          letter-spacing: 0.15em;
        }
      }
    }
    
    .sidebar-menu {
      border-right: none;
      flex: 1;
      
      .el-menu-item {
        display: flex;
        align-items: center;
        
        .el-icon {
          margin-right: 10px;
          font-size: 18px;
        }

        &.is-active {
          background: linear-gradient(90deg, rgba(57, 184, 178, 0.18), transparent);
          box-shadow: inset 3px 0 #39b8b2;
        }
      }
    }
  }
}

.el-header {
  background-color: #fff;
  box-shadow: 0 1px 4px rgba(0, 21, 41, 0.08);
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
        font-size: 20px;
        cursor: pointer;
        color: $text-regular;
        
        &:hover {
          color: $primary-color;
        }
      }

      .workspace-label {
        color: #66757d;
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
  background-color: $bg-color-page;
  padding: 20px;
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
}
</style>
