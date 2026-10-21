export const publicationReviewStates = Object.freeze([
  {value:'pending',label:'待审核'},
  {value:'rejected',label:'审核未通过'},
  {value:'publicly_visible',label:'公开可查看'},
  {value:'unknown',label:'暂不确定'}
])
const states = new Set(publicationReviewStates.map(item=>item.value))
const digest = value => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value)
const observationId = value => typeof value === 'string' && /^[a-f0-9]{32}$/.test(value)

export const publicationReviewArticles = cycle => (cycle?.drafts || []).flatMap(draft=>{
  const targetId=draft.provenance?.destination?.id
  const target=(cycle?.destinations || []).find(item=>item.id===targetId)
  if(!Number.isSafeInteger(draft.article_id) || draft.article_id<=0 || !target) return []
  return [{id:draft.article_id,title:String(draft.title || '未命名稿件'),targetId,
    targetName:String(target.config?.name || '目标平台'),articleSha256:draft.article_sha256,
    supported:digest(draft.article_sha256)}]
})

export const publicationReviewAvailable = cycle => Boolean(cycle?.id &&
  Array.isArray(cycle.moderation_observations) && Array.isArray(cycle.blocked_publications) &&
  publicationReviewArticles(cycle).some(item=>item.supported))

export const newPublicationObservationId = (crypto=globalThis.crypto) => {
  let value
  if(typeof crypto?.randomUUID==='function') value=crypto.randomUUID().replaceAll('-','')
  else if(typeof crypto?.getRandomValues==='function') {
    value=Array.from(crypto.getRandomValues(new Uint8Array(16)),item=>item.toString(16).padStart(2,'0')).join('')
  }
  if(!observationId(value)) throw new Error('当前窗口无法准备核对记录，请更新软件后重试。')
  return value
}

// Capture the article once when the operator opens or deliberately selects it.
// A later prop refresh must never replace this version behind the form.
export const createPublicationReviewSnapshot = (cycle,articleId,id=newPublicationObservationId()) => {
  if(!publicationReviewAvailable(cycle)) throw new Error('当前版本尚未提供稿件核对信息，请升级软件后使用。')
  const article=publicationReviewArticles(cycle).find(item=>item.id===articleId)
  if(!article?.supported || !observationId(id)) throw new Error('这篇稿件暂时无法登记，请刷新资料后重新核对。')
  return Object.freeze({cycleId:cycle.id,articleId:article.id,targetId:article.targetId,
    expectedArticleSha256:article.articleSha256,observationId:id})
}

export const publicationReviewSnapshotIssue = (snapshot,cycle) => {
  if(!snapshot || snapshot.cycleId!==cycle?.id) return '已切换品牌或本轮流程，请重新打开核对。'
  const article=publicationReviewArticles(cycle).find(item=>item.id===snapshot.articleId)
  if(!article || article.targetId!==snapshot.targetId) return '稿件或目标平台已变化，请关闭后重新打开核对。'
  if(!digest(article.articleSha256) || article.articleSha256!==snapshot.expectedArticleSha256) {
    return '当前稿件内容已更新，请关闭后重新打开，核对当前版本。'
  }
  return ''
}

export const publicationReviewUrl = value => {
  try {
    if(typeof value!=='string' || !value || /\s/.test(value)) return ''
    const url=new URL(value)
    if(url.protocol!=='https:' || url.username || url.password || (url.port && url.port!=='443')) return ''
    if(!url.hostname.includes('.') || url.hostname.startsWith('[') || /^\d+(\.\d+){3}$/.test(url.hostname) ||
      ['.local','.localhost','.internal','.test','.invalid'].some(suffix=>url.hostname.endsWith(suffix))) return ''
    return url.href
  } catch {return ''}
}

