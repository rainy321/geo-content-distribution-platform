const BASE_SHORT_OPTIONAL = Object.freeze(['faq'])
const CITEABILITY_SHORT_OPTIONAL = Object.freeze([
  'faq_decision_support'
])

const BASE_DIMENSION_KEYS = Object.freeze(['entity', 'keywords', 'structure', 'faq', 'citation'])
const CITEABILITY_DIMENSION_KEYS = Object.freeze([
  'direct_answer',
  'entity_consistency',
  'evidence_traceability',
  'structural_extractability',
  'semantic_coverage',
  'faq_decision_support',
  'risk_hygiene'
])

const DIMENSION_LABELS = Object.freeze({
  entity: '品牌实体',
  keywords: '关键词覆盖',
  structure: '内容结构',
  faq: 'FAQ',
  citation: '引用证据',
  direct_answer: '答案前置',
  entity_consistency: '实体一致性',
  evidence_traceability: '证据可追溯',
  structural_extractability: '结构可提取',
  semantic_coverage: '语义覆盖',
  faq_decision_support: 'FAQ 决策支持',
  risk_hygiene: '主张风险控制'
})

const normalizeTargetLength = value => {
  const parsed = Number(value)
  if (!Number.isFinite(parsed)) return 1000
  return Math.max(200, Math.min(3000, Math.round(parsed)))
}

const compactUniqueStrings = value => (
  [...new Set((Array.isArray(value) ? value : [value])
    .map(item => String(item || '').trim())
    .filter(Boolean))]
)

const fallbackScoreLengthProfile = value => {
  const targetLength = normalizeTargetLength(value)
  if (targetLength <= 400) {
    return {
      key: 'compact',
      label: '精简稿档',
      rangeLabel: '200–400 字',
      shortForm: true,
      serverBacked: false,
      targetLength,
      targetLabel: `${targetLength} 字目标`,
      baseOptionalKeys: [...BASE_SHORT_OPTIONAL],
      citeabilityOptionalKeys: [...CITEABILITY_SHORT_OPTIONAL],
      criteria: {
        minimum_paragraphs: 2,
        headings_required: false,
        faq_required: false,
        summary_required: false
      },
      applicableLabels: ['品牌 / 产品实体', '核心关键词', '短段落结构', '可核验证据', '风险边界'],
      optionalLabels: ['600 字门槛', '至少 3 段', 'H2 / H3', 'FAQ', '重复总结'],
      guidance: [
        '开头 1–2 句直接回答核心问题，并自然写出标准品牌或产品名。',
        '只保留一个核心结论；数字或强主张应紧邻来源、日期和适用范围。',
        '使用 2–4 个紧凑短段落；清单按需使用，不强制 H2、FAQ 或重复总结。'
      ],
      scoringGuidance: []
    }
  }

  if (targetLength < 800) {
    return {
      key: 'short',
      label: '短稿档',
      rangeLabel: '401–799 字',
      shortForm: true,
      serverBacked: false,
      targetLength,
      targetLabel: `${targetLength} 字目标`,
      baseOptionalKeys: [...BASE_SHORT_OPTIONAL],
      citeabilityOptionalKeys: [...CITEABILITY_SHORT_OPTIONAL],
      criteria: {
        minimum_paragraphs: 2,
        headings_required: false,
        faq_required: false,
        summary_required: false
      },
      applicableLabels: ['实体一致性', '关键词覆盖', '短段落结构', '证据来源', '风险边界'],
      optionalLabels: ['H2 / H3', 'FAQ', '重复总结'],
      guidance: [
        '首段直接回答问题，再用至少 2 个清晰段落解释机制和适用边界。',
        '小标题、FAQ 和总结按信息需要使用，不为凑结构机械堆叠。'
      ],
      scoringGuidance: []
    }
  }

  if (targetLength < 1200) {
    return {
      key: 'standard',
      label: '标准稿档',
      rangeLabel: '800–1199 字',
      shortForm: false,
      serverBacked: false,
      targetLength,
      targetLabel: `${targetLength} 字目标`,
      baseOptionalKeys: [],
      citeabilityOptionalKeys: [],
      criteria: {
        minimum_paragraphs: 3,
        headings_required: true,
        faq_required: true,
        summary_required: true
      },
      applicableLabels: ['实体一致性', '关键词覆盖', '层级结构', '证据来源', 'FAQ / 边界'],
      optionalLabels: [],
      guidance: [
        '首段先回答问题，再用至少 3 个清晰段落展开机制、证据和边界。',
        '小标题和 FAQ 只承载真实信息，避免为了得分机械堆叠。'
      ],
      scoringGuidance: []
    }
  }

  return {
    key: 'long',
    label: '长稿档',
    rangeLabel: '1200–3000 字',
    shortForm: false,
    serverBacked: false,
    targetLength,
    targetLabel: `${targetLength} 字目标`,
    baseOptionalKeys: [],
    citeabilityOptionalKeys: [],
    criteria: {
      minimum_paragraphs: 4,
      headings_required: true,
      faq_required: true,
      summary_required: true
    },
    applicableLabels: ['实体一致性', '语义覆盖', '层级结构', '证据追溯', 'FAQ / 决策支持'],
    optionalLabels: [],
    guidance: [
      '每个章节解决一个子问题，并让关键结论脱离上下文仍可理解。',
      '补充方法、数据、来源、日期和限制条件，避免只有篇幅没有证据。'
    ],
    scoringGuidance: []
  }
}

