const stages = {
  browser_start:'启动浏览器', navigation:'打开豆包页面', new_conversation:'准备新对话',
  model_selection:'核对模型', question_evidence:'保存提问前证据', editor_readback:'核对输入内容',
  submission_checkpoint:'准备提交', submission_control:'核对发送按钮', submit_question:'发送问题', wait_for_answer:'等待回答', extract_sources:'读取引用来源'
}
const statuses = {completed:'已完成',completed_with_gaps:'部分完成',success:'已完成',failed:'失败',stopped:'已停止',pending:'待测',running:'进行中',need_action:'待处理',needs_review:'待核对',partial:'部分完成',service_unavailable:'服务不可用',deferred_answer:'后台回答未完成',private_account_context:'内部资料已隔离',unknown:'状态未知'}
const count = value => Number.isInteger(value) && value >= 0
const reasonText = reason => {
  if(reason==='browser_error:TimeoutError')return '浏览器等待超时，请先检查豆包窗口和网络'
  if(reason==='login_required'||reason==='session_redirected_to_logout')return '豆包需要重新登录'
  if(reason==='human_verification_required')return '豆包需要正常安全验证'
  if(reason==='client_mode_mismatch')return '豆包界面与原测试界面不一致'
  if(reason==='manual_verification_window_closed'||reason==='browser_error:TargetClosedError')return '豆包窗口已关闭或连接中断'
  if(reason==='private_account_context')return '回答使用账号内部资料，已排除公开统计'
  if(reason==='background_task_timeout'||reason==='background_task_not_final')return '尚未取得后台任务的完整回答'
  if(reason==='service_capacity_unavailable')return '豆包当前服务繁忙'
  if(reason==='website_temporarily_unavailable')return '豆包页面暂时不可用，请稍后再检查'
  if(reason==='submission_send_button_not_unique_not_submitted')return '发送按钮无法唯一确认，请检查豆包窗口'
  if(reason==='submission_send_button_not_ready_not_submitted')return '发送按钮尚未就绪，请检查豆包窗口'
  return reason ? '本题未取得可用回答，请查看原采集报告' : ''
}

export function retestCollection(item) {
  const value=item?.collection, counts=value?.counts
  const keys=['planned','evaluable','failed','pending','excluded_private','incomplete']
  const known=value?.schema_version===1 && keys.every(key=>count(counts?.[key])) &&
    counts.planned===counts.evaluable+counts.failed+counts.pending+counts.excluded_private+counts.incomplete
  if(!known)return {known:false,empty:false,hasAnswers:(item?.comparison?.cohorts||[]).some(row=>count(row.paired_answers)&&row.paired_answers>0),issues:[]}
  const empty=counts.evaluable===0
  const parts=[`有效 ${counts.evaluable}/${counts.planned} 题`]
  for(const [key,label] of [['failed','失败'],['pending','待测'],['excluded_private','内部资料隔离'],['incomplete','其他未完成']]){
    if(counts[key])parts.push(`${label} ${counts[key]} 题`)
  }
  const issues=(Array.isArray(value.issues)?value.issues:[]).filter(issue=>issue&&typeof issue==='object'&&!Array.isArray(issue)).map(issue=>({
    caseId:typeof issue.case_id==='string'&&/^[A-Za-z0-9_-]{1,100}$/.test(issue.case_id)?issue.case_id:'未完成题目',
    status:statuses[issue.status]||statuses.unknown,
    stage:stages[issue.failure_stage]||'',
    message:reasonText(issue.failure_reason),
    code:typeof issue.failure_reason==='string'&&/^[A-Za-z0-9_:.-]{1,160}$/.test(issue.failure_reason)?issue.failure_reason:''
  }))
  const stopMessage=reasonText(value.stop_reason)
  return {known:true,empty,hasAnswers:!empty,status:statuses[value.status]||statuses.unknown,
    title:empty?'本轮未取得可用于公开统计的回答':'',countsText:parts.join(' · '),issues,
    explanation:empty?'尚不能比较品牌曝光或发布效果。原采集记录已保留。':'',
    nextStep:empty?(stopMessage?`${stopMessage}。完成检查后可按原计划另测一轮；软件不会自动重发问题。`:'请先查看未完成原因和原会话，再决定是否另测一轮。'):'',
    stopMessage}
}

export function feedbackCollectionTotals(retests) {
  const items=Array.isArray(retests)?retests:[]
  return {attempts:items.length,withAnswers:items.filter(item=>retestCollection(item).hasAnswers).length}
}
