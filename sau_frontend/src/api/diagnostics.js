import { http } from '@/utils/request'

export const diagnosticsApi = {
  diagnoseProject(projectId, { persist = true } = {}, config = {}) {
    return http.post('/api/geo/diagnostics', {
      project_id: projectId,
      persist
    }, config)
  },

  getRuns(projectId, limit = 10, config = {}) {
    return http.get('/api/geo/diagnostics/runs', {
      project_id: projectId,
      limit
    }, config)
  },

  getRun(runId, config = {}) {
    return http.get(`/api/geo/diagnostics/runs/${encodeURIComponent(runId)}`, undefined, config)
  },

  getProjectCostSummary(projectId, limit = 30, config = {}) {
    return http.get(`/api/ai-usage/projects/${encodeURIComponent(projectId)}/summary`, {
      limit
    }, config)
  },

  sampleVisibility(data, { requestId, idempotencyKey, ...config } = {}) {
    return http.post('/api/geo/visibility/sample', data, {
      ...config,
      headers: {
        ...(config.headers || {}),
        'X-Request-ID': requestId,
        'Idempotency-Key': idempotencyKey
      }
    })
  },

  getVisibilitySample(requestId, config = {}) {
    return http.get(
      `/api/geo/visibility/sample/${encodeURIComponent(requestId)}`,
      undefined,
      config
    )
  }
}
