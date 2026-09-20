const visibleLabels = new Map([
  ['classificator', 'Classificator'],
  ['comment', 'Comment'],
  ['zone', 'Zone'],
  ['что было сделано', 'Что было сделано'],
  ['рекомендации', 'Рекомендации'],
])

/** Display projection only: preserve the Tracker source and free-form descriptions. */
export function summarizeIssueDescription(text: string): string {
  const source = text.replace(/\r\n?/g, '\n').replace(/&(?:nbsp|#160|#xa0|#32|#x20);/gi, ' ')
    .replace(/<(?:\[|\{)([^\n>]*?)(?:\]|\})>/gu, '$1')
    .replace(/[ \t]+\n/g, '\n').replace(/\n{3,}/g, '\n\n').trim()
  // Bold headings delimit arbitrary template fields. Plain headings are accepted
  // at line starts; known inline labels also delimit single-line export formats.
  const markers = /(?:\*\*|__)([^:\n*_]{1,80}?)(?::(?:\*\*|__)|(?:\*\*|__)[ \t]*:)[ \t]*|^[ \t]*(?:[-*] )?([\p{L}][\p{L}\p{N} _/-]{0,70}):[ \t]*|(?<!\S)(Classificator|Comment|Zone|Что было сделано|Рекомендации|SUF|Port|Partner|Time|Rover name|Mode|Reported mode):[ \t]*/gimu
  const fields = Array.from(source.matchAll(markers), match => ({
    start: match.index,
    valueStart: match.index + match[0].length,
    label: (match[1] ?? match[2] ?? match[3]).trim().replace(/\s+/g, ' ').toLowerCase(),
  }))
  if (!fields.some(field => visibleLabels.has(field.label))) return source
  return fields.flatMap((field, index) => {
    const label = visibleLabels.get(field.label)
    const value = source.slice(field.valueStart, fields[index + 1]?.start).trim()
    if (!label || !value) return []
    return [`**${label}:**${value.includes('\n') ? '\n\n' : ' '}${value}`]
  }).join('\n\n')
}
