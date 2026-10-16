import { http } from '@/utils/request'
import { withCustomAIConfig } from '@/utils/aiConfig'

export const geoContentApi = {
  list: () => http.get('/api/geo-content/cycles'),
  create: data => http.post('/api/geo-content/cycles', data),
  get: id => http.get(`/api/geo-content/cycles/${id}`),
  facts: (id, data) => http.put(`/api/geo-content/cycles/${id}`, data),
  generate: (id, data) => http.post(`/api/geo-content/cycles/${id}/generate`, withCustomAIConfig(data), { timeout: 150000 }),
  retest: (id, reportId) => http.post(`/api/geo-content/cycles/${id}/retests`, { report_id: reportId }),
  article: id => http.get(`/api/geo-content/articles/${id}`),
  saveDestination: (id, targetId, data) => targetId
    ? http.put(`/api/geo-content/cycles/${id}/destinations/${targetId}`, data)
    : http.post(`/api/geo-content/cycles/${id}/destinations`, data),
  destination: (id, targetId) => http.get(`/api/geo-content/cycles/${id}/destinations/${targetId}`),
  reviewDestination: (id, targetId) => http.post(`/api/geo-content/cycles/${id}/destinations/${targetId}/review`, {confirmed:true})
}
