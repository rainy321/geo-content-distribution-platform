import axios from 'axios'
import { ElMessage } from 'element-plus'
import { API_BASE_URL } from '@/config/api'

// 创建axios实例
const request = axios.create({
  baseURL: API_BASE_URL,
  withCredentials: true,
  headers: {
    'Content-Type': 'application/json'
  }
})

// 请求拦截器
request.interceptors.request.use(
  (config) => {
    // 可以在这里添加token等认证信息
    const token = localStorage.getItem('token')
    if (token) {
      config.headers.Authorization = `Bearer ${token}`
    }
    return config
  },
  (error) => {
    console.error('请求错误:', error)
    return Promise.reject(error)
  }
)

// 响应拦截器
request.interceptors.response.use(
  (response) => {
    const { data } = response
    
    // 根据后端接口规范处理响应
    const hasSuccessfulCode = data.code >= 200 && data.code < 300
    const isRawSuccess = data.code == null && response.status >= 200 && response.status < 300
    if (hasSuccessfulCode || isRawSuccess || data.success) {
      return data
    } else {
      ElMessage.error(data.msg || data.message || '请求失败')
      return Promise.reject(new Error(data.msg || data.message || '请求失败'))
    }
  },
  (error) => {
    if (axios.isCancel(error) || error.code === 'ERR_CANCELED') {
      return Promise.reject(error)
    }
    if (error.config?.suppressGlobalError) {
      return Promise.reject(error)
    }
    if (error.response?.status >= 500) {
      console.error('响应错误:', error)
    } else {
      console.warn('请求未完成:', error.response?.status || error.message)
    }
    
    // 处理HTTP错误状态码
    if (error.response) {
      const { status, data } = error.response
      const backendMsg = (data && (data.msg || data.message)) || ''
      switch (status) {
        case 400:
          ElMessage.error(backendMsg || '请求参数错误')
          break
        case 401:
          ElMessage.error(backendMsg || '未授权，请重新登录')
          window.dispatchEvent(new CustomEvent('geo-auth-required'))
          break
        case 403:
          ElMessage.error(backendMsg || '拒绝访问')
          break
        case 404:
          ElMessage.error(backendMsg || '请求地址不存在')
          break
        case 500:
          ElMessage.error(backendMsg || '服务器内部错误')
          break
        case 429:
          ElMessage.error(backendMsg || '请求过于频繁，请稍后再试')
          break
        default:
          ElMessage.error(backendMsg || '网络错误')
      }
    } else {
      ElMessage.error('网络连接失败')
    }
    
    return Promise.reject(error)
  }
)

// 封装常用的请求方法
export const http = {
  get(url, params, config = {}) {
    return request.get(url, { ...config, params })
  },
  
  post(url, data, config = {}) {
    return request.post(url, data, config)
  },
  
  put(url, data, config = {}) {
    return request.put(url, data, config)
  },
  
  delete(url, params) {
    return request.delete(url, { params })
  },
  
  upload(url, formData, onUploadProgress) {
    return request.post(url, formData, {
      headers: {
        'Content-Type': 'multipart/form-data'
      },
      onUploadProgress
    })
  }
}

export default request
