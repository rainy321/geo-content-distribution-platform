import assert from 'node:assert/strict'
import fs from 'node:fs'
import test from 'node:test'
import * as Vue from 'vue'
import { renderToString } from '@vue/server-renderer'
import { parse, compileScript, compileTemplate, compileStyle } from '@vue/compiler-sfc'
import * as helpers from '../src/utils/geoIndexNow.js'
import { journeyDate } from '../src/utils/geoJourney.js'

const hash = 'a'.repeat(64), uuid = '12345678-1234-4123-8123-123456789abc'
const url = 'https://example.org/products/app/', keyLocation = 'https://example.org/abc123456789.txt'
const overview = (changes = {}) => ({ supported: true, cycle_id: 'cycle-a', project_id: 3, site_origin: 'https://example.org', page_scope: url, context_sha256: hash, previews: [], notifications: [], ...changes })
const cycle = (changes = {}) => ({ id: 'cycle-a', project_id: 3, revision: 2, profile: { name: '测试产品', entity_type: 'product', website: url }, indexnow: overview(), ...changes })
const preview = (changes = {}) => ({ id: 'preview-a', context_sha256: hash, key_location: keyLocation, urls: [url], pages: [{ url, sha256: 'b'.repeat(64), bytes: 300 }], changed_urls: [url], blocked_urls: [], ready: true, expires_at: new Date(Date.now() + 600000).toISOString(), retry_receipt_id: null, ...changes })
const receipt = (changes = {}) => ({ id: 'receipt-a', preview_id: 'preview-a', request_id: uuid, context_sha256: hash, urls: [url], status: 'received', http_status: 200, created_at: '2026-11-03T10:00:00Z', indexing_verified: false, geo_effect_verified: false, ...changes })
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no }); return { promise, resolve, reject } }
const makeStorage = () => { const values = new Map(); return { getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key) } }
const filename = new URL('../src/components/GeoIndexNow.vue', import.meta.url), source = fs.readFileSync(filename, 'utf8')
const { descriptor, errors } = parse(source), script = compileScript(descriptor, { id: 'indexnow-test' })
assert.deepEqual(errors, [])
const names = script.scriptSetupAst.filter(node => node.type === 'VariableDeclaration').flatMap(node => node.declarations).filter(node => node.id.type === 'Identifier').map(node => node.id.name)
const body = descriptor.scriptSetup.content.replace(/^import .*$/gm, '')
function harness(changes = {}, overrides = {}, store = makeStorage()) {
  const props = Vue.reactive({ cycle: cycle(changes), busy: false }), events = [], lifecycle = { mounted: [], unmounted: [] }, calls = []
  let server = props.cycle.indexnow
  const api = {
    get: async id => { calls.push(['get', id]); return { code: 200, data: server } },
    preview: async (id, payload) => { calls.push(['preview', id, payload]); return { code: 200, data: { overview: server, preview: preview() } } },
    submit: async (id, payload) => { calls.push(['submit', id, payload]); server = overview({ notifications: [receipt({ request_id: payload.request_id })] }); return { code: 200, data: { overview: server, receipt: server.notifications[0] } } }, ...overrides
  }
  const effect = Vue.effectScope(), deps = { ...helpers, computed: Vue.computed, ref: Vue.ref, watch: Vue.watch, onMounted: fn => lifecycle.mounted.push(fn), onUnmounted: fn => lifecycle.unmounted.push(fn), defineProps: () => props, defineEmits: () => (...args) => events.push(args), geoIndexNowApi: api, journeyDate, globalThis: { localStorage: store, crypto: { randomUUID: () => uuid } } }
  const instance = effect.run(() => new Function(...Object.keys(deps), body + `;return {${names.join(',')}}`)(...Object.values(deps)))
  return { ...helpers, ...instance, props, events, calls, lifecycle, store, setServer: value => { server = value }, stop: () => { lifecycle.unmounted.forEach(fn => fn()); effect.stop() } }
}
async function prepare(page) { page.keyLocation.value = keyLocation; page.urlText.value = url; await page.preflight(); page.updatedConfirmed.value = true }

