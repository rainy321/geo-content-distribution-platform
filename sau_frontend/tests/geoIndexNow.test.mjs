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
function harness(changes = {}, overrides = {}, store = makeStorage(), environment = {}) {
  const props = Vue.reactive({ cycle: cycle(changes), busy: false }), events = [], lifecycle = { mounted: [], unmounted: [] }, calls = []
  let server = props.cycle.indexnow
  const api = {
    get: async id => { calls.push(['get', id]); return { code: 200, data: server } },
    preview: async (id, payload) => { calls.push(['preview', id, payload]); return { code: 200, data: { overview: server, preview: preview() } } },
    submit: async (id, payload) => { calls.push(['submit', id, payload]); server = overview({ notifications: [receipt({ request_id: payload.request_id })] }); return { code: 200, data: { overview: server, receipt: server.notifications[0] } } }, ...overrides
  }
  const effect = Vue.effectScope(), deps = { ...helpers, computed: Vue.computed, ref: Vue.ref, watch: Vue.watch, onMounted: fn => lifecycle.mounted.push(fn), onUnmounted: fn => lifecycle.unmounted.push(fn), defineProps: () => props, defineEmits: () => (...args) => events.push(args), geoIndexNowApi: api, journeyDate, globalThis: { localStorage: store, crypto: { randomUUID: () => uuid }, ...environment } }
  const instance = effect.run(() => new Function(...Object.keys(deps), body + `;return {${names.join(',')}}`)(...Object.values(deps)))
  return { ...helpers, ...instance, props, events, calls, lifecycle, store, setServer: value => { server = value }, stop: () => { lifecycle.unmounted.forEach(fn => fn()); effect.stop() } }
}
async function prepare(page) { page.keyLocation.value = keyLocation; page.urlText.value = url; await page.preflight(); page.updatedConfirmed.value = true }
async function renderPanel(page) {
  const template = compileTemplate({ source: descriptor.template.content, filename: filename.pathname, id: 'indexnow', compilerOptions: { bindingMetadata: script.bindings } })
  assert.deepEqual(template.errors, [])
  const code = template.code.replace(/import \{([^}]+)\} from "vue"/g, (_all, imports) => `const {${imports.split(',').map(item => { const [name, alias] = item.trim().split(/\s+as\s+/); return alias ? `${name}:${alias}` : name }).join(',')}}=Vue;`).replace('export function render', 'return function render')
  const compiled = new Function('Vue', code)(Vue), app = Vue.createSSRApp({ props: ['cycle', 'busy'], setup: () => ({ ...helpers, ...page }), render: compiled }, page.props)
  app.component('el-button', { props: ['disabled', 'loading'], setup: (props, { slots }) => () => Vue.h('button', { disabled: props.disabled }, slots.default?.()) })
  return renderToString(app)
}

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
  assert.match(parent, /GeoIndexNow :cycle="cycle" :busy="busy \|\| localBusy \|\| loading \|\| bingBusy"/)
  assert.match(parent, /watch\(\(\) => localBusy.value \|\| indexNowBusy.value \|\| bingBusy.value/)
  assert.doesNotMatch(parent, /GeoIndexNow[^>]+:busy="locked/)
})

const primaryAssetId = '1'.repeat(64), secondaryAssetId = '2'.repeat(64), secondaryHash = 'c'.repeat(64)
const secondaryOrigin = 'https://api.example.org', secondaryUrl = secondaryOrigin + '/'
const assets = () => [
  { id: primaryAssetId, url, site_origin: 'https://example.org', page_scope: url, primary: true, basis: 'frozen_website', control_status: 'not_checked' },
  { id: secondaryAssetId, url: secondaryUrl, site_origin: secondaryOrigin, page_scope: secondaryUrl, primary: false, basis: 'configured_official_url', control_status: 'not_checked' }
]
const assetCycle = () => ({ profile: { ...cycle().profile, official_urls: [secondaryOrigin] }, indexnow: assetOverview() })
const assetOverview = (assetId = '', changes = {}) => overview({ assets: assets(), selected_asset_id: assetId || primaryAssetId,
  ...(assetId ? { site_origin: secondaryOrigin, page_scope: secondaryUrl, context_sha256: secondaryHash } : {}), ...changes })
