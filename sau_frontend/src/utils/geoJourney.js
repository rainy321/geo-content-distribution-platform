const PREFIX = 'geo-journey:v1:'
export const normalizeName = value => String(value || '').trim().toLocaleLowerCase()
export const articleSnapshot = draft => draft ? {title:draft.title,content:draft.content,summary:draft.summary||'',tags:draft.tags||[]} : null
export function targetDraftOutdated(draft, target) {
  if (!draft || !target || draft.platform !== target.key) return false
  const prior=draft.provenance?.destination, digest=draft.provenance?.destination_config_sha256
  return Boolean(prior && prior.id===target.id && (
    Number.isInteger(prior.revision) && Number.isInteger(target.revision) && prior.revision!==target.revision
    || typeof digest==='string' && digest && typeof target.config_sha256==='string' && target.config_sha256 && digest!==target.config_sha256))
}
export function journeyDraftLimits(target, channel) {
  const positive=value=>Number.isInteger(value)&&value>0?value:null
  return {title:Math.min(positive(target?.config?.title_limit)||180,channel==='toutiao'?30:180),body:positive(target?.config?.body_limit)}
}
export function journeyLengthReview(editor, limits) {
  const titleCount=Array.from(String(editor.title||'').trim()).length, bodyCount=Array.from(String(editor.content||'').trim()).length
  return {titleCount,bodyCount,
    titleIssue:titleCount>limits.title?`标题 ${titleCount} 字，超过 ${limits.title} 字上限，请手动精简后审稿。`:'',
    bodyIssue:limits.body&&bodyCount>limits.body?`正文 ${bodyCount} 字，超过 ${limits.body} 字上限，请手动精简后审稿。`:''}
}
export function baselineEvidenceSummary(report) {
  const cases=Array.isArray(report?.cases)?report.cases:null
  const invalid=new Set(['failed','stopped','pending','running','needs_review','need_action','service_unavailable','deferred_answer'])
  const effective=cases?.filter(item=>item.assessment?.answer_evaluable===true&&!invalid.has(item.status)).length||0
  const failed=cases?.filter(item=>item.status==='failed').length||0
  return {known:Boolean(cases),total:cases?.length||0,effective,failed,otherExcluded:(cases?.length||0)-effective-failed,
    inspections:(cases||[]).filter(item=>item.source_reinspection?.inspected_at||item.source_inspected_at).map(item=>({
      caseId:item.id,inspectedAt:item.source_reinspection?.inspected_at||item.source_inspected_at,
      originalAt:item.source_reinspection?.original_answer_timestamp||item.collected_at,
      partial:item.source_reinspection?.source_collection_status==='partial'}))}
}
export function canRestoreArticleEdits(saved, draft, cycleId) {
  return Boolean(saved.editorDirty === true && saved.editor && draft
    && saved.cycleId === cycleId && saved.articleId === draft.article_id
    && saved.platform === draft.platform && saved.briefId === draft.brief_id
    && !['published','publishing'].includes(draft.status)
    && JSON.stringify(saved.editorBase) === JSON.stringify(articleSnapshot(draft)))
}
export function canRestoreFactEdits(saved, cycle) {
  return Boolean(Array.isArray(saved.factRows) && saved.cycleId === cycle.id
    && JSON.stringify(saved.factsBase) === JSON.stringify(cycle.facts))
}
export function websiteHost(value) {
  try { return new URL(String(value).includes('://') ? value : `https://${value}`).hostname.toLowerCase().replace(/^www\./, '') }
  catch { return '' }
}
export function sameBrand(left, right) {
  return Boolean(left && right && normalizeName(left.name) === normalizeName(right.name)
    && websiteHost(left.website) && websiteHost(left.website) === websiteHost(right.website))
}
export function normalizeWebsite(value) {
  const text = String(value || '').trim()
  const url = new URL(text.includes('://') ? text : `https://${text}`)
  if (!['https:', 'http:'].includes(url.protocol) || !url.hostname.includes('.') || url.username || url.password || url.search || url.hash) throw new Error('请填写不含账号、查询参数的完整官网地址')
  return url.href.replace(/\/$/, '')
}
export function readJourney(projectId) {
  try { const value = JSON.parse(localStorage.getItem(PREFIX + projectId) || '{}'); return value && typeof value === 'object' && !Array.isArray(value) ? value : {} }
  catch { return {} }
}
export function writeJourney(projectId, value) {
  if (!Number.isSafeInteger(projectId) || projectId <= 0) return
  localStorage.setItem(PREFIX + projectId, JSON.stringify(value))
  localStorage.setItem(PREFIX + 'last-project', String(projectId))
}
export function lastJourneyProject() {
  try { const value = Number(localStorage.getItem(PREFIX + 'last-project')); return Number.isSafeInteger(value) && value > 0 ? value : null }
  catch { return null }
}
export function safeLink(value) {
  try { const url = new URL(value); return ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password ? url.href : null }
  catch { return null }
}
export const journeyDate = value => {
  const date = new Date(value)
  return value && Number.isFinite(date.getTime()) ? date.toLocaleString('zh-CN', {timeZone:'Asia/Hong_Kong',hour12:false}) : '时间未知'
}
