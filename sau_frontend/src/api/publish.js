import { http } from '@/utils/request'

export const publishApi = {
  getPlatforms() {
    return http.get('/api/publish/platforms')
  },

  createJob(data) {
    return http.post('/api/publish', data)
  },

  getJobs(params = {}) {
    return http.get('/api/publish/jobs', params)
  },

  getJob(id) {
    return http.get(`/api/publish/jobs/${id}`)
  },

  checkResult(id) {
    return http.post(`/api/publish/jobs/${id}/check-result`, undefined, { suppressGlobalError: true })
  },

  retryJob(id) {
    return http.post(`/api/publish/jobs/${id}/retry`)
  },

  executeDemoJob(id) {
    return http.post(`/api/publish/jobs/${id}/execute`)
  },

  executeRealJob(id, confirmationSnapshot) {
    return http.post(`/api/publish/jobs/${id}/execute-real`, { confirm: true, expected_confirmation_snapshot: confirmationSnapshot })
  }
}