const secondaryPreview = (changes = {}) => preview({ context_sha256: secondaryHash, asset_id: secondaryAssetId, site_origin: secondaryOrigin, page_scope: secondaryUrl,
  key_location: secondaryOrigin + '/key-1234567890.txt', urls: [secondaryOrigin + '/docs'], changed_urls: [secondaryOrigin + '/docs'], pages: [{ url: secondaryOrigin + '/docs', sha256: 'd'.repeat(64), bytes: 350 }], ...changes })
const secondaryReceipt = (changes = {}) => receipt({ context_sha256: secondaryHash, asset_id: secondaryAssetId, site_origin: secondaryOrigin, page_scope: secondaryUrl,
  key_location: secondaryOrigin + '/key-1234567890.txt', urls: [secondaryOrigin + '/docs'], ...changes })

test('only frozen registered secondary addresses can become a selected notification scope', () => {
  const current = cycle(assetCycle()), source = assetOverview(secondaryAssetId)
  assert.equal(helpers.indexNowOverview(current, source, secondaryAssetId).supported, true)
  assert.equal(helpers.indexNowOverview(current, source).supported, false)
  assert.equal(helpers.indexNowOverview(current, assetOverview()).supported, true)
  const removed = cycle({ ...assetCycle(), profile: { ...current.profile, official_urls: [] } })
  const narrowed = cycle({ ...assetCycle(), profile: { ...current.profile, official_urls: [secondaryOrigin + '/docs/'] } })
  for (const other of [removed, narrowed]) assert.equal(helpers.indexNowOverview(other, source, secondaryAssetId).supported, false)
  for (const changes of [{ selected_asset_id: primaryAssetId }, { project_id: 4 }, { cycle_id: 'other' }, { page_scope: secondaryOrigin + '/elsewhere/' }, { site_origin: 'https://attacker.example.org' }, { assets: [assets()[0], { ...assets()[1], url: 'https://attacker.example.org/' }] }, { assets: undefined }]) {
    assert.equal(helpers.indexNowOverview(current, { ...source, ...changes }, secondaryAssetId).supported, false)
  }
  const view = helpers.indexNowOverview(current, source, secondaryAssetId)
  assert.deepEqual(helpers.indexNowUrls([secondaryOrigin + '/models', secondaryOrigin + '/docs'], view), [secondaryOrigin + '/docs', secondaryOrigin + '/models'])
  assert.throws(() => helpers.indexNowUrls(url, view))
  assert.throws(() => helpers.indexNowPreviewPayload(view, keyLocation, secondaryOrigin + '/docs'))
})

test('secondary preflight, frozen submission and receipt must all identify the same registered asset', () => {
  const view = helpers.indexNowOverview(cycle(assetCycle()), assetOverview(secondaryAssetId), secondaryAssetId)
  const input = helpers.indexNowPreviewPayload(view, secondaryPreview().key_location, secondaryOrigin + '/docs')
  const checked = helpers.indexNowPreview(secondaryPreview(), view, input)
  const captured = helpers.indexNowSubmission(view, checked, uuid)
  assert.equal(captured.asset_id, secondaryAssetId)
  assert.equal(captured.site_origin, secondaryOrigin)
  assert.equal(helpers.indexNowReceiptMatches(secondaryReceipt(), captured), true)
  for (const changes of [{ asset_id: primaryAssetId }, { site_origin: 'https://example.org' }, { page_scope: secondaryOrigin + '/other/' }, { asset_id: undefined }]) {
    assert.throws(() => helpers.indexNowPreview(secondaryPreview(changes), view, input))
    assert.throws(() => helpers.indexNowSubmission(view, secondaryPreview(changes), uuid))
    assert.equal(helpers.indexNowReceiptMatches(secondaryReceipt(changes), captured), false)
  }
  const storage = makeStorage(); storage.setItem(helpers.indexNowStorageKey(view), JSON.stringify(captured))
  assert.equal(helpers.indexNowRestore(storage, view).asset_id, secondaryAssetId)
  assert.equal(helpers.indexNowRestore(storage, helpers.indexNowOverview(cycle(assetCycle()), assetOverview())), null)
})

