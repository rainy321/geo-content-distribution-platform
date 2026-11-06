const identifier = value => typeof value === 'string' && /^[A-Za-z0-9_-]{1,100}$/.test(value)
const unknown = () => ({decision: 'unknown', label: '未记录反馈使用声明', reason: '', probeIds: [],
  note: '缺少声明不能据此判断是否实际参考了后续反馈。'})

// A saved AI declaration is lineage, never proof of reading, adoption or effect.
// Validate used refs against the saved planning snapshot, not later samples.
export function feedbackReviewView(brief) {
  const value = brief?.feedback_review
  if (!value || typeof value !== 'object' || Array.isArray(value)
      || Object.keys(value).sort().join(',') !== 'decision,reason'
      || !['used', 'not_used', 'no_eligible_feedback'].includes(value.decision)
      || typeof value.reason !== 'string') return unknown()
  const reason = value.reason.trim(), size = Array.from(reason).length
  const ids = brief.feedback_probe_ids ?? []
  if (!size || size > 500 || !Array.isArray(ids) || ids.length > 6 || ids.some(id => !identifier(id))
      || new Set(ids).size !== ids.length) return unknown()
  if (value.decision === 'used') {
    if (!ids.length || !Array.isArray(brief.case_ids) || !Array.isArray(brief.feedback_inputs)) return unknown()
    for (const id of ids) {
      const rows = brief.feedback_inputs.filter(row => row?.probe_id === id)
      if (rows.length !== 1 || !brief.case_ids.includes(rows[0].case_id)
          || rows[0].evidence_role !== 'editorial_signal_only_not_writing_fact_or_public_metric') return unknown()
    }
  } else if (ids.length) return unknown()
  const labels = {used: 'AI申报：参考了后续反馈', not_used: 'AI申报：未引用后续反馈',
    no_eligible_feedback: 'AI申报：策划时无可用后续反馈'}
  return {decision: value.decision, label: labels[value.decision], reason, probeIds: [...ids],
    note: '仅为AI申报，不证明实际阅读、采用或优化效果。'}
}
