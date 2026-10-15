import { http } from '@/utils/request'
import { withCustomAIConfig } from '@/utils/aiConfig'

export const geoTestApi = {
  listReports() {
    return http.get('/api/geo-tests/reports')
  },
  getReport(id) {
    return http.get(`/api/geo-tests/reports/${encodeURIComponent(id)}`)
  },
  generatePlan(data) {
    return http.post('/api/geo-tests/plan', withCustomAIConfig(data), { timeout: 150000 })
  }
}
