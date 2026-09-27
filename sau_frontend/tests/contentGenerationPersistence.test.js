import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import {
  CONTENT_GENERATION_SESSION_KEY,
  clearPendingContentGeneration,
  readPendingContentGeneration,
  savePendingContentGeneration
} from '../src/utils/contentGenerationSession.js'

const memoryStorage = () => {
  const values = new Map()
  return {
    getItem: key => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, String(value)),
    removeItem: key => values.delete(key),
    raw: values
  }
}

test('pending generation session retains identity only and never credentials or prompt', () => {
  const storage = memoryStorage()
  const startedAt = Date.now() - 250
  assert.equal(savePendingContentGeneration({
    requestId: 'generation-request:1',
    startedAt,
    apiKey: 'must-not-persist',
    prompt: 'must-not-persist'
  }, storage), true)

  const serialized = storage.getItem(CONTENT_GENERATION_SESSION_KEY)
  assert.deepEqual(JSON.parse(serialized), {
    requestId: 'generation-request:1',
    startedAt
  })
  assert.equal(serialized.includes('must-not-persist'), false)
  assert.deepEqual(readPendingContentGeneration(storage), {
    requestId: 'generation-request:1',
    startedAt
  })
  clearPendingContentGeneration(storage)
  assert.equal(readPendingContentGeneration(storage), null)
})

test('invalid and expired pending identities are discarded', () => {
  const storage = memoryStorage()
  storage.setItem(CONTENT_GENERATION_SESSION_KEY, JSON.stringify({
    requestId: 'contains spaces',
    startedAt: Date.now()
  }))
  assert.equal(readPendingContentGeneration(storage), null)

  storage.setItem(CONTENT_GENERATION_SESSION_KEY, JSON.stringify({
    requestId: 'old-request',
    startedAt: 1
  }))
  assert.equal(readPendingContentGeneration(storage), null)
})

test('content creation stays alive across routes and refresh recovery is read-only', () => {
  const appSource = readFileSync(new URL('../src/App.vue', import.meta.url), 'utf8')
  const viewSource = readFileSync(
    new URL('../src/views/ContentCreation.vue', import.meta.url),
    'utf8'
  )
  const apiSource = readFileSync(new URL('../src/api/article.js', import.meta.url), 'utf8')

  assert.match(appSource, /<KeepAlive include="AiVisibility,ContentCreation,GeoAutomation">/)
  assert.match(viewSource, /defineOptions\(\{ name: 'ContentCreation' \}\)/)
  assert.match(viewSource, /savePendingContentGeneration\(\{[\s\S]*?requestId:[\s\S]*?startedAt:/)
  assert.match(viewSource, /getContentGenerationRunByRequestId\(/)
  assert.match(viewSource, /页面只会按原请求 ID 读取持久化结果/)
  assert.doesNotMatch(viewSource, /savePendingContentGeneration\([\s\S]{0,500}(?:apiKey|api_key)/)
  assert.match(apiSource, /http\.get\([\s\S]*content-generation\/runs\/by-request/)
})
