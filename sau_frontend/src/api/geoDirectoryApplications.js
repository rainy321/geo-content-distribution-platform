import { http } from '@/utils/request'

const quiet = { suppressGlobalError: true }
const base = cycle => `/api/geo-content/cycles/${encodeURIComponent(cycle)}/directory-applications`
export const geoDirectoryApplicationsApi = {
  list: (cycle,target) => http.get(base(cycle),{target_id:target},quiet),
  get: (cycle,id) => http.get(`${base(cycle)}/${encodeURIComponent(id)}`,undefined,quiet),
  save: (cycle,data) => http.post(base(cycle),data,quiet),
  review: (cycle,id,data) => http.post(`${base(cycle)}/${encodeURIComponent(id)}/review`,data,quiet),
  refreshContext: (cycle,id,data) => http.post(`${base(cycle)}/${encodeURIComponent(id)}/refresh-context`,data,quiet),
  externalReceipt: (cycle,id,data) => http.post(`${base(cycle)}/${encodeURIComponent(id)}/external-receipt`,data,quiet)
}

export const directoryAdapter = 'grokcode_api_transit_v1'
export const directoryEntry = 'https://grokcode.cn/api-transit?modal=submit-transit'
export const apiznzAdapter = 'apiznz_external_receipt_v1'
export const apiznzEntry = 'https://www.apiznz.com/submit'
export const apiznzHome = 'https://www.apiznz.com/'
export const apisouAdapter = 'apisou_directory_prefill_v1'
export const apisouEntry = 'https://www.apisou.com/submit'
export const apisouHome = 'https://www.apisou.com/'
export const apisouLimits = Object.freeze({name:120,url:500,tagline:200,type:10,contact:500,note:2000})
export const apisouTypes = Object.freeze(['官转','商业','公益','逆向','开源'])
export const apisouModelLines = Object.freeze(['openai','anthropic','google','xai','deepseek','qwen','doubao','moonshot','minimax','zhipu','runway','bfl'])
export const apisouModelLabels = Object.freeze({openai:'OpenAI',anthropic:'Claude',google:'Gemini',xai:'Grok',deepseek:'DeepSeek',qwen:'Qwen',doubao:'Doubao',moonshot:'Kimi',minimax:'MiniMax',zhipu:'GLM',runway:'Runway',bfl:'FLUX'})
export const apisouModalities = Object.freeze(['文本','图像','视频','音频','向量'])
export const apisouFields = value => ({name:typeof value?.name==='string'?value.name:'',url:typeof value?.url==='string'?value.url:'',
  tagline:typeof value?.tagline==='string'?value.tagline:'',type:typeof value?.type==='string'&&value.type?value.type:'商业',contact:'',
  note:typeof value?.note==='string'?value.note:'',model_lines:Array.isArray(value?.model_lines)?[...value.model_lines]:[],modalities:Array.isArray(value?.modalities)?[...value.modalities]:[]})
export const directoryFieldNames = Object.freeze(['name','url','rate','source','models','contact','notes'])
export const directoryLimits = Object.freeze({name:120,url:500,rate:500,source:1500,contact:500,notes:2000})
export const directoryDigest = value => typeof value==='string' && /^[a-f0-9]{64}$/.test(value)
export const directoryIdentifier = value => typeof value==='string' && /^[A-Za-z0-9_-]{1,128}$/.test(value)

export const isGrokCodeDirectory = target => {
  try {
    const url=new URL(target?.config?.source_scope)
    return url.protocol==='https:' && url.hostname==='grokcode.cn' && !url.username && !url.password && !url.port &&
      ['/api-transit','/api-transit/'].includes(url.pathname) && !url.search && !url.hash
  } catch {return false}
}
export const isApiznzDirectory = target => {
  try {
    const url = new URL(target?.config?.source_scope)
    return url.origin === new URL(apiznzHome).origin && !url.username && !url.password &&
      ['/', '/site/lists', '/site/lists/'].includes(url.pathname) && !url.search && !url.hash
  } catch { return false }
}
export const isApisouDirectory = target => {
  try {
    const url=new URL(target?.config?.source_scope)
    return url.origin===new URL(apisouHome).origin&&!url.username&&!url.password&&url.pathname==='/'&&!url.search&&!url.hash
  } catch {return false}
}
export const directoryAdapterForTarget = target => isGrokCodeDirectory(target) ? directoryAdapter : isApiznzDirectory(target) ? apiznzAdapter : isApisouDirectory(target) ? apisouAdapter : ''
export const directoryTargetForChannel = (cycle,key) => {
  if(typeof key!=='string'||!key)return null
  const known=(cycle?.destinations || []).filter(directoryAdapterForTarget)
  const legacy=known.filter(isGrokCodeDirectory)
  return known.find(target=>target.key===key || target.id===key || `target_${target.id}`===key) ||
    (key.toLowerCase()==='grokcode'&&legacy.length===1?legacy[0]:null)
}

