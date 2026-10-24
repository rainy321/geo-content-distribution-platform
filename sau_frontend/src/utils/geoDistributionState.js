export function distributionReference(scheduled, value) {
  if(!scheduled)return {valid:true,key:'now',mode:'now',params:{},at:null}
  if(typeof value!=='string'||!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(value))return {valid:false,key:'scheduled:missing',mode:'scheduled',params:{},at:null}
  const date=new Date(value)
  if(!Number.isFinite(date.getTime()))return {valid:false,key:'scheduled:invalid',mode:'scheduled',params:{},at:null}
  const at=date.toISOString()
  return {valid:true,key:`scheduled:${at}`,mode:'scheduled',params:{publish_at:at},at}
}

// A legacy positive "remaining" value alone never authorizes a new task.
export function distributionQuota(policy, referenceAt=null) {
  const unknown={state:'unknown',allowed:false,title:'额度尚未确认',detail:'请刷新账号额度后再创建任务。',nextAt:null}
  if(!policy||typeof policy.allowed!=='boolean'||!Number.isFinite(Date.parse(policy.reference_at))
    ||!Number.isInteger(policy.remaining_new)||policy.remaining_new<0)return unknown
  if(referenceAt!==null&&Date.parse(policy.reference_at)!==Date.parse(referenceAt))return unknown
  const nextAt=typeof policy.next_allowed_at==='string'&&Number.isFinite(Date.parse(policy.next_allowed_at))?policy.next_allowed_at:null
  if(policy.allowed===true)return policy.reason_code==='allowed'&&policy.remaining_new>0
    ?{state:'allowed',allowed:true,title:`可再预留 ${policy.remaining_new} 条`,detail:'仅本系统预留，不代表平台官方剩余额度。',nextAt:null}:unknown
  const reasons={minimum_interval:['interval','尚未达到操作间隔','按所选时间核对；已有任务仍保留。'],
    rolling_limit_reached:['limit','达到 24h 预留上限','失败和待确认任务也可能占用预留，请使用下方原任务。'],
    platform_delivery_paused:['paused','该平台自动发布已暂停','可保留稿件与原任务，待平台链路恢复后核对。']}
  const known=reasons[policy.reason_code]
  return known?{state:known[0],allowed:false,title:known[1],detail:known[2],nextAt}:unknown
}

export function distributionOverviewMatches(value, reference) {
  if(!reference.valid||!['demo','real'].includes(value?.execution_mode)||value?.reference_mode!==reference.mode||!Number.isFinite(Date.parse(value?.reference_at)))return false
  return reference.mode==='now'||Date.parse(value.reference_at)===Date.parse(reference.at)
}

export function distributionActionError(kind,error) {
  const copy={execute:'尚未确认这次发布执行的结果。请刷新原任务并核对平台，勿另建或重复提交。',
    retry:'尚未确认重新排队的结果，请刷新原任务后再操作；不会自动再次提交。',
    check:'平台结果核对未完成，原任务仍保留；不会再次填写或提交。'}
  const raw=error?.response?.data?.msg
  const safe=typeof raw==='string'&&raw.length<=260&&/^(请|需要|同一|任务|发布|真实|文章|演示|达到|尚未|该平台|状态为)/.test(raw)
    &&!/[\r\n\\]|https?:|call log|traceback|playwright|browser(type|context)|page\.|locator\.|executable|cookie|authorization/i.test(raw)
  if(error?.response?.status===409)return `${safe?raw+'。':''}请刷新原任务和稿件，重新核对当前版本后再确认；不会自动重新排队或提交。`
  return `${safe?raw+'。':''}${copy[kind]||copy.execute}`
}

export const validDistributionToken=value=>typeof value==='string'&&/^[a-f0-9]{64}$/i.test(value)

export const distributionJobSnapshot=job=>JSON.stringify([job?.job_id,job?.article_id,job?.account_id,job?.platform,job?.status,Boolean(job?.demo),
  job?.article_title,job?.account_name,job?.started_at,job?.finished_at,job?.images||[],job?.video||'',job?.publish_at,Boolean(job?.auto_execute),job?.authorization_bound,job?.confirmation_snapshot])

export const distributionArticleSnapshot=article=>article?JSON.stringify([article.id,article.project_id,article.status,article.title,article.content,article.summary,article.tags,article.review_fingerprint]):null
