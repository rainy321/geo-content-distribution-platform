const digest = value => typeof value === 'string' && /^[a-f0-9]{64}$/i.test(value)
const identifier = value => typeof value === 'string' && /^[\w-]{1,100}$/.test(value)
const uuid = value => typeof value === 'string' && /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value)
export const indexNowStatus = status => ({ reserved: '正在等待同次通知回执', received: '200 · 更新通知已接收', key_pending: '202 · 密钥校验中', rejected: '明确拒绝，尚未接收', delivery_unknown: '送达结果未知，不能重发' }[status] || '通知状态未知，先核对记录')
export const indexNowError = cause => typeof cause?.response?.data?.msg === 'string' ? cause.response.data.msg : cause?.message || '未取得结果，请刷新记录核对。'
export const indexNowUncertain = cause => ![400, 401, 403, 404, 409, 422].includes(cause?.response?.status)

export function indexNowOwner(cycle) {
  const profile = cycle?.profile || cycle?.baseline?.profile || {}
  return JSON.stringify([cycle?.id, cycle?.project_id, cycle?.revision, cycle?.test_plan?.plan_sha256, profile.name, profile.entity_type, profile.website, profile.official_urls || []])
}

function publicUrl(value) {
  try {
    if (typeof value !== 'string' || /[\s\\]/.test(value)) return null
    const url = new URL(value)
    return url.protocol === 'https:' && !url.username && !url.password && !url.port && !url.search && !url.hash ? url : null
  } catch { return null }
}

const scopeUrl = value => {
  const url = publicUrl(value)
  return url ? `${url.origin}${url.pathname.replace(/\/+$/, '')}/` : ''
}

export function indexNowAssets(cycle, source) {
  const profile = cycle?.profile || cycle?.baseline?.profile || {}, primaryScope = scopeUrl(profile.website)
  if (!primaryScope) return []
  if (!Array.isArray(source?.assets)) return [{ id: '', url: profile.website, site_origin: publicUrl(profile.website).origin, page_scope: primaryScope, primary: true, control_status: 'not_checked' }]
  const registered = (Array.isArray(profile.official_urls) ? profile.official_urls : []).map(scopeUrl).filter(Boolean)
  const ids = new Set(), scopes = new Set(), assets = []
  for (const row of source.assets) {
    const canonical = scopeUrl(row?.url), site = publicUrl(row?.url)
    if (!digest(row?.id) || !canonical || !site || typeof row.primary !== 'boolean' || ids.has(row.id) || scopes.has(canonical)
      || row.site_origin !== site.origin || publicUrl(row.page_scope)?.href !== canonical
      || (row.primary ? canonical !== primaryScope : !registered.includes(canonical) || canonical === primaryScope)) continue
    ids.add(row.id); scopes.add(canonical)
    assets.push({ ...row, control_status: 'not_checked' })
  }
  return assets
}

export function indexNowOverview(cycle, source, assetId = '') {
  const profile = cycle?.profile || cycle?.baseline?.profile || {}, primary = publicUrl(profile.website)
  const assets = indexNowAssets(cycle, source)
  const selected = assets.find(row => assetId ? row.id === assetId && !row.primary : row.primary)
  const site = publicUrl(selected?.url), pageScope = selected?.page_scope || ''
  const selectionMatches = !Array.isArray(source?.assets) ? !assetId
    : Boolean(selected && source.selected_asset_id === selected.id)
  const supported = Boolean(site && identifier(cycle?.id) && source?.supported === true && source.cycle_id === cycle.id
    && source.project_id === cycle.project_id && source.site_origin === site.origin && digest(source.context_sha256)
    && publicUrl(source.page_scope)?.href === pageScope && selectionMatches)
  return { ...source, assets, asset_id: assetId, supported, site_origin: site?.origin || '', page_scope: pageScope,
    context_sha256: supported ? source.context_sha256 : null,
    previews: supported && Array.isArray(source.previews) ? source.previews : [],
    notifications: supported && Array.isArray(source.notifications) ? source.notifications : [],
    reason: !primary ? '先为当前项目保存 HTTPS 官网地址。' : !supported ? source?.reason || '尚未读取所选官网的通知记录，请检查原记录。' : '' }
}

const assetSelectionKey = cycle => `geo.indexnow.asset.v1:${cycle?.id}:${cycle?.project_id ?? 'none'}`
export function indexNowReadAssetSelection(storage, cycle) {
  try {
    const saved = JSON.parse(storage?.getItem(assetSelectionKey(cycle)) || 'null')
    const profile = cycle?.profile || cycle?.baseline?.profile || {}, registered = (profile.official_urls || []).map(scopeUrl)
    return saved?.owner === indexNowOwner(cycle) && digest(saved.asset_id) && registered.includes(scopeUrl(saved.url)) ? saved.asset_id : ''
  } catch { return '' }
}
export function indexNowSaveAssetSelection(storage, cycle, asset) {
  if (!storage) throw new Error('未能保存所选官网，请检查本地存储后再切换。')
  const key = assetSelectionKey(cycle)
  if (asset.primary) { storage.removeItem(key); return }
  const saved = { owner: indexNowOwner(cycle), asset_id: asset.id, url: asset.url }
  storage.setItem(key, JSON.stringify(saved))
  if (indexNowReadAssetSelection(storage, cycle) !== asset.id) throw new Error('未能保存所选官网，请检查本地存储后再切换。')
}