export const directoryFields = (value,adapter=directoryAdapter) => adapter===apisouAdapter?apisouFields(value):({name:typeof value?.name==='string'?value.name:'',
  url:typeof value?.url==='string'?value.url:'',rate:typeof value?.rate==='string'?value.rate:'',
  source:typeof value?.source==='string'?value.source:'',models:Array.isArray(value?.models)?value.models.filter(item=>typeof item==='string'):[],
  contact:typeof value?.contact==='string'?value.contact:'',notes:typeof value?.notes==='string'?value.notes:''})
export const directoryFieldsKey = (value,adapter=directoryAdapter) => JSON.stringify(directoryFields(value,adapter))
export const directoryInitialFields = (cycle,adapter=directoryAdapter) => directoryFields({name:(cycle?.profile || cycle?.baseline?.profile)?.name},adapter)
export const directoryOfficialUrls = cycle => {
  const profile=cycle?.profile || cycle?.baseline?.profile || {},seen=new Set()
  return [profile.website,...(Array.isArray(profile.official_urls)?profile.official_urls:[])].filter(value=>{
    try {
      if(typeof value!=='string'||value!==value.trim()||value.length>directoryLimits.url||/\s/.test(value))return false
      const url=new URL(value),key=value.replace(/\/$/,'')
      if(url.protocol!=='https:'||url.username||url.password||url.hash||url.port&&url.port!=='443'||
        [...url.searchParams.keys()].some(name=>/key|token|secret|password|credential|auth/i.test(name))||seen.has(key))return false
      seen.add(key);return true
    } catch {return false}
  })
}

export const directoryScope = (cycle,target) => {
  const adapter=directoryAdapterForTarget(target)
  if(!cycle?.id || !Number.isSafeInteger(cycle.project_id) || cycle.project_id<=0 || !target?.id ||
    !Number.isSafeInteger(target.revision) || !adapter ||
    !cycle.destinations?.some(item=>item.id===target.id && item.revision===target.revision))return null
  return Object.freeze({cycleId:cycle.id,projectId:cycle.project_id,targetId:target.id,targetRevision:target.revision,adapter,
    material:JSON.stringify([target.config_sha256 || target.config,cycle.profile || cycle.baseline?.profile])})
}
export const directoryScopeKey = (cycle,target) => JSON.stringify(directoryScope(cycle,target))
export const directoryProjectRecord = (record,scope) => Boolean(scope && directoryIdentifier(record?.id) &&
  record.project_id===scope.projectId && record.adapter===scope.adapter &&
  Number.isSafeInteger(record.revision) && record.revision>=0 && directoryDigest(record.review_sha256))
export const directoryRecordBelongs = (record,scope) => directoryProjectRecord(record,scope) && record.target_id===scope.targetId
export const directoryRecordCurrent = (record,scope) => directoryRecordBelongs(record,scope) &&
  record.cycle_id===scope.cycleId && record.target_revision===scope.targetRevision

export const directoryTaskView = task => task?.kind==='directory_application'
  ? {title:'目录申请辅助填写进行中',detail:'请按原目录申请卡说明核对同一平台窗口。是否支持最终提交，以原卡显示能力为准；仅预填窗口暂不能提交。核对后回到原卡检查当前窗口。待审核或结果未知时保留原申请，不要重复提交。'}
  : task?.kind==='directory_application_inspect'
    ? {title:'正在查看原目录申请',detail:'这是原平台的只读查看窗口，不会重新填写或提交申请。请按原回执核对审核情况；查看窗口不会将申请记为已收录或 GEO 成功。'}
    : null

