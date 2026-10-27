import assert from 'node:assert/strict'
import test from 'node:test'
import {updateFeedbackBusy} from '../src/utils/geoFeedbackBusy.js'
test('finishing or unmounting one feedback form cannot unlock another active save',()=>{
  let owners=updateFeedbackBusy(new Set(),{owner:'external-a',active:true})
  owners=updateFeedbackBusy(owners,{owner:'review-b',active:true})
  const stillBusy=updateFeedbackBusy(owners,{owner:'external-a',active:false})
  assert.deepEqual([...stillBusy],['review-b']);assert.equal(owners.size,2)
  assert.equal(updateFeedbackBusy(stillBusy,{owner:'review-b',active:false}).size,0)
  assert.equal(updateFeedbackBusy(stillBusy,{owner:'unknown',active:false}).size,1)
})
test('malformed busy events cannot clear the page task latch',()=>{
  const owners=new Set(['active'])
  for(const event of [null,{owner:'active',active:0},{owner:'',active:false},{owner:7,active:false}])assert.equal(updateFeedbackBusy(owners,event),owners)
})
