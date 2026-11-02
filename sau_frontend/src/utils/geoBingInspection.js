import { webmasterInspectionKey, webmasterOrigin, webmasterSiteUrl, webmasterOverview } from './geoWebmaster.js'

const digest = value => typeof value === 'string' && /^[a-f0-9]{64}$/i.test(value)
const identifier = value => typeof value === 'string' && /^[\w-]{1,100}$/.test(value)
const uuid = value => typeof value === 'string' && /^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/i.test(value)

export function bingOwner(cycle, url) {
  const profile = cycle?.profile || cycle?.baseline?.profile || {}
  return JSON.stringify([cycle?.id, cycle?.project_id, profile.name, profile.entity_type, profile.website, url])
}

export function bingTarget(cycle, value, view) {
  const profile = cycle?.profile || cycle?.baseline?.profile || {}
  return webmasterInspectionKey(typeof value === 'string' && value.trim() ? value : profile.website, view?.site_origin)
}

export function bingSnapshot(cycle, view, url, requestId) {
  const target = webmasterInspectionKey(url, view?.site_origin)
  if (!view?.supported || !digest(view.context_sha256) || !identifier(cycle?.id)
      || !Number.isInteger(cycle.project_id) || !target || !uuid(requestId)) throw new Error('请先刷新站长进度，并填写当前官网的完整检查网址。')
  return { cycle_id: cycle.id, project_id: cycle.project_id, url: target,
    context_sha256: view.context_sha256, request_id: requestId }
}

export function bingObservation(value, cycle, origin = webmasterOrigin(cycle)) {
  return Boolean(value && identifier(value.id) && uuid(value.request_id) && value.cycle_id === cycle?.id
    && value.project_id === cycle?.project_id && webmasterSiteUrl(value.url, origin) && digest(value.context_sha256)
    && value.source === 'native_browser_ui' && value.independent === false
    && value.content_version_verified === false && value.geo_effect_verified === false
    && value.platform_timezone === 'unknown' && ['reading', 'observed', 'need_action', 'unsupported'].includes(value.status)
    && ['indexed', 'not_indexed', 'unknown'].includes(value.index_status)
    && (value.observed_at === null || typeof value.observed_at === 'string' && Number.isFinite(Date.parse(value.observed_at)))
    && (value.reason == null || typeof value.reason === 'string')
    && (value.message == null || typeof value.message === 'string')
    && (value.last_crawl_text == null || typeof value.last_crawl_text === 'string')
    && (value.page_fetch_text == null || typeof value.page_fetch_text === 'string'))
}

export function bingObservations(cycle, view) {
  return (Array.isArray(view?.native_inspections) ? view.native_inspections : [])
    .filter(value => bingObservation(value, cycle, view.site_origin))
}

export function bingReceiptMatches(row, captured, cycle) {
  return bingObservation(row, cycle) && row.request_id === captured?.request_id
    && row.context_sha256 === captured.context_sha256 && row.url === captured.url
}

export function bingSavedReply(result, captured, cycle) {
  const view = webmasterOverview(cycle, result?.overview), row = result?.inspection
  const saved = bingObservations(cycle, view).find(item => bingReceiptMatches(item, captured, cycle) && item.id === row?.id)
  if (!view.supported || !bingReceiptMatches(row, captured, cycle) || !saved || saved.status !== row.status
      || saved.index_status !== row.index_status || saved.observed_at !== row.observed_at) throw new Error('读取回执或保存记录与本次请求不一致，请先刷新原记录核对。')
  return row
}

export const bingReadLabel = item => item?.status === 'reading' ? '读取结果待核对'
  : item?.status === 'need_action' ? '本次未取得检查结果'
    : item?.status === 'unsupported' ? '当前环境无法读取'
      : ({ indexed: 'Bing 页面显示已索引', not_indexed: 'Bing 页面显示未索引', unknown: '索引状态未知' }[item?.index_status] || '索引状态未知')

export const bingPendingKey = cycle => `geo.bing-native.pending.v1:${cycle?.id}:${cycle?.project_id}`
export function bingRestore(storage, cycle) {
  try {
    const value = JSON.parse(storage?.getItem(bingPendingKey(cycle)) || 'null')
    return value && value.cycle_id === cycle.id && value.project_id === cycle.project_id && uuid(value.request_id)
      && digest(value.context_sha256) && webmasterSiteUrl(value.url, webmasterOrigin(cycle)) ? value : null
  } catch { return null }
}

export function bingAwait(promise, timers = {
  setTimeout: (callback, milliseconds) => setTimeout(callback, milliseconds),
  clearTimeout: timer => clearTimeout(timer)
}, milliseconds = 45000) {
  let timer, rejectWait, finished = false
  const waiting = new Promise((resolve, reject) => {
    rejectWait = reject
    timer = timers.setTimeout(() => {
      finished = true
      reject(new Error('读取回执超时，请先刷新原记录核对；不会自动再读浏览器。'))
    }, milliseconds)
    Promise.resolve(promise).then(value => {
      if (!finished) { finished = true; timers.clearTimeout(timer); resolve(value) }
    }, cause => {
      if (!finished) { finished = true; timers.clearTimeout(timer); reject(cause) }
    })
  })
  return { promise: waiting, cancel() {
    if (!finished) { finished = true; timers.clearTimeout(timer); rejectWait(new Error('读取页面已切换，原回执保留。')) }
  } }
}
