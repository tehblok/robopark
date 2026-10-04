import type { DefectCode, TaskRepairOptions } from '../../api'

export type RepairTextSource = {
  kind: 'title' | 'description' | 'error' | 'comment' | 'draft'
  text: string
  label?: string
  allowAction?: boolean
}

export type RepairPrefillSuggestion = {
  id: string
  componentIds?: string[]
  defectCode?: string
  solutionMethod?: string
  source: string
  evidence: { source: string; excerpt: string }[]
  reason: string
}

type Candidate = RepairPrefillSuggestion & { score: number }

const MAX_SOURCES = 12
const MAX_SOURCE_LENGTH = 4_000
const MAX_CLAUSES = 32
const MAX_SUGGESTIONS = 3
const SOURCE_LABELS: Record<RepairTextSource['kind'], string> = {
  title: 'Заголовок',
  description: 'Описание',
  error: 'Ошибка',
  comment: 'Комментарий',
  draft: 'Черновик',
}

const METHOD_PATTERNS: Record<string, RegExp> = {
  CHANGE: /(?:заменил(?:а|и)?|заменен(?:а|о|ы)?|поменял(?:а|и)?)/iu,
  REPAIR: /(?:отремонтировал(?:а|и)?|починил(?:а|и)?|восстановил(?:а|и)?)/iu,
  DIAG: /(?:продиагностировал(?:а|и)?|проверил(?:а|и)?)/iu,
  CONFIG: /(?:настроил(?:а|и)?|откалибровал(?:а|и)?)/iu,
  RESTART: /(?:перезапустил(?:а|и)?|перезагрузил(?:а|и)?)/iu,
  INSTALL: /(?:установил(?:а|и)?|смонтировал(?:а|и)?)/iu,
  MAINTENANCE: /(?:обслужил(?:а|и)?|провел(?:а|и)?\s+обслуживание)/iu,
}

function normalize(value: string) {
  return value.normalize('NFKC').toLocaleLowerCase('ru-RU').replaceAll('ё', 'е')
    .replace(/[_‐‑‒–—-]+/gu, ' ').replace(/[^a-zа-я0-9]+/giu, ' ').trim()
}

function stem(token: string) {
  if (!/[a-zа-я]/iu.test(token) || token.length < 5) return token
  return token.replace(/(?:ами|ями|ого|ему|ыми|ими|его|ому|яя|ая|ое|ее|ые|ие|ый|ий|ой|ую|юю|ом|ем|ах|ях|ов|ев|ы|и|а|я|у|ю|е|ь)$/u, '')
}

function stemmed(value: string) {
  return normalize(value).split(' ').filter(Boolean).map(stem)
}

function sequenceSpans(haystack: string[], needle: string[]) {
  if (!needle.length || needle.length > haystack.length) return []
  return haystack.flatMap((_, index) =>
    needle.every((token, offset) => haystack[index + offset] === token)
      ? [{ start: index, end: index + needle.length }]
      : [],
  )
}

function containsSequence(haystack: string[], needle: string[]) {
  return sequenceSpans(haystack, needle).length > 0
}

function componentMatch(clause: string, options: TaskRepairOptions) {
  const clauseNormalized = normalize(clause)
  const clauseTokens = stemmed(clause)
  const matches = options.components.flatMap(component => {
    const names = [component.label, component.tracker_name].filter((value): value is string => Boolean(value))
    const canonicalMatches = names.flatMap(name => {
      const tokens = stemmed(name)
      return sequenceSpans(clauseTokens, tokens).map(span => ({
        id: component.id, label: component.label, score: tokens.length * 100, ...span,
      }))
    })
    const aliasMatches = (component.aliases ?? []).flatMap(alias => {
      const tokens = stemmed(alias)
      const exactBonus = normalize(alias) === clauseNormalized ? 10 : 0
      return sequenceSpans(clauseTokens, tokens).map(span => ({
        id: component.id, label: component.label, score: tokens.length * 80 + exactBonus, ...span,
      }))
    })
    return [...canonicalMatches, ...aliasMatches]
  })
  const unsuppressed = matches.filter(match => !matches.some(other =>
    other.id !== match.id
      && other.start <= match.start
      && other.end >= match.end
      && other.end - other.start > match.end - match.start,
  ))
  const byComponent = new Map<string, (typeof unsuppressed)[number]>()
  for (const match of unsuppressed) {
    const saved = byComponent.get(match.id)
    if (!saved || match.score > saved.score) byComponent.set(match.id, match)
  }
  const winners = [...byComponent.values()]
  return { match: winners.length === 1 ? winners[0] : undefined, ambiguous: winners.length > 1 }
}

function defectMatch(clause: string, defectCodes: readonly DefectCode[]) {
  const tokens = stemmed(clause)
  const normalized = normalize(clause)
  const matches = defectCodes.flatMap(defect => {
    const escapedCode = defect.code.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
    const exactCode = new RegExp(`(^|[^A-Z0-9])${escapedCode}([^A-Z0-9]|$)`, 'iu').test(clause)
    const labelTokens = stemmed(defect.label)
    const labelMatch = labelTokens.length > 0 && containsSequence(tokens, labelTokens)
    const descriptionMatch = defect.description && normalize(defect.description) === normalized
    const negated = new RegExp(`(?:не|исключен)\\s+(?:код\\s+)?${escapedCode}|${escapedCode}.{0,24}(?:не\\s+подтверд|исключен|ошибоч)`, 'iu').test(clause)
      || normalized.includes(`не ${normalize(defect.label)}`)
    if (negated) return []
    const score = exactCode ? 1_000 : labelMatch ? labelTokens.length * 10 : descriptionMatch ? 1 : 0
    return score ? [{ code: defect.code, label: defect.label, score }] : []
  })
  const best = Math.max(0, ...matches.map(match => match.score))
  const winners = matches.filter(match => match.score === best)
  return winners.length === 1 ? winners[0] : undefined
}

