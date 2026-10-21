export { destinationPublicationGate, destinationCanPrepare } from './geoDestinationAvailability.js'

export const publicationUses = Object.freeze([
  {value:'research_only',label:'仅作来源参考'},
  {value:'editorial',label:'原创内容投稿'},
  {value:'commercial',label:'商业推广'}
])
const uses = new Set(publicationUses.map(item=>item.value))

export const destinationUseValues = config => ({
  publication_use:uses.has(config?.publication_use)?config.publication_use:'research_only',
  rules_url:typeof config?.rules_url==='string'?config.rules_url:'',
  rule_review:{
    state:['suitable','unsuitable'].includes(config?.rule_review?.state)?config.rule_review.state:'unknown',
    operator_confirmed:config?.rule_review?.operator_confirmed===true,
    note:typeof config?.rule_review?.note==='string'?config.rule_review.note:''
  }
})

export const destinationUseLabel = config => publicationUses.find(item=>item.value===config?.publication_use)?.label || '仅作来源参考'

export const publicationUseRestriction = (policy,use) => {
  if(use==='research_only' || !policy)return ''
  if(policy.automation_restricted===true)return policy.note || '该栏目限制本软件的 AI 自动化内容流程，保留为来源研究。'
  if(use==='commercial' && policy.commercial_restricted===true)return policy.note || '该栏目不适合当前商业推广用途，保留为来源研究。'
  return ''
}

export const safeResearchLink = value => {
  if(typeof value!=='string' || /[\\\s\u0000-\u001f\u007f]/.test(value))return ''
  try {
    const url=new URL(value)
    if(!['https:','http:'].includes(url.protocol) || url.username || url.password || url.port || !url.hostname.includes('.') ||
      /^\d+(\.\d+){3}$/.test(url.hostname) || url.hostname.startsWith('[') ||
      ['.local','.localhost','.internal','.test','.invalid'].some(suffix=>url.hostname.endsWith(suffix)))return ''
    const rawPath=value.match(/^https?:\/\/[^/?#]+([^?#]*)/i)?.[1] || ''
    if(rawPath.split('/').some(segment=>['.','..'].includes(decodeURIComponent(segment))))return ''
    return url.href
  } catch {return ''}
}

export const destinationUseRequest = (target,values) => ({
  revision:target.revision,
  config:{...target.config,...destinationUseValues(values)}
})

export const destinationUseScope = (cycle,target) => ({cycleId:cycle?.id,targetId:target?.id,revision:target?.revision})
export const sameDestinationUseScope = (scope,cycle,target) => Boolean(scope && scope.cycleId===cycle?.id &&
  scope.targetId===target?.id && scope.revision===target?.revision)
