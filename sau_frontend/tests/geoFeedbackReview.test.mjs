import assert from 'node:assert/strict'
import fs from 'node:fs'
import test from 'node:test'
import * as Vue from 'vue'
import {renderToString} from '@vue/server-renderer'
import {parse, compileScript, compileTemplate} from '@vue/compiler-sfc'
import {feedbackReviewView} from '../src/utils/geoFeedbackReview.js'
import {planningFeedbackNotice} from '../src/utils/geoPlanningFeedback.js'
import {optimizationPlanView, optimizationPlanSelection} from '../src/utils/geoOptimizationPlan.js'
import {sameResearchBrief} from '../src/utils/geoJourney.js'

const declaration = (decision = 'not_used', reason = '本选题先整理已确认的公开资料，后续反馈留作渠道核对。') => ({decision, reason})
const brief = (review = declaration()) => ({id: 'b01', topic: '合成选题', case_ids: ['q01'], source_ids: [],
  feedback_review: review})
const used = () => ({...brief(declaration('used', '参考本题复测的公开编辑线索，仍需核对。')), feedback_probe_ids: ['probe-01'],
  feedback_inputs: [{probe_id: 'probe-01', case_id: 'q01', evidence_role: 'editorial_signal_only_not_writing_fact_or_public_metric'}]})

test('old or malformed declarations stay unknown and never infer usage from IDs', () => {
  const old = brief(); delete old.feedback_review
  for (const value of [null, {}, old, {...old, feedback_probe_ids: ['probe-01']}, brief(null),
    brief({decision: 'used', reason: ''}), brief({decision: 'verified', reason: '已核实。'}),
    brief({...declaration(), verified: true}), brief(declaration('not_used', '甲'.repeat(501))),
    {...brief(), feedback_probe_ids: ['probe-01']}]) {
    const view = feedbackReviewView(value)
    assert.equal(view.decision, 'unknown')
    assert.equal(view.reason, '')
    assert.ok(!view.label.includes('已采用'))
  }
})

test('used refs must match the saved same-case snapshot and remain a model declaration', () => {
  const value = used(), before = structuredClone(value), view = feedbackReviewView(value)
  assert.equal(view.decision, 'used')
  assert.deepEqual(view.probeIds, ['probe-01'])
  assert.match(view.label, /AI申报/)
  assert.match(view.note, /不证明实际阅读、采用或优化效果/)
  assert.deepEqual(value, before)
  for (const mutate of [row => { row.feedback_inputs = [] }, row => { row.feedback_inputs[0].case_id = 'q02' },
    row => { row.feedback_inputs[0].evidence_role = 'verified_fact' }, row => { row.feedback_probe_ids.push('probe-01') },
    row => { row.feedback_inputs.push(structuredClone(row.feedback_inputs[0])) }]) {
    const invalid = used(); mutate(invalid)
    assert.equal(feedbackReviewView(invalid).decision, 'unknown')
  }
})

test('not-used and historical no-eligible declarations are labelled as AI statements with Unicode reasons intact', () => {
  assert.equal(feedbackReviewView(brief()).decision, 'not_used')
  const noEligible = feedbackReviewView(brief(declaration('no_eligible_feedback', '策划时未获得本题的可用后续反馈。')))
  assert.match(noEligible.label, /AI申报.*策划时/)
  const unicode = '🧪'.repeat(500)
  assert.equal(feedbackReviewView(brief(declaration('not_used', unicode))).reason, unicode)
  assert.equal(feedbackReviewView(brief(declaration('not_used', '  '))).decision, 'unknown')
})

test('current notices distinguish all-explicit unused declarations from missing attribution without claiming adoption', () => {
  const cycle = {analysis: {briefs: [brief(), {...brief(), id: 'b02'}]}, feedback_diagnostics: {planning_feedback: {
    context_status: 'current', attribution_status: 'not_recorded', available_count: 6, needs_update: false}}}
  const explained = planningFeedbackNotice(cycle)
  assert.match(explained.message, /AI已说明未引用后续反馈/)
  assert.equal(explained.canReplan, false)
  delete cycle.analysis.briefs[1].feedback_review
  assert.match(planningFeedbackNotice(cycle).message, /反馈出处未标注，待审阅/)
  cycle.feedback_diagnostics.planning_feedback.context_status = 'outdated'
  assert.equal(planningFeedbackNotice(cycle).canReplan, true)
})

