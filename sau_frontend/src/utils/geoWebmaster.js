export const bingWebmasterEntry = 'https://www.bing.com/webmasters/'
export const webmasterFileLimit = 16384
const digest = value => typeof value === 'string' && /^[a-f0-9]{64}$/i.test(value)

export function webmasterOrigin(cycle) {
  try {
    const url = new URL((cycle?.profile || cycle?.baseline?.profile)?.website)
    if (url.protocol !== 'https:' || url.username || url.password || url.port) return ''
    return url.origin
  } catch { return '' }
}

export function webmasterOverview(cycle, source = cycle?.webmaster) {
  const origin = webmasterOrigin(cycle)
  const supported = Boolean(origin && /^[\w-]{1,100}$/.test(cycle?.id || '') && source?.supported === true
    && source.site_origin === origin && digest(source.context_sha256))
  return {
    supported, site_origin: origin, entry_url: bingWebmasterEntry,
    verification_url: origin ? `${origin}/BingSiteAuth.xml` : '', sitemap_url: origin ? `${origin}/sitemap.xml` : '',
    context_sha256: supported ? source.context_sha256 : null, record: supported ? source.record || null : null,
    target_urls: supported && Array.isArray(source.target_urls) ? [...new Set(source.target_urls.filter(url => webmasterSiteUrl(url, origin)))] : [],
    reason: !origin ? '请先保存这个公司或产品的 HTTPS 官网地址。' : !supported ? source?.reason || '尚未读取站长流程，点击刷新读取进度。' : ''
  }
}

export function webmasterSiteUrl(value, origin) {
  try {
    const url = new URL(value)
    return typeof value === 'string' && !/\s/.test(value) && url.origin === origin && !url.username && !url.password && !url.hash
  } catch { return false }
}

export function webmasterPayload(view, action, fields = {}) {
  if (!view?.supported || !digest(view.context_sha256)) throw new Error('官网资料已变化，请先刷新进度。')
  const payload = { action, context_sha256: view.context_sha256 }
  if (action === 'upload_xml') {
    const bytes = webmasterDecodeFile(fields.xml_base64)
    if (!bytes.length || bytes.length > webmasterFileLimit) throw new Error('请选择不超过 16KB 的 BingSiteAuth.xml 原文件。')
    payload.xml_base64 = fields.xml_base64
  } else if (action === 'confirm_ownership') {
    if (fields.confirmed !== true) throw new Error('请先在 Bing 页面完成网站验证。')
    payload.confirmed = true
  } else if (action === 'record_sitemap') {
    if (!['success', 'pending', 'failed'].includes(fields.status)) throw new Error('请选择 Bing 实际显示的处理状态。')
    payload.status = fields.status
    if (fields.note !== undefined) {
      if (typeof fields.note !== 'string' || Array.from(fields.note).length > 500) throw new Error('核对备注最多 500 字。')
      payload.note = fields.note
    }
    if (fields.discovered_urls !== undefined) {
      if (fields.discovered_urls !== null && (!Number.isSafeInteger(fields.discovered_urls) || fields.discovered_urls < 0)) throw new Error('发现网址数请填写非负整数，未知时留空。')
      payload.discovered_urls = fields.discovered_urls
    }
  } else if (action === 'record_inspection') {
    if (!webmasterSiteUrl(fields.url, view.site_origin)) throw new Error('请填写当前官网下的完整页面网址。')
    if (!['indexed', 'not_indexed', 'unknown'].includes(fields.status)) throw new Error('请选择该页面实际显示的索引状态。')
    if (typeof fields.note !== 'string' || Array.from(fields.note).length > 500) throw new Error('核对备注最多 500 字。')
    Object.assign(payload, { url: fields.url, status: fields.status, note: fields.note })
  } else if (action !== 'check_public') throw new Error('未知的站长操作。')
  return payload
}

export const webmasterStage = record => record?.ownership?.status === 'confirmed' ? 3 : record?.verification_file ? 2 : 0
export const webmasterPublicLabel = record => ({ matches: '与原件一致', mismatch: '与原件不一致', xml_present: '已访问到 XML，待核对原件',
  not_found: '文件尚未部署', invalid_xml: '返回内容不是有效 XML', redirect: '文件地址发生跳转', unreachable: '本次访问未完成' }[record?.public_check?.status] || '尚未核对')
export const webmasterSitemapLabel = record => ({ success: 'Bing 处理成功（用户确认）', pending: '已提交，正在处理（用户确认）', failed: '处理失败（用户确认）' }[record?.sitemap_submission?.status] || '尚未登记提交结果')
export const webmasterIndexLabel = status => ({ indexed: '已索引', not_indexed: '未索引', unknown: '索引状态未知' }[status] || '索引状态未知')

export function webmasterDecodeFile(value) {
  if (typeof value !== 'string' || !value || value.length > 4 * Math.ceil(webmasterFileLimit / 3)
    || !/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(value)) throw new Error('验证文件内容无效，请重新选择原文件。')
  const binary = atob(value)
  return Uint8Array.from(binary, character => character.charCodeAt(0))
}

export async function webmasterReadFile(file) {
  if (!file || typeof file.name !== 'string' || !/^BingSiteAuth(?:\s*\(\d+\))?\.xml$/i.test(file.name) || !Number.isInteger(file.size) || file.size < 1 || file.size > webmasterFileLimit) {
    throw new Error('请选择从 Bing 下载的 BingSiteAuth.xml，大小不超过 16KB。')
  }
  const bytes = new Uint8Array(await file.arrayBuffer())
  if (bytes.length !== file.size) throw new Error('文件读取不完整，请重新选择原文件。')
  return btoa(Array.from(bytes, value => String.fromCharCode(value)).join(''))
}

export function webmasterDeploymentText(view) {
  if (!view?.supported) return ''
  return `请为官网 ${view.site_origin}/ 部署附带的 BingSiteAuth.xml 原文件。先确认该域名实际部署目录，再将文件原样发布到 ${view.verification_url}，无需登录即可直接返回 HTTP 200 和真实 XML 内容。请将文件纳入后续部署，保持长期可访问。完成后反馈公网地址、HTTP 状态和 XML 核对结果；我再回 Bing 页面点击 Verify / Try Again。`
}

export const webmasterError = error => typeof error?.response?.data?.msg === 'string' ? error.response.data.msg
  : typeof error?.message === 'string' ? error.message : '进度未取得确认，请刷新核对后再继续。'
