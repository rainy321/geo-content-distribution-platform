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

  logout() {
    return http.post('/api/auth/logout')
  }
}
