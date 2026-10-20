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
  return Boolean(left && right && normalizeName(left.name) && normalizeName(left.name) === normalizeName(right.name)
    && entityType(left) === entityType(right)
    && websiteIdentity(left.website) && websiteIdentity(left.website) === websiteIdentity(right.website))
}
export const entityType = profile => profile?.entity_type === 'company' ? 'company' : 'product'
export const entityLabel = profile => entityType(profile) === 'company' ? '公司' : '产品'
export function restoreEntityDraftFields(project, saved={}, scopedProjectId=null) {
  const profiles=[saved.profile,saved.plan?.profile,saved.report?.profile].filter(Boolean)
  const matchingProfile=profiles.some(profile=>sameBrand(profile,project))
  const legacyScoped=project&&scopedProjectId===project.id&&saved.project_id==null&&(!profiles.length||matchingProfile)
  const sameEntity=Boolean(project&&(saved.project_id===project.id||legacyScoped||matchingProfile))
  const text=key=>sameEntity&&typeof saved[key]==='string'?saved[key]:typeof project?.[key]==='string'?project[key]:''
  const list=key=>sameEntity&&Array.isArray(saved[key])&&saved[key].every(item=>typeof item==='string')?saved[key]:profileLines(project?.[key])
  return {company:text('company'),audience:text('audience'),aliases:list('aliases'),official_urls:list('official_urls')}
}
export function websiteIdentity(value) {
  try {
    const url=new URL(normalizeWebsite(value))
    const path=decodeURIComponent(url.pathname).normalize('NFC').replace(/\/+$/,'')||'/'
    return JSON.stringify([url.protocol,url.hostname,url.port||(url.protocol==='https:'?'443':'80'),path])
  } catch { return '' }
}
export function profileLines(value) {
  return [...new Set((Array.isArray(value)?value:String(value||'').split(/\r?\n/)).filter(item=>typeof item==='string').map(item=>item.trim()).filter(Boolean))]
}
export function entityProfileFields(profile) {
  const company=String(profile?.company||'').trim(), audience=String(profile?.audience||'').trim()
  const aliases=profileLines(profile?.aliases), urls=profileLines(profile?.official_urls)
  if(Array.from(company).length>300||Array.from(audience).length>300)throw new Error('公司名称与目标客户分别最多300字')
  if(aliases.length>20||aliases.some(value=>Array.from(value).length>120))throw new Error('别名最多20个，每个最多120字')
  if(urls.length>10||urls.some(value=>value.length>2000))throw new Error('额外官方页面最多10个，每个网址最多2000字')
  if(entityType(profile)==='product'&&company&&aliases.some(value=>value.replace(/\s/g,'').toLowerCase()===company.replace(/\s/g,'').toLowerCase()))throw new Error('产品别名不能填写运营公司名称，请分别建立公司与产品档案')
  return {entity_type:entityType(profile),company,audience,aliases,official_urls:urls.map(normalizeWebsite)}
}
export function reportEntityContext(record) {
  const profile=record?.profile||{}, label=entityLabel(profile)
  const legacy=record?.evaluation_semantics==='legacy_mixed_entity_hostwide'||!['company','product'].includes(profile.entity_type)
  return {label,legacy,mentionLabel:legacy?'历史混合提及':`${label}提及`,urls:profileLines([profile.website,...profileLines(profile.official_urls)]),
    note:legacy?'旧报告采用公司与产品混合提及、整域名网址口径，不能按新的独立对象口径解释。建议为当前对象重新出题采集，原记录仍保留。'
      :entityType(profile)==='company'?'仅将公司名称与本对象别名计入公司提及；旗下产品需独立建档测试。官方来源按登记的公司网址范围判断。'
        :'仅将产品名称与本对象别名计入产品提及，运营公司提及单独列出。官方来源仅匹配登记的具体产品或服务网址及其路径范围，不把整站页面算作产品命中。'}
}
export function normalizeWebsite(value) {
  const text = String(value || '').trim()
  if(/[\s\\?#\x00-\x1F\x7F]/u.test(text))throw new Error('请填写不含空白、账号、查询参数或片段的完整官方页面网址')
  const rawPath=(text.includes('://')?text:`https://${text}`).match(/^[a-z][a-z\d+.-]*:\/\/([^/?#]*)([^?#]*)$/i)
  if(!rawPath||rawPath[1].endsWith(':')||/%(?![0-9a-f]{2})|%(?:2f|5c)/i.test(rawPath[2]))throw new Error('官方网址包含无效编码、端口或路径分隔符')
  let decoded
  try{decoded=decodeURIComponent(rawPath[2])}catch{throw new Error('官方网址路径必须使用有效的UTF-8编码')}
  if(/\\|%[0-9a-f]{2}|[\x00-\x1F\x7F]/i.test(decoded)||decoded.split('/').some(segment=>segment==='.'||segment==='..'))throw new Error('请填写没有跳转路径或重复编码的具体官方页面网址')
  const url = new URL(text.includes('://') ? text : `https://${text}`)
  if (!['https:', 'http:'].includes(url.protocol) || !url.hostname.includes('.') || url.username || url.password || url.search || url.hash || url.port==='0') throw new Error('请填写不含账号、查询参数的完整官网地址')
  return url.href.replace(/\/+$/, '')
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
