import { http } from '@/utils/request'
import { withCustomAIConfig } from '@/utils/aiConfig'
import { apiUrl } from '@/config/api'

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

  generateArticle(data, config = {}) {
    return http.post('/api/articles/generate', withCustomAIConfig(data), config)
  },

  generateBatch(data, config = {}) {
    return http.post('/api/articles/generate-batch', withCustomAIConfig(data), config)
  },

  getTemplates() {
    return http.get('/api/content-templates')
  },

  createTemplate(data) {
    return http.post('/api/content-templates', data)
  },

  updateTemplate(id, data) {
    return http.put(`/api/content-templates/${id}`, data)
  },

  deleteTemplate(id) {
    return http.delete(`/api/content-templates/${id}`)
  },

  importArticles(formData, onUploadProgress) {
    return http.upload('/api/articles/import', formData, onUploadProgress)
  },

  getImportTemplateUrl() {
    return apiUrl('/api/articles/import-template.xlsx')
  },

  recommendImages(id) {
    return http.get(`/api/articles/${id}/images/recommend`)
  },

  generateCover(id) {
    return http.post(`/api/articles/${id}/images/generate`)
  },

  getAIConfigStatus() {
    return http.get('/api/ai/config-status')
  },

  getContentEngineStatus(config = {}) {
    return http.get('/api/content-engine/status', undefined, config)
  },

  getContentGenerationRun(runId, config = {}) {
    return http.get(`/api/content-generation/runs/${runId}`, undefined, config)
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