export function indexNowKeyFile(view, crypto = globalThis.crypto) {
  if (!view?.supported || !publicUrl(view.page_scope) || publicUrl(view.page_scope).origin !== view.site_origin) throw new Error('请先读取所选官网范围，再生成验证文件。')
  let key
  if (typeof crypto?.getRandomValues === 'function') key = Array.from(crypto.getRandomValues(new Uint8Array(16)), byte => byte.toString(16).padStart(2, '0')).join('')
  else {
    const random = crypto?.randomUUID?.()
    if (!uuid(random)) throw new Error('当前环境无法安全生成验证文件，请更新软件。')
    key = random.replaceAll('-', '').toLowerCase()
  }
  if (!/^[a-f0-9]{32}$/.test(key)) throw new Error('验证文件生成失败，尚未部署或发送。')
  const filename = `${key}.txt`, keyLocation = new URL(filename, view.page_scope).href
  return { content: key, filename, key_location: keyLocation, site_origin: view.site_origin,
    instructions: `请将 ${filename} 原样放入网站公开目录，使 ${keyLocation} 无需登录或跳转即可返回 text/plain。文件正文只包含生成的验证文字，不要添加空格、空行或说明。上传后回到 GEO 检查公开文件和页面；此文件用于搜索引擎校验控制权，不是模型 API 密钥。` }
}

const keyDraftFields = ['cycle_id', 'project_id', 'context_sha256', 'asset_id', 'site_origin', 'page_scope', 'content', 'filename', 'key_location']
export const indexNowKeyDraftStorageKey = view => view?.supported && identifier(view.cycle_id) && Number.isInteger(view.project_id) && view.project_id > 0 && digest(view.context_sha256)
  && (!view.asset_id || digest(view.asset_id)) && publicUrl(view.site_origin)?.origin === view.site_origin && publicUrl(view.page_scope)?.origin === view.site_origin
  ? `geo.indexnow.public-key.v1:${view.cycle_id}:${view.project_id}:${view.context_sha256}:${view.asset_id || 'primary'}` : ''

function validatedKeyDraft(view, record) {
  if (!indexNowKeyDraftStorageKey(view) || !record || Array.isArray(record) || Object.keys(record).length !== keyDraftFields.length
    || !keyDraftFields.every(field => Object.hasOwn(record, field)) || record.cycle_id !== view.cycle_id || record.project_id !== view.project_id
    || record.context_sha256 !== view.context_sha256 || record.asset_id !== (view.asset_id || '') || record.site_origin !== view.site_origin || record.page_scope !== view.page_scope
    || typeof record.content !== 'string' || !/^[a-f0-9]{32}$/.test(record.content) || record.filename !== `${record.content}.txt`
    || record.key_location !== new URL(record.filename, view.page_scope).href) return null
  // Rebuild instructions from the validated public text and scope, never from stored HTML or arbitrary form input.
  return { content: record.content, filename: record.filename, key_location: record.key_location, site_origin: record.site_origin,
    instructions: `请将 ${record.filename} 原样放入网站公开目录，使 ${record.key_location} 无需登录或跳转即可返回 text/plain。文件正文只包含生成的验证文字，不要添加空格、空行或说明。上传后回到 GEO 检查公开文件和页面；此文件用于搜索引擎校验控制权，不是模型 API 密钥。` }
}

export function readValidatedKeyDraft(storage, view) {
  try {
    const key = indexNowKeyDraftStorageKey(view)
    if (!key) return null
    const raw = storage?.getItem(key)
    return typeof raw === 'string' && raw.length <= 8192 ? validatedKeyDraft(view, JSON.parse(raw)) : null
  } catch { return null }
}

export function saveValidatedKeyDraft(storage, view, file) {
  const key = indexNowKeyDraftStorageKey(view)
  const record = { cycle_id: view?.cycle_id, project_id: view?.project_id, context_sha256: view?.context_sha256,
    asset_id: view?.asset_id || '', site_origin: view?.site_origin, page_scope: view?.page_scope,
    content: file?.content, filename: file?.filename, key_location: file?.key_location }
  const expected = validatedKeyDraft(view, record), raw = JSON.stringify(record)
  if (!storage || !key || !expected || raw.length > 8192) throw new Error('本机验证文件草稿未能安全保存，当前界面原文件和地址保留。')
  try { storage.setItem(key, raw) }
  catch { throw new Error('本机验证文件草稿保存结果未确认，当前界面原文件和地址保留。') }
  const saved = readValidatedKeyDraft(storage, view)
  if (!saved || saved.content !== expected.content || saved.key_location !== expected.key_location) throw new Error('本机验证文件草稿回读未成功，保存结果未确认；当前界面原文件和地址保留。')
  return saved
}

