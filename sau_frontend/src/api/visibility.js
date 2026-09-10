import { http } from '@/utils/request'

export const visibilityApi = {
  analyze(data, config = {}) {
    return http.post('/api/geo/visibility/analyze', data, config)
  },

  getRuns(projectId, limit = 10, config = {}) {
    return http.get('/api/geo/visibility/runs', {
      project_id: projectId,
      limit
    }, config)
  },

  getRun(runId, config = {}) {
    return http.get(`/api/geo/visibility/runs/${encodeURIComponent(runId)}`, undefined, config)
  },

  getBlackboxTruthMatrix(config = {}) {
    return http.get('/api/geo/blackbox/truth-matrix', undefined, config)
  },

  evaluateBlackboxPanel(snapshots, config = {}) {
    return http.post('/api/geo/blackbox/imported-panel', { snapshots }, config)
  }
}
