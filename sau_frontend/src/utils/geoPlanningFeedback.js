import {feedbackReviewView} from './geoFeedbackReview.js'

// A current input snapshot and recorded feedback references are separate signals.
export function planningFeedbackNotice(cycle) {
  const feedback = cycle?.feedback_diagnostics?.planning_feedback
  if (!feedback || typeof feedback !== 'object' || Array.isArray(feedback)) return null
  if (feedback.available_count === 0) return null
  const context = feedback.context_status
  if (context === 'current') {
    if (feedback.attribution_status === 'not_recorded') {
      const briefs = cycle?.analysis?.briefs
      const explained = Array.isArray(briefs) && briefs.length > 0 && briefs.length <= 6
        && briefs.every(brief => ['not_used', 'no_eligible_feedback'].includes(feedbackReviewView(brief).decision))
      return {message: explained ? '当前选题的反馈输入已更新；AI已说明未引用后续反馈，理由可在各选题中查看。'
        : '当前选题的反馈输入已更新；反馈出处未标注，待审阅。', canReplan: false}
    }
    const count = feedback.referenced_feedback_count
    if (feedback.attribution_status === 'referenced' && Number.isSafeInteger(count) && count > 0) return {
      message: `当前选题记录了 ${count} 条反馈出处；出处记录不代表内容已核实或优化有效。`, canReplan: false
    }
    return null
  }
  if (context === 'legacy_unknown') return {
    message: '当前选题没有可核对的反馈输入版本，可先审阅后按需更新。', canReplan: feedback.needs_update === true
  }
  if (context === 'not_generated') return null
  // Explicit context status takes priority; the old flag supports older servers.
  if (context === 'outdated' || (context == null && feedback.needs_update === true)) return {
    message: '当前选题生成后，反馈线索有变化，可以重新策划；既有稿件和发布记录保留。', canReplan: true
  }
  return null
}