export const resolveScoreLengthProfile = (value, serverProfile = null) => {
  const legacyProfile = Boolean(
    serverProfile
    && (serverProfile.key === 'legacy' || serverProfile.source === 'legacy')
    && serverProfile.requestedTargetLength == null
    && serverProfile.targetLength == null
  )
  const requestedTarget = legacyProfile
    ? null
    : (serverProfile?.requestedTargetLength ?? serverProfile?.targetLength ?? value)
  const effectiveTarget = (
    legacyProfile
      ? null
      : (
          serverProfile?.effectiveTargetLength
          ?? serverProfile?.targetLength
          ?? requestedTarget
        )
  )
  const fallback = fallbackScoreLengthProfile(legacyProfile ? 1000 : effectiveTarget)
  if (!serverProfile || typeof serverProfile !== 'object') return fallback

  const serverNotApplicable = compactUniqueStrings(serverProfile.notApplicableDimensions)
  const serverApplicable = compactUniqueStrings(serverProfile.applicableDimensions)
  const useServerApplicability = serverProfile.applicabilityProvided === true
  const serverBaseApplicable = serverApplicable.filter(key => BASE_DIMENSION_KEYS.includes(key))
  const serverBaseNotApplicable = serverNotApplicable.filter(key => BASE_DIMENSION_KEYS.includes(key))
  const serverCiteabilityApplicable = serverApplicable.filter(key => CITEABILITY_DIMENSION_KEYS.includes(key))
  const serverCiteabilityNotApplicable = serverNotApplicable.filter(key => CITEABILITY_DIMENSION_KEYS.includes(key))
  const hasServerBaseApplicability = serverBaseApplicable.length > 0 || serverBaseNotApplicable.length > 0
  const hasServerCiteabilityApplicability = (
    serverCiteabilityApplicable.length > 0 || serverCiteabilityNotApplicable.length > 0
  )
  const baseOptionalKeys = useServerApplicability && hasServerBaseApplicability
    ? BASE_DIMENSION_KEYS.filter(key => (
        serverBaseNotApplicable.includes(key)
        || (serverBaseApplicable.length > 0 && !serverBaseApplicable.includes(key))
      ))
    : fallback.baseOptionalKeys
  const citeabilityOptionalKeys = useServerApplicability && hasServerCiteabilityApplicability
    ? CITEABILITY_DIMENSION_KEYS.filter(key => (
        serverCiteabilityNotApplicable.includes(key)
        || (serverCiteabilityApplicable.length > 0 && !serverCiteabilityApplicable.includes(key))
      ))
    : fallback.citeabilityOptionalKeys
  const remoteGuidance = compactUniqueStrings(serverProfile.guidance)
  const serverCriteria = serverProfile.criteria && typeof serverProfile.criteria === 'object'
    ? serverProfile.criteria
    : {}
  const applicableKeys = useServerApplicability && serverApplicable.length
    ? serverApplicable
    : [...BASE_DIMENSION_KEYS, ...CITEABILITY_DIMENSION_KEYS]
      .filter(key => ![...baseOptionalKeys, ...citeabilityOptionalKeys].includes(key))
  const optionalKeys = useServerApplicability
    ? serverNotApplicable
    : [...baseOptionalKeys, ...citeabilityOptionalKeys]
  const optionalCriteriaLabels = []
  if (Number(serverProfile.minimumLength) < 600) optionalCriteriaLabels.push('600 字门槛')
  if (Number(serverCriteria.minimum_paragraphs) > 0 && Number(serverCriteria.minimum_paragraphs) < 3) {
    optionalCriteriaLabels.push('至少 3 段')
  }
  if (serverCriteria.headings_required === false) optionalCriteriaLabels.push('H2 / H3')
  if (serverCriteria.faq_required === false) optionalCriteriaLabels.push('FAQ')
  if (serverCriteria.summary_required === false) optionalCriteriaLabels.push('重复总结')

  return {
    ...fallback,
    key: /^[a-z0-9_-]{1,40}$/i.test(String(serverProfile.key || ''))
      ? String(serverProfile.key)
      : fallback.key,
    label: String(serverProfile.label || fallback.label),
    rangeLabel: legacyProfile ? '旧稿未声明目标' : fallback.rangeLabel,
    targetLength: legacyProfile ? null : normalizeTargetLength(requestedTarget),
    targetLabel: legacyProfile ? '旧稿未声明目标' : `${normalizeTargetLength(requestedTarget)} 字目标`,
    requestedTargetLength: legacyProfile ? null : normalizeTargetLength(requestedTarget),
    effectiveTargetLength: legacyProfile ? null : normalizeTargetLength(effectiveTarget),
    effectiveTargetLabel: legacyProfile
      ? '兼容长文规则'
      : `${normalizeTargetLength(effectiveTarget)} 字计分档`,
    overTarget: serverProfile.overTarget === true,
    actualLength: serverProfile.actualLength,
    minimumLength: serverProfile.minimumLength,
    version: String(serverProfile.version || ''),
    source: String(serverProfile.source || ''),
    serverBacked: true,
    criteria: serverCriteria,
    baseOptionalKeys,
    citeabilityOptionalKeys,
    applicableLabels: useServerApplicability
      ? applicableKeys.map(key => DIMENSION_LABELS[key] || key)
      : fallback.applicableLabels,
    optionalLabels: useServerApplicability
      ? compactUniqueStrings([
          ...optionalKeys.map(key => DIMENSION_LABELS[key] || key),
          ...optionalCriteriaLabels
        ])
      : fallback.optionalLabels,
    scoringGuidance: remoteGuidance,
    guidance: fallback.guidance
  }
}

