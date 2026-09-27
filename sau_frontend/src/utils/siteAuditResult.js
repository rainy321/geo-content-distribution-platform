const EXPECTED_CHECKS = Object.freeze([
  Object.freeze({ key: 'page_access', label: '页面可公开获取' }),
  Object.freeze({ key: 'indexability', label: '页面索引指令' }),
  Object.freeze({ key: 'crawler_access', label: 'AI 检索爬虫规则' }),
  Object.freeze({ key: 'extractable_content', label: '正文可抽取性' }),
  Object.freeze({ key: 'canonical', label: 'Canonical 主地址' }),
  Object.freeze({ key: 'sitemap', label: 'Sitemap' }),
  Object.freeze({ key: 'structured_data', label: '结构化数据' }),
  Object.freeze({ key: 'metadata', label: '基础语义元数据' })
])

const EXPECTED_RESOURCES = Object.freeze([
  Object.freeze({ key: 'robots', label: 'robots.txt' }),
  Object.freeze({ key: 'sitemaps', label: 'Sitemap' }),
  Object.freeze({ key: 'llms_txt', label: 'llms.txt' })
])

const asArray = value => {
  if (Array.isArray(value)) return value
  if (value == null || value === '') return []
  return [value]
}

const unwrapPayload = payload => payload?.data?.data ?? payload?.data ?? payload ?? {}

const numberOrNull = value => {
  if (value == null || value === '') return null
  const number = Number(value)
  return Number.isFinite(number) ? number : null
}

const booleanOrNull = value => {
  if (value === true || value === false) return value
  if (value === 1 || String(value).toLowerCase() === 'true') return true
  if (value === 0 || String(value).toLowerCase() === 'false') return false
  return null
}

const compactStrings = value => [...new Set(asArray(value)
  .map(item => {
    if (typeof item === 'string') return item.trim()
    if (!item || typeof item !== 'object') return ''
    return String(item.message || item.detail || item.evidence || item.url || '').trim()
  })
  .filter(Boolean))]

const canonicalCheckKey = value => ({
  homepage: 'page_access',
  homepage_reachability: 'page_access',
  homepage_access: 'page_access',
  robots: 'crawler_access',
  robots_txt: 'crawler_access',
  sitemap_xml: 'sitemap',
  schema: 'structured_data',
  json_ld: 'structured_data',
  llmstxt: 'llms_txt'
}[String(value || '').trim().toLowerCase()] || String(value || '').trim().toLowerCase())

const normalizedStatus = value => {
  const status = String(value || '').trim().toLowerCase()
  if (['pass', 'passed', 'ok', 'allowed', 'available', 'found', 'valid', 'success', 'present'].includes(status)) return 'passed'
  if (['warn', 'warning', 'partial', 'advisory'].includes(status)) return 'warning'
  if (['fail', 'failed', 'blocked', 'denied', 'invalid', 'missing', 'absent', 'unavailable', 'error'].includes(status)) return 'failed'
  if (['running', 'processing', 'pending'].includes(status)) return 'pending'
  return 'not_checked'
}

const normalizeCheck = (value, fallback = {}) => {
  const source = value && typeof value === 'object' ? value : {}
  const key = canonicalCheckKey(source.key || fallback.key)
  return {
    key: key || fallback.key || 'unknown',
    label: String(source.label || fallback.label || key || '未命名检查'),
    status: normalizedStatus(source.status),
    score: numberOrNull(source.score),
    maxScore: numberOrNull(source.max_score ?? source.maxScore),
    evidence: compactStrings(source.evidence)
  }
}

const normalizeChecks = value => {
  const received = new Map(asArray(value).map(item => {
    const normalized = normalizeCheck(item)
    return [normalized.key, normalized]
  }))
  const known = EXPECTED_CHECKS.map(item => received.get(item.key) || normalizeCheck({}, item))
  const extras = [...received.values()].filter(item => !EXPECTED_CHECKS.some(expected => expected.key === item.key))
  return [...known, ...extras]
}

const normalizeCrawlerAccess = value => asArray(value).map((item, index) => {
  const source = item && typeof item === 'object' ? item : {}
  return {
    id: String(source.user_agent || source.userAgent || `crawler-${index + 1}`),
    userAgent: String(source.user_agent || source.userAgent || '未返回 User-Agent'),
    label: String(source.label || source.name || 'AI 爬虫'),
    purpose: String(source.purpose || ''),
    allowed: booleanOrNull(source.allowed),
    source: String(source.source || '')
  }
})

const normalizeStructuredData = value => {
  const source = value && typeof value === 'object' ? value : {}
  return {
    validBlocks: numberOrNull(source.valid_blocks ?? source.validBlocks),
    invalidBlocks: numberOrNull(source.invalid_blocks ?? source.invalidBlocks),
    types: compactStrings(source.types)
  }
}