export const indexNowReceiptOrigin = receipt => publicUrl(receipt?.site_origin)?.origin || publicUrl(receipt?.key_location)?.origin || '主机未知，请查看原记录'

export function downloadIndexNowKeyFile(file, environment = globalThis) {
  if (!file || !/^[a-f0-9]{32}$/.test(file.content) || file.filename !== `${file.content}.txt`) throw new Error('验证文件内容不完整，请重新生成。')
  const { Blob, URL, document } = environment
  if (!Blob || !URL?.createObjectURL || !document?.createElement) throw new Error('当前窗口无法下载文件，可复制说明交给网站负责人。')
  const href = URL.createObjectURL(new Blob([file.content], { type: 'text/plain;charset=utf-8' })), link = document.createElement('a')
  try { link.href = href; link.download = file.filename; document.body.appendChild(link); link.click() }
  finally { link.remove(); (environment.setTimeout || setTimeout)(() => URL.revokeObjectURL(href), 1000) }
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
      || (view.asset_id && (value.asset_id !== view.asset_id || value.site_origin !== view.site_origin || value.page_scope !== view.page_scope))
      || JSON.stringify(value.urls) !== JSON.stringify(input.urls) || !Array.isArray(value.changed_urls)
      || !value.changed_urls.every(url => input.urls.includes(url)) || new Set(value.changed_urls).size !== value.changed_urls.length || !Array.isArray(value.pages)
      || value.pages.length !== input.urls.length || !value.pages.every(page => input.urls.includes(page.url) && digest(page.sha256) && Number.isInteger(page.bytes) && page.bytes > 0)
      || new Set(value.pages.map(page => page.url)).size !== input.urls.length || !Array.isArray(value.blocked_urls)
      || !Number.isFinite(Date.parse(value.expires_at))) throw new Error('预检回执与本次页面范围不一致，请刷新记录后重新预检。')
  return value
}

export function indexNowSubmission(view, preview, requestId, now = Date.now()) {
  if (!view?.supported || preview?.ready !== true || preview.context_sha256 !== view.context_sha256 || !identifier(preview.id)
      || (view.asset_id && (preview.asset_id !== view.asset_id || preview.site_origin !== view.site_origin || preview.page_scope !== view.page_scope))
      || !Array.isArray(preview.changed_urls) || !preview.changed_urls.length || !Number.isFinite(Date.parse(preview.expires_at)) || Date.parse(preview.expires_at) <= now || !uuid(requestId)) throw new Error('预检已失效或没有可通知的页面，请重新预检。')
  const payload = { action: 'submit', context_sha256: view.context_sha256, preview_id: preview.id, request_id: requestId }
  if (preview.retry_receipt_id) {
    if (!identifier(preview.retry_receipt_id)) throw new Error('重试记录无效，请刷新记录。')
    payload.retry_receipt_id = preview.retry_receipt_id
  }
  return { cycle_id: view.cycle_id, project_id: view.project_id, context_sha256: view.context_sha256, urls: [...preview.changed_urls], payload,
    ...(view.asset_id ? { asset_id: view.asset_id, site_origin: view.site_origin, page_scope: view.page_scope } : {}) }
}

export function indexNowReceiptMatches(receipt, frozen) {
  return Boolean(receipt && frozen && receipt.request_id === frozen.payload.request_id && receipt.preview_id === frozen.payload.preview_id
    && receipt.context_sha256 === frozen.context_sha256 && JSON.stringify(receipt.urls) === JSON.stringify(frozen.urls)
    && (!frozen.asset_id || (receipt.asset_id === frozen.asset_id && receipt.site_origin === frozen.site_origin && receipt.page_scope === frozen.page_scope))
    && ['reserved', 'received', 'key_pending', 'rejected', 'delivery_unknown'].includes(receipt.status))
}

export const indexNowStorageKey = view => view?.supported ? `geo.indexnow.pending.v1:${view.cycle_id}:${view.project_id ?? 'none'}:${view.context_sha256}` : ''
export function indexNowRestore(storage, view) {
  try {
    const value = JSON.parse(storage?.getItem(indexNowStorageKey(view)) || 'null')
    return value?.cycle_id === view.cycle_id && value.project_id === view.project_id && value.context_sha256 === view.context_sha256
      && uuid(value.payload?.request_id) && identifier(value.payload?.preview_id) && value.payload?.context_sha256 === view.context_sha256
      && value.payload?.action === 'submit' && (view.asset_id ? value.asset_id === view.asset_id && value.site_origin === view.site_origin && value.page_scope === view.page_scope : !value.asset_id)
      && Array.isArray(value.urls) && value.urls.length > 0 && value.urls.length <= 3
      && JSON.stringify(indexNowUrls(value.urls, view)) === JSON.stringify(value.urls) ? value : null
  } catch { return null }
}
