export const CONTENT_GENERATION_SESSION_KEY = 'geo.content-creation.pending-generation.v1'

const REQUEST_ID_PATTERN = /^[A-Za-z0-9._:-]{1,128}$/
const MAX_PENDING_AGE_MS = 24 * 60 * 60 * 1000

const browserSessionStorage = () => {
  try {
    return typeof window !== 'undefined' ? window.sessionStorage : null
  } catch {
    return null
  }
}

export const normalizePendingContentGeneration = (value, now = Date.now()) => {
  const source = value && typeof value === 'object' && !Array.isArray(value) ? value : {}
  const requestId = String(source.requestId || '').trim()
  const startedAt = Number(source.startedAt)
  if (!REQUEST_ID_PATTERN.test(requestId)) return null
  if (!Number.isFinite(startedAt) || startedAt <= 0 || startedAt > now + 60_000) return null
  if (now - startedAt > MAX_PENDING_AGE_MS) return null
  return { requestId, startedAt }
}

export const readPendingContentGeneration = (
  storage = browserSessionStorage(),
  now = Date.now()
) => {
  if (!storage) return null
  try {
    const raw = storage.getItem(CONTENT_GENERATION_SESSION_KEY)
    if (!raw) return null
    const normalized = normalizePendingContentGeneration(JSON.parse(raw), now)
    if (!normalized) storage.removeItem(CONTENT_GENERATION_SESSION_KEY)
    return normalized
  } catch {
    try {
      storage.removeItem(CONTENT_GENERATION_SESSION_KEY)
    } catch {
      // Storage is a convenience only; durable state remains in the backend.
    }
    return null
  }
}

export const savePendingContentGeneration = (
  identity,
  storage = browserSessionStorage()
) => {
  if (!storage) return false
  const normalized = normalizePendingContentGeneration(identity)
  if (!normalized) return false
  try {
    // Deliberately retain only opaque request identity and local start time.
    // API keys, prompts and provider configuration must never enter storage.
    storage.setItem(CONTENT_GENERATION_SESSION_KEY, JSON.stringify(normalized))
    return true
  } catch {
    return false
  }
}

export const clearPendingContentGeneration = (storage = browserSessionStorage()) => {
  if (!storage) return
  try {
    storage.removeItem(CONTENT_GENERATION_SESSION_KEY)
  } catch {
    // Ignore unavailable browser storage.
  }
}
