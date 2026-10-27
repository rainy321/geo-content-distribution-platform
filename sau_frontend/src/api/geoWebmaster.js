import { http } from '@/utils/request'

export const geoWebmasterApi = {
  list: cycleId => http.get(`/api/geo-content/cycles/${cycleId}/webmaster`),
  update: (cycleId, data) => http.post(`/api/geo-content/cycles/${cycleId}/webmaster`, data)
}
