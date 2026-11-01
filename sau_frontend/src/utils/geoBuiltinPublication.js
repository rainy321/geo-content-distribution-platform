const positiveId = value => Number.isSafeInteger(value) && value > 0
const digest = value => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value)
const ready = value => ['ready','published'].includes(value)
const attempted = job => positiveId(job?.id) && job.platform==='zhihu' &&
  (job.demo===false || job.demo===0) && ['failed','need_action'].includes(job.status)
const jobView = job => JSON.stringify([job.id,job.platform,job.status,job.demo,job.result_url,job.finished_at])
const articleView = draft => JSON.stringify([draft.article_id,draft.platform,draft.title,draft.content,draft.tags,draft.status,draft.article_sha256])

export const builtinPublicationArticles = cycle => (cycle?.drafts || []).flatMap(draft=>{
  if(!positiveId(draft.article_id) || draft.platform!=='zhihu' || !ready(draft.status))return []
  const jobs=(draft.jobs || []).filter(attempted).map(job=>({id:job.id,status:job.status,
    label:`原任务 #${job.id} · ${job.status==='need_action'?'待处理':'执行未完成'}`}))
  return [{id:draft.article_id,title:String(draft.title || '未命名稿件'),jobs,
    supported:digest(draft.article_sha256) && jobs.length>0}]
})

export const builtinPublicationScope = cycle => JSON.stringify([cycle?.id,cycle?.project_id])

// Capture before any read request, so a refreshed or switched view cannot become
// the implicit confirmation for an earlier selection.
export const captureBuiltinPublicationSelection = (cycle,articleId,jobId) => {
  const draft=cycle?.drafts?.find(item=>item.article_id===articleId)
  const job=draft?.jobs?.find(item=>item.id===jobId)
  if(!cycle?.id || !positiveId(cycle.project_id) || !builtinPublicationArticles(cycle).some(item=>
    item.id===articleId && item.supported && item.jobs.some(job=>job.id===jobId))) {
    throw new Error('请选择本轮已就绪的知乎稿件及已尝试的真实原任务。')
  }
  return Object.freeze({cycleId:cycle.id,projectId:cycle.project_id,articleId,jobId,
    articleView:articleView(draft),jobView:jobView(job),expectedArticleSha256:draft.article_sha256})
}

export const builtinPublicationSnapshotIssue = (snapshot,cycle) => {
  if(!snapshot || snapshot.cycleId!==cycle?.id || snapshot.projectId!==cycle?.project_id) {
    return '已切换品牌或本轮流程，请重新打开登记。'
  }
  const draft=cycle.drafts?.find(item=>item.article_id===snapshot.articleId)
  const job=draft?.jobs?.find(item=>item.id===snapshot.jobId)
  if(!draft || !job || articleView(draft)!==snapshot.articleView || jobView(job)!==snapshot.jobView) {
    return '稿件或原任务已变化，请关闭后重新核对当前版本。'
  }
  return ''
}

export const createBuiltinPublicationSnapshot = (selection,cycle,article,job) => {
  const issue=builtinPublicationSnapshotIssue(selection,cycle)
  if(issue)throw new Error(issue)
  const draft=cycle.drafts.find(item=>item.article_id===selection.articleId)
  if(article?.id!==selection.articleId || article.project_id!==selection.projectId || !ready(article.status) ||
    article.status!==draft.status || article.title!==draft.title || article.content!==draft.content ||
    JSON.stringify(article.tags)!==JSON.stringify(draft.tags) || !digest(article.review_fingerprint)) {
    throw new Error('稿件核对信息已变化，请刷新资料并重新核对。')
  }
  if(job?.job_id!==selection.jobId || job.article_id!==selection.articleId || job.platform!=='zhihu' ||
    job.demo!==false || !['failed','need_action'].includes(job.status) ||
    job.status!==draft.jobs.find(item=>item.id===selection.jobId).status || !job.started_at ||
    !positiveId(job.account_id) || job.authorization_bound!==true || !digest(job.confirmation_snapshot)) {
    throw new Error('请选择已尝试、已绑定账号且当前未运行的真实原任务，再核对登记。')
  }
  return Object.freeze({...selection,accountId:job.account_id,accountName:String(job.account_name || `账号 #${job.account_id}`),
    expectedReviewFingerprint:article.review_fingerprint,expectedConfirmationSnapshot:job.confirmation_snapshot})
}

// Keep the platform identifier in the original URL string, including IDs larger
// than JavaScript's safe integer range. Only canonical public articles qualify.
export const builtinPublicationUrl = value => typeof value==='string' &&
  /^https:\/\/zhuanlan\.zhihu\.com\/p\/[1-9][0-9]*$/.test(value) ? value : ''

