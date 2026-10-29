import assert from 'node:assert/strict'
import fs from 'node:fs'
import test from 'node:test'
import {optimizationProgressHtml,downloadOptimizationProgress} from '../src/utils/geoOptimizationProgressExport.js'

const project={id:7,name:'合成公开服务有限公司',website:'https://example.org/company',entity_type:'company'}
const generatedAt='2026-10-29T15:30:00+08:00'
const excerpt='\n  原回答：尚未确认官网。\r\n来源只有名称；不能证明引用。  '
const evidence=(changes={})=>({evidence_id:'evidence-a',cycle_id:'cycle-a',case_id:'q01',quote:excerpt,
  source:'用户提供的手机回答',recorded_at:'2026-10-28T11:30:00+08:00',collected_at:'2026-10-28T09:00:00+08:00',context_note:'用户说明沿用原对话，首条条件未知。',...changes})
const record=(changes={})=>({id:'progress-a',recorded_at:'2026-10-29T10:00:00+08:00',action_at:'2026-10-28T12:00:00+08:00',
  entity:{name:project.name,website:project.website,entity_type:project.entity_type,basis:'stored_at_record'},current_entity_match:true,
  direction:'企业公开资料',action_summary:'补充官网说明。',status:'observed_improvement',review_note:'这次回答出现官网名称，引用仍待核对。',before:evidence(),after:evidence({evidence_id:'evidence-b',quote:'已找到官网名称，是否采用仍未知。'}),...changes})
