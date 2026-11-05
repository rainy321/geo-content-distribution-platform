import { http } from '@/utils/request'

const endpoint = (id, assetId) => `/api/geo-content/cycles/${encodeURIComponent(id)}/indexnow${assetId ? `/assets/${encodeURIComponent(assetId)}` : ''}`
export const geoIndexNowApi = {
  get: (id, assetId) => http.get(endpoint(id, assetId), undefined, { suppressGlobalError: true }),
  preview: (id, data, assetId) => http.post(endpoint(id, assetId), data, { timeout: 65000, suppressGlobalError: true }),
  submit: (id, data, assetId) => http.post(endpoint(id, assetId), data, { timeout: 65000, suppressGlobalError: true })
}
