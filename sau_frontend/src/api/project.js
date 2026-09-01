import { http } from '@/utils/request'

export const projectApi = {
  getProjects() {
    return http.get('/api/projects')
  },

  createProject(data) {
    return http.post('/api/projects', data)
  },

  updateProject(id, data) {
    return http.put(`/api/projects/${id}`, data)
  },

  deleteProject(id) {
    return http.delete(`/api/projects/${id}`)
  }
}
