import { http } from '@/utils/request'

export const automationApi = {
  createRun(data, { requestId, idempotencyKey } = {}) {
    return http.post('/api/geo/automation-runs', data, {
      headers: {
        ...(requestId ? { 'X-Request-ID': requestId } : {}),
        ...(idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : {})
      }
    })
  },

  listRuns(params = {}) {
    return http.get('/api/geo/automation-runs', params)
  },

  getRun(id) {
    return http.get(`/api/geo/automation-runs/${id}`)
  },

  checkpoint(runId, itemId, data) {
    return http.post(
      `/api/geo/automation-runs/${runId}/items/${itemId}/checkpoint`,
      data,
      { suppressGlobalError: true }
    )
  },

  acquireLease(runId, itemId, data) {
    return http.post(
      `/api/geo/automation-runs/${runId}/items/${itemId}/lease`,
      data,
      { suppressGlobalError: true }
    )
  },

  renewLease(runId, itemId, data) {
    return http.post(
      `/api/geo/automation-runs/${runId}/items/${itemId}/lease/renew`,
      data,
      { suppressGlobalError: true }
    )
  },

  releaseLease(runId, itemId, data) {
    return http.post(
      `/api/geo/automation-runs/${runId}/items/${itemId}/lease/release`,
      data,
      { suppressGlobalError: true }
    )
  },

  setPaused(run, paused) {
    return http.post(`/api/geo/automation-runs/${run.id}/pause`, {
      paused,
      expected_state_version: run.state_version
    })
  }
}
