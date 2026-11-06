import assert from 'node:assert/strict'
import fs from 'node:fs'
import test from 'node:test'
import * as Vue from 'vue'
import {renderToString} from '@vue/server-renderer'
import {parse, compileScript, compileTemplate} from '@vue/compiler-sfc'
import {planningFeedbackNotice} from '../src/utils/geoPlanningFeedback.js'

const cycle = feedback => ({feedback_diagnostics: {planning_feedback: feedback}})
const current = (changes = {}) => cycle({context_status: 'current', attribution_status: 'not_recorded',
  referenced_feedback_count: 0, available_count: 6, needs_update: false, ...changes})
const pendingMessage = '当前选题的反馈输入已更新；反馈出处未标注，待审阅。'
const outdatedMessage = '当前选题生成后，反馈线索有变化，可以重新策划；既有稿件和发布记录保留。'
const legacyMessage = '当前选题没有可核对的反馈输入版本，可先审阅后按需更新。'

test('a current feedback snapshot without references calls for review without replanning', () => {
  const value = current(), before = structuredClone(value)
  assert.deepEqual(planningFeedbackNotice(value), {message: pendingMessage, canReplan: false})
  assert.deepEqual(value, before)
  assert.deepEqual(planningFeedbackNotice(current({needs_update: true})), {message: pendingMessage, canReplan: false})
})

test('changed snapshots and the older update flag retain the manual update notice', () => {
  for (const feedback of [{context_status: 'outdated', needs_update: false}, {needs_update: true}]) {
    assert.deepEqual(planningFeedbackNotice(cycle(feedback)), {message: outdatedMessage, canReplan: true})
  }
})

test('historical input versions stay unknown and ungenerated or malformed states stay quiet', () => {
  assert.deepEqual(planningFeedbackNotice(cycle({context_status: 'legacy_unknown', needs_update: false})),
    {message: legacyMessage, canReplan: false})
  assert.deepEqual(planningFeedbackNotice(cycle({context_status: 'legacy_unknown', needs_update: true})),
    {message: legacyMessage, canReplan: true})
  for (const value of [null, {}, cycle(null), cycle([]), cycle({}), cycle({needs_update: 'true'}),
    cycle({context_status: 'not_generated', needs_update: true}), cycle({context_status: 'unexpected', needs_update: true}),
    current({attribution_status: 'unknown'})]) assert.equal(planningFeedbackNotice(value), null)
})

test('zero available feedback suppresses notices without inventing missing attribution', () => {
  for (const context_status of ['current', 'outdated', 'legacy_unknown']) {
    assert.equal(planningFeedbackNotice(current({context_status, available_count: 0, needs_update: true})), null)
  }
})

test('recorded reference counts do not imply verification or an optimization result', () => {
  assert.deepEqual(planningFeedbackNotice(current({attribution_status: 'referenced', referenced_feedback_count: 2})), {
    message: '当前选题记录了 2 条反馈出处；出处记录不代表内容已核实或优化有效。', canReplan: false
  })
  for (const count of [undefined, null, 0, -1, 1.5, '2', Infinity, Number.MAX_SAFE_INTEGER + 1]) {
    assert.equal(planningFeedbackNotice(current({attribution_status: 'referenced', referenced_feedback_count: count})), null)
  }
})

const pages = ['GeoJourney', 'GeoContentLoop'].map(name => {
  const filename = new URL(`../src/views/${name}.vue`, import.meta.url)
  const source = fs.readFileSync(filename, 'utf8'), {descriptor, errors} = parse(source, {filename: filename.pathname})
  assert.deepEqual(errors, [])
  const script = compileScript(descriptor, {id: name})
  const declaration = script.scriptSetupAst.flatMap(node => node.type === 'VariableDeclaration' ? node.declarations : [])
    .find(node => node.id.name === 'planningFeedback')
  assert.ok(declaration)
  const expression = descriptor.scriptSetup.content.slice(declaration.init.start, declaration.init.end)
  const binding = new Function('computed', 'planningFeedbackNotice', 'cycle', `return (${expression})`)
  // Render the actual notice element without mounting either full page or its services.
  const start = descriptor.template.content.indexOf(name === 'GeoJourney' ? '<div v-if="planningFeedback"' : '<p v-if="planningFeedback"')
  assert.ok(start >= 0)
  const closing = name === 'GeoJourney' ? '</div>' : '</p>'
  const end = descriptor.template.content.indexOf(closing, start) + closing.length
  const template = compileTemplate({source: descriptor.template.content.slice(start, end), filename: filename.pathname,
    id: `${name}-feedback-notice`, compilerOptions: {bindingMetadata: script.bindings}})
  assert.deepEqual(template.errors, [])
  const code = template.code.replace(/import \{([^}]+)\} from "vue"/g, (_all, imports) =>
    `const {${imports.split(',').map(item => {const [name, alias] = item.trim().split(/\s+as\s+/); return alias ? `${name}:${alias}` : name}).join(',')}}=Vue;`)
    .replace('export function render', 'return function render')
  const render = new Function('Vue', code)(Vue)
  return {name, filename, descriptor, script, binding, render}
})

test('both views render review and stale states using the shared notice, with no CTA for unmarked references', async () => {
  for (const page of pages) {
    const record = Vue.ref(current()), planningFeedback = page.binding(Vue.computed, planningFeedbackNotice, record)
    const render = () => {
      const app = Vue.createSSRApp({
        setup: () => ({planningFeedback, busy: false, updateResearchFromFeedback: () => {throw new Error('unexpected replan')}}),
        render: page.render
      })
      app.component('el-button', {setup: (_props, {attrs, slots}) => () => Vue.h('button', attrs, slots.default?.())})
      return renderToString(app)
    }
    const pending = await render()
    assert.ok(pending.includes(pendingMessage), page.name)
    assert.ok(!pending.includes('journey-update-from-feedback'), page.name)
    record.value = cycle({context_status: 'legacy_unknown'})
    const legacy = await render()
    assert.ok(legacy.includes(legacyMessage), page.name)
    assert.ok(!legacy.includes('journey-update-from-feedback'), page.name)
    record.value = cycle({context_status: 'legacy_unknown', needs_update: true})
    const legacyUpdate = await render()
    assert.ok(legacyUpdate.includes(legacyMessage), page.name)
    assert.equal(legacyUpdate.includes('journey-update-from-feedback'), page.name === 'GeoJourney', page.name)
    record.value = cycle({context_status: 'outdated', needs_update: true})
    const outdated = await render()
    assert.ok(outdated.includes(outdatedMessage), page.name)
    assert.equal(outdated.includes('journey-update-from-feedback'), page.name === 'GeoJourney', page.name)
  }
})

test('both changed Vue views compile with their script bindings', () => {
  for (const page of pages) assert.deepEqual(compileTemplate({source: page.descriptor.template.content,
    filename: page.filename.pathname, id: page.name, compilerOptions: {bindingMetadata: page.script.bindings}}).errors, [])
})
