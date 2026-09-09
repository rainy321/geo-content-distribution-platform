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

  retryJob(id) {
    return http.post(`/api/publish/jobs/${id}/retry`)
  },

  executeDemoJob(id) {
    return http.post(`/api/publish/jobs/${id}/execute`)
  },

  executeRealJob(job) {
    return http.post(`/api/publish/jobs/${job.job_id}/execute-real`, {
      confirm: true,
      job_id: job.job_id,
      state_version: job.state_version,
      account_id: job.account_id,
      confirmation_fingerprint: job.confirmation_fingerprint
    })
  }
}
