import assert from 'node:assert/strict'
import fs from 'node:fs'
import test from 'node:test'
import * as Vue from 'vue'
import { renderToString } from '@vue/server-renderer'
import { parse, compileScript, compileTemplate, compileStyle } from '@vue/compiler-sfc'
import * as helpers from '../src/utils/geoWebmaster.js'
import { journeyDate } from '../src/utils/geoJourney.js'

const hash = 'a'.repeat(64), otherHash = 'b'.repeat(64)
const raw = new TextEncoder().encode('<?xml version="1.0"?>\r\n<users>\n\t<user>0123456789ABCDEF0123456789ABCDEF</user>\n</users>\r\n')
const encoded = Buffer.from(raw).toString('base64')
const file = (bytes = raw, changes = {}) => ({ name: 'BingSiteAuth.xml', size: bytes.length, arrayBuffer: async () => bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength), ...changes })
const record = (changes = {}) => ({ verification_file: null, public_check: { status: 'not_checked', checked_at: null }, sitemap_check: { status: 'not_checked', entries: null }, ownership: { status: 'not_confirmed', evidence_source: 'user_reported_platform_result', observed_at: null }, sitemap_submission: { status: 'not_reported', evidence_source: 'user_reported_platform_result', observed_at: null }, inspections: [], ...changes })
const overview = (changes = {}) => ({ supported: true, reason: '', site_origin: 'https://example.org', site_key: hash, context_sha256: hash, entry_url: helpers.bingWebmasterEntry, verification_url: 'https://example.org/BingSiteAuth.xml', sitemap_url: 'https://example.org/sitemap.xml', target_urls: ['https://example.org/', 'https://example.org/about/'], record: null, ...changes })
const cycle = (changes = {}) => ({ id: 'cycle-a', baseline: { profile: { name: '示例公司', entity_type: 'company', website: 'https://example.org/' } }, webmaster: overview(), ...changes })
const filename = new URL('../src/components/GeoWebmaster.vue', import.meta.url)
const source = fs.readFileSync(filename, 'utf8'), { descriptor, errors } = parse(source, { filename: filename.pathname })
assert.deepEqual(errors, [])
const script = compileScript(descriptor, { id: 'webmaster-test' })
const names = script.scriptSetupAst.flatMap(node => node.type === 'VariableDeclaration' ? node.declarations : []).map(node => node.id.name)
let body = descriptor.scriptSetup.content
for (const node of script.scriptSetupAst.filter(node => node.type === 'ImportDeclaration').reverse()) body = body.slice(0, node.start) + body.slice(node.end)
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no }); return { promise, resolve, reject } }
const setup = (props, api, emit = () => {}) => {
  const lifecycle = { mounted: [], unmounted: [] }, clipboard = [], downloads = [], blobs = [], revoked = []
  const dom = { body: { appendChild: () => {} }, createElement: () => ({ click() { downloads.push({ href: this.href, name: this.download }) }, remove: () => {} }) }
  const deps = { computed: Vue.computed, ref: Vue.ref, watch: Vue.watch, onMounted: fn => lifecycle.mounted.push(fn), onUnmounted: fn => lifecycle.unmounted.push(fn),
    defineProps: () => props, defineEmits: () => emit, geoWebmasterApi: api, journeyDate, ...helpers,
    navigator: { clipboard: { writeText: async text => clipboard.push(text) } }, Blob,
    URL: { createObjectURL: blob => { blobs.push(blob); return 'blob:original-xml' }, revokeObjectURL: url => revoked.push(url) },
    document: dom, setTimeout: fn => fn() }
  const instance = new Function(...Object.keys(deps), body + `;return {${names.join(',')}};`)(...Object.values(deps))
  return { ...helpers, ...instance, lifecycle, clipboard, downloads, blobs, revoked }
}
const harness = (value = cycle(), overrides = {}) => {
  const props = Vue.reactive({ cycle: value, busy: false }), calls = { list: [], update: [], emit: [] }
  let server = value.webmaster
  const api = { list: async id => { calls.list.push(id); return { data: server } }, update: async (id, payload) => { calls.update.push([id, payload]); return { data: server } }, ...overrides }
  const instance = setup(props, api, (...args) => calls.emit.push(args)); calls.emit = []
  return { props, calls, setServer: value => { server = value }, ...instance }
}
const render = async (value = cycle(), stage) => {
  const template = compileTemplate({ source: descriptor.template.content, filename: filename.pathname, id: 'webmaster-render', compilerOptions: { bindingMetadata: script.bindings } })
  assert.deepEqual(template.errors, [])
  const code = template.code.replace(/import \{([^}]+)\} from "vue"/g, (_all, imports) => `const {${imports.split(',').map(item => { const [name, alias] = item.trim().split(/\s+as\s+/); return alias ? `${name}:${alias}` : name }).join(',')}}=Vue;`).replace('export function render', 'return function render')
  const compiled = new Function('Vue', code)(Vue)
  const app = Vue.createSSRApp({ props: ['cycle', 'busy'], setup: props => { const instance = setup(props, { list: async () => ({ data: value.webmaster }) }); if (stage !== undefined) instance.step.value = stage; return instance }, render: compiled }, { cycle: value, busy: false })
  app.component('el-button', { props: ['disabled', 'loading'], setup: (props, { slots }) => () => Vue.h('button', { disabled: props.disabled }, slots.default?.()) })
  return renderToString(app)
}

