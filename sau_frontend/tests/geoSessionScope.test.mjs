import assert from 'node:assert/strict'
import test from 'node:test'
import {normalizeSessionSlot,sessionScopeForRecord,sessionSlotForRecord,sessionSlotMatchesRecord} from '../src/utils/geoSessionScope.js'

test('session slots are strict and legacy omission remains primary',()=>{
  assert.equal(normalizeSessionSlot(),'primary')
  assert.equal(normalizeSessionSlot('secondary','consumer'),'secondary')
  assert.equal(normalizeSessionSlot('primary','work'),'primary')
  for(const [slot,mode] of [[null,'consumer'],['','consumer'],['other','consumer'],[2,'consumer'],['secondary','work'],['primary',null],['primary','auto']])assert.throws(()=>normalizeSessionSlot(slot,mode))
  assert.deepEqual(sessionScopeForRecord({execution:{client_mode:'work'}}),{})
  assert.equal(sessionSlotForRecord({execution:{client_mode:'work'}}),'primary')
})

test('stored account sessions come from execution or task results without changing profiles or questions',()=>{
  for(const value of [{session_slot:'secondary'},{options:{session_slot:'secondary',client_mode:'consumer'}},
    {execution:{session_slot:'secondary',client_mode:'consumer'}},{baseline:{execution:{session_slot:'secondary',profile_mode:'consumer',mode_policy:'available_web'}}},
    {result:{session_slot:'secondary'}},{result:{probe:{execution:{session_slot:'secondary',client_mode:'consumer'}}}}]){
    const before=JSON.stringify(value)
    assert.equal(sessionSlotForRecord(value),'secondary')
    assert.equal(sessionSlotMatchesRecord(value,'secondary'),true)
    assert.equal(sessionSlotMatchesRecord(value,'primary'),false)
    assert.equal(JSON.stringify(value),before)
  }
  assert.equal(sessionSlotForRecord({profile:{session_slot:'secondary'},plan_sha256:'a'.repeat(64)}),'primary')
  assert.equal(sessionSlotForRecord({baseline_source:'user_reported_external',execution:{session_slot:'secondary'}}),'primary')
})

test('conflicting slots or any observed work mode refuse secondary, including login and recovery metadata',()=>{
  for(const value of [{session_slot:null},{session_slot:'secondary',result:{session_slot:'primary'}},
    {session_slot:'secondary',result:{report:{execution:{profile_mode:'consumer',client_mode:'work'}}}},
    {session_slot:'secondary',recovery:{requested_client_mode:'work'}},
    {session_slot:'secondary',result:{observation:{client_mode:'work'}}},
    {session_slot:'secondary',result:{login:{observation:{actual_client_mode:'work'}}}},
    {session_slot:'secondary',result:{login_observation:{client_mode:'work'}}},
    {execution:{session_slot:'secondary',profile_mode:null}},
    {options:{session_slot:'secondary',client_mode:'invalid'}}]){
    assert.throws(()=>sessionSlotForRecord(value))
    assert.equal(sessionSlotMatchesRecord(value,'secondary'),false)
    assert.equal(sessionSlotMatchesRecord(value,'primary'),false)
  }
  assert.equal(sessionSlotForRecord({session_slot:'primary',result:{report:{execution:{profile_mode:'consumer',client_mode:'work'}}}}),'primary')
})

test('every nested profile and policy declaration is checked rather than trusting the first one',()=>{
  for(const record of [{session_slot:'primary',profile_mode:'consumer',result:{report:{execution:{profile_mode:'work'}}}},
    {session_slot:'primary',profile_mode:'consumer',mode_policy:'available_web',result:{report:{execution:{mode_policy:'force_consumer'}}}},
    {session_slot:'primary',profile_mode:'consumer',mode_policy:'available_web',result:{probe:{execution:{mode_policy:null}}}},
    {session_slot:'primary',mode_policy:'available_web'},
    {profile_mode:'consumer',result:{report:{execution:{profile_mode:null}}}},
    {profile_mode:'consumer',mode_policy:'available_web',result:{report:{execution:{mode_policy:'invalid'}}}}]){
    assert.throws(()=>sessionScopeForRecord(record))
    assert.equal(sessionSlotMatchesRecord(record,'primary'),false)
  }
  const valid={session_slot:'secondary',profile_mode:'consumer',mode_policy:'available_web',result:{report:{execution:{session_slot:'secondary',profile_mode:'consumer',mode_policy:'available_web'}}}}
  assert.deepEqual(sessionScopeForRecord(valid),{session_slot:'secondary',profile_mode:'consumer',mode_policy:'available_web'})
  assert.deepEqual(sessionScopeForRecord({execution:{profile_mode:'consumer',mode_policy:'available_web'}}),{})
})
