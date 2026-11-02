const digest = value => typeof value === 'string' && /^[a-f0-9]{64}$/i.test(value)
const identifier = value => typeof value === 'string' && /^[\w-]{1,100}$/.test(value)
const uuid = value => typeof value === 'string' && /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value)
export const indexNowStatus = status => ({ reserved: '正在等待同次通知回执', received: '200 · 更新通知已接收', key_pending: '202 · 密钥校验中', rejected: '明确拒绝，尚未接收', delivery_unknown: '送达结果未知，不能重发' }[status] || '通知状态未知，先核对记录')
export const indexNowError = cause => typeof cause?.response?.data?.msg === 'string' ? cause.response.data.msg : cause?.message || '未取得结果，请刷新记录核对。'
export const indexNowUncertain = cause => ![400, 401, 403, 404, 409, 422].includes(cause?.response?.status)

export function indexNowOwner(cycle) {
  const profile = cycle?.profile || cycle?.baseline?.profile || {}
  return JSON.stringify([cycle?.id, cycle?.project_id, cycle?.revision, cycle?.test_plan?.plan_sha256, profile.name, profile.entity_type, profile.website])
}

function publicUrl(value) {
  try {
    if (typeof value !== 'string' || /[\s\\]/.test(value)) return null
    const url = new URL(value)
    return url.protocol === 'https:' && !url.username && !url.password && !url.port && !url.search && !url.hash ? url : null
  } catch { return null }
}

export function indexNowOverview(cycle, source) {
  const profile = cycle?.profile || cycle?.baseline?.profile || {}, site = publicUrl(profile.website)
  const pageScope = site ? `${site.origin}${site.pathname.replace(/\/+$/, '')}/` : ''
  const supported = Boolean(site && identifier(cycle?.id) && source?.supported === true && source.cycle_id === cycle.id
    && source.project_id === cycle.project_id && source.site_origin === site.origin && digest(source.context_sha256)
    && publicUrl(source.page_scope)?.href === pageScope)
  return { ...source, supported, site_origin: site?.origin || '', page_scope: pageScope,
    context_sha256: supported ? source.context_sha256 : null,
    previews: supported && Array.isArray(source.previews) ? source.previews : [],
    notifications: supported && Array.isArray(source.notifications) ? source.notifications : [],
    reason: !site ? '先为当前项目保存 HTTPS 官网地址。' : !supported ? source?.reason || '尚未读取本项目的通知记录。' : '' }
}

export function indexNowUrls(value, view) {
  const values = typeof value === 'string' ? value.split(/\r?\n/).map(row => row.trim()).filter(Boolean) : value
  if (!Array.isArray(values) || values.length < 1 || values.length > 3) throw new Error('每行一个已更新的网址，最多 3 个。')
  const scope = publicUrl(view?.page_scope)
  const urls = values.map(value => {
    const url = publicUrl(value)
    const path = scope?.pathname.replace(/\/+$/, '') || '/', base = path === '/' ? '/' : `${path}/`
    if (!scope || !url || url.origin !== scope.origin || !(path === '/' || url.pathname === path || url.pathname.startsWith(base))) throw new Error('只可检查当前项目范围内的 HTTPS 页面，不包含查询参数、片段或其他产品。')
    return url.href
  })
  if (new Set(urls).size !== urls.length) throw new Error('网址重复，请每个页面只保留一行。')
  // The service canonicalizes batches with sorted(urls) before hashing them.
  return urls.sort()
}

export function indexNowPreviewPayload(view, keyLocation, text) {
  if (!view?.supported || !digest(view.context_sha256)) throw new Error('请先刷新当前项目的通知记录。')
  const key = publicUrl(keyLocation?.trim())
  if (!key || key.origin !== view.site_origin) throw new Error('请填写当前官网上已部署的公开 IndexNow key 文件完整地址。')
  return { action: 'preview', context_sha256: view.context_sha256, key_location: key.href, urls: indexNowUrls(text, view) }
}

export function indexNowPreview(value, view, input) {
  if (!identifier(value?.id) || value.context_sha256 !== view.context_sha256 || value.key_location !== input.key_location
      || JSON.stringify(value.urls) !== JSON.stringify(input.urls) || !Array.isArray(value.changed_urls)
      || !value.changed_urls.every(url => input.urls.includes(url)) || new Set(value.changed_urls).size !== value.changed_urls.length || !Array.isArray(value.pages)
      || value.pages.length !== input.urls.length || !value.pages.every(page => input.urls.includes(page.url) && digest(page.sha256) && Number.isInteger(page.bytes) && page.bytes > 0)
      || new Set(value.pages.map(page => page.url)).size !== input.urls.length || !Array.isArray(value.blocked_urls)
      || !Number.isFinite(Date.parse(value.expires_at))) throw new Error('预检回执与本次页面范围不一致，请刷新记录后重新预检。')
  return value
}

export function indexNowSubmission(view, preview, requestId, now = Date.now()) {
  if (!view?.supported || preview?.ready !== true || preview.context_sha256 !== view.context_sha256 || !identifier(preview.id)
      || !Array.isArray(preview.changed_urls) || !preview.changed_urls.length || !Number.isFinite(Date.parse(preview.expires_at)) || Date.parse(preview.expires_at) <= now || !uuid(requestId)) throw new Error('预检已失效或没有可通知的页面，请重新预检。')
  const payload = { action: 'submit', context_sha256: view.context_sha256, preview_id: preview.id, request_id: requestId }
  if (preview.retry_receipt_id) {
    if (!identifier(preview.retry_receipt_id)) throw new Error('重试记录无效，请刷新记录。')
    payload.retry_receipt_id = preview.retry_receipt_id
  }
  return { cycle_id: view.cycle_id, project_id: view.project_id, context_sha256: view.context_sha256, urls: [...preview.changed_urls], payload }
}

export function indexNowReceiptMatches(receipt, frozen) {
  return Boolean(receipt && frozen && receipt.request_id === frozen.payload.request_id && receipt.preview_id === frozen.payload.preview_id
    && receipt.context_sha256 === frozen.context_sha256 && JSON.stringify(receipt.urls) === JSON.stringify(frozen.urls)
    && ['reserved', 'received', 'key_pending', 'rejected', 'delivery_unknown'].includes(receipt.status))
}

export const indexNowStorageKey = view => view?.supported ? `geo.indexnow.pending.v1:${view.cycle_id}:${view.project_id ?? 'none'}:${view.context_sha256}` : ''
export function indexNowRestore(storage, view) {
  try {
    const value = JSON.parse(storage?.getItem(indexNowStorageKey(view)) || 'null')
    return value?.cycle_id === view.cycle_id && value.project_id === view.project_id && value.context_sha256 === view.context_sha256
      && uuid(value.payload?.request_id) && identifier(value.payload?.preview_id) && value.payload?.context_sha256 === view.context_sha256
      && value.payload?.action === 'submit' && Array.isArray(value.urls) && value.urls.length > 0 && value.urls.length <= 3
      && JSON.stringify(indexNowUrls(value.urls, view)) === JSON.stringify(value.urls) ? value : null
  } catch { return null }
}
