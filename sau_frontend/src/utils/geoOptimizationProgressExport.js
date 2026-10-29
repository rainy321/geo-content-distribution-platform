const statusLabels=Object.freeze({
  observed_improvement:'用户记录回答改善',
  not_observed:'未观察到改善',
  preparation:'基础准备'
})
const escapeHtml=value=>value.replace(/[&<>"']/g,character=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[character]))
const text=value=>typeof value==='string'&&value.trim()?value:typeof value==='number'&&Number.isFinite(value)?String(value):'未知'
const field=value=>escapeHtml(text(value))
const count=value=>Number.isSafeInteger(value)&&value>=0?String(value):'未知'
const timestamp=(value,requireTimezone=false)=>{
  if(value instanceof Date)return Number.isFinite(value.getTime())?value.toISOString():'未知'
  return typeof value==='string'&&/^\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}(?::\d{2}(?:\.\d{1,9})?)?(?:Z|[+-]\d{2}:\d{2})?)?$/.test(value)
    &&(!requireTimezone||/(?:Z|[+-]\d{2}:\d{2})$/.test(value))
    &&Number.isFinite(Date.parse(value))?value:'未知'
}
const row=(label,value)=>`<div><dt>${label}</dt><dd>${field(value)}</dd></div>`
const hongKongTime=new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Hong_Kong',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23'})
const timeRow=(label,value)=>{
  const original=timestamp(value,true)
  if(original==='未知')return row(label,'未知')
  const parts=Object.fromEntries(hongKongTime.formatToParts(new Date(original)).map(part=>[part.type,part.value]))
  const readable=`${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute}:${parts.second}（香港时间 UTC+08:00）`
  return `<div><dt>${label}</dt><dd><time datetime="${escapeHtml(original)}">${escapeHtml(readable)}</time></dd></div>`
}
const entityType=value=>value==='company'?'公司':value==='product'?'产品':'未知'
const entityScope=value=>value===true?'当前对象记录':value===false?'历史对象记录（不计当前对象改善）':'范围待核对（不计当前对象改善）'
const basisLabels=Object.freeze({stored_at_record:'记录时保留的对象',derived_frozen_profile:'由原题范围核对',derived_unchanged_project:'由未变项目资料核对',unknown:'范围未知'})
const entityBasis=value=>Object.hasOwn(basisLabels,value)?basisLabels[value]:'范围未知'
const brief=(value,max)=>{
  if(typeof value!=='string'||!value.trim())return '未知'
  const characters=Array.from(value.replace(/\s+/g,' ').trim())
  return characters.slice(0,max).join('')+(characters.length>max?'…':'')
}
const evidence=(label,value)=>{
  const quote=typeof value?.quote==='string'&&value.quote.trim()?escapeHtml(value.quote):'未提供摘录'
  return `<section class="evidence"><h3>${label}</h3><pre class="excerpt"><span>${quote}</span></pre><dl>
${timeRow('录入时间',value?.recorded_at)}${timeRow('采集时间',value?.collected_at)}${row('来源（用户声明）',value?.source)}${row('采样条件（用户声明）',value?.context_note)}
</dl><details class="appendix"><summary>证据编号与原题范围</summary><dl>${row('证据 ID',value?.evidence_id)}${row('周期 ID',value?.cycle_id)}${row('原题 ID',value?.case_id)}</dl></details></section>`
}
const record=value=>`<li class="record"><article><h2>${field(value?.direction)} · ${Object.hasOwn(statusLabels,value?.status)?statusLabels[value.status]:'状态未知'}</h2><p class="action-summary">${field(value?.action_summary)}</p><dl class="details">
${timeRow('行动时间',value?.action_at)}${timeRow('记录时间',value?.recorded_at)}${row('对象范围',entityScope(value?.current_entity_match))}${row('记录对象',value?.entity?.name)}${row('记录官网（纯文本）',value?.entity?.website)}
</dl>${value?.before||value?.after?`<div class="evidence-pair">${evidence('之前的回答摘录',value?.before)}${evidence('之后的回答摘录',value?.after)}</div>`:''}<dl>${row('核对备注',value?.review_note)}</dl>
<details class="appendix"><summary>记录编号与对象依据</summary><dl>${row('记录 ID',value?.id)}${row('记录对象类型',entityType(value?.entity?.entity_type))}${row('对象依据',entityBasis(value?.entity?.basis))}</dl></details></article></li>`

