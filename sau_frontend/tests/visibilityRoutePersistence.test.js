import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const appSource = readFileSync(new URL('../src/App.vue', import.meta.url), 'utf8')
const visibilitySource = readFileSync(new URL('../src/views/AiVisibility.vue', import.meta.url), 'utf8')

test('AI visibility keeps its in-memory sampling session across route switches', () => {
  assert.match(
    appSource,
    /<router-view\s+v-slot="\{ Component \}">\s*<KeepAlive\s+include="AiVisibility">\s*<component\s+:is="Component"\s*\/>\s*<\/KeepAlive>\s*<\/router-view>/s
  )
  assert.match(visibilitySource, /defineOptions\(\{ name: 'AiVisibility' \}\)/)
})

test('AI visibility sampling credentials stay memory-only and are cleared on final unmount', () => {
  assert.doesNotMatch(visibilitySource, /(?:localStorage|sessionStorage)/)
  assert.match(
    visibilitySource,
    /onBeforeUnmount\(\(\) => \{\s*sampling\.apiKey = ''/s
  )
})