test('explicit asset switching clears old inputs, generated file, confirmation and preflight before using the new endpoint', async () => {
  const reads = [], page = harness(assetCycle(), { get: async (id, assetId) => { reads.push([id, assetId]); return { code: 200, data: assetOverview(assetId) } } })
  page.generateKey(); assert.ok(page.generatedKey.value)
  await prepare(page); assert.ok(page.preview.value); assert.equal(page.updatedConfirmed.value, true)
  await page.selectAsset(secondaryAssetId)
  assert.deepEqual(reads, [['cycle-a', secondaryAssetId]])
  assert.equal(page.view.value.supported, true); assert.equal(page.view.value.site_origin, secondaryOrigin)
  assert.equal(page.preview.value, null); assert.equal(page.generatedKey.value, null); assert.equal(page.updatedConfirmed.value, false)
  assert.equal(page.keyLocation.value, ''); assert.equal(page.urlText.value, '')
  assert.equal(page.calls.filter(row => row[0] === 'submit').length, 0)
  await page.selectAsset('foreign-asset')
  assert.equal(page.selectedAssetId.value, secondaryAssetId); assert.equal(reads.length, 1)
  assert.match(page.error.value, /不属于本轮/); page.stop()
})

test('remount restores the pending secondary host for read-only reconciliation and cannot switch it while uncertain', async () => {
  const storage = makeStorage(), current = cycle(assetCycle()), source = assetOverview(secondaryAssetId)
  const view = helpers.indexNowOverview(current, source, secondaryAssetId)
  const frozen = helpers.indexNowSubmission(view, secondaryPreview(), uuid)
  helpers.indexNowSaveAssetSelection(storage, current, assets()[1])
  storage.setItem(helpers.indexNowStorageKey(view), JSON.stringify(frozen))
  const reads = []; let record = source
  const page = harness(assetCycle(), { get: async (id, assetId) => { reads.push([id, assetId]); return { code: 200, data: record } } }, storage)
  assert.equal(page.selectedAssetId.value, secondaryAssetId)
  await page.refresh(); assert.equal(page.frozen.value.payload.request_id, uuid)
  await page.selectAsset(''); assert.equal(page.selectedAssetId.value, secondaryAssetId); assert.equal(reads.length, 1)
  await page.submit(); assert.equal(page.calls.filter(row => row[0] === 'submit').length, 0)
  record = assetOverview(secondaryAssetId, { notifications: [secondaryReceipt()] })
  await page.refresh(); assert.equal(page.frozen.value, null)
  record = assetOverview(); await page.selectAsset('')
  assert.equal(page.selectedAssetId.value, ''); assert.equal(page.view.value.supported, true)
  assert.equal(helpers.indexNowReadAssetSelection(storage, current), '')
  assert.equal(helpers.indexNowReadAssetSelection(storage, { ...current, id: 'other' }), '')
  page.stop()
})

test('new asset metadata still restores a legacy primary pending UUID and reconciles through the original GET endpoint', async () => {
  const storage = makeStorage(), current = cycle(assetCycle())
  const legacyView = helpers.indexNowOverview(current, overview())
  const legacyIntent = helpers.indexNowSubmission(legacyView, preview(), uuid)
  assert.equal(legacyIntent.asset_id, undefined)
  storage.setItem(helpers.indexNowStorageKey(legacyView), JSON.stringify(legacyIntent))
  let source = assetOverview(); const reads = []
  const page = harness(assetCycle(), { get: async (...args) => { reads.push(args); return { code: 200, data: source } } }, storage)
  await page.refresh()
  assert.equal(page.view.value.supported, true); assert.equal(page.selectedAssetId.value, '')
  assert.equal(page.frozen.value.payload.request_id, uuid)
  assert.deepEqual(reads, [['cycle-a']])
  await page.selectAsset(secondaryAssetId); await page.submit()
  assert.equal(page.selectedAssetId.value, ''); assert.equal(page.calls.filter(row => row[0] === 'submit').length, 0)
  source = assetOverview('', { notifications: [receipt({ asset_id: primaryAssetId, site_origin: 'https://example.org', page_scope: url })] })
  await page.refresh()
  assert.equal(page.frozen.value, null); assert.equal(storage.getItem(helpers.indexNowStorageKey(legacyView)), null)
  assert.deepEqual(reads, [['cycle-a'], ['cycle-a']]); page.stop()
})

