import { http } from '@/utils/request'

export const aiConfigApi = {
  get(config = {}) {
    return http.get('/api/ai/config', undefined, config)
  },

  save(data, config = {}) {
    return http.put('/api/ai/config', data, config)
  },

  clear(config = {}) {
    return http.delete('/api/ai/config', undefined, config)
  },

  test(config = {}) {
    return http.post('/api/ai/config/test', {
      confirm_external_call: true
    }, config)
  }
}