test('scope and URL selection exclude another cycle, project, origin, sibling product and ambiguous targets', () => {
  const view = helpers.indexNowOverview(cycle(), overview())
  assert.equal(view.supported, true)
  for (const changes of [{ cycle_id: 'other' }, { project_id: 4 }, { site_origin: 'https://evil.test' }, { page_scope: 'https://example.org/' }, { context_sha256: 'bad' }]) assert.equal(helpers.indexNowOverview(cycle(), overview(changes)).supported, false)
  assert.deepEqual(helpers.indexNowUrls(url + '\n' + url + 'docs/', view), [url, url + 'docs/'])
  for (const value of ['https://example.org/products/appx/', 'https://example.org/', 'https://evil.test/products/app/', `${url}?key=secret`, `${url}#part`, `https://user:pass@example.org/products/app/`, 'http:// https://example.org/', `${url}\n${url}`, [url, url, url, url]]) assert.throws(() => helpers.indexNowUrls(value, view))
  assert.throws(() => helpers.indexNowPreviewPayload(view, 'https://evil.test/key.txt', url))
})

test('actual backend offline contract accepts reverse-order input and matches its sorted two-page receipt', () => {
  // Generated through geo_indexnow_service preview/submit, with HTTP and DNS
  // mocked. Keep real field shapes and the service's canonical batch order.
  const fixture = JSON.parse(fs.readFileSync(new URL('./fixtures/geoIndexNow.contract.json', import.meta.url), 'utf8'))
  assert.equal(fixture.synthetic_only, true)
  const current = { id: fixture.overview_before.cycle_id, project_id: fixture.overview_before.project_id,
    profile: { name: 'Synthetic contract fixture', website: fixture.overview_before.page_scope } }
  const view = helpers.indexNowOverview(current, fixture.overview_before)
  assert.equal(view.supported, true)
  const input = helpers.indexNowPreviewPayload(view, fixture.preview_input.key_location, fixture.preview_input.urls.join('\n'))
  assert.notDeepEqual(input.urls, fixture.preview_input.urls)
  assert.deepEqual(input.urls, [...fixture.preview_input.urls].sort())
  const checked = helpers.indexNowPreview(fixture.preview_result.preview, view, input)
  const frozen = helpers.indexNowSubmission(view, checked, fixture.submit_input.request_id, Date.parse(checked.created_at))
  assert.deepEqual(frozen.payload, fixture.submit_input)
  assert.equal(helpers.indexNowReceiptMatches(fixture.submit_result.receipt, frozen), true)
  assert.deepEqual(frozen.urls, input.urls)
})

test('one explicit preflight reads pages but never submits a notification or claims indexing', async () => {
  const page = harness()
  assert.deepEqual(page.calls, [])
  await prepare(page)
  assert.equal(page.calls.filter(row => row[0] === 'preview').length, 1)
  assert.equal(page.calls.filter(row => row[0] === 'submit').length, 0)
  assert.deepEqual(page.calls[0][2], { action: 'preview', context_sha256: hash, key_location: keyLocation, urls: [url] })
  assert.equal(page.preview.value.pages[0].sha256, 'b'.repeat(64)); page.stop()
})

test('submit requires explicit updated-page confirmation and rapid clicks send one immutable UUID', async () => {
  const pending = deferred(), calls = [], page = harness({}, { submit: (...args) => { calls.push(args); return pending.promise } })
  await prepare(page); page.updatedConfirmed.value = false; await page.submit(); assert.equal(calls.length, 0)
  page.updatedConfirmed.value = true
  const first = page.submit(); await page.submit(); assert.equal(calls.length, 1)
  assert.deepEqual(calls[0][1], { action: 'submit', context_sha256: hash, preview_id: 'preview-a', request_id: uuid })
  assert.equal(page.pending.value, 'submit'); assert.equal(page.props.busy, false)
  pending.resolve({ code: 200, data: { overview: overview({ notifications: [receipt()] }), receipt: receipt() } }); await first
  assert.equal(page.frozen.value, null); assert.equal(page.notice.value, '200 · 更新通知已接收'); assert.equal(page.preview.value, null); page.stop()
})

