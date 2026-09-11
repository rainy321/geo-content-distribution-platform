import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const source = readFileSync(
  new URL('../src/views/DistributionCenter.vue', import.meta.url),
  'utf8'
)

test('publish upload inputs stay inside the inner page scroll boundary', () => {
  assert.match(source, /\.asset-panel\s*\{[^}]*position:\s*relative;/)
  assert.match(source, /\.visually-hidden\s*\{[^}]*position:\s*absolute;/)
  assert.match(source, /class="visually-hidden"[\s\S]*?type="file"/)
})