test('a removed secondary address still offers explicit return to the primary site while retaining its old unknown intent', async () => {
  const storage = makeStorage(), current = cycle(assetCycle())
  const view = helpers.indexNowOverview(current, assetOverview(secondaryAssetId), secondaryAssetId)
  const captured = helpers.indexNowSubmission(view, secondaryPreview(), uuid)
  helpers.indexNowSaveAssetSelection(storage, current, assets()[1])
  const oldKey = helpers.indexNowStorageKey(view), original = JSON.stringify(captured)
  storage.setItem(oldKey, original)
  const reads = [], primaryOnly = { assets: [assets()[0]] }
  const page = harness(assetCycle(), { get: async (id, assetId) => {
    reads.push([id, assetId])
    return { code: 200, data: assetId ? assetOverview(secondaryAssetId, { ...primaryOnly, supported: false, selected_asset_id: null, reason: '该地址已从当前资料移除' }) : assetOverview('', primaryOnly) }
  } }, storage)
  await page.refresh()
  assert.equal(page.view.value.supported, false); assert.equal(page.assets.value.length, 1)
  assert.equal(page.selectedAssetAvailable.value, false)
  const html = await renderPanel(page)
  assert.match(html, /data-testid="indexnow-asset"/); assert.match(html, /原地址已不在当前范围/); assert.match(html, /主官网/)
  await page.selectAsset('')
  assert.equal(page.selectedAssetId.value, ''); assert.equal(page.view.value.supported, true)
  assert.deepEqual(reads, [['cycle-a', secondaryAssetId], ['cycle-a', undefined]])
  assert.equal(storage.getItem(oldKey), original)
  assert.equal(page.calls.filter(row => row[0] === 'submit').length, 0); page.stop()
})

test('generation and download produce only the exact public verification text and never make a POST', async () => {
  let blob, clicked = 0, revoked = 0, appended = 0, bytesRequested = 0
  const link = { click() { clicked++ }, remove() {} }, environment = { Blob, URL: { createObjectURL(value) { blob = value; return 'blob:synthetic' }, revokeObjectURL() { revoked++ } },
    document: { createElement: () => link, body: { appendChild() { appended++ } } }, setTimeout: fn => fn(),
    crypto: { randomUUID: () => uuid, getRandomValues(value) { bytesRequested += value.length; return value.fill(0xab) } } }
  const page = harness(assetCycle(), {}, makeStorage(), environment)
  page.generateKey()
  const file = page.generatedKey.value
  assert.equal(bytesRequested, 16); assert.equal(file.content, 'ab'.repeat(16)); assert.equal(file.filename, 'ab'.repeat(16) + '.txt')
  assert.equal(page.keyLocation.value, url + file.filename)
  assert.match(file.instructions, /不是模型 API 密钥/)
  assert.equal(page.preview.value, null); assert.equal(page.view.value.assets[0].control_status, 'not_checked')
  page.downloadKey()
  assert.equal(link.download, file.filename); assert.equal(clicked, 1); assert.equal(appended, 1); assert.equal(revoked, 1)
  assert.equal(blob.type, 'text/plain;charset=utf-8')
  assert.deepEqual(Buffer.from(await blob.arrayBuffer()), Buffer.from(file.content, 'utf8'))
  assert.equal((await blob.arrayBuffer()).byteLength, 32)
  assert.equal(page.calls.length, 0)
  assert.throws(() => helpers.downloadIndexNowKeyFile({ ...file, content: file.content + '\n' }, environment))
  assert.throws(() => helpers.indexNowKeyFile({ ...page.view.value, supported: false }, environment.crypto))
  assert.throws(() => helpers.indexNowKeyFile(page.view.value, {})); page.stop()
})

