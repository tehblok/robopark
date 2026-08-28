import type { TrackerAttachment, TrackerComment } from '../../api'

const PLATFORM_SIGNATURE_FOOTER_RE =
  /\n([^/\n]+) \/ ([^/\n]+) \/ ([^\n]+)\s*$/

export function splitPlatformComment(text: string): {
  body: string
  signature: string | null
} {
  const normalized = (text || '').trim()
  if (!normalized) {
    return { body: '', signature: null }
  }
  const match = PLATFORM_SIGNATURE_FOOTER_RE.exec(normalized)
  if (!match || match.index === undefined) {
    return { body: normalized, signature: null }
  }
  return {
    body: normalized.slice(0, match.index).trim(),
    signature: match[0].trim(),
  }
}

export function isImageAttachment(attachment: TrackerAttachment): boolean {
  const mime = (attachment.mimetype || '').toLowerCase()
  if (mime.startsWith('image/')) return true
  const name = (attachment.name || '').toLowerCase()
  return /\.(jpe?g|png|webp|heic|heif|gif)$/.test(name)
}

export function sortCommentsChronologically(comments: TrackerComment[]): TrackerComment[] {
  return [...comments].sort((left, right) => {
    const leftTs = Date.parse(left.created_at || '')
    const rightTs = Date.parse(right.created_at || '')
    if (Number.isFinite(leftTs) && Number.isFinite(rightTs) && leftTs !== rightTs) {
      return leftTs - rightTs
    }
    return left.id.localeCompare(right.id)
  })
}