test('website scope supports company and product URLs but never trusts a foreign overview or action entry', () => {
  const product = cycle({ baseline: { profile: { entity_type: 'product', website: 'https://example.org/products/app/' } } })
  assert.equal(helpers.webmasterOverview(product, overview()).supported, true)
  for (const website of ['http://example.org/', 'https://user:secret@example.org/', 'https://example.org:8443/', 'javascript:alert(1)']) assert.equal(helpers.webmasterOrigin(cycle({ baseline: { profile: { website } } })), '')
  for (const change of [{ site_origin: 'https://other.org' }, { context_sha256: 'unknown' }, { supported: false }]) assert.equal(helpers.webmasterOverview(cycle(), overview(change)).supported, false)
  assert.equal(helpers.webmasterOverview(cycle(), overview({ entry_url: 'https://evil.test/' })).entry_url, helpers.bingWebmasterEntry)
})

test('reading XML preserves original byte order and mixed line endings without converting to text', async () => {
  assert.equal(await helpers.webmasterReadFile(file()), encoded)
  assert.equal(await helpers.webmasterReadFile(file(raw, { name: 'BingSiteAuth (1).xml' })), encoded)
  assert.deepEqual(helpers.webmasterDecodeFile(encoded), raw)
  for (const bad of [{ name: 'other.xml' }, { size: 0 }, { size: 16385 }, { size: raw.length + 1 }]) await assert.rejects(helpers.webmasterReadFile(file(raw, bad)))
  for (const value of ['', '!!!!', 'A', 'AA=A', 'A'.repeat(22000)]) assert.throws(() => helpers.webmasterDecodeFile(value))
})

test('payloads allow exact supported fields and never accept file paths or arbitrary request URLs', () => {
  const view = helpers.webmasterOverview(cycle())
  assert.deepEqual(helpers.webmasterPayload(view, 'upload_xml', { xml_base64: encoded, file_path: 'C:/secret' }), { action: 'upload_xml', context_sha256: hash, xml_base64: encoded })
  assert.deepEqual(helpers.webmasterPayload(view, 'check_public', { url: 'http://127.0.0.1/' }), { action: 'check_public', context_sha256: hash })
  assert.throws(() => helpers.webmasterPayload(view, 'confirm_ownership', { confirmed: false }))
  assert.throws(() => helpers.webmasterPayload(view, 'record_sitemap', { status: 'submitted' }))
  for (const url of ['https://other.org/', 'https://name@example.org/', 'https://example.org/#fragment', 'http:// https://example.org/', 'https:// https://example.org/', 'https://example.org/ https://other.org/']) assert.throws(() => helpers.webmasterPayload(view, 'record_inspection', { url, status: 'indexed', note: '' }))
  assert.throws(() => helpers.webmasterPayload(view, 'record_inspection', { url: 'https://example.org/', status: 'indexed', note: '😀'.repeat(501) }))
})

