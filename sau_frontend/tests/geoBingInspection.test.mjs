import assert from 'node:assert/strict'
import fs from 'node:fs'
import test from 'node:test'
import * as Vue from 'vue'
import { renderToString } from '@vue/server-renderer'
import { parse, compileScript, compileTemplate, compileStyle } from '@vue/compiler-sfc'
import * as bing from '../src/utils/geoBingInspection.js'
import * as webmaster from '../src/utils/geoWebmaster.js'
import { journeyDate } from '../src/utils/geoJourney.js'

const hash = 'a'.repeat(64), nextHash = 'b'.repeat(64), requestId = '12345678-1234-4123-8123-123456789abc'
const url = 'https://example.org/products/app/'
const cycle = (changes = {}) => ({ id: 'cycle-a', project_id: 5, profile: { name: '合成产品', entity_type: 'product', website: url }, ...changes })
const overview = (changes = {}) => ({ supported: true, site_origin: 'https://example.org', context_sha256: hash, target_urls: [url], record: { inspections: [{ url, status: 'unknown', note: '人工确认保留' }] }, native_inspections: [], ...changes })
const observation = (changes = {}) => ({ id: 'observation-a', cycle_id: 'cycle-a', project_id: 5, url, context_sha256: hash, request_id: requestId,
  status: 'observed', reason: 'observed', message: '已读取当前检查页，未点击 Inspect。', index_status: 'indexed', last_crawl_text: '20 Oct 2026 at 10:55', page_fetch_text: 'Successful', observed_at: '2026-11-03T00:00:00Z',
  platform_timezone: 'unknown', timezone: 'unknown', page_freshness: 'unknown', source: 'native_browser_ui', independent: false, content_version_verified: false, geo_effect_verified: false, idempotent: false, ...changes })
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no }); return { promise, resolve, reject } }
const flush = async () => { for (let index = 0; index < 12; index++) await Promise.resolve(); await Vue.nextTick() }
const memory = () => { const map = new Map(); return { getItem: key => map.get(key) ?? null, setItem: (key, value) => map.set(key, value), removeItem: key => map.delete(key) } }
const filename = new URL('../src/components/GeoBingInspection.vue', import.meta.url), source = fs.readFileSync(filename, 'utf8')
const { descriptor, errors } = parse(source), compiled = compileScript(descriptor, { id: 'bing-window-test' })
assert.deepEqual(errors, [])
const names = compiled.scriptSetupAst.filter(row => row.type === 'VariableDeclaration').flatMap(row => row.declarations).filter(row => row.id.type === 'Identifier').map(row => row.id.name)
const body = descriptor.scriptSetup.content.replace(/^import .*$/gm, '')

function harness(options = {}) {
  const current = options.cycle || cycle(), initial = options.overview || overview()
  const props = Vue.reactive({ cycle: current, view: webmaster.webmasterOverview(current, initial), url, busy: false })
  const events = [], calls = [], nativeCalls = [], mounted = [], unmounted = [], listeners = new Map(), timers = new Map()
  const store = options.store || memory(); let timerId = 0, server = initial
  const fakeWindow = options.browser ? {} : { pywebview: { api: { read_bing_inspection: (...args) => {
    nativeCalls.push(args)
    return options.read ? options.read(...args) : Promise.resolve({ overview: overview({ native_inspections: [observation()] }), inspection: observation() })
  } } } }
  fakeWindow.addEventListener = (name, fn) => listeners.set(name, fn)
  fakeWindow.removeEventListener = name => listeners.delete(name)
  const effect = Vue.effectScope(), deps = { ...webmaster, ...bing, computed: Vue.computed, ref: Vue.ref, watch: Vue.watch,
    onMounted: fn => mounted.push(fn), onUnmounted: fn => unmounted.push(fn), defineProps: () => props,
    defineEmits: () => (name, value) => { events.push([name, value]); if (name === 'overview') props.view = webmaster.webmasterOverview(props.cycle, value) },
    geoWebmasterApi: { list: async id => { calls.push(id); return options.list ? options.list(id) : { data: server } } }, journeyDate,
    globalThis: { localStorage: store, crypto: { randomUUID: () => requestId } }, window: fakeWindow,
    setTimeout: (callback, ms) => { const id = ++timerId; timers.set(id, { callback, ms }); return id }, clearTimeout: id => timers.delete(id) }
  const instance = effect.run(() => new Function(...Object.keys(deps), body + `;return {${names.join(',')}}`)(...Object.values(deps)))
  mounted.forEach(fn => fn())
  return { ...webmaster, ...bing, ...instance, props, events, calls, nativeCalls, timers, store, fakeWindow, listeners,
    setServer: value => { server = value }, stop: () => { unmounted.forEach(fn => fn()); effect.stop() } }
}