test('a generated file always belongs to the selected host and is discarded when the project changes', () => {
  const current = cycle(assetCycle()), view = helpers.indexNowOverview(current, assetOverview(secondaryAssetId), secondaryAssetId)
  const file = helpers.indexNowKeyFile(view, { randomUUID: () => uuid })
  assert.equal(file.key_location, secondaryOrigin + '/' + uuid.replaceAll('-', '') + '.txt')
  assert.equal(helpers.indexNowReceiptOrigin({ key_location: file.key_location }), secondaryOrigin)
  assert.equal(helpers.indexNowReceiptOrigin({ key_location: 'https://person:secret@example.org/key.txt' }), '主机未知，请查看原记录')
  const page = harness(assetCycle()); page.generateKey(); assert.ok(page.generatedKey.value)
  page.props.cycle = cycle({ id: 'other', indexnow: overview({ cycle_id: 'other' }) })
  assert.equal(page.generatedKey.value, null); assert.equal(page.keyLocation.value, ''); page.stop()
})

test('actual transport retains the primary endpoint and sends a secondary selection only through the asset path', () => {
  const apiSource = fs.readFileSync(new URL('../src/api/geoIndexNow.js', import.meta.url), 'utf8').replace(/^import .*$/gm, '').replace('export const geoIndexNowApi', 'const geoIndexNowApi')
  const calls = [], http = { get: (...args) => calls.push(['get', ...args]), post: (...args) => calls.push(['post', ...args]) }
  const api = new Function('http', apiSource + ';return geoIndexNowApi')(http), payload = { action: 'submit', context_sha256: secondaryHash }
  api.get('cycle-a'); api.preview('cycle-a', payload); api.submit('cycle-a', payload, secondaryAssetId)
  assert.equal(calls[0][1], '/api/geo-content/cycles/cycle-a/indexnow')
  assert.equal(calls[1][1], '/api/geo-content/cycles/cycle-a/indexnow')
  assert.equal(calls[2][1], '/api/geo-content/cycles/cycle-a/indexnow/assets/' + secondaryAssetId)
  assert.equal(calls[2][2], payload)
  assert.equal(calls[2][3].timeout, 65000)
})

test('actual synthetic secondary backend contract keeps the selected asset, sorted URLs, payload and receipt aligned', () => {
  const fixture = JSON.parse(fs.readFileSync(new URL('./fixtures/geoIndexNow.assets.contract.json', import.meta.url), 'utf8'))
  assert.equal(fixture.synthetic_only, true)
  const current = { id: fixture.overview_before.cycle_id, project_id: fixture.overview_before.project_id, profile: fixture.profile }
  const secondary = fixture.overview_before.assets.find(asset => !asset.primary)
  assert.ok(secondary)
  const view = helpers.indexNowOverview(current, fixture.overview_before, secondary.id)
  assert.equal(view.supported, true)
  assert.equal(helpers.indexNowOverview(current, fixture.primary_overview).supported, true)
  const input = helpers.indexNowPreviewPayload(view, fixture.preview_input.key_location, fixture.preview_input.urls.join('\n'))
  assert.deepEqual(input.urls, [...fixture.preview_input.urls].sort())
  const checked = helpers.indexNowPreview(fixture.preview_result.preview, view, input)
  const frozen = helpers.indexNowSubmission(view, checked, fixture.submit_input.request_id, Date.parse(checked.created_at))
  assert.deepEqual(frozen.payload, fixture.submit_input)
  assert.equal(frozen.asset_id, secondary.id)
  assert.equal(helpers.indexNowReceiptMatches(fixture.submit_result.receipt, frozen), true)
  assert.equal(fixture.submit_result.receipt.indexing_verified, false)
  assert.equal(fixture.submit_result.receipt.geo_effect_verified, false)
  const apiSource = fs.readFileSync(new URL('../src/api/geoIndexNow.js', import.meta.url), 'utf8').replace(/^import .*$/gm, '').replace('export const geoIndexNowApi', 'const geoIndexNowApi')
  const calls = [], api = new Function('http', apiSource + ';return geoIndexNowApi')({ post: (...args) => calls.push(args) })
  api.preview(current.id, input, secondary.id); api.submit(current.id, frozen.payload, frozen.asset_id)
  assert.ok(calls.every(row => row[0] === `/api/geo-content/cycles/${current.id}/indexnow/assets/${secondary.id}`))
  assert.deepEqual(calls[1][1], fixture.submit_input)
})

