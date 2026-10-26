import { http } from '@/utils/request'

export const geoSourceMaintenanceApi = {
  list: cycleId => http.get(`/api/geo-content/cycles/${cycleId}/source-maintenance`),
  save: (cycleId,data) => http.post(`/api/geo-content/cycles/${cycleId}/source-maintenance`,data)
}
