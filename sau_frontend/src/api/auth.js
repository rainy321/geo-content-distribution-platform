import { http } from '@/utils/request'

export const authApi = {
  getStatus() {
    return http.get('/api/auth/status')
  },

  login(password) {
    return http.post(
      '/api/auth/login',
      { password },
      { suppressGlobalError: true }
    )
  },

  setup(password) {
    return http.post(
      '/api/auth/setup',
      { password },
      { suppressGlobalError: true }
    )
  },

  changePassword(currentPassword, newPassword) {
    return http.post(
      '/api/auth/change-password',
      {
        current_password: currentPassword,
        new_password: newPassword
      },
      { suppressGlobalError: true }
    )
  },

  logout() {
    return http.post('/api/auth/logout')
  }
}