export const isLengthMetricApplicable = (profile, scope, key) => {
  const optionalKeys = scope === 'citeability'
    ? profile?.citeabilityOptionalKeys
    : profile?.baseOptionalKeys
  return !Array.isArray(optionalKeys) || !optionalKeys.includes(key)
}

const requiredLengthFromSuggestion = suggestion => {
  const match = String(suggestion || '').match(/至少\s*(\d{2,4})\s*字/)
  return match ? Number(match[1]) : null
}

const requiredParagraphsFromSuggestion = suggestion => {
  const match = String(suggestion || '').match(/至少\s*(\d{1,2})\s*个?\s*清晰段落/)
  return match ? Number(match[1]) : null
}

const SHORT_FORM_ONLY_NOISE = [
  /H2\s*[/／]\s*H3/i,
  /FAQ|常见问题|常见问答/i,
  /总结|结语/
]

export const filterLengthAwareSuggestions = (value, profile) => (
  compactUniqueStrings(value).filter((suggestion) => {
    const requiredLength = requiredLengthFromSuggestion(suggestion)
    const scoringTargetLength = (
      profile?.effectiveTargetLength
      ?? profile?.targetLength
      ?? Number.POSITIVE_INFINITY
    )
    if (requiredLength && requiredLength > scoringTargetLength) return false
    const requiredParagraphs = requiredParagraphsFromSuggestion(suggestion)
    const allowedParagraphs = Number(
      profile?.criteria?.minimum_paragraphs
      ?? profile?.criteria?.minimumParagraphs
      ?? (profile?.shortForm ? 2 : Number.POSITIVE_INFINITY)
    )
    if (requiredParagraphs && requiredParagraphs > allowedParagraphs) return false
    if (profile?.shortForm !== true) return true
    return !SHORT_FORM_ONLY_NOISE.some(pattern => pattern.test(suggestion))
  })
)

export const resolveScoreBand = value => {
  const parsed = Number(value)
  const score = Number.isFinite(parsed) ? Math.max(0, Math.min(100, parsed)) : 0
  if (score >= 85) return { code: 'A', label: '人工复核档', minimum: 85 }
  if (score >= 70) return { code: 'B', label: '继续加强档', minimum: 70 }
  return { code: 'C', label: '优先补证据档', minimum: 0 }
}
