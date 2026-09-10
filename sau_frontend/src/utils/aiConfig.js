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

export const AI_ROLE_DEFINITIONS = Object.freeze([
  Object.freeze({ id: 'writer', label: '写稿模型', code: 'WRITE', description: '生成文章与批量草稿' }),
  Object.freeze({ id: 'reviewer', label: '调优模型', code: 'REVIEW', description: '复核结构、证据和表达' }),
  Object.freeze({ id: 'evaluator', label: '回测模型', code: 'EVAL', description: 'AI 可见度真实采样' }),
  Object.freeze({ id: 'image', label: '生图模型', code: 'IMAGE', description: '为后续外部生图预留' })
])

export const unifiedAIProviderPreset = id => (
  UNIFIED_AI_PROVIDER_PRESETS.find(item => item.id === id)
  || UNIFIED_AI_PROVIDER_PRESETS.at(-1)
)

export const normalizeUnifiedAIStatus = value => {
  const root = objectValue(value)
  const source = objectValue(root.data || root)
  const provider = String(source.provider || '').trim().toLowerCase()
  const rawRoles = objectValue(source.roles)
  const roles = Object.fromEntries(AI_ROLE_DEFINITIONS.map(definition => {
    const item = objectValue(rawRoles[definition.id])
    const roleProvider = String(item.provider || (source.configured ? provider : '')).trim().toLowerCase()
    const configured = Boolean(item.configured ?? (definition.id === 'writer' && source.configured))
    const effectiveConfigured = Boolean(item.effective_configured ?? source.configured)
    return [definition.id, {
      ...definition,
      configured,
      effectiveConfigured,
      fallback: Boolean(item.fallback ?? (definition.id !== 'writer' && source.configured)),
      sourceRole: String(item.source_role || (configured ? definition.id : 'writer')),
      provider: roleProvider,
      providerLabel: roleProvider ? unifiedAIProviderPreset(roleProvider).label : '',
      baseUrl: String(item.base_url || (source.configured ? source.base_url : '')).trim(),
      model: String(item.model || (source.configured ? source.model : '')).trim(),
      apiKeyConfigured: Boolean(item.api_key_configured ?? source.api_key_configured),
      credentialStorage: String(item.credential_storage || source.credential_storage || ''),
      updatedAt: String(item.updated_at || ''),
      sameAsWriter: Boolean(item.same_as_writer)
    }]
  }))
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
    updatedAt: String(source.updated_at || ''),
    roles
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
  const sameSavedConnection = (
    payload.provider === String(status?.provider || '').trim().toLowerCase()
    && payload.base_url === String(status?.baseUrl || '').trim().replace(/\/+$/, '')
  )
  if (!apiKey && !(status?.savedConfigured && status?.apiKeyConfigured && sameSavedConnection)) {
    throw new Error('首次保存或切换服务地址时需要填写 API Key')
  }
  return payload
}

export const buildUnifiedAIRoleConfigPayload = (draft, roleStatus = {}, writerStatus = {}) => {
  const payload = {
    provider: String(draft?.provider || 'custom').trim().toLowerCase(),
    base_url: String(draft?.baseUrl || '').trim().replace(/\/+$/, ''),
    model: String(draft?.model || '').trim(),
    reuse_writer_credential: Boolean(draft?.reuseWriterCredential)
  }
  const apiKey = String(draft?.apiKey || '').trim()
  if (apiKey) payload.api_key = apiKey
  const sameSavedRoleConnection = (
    payload.provider === String(roleStatus?.provider || '').trim().toLowerCase()
    && payload.base_url === String(roleStatus?.baseUrl || '').trim().replace(/\/+$/, '')
  )
  const canReuseSavedRole = Boolean(
    roleStatus?.configured
    && roleStatus?.apiKeyConfigured
    && sameSavedRoleConnection
  )
  const sameWriterConnection = (
    payload.provider === String(writerStatus?.provider || '').trim().toLowerCase()
    && payload.base_url === String(writerStatus?.baseUrl || '').trim().replace(/\/+$/, '')
  )
  const canReuseWriter = payload.reuse_writer_credential && sameWriterConnection && writerStatus?.apiKeyConfigured
  if (!apiKey && !canReuseSavedRole && !canReuseWriter) {
    throw new Error('首次配置该角色需要 API Key；同服务地址可选择复用写稿模型密钥')
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
