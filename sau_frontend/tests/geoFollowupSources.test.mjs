import assert from 'node:assert/strict'
import fs from 'node:fs'
import test from 'node:test'
import * as Vue from 'vue'
import {renderToString} from '@vue/server-renderer'
import {parse, compileScript, compileTemplate, compileStyle} from '@vue/compiler-sfc'
import {followupPublicUrl, followupSourcesView, feedbackRoutesForBrief} from '../src/utils/geoFollowupSources.js'
import {optimizationPlanView} from '../src/utils/geoOptimizationPlan.js'

const cases = [{id: 'q01', category: 'discovery', question: '如何比较模型接入服务？'},
  {id: 'q02', category: 'brand_identity', question: '示例产品由谁运营？'}]
const role = 'followup_channel_research_only'
const fixture = (count = 1) => {
  const probes = cases.map((row, index) => ({id: `probe-${index + 1}`, cycle_id: 'cycle-a', case_id: row.id,
    question: row.question, observed_question: row.question, plan_sha256: 'a'.repeat(64), source: 'native_collector',
    native_verified: true, conditions: {same_question: true}, after: {answer_evaluable: true, response: '合成回答仅用于离线界面验证。', client_mode: 'consumer', model_label: '合成模型'}}))
  const opportunities = Array.from({length: count}, (_, index) => {
    const source_ref = `fs_${(index + 1).toString(16).padStart(64, '0')}`
    const source = {probe_id: 'probe-1', case_id: 'q01', source_ref, title: `合成来源 ${index + 1}`,
      url: `https://directory${String(index + 1).padStart(2, '0')}.example.org/catalog`, kind: 'displayed_sources', source: 'native_collector', native_verified: true}
    return {id: `feedback:directory-${index + 1}`, domain: `directory${index + 1}.example.org`, surface: 'unknown',
      candidate: null, source_ids: [], feedback_source_refs: [{probe_id: 'probe-1', case_id: 'q01', source_ref}],
      feedback_sources: [source], evidence_role: role, facts_verified: false, publication_authorized: false,
      geo_effect_verified: false, note: '仅为研究线索，权限和效果均未确认。'}
  })
  const brief = {id: 'b01', topic: '模型接入选型说明', case_ids: ['q01'], source_ids: [], feedback_probe_ids: ['probe-1']}
  return {id: 'cycle-a', test_plan: {plan_sha256: 'a'.repeat(64), cases: structuredClone(cases)}, probes,
    analysis: {briefs: [brief]}, observations: {sources: []}, opportunities: [], followup_opportunities: opportunities,
    optimization_plan: {schema_version: 'geo_optimization_plan_v1',
      questions: [{case_id: 'q01', category: 'discovery', question: cases[0].question, goal: 'natural_discovery'}],
      actions: [{...brief, gap: '公开依据未找到。', change_summary: '先核对渠道与公开材料。',
        source_routes: opportunities.map(row => ({id: row.id, label: row.domain, surface: row.surface, source_ids: [],
          feedback_source_refs: structuredClone(row.feedback_source_refs), evidence_role: role,
          facts_verified: false, publication_authorized: false, geo_effect_verified: false}))}]}}
}

test('seventeen sources including directory nine and ten are reachable and remain separate from baseline IDs', () => {
  const cycle = fixture(17), before = structuredClone(cycle), view = followupSourcesView(cycle)
  assert.equal(view.sourceCount, 17)
  assert.equal(view.routes.length, 17)
  assert.equal(view.probeCount, 1)
  assert.equal(view.caseCount, 1)
  for (const index of [9, 10]) {
    assert.ok(view.routes.some(route => route.sources.some(source => source.title === `合成来源 ${index}`)))
  }
  const action = optimizationPlanView(cycle).selected
  assert.equal(action.sourceRoutes.length, 17)
  assert.ok(action.sourceRoutes.every(route => route.sourceIds.length === 0 && route.feedbackSourceRefs[0].probeId === 'probe-1'))
  assert.deepEqual(cycle, before)
})

test('legacy briefs do not acquire follow-up routes without an explicit current feedback reference', () => {
  const cycle = fixture(), brief = cycle.analysis.briefs[0]
  for (const value of [undefined, [], ['missing'], ['probe-2'], ['probe-1', 'probe-1'], ['probe-1', 'missing'], 'probe-1']) {
    brief.feedback_probe_ids = value
    assert.deepEqual(feedbackRoutesForBrief(cycle, brief, cycle.optimization_plan.actions[0].source_routes), [])
    assert.deepEqual(optimizationPlanView(cycle).selected.sourceRoutes, [])
  }
})

