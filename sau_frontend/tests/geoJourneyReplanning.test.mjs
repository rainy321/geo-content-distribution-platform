import assert from 'node:assert/strict'
import fs from 'node:fs'
import test from 'node:test'
import {sameResearchBrief,articleSnapshot} from '../src/utils/geoJourney.js'
const source=fs.readFileSync(new URL('../src/views/GeoJourney.vue',import.meta.url),'utf8')
const expression=source.slice(source.indexOf('const applyCycle=')+'const applyCycle='.length,source.indexOf('\nconst refreshCycle='))
const priorBrief={id:'b01',topic:'原主题'},nextBrief={id:'b01',topic:'新主题'}
const article=(id,brief,changes={})=>({article_id:id,platform:'owned_web',brief_id:'b01',title:`稿件${id}`,content:`已保存正文${id}`,provenance:{brief},...changes})
function harness(){
  const old=article(1,priorBrief),cycle={value:{id:'cycle-a',project_id:7,analysis:{briefs:[priorBrief]},drafts:[old],facts:[]}}
  const cycleId={value:'cycle-a'},briefId={value:'b01'},platform={value:'owned_web'},articleId={value:1}
  const matchingDrafts={get value(){return cycle.value.drafts.filter(item=>item.platform===platform.value&&item.brief_id===briefId.value&&sameResearchBrief(item,cycle.value.analysis.briefs.find(brief=>brief.id===briefId.value)))}}
  const activeDraft={get value(){return matchingDrafts.value.find(item=>item.article_id===articleId.value)||null}}
  const editor={title:'旧稿本地未保存标题',content:'旧稿本地未保存正文',tags:[]},recoveryDraft={value:null},error={value:''},factRows={value:[]}
  const loads=[]
  const names={projectId:{value:7},project:{value:{}},sameBrand:()=>true,sameResearchBrief,activeDraft,editorDirty:{value:true},editor,cycle,cycleId,briefId,platform,articleId,articleSnapshot,recoveryDraft,error,matchingDrafts,
    loadEditor:()=>{loads.push(articleId.value);if(activeDraft.value)Object.assign(editor,articleSnapshot(activeDraft.value))},factRows}
  const apply=Function(...Object.keys(names),`return (${expression})`)(...Object.values(names))
  return {...names,loads,old,apply}
}
const incoming=(brief,drafts)=>({id:'cycle-a',project_id:7,profile:{},analysis:{briefs:[brief]},drafts,facts:[],channels:{owned_web:'官网'}})
test('same brief ID after replanning cannot bind old local edits to the new article',()=>{
  const page=harness();page.apply(incoming(nextBrief,[page.old,article(2,nextBrief)]),{keepEditor:true})
  assert.equal(page.articleId.value,2);assert.equal(page.editor.title,'稿件2')
  assert.equal(page.recoveryDraft.value.article_id,1);assert.equal(page.recoveryDraft.value.editor.title,'旧稿本地未保存标题')
  assert.deepEqual(page.loads,[2])
})
test('unchanged article and exact brief preserve the local edit during a feedback-only refresh',()=>{
  const page=harness();page.apply(incoming(priorBrief,[page.old]),{keepEditor:true})
  assert.equal(page.articleId.value,1);assert.equal(page.editor.title,'旧稿本地未保存标题')
  assert.equal(page.recoveryDraft.value,null);assert.deepEqual(page.loads,[])
})
test('changed server article or a new brief without a draft preserve edits only as an attributed recovery copy',()=>{
  const page=harness();page.apply(incoming(priorBrief,[article(1,priorBrief,{title:'服务端新标题'})]),{keepEditor:true})
  assert.equal(page.editor.title,'服务端新标题');assert.equal(page.recoveryDraft.value.article_id,1)
  const noDraft=harness();noDraft.apply(incoming(nextBrief,[noDraft.old]),{keepEditor:true})
  assert.equal(noDraft.articleId.value,null);assert.equal(noDraft.activeDraft.value,null)
  assert.equal(noDraft.recoveryDraft.value.editor.title,'旧稿本地未保存标题')
})

test('q02 archival cannot bind saved article14 to b07 and preserves unrelated article13 local edits',()=>{
  const page=harness()
  const brief13={id:'b04',topic:'q01原选题',case_ids:['q01']},brief14={id:'b02',topic:'q02原选题',case_ids:['q02']}
  const saved13=article(13,brief13,{brief_id:'b04'}),saved14=article(14,brief14,{brief_id:'b02'})
  page.cycle.value={...page.cycle.value,analysis:{briefs:[brief13,brief14]},drafts:[saved13,saved14]}
  page.briefId.value='b04';page.articleId.value=13
  const next={...page.cycle.value,profile:{},channels:{owned_web:'官网'},analysis:{method:'mixed_scope_planning',
    briefs:[brief13,{id:'b07',topic:'q02新选题',case_ids:['q02']}],history:[{analysis:{briefs:[brief13,brief14]}}]}}
  page.apply(next,{keepEditor:true})
  assert.equal(page.articleId.value,13);assert.equal(page.briefId.value,'b04')
  assert.equal(page.editor.title,'旧稿本地未保存标题');assert.equal(page.recoveryDraft.value,null);assert.deepEqual(page.loads,[])
  page.briefId.value='b07'
  assert.deepEqual(page.matchingDrafts.value,[])
  assert.equal(page.cycle.value.drafts.find(row=>row.article_id===14).provenance.brief.id,'b02')
})
