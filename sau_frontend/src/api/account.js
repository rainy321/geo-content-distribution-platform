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

  // 结束由当前软件启动的交互式登录，并让后台关闭对应浏览器。
  cancelMediaLogin(data) {
    return http.post('/api/media-login/cancel', data, { suppressGlobalError: true })
  },

  // 仅移除本机账号绑定和凭证，不代表撤销平台上的其他登录会话。
  disconnectMediaAccount(id) {
    return http.post(
      `/api/media-accounts/${id}/disconnect`,
      { confirm_unbind: true },
      { suppressGlobalError: true }
    )
  },

  // 仅确认平台侧风险已处理；账号仍需重新登录，不会自动解冻或切换账号。
  resolveMediaAccountRisk(id, category) {
    return http.post(
      `/api/media-accounts/${id}/risk/resolve`,
      { confirm_recovery: true, category },
      { suppressGlobalError: true }
    )
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
  }
}
