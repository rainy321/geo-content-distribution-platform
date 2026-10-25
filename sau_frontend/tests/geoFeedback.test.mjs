import assert from 'node:assert/strict'
import test from 'node:test'
import {feedbackCollectionTotals,retestCollection} from '../src/utils/geoFeedback.js'

const stopped=()=>({collection:{schema_version:1,status:'stopped',stop_reason:'browser_error:TimeoutError',
  counts:{planned:3,evaluable:0,failed:1,pending:2,excluded_private:0,incomplete:0},issues:[
    {case_id:'q01',status:'failed',failure_reason:'browser_error:TimeoutError',failure_stage:null,question_submitted:null},
    {case_id:'q02',status:'pending'},{case_id:'q03',status:'pending'}]},
  comparison:{conditions_match:false,cohorts:[{paired_answers:0,change:null}]}})

test('stopped company-style collection is an unsuccessful attempt, with readable cause and no zero-exposure implication',()=>{
  const value=retestCollection(stopped())
  assert.equal(value.known,true)
  assert.equal(value.empty,true)
  assert.equal(value.hasAnswers,false)
  assert.match(value.title,/未取得/)
  assert.match(value.explanation,/尚不能比较/)
  assert.match(value.countsText,/有效 0\/3 题 · 失败 1 题 · 待测 2 题/)
  assert.match(value.nextStep,/浏览器等待超时/)
  assert.match(value.nextStep,/不会自动重发/)
  assert.equal(value.issues[0].stage,'')
  assert.equal(value.issues[1].status,'待测')
  assert.doesNotMatch(JSON.stringify(value),/零曝光|效果差|未发送|发送前/)
  assert.deepEqual(feedbackCollectionTotals([stopped()]),{attempts:1,withAnswers:0})
})

test('partial public answers count as collected while private and pending answers remain separate',()=>{
  const value=stopped()
  value.collection.status='completed_with_gaps'
  value.collection.counts={planned:3,evaluable:1,failed:0,pending:1,excluded_private:1,incomplete:0}
  value.collection.issues=[{case_id:'q02',status:'private_account_context',failure_reason:'private_account_context'}, {case_id:'q03',status:'pending'}]
  const parsed=retestCollection(value)
  assert.equal(parsed.empty,false)
  assert.equal(parsed.hasAnswers,true)
  assert.match(parsed.countsText,/内部资料隔离 1 题/)
  assert.match(parsed.issues[0].message,/已排除公开统计/)
  assert.deepEqual(feedbackCollectionTotals([stopped(),value]),{attempts:2,withAnswers:1})
})

test('actual saved stages are explained but unsupported codes and private paths never become UI explanations',()=>{
  const value=stopped()
  value.collection.issues=[{case_id:'q01',status:'failed',failure_reason:'browser_error:TimeoutError',failure_stage:'navigation'}]
  assert.equal(retestCollection(value).issues[0].stage,'打开豆包页面')
  value.collection.issues[0].failure_stage='submission_control'
  assert.equal(retestCollection(value).issues[0].stage,'核对发送按钮')
  assert.doesNotMatch(JSON.stringify(retestCollection(value)),/未发送/)
  for(const [reason,text] of [['submission_send_button_not_unique_not_submitted','发送按钮无法唯一确认'],['submission_send_button_not_ready_not_submitted','发送按钮尚未就绪']]){
    value.collection.issues[0].failure_reason=reason
    assert.match(retestCollection(value).issues[0].message,new RegExp(text))
    assert.doesNotMatch(retestCollection(value).issues[0].message,/未发送/)
  }
  value.collection.issues=[{case_id:'C:/private/account',status:'C:/private/account',failure_reason:'C:/private/account',failure_stage:'C:/private/account'}]
  assert.doesNotMatch(JSON.stringify(retestCollection(value)),/C:\/private/)
})

test('legacy feedback remains renderable without pretending a zero-pair attempt produced usable answers',()=>{
  const legacy={comparison:{cohorts:[{paired_answers:0}]}}
  assert.equal(retestCollection(legacy).known,false)
  assert.equal(retestCollection(legacy).empty,false)
  assert.equal(retestCollection(legacy).hasAnswers,false)
  assert.deepEqual(feedbackCollectionTotals([legacy]),{attempts:1,withAnswers:0})
  legacy.comparison.cohorts[0].paired_answers=1
  assert.equal(retestCollection(legacy).hasAnswers,true)
  assert.deepEqual(feedbackCollectionTotals(null),{attempts:0,withAnswers:0})
})

test('invalid or inconsistent collection counts never claim a successful round',()=>{
  for(const mutate of [v=>v.collection.counts.evaluable=1,v=>v.collection.counts.failed=-1,v=>v.collection.counts.pending='2',v=>v.collection.schema_version=2]){
    const value=stopped();mutate(value)
    assert.equal(retestCollection(value).known,false)
    assert.equal(retestCollection(value).hasAnswers,false)
  }
})
