export const countPublishCharacters = value => Array.from(String(value || '')).length

const normalizedSnapshotText = value => String(value || '')
  .replace(/\r\n?/g, '\n')
  .trim()

export const refreshEditableVariant = variant => {
  if (!variant) return null
  const editableTitle = String(variant.editable_title ?? variant.title ?? '')
  const editableContent = String(
    variant.editable_content ?? variant.publish_content ?? ''
  )
  const titleSafeLimit = Number(variant.title_policy?.safe_limit || 0)
  const titleMinLength = Number(variant.title_policy?.min_length || 1)
  const safeLimit = Number(variant.content_policy?.safe_limit || 0)
  const titleLength = countPublishCharacters(editableTitle.trim())
  const publishLength = countPublishCharacters(editableContent)
  const citeability = variant.citeability && typeof variant.citeability === 'object'
    ? variant.citeability
    : null
  const offlinePreflight = variant.offline_preflight
    && typeof variant.offline_preflight === 'object'
    ? variant.offline_preflight
    : null
  const citeabilityScore = Number(citeability?.score)
  const citeabilityPassScore = Number(citeability?.pass_score)
  const citeabilityCurrent = Boolean(citeability)
    && normalizedSnapshotText(editableTitle) === normalizedSnapshotText(variant.title)
    && normalizedSnapshotText(editableContent) === normalizedSnapshotText(variant.publish_content)
  const offlinePreflightCurrent = !offlinePreflight || (
    normalizedSnapshotText(editableTitle) === normalizedSnapshotText(variant.title)
    && normalizedSnapshotText(editableContent) === normalizedSnapshotText(variant.publish_content)
  )
  return {
    ...variant,
    editable_title: editableTitle,
    editable_content: editableContent,
    title_length: titleLength,
    publish_length: publishLength,
    title_within_safe_limit: titleLength >= titleMinLength
      && (!titleSafeLimit || titleLength <= titleSafeLimit),
    within_safe_limit: !safeLimit || (publishLength > 0 && publishLength <= safeLimit),
    citeability_current: citeabilityCurrent,
    citeability_passed: citeabilityCurrent && citeability?.passed === true,
    citeability_score: Number.isFinite(citeabilityScore) ? citeabilityScore : null,
    citeability_pass_score: Number.isFinite(citeabilityPassScore) ? citeabilityPassScore : null,
    citeability_blocking_issues: Array.isArray(citeability?.blocking_issues)
      ? citeability.blocking_issues
      : [],
    citeability_suggestions: Array.isArray(citeability?.suggestions)
      ? citeability.suggestions
      : [],
    offline_preflight_current: offlinePreflightCurrent,
    offline_preflight_passed: !offlinePreflight
      || (offlinePreflightCurrent && offlinePreflight?.passed === true),
    offline_preflight_issues: Array.isArray(offlinePreflight?.issues)
      ? offlinePreflight.issues
      : [],
    offline_preflight_disclaimer: String(offlinePreflight?.disclaimer || '')
  }
}

export const frequencyGuidanceLabel = guidance => {
  const dailyMax = Number(guidance?.daily_recommended_max || 0)
  const cooldownMinutes = Number(guidance?.cooldown_minutes || 0)
  if (!dailyMax || !cooldownMinutes) return '按平台动态风控谨慎发布'
  return `软件建议 ≤${dailyMax} 条/日 · 间隔 ≥${cooldownMinutes} 分钟`
}
