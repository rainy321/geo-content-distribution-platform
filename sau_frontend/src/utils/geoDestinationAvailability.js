const inactiveStates = new Set(['disabled', 'archived', 'deleted'])

export const destinationEnabled = target => Boolean(target && target.config &&
  (target.config.enabled === undefined || target.config.enabled === true) && !inactiveStates.has(target.state))

export const writingChannelEnabled = (cycle, key) => {
  if (!key || !Object.hasOwn(cycle?.channels || {}, key)) return false
  const target = cycle?.destinations?.find(item => item.key === key)
  return target ? destinationCanPrepare(target) : !key.startsWith('target_')
}

export const inactiveDestinationMessage = '平台已停用，原稿和记录仍保留。可在编辑接入配置中明确启用后继续。'

export const destinationPublicationGate = target => {
  if(!destinationEnabled(target))return {allowed:false,code:'inactive',reason:inactiveDestinationMessage}
  const gate=target?.publication_gate
  if(gate?.allowed===true && gate.code==='allowed')return {allowed:true,code:'allowed',reason:typeof gate.reason==='string'?gate.reason:'已记录本次用途核对；投稿资格和文章仍需检查。'}
  return {allowed:false,code:typeof gate?.code==='string'?gate.code:'review_unknown',
    reason:typeof gate?.reason==='string' && gate.reason?gate.reason:'先核对平台用途与规则，再开始写稿或登录；原稿和记录保留。'}
}

export const destinationCanPrepare = target => destinationPublicationGate(target).allowed
export const writingChannelLabel = (cycle,key,label) => {
  const target=cycle?.destinations?.find(item=>item.key===key)
  if(!target || destinationCanPrepare(target))return label
  return `${label} · ${destinationEnabled(target)?'用途待核对':'已停用'}`
}
