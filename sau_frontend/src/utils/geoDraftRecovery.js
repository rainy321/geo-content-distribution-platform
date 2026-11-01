// Reuse a completed local AI response. These helpers never generate or publish.
const identifier = value => typeof value === 'string' && /^[A-Za-z0-9_-]{1,100}$/.test(value)
const terminal = new Set(['failed', 'need_action', 'needs_review', 'stopped'])
const digest = value => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value)
const positiveId = value => Number.isSafeInteger(value) && value > 0

export const draftRecoveryBridgeReady = api => typeof api?.preview_research_draft === 'function'
  && typeof api?.recover_research_draft === 'function'

export function recoveredDraftArticle(cycle, task) {
  if (!identifier(cycle?.id) || !identifier(task?.id) || task.kind !== 'draft'
      || task.cycle_id !== cycle.id || !terminal.has(task.status)) return null
  return cycle.drafts?.find(item => positiveId(item.article_id)
    && item.provenance?.recovered_from_task_id === task.id)?.article_id || null
}

export function draftRecoveryScope(cycle, task, selection = '') {
  if (!identifier(cycle?.id) || !identifier(task?.id) || task.kind !== 'draft'
      || task.cycle_id !== cycle.id || !terminal.has(task.status)
      || !Number.isSafeInteger(cycle.revision) || cycle.revision < 0
      || typeof selection !== 'string' || selection.length > 300) return null
  const material = JSON.stringify({revision:cycle.revision, profile:cycle.profile,
    facts:cycle.facts, briefs:cycle.analysis?.briefs,
    destinations:cycle.destinations?.map(item => ({id:item.id, revision:item.revision, config:item.config}))})
  return {taskId:task.id, cycleId:cycle.id, material, selection}
}

export const sameDraftRecoveryScope = (left, right) => Boolean(left && right
  && left.taskId === right.taskId && left.cycleId === right.cycleId
  && left.material === right.material && left.selection === right.selection)

export function draftRecoveryMessage(value, fallback = '这份结果暂时不能直接保存，请核对当前资料或查看原任务。') {
  if (typeof value !== 'string' || !value.trim() || value.length > 300
      || /(?:[A-Za-z]:[\\/]|(?:^|\s)\/(?:Users|home|tmp|srv|var)\/|Traceback|https?:\/\/)/.test(value)) return fallback
  return value.trim()
}

export function draftRecoveryPreview(value) {
  if (value?.available !== true) return {available:false, message:draftRecoveryMessage(value?.message)}
  const draft=value.draft
  if (!digest(value.preview_sha256) || !identifier(value.brief_id)
      || typeof value.platform !== 'string' || !value.platform || value.platform.length > 100
      || !draft || typeof draft.title !== 'string' || !draft.title.trim() || draft.title.length > 180
      || typeof draft.summary !== 'string' || draft.summary.length > 1500
      || typeof draft.content !== 'string' || !draft.content.trim() || draft.content.length > 30000
      || !Array.isArray(draft.tags) || draft.tags.length > 12
      || draft.tags.some(tag => typeof tag !== 'string' || tag.length > 300)
      || (value.already_saved === true && !positiveId(value.article_id))) throw new Error('invalid draft preview')
  return {available:true, preview_sha256:value.preview_sha256, brief_id:value.brief_id, platform:value.platform,
    draft:{title:draft.title, summary:draft.summary, content:draft.content, tags:[...draft.tags]},
    already_saved:value.already_saved === true, article_id:positiveId(value.article_id) ? value.article_id : null}
}

export function recoveredDraftResult(value, scope) {
  return Boolean(scope && value && !value.error && value.cycle_id === scope.cycleId
    && positiveId(value.article_id) && value.reused_existing_response === true
    && typeof value.already_saved === 'boolean')
}

export const draftRecoveryState = () => ({preview:null, loading:false, saving:false, confirmed:false,
  error:'', unavailable:'', savedArticleId:null, uncertain:false})

export function createDraftRecoveryFlow({state, getScope, getApi, isBlocked, loadCycle, applyCycle, busy}) {
  let version=0, disposed=false, ownedBusy=false, inspectAfterSave=false
  const current = scope => !disposed && sameDraftRecoveryScope(scope, getScope())
  const clear = () => Object.assign(state,{preview:null, loading:false, confirmed:false,
    error:'', unavailable:'', savedArticleId:null, uncertain:false})
  const release = () => { if (ownedBusy) {ownedBusy=false;busy(false)} }
  const refresh = async scope => {
    const cycle = await loadCycle(scope.cycleId)
    if (!current(scope) || cycle?.id !== scope.cycleId) return false
    applyCycle(cycle)
    return true
  }
  const inspect = async () => {
    const request=++version, scope=getScope(), api=getApi()
    clear()
    if (state.saving) {inspectAfterSave=true;return}
    if (disposed || !scope || !draftRecoveryBridgeReady(api)) return
    state.loading=true
    try {
      const raw=await api.preview_research_draft(scope.taskId,scope.cycleId)
      if (request!==version || !current(scope)) return
      const preview=draftRecoveryPreview(raw)
      if (!preview.available) {state.unavailable=preview.message;return}
      state.preview=preview
      if (preview.already_saved) {
        state.savedArticleId=preview.article_id
        if (!await refresh(scope) && current(scope)) state.error='稿件已保存，当前列表尚未刷新，请刷新任务单查看。'
      }
    } catch {
      if (request===version && current(scope)) state.error='已有稿件暂时无法核对，原回答仍保留；没有重新发送请求。'
    } finally { if (request===version) state.loading=false }
  }
  const save = async () => {
    const scope=getScope(), api=getApi(), preview=state.preview
    if (disposed || state.saving || state.loading || state.savedArticleId || state.uncertain
        || !state.confirmed || isBlocked() || !scope || !preview?.available || !draftRecoveryBridgeReady(api)) return
    const request=++version
    state.saving=true;state.error='';ownedBusy=true;busy(true)
    try {
      const result=await api.recover_research_draft(scope.taskId,scope.cycleId,preview.preview_sha256)
      if (request!==version || !current(scope)) return
      if (!recoveredDraftResult(result,scope)) throw new Error('unconfirmed local save')
      state.savedArticleId=result.article_id;state.confirmed=false
      try {if (!await refresh(scope) && current(scope)) state.error='稿件已保存，当前列表尚未刷新，请刷新任务单查看。'}
      catch {if(current(scope))state.error='稿件已保存，当前列表尚未刷新，请刷新任务单查看。'}
    } catch {
      if (request===version && current(scope)) {
        state.uncertain=true;state.confirmed=false
        state.error='尚未确认这次保存结果。请先检查保存结果，原回答仍保留；不会重新向豆包提问。'
      }
    } finally {
      state.saving=false;release()
      if (inspectAfterSave && !disposed) {inspectAfterSave=false;await inspect()}
    }
  }
  const dispose = () => {disposed=true;version++;release()}
  return {inspect,save,dispose}
}