test('generated public verification draft survives remount and refresh without starting a preflight or notification', async () => {
  const storage = makeStorage(), page = harness(assetCycle(), {}, storage)
  page.generateKey()
  const content = page.generatedKey.value.content, location = page.keyLocation.value
  const raw = storage.getItem(helpers.indexNowKeyDraftStorageKey(page.view.value)), record = JSON.parse(raw)
  assert.deepEqual(Object.keys(record).sort(), ['cycle_id', 'project_id', 'context_sha256', 'asset_id', 'site_origin', 'page_scope', 'content', 'filename', 'key_location'].sort())
  assert.equal(record.content, content); assert.equal(record.key_location, location)
  assert.ok(!raw.includes('profile')); page.stop()
  const reopened = harness(assetCycle(), {}, storage)
  assert.equal(reopened.generatedKey.value.content, content); assert.equal(reopened.keyLocation.value, location)
  assert.match(reopened.notice.value, /已恢复本机/)
  reopened.keyLocation.value = 'https://example.org/an-existing-public-key.txt'
  await reopened.refresh()
  assert.equal(reopened.generatedKey.value.content, content)
  assert.equal(reopened.keyLocation.value, 'https://example.org/an-existing-public-key.txt')
  assert.equal(storage.getItem(helpers.indexNowKeyDraftStorageKey(reopened.view.value)), raw)
  assert.deepEqual(reopened.calls.map(row => row[0]), ['get']); assert.equal(reopened.preview.value, null); reopened.stop()
})

test('separate primary and secondary generated drafts restore only their own current scope', async () => {
  const storage = makeStorage(); let nonce = 0
  const page = harness(assetCycle(), { get: async (_id, assetId) => ({ code: 200, data: assetOverview(assetId) }) }, storage,
    { crypto: { getRandomValues: value => value.fill(++nonce) } })
  page.generateKey(); const first = page.generatedKey.value.content, firstLocation = page.keyLocation.value
  await page.selectAsset(secondaryAssetId); assert.equal(page.generatedKey.value, null)
  page.generateKey(); const second = page.generatedKey.value.content
  assert.notEqual(second, first); assert.ok(page.keyLocation.value.startsWith(secondaryOrigin + '/'))
  await page.selectAsset('')
  assert.equal(page.generatedKey.value.content, first); assert.equal(page.keyLocation.value, firstLocation)
  await page.selectAsset(secondaryAssetId)
  assert.equal(page.generatedKey.value.content, second)
  const narrowed = { ...page.view.value, supported: false }
  assert.equal(helpers.readValidatedKeyDraft(storage, narrowed), null)
  assert.equal(page.calls.filter(row => row[0] === 'submit').length, 0); page.stop()
})

test('draft binding rejects copied records from other projects, cycles, assets, contexts, hosts or paths', () => {
  const storage = makeStorage(), view = helpers.indexNowOverview(cycle(assetCycle()), assetOverview())
  const file = helpers.indexNowKeyFile(view, { randomUUID: () => uuid })
  helpers.saveValidatedKeyDraft(storage, view, { ...file, internal_cost: 'NEVER-PERSIST-PRIVATE-SENTINEL' })
  const original = storage.getItem(helpers.indexNowKeyDraftStorageKey(view))
  assert.ok(!original.includes('NEVER-PERSIST-PRIVATE-SENTINEL'))
  for (const changes of [{ cycle_id: 'other-cycle' }, { project_id: 5 }, { context_sha256: 'f'.repeat(64) }, { asset_id: secondaryAssetId },
    { site_origin: secondaryOrigin, page_scope: secondaryUrl }, { page_scope: 'https://example.org/another-product/' }]) {
    const other = { ...view, ...changes }, key = helpers.indexNowKeyDraftStorageKey(other)
    assert.ok(key)
    storage.setItem(key, original)
    assert.equal(helpers.readValidatedKeyDraft(storage, other), null)
  }
})