export const directoryFieldsIssue = (value,limits=directoryLimits,modelOptions=[],adapter=directoryAdapter) => {
  if(adapter===apisouAdapter){
    const fields=apisouFields(value)
    if(!fields.name.trim()||!fields.url.trim())return '请填写服务名称和已登记的官方入口。'
    for(const [key,maximum] of Object.entries(apisouLimits))if(Array.from(fields[key]).length>maximum)return '公开字段超过长度限制。'
    if(value?.contact)return '当前API搜联系方式请留空。'
    if(!apisouTypes.includes(fields.type))return '请选择平台提供的站点类型。'
    for(const [key,options] of [['model_lines',apisouModelLines],['modalities',apisouModalities]])if(!Array.isArray(value?.[key])||new Set(value[key]).size!==value[key].length||value[key].some(item=>!options.includes(item)))return '未核实的模型与能力请不选。'
    try{const url=new URL(fields.url);if(url.protocol!=='https:'||url.username||url.password||url.hash||url.port&&url.port!=='443')throw new Error()}catch{return '请填写完整的公开 HTTPS 服务网址。'}
    return ''
  }
  const fields=directoryFields(value)
  for(const [key,label] of [['name','名称'],['url','网址'],['rate','对客价格'],['source','公开资料来源']]) {
    if(!fields[key].trim())return `请填写${label}。`
  }
  for(const [key,label] of [['name','名称'],['url','网址'],['rate','对客价格'],['source','来源'],['contact','联系入口'],['notes','补充说明']]) {
    const limit=Math.min(directoryLimits[key],Number.isSafeInteger(limits?.[key])?limits[key]:directoryLimits[key])
    if(Array.from(fields[key]).length>limit)return `${label}请控制在 ${limit} 字以内。`
  }
  try {
    const url=new URL(fields.url)
    if(url.protocol!=='https:' || url.username || url.password || (url.port && url.port!=='443'))return '请填写完整的公开 HTTPS 服务网址。'
  } catch {return '请填写完整的公开 HTTPS 服务网址。'}
  if(!Array.isArray(value?.models) || value.models.length!==1 || fields.models.length!==1 || !modelOptions.includes(fields.models[0]))return '请选择这次申请涉及的一项模型类型。'
  return ''
}

// The operator chooses the actual precision; never append invented seconds.
export const directoryReceiptTime = (value,precision) => {
  const pattern=precision==='minute'?/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/:precision==='second'?/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$/:null
  if(!pattern || typeof value!=='string' || !pattern.test(value) || value.startsWith('0000-'))return ''
  const normalized=precision==='minute'?value+':00':value,date=new Date(normalized+'Z')
  return Number.isFinite(date.getTime()) && date.toISOString().slice(0,19)===normalized?value+'+08:00':''
}

export const directorySafeMessage = (error,fallback='操作结果尚未确认，请先刷新原申请，勿重复打开或提交。') => {
  if([404,405,501].includes(error?.response?.status))return '当前软件尚未提供目录申请服务，请更新后使用；原记录保留。'
  if(error?.response?.status===409)return '申请资料或状态已变化，请刷新原申请后重新核对；不会自动重试。'
  const value=typeof error==='string'?error:error?.response?.data?.msg || error?.message
  if(typeof value!=='string' || value.length>260 || !/^(请|本|当前|已有|同一|这|目录|申请|已|仅|不|保存|无法|平台|字段|名称|网址|价格|来源|联系|补充)/.test(value) ||
    /(?:[A-Za-z]:[\\/]|(?:^|\s)\/(?:Users|home|tmp|srv|var)\/|https?:|Traceback|Playwright|locator|sqlite|snapshot|sha256|nonce|fingerprint)/i.test(value))return fallback
  return value
}

export const directoryStage = record => ({draft:'草稿待核对',reviewed:'资料已核对，等待打开平台',opening:'平台窗口正在打开',
  awaiting_human_verification:'请在原平台完成登录或验证',needs_manual_review:'页面与公开字段待核对',
  awaiting_human_confirmation:'请在平台核对并最终确认',submitted_pending_review:'平台已接收，待审核',submission_unknown:'提交结果待确认',
  rejected:'平台未接收或审核未通过',window_closed:'窗口已结束，提交状态待核对',closed_not_submitted:'窗口已受控结束，本次未提交',
  accepted:'已收录（按记录来源核对）'}[record?.stage] || '状态待核对')
