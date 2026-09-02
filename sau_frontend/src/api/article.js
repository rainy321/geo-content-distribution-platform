import { http } from '@/utils/request'
import { withCustomAIConfig } from '@/utils/aiConfig'

export const articleApi = {
  getArticles(params = {}) {
    return http.get('/api/articles', params)
  },

  getArticle(id) {
    return http.get(`/api/articles/${id}`)
  },

  optimizeArticle(id) {
    return http.post(`/api/articles/${id}/optimize`, withCustomAIConfig())
  },

  generateArticle(data) {
    return http.post('/api/articles/generate', withCustomAIConfig(data))
  },

  getAIConfigStatus() {
    return http.get('/api/ai/config-status')
  },

  scoreArticle(data) {
    return http.post('/api/geo/score', data)
  },

  createArticle(data) {
    return http.post('/api/articles', data)
  },

  updateArticle(id, data) {
    return http.put(`/api/articles/${id}`, data)
  }
}