test('browser preview and older bridge never expose a fake read capability or invoke it on mount', async () => {
  const page = harness({ browser: true })
  assert.equal(page.available.value, false); assert.deepEqual(page.calls, []); assert.deepEqual(page.nativeCalls, [])
  await page.readWindow(); assert.deepEqual(page.calls, []); assert.equal(page.available.value, false)
  page.fakeWindow.pywebview = { api: { read_bing_inspection: () => { throw new Error('must not auto read') } } }
  page.listeners.get('pywebviewready')(); assert.equal(page.available.value, true); assert.deepEqual(page.nativeCalls, []); page.stop()
})

test('an explicit click refreshes context then awaits exactly one four-argument native request', async () => {
  const pending = deferred(), page = harness({ read: () => pending.promise })
  const manual = JSON.parse(JSON.stringify(page.props.view.record))
  const first = page.readWindow(); await page.readWindow(); await flush()
  assert.deepEqual(page.nativeCalls, [['cycle-a', url, hash, requestId]])
  assert.equal(page.pending.value, true); assert.equal(page.props.busy, false)
  pending.resolve({ overview: overview({ native_inspections: [observation()] }), inspection: observation() }); await first
  assert.equal(page.pending.value, false); assert.equal(page.frozen.value, null); assert.equal(page.latest.value.index_status, 'indexed')
  assert.deepEqual(page.props.view.record, manual); assert.equal(page.props.view.record.inspections[0].status, 'unknown'); page.stop()
})

test('stale preflight context prevents a browser read and preserves the refreshed data', async () => {
  const page = harness({ list: async () => ({ data: overview({ context_sha256: nextHash }) }) })
  await page.readWindow(); assert.deepEqual(page.nativeCalls, []); assert.equal(page.props.view.context_sha256, nextHash)
  assert.match(page.error.value, /站长进度已变化/); assert.equal(page.pending.value, false); page.stop()
})

test('an empty field reads the displayed current product address without overwriting a manual URL', () => {
  const page = harness(); page.props.url = ''
  assert.equal(page.target.value, url); assert.equal(page.props.url, '')
  page.props.url = url + 'docs/'
  assert.equal(page.target.value, url + 'docs/'); assert.equal(page.props.url, url + 'docs/')
  page.props.url = 'https://other.test/'
  assert.equal(page.target.value, ''); page.stop()
})

test('lost acknowledgement persists the original UUID; only GET can reconcile it across remount', async () => {
  const store = memory(), page = harness({ store, read: async () => { throw new Error('bridge disconnected') } })
  await page.readWindow(); assert.equal(page.frozen.value.request_id, requestId)
  await page.readWindow(); assert.equal(page.nativeCalls.length, 1); await page.refresh(); assert.ok(page.frozen.value)
  assert.equal(page.nativeCalls.length, 1); page.stop()
  const restored = harness({ store }); assert.equal(restored.frozen.value.request_id, requestId)
  restored.setServer(overview({ native_inspections: [observation({ status: 'reading', reason: 'reading' })] })); await restored.refresh(); assert.ok(restored.frozen.value)
  restored.setServer(overview({ native_inspections: [observation()] })); await restored.refresh(); assert.equal(restored.frozen.value, null)
  assert.equal(restored.nativeCalls.length, 0); restored.stop()
})

test('timeout releases busy but ignores a late promise; it does not automatically reread', async () => {
  const response = deferred(), page = harness({ read: () => response.promise }), running = page.readWindow()
  await flush(); const timeout = [...page.timers.values()].find(item => item.ms === 45000); assert.ok(timeout)
  timeout.callback(); await running
  assert.equal(page.pending.value, false); assert.ok(page.frozen.value); const updates = page.events.filter(row => row[0] === 'overview').length
  response.resolve({ overview: overview({ native_inspections: [observation()] }), inspection: observation() }); await flush()
  assert.equal(page.events.filter(row => row[0] === 'overview').length, updates); assert.ok(page.frozen.value)
  await page.readWindow(); assert.equal(page.nativeCalls.length, 1); page.stop()
})

test('changing URL, A to B to A, and unmount isolate pending reads from the new page', async () => {
  const response = deferred(), page = harness({ read: () => response.promise }), running = page.readWindow(); await flush()
  page.props.cycle = cycle({ id: 'cycle-b' }); page.props.view = webmaster.webmasterOverview(page.props.cycle, overview()); page.props.cycle = cycle(); await flush()
  response.resolve({ overview: overview({ native_inspections: [observation()] }), inspection: observation() }); await running
  assert.equal(page.rows.value.length, 0); assert.equal(page.pending.value, false); page.stop()
  const later = deferred(), gone = harness({ read: () => later.promise }), request = gone.readWindow(); await flush(); gone.stop()
  later.resolve({ overview: overview({ native_inspections: [observation()] }), inspection: observation() }); await request
  assert.deepEqual(gone.events.at(-1), ['busy', false]); assert.equal(gone.notice.value, '')
})