const normalizeResource = (key, label, value) => {
  if (Array.isArray(value)) {
    const items = compactStrings(value)
    const statuses = value
      .filter(item => item && typeof item === 'object')
      .map(item => normalizedStatus(item.status))
    const status = statuses.includes('passed')
      ? 'passed'
      : statuses.some(item => item === 'failed')
        ? 'failed'
        : items.length
          ? 'passed'
          : 'not_checked'
    return {
      key,
      label,
      status,
      url: '',
      items,
      evidence: [],
      advisoryOnly: false,
      scoreWeight: null
    }
  }
  if (typeof value === 'string') {
    const text = value.trim()
    const url = safeSiteAuditUrl(text)
    return {
      key,
      label,
      status: text ? 'passed' : 'not_checked',
      url,
      items: text && !url ? [text] : [],
      evidence: [],
      advisoryOnly: false,
      scoreWeight: null
    }
  }
  if (typeof value === 'boolean') {
    return {
      key,
      label,
      status: value ? 'passed' : 'failed',
      url: '',
      items: [],
      evidence: [],
      advisoryOnly: key === 'llms_txt',
      scoreWeight: key === 'llms_txt' ? 0 : null
    }
  }
  const source = value && typeof value === 'object' ? value : {}
  const candidateUrls = compactStrings(source.urls ?? source.items ?? source.sitemaps)
  const discovered = booleanOrNull(source.discovered ?? source.found ?? source.available)
  const status = source.status != null
    ? normalizedStatus(source.status)
    : discovered === true
      ? 'passed'
      : discovered === false
        ? 'failed'
        : candidateUrls.length
          ? 'passed'
          : 'not_checked'
  return {
    key,
    label,
    status,
    url: safeSiteAuditUrl(source.url || source.final_url || ''),
    items: candidateUrls,
    evidence: compactStrings(source.evidence ?? source.message ?? source.detail ?? source.error),
    advisoryOnly: booleanOrNull(source.advisory_only ?? source.proposal_only) ?? key === 'llms_txt',
    scoreWeight: numberOrNull(source.score_weight)
  }
}

const normalizeResources = value => {
  const source = value && typeof value === 'object' ? value : {}
  return EXPECTED_RESOURCES.map(item => normalizeResource(item.key, item.label, source[item.key]))
}

const normalizeRecommendations = value => asArray(value).map((item, index) => {
  const source = typeof item === 'string' ? { title: item } : (item || {})
  return {
    id: String(source.code || `recommendation-${index + 1}`),
    priority: String(source.priority || 'P2').toUpperCase(),
    code: String(source.code || ''),
    title: String(source.title || source.message || '待处理问题'),
    action: String(source.action || source.recommendation || '')
  }
})

export const safeSiteAuditUrl = value => {
  try {
    const url = new URL(String(value || '').trim())
    return ['http:', 'https:'].includes(url.protocol) ? url.href : ''
  } catch {
    return ''
  }
}

export const siteAuditStatusPresentation = value => {
  const status = String(value || '').trim().toLowerCase()
  if (['completed', 'complete', 'success', 'ready'].includes(status)) {
    return { tone: 'complete', label: status === 'ready' ? '技术准备较好' : '审计完成' }
  }
  if (['partial', 'warning', 'needs_work'].includes(status)) {
    return { tone: 'warning', label: status === 'needs_work' ? '需要整改' : '部分完成' }
  }
  if (['failed', 'error', 'blocked', 'limited', 'outcome_unknown'].includes(status)) {
    return {
      tone: 'failed',
      label: status === 'blocked'
        ? '存在阻断'
        : status === 'limited'
          ? '准备度较低'
          : status === 'outcome_unknown'
            ? '结果未知'
            : '审计失败'
    }
  }
  if (['running', 'processing', 'pending', 'started'].includes(status)) {
    return { tone: 'pending', label: '审计中' }
  }
  return { tone: 'unknown', label: '未检测' }
}

export const normalizeSiteAuditResult = payload => {
  const container = unwrapPayload(payload)
  const source = container?.result && typeof container.result === 'object'
    ? { ...container.result, id: container.id ?? container.result.id }
    : container
  const score = numberOrNull(source.score)
  return {
    id: source.id ?? source.run_id ?? null,
    projectId: source.project_id ?? source.projectId ?? null,
    requestedUrl: safeSiteAuditUrl(source.requested_url ?? source.requestedUrl),
    finalUrl: safeSiteAuditUrl(source.final_url ?? source.finalUrl),
    status: String(source.status || 'not_checked'),
    score: score == null ? null : Math.min(100, Math.max(0, score)),
    grade: String(source.grade || '未检测'),
    checks: normalizeChecks(source.checks),
    crawlerAccess: normalizeCrawlerAccess(source.crawler_access ?? source.crawlerAccess),
    structuredData: normalizeStructuredData(source.structured_data ?? source.structuredData),
    resources: normalizeResources(source.resources),
    recommendations: normalizeRecommendations(source.recommendations),
    boundaries: compactStrings(source.boundaries),
    auditVersion: String(source.audit_version ?? source.auditVersion ?? ''),
    auditedAt: String(source.audited_at ?? source.auditedAt ?? source.created_at ?? ''),
    resultAvailable: booleanOrNull(source.result_available ?? source.resultAvailable),
    inProgress: booleanOrNull(source.in_progress ?? source.inProgress),
    inProgressExpired: booleanOrNull(source.in_progress_expired ?? source.inProgressExpired),
    outcomeUnknown: booleanOrNull(source.outcome_unknown ?? source.outcomeUnknown),
    inProgressAgeSeconds: numberOrNull(source.in_progress_age_seconds ?? source.inProgressAgeSeconds)
  }
}

export const normalizeSiteAuditHistory = payload => {
  const container = unwrapPayload(payload)
  const rows = Array.isArray(container)
    ? container
    : asArray(container.runs ?? container.items ?? container.results)
  return rows.map(normalizeSiteAuditResult)
}
