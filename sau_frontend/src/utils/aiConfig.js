const objectValue = value => (value && typeof value === 'object' ? value : {})
const CHAT_COMPLETIONS_SUFFIX = '/chat/completions'
const IMAGE_GENERATIONS_SUFFIX = '/images/generations'
const VIDEO_GENERATION_ENDPOINT_SUFFIXES = [
  '/contents/generations/tasks',
  '/video/generations',
  '/videos/generations'
]

export const normalizeUnifiedAIBaseUrl = value => {
  const raw = String(value || '').trim()
  const parsed = new URL(raw)
  const host = parsed.hostname.toLowerCase().replace(/\.$/, '')
  const isInternalHost = (
    host === 'localhost'
    || host.endsWith('.localhost')
    || host.endsWith('.local')
    || host.endsWith('.internal')
    || !host.includes('.')
  )
  if (
    parsed.protocol !== 'https:'
    || parsed.username
    || parsed.password
    || parsed.search
    || parsed.hash
    || isInternalHost
  ) {
    throw new Error('请输入不含账号密码的公开 HTTPS 地址')
  }

  let path = parsed.pathname.replace(/\/{2,}/g, '/').replace(/\/+$/, '')
  let foldedPath = path.toLowerCase()
  const repeatedSuffix = CHAT_COMPLETIONS_SUFFIX + CHAT_COMPLETIONS_SUFFIX
  while (foldedPath.endsWith(repeatedSuffix)) {
    path = path.slice(0, -CHAT_COMPLETIONS_SUFFIX.length)
    foldedPath = path.toLowerCase()
  }
  if (foldedPath.includes(CHAT_COMPLETIONS_SUFFIX) && !foldedPath.endsWith(CHAT_COMPLETIONS_SUFFIX)) {
    throw new Error('Base URL 中的 /chat/completions 位置无效')
  }
  if (foldedPath.endsWith(CHAT_COMPLETIONS_SUFFIX)) {
    path = path.slice(0, -CHAT_COMPLETIONS_SUFFIX.length) + CHAT_COMPLETIONS_SUFFIX
  }
  return `${parsed.origin}${path}`
}

export const resolveUnifiedAIChatEndpoint = value => {
  const baseUrl = normalizeUnifiedAIBaseUrl(value)
  const parsed = new URL(baseUrl)
  const path = parsed.pathname.replace(/\/+$/, '')
  if (path.toLowerCase().endsWith(CHAT_COMPLETIONS_SUFFIX)) return baseUrl
  if (path) return `${baseUrl}${CHAT_COMPLETIONS_SUFFIX}`
  if (knownProviderForHost(parsed.hostname) === 'deepseek') {
    return `${parsed.origin}${CHAT_COMPLETIONS_SUFFIX}`
  }
  return `${parsed.origin}/v1${CHAT_COMPLETIONS_SUFFIX}`
}

export const resolveUnifiedAIImageEndpoint = value => {
  const baseUrl = normalizeUnifiedAIBaseUrl(value)
  const parsed = new URL(baseUrl)
  const path = parsed.pathname.replace(/\/+$/, '')
  const foldedPath = path.toLowerCase()
  if (foldedPath.endsWith(IMAGE_GENERATIONS_SUFFIX)) return baseUrl
  if (foldedPath.endsWith(CHAT_COMPLETIONS_SUFFIX)) {
    return `${baseUrl.slice(0, -CHAT_COMPLETIONS_SUFFIX.length)}${IMAGE_GENERATIONS_SUFFIX}`
  }
  if (path) return `${baseUrl}${IMAGE_GENERATIONS_SUFFIX}`
  if (knownProviderForHost(parsed.hostname) === 'deepseek') {
    return `${parsed.origin}${IMAGE_GENERATIONS_SUFFIX}`
  }
  return `${parsed.origin}/v1${IMAGE_GENERATIONS_SUFFIX}`
}

export const unifiedAIImageCompatibilityIssue = (baseUrl, model) => {
  const normalizedModel = String(model || '').trim()
  if (normalizedModel.toLowerCase().includes('seedance')) {
    return {
      code: 'video_model_not_supported',
      message: `IMAGE 角色不支持视频生成模型 "${normalizedModel}"：Seedance 是视频模型，请改用 Seedream、GPT Image / DALL-E 或供应商明确支持 /images/generations 的图片模型。`
    }
  }
  const path = new URL(normalizeUnifiedAIBaseUrl(baseUrl)).pathname
    .replace(/\/+$/, '')
    .toLowerCase()
  if (VIDEO_GENERATION_ENDPOINT_SUFFIXES.some(suffix => path.endsWith(suffix))) {
    return {
      code: 'video_endpoint_not_supported',
      message: 'IMAGE 角色只调用 OpenAI-compatible Images API；当前 Base URL 是视频生成任务地址。请改填图片 API 根路径或完整 /images/generations 地址。'
    }
  }
  return null
}

export const validateUnifiedAIImageCompatibility = (baseUrl, model) => {
  const issue = unifiedAIImageCompatibilityIssue(baseUrl, model)
  if (issue) throw new Error(issue.message)
  return true
}

