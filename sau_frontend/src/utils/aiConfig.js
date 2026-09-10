const objectValue = value => (value && typeof value === 'object' ? value : {})

export const UNIFIED_AI_PROVIDER_PRESETS = Object.freeze([
  Object.freeze({
    id: 'qwen',
    label: '千问 / Qwen',
    baseUrl: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
    model: 'qwen-plus'
  }),
  Object.freeze({
    id: 'deepseek',
    label: 'DeepSeek',
    baseUrl: 'https://api.deepseek.com',
    model: 'deepseek-chat'
  }),
  Object.freeze({
    id: 'doubao',
    label: '豆包 / 火山方舟',
    baseUrl: 'https://ark.cn-beijing.volces.com/api/v3',
    model: ''
  }),
  Object.freeze({
    id: 'custom',
    label: '自定义兼容服务',
    baseUrl: '',
    model: ''
  })
])

export const unifiedAIProviderPreset = id => (
  UNIFIED_AI_PROVIDER_PRESETS.find(item => item.id === id)
  || UNIFIED_AI_PROVIDER_PRESETS.at(-1)
)

export const normalizeUnifiedAIStatus = value => {
  const root = objectValue(value)
  const source = objectValue(root.data || root)
  const provider = String(source.provider || '').trim().toLowerCase()
  return {
    configured: Boolean(source.configured),
    savedConfigured: Boolean(source.saved_configured),
    saveSupported: source.save_supported !== false,
    serverConfigured: Boolean(source.server_configured),
    source: String(source.source || (source.configured ? 'saved' : 'none')),
    provider,
    providerLabel: provider ? unifiedAIProviderPreset(provider).label : '',
    baseUrl: String(source.base_url || '').trim(),
    model: String(source.model || '').trim(),
    apiKeyConfigured: Boolean(source.api_key_configured),
    credentialStorage: String(source.credential_storage || source.custom_key_storage || ''),
    updatedAt: String(source.updated_at || '')
  }
}

export const buildUnifiedAIConfigPayload = (draft, status = {}) => {
  const payload = {
    provider: String(draft?.provider || 'custom').trim().toLowerCase(),
    base_url: String(draft?.baseUrl || '').trim().replace(/\/+$/, ''),
    model: String(draft?.model || '').trim()
  }
  const apiKey = String(draft?.apiKey || '').trim()
  if (apiKey) payload.api_key = apiKey
  if (!apiKey && !(status?.savedConfigured && status?.apiKeyConfigured)) {
    throw new Error('首次保存需要填写 API Key')
  }
  return payload
}

export const applyUnifiedAIProviderPreset = (draft, providerId) => {
  const preset = unifiedAIProviderPreset(providerId)
  const providerChanged = String(draft?.provider || '') !== preset.id
  return {
    provider: preset.id,
    baseUrl: preset.baseUrl,
    model: preset.model,
    apiKey: providerChanged ? '' : String(draft?.apiKey || '')
  }
}

export const unifiedAIStatusLabel = status => {
  if (!status?.configured) return '尚未配置'
  if (status.source === 'saved') return '已安全保存'
  if (status.source === 'environment') return '由运行环境提供'
  return '已配置'
}

export const unifiedAICredentialLabel = status => {
  if (status?.savedConfigured && status?.apiKeyConfigured) return '•••••••• · 已受保护'
  if (status?.serverConfigured) return '由运行环境保护'
  return '未保存凭据'
}
