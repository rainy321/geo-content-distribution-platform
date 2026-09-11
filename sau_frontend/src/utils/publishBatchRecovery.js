export const PUBLISH_BATCH_CHECKPOINT_KEY = 'geo.publish.batch.checkpoint.v1'
export const PUBLISH_BATCH_CHECKPOINT_VERSION = 1
export const PUBLISH_BATCH_MAX_JOB_IDS = 200
export const PUBLISH_BATCH_MAX_AGE_MS = 7 * 24 * 60 * 60 * 1000

const normalizeJobIds = values => [...new Set((Array.isArray(values) ? values : [])
  .map(Number)
  .filter(value => Number.isInteger(value) && value > 0))]
  .slice(0, PUBLISH_BATCH_MAX_JOB_IDS)

export const buildPublishBatchCheckpoint = ({ batchId, jobIds, createdAt }, now = Date.now()) => ({
  version: PUBLISH_BATCH_CHECKPOINT_VERSION,
  batch_id: String(batchId || `publish-batch-${now}`).slice(0, 160),
  job_ids: normalizeJobIds(jobIds),
  created_at: Number.isFinite(Number(createdAt)) ? Number(createdAt) : now,
  updated_at: now
})

export const parsePublishBatchCheckpoint = (raw, now = Date.now()) => {
  if (!raw) return null
  let value
  try {
    value = typeof raw === 'string' ? JSON.parse(raw) : raw
  } catch {
    return null
  }
  if (!value || value.version !== PUBLISH_BATCH_CHECKPOINT_VERSION) return null
  const createdAt = Number(value.created_at)
  if (!Number.isFinite(createdAt) || createdAt > now + 60_000) return null
  if (now - createdAt > PUBLISH_BATCH_MAX_AGE_MS) return null
  const checkpoint = buildPublishBatchCheckpoint({
    batchId: value.batch_id,
    jobIds: value.job_ids,
    createdAt
  }, Number(value.updated_at) || now)
  return checkpoint.job_ids.length ? checkpoint : null
}

export const readPublishBatchCheckpoint = (storage, now = Date.now()) => {
  try {
    return parsePublishBatchCheckpoint(
      storage?.getItem?.(PUBLISH_BATCH_CHECKPOINT_KEY),
      now
    )
  } catch {
    return null
  }
}

export const writePublishBatchCheckpoint = (storage, value, now = Date.now()) => {
  const checkpoint = buildPublishBatchCheckpoint(value, now)
  try {
    if (!checkpoint.job_ids.length) {
      storage?.removeItem?.(PUBLISH_BATCH_CHECKPOINT_KEY)
      return null
    }
    storage?.setItem?.(PUBLISH_BATCH_CHECKPOINT_KEY, JSON.stringify(checkpoint))
    return checkpoint
  } catch {
    return null
  }
}

export const mergePublishBatchCheckpoint = (storage, jobIds, options = {}) => {
  const existing = readPublishBatchCheckpoint(storage, options.now)
  return writePublishBatchCheckpoint(storage, {
    batchId: options.batchId || existing?.batch_id,
    createdAt: existing?.created_at,
    jobIds: [...(existing?.job_ids || []), ...normalizeJobIds(jobIds)]
  }, options.now)
}

export const removePublishBatchJobIds = (storage, jobIds, now = Date.now()) => {
  const existing = readPublishBatchCheckpoint(storage, now)
  if (!existing) return null
  const removed = new Set(normalizeJobIds(jobIds))
  return writePublishBatchCheckpoint(storage, {
    batchId: existing.batch_id,
    createdAt: existing.created_at,
    jobIds: existing.job_ids.filter(jobId => !removed.has(jobId))
  }, now)
}

export const clearPublishBatchCheckpoint = storage => {
  try {
    storage?.removeItem?.(PUBLISH_BATCH_CHECKPOINT_KEY)
  } catch {
    // Storage failure must never change a publish result.
  }
}
