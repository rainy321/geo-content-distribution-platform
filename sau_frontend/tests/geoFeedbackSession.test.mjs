import assert from 'node:assert/strict'
import test from 'node:test'
import fs from 'node:fs'
import {createFeedbackSession,feedbackCheckpointKey,feedbackSessionLimits} from '../src/utils/geoFeedbackSession.js'
const scope={cycleId:'cycle-a',projectId:4}

test('session checkpoint keys isolate project, cycle, observation and form kind',()=>{
  const keys=[feedbackCheckpointKey('external',scope),feedbackCheckpointKey('context',scope,'probe-a'),
    feedbackCheckpointKey('business',scope,'probe-a'),feedbackCheckpointKey('business',scope,'probe-b'),
    feedbackCheckpointKey('external',{...scope,projectId:5}),feedbackCheckpointKey('external',{...scope,cycleId:'cycle-b'})]
  assert.equal(new Set(keys).size,6)
  assert.notEqual(feedbackCheckpointKey('external',{...scope,projectId:null}),null)
  for(const args of [['arbitrary',scope],['business',scope],['external',{...scope,projectId:0}],['external',{...scope,cycleId:'../bad'}]])assert.equal(feedbackCheckpointKey(...args),null)
})

test('round-trip keeps exact raw whitespace, guards and unknown frozen request without shared references',()=>{
  const store=createFeedbackSession(),key=feedbackCheckpointKey('external',scope)
  const value={form:{raw:'  原文\n否定结论。  '},uncertain:true,frozen:{expected_revision:2,expected_plan_sha256:'a'.repeat(64),raw_response_text:'  原文\n否定结论。  '}}
  const saved=store.write(key,value);assert.equal(saved.ok,true)
  value.form.raw='changed'
  const restored=store.read(key);assert.equal(restored.value.form.raw,'  原文\n否定结论。  ')
  restored.value.frozen.expected_revision=999
  assert.equal(store.read(key).value.frozen.expected_revision,2)
  assert.equal(store.stats().limits.bytes,feedbackSessionLimits.bytes)
})

test('entry capacity never evicts an unconfirmed draft or unknown request',()=>{
  const store=createFeedbackSession({entries:1,bytes:10000})
  const first=store.write('one',{raw:'original',uncertain:true})
  assert.equal(store.write('two',{raw:'later'}).reason,'capacity')
  assert.deepEqual(store.read('one').value,{raw:'original',uncertain:true})
  assert.equal(store.stats().entries,1)
  assert.equal(store.confirmed('one',first.token),true)
  assert.equal(store.write('two',{raw:'later'}).ok,true)
})

test('UTF8 byte limit rejects a larger replacement atomically and preserves both existing entries',()=>{
  const store=createFeedbackSession({entries:4,bytes:100})
  const first=store.write('one',{raw:'first'});store.write('two',{raw:'second'})
  const before=store.stats()
  assert.equal(store.write('one',{raw:'字'.repeat(40)},first.token).reason,'capacity')
  assert.equal(store.read('one').value.raw,'first');assert.equal(store.read('two').value.raw,'second')
  assert.deepEqual(store.stats(),before)
})

test('late acknowledgements and failures cannot clear or overwrite a later checkpoint incarnation',()=>{
  const store=createFeedbackSession(),first=store.write('same',{raw:'first',uncertain:true})
  assert.equal(store.confirmed('same',first.token),true)
  const second=store.write('same',{raw:'second',uncertain:false})
  assert.notEqual(second.token,first.token)
  assert.equal(store.confirmed('same',first.token),false)
  assert.equal(store.write('same',{raw:'late failure'},first.token).reason,'stale')
  assert.equal(store.read('same').value.raw,'second')
  assert.equal(store.write('missing',{raw:'late failure'},first.token).reason,'stale')
})

test('different component instances have independent busy owners and drafts never use browser storage',()=>{
  const store=createFeedbackSession()
  assert.notEqual(store.owner('business'),store.owner('business'))
  const source=fs.readFileSync(new URL('../src/utils/geoFeedbackSession.js',import.meta.url),'utf8')
  assert.ok(!source.includes('localStorage'));assert.ok(!source.includes('sessionStorage'))
  assert.ok(!source.includes('fetch('));assert.ok(!source.includes('indexedDB'))
})

test('a mounted older editor cannot overwrite a newer checkpoint version under the same key',()=>{
  const store=createFeedbackSession(),first=store.write('same',{form:{quote:'first'}})
  const second=store.write('same',{form:{quote:'newer'}},first.token,first.version)
  assert.equal(second.ok,true)
  assert.equal(store.write('same',{form:{quote:'stale unmount'}},first.token,first.version).reason,'stale')
  assert.equal(store.read('same').value.form.quote,'newer')
})

test('late receipt or failure for a different frozen request cannot settle the current checkpoint',()=>{
  const store=createFeedbackSession(),old={review:{quote:'old'}},next={review:{quote:'new'}}
  const first=store.write('same',{frozen:old,uncertain:true})
  store.write('same',{frozen:next,uncertain:true},first.token,first.version)
  assert.equal(store.confirmed('same',first.token,old),false)
  assert.equal(store.settle('same',first.token,old,{frozen:old,uncertain:true}),false)
  assert.deepEqual(store.read('same').value.frozen,next)
  assert.equal(store.confirmed('same',first.token,next),true)
})
