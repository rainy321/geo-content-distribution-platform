const identifier = value => typeof value === 'string' && /^[\w-]{1,100}$/.test(value)
const digest = value => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value)

export function externalFeedbackScope(cycle) {
  const plan = cycle?.test_plan?.plan_sha256 || cycle?.baseline?.plan_sha256
  if (!identifier(cycle?.id) || !digest(plan) || !Number.isInteger(cycle?.revision) || cycle.revision < 0
    || cycle.project_id !== null && (!Number.isInteger(cycle.project_id) || cycle.project_id <= 0)) return null
  return {cycleId: cycle.id, projectId: cycle.project_id, plan, revision: cycle.revision}
}

export function externalFeedbackCases(cycle) {
  const rows = Array.isArray(cycle?.baseline?.cases) ? cycle.baseline.cases : []
  return rows.filter(row => identifier(row?.id) && typeof row.question === 'string' && row.question.trim()
    && rows.filter(other => other?.id === row.id).length === 1)
}

export const emptyExternalFeedback = () => ({caseId: '', raw: '', questionMode: 'unknown', question: '',
  clientMode: 'unknown', model: '', completeness: 'unknown', collectedAt: '', note: '', sourceLinks: ''})

export function externalFeedbackPayload(cycle, form) {
  const scope = externalFeedbackScope(cycle)
  const selected = externalFeedbackCases(cycle).find(row => row.id === form.caseId)
  if (!scope || !selected) throw new Error('请选择这轮实验中的一道关联问题。')
  if (typeof form.raw !== 'string' || !form.raw.trim()) throw new Error('请粘贴完整取得的回答原文。')
  if (new TextEncoder().encode(form.raw).length > 1024 * 1024) throw new Error('回答原文不能超过 1 MB。')
  const metadata = {}
  if (form.questionMode === 'same') metadata.question = selected.question
  else if (form.questionMode === 'custom') {
    if (typeof form.question !== 'string' || !form.question.trim() || form.question.length > 4000) throw new Error('请粘贴实际发送的题文，最多 4000 字。')
    metadata.question = form.question
  } else if (form.questionMode !== 'unknown') throw new Error('请核对实际提问条件。')
  if (form.clientMode === 'consumer' || form.clientMode === 'work') metadata.client_mode = form.clientMode
  else if (form.clientMode !== 'unknown') throw new Error('请核对这次回答的实际界面。')
  const model = typeof form.model === 'string' ? form.model.trim() : ''
  if (model.length > 100) throw new Error('模型名称最多 100 字。')
  if (model) metadata.model_label = model
  if (form.completeness === 'complete') metadata.answer_complete = true
  else if (form.completeness === 'partial') metadata.answer_complete = false
  else if (form.completeness !== 'unknown') throw new Error('请核对回答是否完整。')
  if (form.collectedAt) {
    const date = new Date(form.collectedAt)
    if (!Number.isFinite(date.getTime())) throw new Error('请填写有效的实际采集时间。')
    metadata.collected_at = date.toISOString()
  }
  const note = typeof form.note === 'string' ? form.note.trim() : ''
  if (note.length > 1000) throw new Error('备注最多 1000 字。')
  if (note) metadata.note = note
  const links = (typeof form.sourceLinks === 'string' ? form.sourceLinks : '').split(/\r?\n/).map(value => value.trim()).filter(Boolean)
  if (links.length > 20) throw new Error('来源链接最多 20 条。')
  if (links.length) {
    metadata.source_links = [...new Set(links)].map(value => {
      let parsed
      try { parsed = new URL(value) } catch { throw new Error('来源链接须为完整的 HTTP 或 HTTPS 网址。') }
      if (!['http:', 'https:'].includes(parsed.protocol) || parsed.username || parsed.password || value.length > 2000) throw new Error('请填写公开来源网址，不含账号信息。')
      return {url: value}
    })
  }
  return {project_id: scope.projectId, case_id: selected.id, expected_plan_sha256: scope.plan,
    expected_revision: scope.revision, raw_response_text: form.raw, metadata}
}

export function externalContextPayload(cycle, probe, form) {
  const scope = externalFeedbackScope(cycle)
  if (!scope || !identifier(probe?.id) || probe.cycle_id !== scope.cycleId || probe.source !== 'user_reported_external'
    || probe.native_verified !== false || !externalFeedbackCases(cycle).some(row => row.id === probe.case_id)) throw new Error('请选择本轮已保存的外部反馈。')
  const hash = probe.response_sha256 || probe.after?.response_sha256
  if (!digest(hash) || probe.response_sha256 && probe.after?.response_sha256 && probe.response_sha256 !== probe.after.response_sha256) throw new Error('原文身份待核对，请先刷新记录。')
  if (typeof form.question !== 'string' || !form.question.trim() || form.question.length > 4000) throw new Error('请填写实际发送的题文。')
  if (typeof form.newConversation !== 'boolean' || typeof form.firstMessage !== 'boolean') throw new Error('请明确选择是否新建对话、是否首条提问；不确定的条件暂不补充。')
  if (typeof form.confirmation !== 'string' || !form.confirmation.trim() || form.confirmation.length > 3000) throw new Error('请填写你掌握的提问条件说明。')
  if (form.confirmed !== true) throw new Error('请确认这些条件来自你实际掌握的信息。')
  const history = probe.context_attestations || []
  if (!Array.isArray(history)) throw new Error('条件记录待核对，请先刷新。')
  return {project_id: scope.projectId, case_id: probe.case_id, expected_plan_sha256: scope.plan,
    expected_revision: scope.revision, expected_response_sha256: hash, expected_context_sequence: history.length,
    attestation: {question: form.question, new_conversation: form.newConversation, first_message: form.firstMessage,
      confirmation_text: form.confirmation}}
}

export function externalFeedbackResponse(value, scope, caseId) {
  const data = value?.data
  if (!data?.cycle || data.cycle.id !== scope.cycleId || data.cycle.project_id !== scope.projectId
    || externalFeedbackScope(data.cycle)?.plan !== scope.plan || data.cycle.revision < scope.revision
    || data.probe?.cycle_id !== scope.cycleId || data.probe.case_id !== caseId || data.probe.source !== 'user_reported_external'
    || data.probe.native_verified !== false || !identifier(data.probe.id)
    || !Array.isArray(data.cycle.probes) || !data.cycle.probes.some(probe => probe.id === data.probe.id)) throw new Error('保存回执与当前反馈不一致，请刷新核对原记录。')
  return data
}

export function externalCycleCanApply(value, current) {
  const next = externalFeedbackScope(value), prior = externalFeedbackScope(current)
  return Boolean(next && prior && next.cycleId === prior.cycleId && next.projectId === prior.projectId
    && next.plan === prior.plan && next.revision >= prior.revision)
}

export const externalWriteUncertain = error => !error?.response || error.response.status >= 500