function methodMatch(clause: string, source: RepairTextSource, options: TaskRepairOptions) {
  const normalized = normalize(clause)
  const tokens = normalized.split(' ')
  // Quote/report markers can be on another line; splitting must not turn copied
  // history into a statement that the current mechanic performed the repair.
  const unsafe = /["«»`]/u.test(source.text)
    || normalize(source.text).includes('выполненные работы')
    || /^\s*>/u.test(clause)
    || clause.includes('?')
    || normalized.includes('выполненные работы')
    || normalized.includes('если бы')
    || normalized.includes('хотел чтобы')
    || normalized.includes('хотела чтобы')
    || normalized.includes('хотели чтобы')
    || tokens.some(token => ['не', 'нужно', 'надо', 'следует', 'требуется', 'возможно', 'вероятно', 'если', 'бы', 'чтобы', 'ли'].includes(token)
      || token.startsWith('планир') || token.startsWith('предполож'))
  if (source.kind !== 'draft' || source.allowAction !== true || unsafe) return undefined
  const allowed = new Set(options.solution_methods.map(method => method.code))
  const matches = Object.entries(METHOD_PATTERNS).filter(([code, pattern]) => allowed.has(code) && pattern.test(normalized))
  return matches.length === 1 ? matches[0][0] : undefined
}

function excerpt(value: string) {
  const compact = value.replace(/\s+/gu, ' ').trim()
  return compact.length <= 160 ? compact : `${compact.slice(0, 157).trimEnd()}…`
}

function sourceLabel(source: RepairTextSource) {
  return source.label?.trim() || SOURCE_LABELS[source.kind]
}

function makeCandidate(
  source: RepairTextSource,
  clause: string,
  sourceIndex: number,
  clauseIndex: number,
  options: TaskRepairOptions,
  defectCodes: readonly DefectCode[],
): Candidate | undefined {
  const componentResult = componentMatch(clause, options)
  const component = componentResult.match
  const defect = defectMatch(clause, defectCodes)
  const method = componentResult.ambiguous ? undefined : methodMatch(clause, source, options)
  if (!component && !defect && !method) return undefined
  const reasons = [
    component ? `В тексте упомянута деталь: ${component.label}` : '',
    defect ? `Найден код или название неисправности: ${defect.code}` : '',
    method ? 'В черновике описано уже выполненное действие' : '',
  ].filter(Boolean)
  return {
    id: `${source.kind}-${sourceIndex}-${clauseIndex}-${component?.id ?? 'none'}-${defect?.code ?? 'none'}-${method ?? 'none'}`,
    componentIds: component ? [component.id] : undefined,
    defectCode: defect?.code,
    solutionMethod: method,
    source: sourceLabel(source),
    evidence: [{ source: sourceLabel(source), excerpt: excerpt(clause) }],
    reason: reasons.join('. '),
    score: (component?.score ?? 0) + (defect?.score ?? 0) + (method ? 500 : 0),
  }
}

function compatible(left: Candidate, right: Candidate) {
  const leftComponent = left.componentIds?.[0]
  const rightComponent = right.componentIds?.[0]
  return Boolean(leftComponent && leftComponent === rightComponent)
    && (!left.defectCode || !right.defectCode || left.defectCode === right.defectCode)
    && (!left.solutionMethod || !right.solutionMethod || left.solutionMethod === right.solutionMethod)
}

function mergeCandidates(candidates: Candidate[]) {
  const merged: Candidate[] = []
  for (const candidate of candidates) {
    const target = merged.find(existing => compatible(existing, candidate))
    if (!target) {
      merged.push(candidate)
      continue
    }
    target.defectCode ??= candidate.defectCode
    target.solutionMethod ??= candidate.solutionMethod
    if (!target.source.split(' · ').includes(candidate.source)) target.source += ` · ${candidate.source}`
    for (const item of candidate.evidence) {
      if (!target.evidence.some(existing => existing.source === item.source && existing.excerpt === item.excerpt)) target.evidence.push(item)
    }
    target.reason = [...new Set(`${target.reason}. ${candidate.reason}`.split('. ').filter(Boolean))].join('. ')
    target.score = Math.max(target.score, candidate.score) + 1
  }
  return merged
}

export function suggestRepairFields({
  options,
  defectCodes,
  sources,
}: {
  options: TaskRepairOptions
  defectCodes: readonly DefectCode[]
  sources: readonly RepairTextSource[]
}): RepairPrefillSuggestion[] {
  let clauseCount = 0
  const candidates: Candidate[] = []
  sources.slice(0, MAX_SOURCES).forEach((source, sourceIndex) => {
    const boundedSource = { ...source, text: source.text.slice(0, MAX_SOURCE_LENGTH) }
    boundedSource.text.split(/[\n.!;]+/u).forEach((clause, clauseIndex) => {
      if (clauseCount >= MAX_CLAUSES || !clause.trim()) return
      clauseCount += 1
      const candidate = makeCandidate(boundedSource, clause, sourceIndex, clauseIndex, options, defectCodes)
      if (candidate) candidates.push(candidate)
    })
  })
  return mergeCandidates(candidates)
    .sort((left, right) => right.score - left.score)
    .slice(0, MAX_SUGGESTIONS)
    .map(({ score: _score, ...suggestion }) => suggestion)
}