test('forged cache refs, cross-case refs, baseline-ID confusion and duplicate bindings are dropped', () => {
  const mutations = [
    cycle => { cycle.optimization_plan.actions[0].source_routes[0].feedback_source_refs[0].source_ref = `fs_${'f'.repeat(64)}` },
    cycle => { cycle.followup_opportunities[0].feedback_sources[0].case_id = 'q02' },
    cycle => { cycle.followup_opportunities[0].feedback_source_refs[0].case_id = 'q02' },
    cycle => { cycle.followup_opportunities[0].feedback_sources[0].source = 'user_reported_external' },
    cycle => { cycle.followup_opportunities[0].feedback_sources[0].native_verified = false },
    cycle => { cycle.followup_opportunities[0].source_ids = [cycle.followup_opportunities[0].feedback_source_refs[0].source_ref] },
    cycle => { cycle.optimization_plan.actions[0].source_routes[0].source_ids = [cycle.followup_opportunities[0].feedback_source_refs[0].source_ref] },
    cycle => { cycle.followup_opportunities[0].feedback_source_refs.push(structuredClone(cycle.followup_opportunities[0].feedback_source_refs[0])) },
    cycle => { cycle.followup_opportunities[0].feedback_sources.push(structuredClone(cycle.followup_opportunities[0].feedback_sources[0])) },
    cycle => { cycle.followup_opportunities.push(structuredClone(cycle.followup_opportunities[0])) },
  ]
  for (const mutate of mutations) {
    const cycle = fixture(); mutate(cycle)
    assert.deepEqual(optimizationPlanView(cycle).selected.sourceRoutes, [], mutate.toString())
  }
})

test('foreign plans, original-question mismatches, incomplete and unverified probes do not render leads', () => {
  const mutations = [
    probe => { probe.cycle_id = 'foreign' }, probe => { probe.plan_sha256 = 'b'.repeat(64) },
    probe => { probe.conditions.same_question = false }, probe => { probe.question = '不同原题' },
    probe => { probe.observed_question = '含网址提示的不同问题' }, probe => { probe.native_verified = false },
    probe => { probe.after.answer_evaluable = false }, probe => { probe.after.answer_error = 'private_account_context' },
    probe => { probe.after.response = '' }, probe => { probe.observation_role = 'baseline' },
  ]
  for (const mutate of mutations) {
    const cycle = fixture(); mutate(cycle.probes[0])
    assert.equal(followupSourcesView(cycle).routes.length, 0, mutate.toString())
    assert.deepEqual(optimizationPlanView(cycle).selected.sourceRoutes, [])
  }
})

test('missing and malformed detail fields fail closed without a runtime error', () => {
  for (const cycle of [null, {}, {followup_opportunities: {}}, {...fixture(), probes: null},
    {...fixture(), test_plan: null}, {...fixture(), test_plan: {cases, plan_sha256: null}}]) {
    assert.deepEqual(followupSourcesView(cycle), {routes: [], sourceCount: 0, probeCount: 0, caseCount: 0})
  }
})

test('links accept public HTTPS syntax and reject executable, credentialed, private or ambiguous URLs', () => {
  assert.equal(followupPublicUrl('https://directory.example.org/catalog'), 'https://directory.example.org/catalog')
  for (const value of ['javascript:alert(1)', 'data:text/html,hi', 'http://example.org/', '//example.org/',
    'https://user:secret@example.org/', 'https://127.0.0.1/', 'https://2130706433/', 'https://localhost/',
    'https://server.local/', 'https://server.internal/', 'https://example.org:444/', 'https://%65xample.org/',
    'https://example.org\\@evil.org/', 'https://example.org/\nfoo', {}, null]) assert.equal(followupPublicUrl(value), null, String(value))
})

const filename = new URL('../src/components/GeoFollowupSources.vue', import.meta.url)
const source = fs.readFileSync(filename, 'utf8'), {descriptor, errors} = parse(source, {filename: filename.pathname})
assert.deepEqual(errors, [])
const script = compileScript(descriptor, {id: 'followup-sources'})
const template = compileTemplate({source: descriptor.template.content, filename: filename.pathname, id: 'followup-sources', compilerOptions: {bindingMetadata: script.bindings}})
assert.deepEqual(template.errors, [])
const code = template.code.replace(/import \{([^}]+)\} from "vue"/g, (_all, imports) =>
  `const {${imports.split(',').map(item => {const [name, alias] = item.trim().split(/\s+as\s+/); return alias ? `${name}:${alias}` : name}).join(',')}}=Vue;`)
  .replace('export function render', 'return function render')
