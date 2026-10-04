import type { DefectCode, TaskRepairOptions } from '../../api'
import { Button } from '../../design-system/actions/Button'
import type { RepairPrefillSuggestion } from './repairPrefill'

export type RepairDraftFields = { componentIds: string[]; defectCode: string; solutionMethod: string }
const sameParts = (left: readonly string[], right: readonly string[]) => [...left].sort().join('\0') === [...right].sort().join('\0')

export function RepairPrefillSuggestions({ suggestions, options, defectCodes, values, onApply, onDismiss }: {
  suggestions: readonly RepairPrefillSuggestion[]
  options: TaskRepairOptions
  defectCodes: readonly DefectCode[]
  values: RepairDraftFields
  onApply: (suggestion: RepairPrefillSuggestion) => void
  onDismiss: () => void
}) {
  const partsLabel = (ids: readonly string[]) => ids.map(id => options.components.find(item => item.id === id)?.label ?? id).join(', ')
  const defectLabel = (code: string) => defectCodes.find(item => item.code === code)?.label ?? code
  const methodLabel = (code: string) => options.solution_methods.find(item => item.code === code)?.label ?? code
  const cards = suggestions.map(suggestion => {
    const fields = [
      ...(suggestion.componentIds?.length ? [{ name: 'Деталь', before: partsLabel(values.componentIds), after: partsLabel(suggestion.componentIds), changed: !sameParts(values.componentIds, suggestion.componentIds) }] : []),
      ...(suggestion.defectCode ? [{ name: 'Неисправность', before: defectLabel(values.defectCode), after: defectLabel(suggestion.defectCode), changed: values.defectCode !== suggestion.defectCode }] : []),
      ...(suggestion.solutionMethod ? [{ name: 'Действие', before: methodLabel(values.solutionMethod), after: methodLabel(suggestion.solutionMethod), changed: values.solutionMethod !== suggestion.solutionMethod }] : []),
    ]
    return { suggestion, fields, replacing: fields.some(item => item.changed && item.before) }
  }).filter(card => card.fields.some(item => item.changed))
  if (!cards.length) return null
  return <section aria-label="Подсказки по тексту" className="rp-repair-prefill">
    <div className="rp-repair-prefill-heading"><strong>Подсказки по тексту</strong><Button type="button" variant="ghost" onClick={onDismiss}>Скрыть подсказки</Button></div>
    <p className="muted">Проверьте вариант и подставьте поля одним нажатием.</p>
    {cards.map(({ suggestion, fields, replacing }) => <div className="rp-repair-prefill-card" key={suggestion.id}>
      <ul>{fields.map(field => <li key={field.name}><span>{field.name}: </span>{field.changed && field.before ? <><span>{field.before}</span><span aria-label="заменится на"> → </span></> : null}<strong>{field.after}</strong></li>)}</ul>
      <details><summary>{suggestion.source} · почему этот вариант</summary><p>{suggestion.reason}</p>{suggestion.evidence.map((item, index) => <blockquote key={index}><small>{item.source}</small><br />{item.excerpt}</blockquote>)}</details>
      <Button type="button" variant="secondary" onClick={() => onApply(suggestion)}>{replacing ? 'Заменить выбранные поля' : 'Подставить поля'}</Button>
    </div>)}
  </section>
}
