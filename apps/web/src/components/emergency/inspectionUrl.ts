export function parseInspectionParams(params: URLSearchParams): { q: string; tab: string } {
  const q = (params.get('q') || params.get('robot') || '').trim()
  const tab = (params.get('tab') || 'map').trim() || 'map'
  return { q, tab }
}

export function buildInspectionParams(q: string, tab: string): URLSearchParams {
  const next = new URLSearchParams()
  const query = q.trim()
  const section = tab.trim() || 'map'
  if (query) next.set('q', query)
  if (section !== 'map') next.set('tab', section)
  return next
}

export function inspectionParamsEqual(a: URLSearchParams, b: URLSearchParams): boolean {
  return a.toString() === b.toString()
}
