import assert from 'node:assert/strict'
import test from 'node:test'

import {
  PUBLISH_BATCH_CHECKPOINT_KEY,
  PUBLISH_BATCH_MAX_AGE_MS,
  mergePublishBatchCheckpoint,
  readPublishBatchCheckpoint,
  removePublishBatchJobIds,
  writePublishBatchCheckpoint
} from '../src/utils/publishBatchRecovery.js'


const memoryStorage = () => {
  const values = new Map()
  return {
    getItem: key => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
    removeItem: key => values.delete(key),
    has: key => values.has(key)
  }
}


test('checkpoint retains only bounded positive job ids and no content payload', () => {
  const storage = memoryStorage()
  const saved = writePublishBatchCheckpoint(storage, {
    batchId: 'batch-1',
    jobIds: [5, '5', -1, 0, 8],
    article: { content: 'must not persist' },
    account: { cookie: 'must not persist' }
  }, 1000)

  assert.deepEqual(saved.job_ids, [5, 8])
  const raw = storage.getItem(PUBLISH_BATCH_CHECKPOINT_KEY)
  assert.doesNotMatch(raw, /must not persist|cookie|article/)
})


test('completed or unsafe jobs can be removed without losing queued siblings', () => {
  const storage = memoryStorage()
  writePublishBatchCheckpoint(storage, {
    batchId: 'batch-2',
    jobIds: [11, 12, 13]
  }, 2000)

  removePublishBatchJobIds(storage, [11, 13], 2100)

  assert.deepEqual(readPublishBatchCheckpoint(storage, 2200).job_ids, [12])
  removePublishBatchJobIds(storage, [12], 2300)
  assert.equal(readPublishBatchCheckpoint(storage, 2400), null)
  assert.equal(storage.has(PUBLISH_BATCH_CHECKPOINT_KEY), false)
})


test('merge is idempotent and expired checkpoints fail closed', () => {
  const storage = memoryStorage()
  mergePublishBatchCheckpoint(storage, [21, 22], { batchId: 'batch-3', now: 3000 })
  mergePublishBatchCheckpoint(storage, [22, 23], { now: 3100 })

  assert.deepEqual(readPublishBatchCheckpoint(storage, 3200).job_ids, [21, 22, 23])
  assert.equal(
    readPublishBatchCheckpoint(storage, 3000 + PUBLISH_BATCH_MAX_AGE_MS + 1),
    null
  )
})