test('lost acknowledgement persists UUID and allows only GET reconciliation, including after remount', async () => {
  let writes = 0
  const store = makeStorage(), page = harness({}, { submit: async () => { writes++; throw new Error('网络中断') } }, store)
  await prepare(page); await page.submit(); assert.equal(writes, 1); assert.equal(page.frozen.value.payload.request_id, uuid)
  await page.submit(); await page.preflight(); assert.equal(writes, 1)
  page.stop()
  const reopened = harness({}, {}, store); await reopened.refresh(); assert.equal(reopened.frozen.value.payload.request_id, uuid)
  assert.deepEqual(reopened.calls.map(row => row[0]), ['get'])
  reopened.setServer(overview({ notifications: [receipt({ request_id: 'another' })] })); await reopened.refresh(); assert.ok(reopened.frozen.value)
  reopened.setServer(overview({ notifications: [receipt({ status: 'delivery_unknown' })] })); await reopened.refresh(); assert.equal(reopened.frozen.value, null)
  assert.equal(reopened.view.value.notifications[0].status, 'delivery_unknown'); reopened.stop()
})

test('unknown, accepted and pending-key histories cannot be converted into a resubmission', async () => {
  for (const status of ['reserved', 'received', 'key_pending', 'delivery_unknown']) {
    const blocked = preview({ changed_urls: [], blocked_urls: [{ url, status, receipt_id: 'old', reason: '相同内容' }], ready: false })
    const page = harness({}, { preview: async () => ({ code: 200, data: { overview: overview(), preview: blocked } }) })
    await prepare(page); await page.submit(); assert.equal(page.calls.filter(row => row[0] === 'submit').length, 0)
    assert.equal(page.preview.value.ready, false); page.stop()
  }
})

test('an explicitly rejected prior receipt can be retried only after a fresh preflight and an explicit click', async () => {
  const priorId = '12345678-1234-4123-8123-123456789000'
  const page = harness({}, { preview: async () => ({ code: 200, data: { overview: overview({ notifications: [receipt({ status: 'rejected', request_id: priorId })] }), preview: preview({ retry_receipt_id: 'rejected-id' }) } }) })
  await prepare(page); assert.equal(page.calls.filter(row => row[0] === 'submit').length, 0)
  await page.submit(); assert.equal(page.calls.find(row => row[0] === 'submit')[2].retry_receipt_id, 'rejected-id')
  assert.notEqual(page.calls.find(row => row[0] === 'submit')[2].request_id, priorId); page.stop()
})

test('editing inputs invalidates a preflight immediately, and an old in-flight preflight cannot restore it', async () => {
  const pending = deferred(), page = harness({}, { preview: () => pending.promise })
  page.keyLocation.value = keyLocation; page.urlText.value = url
  const first = page.preflight(); page.urlText.value = url + 'docs/'
  pending.resolve({ code: 200, data: { overview: overview(), preview: preview() } }); await first
  assert.equal(page.preview.value, null); assert.match(page.error.value, /输入已变化/); page.stop()
  const ready = harness(); await prepare(ready); ready.keyLocation.value = 'https://example.org/changed.txt'
  assert.equal(ready.preview.value, null); assert.equal(ready.updatedConfirmed.value, false); ready.stop()
})

test('A to B to A and unmount discard stale page data and release the parent busy signal', async () => {
  const pending = deferred(), page = harness({}, { preview: () => pending.promise })
  page.keyLocation.value = keyLocation; page.urlText.value = url; const first = page.preflight()
  page.props.cycle = cycle({ id: 'cycle-b', indexnow: overview({ cycle_id: 'cycle-b' }) }); page.props.cycle = cycle()
  pending.resolve({ code: 200, data: { overview: overview(), preview: preview() } }); await first
  assert.equal(page.preview.value, null); assert.equal(page.urlText.value, ''); page.stop()
  const response = deferred(), gone = harness({}, { submit: () => response.promise }); await prepare(gone)
  const submit = gone.submit(); gone.stop(); response.resolve({ code: 200, data: { overview: overview(), receipt: receipt() } }); await submit
  assert.deepEqual(gone.events.at(-1), ['busy', false]); assert.equal(gone.notice.value, '')
})

