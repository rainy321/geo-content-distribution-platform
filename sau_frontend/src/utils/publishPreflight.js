export const countPublishCharacters = value => Array.from(String(value || '')).length

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
  return {
    ...variant,
    editable_title: editableTitle,
    editable_content: editableContent,
    title_length: titleLength,
    publish_length: publishLength,
    title_within_safe_limit: titleLength >= titleMinLength
      && (!titleSafeLimit || titleLength <= titleSafeLimit),
    within_safe_limit: !safeLimit || (publishLength > 0 && publishLength <= safeLimit)
  }
}

export const frequencyGuidanceLabel = guidance => {
  const dailyMax = Number(guidance?.daily_recommended_max || 0)
  const cooldownMinutes = Number(guidance?.cooldown_minutes || 0)
  if (!dailyMax || !cooldownMinutes) return '按平台动态风控谨慎发布'
  return `软件建议 ≤${dailyMax} 条/日 · 间隔 ≥${cooldownMinutes} 分钟`
}