test('deployment instructions are website-specific and do not claim submission, indexing or server access', () => {
  const text = helpers.webmasterDeploymentText(helpers.webmasterOverview(cycle()))
  assert.match(text, /https:\/\/example.org\/BingSiteAuth.xml/)
  assert.match(text, /原样发布/); assert.match(text, /后续部署/)
  assert.doesNotMatch(text, /ssh|密码|私钥|GEO 成功|已经收录/)
})

test('a restart reads persisted user confirmations without replaying any write or public request', async () => {
  const persisted = record({ ownership: { status: 'confirmed', observed_at: '2026-10-27T03:00:00Z', evidence_source: 'user_reported_platform_result' }, sitemap_submission: { status: 'pending', evidence_source: 'user_reported_platform_result', observed_at: '2026-10-27T03:05:00Z' } })
  const page = harness(cycle({ webmaster: overview({ record: persisted }) }))
  await page.refresh()
  assert.equal(page.step.value, 3); assert.equal(page.record.value.ownership.status, 'confirmed')
  assert.deepEqual(page.calls.update, []); assert.deepEqual(page.calls.list, ['cycle-a'])
  const html = await render(page.props.cycle)
  assert.match(html, /网站验证完成（用户确认）/); assert.match(html, /已提交，正在处理（用户确认）/); assert.match(html, /索引状态未知/)
})

test('upload stores only original base64 and does not confirm Bing ownership', async () => {
  const saved = record({ verification_file: { name: 'BingSiteAuth.xml', bytes: raw.length, sha256: hash, xml_base64: encoded } })
  const page = harness(cycle(), { update: async (id, payload) => {
    page.calls.update.push([id, payload])
    return { data: overview({ context_sha256: otherHash, record: saved }) }
  } })
  page.setServer(overview())
  const event = { target: { files: [file()], value: 'chosen' } }
  await page.uploadFile(event)
  assert.equal(page.calls.update.length, 1)
  assert.deepEqual(page.calls.update[0], ['cycle-a', { action: 'upload_xml', context_sha256: hash, xml_base64: encoded }])
  assert.equal(page.step.value, 2); assert.equal(event.target.value, '')
  assert.equal(page.record.value.ownership.status, 'not_confirmed')
  assert.equal(page.record.value.verification_file.xml_base64, encoded)
})

test('download returns the persisted original bytes under the fixed Bing filename', async () => {
  const page = harness(cycle({ webmaster: overview({ record: record({ verification_file: { name: 'unsafe/path', xml_base64: encoded, bytes: raw.length, sha256: hash } }) }) }))
  page.saveVerificationFile()
  assert.deepEqual(page.downloads, [{ href: 'blob:original-xml', name: 'BingSiteAuth.xml' }])
  assert.deepEqual(new Uint8Array(await page.blobs[0].arrayBuffer()), raw)
  assert.deepEqual(page.revoked, ['blob:original-xml'])
})

test('an explicit ownership checkbox records a user confirmation, never a public-check inference', async () => {
  const page = harness(cycle({ webmaster: overview({ record: record({ public_check: { status: 'matches', exact_source_bytes: true } }) }) }))
  await page.confirmOwnership(); assert.equal(page.calls.update.length, 0)
  page.ownershipConfirmed.value = true
  await page.confirmOwnership()
  assert.deepEqual(page.calls.update[0][1], { action: 'confirm_ownership', context_sha256: hash, confirmed: true })
  assert.equal(page.step.value, 3)
  const html = await render(page.props.cycle, 2)
  assert.match(html, /软件公网核对：/); assert.match(html, /与原件一致/)
  assert.doesNotMatch(html, /网站验证完成（用户确认）/)
})

