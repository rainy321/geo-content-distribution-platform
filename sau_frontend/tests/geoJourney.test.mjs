import assert from 'node:assert/strict'
import test from 'node:test'
import { articleSnapshot, baselineEvidenceSummary, canRestoreArticleEdits, canRestoreFactEdits, entityProfileFields, journeyDraftLimits, journeyLengthReview, lastJourneyProject, normalizeWebsite, readJourney, reportEntityContext, restoreEntityDraftFields, sameBrand, targetDraftOutdated, websiteIdentity, writeJourney } from '../src/utils/geoJourney.js'

test('same-entity unsaved workspace fields survive migration defaults, including explicit clearing',()=>{
  const project={id:7,name:'产品A',entity_type:'product',website:'https://example.org/products/a',company:'',audience:'',aliases:[],official_urls:[]}
  const values={company:'迁移前草稿公司',audience:'未提交受众',aliases:['未提交别名'],official_urls:['https://api.example.org/a']}
  for(const evidence of [{project_id:7},{plan:{profile:project}},{report:{profile:project}}])assert.deepEqual(restoreEntityDraftFields(project,{...values,...evidence}),values)
  assert.deepEqual(restoreEntityDraftFields(project,values,7),values)
  const populated={...project,company:'已保存公司',audience:'已保存受众',aliases:['已保存别名'],official_urls:['https://example.org/docs/a']}
  const cleared={company:'',audience:'',aliases:[],official_urls:[]}
  assert.deepEqual(restoreEntityDraftFields(populated,{project_id:7,...cleared}),cleared)
  assert.deepEqual(restoreEntityDraftFields(populated,{project_id:7,company:''}),{...cleared,audience:populated.audience,aliases:populated.aliases,official_urls:populated.official_urls})
})

test('unbound or another entity workspace cannot seed profile drafts by name alone',()=>{
  const project={id:7,name:'同名对象',entity_type:'product',website:'https://example.org/products/a',company:'正确公司',audience:'本对象受众',aliases:['甲'],official_urls:['https://api.example.org/a']}
  const foreign={project_id:8,company:'别的对象草稿',audience:'别的受众',aliases:['乙'],official_urls:['https://api.example.org/b']}
  const expected={company:project.company,audience:project.audience,aliases:project.aliases,official_urls:project.official_urls}
  for(const evidence of [{},{plan:{profile:{...project,website:'https://example.org/products/b'}}},{report:{profile:{...project,entity_type:'company'}}}])assert.deepEqual(restoreEntityDraftFields(project,{...foreign,...evidence}),expected)
  assert.deepEqual(restoreEntityDraftFields(project,foreign,7),expected)
  assert.deepEqual(restoreEntityDraftFields(project,{...foreign,project_id:undefined,plan:{profile:{...project,website:'https://example.org/products/b'}}},7),expected)
  assert.deepEqual(restoreEntityDraftFields(undefined,{project_id:7,...foreign}),{company:'',audience:'',aliases:[],official_urls:[]})
})

test('same-name brands on different sites cannot share research', () => {
  const original = {name:'懂AI中转站',website:'https://tongyuye.com'}
  assert.equal(sameBrand(original, {...original,website:'https://another-company.com'}),false)
  assert.equal(sameBrand(original, {...original,website:''}),false)
  assert.equal(sameBrand(original, {name:'另一品牌',website:original.website}),false)
  assert.equal(sameBrand(original, {...original,website:'https://www.tongyuye.com/about'}),false)
  assert.equal(sameBrand(original, {...original,website:'https://tongyuye.com:443/'}),true)
})

test('company and same-host product paths never reuse another target plan or report',()=>{
  const product={name:'相同名称',entity_type:'product',website:'https://example.org/products/a/'}
  for(const changes of [{entity_type:'company'},{website:'https://example.org/'},{website:'https://example.org/products/ab'},
    {website:'https://example.org/products/b/'},{website:'https://www.example.org/products/a/'},
    {website:'http://example.org/products/a/'},{website:'https://example.org:444/products/a/'}]) {
    assert.equal(sameBrand(product,{...product,...changes}),false,JSON.stringify(changes))
  }
  const legacy={name:product.name,website:product.website}
  assert.equal(sameBrand(legacy,product),true)
  assert.equal(sameBrand(legacy,{...product,entity_type:'company'}),false)
  assert.equal(sameBrand({...product,name:''},{...product,name:''}),false)
})

