import { http } from '@/utils/request'

export const aiConfigApi = {
  get(config = {}) {
    return http.get('/api/ai/config', undefined, config)
  },

  save(data, config = {}) {
    return http.put('/api/ai/config', data, config)
  },

  saveRole(role, data, config = {}) {
    return http.put(`/api/ai/config/roles/${encodeURIComponent(role)}`, data, config)
  },

  clearRole(role, config = {}) {
    return http.delete(`/api/ai/config/roles/${encodeURIComponent(role)}`, undefined, config)
  },

  clear(config = {}) {
    return http.delete('/api/ai/config', undefined, config)
  },

  test(config = {}, role = 'writer') {
    return http.post('/api/ai/config/test', {
      confirm_external_call: true,
      role
    }, config)
  }
}