const knownProviderForHost = hostname => {
  const host = String(hostname || '').toLowerCase().replace(/\.$/, '')
  if (host.endsWith('.aliyuncs.com')) return 'qwen'
  if (host === 'api.deepseek.com') return 'deepseek'
  if (host.endsWith('.volces.com')) return 'doubao'
  return ''
}

export const validateUnifiedAIProviderBinding = (provider, baseUrl) => {
  const normalizedProvider = String(provider || '').trim().toLowerCase()
  const normalizedBaseUrl = normalizeUnifiedAIBaseUrl(baseUrl)
  if (!['qwen', 'deepseek', 'doubao'].includes(normalizedProvider)) return normalizedBaseUrl
  const detectedProvider = knownProviderForHost(new URL(normalizedBaseUrl).hostname)
  if (detectedProvider && detectedProvider !== normalizedProvider) {
    throw new Error('当前服务商与 Base URL 不匹配，请重新选择服务商')
  }
  if (
    normalizedProvider === 'doubao'
    && detectedProvider === 'doubao'
    && !new URL(resolveUnifiedAIChatEndpoint(normalizedBaseUrl)).pathname
      .toLowerCase()
      .endsWith('/api/v3/chat/completions')
  ) {
    throw new Error('火山方舟 Base URL 应以 /api/v3 结尾')
  }
  if (
    normalizedProvider === 'qwen'
    && detectedProvider === 'qwen'
    && !new URL(resolveUnifiedAIChatEndpoint(normalizedBaseUrl)).pathname
      .toLowerCase()
      .endsWith('/compatible-mode/v1/chat/completions')
  ) {
    throw new Error('千问 OpenAI 兼容 Base URL 应以 /compatible-mode/v1 结尾')
  }
  return normalizedBaseUrl
}

export const unifiedAIProviderConfigurationHint = provider => ({
  qwen: '模型名称示例：qwen-plus；Base URL 通常以 /compatible-mode/v1 结尾。',
  deepseek: '填写当前控制台可用的模型 ID；官方根地址会解析到 /chat/completions。',
  doubao: '模型名称请填写火山方舟控制台的模型或接入点 ID；Base URL 应以 /api/v3 结尾。',
  custom: '填写 OpenAI-compatible API 根路径；也可粘贴完整 /chat/completions 地址。'
}[String(provider || '').trim().toLowerCase()] || '请确认 Base URL 与模型 ID 属于同一服务。')

const endpointIdentity = value => {
  try {
    return resolveUnifiedAIChatEndpoint(value)
  } catch {
    return String(value || '').trim().replace(/\/+$/, '')
  }
}

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
  Object.freeze({ id: 'evaluator', label: '评估 / 回测模型', code: 'EVAL', description: '独立文章评估与 API 可见度采样' }),
  Object.freeze({ id: 'image', label: '生图模型', code: 'IMAGE', description: '生成文章封面并记录实际调用成本' })
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
      sameAsWriter: Boolean(item.same_as_writer),
      sameAsReviewer: Boolean(item.same_as_reviewer)
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
    base_url: validateUnifiedAIProviderBinding(draft?.provider || 'custom', draft?.baseUrl),
    model: String(draft?.model || '').trim()
  }
  const apiKey = String(draft?.apiKey || '').trim()
  if (apiKey) payload.api_key = apiKey
  const sameSavedConnection = (
    payload.provider === String(status?.provider || '').trim().toLowerCase()
    && endpointIdentity(payload.base_url) === endpointIdentity(status?.baseUrl)
  )
  if (!apiKey && !(status?.savedConfigured && status?.apiKeyConfigured && sameSavedConnection)) {
    throw new Error('首次保存或切换服务地址时需要填写 API Key')
  }
  return payload
}

export const buildUnifiedAIRoleConfigPayload = (
  draft,
  roleStatus = {},
  writerStatus = {},
  role = roleStatus?.id || ''
) => {
  const payload = {
    provider: String(draft?.provider || 'custom').trim().toLowerCase(),
    base_url: validateUnifiedAIProviderBinding(draft?.provider || 'custom', draft?.baseUrl),
    model: String(draft?.model || '').trim(),
    reuse_writer_credential: Boolean(draft?.reuseWriterCredential)
  }
  if (String(role || '').trim().toLowerCase() === 'image') {
    validateUnifiedAIImageCompatibility(payload.base_url, payload.model)
  }
  const apiKey = String(draft?.apiKey || '').trim()
  if (apiKey) payload.api_key = apiKey
  const sameSavedRoleConnection = (
    payload.provider === String(roleStatus?.provider || '').trim().toLowerCase()
    && endpointIdentity(payload.base_url) === endpointIdentity(roleStatus?.baseUrl)
  )
  const canReuseSavedRole = Boolean(
    roleStatus?.configured
    && roleStatus?.apiKeyConfigured
    && sameSavedRoleConnection
  )
  const sameWriterConnection = (
    payload.provider === String(writerStatus?.provider || '').trim().toLowerCase()
    && endpointIdentity(payload.base_url) === endpointIdentity(writerStatus?.baseUrl)
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
