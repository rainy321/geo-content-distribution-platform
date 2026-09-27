import { http } from '@/utils/request'

export const publishApi = {
  getPlatforms() {
    return http.get('/api/publish/platforms')
  },

  previewVariants(data) {
    return http.post('/api/publish/preflight', data)
  },

  createJob(data, config = {}) {
    return http.post('/api/publish', data, config)
  },

  getJobs(params = {}) {
    return http.get('/api/publish/jobs', params)
  },

  getJob(id) {
    return http.get(`/api/publish/jobs/${id}`)
  },

  getReviewMonitor(params = {}) {
    return http.get('/api/publish/review-monitor', params)
  },

  acknowledgeReviewAlert(id) {
    return http.post(`/api/publish/review-alerts/${id}/acknowledge`, {
      confirm_seen: true
    })
  },

  rearmReviewMonitor(id) {
    return http.post(`/api/publish/jobs/${id}/review-monitor/rearm`, {
      confirm_read_only: true
    })
  },

  retryJob(id) {
    return http.post(`/api/publish/jobs/${id}/retry`)
  },

  autoRepairJob(job) {
    return http.post(`/api/publish/jobs/${job.job_id}/auto-repair`, {
      confirm_pre_submit: true,
      job_id: job.job_id,
      state_version: job.state_version
    })
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
  },

  reconcileJob(job, reconciliation) {
    return http.post(`/api/publish/jobs/${job.job_id}/reconcile`, {
      confirm: true,
      platform_checked: true,
      job_id: job.job_id,
      state_version: job.state_version,
      decision: reconciliation.decision,
      execution_stopped_confirmed: Boolean(reconciliation.execution_stopped_confirmed),
      verification_note: reconciliation.verification_note,
      result_url: reconciliation.result_url || ''
    })
  }
}