const render = new Function('Vue', code)(Vue)
const names = script.scriptSetupAst.flatMap(node => node.type === 'VariableDeclaration' ? node.declarations.map(value => value.id.name) : [])
const setup = props => new Function('computed', 'followupSourcesView', 'defineProps',
  descriptor.scriptSetup.content.replace(/^import .*$/gm, '') + `;return {${names.join(',')}};`)(Vue.computed, followupSourcesView, () => props)
const html = cycle => renderToString(Vue.createSSRApp({setup: () => setup({cycle}), render}))

test('the collapsed component renders attribution, both later directories and explicit boundaries without write controls', async () => {
  const result = await html(fixture(17))
  for (const fragment of ['后续复测的渠道线索', '17 条来源', '17 个候选渠道', '合成来源 9', '合成来源 10',
    '复测记录：probe-1', '原题 q01', '普通对话', '合成模型', cases[0].question, '不表示入口已核对', '投稿权限', 'GEO 已有效果']) assert.ok(result.includes(fragment), fragment)
  assert.ok(!/<details[^>]*\bopen(?:[\s=>])/.test(result))
  assert.ok(!result.includes('<button'))
  assert.ok(!source.includes('v-html'))
  assert.ok(!source.includes('defineEmits'))
  assert.ok(!source.includes('@/api/'))
  const primaryLines = [...result.matchAll(/<p class="attribution">(.*?)<\/p>/g)].map(match => match[1]).filter(line => line.includes('原题 q01'))
  assert.equal(primaryLines.length, 17)
  assert.ok(primaryLines.every(line => !line.includes('probe-1') && !line.includes('fs_') && !line.includes(cases[0].question)))
  assert.equal((result.match(/<details class="source-details">/g) || []).length, 17)
  assert.deepEqual(compileStyle({source: descriptor.styles[0].content, id: 'followup-sources'}).errors, [])
})

test('mode and model labels come from the corresponding saved probe and absent labels remain unknown', () => {
  const cycle = fixture(), read = () => followupSourcesView(cycle).routes[0].sources[0]
  cycle.followup_opportunities[0].feedback_sources[0].client_mode = 'work'
  assert.equal(read().clientMode, 'consumer')
  cycle.probes[0].after.client_mode = 'work'
  assert.equal(read().clientMode, 'work')
  delete cycle.probes[0].after.client_mode
  delete cycle.probes[0].after.model_label
  assert.equal(read().clientMode, 'unknown')
  assert.equal(read().modelLabel, '模型未知')
})

test('unknown URLs are inert text, HTML is escaped, and switching cycles removes old sources', async () => {
  const cycle = fixture(), item = cycle.followup_opportunities[0].feedback_sources[0]
  item.title = '<img src=x onerror=alert(1)>'
  item.url = 'javascript:alert(1)'
  const result = await html(cycle)
  assert.ok(!result.includes('href='))
  assert.ok(!result.includes('<img'))
  assert.ok(result.includes('&lt;img'))
  assert.ok(result.includes('公开网址未确认'))
  const props = Vue.reactive({cycle}), page = setup(props)
  assert.equal(page.view.value.sourceCount, 1)
  props.cycle = {id: 'cycle-b'}
  assert.equal(page.view.value.sourceCount, 0)
  assert.ok(!/\bsection\b|probe-1|合成来源/.test(await html({id: 'cycle-b'})))
})

test('both pages wire the independent read-only panel while retaining planning review notices', () => {
  for (const name of ['GeoJourney', 'GeoContentLoop']) {
    const page = fs.readFileSync(new URL(`../src/views/${name}.vue`, import.meta.url), 'utf8')
    assert.equal((page.match(/<GeoFollowupSources\b/g) || []).length, 1)
    assert.ok(page.split(/\r?\n/).find(line => line.includes('<GeoFollowupSources')).includes(':cycle="cycle"'))
    assert.match(page, /planningFeedbackNotice/)
    assert.ok(page.indexOf('<GeoOptimizationPlan') < page.indexOf('<GeoFollowupSources'))
    const parsed = parse(page).descriptor
    assert.deepEqual(compileScript(parsed, {id: name}).errors || [], [])
    assert.deepEqual(compileTemplate({source: parsed.template.content, filename: name + '.vue', id: name}).errors, [])
  }
})
