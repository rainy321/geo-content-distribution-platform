import { http } from '@/utils/request'

export const dashboardApi = {
  getOverview() {
    return http.get('/api/dashboard')
  }
}
