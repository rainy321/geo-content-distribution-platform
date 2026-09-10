import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const appSource = readFileSync(new URL('../src/App.vue', import.meta.url), 'utf8')
const visibilitySource = readFileSync(new URL('../src/views/AiVisibility.vue', import.meta.url), 'utf8')

test('AI visibility keeps its in-memory sampling session across route switches', () => {
  assert.match(
    appSource,
    /<router-view\s+v-slot="\{ Component \}">\s*<KeepAlive\s+include="AiVisibility,ContentCreation">\s*<component\s+:is="Component"\s*\/>\s*<\/KeepAlive>\s*<\/router-view>/s
  )
  assert.match(visibilitySource, /defineOptions\(\{ name: 'AiVisibility' \}\)/)
})

test('AI visibility sampling resumes by durable request identity without storing credentials', () => {
  assert.match(visibilitySource, /geo\.ai-visibility\.pending-sample\.v1/)
  assert.match(visibilitySource, /getVisibilitySample\(pending\.requestId/)
  assert.match(visibilitySource, /切换页面不会重新调用模型/)
  assert.doesNotMatch(
    visibilitySource,
    /sessionStorage\?\.setItem\([\s\S]{0,600}(?:apiKey|api_key)/
  )
  assert.match(
    visibilitySource,
    /onBeforeUnmount\(\(\) => \{\s*sampling\.apiKey = ''/s
  )
})
