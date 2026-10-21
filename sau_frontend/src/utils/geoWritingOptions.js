const modes = new Set(['consumer', 'work'])
const preferenceKey = id => `geo-content-writing-v1:${id}`
const validId = id => typeof id === 'string' && id.length > 0 && id.length <= 200
const validChoice = value => value && modes.has(value.client_mode) && typeof value.model_label === 'string'

export const baselineWritingOptions = cycle => {
  const execution = cycle?.baseline?.execution || {}
  const knownMode = modes.has(execution.client_mode)
  return {
    client_mode: knownMode ? execution.client_mode : 'consumer',
    // An observed model label is not a request to select that model.
    model_label: knownMode && typeof execution.requested_model_label === 'string' ? execution.requested_model_label.trim() : ''
  }
}

export const createWritingPreferences = storage => {
  const choices = new Map()
  const read = id => {
    if (choices.has(id)) return choices.get(id)
    try {
      const value = JSON.parse(storage?.getItem(preferenceKey(id)) || 'null')
      if (validChoice(value)) {
        const choice = {client_mode:value.client_mode,model_label:value.model_label}
        choices.set(id, choice)
        return choice
      }
    } catch { /* Unavailable or older local preferences do not change the baseline. */ }
    return null
  }
  return {
    get(id, cycle) {
      const choice = validId(id) ? read(id) : null
      return {...(choice || baselineWritingOptions(cycle?.id === id ? cycle : null))}
    },
    set(id, value) {
      if (!validId(id) || !validChoice(value)) return false
      const choice = {client_mode:value.client_mode,model_label:value.model_label}
      choices.set(id, choice)
      try { storage?.setItem(preferenceKey(id), JSON.stringify(choice)) }
      catch { /* Keep the current page's explicit choice even if local storage is full. */ }
      return true
    }
  }
}
