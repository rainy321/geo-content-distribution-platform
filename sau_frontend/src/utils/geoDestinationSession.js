const destinationKinds = new Set(['destination_login', 'destination_fill'])
const sessionStates = new Set(['opening', 'checking', 'login_required', 'challenge', 'binding_required', 'editor_ready', 'action_required', 'window_closed'])
const bindingNames = {wechat:'微信',phone:'手机号',email:'邮箱',account:'账号'}
const csdnHosts = new Set(['blog.csdn.net','editor.csdn.net','passport.csdn.net','mp.csdn.net'])

export const isDestinationTask = task => destinationKinds.has(task?.kind)
export const destinationSupportsFill = target => ['configured_fill','platform_assisted_fill'].includes(target?.capability)

export const matchesDestinationTask = (task, cycleId, targetId) => Boolean(
  task?.id && cycleId && targetId && isDestinationTask(task) &&
  task.cycle_id === cycleId && task.target_id === targetId
)

export const destinationPreparation = target => {
  const config = target?.config || {}
  const csdn = ['home_url','source_scope','login_url','editor_url'].some(key => {
    try {const url = new URL(config[key]);return url.protocol === 'https:' && !url.username && !url.password && csdnHosts.has(url.hostname)}
    catch {return false}
  })
  return {
    login:'在平台专用窗口正常登录或注册；可能需要扫码或手机号。',
    verification:'登录后或发文时仍可能要求安全验证，请在同一窗口正常完成。',
    binding:csdn ? 'CSDN 发文可能要求微信或手机号绑定，以当前账号提示为准。' : '实名、账号绑定或投稿申请，以该平台当前账号提示为准。',
    editor:csdn && target?.capability === 'platform_assisted_fill' ? '仅确认空白或完整欢迎示例后，才会将已审稿文章正常导入为新文档；其他已有草稿会保留并暂停填写。' : '',
    publishing:csdn ? '发布前核对平台已有标签、AI创作声明和可见范围；自定义标签可能受账号等级限制，跨站备份或同步由你选择。' : '',
    capability:target?.capability === 'platform_assisted_fill'
      ? '当前支持平台专用辅助填写；投稿条件需核对，最终提交由你在平台确认。'
      : target?.capability === 'configured_fill'
        ? '当前为配置式辅助填写，仍待实站核对；最终提交由你在平台确认。'
        : '当前支持登录与导出；编辑器和投稿权限仍待核对，尚无自动提交功能。'
  }
}

export const destinationPreparationText = target => {
  const preparation = destinationPreparation(target)
  return [preparation.login,preparation.verification,preparation.binding,preparation.editor,preparation.publishing].filter(Boolean).join(' ')
}

export const destinationContinuationText = api => typeof api?.check_destination_session === 'function'
  ? '完成登录、验证或绑定后保持窗口打开，在平台准备卡“检查并继续”。'
  : '当前桌面版本不支持窗口复检，请按原专用窗口提示正常完成操作；原任务记录保留，升级后可使用新的检查流程。'

// Only the live task for this cycle and target may be checked. Saved cookies,
// a closed window and an old worker's can_check flag are not authentication.
export const destinationSessionView = (task, cycleId, targetId) => {
  if (!matchesDestinationTask(task,cycleId,targetId)) return null
  const active = task.status === 'running'
  const observation = (active ? task.progress?.destination_session : task.result?.destination_session) || task.progress?.destination_session || {}
  let state = sessionStates.has(observation.state) ? observation.state : 'unknown'
  if (state === 'editor_ready' && observation.editor_ready !== true) state = 'unknown'
  if (!active && !['completed'].includes(task.status) && state !== 'window_closed') state = 'unknown'
  const binding = bindingNames[observation.binding_kind] || '平台要求的账号'
  const contentWriteAttempted = observation.content_write_attempted === true
  const labels = {
    opening:['正在打开平台','等待专用窗口打开，再按页面提示操作。'],
    checking:['正在检查当前窗口','检查完成后会更新这里的状态，请保留窗口。'],
    login_required:['需要登录','请在平台专用窗口正常完成登录或注册。'],
    challenge:['需要安全验证','请在平台专用窗口完成当前安全验证，完成后保留窗口。'],
    binding_required:['需要账号绑定',`请在平台专用窗口按提示完成${binding}绑定，再返回检查。`],
    editor_ready:[active ? '编辑器可用' : '上次检查编辑器可用','已检查本次窗口的编辑框；投稿权限与最终提交结果仍以平台为准。'],
    action_required:['需要检查平台提示','请在专用窗口核对账号的投稿条件与页面提示，编辑器状态尚未确认。'],
    window_closed:['窗口已结束，状态待确认','关闭窗口不能证明已登录或已发布，原任务记录已保留。'],
    unknown:['状态待确认',active ? '软件尚未读到可确认的窗口状态，请先查看平台专用窗口。' : '原任务已结束，记录已保留；当前登录与编辑器状态需重新核对。']
  }
  const [title,detail] = labels[state]
  const welcomeExample = state === 'editor_ready' && observation.body_state === 'welcome_example'
  return {taskId:task.id,state,title:welcomeExample ? (active ? '编辑器可用，带有首次示例' : '上次检查编辑器带有首次示例') : title,
    detail:welcomeExample ? '已确认完整预置欢迎示例；已审稿填写任务会正常导入文章为新文档，原示例文档保留；其他已有内容会暂停处理，最终提交由你确认。' : detail,active,contentWriteAttempted,
    canCheck:active && observation.can_check === true && observation.window_open === true && !['opening','checking','window_closed'].includes(state),
    checkLabel:contentWriteAttempted ? '检查当前窗口' : '检查并继续'}
}

export const requestDestinationSessionCheck = async (api, task, cycleId, targetId) => {
  const session = destinationSessionView(task,cycleId,targetId)
  if (!session?.canCheck) throw new Error('当前平台任务不可复检，请刷新状态；原记录已保留。')
  if (typeof api?.check_destination_session !== 'function') throw new Error('当前桌面版本不支持窗口复检，请升级软件；原任务记录已保留。')
  const result = await api.check_destination_session(session.taskId)
  if (result?.error) throw new Error(result.error)
  if (result?.id !== session.taskId || result.status !== 'running' || result.check_requested !== true) {
    throw new Error('尚未确认原任务收到检查请求，请刷新状态；未另建任务。')
  }
  return result
}