test('a changed declaration changes current-brief identity while old absent fields retain their prior matching', () => {
  const value = brief(), saved = {provenance: {brief: structuredClone(value)}}
  assert.equal(sameResearchBrief(saved, value), true)
  value.feedback_review.reason = '后来修改过的另一条声明理由。'
  assert.equal(sameResearchBrief(saved, value), false)
  const old = {id: 'b01', topic: '旧选题', case_ids: ['q01']}
  assert.equal(sameResearchBrief({provenance: {brief: structuredClone(old)}}, old), true)
})

const fixture = () => {
  const first = brief(), second = {...used(), id: 'b02', topic: '另一个合成选题'}
  return {id: 'cycle-a', analysis: {briefs: [first, second]}, test_plan: {cases: [{id: 'q01', category: 'discovery', question: '团队怎样选择模型接入服务？'}]},
    observations: {sources: []}, opportunities: [], optimization_plan: {schema_version: 'geo_optimization_plan_v1',
      questions: [{case_id: 'q01', category: 'discovery', question: '团队怎样选择模型接入服务？', goal: 'natural_discovery'}],
      actions: [first, second].map(row => ({id: row.id, topic: row.topic, case_ids: row.case_ids,
        gap: '待核对本题的公开依据。', change_summary: '先核对资料再安排内容。', source_routes: []}))}}
}
const filename = new URL('../src/components/GeoOptimizationPlan.vue', import.meta.url)
const {descriptor, errors} = parse(fs.readFileSync(filename, 'utf8'), {filename: filename.pathname})
assert.deepEqual(errors, [])
const script = compileScript(descriptor, {id: 'feedback-review'})
const names = script.scriptSetupAst.flatMap(node => node.type === 'VariableDeclaration' ? node.declarations.map(value => value.id.name) : [])
const setup = props => new Function('computed', 'optimizationPlanView', 'optimizationPlanSelection', 'defineProps', 'defineEmits',
  descriptor.scriptSetup.content.replace(/^import .*$/gm, '') + `;return {${names.join(',')}};`)(Vue.computed, optimizationPlanView, optimizationPlanSelection, () => props, () => () => {})
const template = compileTemplate({source: descriptor.template.content, filename: filename.pathname, id: 'feedback-review', compilerOptions: {bindingMetadata: script.bindings}})
assert.deepEqual(template.errors, [])
const code = template.code.replace(/import \{([^}]+)\} from "vue"/g, (_all, imports) =>
  `const {${imports.split(',').map(item => {const [name, alias] = item.trim().split(/\s+as\s+/); return alias ? `${name}:${alias}` : name}).join(',')}}=Vue;`)
  .replace('export function render', 'return function render')
const render = new Function('Vue', code)(Vue)
const html = props => renderToString(Vue.createSSRApp({props: ['cycle', 'selectedBriefId', 'busy'],
  setup: input => setup(input), render}, props))

test('only the selected brief declaration and escaped reason appear in the existing collapsed plan details', async () => {
  const cycle = fixture()
  cycle.analysis.briefs[0].feedback_review.reason = '<img src=x onerror=alert(1)>'
  const first = await html({cycle, selectedBriefId: 'b01', busy: false})
  assert.match(first, /本选题的后续反馈声明/)
  assert.match(first, /AI申报：未引用后续反馈/)
  assert.match(first, /&lt;img/)
  assert.ok(!first.includes('<img'))
  assert.ok(!first.includes('参考本题复测的公开编辑线索'))
  assert.ok(!/<details[^>]*\bopen(?:[\s=>])/.test(first))
  const second = await html({cycle, selectedBriefId: 'b02', busy: false})
  assert.match(second, /AI申报：参考了后续反馈/)
  assert.match(second, /参考本题复测的公开编辑线索/)
  assert.ok(!second.includes('&lt;img'))
  assert.match(second, /不证明实际阅读、采用或优化效果/)
})

test('legacy selected briefs render unknown without synthesizing a used or unused reason', async () => {
  const cycle = fixture()
  delete cycle.analysis.briefs[0].feedback_review
  const result = await html({cycle, selectedBriefId: 'b01', busy: false})
  assert.match(result, /未记录反馈使用声明/)
  assert.match(result, /缺少声明不能据此判断/)
  assert.ok(!result.includes('AI申报：未引用后续反馈'))
})