export const buildPublicationReviewPayload = (snapshot,form,cycle) => {
  const changed=publicationReviewSnapshotIssue(snapshot,cycle)
  if(changed) throw new Error(changed)
  if(!states.has(form?.state)) throw new Error('请选择当前在平台看到的状态。')
  if(form.confirmed!==true) throw new Error('请先在平台核对，再确认保存这条观察。')
  if(typeof form.note!=='string' || form.note.trim().length>400) throw new Error('平台原因或补充说明请控制在 400 字以内。')
  const url=typeof form.url==='string' ? form.url.trim() : ''
  if(url && !publicationReviewUrl(url)) throw new Error('请填写完整的公开 HTTPS 文章链接。')
  if(form.state==='publicly_visible' && (!url || form.contentConfirmed!==true)) {
    throw new Error('请核对公开文章的标题与全文，并确认与当前稿件一致。')
  }
  return Object.freeze({observation_id:snapshot.observationId,article_id:snapshot.articleId,
    target_id:snapshot.targetId,state:form.state,confirmed:true,note:form.note.trim(),
    expected_article_sha256:snapshot.expectedArticleSha256,...(url?{url}:{}),
    ...(form.state==='publicly_visible'?{content_confirmed:true}:{})})
}

// Reusing this frozen payload after an uncertain response keeps the same event.
// The call records an observation only; it never invokes a platform publisher.
export const savePublicationReviewAttempt = async (api,snapshot,payload) => {
  if(typeof api?.moderation!=='function') throw new Error('当前版本不支持平台结果登记，请升级软件。')
  const result=await api.moderation(snapshot.cycleId,payload)
  const cycle=result?.data
  const record=cycle?.moderation_observations?.find(item=>item.id===snapshot.observationId)
  if(cycle?.id!==snapshot.cycleId || !record || record.article_id!==snapshot.articleId || record.target_id!==snapshot.targetId ||
    record.state!==payload.state || record.article_sha256!==payload.expected_article_sha256) {
    throw new Error('保存结果尚未确认，请刷新查看；重试会沿用这次记录。')
  }
  return cycle
}

export const publicationReviewResultMatchesScope = (snapshot,currentSnapshot,currentCycle,resultCycle) =>
  Boolean(snapshot && snapshot===currentSnapshot && snapshot.cycleId===currentCycle?.id && resultCycle?.id===snapshot.cycleId &&
  !publicationReviewSnapshotIssue(snapshot,currentCycle) && !publicationReviewSnapshotIssue(snapshot,resultCycle))

export const publicationObservationViews = cycle => (cycle?.moderation_observations || []).flatMap(item=>{
  const article=publicationReviewArticles(cycle).find(draft=>draft.id===item.article_id && draft.targetId===item.target_id)
  if(!article) return []
  let state=states.has(item.state)?item.state:'unknown'
  if(state==='publicly_visible' && (item.source!=='operator_reported' || item.content_confirmed!==true || !publicationReviewUrl(item.url))) state='unknown'
  return [{id:item.id,articleId:article.id,title:article.title,targetName:article.targetName,state,
    label:publicationReviewStates.find(entry=>entry.value===state).label,
    sourceLabel:item.source==='operator_reported'?'运营方观察':'历史观察，来源待核对',
    note:typeof item.note==='string'?item.note:'',url:publicationReviewUrl(item.url),createdAt:item.created_at,
    olderContent:digest(item.article_sha256) && digest(article.articleSha256) && item.article_sha256!==article.articleSha256}]
})

export const publicationReviewError = error => {
  if([404,405,501].includes(error?.response?.status)) return '当前软件尚不支持平台结果登记，请升级后使用。'
  const message=error?.response?.data?.msg || error?.message || ''
  if(/sha256|hash|observation_id|expected_article|fingerprint|uuid/i.test(message)) return '稿件或核对记录已变化，请刷新资料后重新核对。'
  if(!message || /network error|failed to fetch|timeout|ECONN/i.test(message)) return '连接未完成，保存结果待确认；重试会沿用这次记录，也可以刷新查看。'
  return message
}
