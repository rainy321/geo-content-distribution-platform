import { http } from '@/utils/request'

export const billingApi = {
  getBill(params = {}, config = {}) {
    return http.get('/api/ai-usage/billing', params, config)
  }
}