// Deliberately select individual summary and excerpt fields. Full responses,
// account details and any future view-model fields are never serialized here.
export function optimizationProgressHtml(project,view,generatedAt){
  const records=Array.isArray(view?.records)?view.records:[]
  const archivedRecords=Array.isArray(view?.archived_records)?view.archived_records:[]
  const showArchive=archivedRecords.length||Number.isSafeInteger(view?.archive_summary?.historical)&&view.archive_summary.historical>0||Number.isSafeInteger(view?.archive_summary?.unknown)&&view.archive_summary.unknown>0
  const summary=view?.summary
  const known=Object.keys(statusLabels).every(key=>Number.isSafeInteger(summary?.[key])&&summary[key]>=0)
  const conclusion=known?`当前对象人工记录中，回答改善 ${summary.observed_improvement} 条，未观察到改善 ${summary.not_observed} 条，基础准备 ${summary.preparation} 条。`:'当前对象记录分类计数尚待核对。'
  const latestImprovement=known&&summary.observed_improvement>0?records.find(value=>value?.current_entity_match===true&&value.status==='observed_improvement'):null
  const nextStep=!known?'先核对当前记录和分类统计，再保留实际改动与原文摘录。':summary.observed_improvement>0?'继续同题检查稳定性，再用不点名的自然需求问题核对能否被发现。':summary.not_observed>0?'继续按原题记录后续回答，并核对采样条件与公开资料。':summary.preparation>0?'先核对准备事项和公开资料，再按原题记录实际回答。':'先记录实际改动，保留可核对的前后回答摘录，再继续同题观察。'
  return `<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'none'; style-src 'unsafe-inline'; img-src 'none'; font-src 'none'; connect-src 'none'; object-src 'none'; frame-src 'none'; base-uri 'none'; form-action 'none'">
<meta name="referrer" content="no-referrer"><title>${field(project?.name)} · 优化进展记录</title>
<style>
@page{size:A4;margin:16mm}*{box-sizing:border-box}body{margin:0;background:#f4f7f5;color:#173e3b;font:14px/1.7 system-ui,sans-serif}main{max-width:960px;margin:auto;padding:28px}h1{font-size:26px;margin:0 0 14px}h2{font-size:18px;margin:0 0 14px}h3{font-size:15px;margin:0 0 8px}header,.record{padding:22px;border:1px solid #dce6e2;border-radius:10px;background:#fff}dl{margin:0}dl>div{display:grid;grid-template-columns:140px minmax(0,1fr);gap:10px;margin:5px 0}dt{color:#63736f}dd{margin:0;white-space:pre-wrap;overflow-wrap:anywhere}.note{color:#63736f;margin:16px 0 0}.counts{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin:20px 0}.count{padding:14px;border:1px solid #dce6e2;border-radius:8px;background:#fff}.count strong{display:block;font-size:24px}.records{padding:0;margin:0;list-style:none;display:grid;gap:20px}.evidence-pair{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:18px;margin-top:20px}.evidence{min-width:0}.excerpt{font:inherit;white-space:pre-wrap;overflow-wrap:anywhere;background:#f4f7f5;border-left:3px solid #afd2c8;padding:12px;margin:0 0 12px}.evidence dl>div{grid-template-columns:110px minmax(0,1fr)}.empty{padding:22px;background:#fff;border:1px solid #dce6e2;border-radius:10px;color:#63736f}@media(max-width:640px){main{padding:14px}.counts,.evidence-pair{grid-template-columns:1fr}dl>div,.evidence dl>div{grid-template-columns:1fr;gap:0}}@media print{body{background:#fff;color:#111}main{max-width:none;padding:0}header,.record,.count{border-color:#bbb;border-radius:0}.record{break-inside:auto}h1,h2,h3{break-after:avoid}.evidence{break-inside:avoid}.excerpt{background:#fff}.note,dt{color:#444}}
.action-summary{white-space:pre-wrap;overflow-wrap:anywhere;margin:0 0 14px}.appendix{font-size:12px;color:#63736f;margin-top:12px}.appendix summary{cursor:pointer}main>section{margin-top:20px}@media print{.appendix{break-inside:avoid}}
</style></head><body><main>
<header><h1>优化进展记录</h1><dl>${row('项目',project?.name)}${row('项目 ID',project?.id)}${row('对象类型',entityType(project?.entity_type))}${row('官网（纯文本）',project?.website)}${timeRow('导出时间',generatedAt)}</dl>
<p class="note">以下用户记录和所选摘录仅供核对，不能证明改动导致回答改善。</p></header>
<section class="counts" aria-label="三类进展计数">${Object.entries(statusLabels).map(([key,label])=>`<div class="count">${label}<strong>${count(view?.summary?.[key])}</strong></div>`).join('')}</section>
<section class="empty" aria-label="当前结论与下一步建议"><h2>当前结论</h2><p>${escapeHtml(conclusion)}</p>${latestImprovement?`<p class="latest-improvement">最近一条当前对象改善记录：${escapeHtml(brief(latestImprovement.direction,80))}；${escapeHtml(brief(latestImprovement.action_summary,180))}</p>`:''}<h2>下一步建议</h2><p>${escapeHtml(nextStep)}</p></section>
<section aria-label="当前对象进展记录"><h2>当前对象进展记录</h2>${records.length?`<ol class="records">${records.map(record).join('')}</ol>`:'<p class="empty">当前对象暂无进展记录。</p>'}</section>
${showArchive?`<section aria-label="历史与范围待核对记录"><h2>历史与范围待核对记录（不计当前对象改善）</h2><p class="note">历史记录 ${count(view?.archive_summary?.historical)} 条 · 范围待核对 ${count(view?.archive_summary?.unknown)} 条</p>${archivedRecords.length?`<ol class="records">${archivedRecords.map(record).join('')}</ol>`:'<p class="empty">归档明细尚未提供。</p>'}</section>`:''}
</main></body></html>`
}

export function downloadOptimizationProgress(project,view){
  const html=optimizationProgressHtml(project,view,new Date())
  const href=URL.createObjectURL(new Blob([html],{type:'text/html;charset=utf-8'}))
  try{
    const link=document.createElement('a')
    const projectId=Number.isSafeInteger(project?.id)&&project.id>0?String(project.id):'project'
    link.href=href;link.download=`optimization-progress-${projectId}.html`;link.click()
  }finally{setTimeout(()=>URL.revokeObjectURL(href),1000)}
}
