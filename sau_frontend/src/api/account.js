import { http } from '@/utils/request'

// 账号管理相关API
export const accountApi = {
  // GEO 媒体账号总览（只读，不自动访问平台）
  getMediaAccounts() {
    return http.get('/api/media-accounts')
  },

  // 用户显式触发的单账号登录状态检测
  checkMediaAccount(id) {
    return http.post(`/api/media-accounts/${id}/check`)
  },

  // 获取有效账号列表（带验证）
  getValidAccounts() {
    return http.get('/getValidAccounts')
  },

  // 获取账号列表（不带验证，快速加载）
  getAccounts() {
    return http.get('/getAccounts')
  },

  // 添加账号
  addAccount(data) {
    return http.post('/account', data)
  },

  // 更新账号
  updateAccount(data) {
    return http.post('/updateUserinfo', data)
  },

  // 删除账号
  deleteAccount(id) {
    return http.get(`/deleteAccount?id=${id}`)
  }
}