test('submitted sitemap remains processing and zero discovered URLs never become zero indexed pages', async () => {
  const state = record({ sitemap_check: { status: 'ok', entries: 18 }, sitemap_submission: { status: 'pending', discovered_urls: 0, note: 'Last crawl 未显示', evidence_source: 'user_reported_platform_result' } })
  const page = harness(cycle({ webmaster: overview({ record: state }) }))
  page.sitemapStatus.value = 'pending'; page.discoveredUrls.value = 0; page.sitemapNote.value = 'Last crawl 未显示'; await page.saveSitemap()
  assert.equal(page.calls.update[0][1].status, 'pending'); assert.equal(page.calls.update[0][1].discovered_urls, 0); assert.equal(page.calls.update[0][1].note, 'Last crawl 未显示')
  const html = await render(page.props.cycle, 3)
  assert.match(html, /Processing/); assert.match(html, /已提交，正在处理（用户确认）/)
  assert.match(html, /包含 18 条网址/); assert.match(html, /此数量不是已索引页数/)
  assert.match(html, /索引状态未知/); assert.match(html, /Bing 发现网址数：0/); assert.doesNotMatch(html, /已索引 18|已索引 0|已收录 0/)
})

test('only same-site inspections are displayed and unknown remains an explicit saved result', async () => {
  const state = record({ inspections: [{ url: 'https://example.org/', status: 'unknown', evidence_source: 'user_reported_platform_result', note: '<script>未确认</script>' }, { url: 'https://other.org/', status: 'indexed' }] })
  const page = harness(cycle({ webmaster: overview({ record: state }) }))
  assert.equal(page.inspections.value.length, 1)
  page.inspectionUrl.value = 'https://example.org/'; await page.saveInspection()
  assert.equal(page.calls.update[0][1].status, 'unknown')
  const html = await render(page.props.cycle, 3)
  assert.match(html, /索引状态未知（用户确认）/); assert.match(html, /&lt;script&gt;/)
  assert.doesNotMatch(html, /https:\/\/other.org/)
})

test('inspection copy uses one complete valid URL and interface errors remain unknown', async () => {
  const page = harness()
  assert.equal(page.inspectionCopyUrl.value, 'https://example.org/')
  page.inspectionUrl.value = 'http:// https://example.org/'
  assert.equal(page.validInspection.value, false)
  await page.saveInspection(); assert.deepEqual(page.calls.update, [])
  await page.copyText(page.inspectionCopyUrl.value)
  assert.deepEqual(page.clipboard, ['https://example.org/'])
  page.inspectionUrl.value = 'https://example.org/about/'
  await page.copyText(page.inspectionCopyUrl.value)
  assert.equal(page.clipboard.at(-1), 'https://example.org/about/')
  page.inspectionNote.value = 'Bing 接口 Error，未取得结果'; await page.saveInspection()
  assert.equal(page.calls.update[0][1].status, 'unknown')
  const html = await render(page.props.cycle, 3)
  assert.match(html, /Ctrl\+A/); assert.match(html, /接口 Error 时，索引状态仍未知/)
})

test('writes refresh the latest context and block concurrent clicks while pending', async () => {
  const wait = deferred(), page = harness(cycle(), { update: async (id, payload) => { page.calls.update.push([id, payload]); return wait.promise } })
  const first = page.perform('check_public'); await Promise.resolve(); await Promise.resolve()
  await page.perform('check_public'); assert.equal(page.calls.update.length, 1)
  assert.deepEqual(page.calls.emit, [['busy', true]])
  wait.resolve({ data: overview({ context_sha256: otherHash, record: record({ public_check: { status: 'matches' } }) }) }); await first
  assert.equal(page.view.value.context_sha256, otherHash)
  assert.deepEqual(page.calls.emit, [['busy', true], ['busy', false]])
})

test('a changed server context aborts the old operation before any write', async () => {
  const page = harness(); page.setServer(overview({ context_sha256: otherHash }))
  await page.perform('check_public')
  assert.deepEqual(page.calls.update, []); assert.match(page.error.value, /已变化/)
  assert.equal(page.writeUncertain.value, false); assert.equal(page.view.value.context_sha256, otherHash)
})

test('a different journey task starting during the preliminary read prevents the pending write', async () => {
  const wait = deferred(), page = harness(cycle(), { list: async () => wait.promise })
  const operation = page.perform('check_public')
  page.props.busy = true; wait.resolve({ data: overview() }); await operation
  assert.deepEqual(page.calls.update, [])
})

test('an uncertain write never retries automatically and manual refresh restores available actions', async () => {
  const page = harness(cycle(), { update: async () => { throw new Error('保存回执未返回') } })
  await page.perform('check_public'); assert.equal(page.writeUncertain.value, true)
  const reads = page.calls.list.length
  await page.perform('check_public'); assert.equal(page.calls.list.length, reads)
  await page.refresh(); assert.equal(page.writeUncertain.value, false)
})

