import { http } from '@/utils/request'

export const publishApi = {
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

  executeRealJob(id) {
    return http.post(`/api/publish/jobs/${id}/execute-real`, { confirm: true })
  }
}
