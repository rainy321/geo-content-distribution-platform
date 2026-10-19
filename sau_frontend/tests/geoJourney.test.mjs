import assert from 'node:assert/strict'
import test from 'node:test'
import { articleSnapshot, baselineEvidenceSummary, canRestoreArticleEdits, canRestoreFactEdits, journeyDraftLimits, journeyLengthReview, lastJourneyProject, normalizeWebsite, readJourney, sameBrand, targetDraftOutdated, writeJourney } from '../src/utils/geoJourney.js'

test('same-name brands on different sites cannot share research', () => {
  const original = {name:'懂AI中转站',website:'https://tongyuye.com'}
  assert.equal(sameBrand(original, {...original,website:'https://another-company.com'}),false)
  assert.equal(sameBrand(original, {...original,website:''}),false)
  assert.equal(sameBrand(original, {name:'另一品牌',website:original.website}),false)
  assert.equal(sameBrand(original, {...original,website:'https://www.tongyuye.com/about'}),true)
})

test('brand website cannot contain credentials, query data or script schemes', () => {
  assert.equal(normalizeWebsite('tongyuye.com'),'https://tongyuye.com')
  for(const value of ['https://person:secret@example.com','https://example.com/?key=secret','https://example.com/#token','javascript:alert(1)','file:///tmp/brand']) {
    assert.throws(()=>normalizeWebsite(value))
  }
})

test('journey progress stays isolated by project and corrupt storage is recoverable', () => {
  const values=new Map()
  globalThis.localStorage={getItem:key=>values.get(key)??null,setItem:(key,value)=>values.set(key,value)}
  writeJourney(3,{cycleId:'client-a',company:'A',step:2})
  writeJourney(7,{cycleId:'client-b',company:'B',step:1})
  assert.equal(readJourney(3).cycleId,'client-a')
  assert.equal(readJourney(7).cycleId,'client-b')
  assert.equal(lastJourneyProject(),7)
  values.set('geo-journey:v1:3','{incomplete')
  assert.deepEqual(readJourney(3),{})
  writeJourney(-1,{cycleId:'invalid'})
  assert.equal(lastJourneyProject(),7)
})

test('local edits cannot overwrite an article changed or published in another page', () => {
  const draft={article_id:2,platform:'zhihu',brief_id:'brief-1',status:'draft',title:'原稿',content:'已保存正文',tags:[]}
  const saved={editorDirty:true,editor:{title:'本机未保存标题'},editorBase:articleSnapshot(draft),cycleId:'cycle-a',articleId:2,platform:'zhihu',briefId:'brief-1'}
  assert.equal(canRestoreArticleEdits(saved,draft,'cycle-a'),true)
  assert.equal(canRestoreArticleEdits({...saved,editorDirty:false},draft,'cycle-a'),false)
  assert.equal(canRestoreArticleEdits(saved,{...draft,content:'其他页面新正文'},'cycle-a'),false)
  assert.equal(canRestoreArticleEdits(saved,{...draft,status:'published'},'cycle-a'),false)
  assert.equal(canRestoreArticleEdits(saved,draft,'different-cycle'),false)
  assert.equal(canRestoreArticleEdits(saved,{...draft,article_id:3},'cycle-a'),false)
})

test('local fact edits only restore against the unchanged server baseline', () => {
  const facts=[{text:'运营方确认',source_url:'',confirmed:true}]
  const saved={cycleId:'cycle-a',factsBase:facts,factRows:[{text:'未保存补充',source_url:'',confirmed:false}]}
  assert.equal(canRestoreFactEdits(saved,{id:'cycle-a',facts}),true)
  assert.equal(canRestoreFactEdits(saved,{id:'cycle-a',facts:[...facts,{text:'其他页面新事实'}]}),false)
  assert.equal(canRestoreFactEdits(saved,{id:'cycle-b',facts}),false)
})

test('new target copy is offered only for a known revision or configuration mismatch', () => {
  const target={id:'target-a',key:'target_target-a',revision:2,config_sha256:'current'}
  const draft={platform:target.key,provenance:{destination:{id:target.id,revision:2},destination_config_sha256:'current'}}
  assert.equal(targetDraftOutdated(draft,target),false)
  assert.equal(targetDraftOutdated({...draft,provenance:{...draft.provenance,destination:{id:target.id,revision:1}}},target),true)
  assert.equal(targetDraftOutdated({...draft,provenance:{...draft.provenance,destination_config_sha256:'old'}},target),true)
  assert.equal(targetDraftOutdated({...draft,platform:'toutiao'},target),false)
  assert.equal(targetDraftOutdated({...draft,provenance:{}},target),false)
})

test('review uses the stricter configured or known adapter limit without truncating text', () => {
  const limits=journeyDraftLimits({config:{title_limit:100,body_limit:150}},'toutiao')
  assert.deepEqual(limits,{title:30,body:150})
  assert.equal(journeyDraftLimits({config:{title_limit:20}},'toutiao').title,20)
  assert.equal(journeyDraftLimits(null,'sohu').body,null)
  const editor={title:'中'.repeat(31),content:'😀'.repeat(151)},before=JSON.stringify(editor)
  const result=journeyLengthReview(editor,limits)
  assert.equal(result.titleCount,31)
  assert.equal(result.bodyCount,151)
  assert.match(result.titleIssue,/31.*30/)
  assert.match(result.bodyIssue,/151.*150/)
  assert.equal(JSON.stringify(editor),before)
})

test('historical answer counts exclude failed and incomplete cases; later source review retains its own time', () => {
  const summary=baselineEvidenceSummary({cases:[
    {id:'q01',status:'success',assessment:{answer_evaluable:true},collected_at:'old-answer',source_reinspection:{inspected_at:'later-sources',original_answer_timestamp:'old-answer',source_collection_status:'partial'}},
    {id:'q02',status:'deferred_answer',assessment:{answer_evaluable:false}},
    {id:'q03',status:'failed',assessment:{answer_evaluable:true}},
  ]})
  assert.deepEqual({...summary,inspections:[]},{known:true,total:3,effective:1,failed:1,otherExcluded:1,inspections:[]})
  assert.deepEqual(summary.inspections,[{caseId:'q01',inspectedAt:'later-sources',originalAt:'old-answer',partial:true}])
  assert.equal(baselineEvidenceSummary({}).known,false)
})
