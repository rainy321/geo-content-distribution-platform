const configuredBaseUrl = (
  import.meta.env.VITE_API_BASE_URL
  || import.meta.env.NEXT_PUBLIC_BACKEND_URL
  || '/backend'
)

export const API_BASE_URL = configuredBaseUrl.replace(/\/$/, '')

export const apiUrl = (path) => {
  const normalizedPath = path.startsWith('/') ? path : `/${path}`
  return `${API_BASE_URL}${normalizedPath}`
}
