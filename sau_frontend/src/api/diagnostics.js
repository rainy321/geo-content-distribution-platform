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

  runSiteAudit(data, { requestId, idempotencyKey, ...config } = {}) {
    return http.post('/api/geo/site-audits', data, {
      ...config,
      headers: {
        ...(config.headers || {}),
        'X-Request-ID': requestId,
        'Idempotency-Key': idempotencyKey
      }
    })
  },

  getSiteAuditRuns(projectId, limit = 5, config = {}) {
    return http.get('/api/geo/site-audits/runs', {
      project_id: projectId,
      limit
    }, config)
  },

  getSiteAuditRun(runId, config = {}) {
    return http.get(
      `/api/geo/site-audits/runs/${encodeURIComponent(runId)}`,
      undefined,
      config
    )
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

  sampleVisibilityPanel(data, { requestId, idempotencyKey, ...config } = {}) {
    return http.post('/api/geo/visibility/sample-panel', data, {
      ...config,
      headers: {
        ...(config.headers || {}),
        'X-Request-ID': requestId,
        'Idempotency-Key': idempotencyKey
      }
    })
  },

  getVisibilityPanel(requestId, config = {}) {
    return http.get(
      `/api/geo/visibility/sample-panel/${encodeURIComponent(requestId)}`,
      undefined,
      config
    )
  },

  getVisibilitySample(requestId, config = {}) {
    return http.get(
      `/api/geo/visibility/sample/${encodeURIComponent(requestId)}`,
      undefined,
      config
    )
  }
}
