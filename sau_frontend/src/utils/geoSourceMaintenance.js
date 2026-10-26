export const qccMaintenanceEntry = 'https://help.qcc.com/spt/support/handle-dissent'
export const sourceMaintenanceModules = {website:'企业官网',introduction:'企业简介'}

const identifier=value=>typeof value==='string'&&/^[\w-]{1,100}$/.test(value)
const digest=value=>typeof value==='string'&&/^[a-f0-9]{64}$/i.test(value)
const knownStages=new Set(['draft','opening','awaiting_login','awaiting_human_verification','needs_manual_input',
  'confirmation_ready','submitted_pending_review','submission_unknown','needs_manual_review','fee_required_stopped','window_closed','browser_rejected','waiting_verification'])

export const sourceMaintenanceCompany=cycle=>cycle?.baseline?.profile?.entity_type==='company'
export function sourceMaintenanceOverview(cycle,overview=cycle?.source_maintenance) {
  const company=sourceMaintenanceCompany(cycle),name=cycle?.baseline?.profile?.name
  const scoped=Boolean(company&&identifier(cycle?.id)&&overview?.platform==='qcc'
    &&typeof name==='string'&&name.trim()&&overview.company_name===name)
  const supported=Boolean(scoped&&overview.supported===true&&digest(overview.context_sha256))
  const modules=scoped&&Array.isArray(overview.modules)?overview.modules.filter(item=>Object.hasOwn(sourceMaintenanceModules,item?.key)
    &&typeof item.proposal==='string'&&Number.isInteger(item.max_length)&&item.max_length>0&&item.max_length<=500
    &&Array.from(item.proposal).length<=item.max_length
    &&overview.modules.filter(other=>other?.key===item.key).length===1).map(item=>({...item,label:sourceMaintenanceModules[item.key]})):[]
  const tasks=scoped&&Array.isArray(overview.tasks)?overview.tasks.filter(item=>identifier(item?.id)&&identifier(item.cycle_id)
    &&item.company_name===name&&Object.hasOwn(sourceMaintenanceModules,item.module)&&typeof item.proposal==='string'
    &&overview.tasks.filter(other=>other?.id===item.id).length===1):[]
  return {supported,company_name:scoped?name:'',context_sha256:supported?overview.context_sha256:null,modules,tasks,
    reason:!company?'企业资料补正适用于公司档案，请切换到运营公司单独处理。':typeof overview?.reason==='string'&&overview.reason?overview.reason:
      !supported?'请先核对并保存公司公开事实；当前资料补正条件尚未确认。':''}
}
export const sourceMaintenanceReadOnly=record=>!record||record.read_only!==false||!knownStages.has(record.stage)
  ||['submitted_pending_review','submission_unknown'].includes(record.stage)
export const sourceMaintenanceEditable=record=>Boolean(record&&record.stage==='draft'&&record.can_edit===true&&!sourceMaintenanceReadOnly(record))
export const sourceMaintenanceTask=(overview,module)=>{
  const rows=overview?.tasks?.filter(item=>item.module===module)||[]
  return rows.length===1?rows[0]:null
}
export function sourceMaintenancePayload(cycle,overview,module,proposal) {
  const current=sourceMaintenanceOverview(cycle,overview)
  if(!current.supported)throw new Error(current.reason||'当前对象不能创建企业资料补正任务。')
  const selected=current.modules.find(item=>item.key===module)
  if(!selected)throw new Error('该补正模块尚未准备好，请先核对公开事实。')
  const rows=current.tasks.filter(item=>item.module===module),record=sourceMaintenanceTask(current,module)
  if(rows.length>1||record&&(record.cycle_id!==cycle.id||!sourceMaintenanceEditable(record)))throw new Error('这个模块已有原申请，请先核对原任务，不要重复创建。')
  if(typeof proposal!=='string'||!proposal.trim()||Array.from(proposal).length>selected.max_length)throw new Error(`请填写不超过 ${selected.max_length} 字的公开补正说明。`)
  const facts=selected.reference_fact_ids
  if(module==='introduction'&&(!Array.isArray(facts)||!facts.length))throw new Error('请先核对并保存简介所依据的公开事实。')
  if(facts!==undefined&&(!Array.isArray(facts)||new Set(facts).size!==facts.length||!facts.every(id=>(cycle.facts||[]).some(fact=>fact.id===id&&fact.confirmed===true))))throw new Error('补正依据已变化，请刷新并重新核对公开事实。')
  return {module,proposal,expected_context_sha256:current.context_sha256,...(facts?{reference_fact_ids:[...facts]}:{}),...(record?{task_id:record.id}:{})}
}
export const sourceMaintenanceCanOpen=(record,cycle)=>Boolean(identifier(record?.id)&&record.cycle_id===cycle?.id
  &&sourceMaintenanceCompany(cycle)&&record.company_name===cycle.baseline.profile.name&&record.can_open===true&&knownStages.has(record.stage)
  &&record.stage!=='browser_rejected')
export const sourceMaintenanceNativeId=record=>identifier(record?.native_task_id)?record.native_task_id:null
export function sourceMaintenanceStage(record) {
  const states={
    draft:['说明已保存','打开官方窗口后，软件核对企业主体和当前页面，再辅助填写公开说明。'],
    opening:['正在打开官方窗口','请等待当前窗口就绪，不要重复打开申请。'],
    awaiting_login:['请在企查查登录','请在官方窗口正常登录，完成后保持窗口打开，再检查当前窗口。'],
    awaiting_human_verification:['请完成安全验证','请在当前官方窗口完成平台验证，保持窗口打开，再检查当前窗口。'],
    waiting_verification:['请完成滑块验证','请在当前官方窗口拖动滑块完成拼图；验证可能位于嵌入页面内。完成后保持窗口打开，再检查当前窗口。'],
    needs_manual_input:['官方页面还有待填写项','请按当前官方页面补充缺项。姓名、联系方式和证明材料仅在官方浏览器处理，不填入软件。'],
    confirmation_ready:['请在官方页面核对并提交','请核对主体、模块与公开说明；联系人信息和最终提交由你在官方页面完成。软件不会点击最终提交。'],
    submitted_pending_review:['已提交，待平台审核','提交回执不代表审核通过或公开资料已更新。请核对原申请，不要重复提交。'],
    submission_unknown:['提交结果待核对','请只读核对原申请与平台状态，不要重复提交。软件不会重放原申请。'],
    needs_manual_review:['请人工核对当前页面','当前页面或资料条件尚未确认。请查看官方提示，软件停止自动推进。'],
    fee_required_stopped:['出现收费提示，已停止','请先核对当前官方权益与费用要求；软件不会付款或替你继续。'],
    window_closed:['官方窗口已关闭','原任务已保留。重新打开会先核对原进度；已填写或提交过的申请只读核对。'],
    browser_rejected:['当前浏览器未获平台接受','请使用下方固定官方入口，在自己的日常浏览器手动处理。软件不会更换启动方式或搬迁登录凭据。']
  }
  const value=states[record?.stage]||['进度待核对','当前阶段尚未确认，请保留原任务并核对，不要重复创建或提交。']
  return {label:value[0],hint:value[1],tone:['submission_unknown','fee_required_stopped','needs_manual_review'].includes(record?.stage)?'warning':'info'}
}
export const sourceMaintenanceError=error=>error?.response?.data?.msg||error?.message||'操作尚未确认，原任务已保留。请刷新进度后核对，避免重复创建。'
