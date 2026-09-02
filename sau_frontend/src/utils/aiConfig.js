const PUBLIC_CONFIG_KEY = 'geo.ai.public-config.v1'
const SESSION_SECRET_KEY = 'geo.ai.session-key.v1'

const browserStorageAvailable = () => (
  typeof window !== 'undefined'
  && window.localStorage
  && window.sessionStorage
)

const readPublicConfig = () => {
  if (!browserStorageAvailable()) return { base_url: '', model: '' }
  try {
    const value = JSON.parse(window.localStorage.getItem(PUBLIC_CONFIG_KEY) || '{}')
    return {
      base_url: String(value.base_url || '').trim(),
      model: String(value.model || '').trim()
    }
  } catch {
    return { base_url: '', model: '' }
  }
}

export const getAIConfigDraft = () => {
  const publicConfig = readPublicConfig()
  const apiKey = browserStorageAvailable()
    ? String(window.sessionStorage.getItem(SESSION_SECRET_KEY) || '')
    : ''
  return {
    baseUrl: publicConfig.base_url,
    apiKey,
    model: publicConfig.model
  }
}

export const getCustomAIConfig = () => {
  const draft = getAIConfigDraft()
  if (!draft.baseUrl || !draft.apiKey || !draft.model) return null
  return {
    base_url: draft.baseUrl,
    api_key: draft.apiKey,
    model: draft.model
  }
}

export const saveCustomAIConfig = ({ baseUrl, apiKey, model }) => {
  if (!browserStorageAvailable()) throw new Error('当前浏览器不支持会话存储')
  const publicConfig = {
    base_url: String(baseUrl || '').trim().replace(/\/$/, ''),
    model: String(model || '').trim()
  }
  window.localStorage.setItem(PUBLIC_CONFIG_KEY, JSON.stringify(publicConfig))
  window.sessionStorage.setItem(SESSION_SECRET_KEY, String(apiKey || '').trim())
  window.dispatchEvent(new CustomEvent('geo-ai-config-changed'))
}

export const clearCustomAIConfig = () => {
  if (!browserStorageAvailable()) return
  window.localStorage.removeItem(PUBLIC_CONFIG_KEY)
  window.sessionStorage.removeItem(SESSION_SECRET_KEY)
  window.dispatchEvent(new CustomEvent('geo-ai-config-changed'))
}

export const withCustomAIConfig = (payload = {}) => {
  const customConfig = getCustomAIConfig()
  return customConfig
    ? { ...payload, ai_config: customConfig }
    : { ...payload }
}