test('oversized or corrupt verification drafts are ignored without touching pending notification records', () => {
  const storage = makeStorage(), view = helpers.indexNowOverview(cycle(), overview()), file = helpers.indexNowKeyFile(view, { randomUUID: () => uuid })
  helpers.saveValidatedKeyDraft(storage, view, file)
  const key = helpers.indexNowKeyDraftStorageKey(view), valid = JSON.parse(storage.getItem(key))
  const pendingKey = helpers.indexNowStorageKey(view); storage.setItem(pendingKey, 'original-notification-UUID')
  for (const raw of ['{broken', 'x'.repeat(8193), JSON.stringify([]), JSON.stringify({ ...valid, api_key: 'DO-NOT-LOAD' }),
    JSON.stringify({ ...valid, content: valid.content + '\n' }), JSON.stringify({ ...valid, filename: '../unsafe.txt' }),
    JSON.stringify({ ...valid, key_location: secondaryOrigin + '/' + file.filename }), JSON.stringify({ ...valid, key_location: file.key_location + '?key=secret' })]) {
    storage.setItem(key, raw)
    assert.equal(helpers.readValidatedKeyDraft(storage, view), null)
    assert.equal(storage.getItem(pendingKey), 'original-notification-UUID')
  }
})

test('storage or readback failure keeps an existing generated file and URL available without claiming the replacement saved', async () => {
  const backing = makeStorage(); let fail = false, nonce = 0
  const storage = { getItem: key => backing.getItem(key), setItem: (key, value) => { if (fail) throw new Error('本机存储不可写'); backing.setItem(key, value) }, removeItem: key => backing.removeItem(key) }
  const page = harness(assetCycle(), {}, storage, { crypto: { getRandomValues: value => value.fill(++nonce) } })
  page.generateKey(); const first = page.generatedKey.value, originalLocation = page.keyLocation.value
  fail = true; page.generateKey()
  assert.equal(page.generatedKey.value.content, first.content); assert.equal(page.keyLocation.value, originalLocation)
  assert.match(page.keyDraftWarning.value, /保存未确认/); assert.equal(page.notice.value, '')
  const html = await renderPanel(page)
  assert.match(html, /indexnow-key-draft-warning/); assert.match(html, /indexnow-download-key/)
  assert.equal(page.calls.length, 0)
  assert.throws(() => helpers.saveValidatedKeyDraft({ setItem() {}, getItem() { return null } }, page.view.value, first), /回读未成功/)
  page.stop()
})

test('a late GET cannot replace manual form edits with a stored generated draft', async () => {
  const storage = makeStorage(), view = helpers.indexNowOverview(cycle(assetCycle()), assetOverview())
  helpers.saveValidatedKeyDraft(storage, view, helpers.indexNowKeyFile(view, { randomUUID: () => uuid }))
  const response = deferred(), page = harness({ ...assetCycle(), indexnow: assetOverview('', { supported: false }) }, { get: () => response.promise }, storage)
  const loading = page.refresh()
  page.keyLocation.value = 'https://example.org/manual-public-file.txt'; page.urlText.value = url
  response.resolve({ code: 200, data: assetOverview() }); await loading
  assert.equal(page.keyLocation.value, 'https://example.org/manual-public-file.txt'); assert.equal(page.urlText.value, url)
  assert.equal(page.generatedKey.value, null); assert.equal(page.preview.value, null)
  await page.refresh(); assert.equal(page.keyLocation.value, 'https://example.org/manual-public-file.txt')
  assert.equal(page.calls.filter(row => row[0] === 'submit').length, 0); page.stop()
})
