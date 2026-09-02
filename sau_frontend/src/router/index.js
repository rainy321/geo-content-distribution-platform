import { createRouter, createWebHashHistory } from 'vue-router'
import Dashboard from '../views/Dashboard.vue'
import AccountManagement from '../views/AccountManagement.vue'
import MaterialManagement from '../views/MaterialManagement.vue'
import DistributionCenter from '../views/DistributionCenter.vue'
import About from '../views/About.vue'
import ProjectManagement from '../views/ProjectManagement.vue'
import ContentCreation from '../views/ContentCreation.vue'
import ContentLibrary from '../views/ContentLibrary.vue'
import GeoOptimization from '../views/GeoOptimization.vue'
import SystemSettings from '../views/SystemSettings.vue'

const routes = [
  {
    path: '/',
    name: 'Dashboard',
    component: Dashboard
  },
  {
    path: '/account-management',
    name: 'AccountManagement',
    component: AccountManagement
  },
  {
    path: '/projects',
    name: 'ProjectManagement',
    component: ProjectManagement
  },
  {
    path: '/content-create',
    name: 'ContentCreation',
    component: ContentCreation
  },
  {
    path: '/content-library',
    name: 'ContentLibrary',
    component: ContentLibrary
  },
  {
    path: '/geo-optimize',
    name: 'GeoOptimization',
    component: GeoOptimization
  },
  {
    path: '/material-management',
    name: 'MaterialManagement',
    component: MaterialManagement
  },
  {
    path: '/publish-center',
    name: 'PublishCenter',
    component: DistributionCenter
  },
  {
    path: '/settings',
    name: 'SystemSettings',
    component: SystemSettings
  },
  {
    path: '/about',
    name: 'About',
    component: About
  }
]

const router = createRouter({
  history: createWebHashHistory(),
  routes
})

export default router
