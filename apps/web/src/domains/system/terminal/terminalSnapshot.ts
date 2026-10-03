export const MAX_SNAPSHOT_LINES = 2000
export const MAX_SNAPSHOT_BYTES = 256 * 1024

const encoder = new TextEncoder()

export function buildPlainTextSnapshot(source: string[]): string {
  const lines = source.slice(-MAX_SNAPSHOT_LINES)
  while (lines.length && encoder.encode(lines.join('\n')).byteLength > MAX_SNAPSHOT_BYTES) {
    if (lines.length > 1) lines.shift()
    else {
      const bytes = encoder.encode(lines[0]).slice(0, MAX_SNAPSHOT_BYTES)
      lines[0] = new TextDecoder().decode(bytes)
      break
    }
  }
  return lines.join('\n')
}
