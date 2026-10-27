import { http } from '@/utils/request'

export const geoExternalFeedbackApi = {
  create: (cycleId, data) => http.post(`/api/geo-content/cycles/${cycleId}/external-observations`, data, {timeout: 20000, suppressGlobalError: true}),
  context: (cycleId, probeId, data) => http.post(`/api/geo-content/cycles/${cycleId}/external-observations/${probeId}/context`, data, {timeout: 20000, suppressGlobalError: true})
}