const view=(changes={})=>({summary:{observed_improvement:1,not_observed:2,preparation:3},records:[record()],...changes})
const escape=value=>value.replace(/[&<>"']/g,character=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[character]))
const visibleHtml=html=>html.replace(/<head>[\s\S]*?<\/head>/,'')

test('a standalone printable document includes the exact project, explicit counts and evidence excerpt identities',()=>{
  const html=optimizationProgressHtml(project,view(),generatedAt)
  assert.match(html,/^<!doctype html>/);assert.match(html,/<html lang="zh-CN">/);assert.match(html,/<meta charset="utf-8">/)
  for(const value of [project.name,project.website,generatedAt,'evidence-a','evidence-b','cycle-a','q01','用户说明沿用原对话，首条条件未知。'])assert.ok(html.includes(value),value)
  assert.ok(html.includes(`<pre class="excerpt"><span>${escape(excerpt)}</span></pre>`),'leading newline and all excerpt whitespace remain intact')
  assert.match(html,/用户记录回答改善<strong>1<\/strong>/);assert.match(html,/未观察到改善<strong>2<\/strong>/);assert.match(html,/基础准备<strong>3<\/strong>/)
  assert.match(html,/来源（用户声明）/);assert.match(html,/不能证明改动导致回答改善/)
  assert.match(html,/@media print/);assert.match(html,/@page\{size:A4/)
})

test('record order is supplied by the caller and the three status values are translated without deriving a result',()=>{
  const values=view({summary:{observed_improvement:9,not_observed:8,preparation:7},records:[
    record({id:'last-time-first',status:'preparation',recorded_at:'2026-10-29T12:00:00+08:00'}),
    record({id:'first-time-last',status:'not_observed',recorded_at:'2026-10-27T12:00:00+08:00'}),record({id:'third',status:'observed_improvement'})]})
  const html=optimizationProgressHtml(project,values,generatedAt)
  assert.ok(html.indexOf('last-time-first')<html.indexOf('first-time-last'))
  assert.match(html,/<h2>企业公开资料 · 基础准备<\/h2>/);assert.match(html,/<h2>企业公开资料 · 未观察到改善<\/h2>/);assert.match(html,/<h2>企业公开资料 · 用户记录回答改善<\/h2>/)
  assert.match(html,/用户记录回答改善<strong>9<\/strong>/)
  assert.doesNotMatch(visibleHtml(html),/\d+(?:\.\d+)?%|提升率|改善率|提及率|已证明|确定导致/)
})

test('all string fields escape active tags, attribute quotes and HTML entities instead of introducing markup',()=>{
  const malicious=`<script>alert("x")</script><img src=x onerror='steal()'>&quot;`
  const evilEvidence={evidence_id:malicious,cycle_id:malicious,case_id:malicious,quote:malicious,source:malicious,collected_at:malicious,context_note:malicious}
  const html=optimizationProgressHtml({...project,name:malicious,website:malicious},view({records:[record({id:malicious,direction:malicious,action_summary:malicious,review_note:malicious,before:evilEvidence,after:evilEvidence})]}),malicious)
  assert.ok(html.includes(escape(malicious)))
  assert.doesNotMatch(html,/<script\b|<img\b|<iframe\b|<svg\b|<object\b|<form\b|<base\b|href\s*=/i)
  assert.match(html,/&lt;script&gt;alert\(&quot;x&quot;\)&lt;\/script&gt;/)
  assert.match(html,/&amp;quot;/);assert.match(html,/&#39;steal\(\)&#39;/)
})

test('website values are unlinked plain text even for active, credential or quote-injecting URLs',()=>{
  for(const website of ['javascript:alert(1)','data:text/html,<script>bad()</script>','file:///private/customer.html',
    'https://name:secret@example.org/','https://example.org/" onclick="bad()','https://example.org/company?x=1&y=2']){
    const html=optimizationProgressHtml({...project,website},view({records:[]}),generatedAt)
    assert.ok(html.includes(escape(website)));assert.doesNotMatch(html,/<a\b|href\s*=/i)
  }
})

test('only whitelisted summaries and excerpts are exported; raw answers, accounts, tokens and extra fields never leak',()=>{
  const secrets={raw:'PRIVATE_FULL_RESPONSE',raw_response_text:'PRIVATE_RAW_RESPONSE',account:'PRIVATE_ACCOUNT',token:'PRIVATE_TOKEN',
    password:'PRIVATE_PASSWORD',cookie:'PRIVATE_COOKIE',private_notes:'PRIVATE_INTERNAL_NOTES',extra:{quote:'PRIVATE_NESTED_QUOTE'}}
  const html=optimizationProgressHtml({...project,...secrets},view({...secrets,summary:{observed_improvement:1,not_observed:0,preparation:0,...secrets},
    records:[{...record(),...secrets,before:{...evidence(),...secrets},after:{...evidence(),...secrets}}]}),generatedAt)
  assert.doesNotMatch(html,/PRIVATE_[A-Z_]+/)
  assert.ok(html.includes(escape(excerpt)))
})

test('different customers receive independent documents without shared state or a previous customer identity',()=>{
  const first=optimizationProgressHtml(project,view(),generatedAt)
  const other={id:8,name:'另一合成产品',website:'https://other.example.net/product',entity_type:'product'}
  const second=optimizationProgressHtml(other,view({records:[record({id:'other-progress',entity:{name:other.name,website:other.website,entity_type:other.entity_type,basis:'另一对象快照'},before:evidence({cycle_id:'other-cycle'}),after:null})]}),generatedAt)
  assert.ok(first.includes(project.name));assert.ok(second.includes(other.name));assert.ok(second.includes(other.website))
  assert.doesNotMatch(second,/合成公开服务有限公司|https:\/\/example\.org\/company/)
  assert.equal(optimizationProgressHtml(project,view(),generatedAt),first)
})

test('missing, malformed and unavailable fields remain unknown and an empty list does not invent records or counts',()=>{
  const empty=optimizationProgressHtml(null,null,null)
  assert.match(empty,/暂无进展记录/);assert.equal((empty.match(/<strong>未知<\/strong>/g)||[]).length,3)
  const html=optimizationProgressHtml({entity_type:'unknown'},view({summary:{observed_improvement:-1,not_observed:NaN,preparation:1.5},
    records:[{id:'missing-fields',status:'__proto__',recorded_at:'not-a-date',action_at:null,before:null,after:{quote:'   ',collected_at:'not-a-date'}}]}),new Date('invalid'))
  assert.match(html,/状态未知/);assert.match(html,/未提供摘录/);assert.doesNotMatch(html,/not-a-date|Invalid Date|undefined|NaN|\[object Object\]/)
  assert.equal((html.match(/<strong>未知<\/strong>/g)||[]).length,3)
  for(const label of ['记录时间','行动时间','录入时间','采集时间','导出时间'])assert.ok(html.includes(`<dt>${label}</dt><dd>未知</dd>`))
})

test('generated timestamps are explicit, source dates are not replaced and the pure renderer leaves input objects unchanged',()=>{
  const value=view(),before=JSON.stringify(value),date=new Date('2026-10-29T07:30:00.000Z')
  const html=optimizationProgressHtml(project,value,date)
  assert.ok(html.includes(date.toISOString()));assert.ok(html.includes(value.records[0].recorded_at));assert.ok(html.includes(value.records[0].before.collected_at))
  assert.equal(JSON.stringify(value),before)
  const unknown=optimizationProgressHtml(project,value)
  assert.match(unknown,/<dt>导出时间<\/dt><dd>未知<\/dd>/)
  for(const value of ['2026-10-29','2026-10-29T15:30:00'])assert.match(optimizationProgressHtml(project,view(),value),/<dt>导出时间<\/dt><dd>未知<\/dd>/)
})

test('recording and collection times stay distinct and no recording timestamp fills an unknown collection time',()=>{
  const value=view({records:[record({before:evidence({collected_at:null}),after:evidence({recorded_at:'2026-10-29T08:00:00Z',collected_at:'2026-10-28T09:00:00Z'})})]})
  const html=optimizationProgressHtml(project,value,generatedAt)
  assert.match(html,/<dt>录入时间<\/dt><dd><time datetime="2026-10-28T11:30:00\+08:00">2026-10-28 11:30:00（香港时间 UTC\+08:00）<\/time><\/dd><\/div><div><dt>采集时间<\/dt><dd>未知<\/dd>/)
  assert.match(html,/<dt>录入时间<\/dt><dd><time datetime="2026-10-29T08:00:00Z">2026-10-29 16:00:00（香港时间 UTC\+08:00）<\/time><\/dd><\/div><div><dt>采集时间<\/dt><dd><time datetime="2026-10-28T09:00:00Z">2026-10-28 17:00:00（香港时间 UTC\+08:00）<\/time><\/dd>/)
})

test('server microsecond timestamps and one-to-nine fractional digits remain visible with their original timezone',()=>{
  for(const timestamp of ['2026-10-29T10:20:30.1Z','2026-10-29T10:20:30.123456+00:00','2026-10-29T10:20:30.123456789+08:00']){
    const html=optimizationProgressHtml(project,view({records:[record({recorded_at:timestamp,action_at:timestamp,
      before:evidence({recorded_at:timestamp,collected_at:timestamp}),after:null})]}),timestamp)
    for(const label of ['导出时间','记录时间','行动时间','录入时间','采集时间'])assert.ok(html.includes(`<dt>${label}</dt><dd><time datetime="${timestamp}">`),`${label} keeps ${timestamp}`)
    assert.ok(html.includes(timestamp.endsWith('+08:00')?'2026-10-29 10:20:30（香港时间 UTC+08:00）':'2026-10-29 18:20:30（香港时间 UTC+08:00）'))
  }
  const noTimezone=optimizationProgressHtml(project,view(),'2026-10-29T10:20:30.123456')
  assert.match(noTimezone,/<dt>导出时间<\/dt><dd>未知<\/dd>/)
})

test('conclusions state supplied manual counts and fixed next steps never promise an effect or use arbitrary advice fields',()=>{
  for(const [summary,next] of [[{observed_improvement:1,not_observed:2,preparation:3},'继续同题检查稳定性'],
    [{observed_improvement:0,not_observed:2,preparation:3},'继续按原题记录后续回答'],
    [{observed_improvement:0,not_observed:0,preparation:2},'先核对准备事项和公开资料'],
    [{observed_improvement:1,not_observed:0,preparation:0},'继续同题检查稳定性'],
    [{observed_improvement:0,not_observed:0,preparation:0},'先记录实际改动']]){
    const html=optimizationProgressHtml(project,view({summary,records:[],next_steps:['PRIVATE_FAKE_EFFECT_ADVICE']}),generatedAt)
    assert.match(html,/<h2>当前结论<\/h2>/);assert.match(html,/<h2>下一步建议<\/h2>/)
    assert.ok(html.includes(`回答改善 ${summary.observed_improvement} 条，未观察到改善 ${summary.not_observed} 条，基础准备 ${summary.preparation} 条`))
    assert.ok(html.includes(next));assert.doesNotMatch(visibleHtml(html),/PRIVATE_FAKE_EFFECT_ADVICE|\d+%|保证|独立核验成功|已证明/)
  }
})

test('current and archived entity sections retain each record identity without adding historical improvements to current counts',()=>{
  const current={...project,name:'更名后的当前公司',website:'https://example.org/current-company'}
  const historical=record({id:'historical-entry',entity:{name:'旧业务主体',website:'https://example.org/old-company',entity_type:'company',basis:'stored_at_record'},current_entity_match:false})
  const unknown=record({id:'unknown-entry',entity:null,current_entity_match:null,status:'preparation'})
  const html=optimizationProgressHtml(current,view({summary:{observed_improvement:1,not_observed:0,preparation:0},
    records:[record({id:'current-entry',entity:{name:current.name,website:current.website,entity_type:'company',basis:'stored_at_record'}})],
    archived_records:[historical,unknown],archive_summary:{historical:1,unknown:1}}),generatedAt)
  const currentSection=html.slice(html.indexOf('<section aria-label="当前对象进展记录">'),html.indexOf('<section aria-label="历史与范围待核对记录">'))
  const archive=html.slice(html.indexOf('<section aria-label="历史与范围待核对记录">'))
  assert.ok(currentSection.includes(current.name));assert.ok(currentSection.includes(current.website));assert.doesNotMatch(currentSection,/旧业务主体|old-company|historical-entry|unknown-entry/)
  assert.match(archive,/历史与范围待核对记录（不计当前对象改善）/);assert.match(archive,/历史记录 1 条 · 范围待核对 1 条/)
  assert.ok(archive.indexOf('historical-entry')<archive.indexOf('unknown-entry'))
  assert.match(archive,/<dt>记录对象<\/dt><dd>旧业务主体<\/dd>/);assert.ok(archive.includes('https://example.org/old-company'))
  assert.match(archive,/历史对象记录（不计当前对象改善）/);assert.match(archive,/范围待核对（不计当前对象改善）/)
  const unknownRecord=archive.match(/<li class="record">(?:(?!<li class="record">)[\s\S])*unknown-entry[\s\S]*?<\/li>/)?.[0]
  assert.ok(unknownRecord);assert.match(unknownRecord,/<dt>记录对象<\/dt><dd>未知<\/dd>/)
  assert.match(unknownRecord,/<dt>记录官网（纯文本）<\/dt><dd>未知<\/dd>/);assert.doesNotMatch(unknownRecord,/更名后的当前公司|current-company|旧业务主体/)
  assert.match(html,/用户记录回答改善<strong>1<\/strong>/);assert.match(html,/当前对象人工记录中，回答改善 1 条，未观察到改善 0 条，基础准备 0 条/)
})

test('archive-only reports keep current counts zero and never borrow a current identity for unknown historical scope',()=>{
  const html=optimizationProgressHtml(project,view({summary:{observed_improvement:0,not_observed:0,preparation:0},records:[],
    archived_records:[record({id:'legacy-unknown',entity:undefined,current_entity_match:null})],archive_summary:{historical:0,unknown:1}}),generatedAt)
  assert.match(html,/当前对象暂无进展记录/);assert.match(html,/用户记录回答改善<strong>0<\/strong>/)
  assert.match(html,/当前对象人工记录中，回答改善 0 条，未观察到改善 0 条，基础准备 0 条/)
  const archive=html.slice(html.indexOf('<section aria-label="历史与范围待核对记录">'))
  assert.match(archive,/<dt>记录对象<\/dt><dd>未知<\/dd>/);assert.match(archive,/<dt>对象依据<\/dt><dd>范围未知<\/dd>/)
  assert.doesNotMatch(archive,/合成公开服务有限公司|https:\/\/example\.org\/company/)
})

test('archive entity fields remain an escaped whitelist and missing archive counts stay unknown',()=>{
  const malicious='<img src=x onerror="bad()">&\'archive'
  const html=optimizationProgressHtml(project,view({records:[],archived_records:[record({current_entity_match:false,
    entity:{name:malicious,website:'javascript:alert(1)',entity_type:'company',basis:malicious,token:'PRIVATE_ENTITY_TOKEN',raw:'PRIVATE_ENTITY_RAW'},
    raw:'PRIVATE_ARCHIVE_RESPONSE',token:'PRIVATE_ARCHIVE_TOKEN',before:{...evidence(),raw:'PRIVATE_ARCHIVE_EVIDENCE'}})]}),generatedAt)
  const archive=html.slice(html.indexOf('<section aria-label="历史与范围待核对记录">'))
  assert.ok(archive.includes(escape(malicious)));assert.match(archive,/历史记录 未知 条 · 范围待核对 未知 条/)
  assert.doesNotMatch(archive,/<img\b|<a\b|href\s*=|PRIVATE_[A-Z_]+/)
  assert.ok(archive.includes(escape(excerpt)))
})

test('record headings lead with direction and stage while changes and Hong Kong times precede quotations and technical appendices',()=>{
  const html=optimizationProgressHtml(project,view({records:[record({id:'technical-record-id',direction:'官网身份核对',
    action_summary:'明确这次改动的合成摘要。',before:evidence({evidence_id:'a'.repeat(64)})})]}),generatedAt)
  const section=html.slice(html.indexOf('<section aria-label="当前对象进展记录">'))
  assert.match(section,/<h2>官网身份核对 · 用户记录回答改善<\/h2>/);assert.doesNotMatch(section,/<h2>记录 \d|<h2>technical-record-id/)
  assert.ok(section.indexOf('明确这次改动的合成摘要。')<section.indexOf('之前的回答摘录'))
  assert.ok(section.indexOf('行动时间')<section.indexOf('之前的回答摘录'))
  assert.ok(section.indexOf('technical-record-id')>section.indexOf('之前的回答摘录'))
  assert.match(section,/<details class="appendix"><summary>记录编号与对象依据<\/summary>[\s\S]*technical-record-id/)
  assert.match(section,/<details class="appendix"><summary>证据编号与原题范围<\/summary>[\s\S]*a{64}/)
  assert.match(html,/<time datetime="2026-10-29T15:30:00\+08:00">2026-10-29 15:30:00（香港时间 UTC\+08:00）<\/time>/)
})

test('identity basis enums render as plain Chinese labels in current and archived records',()=>{
  const labels={stored_at_record:'记录时保留的对象',derived_frozen_profile:'由原题范围核对',derived_unchanged_project:'由未变项目资料核对',unknown:'范围未知'}
  for(const [basis,label] of Object.entries(labels)){
    const html=optimizationProgressHtml(project,view({records:[record({entity:{name:project.name,website:project.website,entity_type:'company',basis}})]}),generatedAt)
    assert.match(html,new RegExp(`<dt>对象依据</dt><dd>${label}</dd>`));assert.ok(!html.includes(basis))
  }
})

test('the latest current improvement summary is bounded and escaped and archive observations never become the latest result',()=>{
  const direction='<script>方向</script>',summary='第一行\n第二行 '+ '长'.repeat(220)+'PRIVATE_TAIL_OUTSIDE_EXCERPT'
  const html=optimizationProgressHtml(project,view({summary:{observed_improvement:2,not_observed:1,preparation:1},
    records:[record({status:'preparation',direction:'准备先显示'}),record({direction,action_summary:summary}),record({direction:'较旧的当前改善方向'})],
    archived_records:[record({direction:'旧实体改善不能当最新',current_entity_match:false})],archive_summary:{historical:1,unknown:0}}),generatedAt)
  const conclusion=html.slice(html.indexOf('<section class="empty" aria-label="当前结论与下一步建议">'),html.indexOf('<section aria-label="当前对象进展记录">'))
  assert.ok(conclusion.includes(escape(direction)));assert.match(conclusion,/第一行 第二行/)
  assert.doesNotMatch(conclusion,/<script>|PRIVATE_TAIL_OUTSIDE_EXCERPT|较旧的当前改善方向|旧实体改善不能当最新/)
  assert.match(conclusion,/继续同题检查稳定性，再用不点名的自然需求问题核对能否被发现/)
  const archiveOnly=optimizationProgressHtml(project,view({summary:{observed_improvement:0,not_observed:0,preparation:0},records:[],
    archived_records:[record({direction:'归档改善',current_entity_match:false})],archive_summary:{historical:1,unknown:0}}),generatedAt)
  assert.doesNotMatch(archiveOnly,/class="latest-improvement"/)
})

test('preparation without linked answers omits empty evidence columns while retaining its actual work and timing',()=>{
  const preparation=record({status:'preparation',direction:'官网公开资料',action_summary:'补充可见公司介绍。',before:null,after:null})
  const html=optimizationProgressHtml(project,view({records:[preparation]}),generatedAt)
  assert.ok(html.includes('补充可见公司介绍。'));assert.ok(html.includes('行动时间'));assert.ok(html.includes('记录编号与对象依据'))
  assert.doesNotMatch(html,/<div class="evidence-pair">|未提供摘录|之前的回答摘录|之后的回答摘录|来源（用户声明）/)
  const compared=optimizationProgressHtml(project,view({records:[record({before:evidence(),after:evidence()})]}),generatedAt)
  assert.match(compared,/<div class="evidence-pair">/);assert.match(compared,/之前的回答摘录/);assert.match(compared,/之后的回答摘录/)
})

test('CSP forbids scripts and external resources and the document needs no storage, network or external stylesheet',()=>{
  const html=optimizationProgressHtml(project,view(),generatedAt)
  assert.match(html,/<meta http-equiv="Content-Security-Policy"/)
  for(const directive of ["default-src 'none'","script-src 'none'","connect-src 'none'","img-src 'none'","font-src 'none'","base-uri 'none'","form-action 'none'"])assert.ok(html.includes(directive))
  assert.doesNotMatch(html,/<script\b|<link\b|<iframe\b|\son\w+=|url\s*\(/i)
  const source=fs.readFileSync(new URL('../src/utils/geoOptimizationProgressExport.js',import.meta.url),'utf8')
  assert.doesNotMatch(source,/localStorage|sessionStorage|indexedDB|fetch\s*\(|XMLHttpRequest|JSON\.stringify\(view/)
})

test('downloading creates one independent HTML blob, clicks only a generated blob URL and releases it',async t=>{
  const blobs=[],links=[],revoked=[],timers=[]
  t.mock.method(URL,'createObjectURL',blob=>{blobs.push(blob);return 'blob:synthetic-export'})
  t.mock.method(URL,'revokeObjectURL',value=>revoked.push(value))
  t.mock.method(globalThis,'setTimeout',(callback,delay)=>{timers.push({callback,delay});return 1})
  const original=Object.getOwnPropertyDescriptor(globalThis,'document')
  Object.defineProperty(globalThis,'document',{configurable:true,value:{createElement:tag=>{assert.equal(tag,'a');const link={click(){link.clicked=true}};links.push(link);return link}}})
  t.after(()=>{if(original)Object.defineProperty(globalThis,'document',original);else delete globalThis.document})
  downloadOptimizationProgress(project,view())
  assert.equal(blobs.length,1);assert.equal(blobs[0].type,'text/html;charset=utf-8')
  assert.equal(links[0].href,'blob:synthetic-export');assert.equal(links[0].download,'optimization-progress-7.html');assert.equal(links[0].clicked,true)
  const html=await blobs[0].text();assert.ok(html.includes(project.name));assert.match(html,/<dt>导出时间<\/dt><dd><time datetime="\d{4}-/)
  assert.deepEqual(revoked,[]);assert.equal(timers[0].delay,1000);timers[0].callback();assert.deepEqual(revoked,['blob:synthetic-export'])
})

test('failed download clicks still release their blob and untrusted project IDs never become file paths',t=>{
  const callbacks=[],revoked=[],links=[]
  t.mock.method(URL,'createObjectURL',()=> 'blob:failed-export');t.mock.method(URL,'revokeObjectURL',value=>revoked.push(value))
  t.mock.method(globalThis,'setTimeout',callback=>{callbacks.push(callback);return 1})
  const original=Object.getOwnPropertyDescriptor(globalThis,'document')
  Object.defineProperty(globalThis,'document',{configurable:true,value:{createElement:()=>{const link={click(){throw Error('synthetic click failure')}};links.push(link);return link}}})
  t.after(()=>{if(original)Object.defineProperty(globalThis,'document',original);else delete globalThis.document})
  assert.throws(()=>downloadOptimizationProgress({...project,id:'../../other-client'},view()),/synthetic click failure/)
  assert.equal(links[0].download,'optimization-progress-project.html');callbacks[0]();assert.deepEqual(revoked,['blob:failed-export'])
})
