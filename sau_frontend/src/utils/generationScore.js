export const buildGeoScorePayload = ({
  title,
  content,
  generationRunId,
  brand,
  keywords
}) => {
  const payload = { title, content }
  if (generationRunId !== null && generationRunId !== undefined && generationRunId !== '') {
    payload.generation_run_id = generationRunId
    return payload
  }
  payload.brand = brand || ''
  payload.keywords = Array.isArray(keywords) ? [...keywords] : []
  return payload
}
