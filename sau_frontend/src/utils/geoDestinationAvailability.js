const inactiveStates = new Set(['disabled', 'archived', 'deleted'])

export const destinationEnabled = target => Boolean(target && target.config &&
  (target.config.enabled === undefined || target.config.enabled === true) && !inactiveStates.has(target.state))

export const writingChannelEnabled = (cycle, key) => {
  if (!key || !Object.hasOwn(cycle?.channels || {}, key)) return false
  const target = cycle?.destinations?.find(item => item.key === key)
  return target ? destinationEnabled(target) : !key.startsWith('target_')
}

export const inactiveDestinationMessage = '平台已停用，原稿和记录仍保留。可在编辑接入配置中明确启用后继续。'
