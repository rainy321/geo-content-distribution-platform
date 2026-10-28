import {http} from '@/utils/request'
export const geoSearchOnboardingApi={
  get:cycleId=>http.get(`/api/geo-content/cycles/${cycleId}/search-onboarding`),
  save:(cycleId,data)=>http.post(`/api/geo-content/cycles/${cycleId}/search-onboarding`,data)
}