test('foreign receipts and unverified sources are rejected without copying a native result into user confirmation', async () => {
  for (const change of [{ request_id: '87654321-1234-4123-8123-123456789abc' }, { url: 'https://evil.test/' }, { cycle_id: 'other' }, { project_id: 9 }, { source: 'user_reported_platform_result' }, { independent: true }]) {
    const page = harness({ read: async () => ({ overview: overview(), inspection: observation(change) }) })
    await page.readWindow(); assert.ok(page.frozen.value); assert.equal(page.rows.value.length, 0); assert.equal(page.props.view.record.inspections[0].status, 'unknown'); page.stop()
  }
})

test('known action-required errors give a recorded message while unknown index status stays unknown', async () => {
  const row = observation({ status: 'need_action', reason: 'login_required', message: '请在已打开的官方 Bing 页面完成登录，再明确读取。', index_status: 'unknown', last_crawl_text: null })
  const page = harness({ read: async () => ({ overview: overview({ native_inspections: [row] }), inspection: row }) })
  await page.readWindow(); assert.equal(page.frozen.value, null); assert.equal(page.latest.value.index_status, 'unknown'); assert.match(page.latest.value.message, /完成登录/)
  assert.equal(bing.bingReadLabel(observation({ index_status: 'unknown' })), '索引状态未知'); assert.notEqual(bing.bingReadLabel(row), 'Bing 页面显示未索引'); page.stop()
})

test('a saved reading reservation blocks another native call, and external busy blocks both actions', async () => {
  const page = harness({ overview: overview({ native_inspections: [observation({ status: 'reading' })] }) })
  await page.readWindow(); assert.equal(page.nativeCalls.length, 0); page.props.busy = true; await page.refresh(); assert.equal(page.calls.length, 0); page.stop()
})

test('a reading reservation discovered by preflight GET blocks the new native read', async () => {
  const page = harness({ list: async () => ({ data: overview({ native_inspections: [observation({ status: 'reading' })] }) }) })
  await page.readWindow(); assert.equal(page.calls.length, 1); assert.equal(page.nativeCalls.length, 0)
  assert.match(page.error.value, /原读取结果尚未确认/); page.stop()
})

test('a success-shaped bridge reply without its saved history is unknown until GET confirms it', async () => {
  const page = harness({ read: async () => ({ overview: overview(), inspection: observation() }) })
  await page.readWindow(); assert.ok(page.frozen.value); assert.equal(page.rows.value.length, 0); assert.equal(page.notice.value, '')
  page.setServer(overview({ native_inspections: [observation()] })); await page.refresh()
  assert.equal(page.frozen.value, null); assert.equal(page.rows.value.length, 1); assert.equal(page.nativeCalls.length, 1); page.stop()
})

test('unavailable local storage stops before the native method; prior context GET remains read only', async () => {
  const page = harness({ store: { getItem: () => null, setItem: () => { throw new Error('storage unavailable') } } })
  await page.readWindow(); assert.equal(page.calls.length, 1); assert.equal(page.nativeCalls.length, 0); assert.equal(page.frozen.value, null); page.stop()
})

test('UI compiles and shows source/time/cache boundaries independently from user confirmation', async () => {
  const row = observation({ message: '<img src=x onerror=bad()> 已读取', index_status: 'unknown' })
  const page = harness({ overview: overview({ native_inspections: [row] }) })
  const template = compileTemplate({ source: descriptor.template.content, filename: filename.pathname, id: 'bing-window', compilerOptions: { bindingMetadata: compiled.bindings } })
  assert.deepEqual(template.errors, []); for (const style of descriptor.styles) assert.deepEqual(compileStyle({ source: style.content, id: 'bing-window' }).errors, [])
  const code = template.code.replace(/import \{([^}]+)\} from "vue"/g, (_all, imports) => `const {${imports.split(',').map(item => { const [name, alias] = item.trim().split(/\s+as\s+/); return alias ? `${name}:${alias}` : name }).join(',')}}=Vue;`).replace('export function render', 'return function render')
  const app = Vue.createSSRApp({ props: ['cycle', 'view', 'url', 'busy'], setup: () => ({ ...bing, ...page }), render: new Function('Vue', code)(Vue) }, page.props)
  app.component('el-button', { props: ['disabled', 'loading'], setup: (props, { slots }) => () => Vue.h('button', { disabled: props.disabled }, slots.default?.()) })
  const html = await renderToString(app)
  for (const phrase of ['索引状态未知', '20 Oct 2026 at 10:55', '采集时刻', '缓存版本', '平台时区', '与用户确认分开', '不计 GEO 效果', '&lt;img']) assert.ok(html.includes(phrase), phrase)
  assert.ok(!html.includes('<img')); assert.ok(!source.includes('v-html')); page.stop()
})
