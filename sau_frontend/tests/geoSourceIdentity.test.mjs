import assert from 'node:assert/strict'
import fs from 'node:fs'
import test from 'node:test'
import * as Vue from 'vue'
import { renderToString } from '@vue/server-renderer'
import { parse, compileScript, compileTemplate, compileStyle } from '@vue/compiler-sfc'
import { sourceIdentityView, sourceIdentitySummary } from '../src/utils/geoSourceIdentity.js'

const identity = (changes = {}) => ({ classification: 'target', basis: 'exact_official_host', evidence_scope: 'official_host', matched_name: '北京合成树科技有限公司', ...changes })
const filename = new URL('../src/components/GeoSourceIdentity.vue', import.meta.url)
const { descriptor, errors } = parse(fs.readFileSync(filename, 'utf8'), { filename: filename.pathname })
assert.deepEqual(errors, [])
const script = compileScript(descriptor, { id: 'source-identity-test' })
const render = async (value, compact = false) => {
  const template = compileTemplate({ source: descriptor.template.content, filename: filename.pathname, id: 'source-identity-render', compilerOptions: { bindingMetadata: script.bindings } })
  assert.deepEqual(template.errors, [])
  const code = template.code.replace(/import \{([^}]+)\} from "vue"/g, (_all, imports) => `const {${imports.split(',').map(item => { const [name, alias] = item.trim().split(/\s+as\s+/); return alias ? `${name}:${alias}` : name }).join(',')}}=Vue;`).replace('export function render', 'return function render')
  const compiled = new Function('Vue', code)(Vue)
  const setupBody = descriptor.scriptSetup.content.replace(/^import .*$/gm, '')
  const component = { props: ['identity', 'compact'], setup: props => new Function('computed', 'defineProps', 'sourceIdentityView', setupBody + ';return {props,view};')(Vue.computed, () => props, sourceIdentityView), render: compiled }
  return renderToString(Vue.createSSRApp(component, { identity: value, compact }))
}

test('only compatible basis and scope combinations produce a specific identity label', () => {
  assert.equal(sourceIdentityView(identity()).label, '官网域名匹配')
  assert.equal(sourceIdentityView(identity({ evidence_scope: 'official_page_scope' })).label, '官方页面范围匹配')
  assert.equal(sourceIdentityView(identity({ basis: 'exact_legal_name_title', evidence_scope: 'title_identity_only' })).label, '标题包含完整公司名')
  assert.equal(sourceIdentityView(identity({ classification: 'related', basis: 'confirmed_relationship', evidence_scope: 'confirmed_relation_identity_only' })).label, '关联公司')
  for (const value of [null, [], 'target', {}, identity({ basis: 'short_alias_title' }), identity({ evidence_scope: 'business_verified' }), identity({ matched_name: null }), identity({ classification: null }), identity({ basis: {} }), identity({ matched_name: '' })]) {
    assert.equal(sourceIdentityView(value).classification, 'unknown')
    assert.equal(sourceIdentityView(value).label, '主体待核对')
  }
  const missing = identity(); delete missing.evidence_scope
  assert.equal(sourceIdentityView(missing).classification, 'unknown')
})

test('an unrelated company is visibly limited to its title while remaining a platform lead', async () => {
  const value = Object.freeze(identity({ classification: 'other', basis: 'different_legal_name_title', evidence_scope: 'title_identity_only', matched_name: '北京另一棵树科技有限公司' }))
  const before = JSON.stringify(value), html = await render(value)
  assert.match(html, /标题为其他公司/); assert.match(html, /北京另一棵树科技有限公司/)
  assert.match(html, /仍可作为平台线索/); assert.match(html, /不能当作本对象的业务事实/)
  assert.equal(JSON.stringify(value), before)
})

test('unknown and malformed metadata never become business verification or a guessed relationship', async () => {
  for (const basis of ['source_url_unconfirmed', 'unconfirmed_relationship', 'company_title_not_product', 'official_authority_or_scope_mismatch']) {
    const html = await render(identity({ classification: 'unknown', basis, evidence_scope: 'unconfirmed', matched_name: null }))
    assert.match(html, /主体待核对/); assert.match(html, /不等于业务事实已核实/)
    assert.doesNotMatch(html, /source-identity-target|source-identity-related/)
  }
  const html = await render(identity({ classification: 'related', basis: 'different_legal_name_title', evidence_scope: 'title_identity_only' }))
  assert.match(html, /主体待核对/)
})

test('matched titles remain escaped text and compact labels retain their scope explanation', async () => {
  const value = identity({ matched_name: '<img src=x onerror=alert(1)>公司' })
  const html = await render(value, true)
  assert.match(html, /&lt;img/); assert.doesNotMatch(html, /<img/)
  assert.match(html, /title="[^"]*不等于页面内容或业务事实已核实/)
})

test('opportunity summaries bind exact source IDs without dropping unknown or other-subject leads', () => {
  const sources = Object.freeze([
    Object.freeze({ id: 'official', title: '官网原始标题', source_identity: Object.freeze(identity()) }),
    Object.freeze({ id: 'other', title: '其他公司原始标题', source_identity: Object.freeze(identity({ classification: 'other', basis: 'different_legal_name_title', evidence_scope: 'title_identity_only' })) }),
    Object.freeze({ id: 'unknown', title: '网址未知的原始标题' })
  ])
  const opportunity = Object.freeze({ source_ids: Object.freeze(['official', 'other', 'unknown', 'missing', 'official']) }), before = JSON.stringify({ opportunity, sources })
  const text = sourceIdentitySummary(opportunity, sources)
  assert.match(text, /域名或完整名称匹配 1 条/); assert.match(text, /标题为其他公司 1 条/); assert.match(text, /主体待核对 2 条/)
  assert.equal(JSON.stringify({ opportunity, sources }), before)
  assert.match(sourceIdentitySummary({ source_ids: ['official'] }, [...sources, sources[0]]), /主体待核对 1 条/)
  assert.match(sourceIdentitySummary({}, sources), /暂无对应来源明细/)
})

test('both views use projected source identities and retain the original channel leads', () => {
  const loop = fs.readFileSync(new URL('../src/views/GeoContentLoop.vue', import.meta.url), 'utf8')
  const journey = fs.readFileSync(new URL('../src/views/GeoJourney.vue', import.meta.url), 'utf8')
  assert.match(loop, /<GeoSourceIdentity :identity="source.source_identity" compact/)
  assert.match(loop, /\{\{ source.title \|\| source.domain \}\}/)
  assert.match(journey, /sourceIdentitySummary\(item,cycle.observations\?\.sources\)/)
  assert.match(journey, /lead in item.title_leads/)
  const template = compileTemplate({ source: descriptor.template.content, filename: filename.pathname, id: 'source-identity-compile' })
  assert.deepEqual(template.errors, [])
  for (const style of descriptor.styles) assert.deepEqual(compileStyle({ source: style.content, filename: filename.pathname, id: 'source-identity-compile', scoped: true }).errors, [])
})