export const builtinPublicationMinute = value => {
  if(typeof value!=='string' || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(value) || value.startsWith('0000-'))return ''
  const time=new Date(`${value}:00Z`)
  return Number.isFinite(time.getTime()) && time.toISOString().slice(0,16)===value ? `${value}+08:00` : ''
}

export const buildBuiltinPublicationPayload = (snapshot,form,cycle) => {
  const issue=builtinPublicationSnapshotIssue(snapshot,cycle)
  if(issue)throw new Error(issue)
  if(!positiveId(snapshot.accountId) || !digest(snapshot.expectedReviewFingerprint) || !digest(snapshot.expectedConfirmationSnapshot)) {
    throw new Error('请先读取并核对当前稿件和原任务。')
  }
  if(form?.confirmed!==true)throw new Error('请先确认平台上的文章与本轮稿件及原任务一致。')
  const url=typeof form.url==='string'?form.url.trim():''
  if(!builtinPublicationUrl(url))throw new Error('请填写规范的知乎公开文章链接，例如 https://zhuanlan.zhihu.com/p/文章编号。')
  const publishedAt=builtinPublicationMinute(form.publishedAt)
  if(!publishedAt)throw new Error('请按平台显示填写北京时间，精确到分钟。')
  if(typeof form.note!=='string' || form.note.length>1000)throw new Error('补充说明请控制在 1000 字以内。')
  return Object.freeze({kind:'builtin_job',article_id:snapshot.articleId,job_id:snapshot.jobId,account_id:snapshot.accountId,
    expected_review_fingerprint:snapshot.expectedReviewFingerprint,expected_confirmation_snapshot:snapshot.expectedConfirmationSnapshot,
    url,published_at:publishedAt,published_at_precision:'minute',confirmed:true,note:form.note.trim()})
}

export const saveBuiltinPublicationAttempt = async (api,snapshot,payload) => {
  if(typeof api?.publication!=='function')throw new Error('当前版本不支持人工发布登记，请升级软件。')
  const result=await api.publication(snapshot.cycleId,payload),cycle=result?.data
  const record=cycle?.reported_publications?.find(item=>typeof item.id==='string' && item.id && item.kind==='builtin_job' &&
    item.platform==='zhihu' && item.target_id===null && item.article_id===snapshot.articleId &&
    item.job_id===snapshot.jobId && item.account_id===snapshot.accountId && item.url===payload.url &&
    item.review_fingerprint===payload.expected_review_fingerprint && item.job_confirmation_snapshot===payload.expected_confirmation_snapshot && item.published_at_precision==='minute' &&
    item.evidence_level==='owner_reported' && item.published_at_basis==='operator_reported_platform_time' &&
    Date.parse(item.published_at)===Date.parse(payload.published_at) && item.note===payload.note)
  if(cycle?.id!==snapshot.cycleId || cycle.project_id!==snapshot.projectId || !record) {
    throw new Error('保存结果尚未确认，请刷新查看；重试会沿用这次登记。')
  }
  return cycle
}

export const builtinPublicationResultMatchesScope = (snapshot,currentSnapshot,currentCycle,result) =>
  Boolean(snapshot && snapshot===currentSnapshot && !builtinPublicationSnapshotIssue(snapshot,currentCycle) &&
    result?.id===snapshot.cycleId && result.project_id===snapshot.projectId && !builtinPublicationSnapshotIssue(snapshot,result))

export const builtinPublicationRecords = cycle => (cycle?.reported_publications || []).filter(item=>item.kind==='builtin_job' &&
  item.evidence_level==='owner_reported' && builtinPublicationUrl(item.url)).map(item=>({
    id:item.id,title:cycle.drafts?.find(draft=>draft.article_id===item.article_id)?.title || `稿件 #${item.article_id}`,
    jobId:item.job_id,url:item.url,note:typeof item.note==='string'?item.note:'',
    publishedAt:item.published_at,precision:item.published_at_precision,
    olderContent:digest(item.article_sha256) && digest(cycle.drafts?.find(draft=>draft.article_id===item.article_id)?.article_sha256) &&
      item.article_sha256!==cycle.drafts.find(draft=>draft.article_id===item.article_id).article_sha256
  }))

export const builtinPublicationError = error => {
  if([404,405,501].includes(error?.response?.status))return '当前软件尚不支持人工发布登记，请升级后使用。'
  const message=error?.response?.data?.msg || error?.message || ''
  if(/fingerprint|snapshot|sha256/i.test(message))return '稿件或原任务已变化，请刷新资料后重新核对。'
  if(!message || /network error|failed to fetch|timeout|ECONN/i.test(message)) {
    return '连接未完成，保存结果待确认；重试会沿用这次登记，也可以刷新查看。'
  }
  return message
}
