const unknown = () => ({ classification: 'unknown', label: '主体待核对', matchedName: '',
  explanation: '来源主体尚未确认；标题或域名只能识别来源范围，不等于业务事实已核实。' })

const definitions = {
  'target:exact_official_host:official_host': {
    label: '官网域名匹配', explanation: '来源网址与登记的官网域名匹配；不等于页面内容或业务事实已核实。'
  },
  'target:exact_official_host:official_page_scope': {
    label: '官方页面范围匹配', explanation: '来源网址在登记的产品官方页面范围内；不等于产品功能或业务事实已核实。'
  },
  'target:exact_legal_name_title:title_identity_only': {
    label: '标题包含完整公司名', explanation: '仅按来源标题识别公司名称；尚未核实链接页面正文或业务事实。'
  },
  'other:different_legal_name_title:title_identity_only': {
    label: '标题为其他公司', explanation: '标题指向另一家公司；仍可作为平台线索，不能当作本对象的业务事实。'
  },
  'related:confirmed_relationship:confirmed_relation_identity_only': {
    label: '关联公司', explanation: '依据已确认的公司关系识别来源范围；关联公司的业务不能直接归给本对象。'
  }
}

export function sourceIdentityView(identity) {
  if (!identity || typeof identity !== 'object' || Array.isArray(identity)
    || !['classification', 'basis', 'evidence_scope', 'matched_name'].every(key => Object.hasOwn(identity, key))
    || !['classification', 'basis', 'evidence_scope'].every(key => typeof identity[key] === 'string')) return unknown()
  const name = identity.matched_name
  const definition = definitions[`${identity.classification}:${identity.basis}:${identity.evidence_scope}`]
  if (!definition || typeof name !== 'string' || !name.trim() || name.length > 240 || /[\u0000-\u001f]/.test(name)) return unknown()
  return { classification: identity.classification, label: definition.label, matchedName: name, explanation: definition.explanation }
}

export function sourceIdentitySummary(opportunity, sources) {
  if (!Array.isArray(opportunity?.source_ids) || !opportunity.source_ids.length || !Array.isArray(sources)) return '来源主体待核对：暂无对应来源明细。'
  const ids = [...new Set(opportunity.source_ids)]
  if (!ids.every(id => typeof id === 'string' && id)) return '来源主体待核对：来源关联信息不完整。'
  const counts = { target: 0, related: 0, other: 0, unknown: 0 }
  for (const id of ids) {
    const matches = sources.filter(source => source && typeof source === 'object' && source.id === id)
    counts[matches.length === 1 ? sourceIdentityView(matches[0].source_identity).classification : 'unknown']++
  }
  const labels = { target: '域名或完整名称匹配', related: '关联公司', other: '标题为其他公司', unknown: '主体待核对' }
  return `来源主体：${Object.entries(counts).filter(([, count]) => count).map(([key, count]) => `${labels[key]} ${count} 条`).join(' · ')}。`
}