test('late reads from a previous cycle cannot replace the current website progress', async () => {
  const wait = deferred(), page = harness(cycle(), { list: async id => id === 'cycle-a' ? wait.promise : { data: overview({ site_origin: 'https://new.org', context_sha256: otherHash }) } })
  const reading = page.refresh()
  page.props.cycle = cycle({ id: 'cycle-b', baseline: { profile: { website: 'https://new.org/' } }, webmaster: overview({ site_origin: 'https://new.org', context_sha256: otherHash }) })
  await Promise.resolve(); wait.resolve({ data: overview({ record: record({ ownership: { status: 'confirmed' } }) }) }); await reading
  assert.equal(page.view.value.site_origin, 'https://new.org'); assert.equal(page.view.value.context_sha256, otherHash)
  assert.notEqual(page.record.value.ownership?.status, 'confirmed')
})

test('a late file read cannot upload into another cycle', async () => {
  const wait = deferred(), page = harness()
  const reading = page.uploadFile({ target: { files: [file(raw, { arrayBuffer: () => wait.promise })], value: 'chosen' } })
  page.props.cycle = cycle({ id: 'cycle-b' }); wait.resolve(raw.buffer.slice(raw.byteOffset, raw.byteOffset + raw.byteLength)); await reading
  assert.deepEqual(page.calls.update, [])
})

test('a late write response cannot replace the newly selected cycle or release its busy state', async () => {
  const wait = deferred(), page = harness(cycle(), { update: async () => wait.promise })
  const writing = page.perform('check_public'); await Promise.resolve(); await Promise.resolve()
  page.props.cycle = cycle({ id: 'cycle-b', webmaster: overview({ context_sha256: otherHash }) })
  wait.resolve({ data: overview({ record: record({ ownership: { status: 'confirmed' } }) }) }); await writing
  assert.equal(page.props.cycle.id, 'cycle-b'); assert.equal(page.record.value.ownership?.status, undefined)
})

test('a component removed during a write keeps the returned data out of the departed interface', async () => {
  const wait = deferred(), page = harness(cycle(), { update: async () => wait.promise })
  const operation = page.perform('check_public'); await Promise.resolve(); await Promise.resolve()
  page.lifecycle.unmounted.forEach(fn => fn())
  wait.resolve({ data: overview({ record: record({ ownership: { status: 'confirmed' } }) }) }); await operation
  assert.equal(page.record.value.ownership?.status, undefined)
  assert.deepEqual(page.calls.emit.at(-1), ['busy', false])
})

test('fixed official entry and source labels are escaped and stay independent from GEO success', async () => {
  const html = await render(cycle(), 0)
  assert.deepEqual([...html.matchAll(/href="([^"]+)"/g)].map(item => item[1]), [helpers.bingWebmasterEntry])
  assert.match(html, /日常浏览器/); assert.match(html, /软件不接收账号密码/)
  assert.match(html, /这里的进度不计入 GEO 成效/)
  assert.doesNotMatch(source, /v-html|pywebview|localStorage|sessionStorage|password|ssh/)
})

test('the component compiles with reduced-motion support and both journey placements share global busy', () => {
  const template = compileTemplate({ source: descriptor.template.content, filename: filename.pathname, id: 'webmaster-test' })
  assert.deepEqual(template.errors, [])
  for (const style of descriptor.styles) assert.deepEqual(compileStyle({ source: style.content, filename: filename.pathname, id: 'webmaster-test', scoped: true }).errors, [])
  assert.match(source, /prefers-reduced-motion:reduce/)
  const journey = fs.readFileSync(new URL('../src/views/GeoJourney.vue', import.meta.url), 'utf8')
  assert.equal((journey.match(/<GeoWebmaster /g) || []).length, 2)
  assert.match(journey, /busy=computed\([^\n]*webmasterBusy.value/)
  assert.doesNotMatch(journey.slice(journey.indexOf('key="publish"'), journey.indexOf('key="feedback"')), /<GeoWebmaster/)
})
