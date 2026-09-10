import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const apiSource = readFileSync(new URL('../src/api/visibility.js', import.meta.url), 'utf8')
const viewSource = readFileSync(new URL('../src/views/AiVisibility.vue', import.meta.url), 'utf8')


test('black-box measurement uses the truth-matrix and imported-panel contracts', () => {
  assert.match(apiSource, /http\.get\('\/api\/geo\/blackbox\/truth-matrix', undefined, config\)/)
  assert.match(apiSource, /http\.post\('\/api\/geo\/blackbox\/imported-panel', \{ snapshots \}, config\)/)
  assert.match(viewSource, /visibilityApi\.getBlackboxTruthMatrix\(\{ suppressGlobalError: true \}\)/)
  assert.match(viewSource, /visibilityApi\.evaluateBlackboxPanel\(snapshots, \{ suppressGlobalError: true \}\)/)
  assert.match(viewSource, /void loadBlackboxTruth\(\)/)
})


test('the panel requires baseline, control and matched day 7, 14 and 30 retests', () => {
  assert.match(viewSource, /MEASUREMENT \/ BLACK-BOX LOOP/)
  assert.match(viewSource, /Day 0 · 发布前/)
  assert.match(viewSource, /处理组 \/ 对照组/)
  assert.match(viewSource, /Day 7 \/ 14 \/ 30/)
  assert.match(viewSource, /处理变化 − 对照变化/)
  assert.match(viewSource, /retest_day_7_14_30: '7 \/ 14 \/ 30 天复测'/)
  assert.match(viewSource, /matched_control: '匹配对照组'/)
})


test('missing evidence stays unknown instead of becoming zero or a fabricated lift', () => {
  assert.match(viewSource, /缺少证据时显示“未测量”，不会自动补零或制造提升/)
  assert.match(viewSource, /NOT_MEASURED: '未测量'/)
  assert.match(viewSource, /UNOBSERVABLE_INTERNAL: '黑盒不可观测'/)
  assert.match(viewSource, /缺少槽位或采样口径不一致时不计算增长/)
  assert.match(viewSource, /它是描述性观察，不等于因果证明/)
  assert.match(viewSource, /缺失证据保持为阻塞项/)
  assert.doesNotMatch(viewSource, /缺少证据时[^\n]*(?:按|记为|视为)\s*0/)
})


test('import accepts only a bounded JSON snapshot panel and preserves backend evidence state', () => {
  assert.match(viewSource, /accept="application\/json,\.json"/)
  assert.match(viewSource, /file\.size > 8 \* 1024 \* 1024/)
  assert.match(viewSource, /const snapshots = Array\.isArray\(parsed\) \? parsed : parsed\?\.snapshots/)
  assert.match(viewSource, /if \(!Array\.isArray\(snapshots\)\) throw new Error/)
  assert.match(viewSource, /blackboxResult\.value = response\.data \|\| null/)
  assert.match(viewSource, /blackboxResult\.value\?\.truth_matrix/)
})
