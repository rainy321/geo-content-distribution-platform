export const WEIBO_ACCOUNT_TYPE = 11
export const WEIBO_PLATFORM_KEY = 'weibo'
export const WEIBO_PLATFORM_NAME = '微博'

export const WEIBO_CONNECTOR_UNAVAILABLE_REASON = (
  '当前构建未安装微博官方连接器；安装后仍需通过微博 OAuth 授权账号，软件不会读取账号密码或绕过授权。'
)

export const WEIBO_FRONTEND_CAPABILITY = Object.freeze({
  key: WEIBO_PLATFORM_KEY,
  name: WEIBO_PLATFORM_NAME,
  account_type: WEIBO_ACCOUNT_TYPE,
  type: WEIBO_ACCOUNT_TYPE,
  priority: 'P0',
  content_mode: 'short_post',
  requires_images: false,
  requires_video: false,
  supports_schedule: false,
  release_status: 'connector_required',
  connector_available: false,
  oauth_required: true,
  login_available: false,
  runtime_available: false,
  task_creation_available: false,
  real_publishing_authorized: false,
  demo_available: false,
  unavailable_reason: WEIBO_CONNECTOR_UNAVAILABLE_REASON
})

const isWeiboPlatform = platform => (
  platform?.key === WEIBO_PLATFORM_KEY
  || Number(platform?.account_type ?? platform?.type) === WEIBO_ACCOUNT_TYPE
)

const mergeBackendWeiboCapability = backendPlatform => ({
  ...WEIBO_FRONTEND_CAPABILITY,
  ...backendPlatform,
  key: WEIBO_PLATFORM_KEY,
  name: backendPlatform?.name || WEIBO_PLATFORM_NAME,
  account_type: WEIBO_ACCOUNT_TYPE,
  type: WEIBO_ACCOUNT_TYPE,
  // Runtime availability belongs to the backend probe. Release maturity remains
  // fail-closed until the frontend explicitly understands the backend status.
  release_status: backendPlatform?.release_status === 'official_connector_required'
    ? 'official_connector_required'
    : WEIBO_FRONTEND_CAPABILITY.release_status
})

const insertAfterPriority = (items, item, priority) => {
  let lastPriorityIndex = -1
  for (let index = 0; index < items.length; index += 1) {
    if (items[index]?.priority === priority) lastPriorityIndex = index
  }
  if (lastPriorityIndex < 0) return [...items, item]
  return [
    ...items.slice(0, lastPriorityIndex + 1),
    item,
    ...items.slice(lastPriorityIndex + 1)
  ]
}

export const withWeiboCapability = (platforms = []) => {
  const items = Array.isArray(platforms) ? [...platforms] : []
  const existingIndex = items.findIndex(isWeiboPlatform)
  if (existingIndex < 0) {
    return insertAfterPriority(items, { ...WEIBO_FRONTEND_CAPABILITY }, 'P0')
  }

  items[existingIndex] = mergeBackendWeiboCapability(items[existingIndex])
  return items
}

export const withWeiboAccountPlatform = (platforms = [], accounts = []) => {
  const accountItems = Array.isArray(accounts) ? accounts : []
  const weiboAccounts = accountItems.filter(account => (
    Number(account?.type) === WEIBO_ACCOUNT_TYPE
    || account?.platform_key === WEIBO_PLATFORM_KEY
  ))
  const items = Array.isArray(platforms) ? [...platforms] : []
  const existingIndex = items.findIndex(isWeiboPlatform)
  const accountSummary = {
    account_count: weiboAccounts.length,
    connected_count: weiboAccounts.filter(account => account?.status === 'connected').length,
    accounts: weiboAccounts
  }

  if (existingIndex < 0) {
    return insertAfterPriority(items, {
      ...WEIBO_FRONTEND_CAPABILITY,
      ...accountSummary
    }, 'P0')
  }

  items[existingIndex] = {
    ...mergeBackendWeiboCapability(items[existingIndex]),
    ...accountSummary,
  }
  return items
}
