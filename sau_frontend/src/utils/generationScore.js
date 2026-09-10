export const buildGeoScorePayload = ({
  title,
  content,
  summary,
  generationRunId,
  brand,
  product,
  website,
  keywords,
  sources
}) => {
  const payload = { title, content }
  if (summary) payload.summary = summary
  if (Array.isArray(sources) && sources.length) payload.sources = sources.map(item => ({ ...item }))
  if (generationRunId !== null && generationRunId !== undefined && generationRunId !== '') {
    payload.generation_run_id = generationRunId
    return payload
  }
  payload.brand = brand || ''
  payload.keywords = Array.isArray(keywords) ? [...keywords] : []
  if (product) payload.product = product
  if (website) payload.website = website
  return payload
}