test('expired, malformed or foreign preflight receipts never submit', () => {
  const view = overview()
  for (const value of [preview({ expires_at: 'invalid' }), preview({ expires_at: '2020-01-01T00:00:00Z' }), preview({ context_sha256: 'b'.repeat(64) }), preview({ ready: false })]) assert.throws(() => helpers.indexNowSubmission(view, value, uuid))
  for (const change of [{ urls: ['https://other.test/'] }, { pages: [{ url, sha256: 'bad', bytes: 4 }] }, { changed_urls: [url, url] }, { blocked_urls: null }]) assert.throws(() => helpers.indexNowPreview(preview(change), view, helpers.indexNowPreviewPayload(view, keyLocation, url)))
})

test('unavailable persistent storage blocks POST and explicit backend rejection clears only that pending intent', async () => {
  const blocked = harness({}, {}, { getItem: () => null, setItem: () => { throw new Error('不可写') } })
  await prepare(blocked); await blocked.submit(); assert.equal(blocked.calls.filter(row => row[0] === 'submit').length, 0); blocked.stop()
  const rejected = harness({}, { submit: async () => { throw { response: { status: 400, data: { msg: '预检过期，请重做' } } } } })
  await prepare(rejected); await rejected.submit(); assert.equal(rejected.frozen.value, null); assert.equal(rejected.preview.value, null); assert.equal(rejected.error.value, '预检过期，请重做'); rejected.stop()
})

test('panel compiles, escapes remote text and labels 200/202/unknown separately from indexing', async () => {
  const template = compileTemplate({ source: descriptor.template.content, filename: filename.pathname, id: 'indexnow', compilerOptions: { bindingMetadata: script.bindings } })
  assert.deepEqual(template.errors, []); assert.deepEqual(compileStyle({ source: descriptor.styles[0].content, id: 'indexnow' }).errors, [])
  const code = template.code.replace(/import \{([^}]+)\} from "vue"/g, (_all, imports) => `const {${imports.split(',').map(item => { const [name, alias] = item.trim().split(/\s+as\s+/); return alias ? `${name}:${alias}` : name }).join(',')}}=Vue;`).replace('export function render', 'return function render')
  const compiled = new Function('Vue', code)(Vue), page = harness({ indexnow: overview({ notifications: [receipt({ urls: ['<script>unsafe</script>'] }), receipt({ id: 'other', status: 'key_pending', http_status: 202 })] }) })
  const app = Vue.createSSRApp({ props: ['cycle', 'busy'], setup: () => ({ ...helpers, ...page }), render: compiled }, page.props)
  app.component('el-button', { props: ['disabled', 'loading'], setup: (props, { slots }) => () => Vue.h('button', { disabled: props.disabled }, slots.default?.()) })
  const html = await renderToString(app)
  assert.match(html, /200 · 更新通知已接收/); assert.match(html, /202 · 密钥校验中/); assert.match(html, /不据此认定 GEO 优化成功/); assert.match(html, /&lt;script&gt;/)
  assert.ok(source.includes('prefers-reduced-motion')); assert.ok(!source.includes('v-html')); page.stop()
})

test('webmaster aggregates child busy but never reflects that same busy into the child external lock', () => {
  const parent = fs.readFileSync(new URL('../src/components/GeoWebmaster.vue', import.meta.url), 'utf8')
  assert.match(parent, /GeoIndexNow :cycle="cycle" :busy="busy \|\| localBusy \|\| loading"/)
  assert.match(parent, /watch\(\(\) => localBusy.value \|\| indexNowBusy.value/)
  assert.doesNotMatch(parent, /GeoIndexNow[^>]+:busy="locked/)
})
