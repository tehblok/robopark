const ROBOT_VALUE = /^(?:[a-z]\d+|\d+|yasadr[\d\s-]+)$/i

function valid(value: string | null): string | null {
  const trimmed = value?.trim() ?? ''
  return trimmed.length > 0 && trimmed.length <= 64 && ROBOT_VALUE.test(trimmed) ? trimmed : null
}

export function parseRobotReference(raw: string): string | null {
  const input = raw.trim()
  if (!input) return null
  if (!input.includes('/') && !input.includes('?')) return valid(input)

  let url: URL
  try {
    url = new URL(input, 'https://robopark.local')
  } catch {
    return null
  }

  const robotRoute = url.pathname.match(/^\/robots\/([^/]+)(?:\/check)?\/?$/)
  if (robotRoute) {
    try {
      return valid(decodeURIComponent(robotRoute[1]))
    } catch {
      return null
    }
  }

  if (url.pathname === '/emergency' || url.pathname === '/emergency/') {
    return valid(url.searchParams.get('q') ?? url.searchParams.get('robot'))
  }

  return null
}