test('entity URL identity aligns UTF-8, NFC, default ports and slash boundaries',()=>{
  assert.equal(websiteIdentity('https://example.org/产品/'),websiteIdentity('https://EXAMPLE.org:443/%e4%ba%a7%e5%93%81'))
  assert.equal(websiteIdentity('https://example.org/café/'),websiteIdentity('https://example.org/cafe%CC%81'))
  assert.equal(websiteIdentity('https://example.org/products/%61'),websiteIdentity('https://example.org/products/a/'))
  for(const url of ['https://example.org/products/a%2fb','https://example.org/products/a%5Cb',
    'https://example.org/%252F','https://example.org/%FF','https://example.org/%GG',
    'https://example.org/products/../b','https://example.org/products/%2e%2e/b','https://example.org:0/a',
    'https://example.org:/a','https://example.org/a\\b','https://example.org/a?','https://example.org/a#'])assert.equal(websiteIdentity(url),'',url)
})

test('profile fields preserve object-specific aliases, owner and official paths in API payloads',()=>{
  const fields=entityProfileFields({entity_type:'product',company:'运营公司',audience:' 开发者 ',
    aliases:' 产品A\n\n产品英文名\n产品A ',official_urls:'example.org/products/a/\nhttps://api.example.org/docs/a'})
  assert.deepEqual(fields,{entity_type:'product',company:'运营公司',audience:'开发者',aliases:['产品A','产品英文名'],
    official_urls:['https://example.org/products/a','https://api.example.org/docs/a']})
  assert.throws(()=>entityProfileFields({...fields,aliases:['运营公司']}),/别名不能填写运营公司/)
  assert.throws(()=>entityProfileFields({...fields,official_urls:['https://example.org/products/a?token=secret']}))
  assert.throws(()=>entityProfileFields({...fields,aliases:Array.from({length:21},(_,i)=>`别名${i}`)}),/20/)
  assert.throws(()=>entityProfileFields({...fields,official_urls:Array.from({length:11},(_,i)=>`https://example.org/products/${i}`)}),/10/)
  assert.equal(entityProfileFields({entity_type:'company',company:'公'.repeat(300),aliases:['😀'.repeat(120)]}).company.length,300)
  assert.throws(()=>entityProfileFields({...fields,company:'公'.repeat(301)}),/300/)
  assert.throws(()=>entityProfileFields({...fields,audience:'客'.repeat(301)}),/300/)
  assert.throws(()=>entityProfileFields({...fields,aliases:['别'.repeat(121)]}),/120/)
})

test('explicit product semantics separate owner and exact URL scope, while legacy stays visibly mixed',()=>{
  const profile={entity_type:'product',name:'产品A',website:'https://example.org/products/a/',company:'运营公司',official_urls:['https://api.example.org/a']}
  const strict=reportEntityContext({profile,evaluation_semantics:'target_entity_scoped_urls'})
  assert.equal(strict.legacy,false)
  assert.equal(strict.mentionLabel,'产品提及')
  assert.match(strict.note,/运营公司提及单独列出/)
  assert.match(strict.note,/不把整站页面/)
  assert.deepEqual(strict.urls,[profile.website,...profile.official_urls])
  assert.equal(reportEntityContext({profile:{...profile,entity_type:'company'}}).mentionLabel,'公司提及')
  for(const report of [{profile:{name:'旧档案',website:profile.website}},
    {profile,evaluation_semantics:'legacy_mixed_entity_hostwide'}]) {
    assert.equal(reportEntityContext(report).mentionLabel,'历史混合提及')
    assert.equal(reportEntityContext(report).legacy,true)
    assert.match(reportEntityContext(report).note,/旧报告.*不能按新的独立对象/)
  }
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
