import { http } from '@/utils/request'

export const geoIndexNowApi = {
  get: id => http.get(`/api/geo-content/cycles/${encodeURIComponent(id)}/indexnow`, undefined, { suppressGlobalError: true }),
  preview: (id, data) => http.post(`/api/geo-content/cycles/${encodeURIComponent(id)}/indexnow`, data, { timeout: 65000, suppressGlobalError: true }),
  submit: (id, data) => http.post(`/api/geo-content/cycles/${encodeURIComponent(id)}/indexnow`, data, { timeout: 65000, suppressGlobalError: true })
}
