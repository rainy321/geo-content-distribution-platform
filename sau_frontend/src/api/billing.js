import { http } from '@/utils/request'

export const billingApi = {
  getBill(params = {}, config = {}) {
    return http.get('/api/ai-usage/billing', params, config)
  },
  getPrices(config = {}) {
    return http.get('/api/ai-usage/prices', {}, config)
  },
  savePrice(data, config = {}) {
    return http.put('/api/ai-usage/prices', data, config)
  },
  deletePrice(priceId, config = {}) {
    return http.delete(`/api/ai-usage/prices/${priceId}`, {}, config)
  }
}
