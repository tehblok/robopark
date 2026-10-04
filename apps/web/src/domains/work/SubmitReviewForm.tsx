import { type ChangeEvent, type FormEvent, useEffect, useId, useRef, useState } from 'react'
import { ApiError, type DefectCode, type TaskRepairFields, type TaskRepairOptions } from '../../api'
import { RepairComponentPicker } from './RepairComponentPicker'
import { Button } from '../../design-system/actions/Button'
import { mapApiError } from '../../i18n/errors'

const ACCEPTED = 'image/jpeg,image/png,image/webp'
const MAX_BYTES = 15 * 1024 * 1024
const COMMON_METHODS = new Set(['CHANGE', 'REPAIR', 'MAINTENANCE'])

export type SubmitReviewValue = { defectCode: string; photo: File; comment?: string; repairFields?: TaskRepairFields }

export function SubmitReviewForm({ defectCodes, hasQualifyingComment, repairOptions, onRefreshOptions, onSubmit, onCancel }: {
  defectCodes: readonly DefectCode[]
  hasQualifyingComment: boolean
  repairOptions?: TaskRepairOptions
  onRefreshOptions?: () => Promise<TaskRepairOptions>
  onSubmit: (value: SubmitReviewValue) => Promise<void>
  onCancel?: () => void
}) {
  // Initialize once on open: background refresh must not replace the person's draft.
  const [initialOptions, setInitialOptions] = useState(repairOptions)
  const [componentIds, setComponentIds] = useState(() => repairOptions?.selected_component_ids.length ? repairOptions.selected_component_ids : repairOptions?.suggested_component_ids ?? [])
  const [method, setMethod] = useState(repairOptions?.solution_method ?? '')
  const [code, setCode] = useState(repairOptions?.defect_code ?? '')
  const structured = Boolean(initialOptions)
  const validFields = !initialOptions || (componentIds.length > 0 && initialOptions.solution_methods.some(item => item.code === method))
  const commentRequired = !structured && !hasQualifyingComment
  const [comment, setComment] = useState('')
  const [photo, setPhoto] = useState<File | null>(null)
  const [preview, setPreview] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [conflict, setConflict] = useState(false)
  const [refreshed, setRefreshed] = useState(false)
  const methodLabelId = useId()
  const submitting = useRef(false)
  const fileRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (!photo) { setPreview(''); return }
    const url = URL.createObjectURL(photo)
    setPreview(url)
    return () => URL.revokeObjectURL(url)
  }, [photo])

  const clear = () => {
    setPhoto(null)
    if (fileRef.current) fileRef.current.value = ''
  }
  const choose = (event: ChangeEvent<HTMLInputElement>) => {
    const next = event.target.files?.[0]
    setError('')
    if (!next) return
    if (!['image/jpeg', 'image/png', 'image/webp'].includes(next.type)) { clear(); setError('Выберите JPEG, PNG или WebP.'); return }
    if (next.size > MAX_BYTES) { clear(); setError('Фото больше 15 МБ.'); return }
    setPhoto(next)
  }
  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (submitting.current) return
    const cleanComment = comment.trim()
    if (!photo || !defectCodes.some(item => item.code === code) || !validFields || (commentRequired && !cleanComment)) return
    submitting.current = true
    setBusy(true); setError('')
    try {
      await onSubmit({ defectCode: code, photo, comment: cleanComment || undefined,
        ...(initialOptions ? { repairFields: { componentIds, solutionMethod: method, expected: initialOptions.field_snapshot } } : {}),
      })
      setCode(''); setComment(''); clear()
    } catch (cause) {
      setConflict(cause instanceof ApiError && cause.detail === 'repair_fields_conflict')
      setError(mapApiError(cause, 'Не удалось передать задачу на проверку. Данные сохранены в форме — попробуйте ещё раз.'))
    }
    finally { submitting.current = false; setBusy(false) }
  }
  const refreshFields = async () => {
    if (!onRefreshOptions || submitting.current) return
    submitting.current = true
    setBusy(true)
    try {
      const next = await onRefreshOptions()
      if (next.issue_key !== initialOptions?.issue_key) throw new Error('repair_options_mismatch')
      setInitialOptions(next)
      setComponentIds(next.selected_component_ids.length ? next.selected_component_ids : next.suggested_component_ids)
      setCode(next.defect_code ?? '')
      setMethod(next.solution_method ?? '')
      setConflict(false); setError(''); setRefreshed(true)
    } catch (cause) { setError(mapApiError(cause, 'Не удалось обновить поля. Фото и уточнение сохранены — повторите загрузку.')) }
    finally { submitting.current = false; setBusy(false) }
  }
  const commentLabel = !commentRequired ? 'Добавить уточнение' : 'Комментарий о выполненной работе'
  return <form onSubmit={submit} aria-busy={busy}>
    <fieldset className="form-grid rp-form-stack--mobile" disabled={busy}>
    {commentRequired ? <p>Напишите, что было сделано перед передачей на проверку</p> : null}
    {initialOptions ? <>
      <details className="rp-repair-components" open={componentIds.length === 0 ? true : undefined}>
        <summary>Что ремонтируем: {componentIds.map(id => initialOptions.components.find(item => item.id === id)?.label ?? 'Текущая компонента').join(', ') || 'выберите компоненту'} · изменить</summary>
        <RepairComponentPicker options={initialOptions.components} value={componentIds} onChange={setComponentIds} />
      </details>
      <label className="field"><span>Что случилось?</span>
        <select aria-label="Что случилось?" onChange={event => setCode(event.target.value)} required value={code}>
          <option value="">Выберите неисправность</option>
          {Object.entries({ BD: 'Корпус', CH: 'Механика', EL: 'Электрика', WH: 'Проводка', PP: 'Комплектность' }).map(([prefix, label]) => <optgroup key={prefix} label={label}>
            {defectCodes.filter(item => item.code.startsWith(`${prefix}-`)).map(item => <option key={item.code} value={item.code}>{item.label} · {item.code}</option>)}
          </optgroup>)}
        </select>
      </label>
      <div className="field"><span id={methodLabelId}>Что сделали?</span>
        <div aria-labelledby={methodLabelId} className="rp-action-bar rp-repair-methods" role="group">
          {initialOptions.solution_methods.filter(item => COMMON_METHODS.has(item.code)).map(item => <Button aria-pressed={method === item.code} key={item.code} onClick={() => setMethod(item.code)} type="button" variant={method === item.code ? 'primary' : 'secondary'}>{item.label}</Button>)}
        </div>
        {initialOptions.solution_methods.some(item => !COMMON_METHODS.has(item.code)) ? <details className="rp-repair-other-methods" open={method && !COMMON_METHODS.has(method) ? true : undefined}>
          <summary>Другое действие{method && !COMMON_METHODS.has(method) ? `: ${initialOptions.solution_methods.find(item => item.code === method)?.label ?? ''}` : ''}</summary>
          <div aria-label="Другие выполненные действия" className="rp-repair-methods" role="group">
            {initialOptions.solution_methods.filter(item => !COMMON_METHODS.has(item.code)).map(item => <Button aria-pressed={method === item.code} key={item.code} onClick={() => setMethod(item.code)} type="button" variant={method === item.code ? 'primary' : 'secondary'}>{item.label}</Button>)}
          </div>
        </details> : null}
      </div>
      <p className="muted">Отчёт соберём из выбранных полей и фото. При необходимости добавьте детали.</p>
    </> : <>
      <label className="field"><span>Код дефекта</span><input aria-label="Код дефекта" list="task-defect-codes" onChange={event => setCode(event.target.value.trim().toUpperCase())} required type="search" value={code} /></label>
      <datalist id="task-defect-codes">{defectCodes.map(item => <option key={item.code} value={item.code}>{item.label}</option>)}</datalist>
    </>}
    {code && !defectCodes.some(item => item.code === code) ? <p role="alert">Выберите один код из списка.</p> : null}
    <label className="field"><span>{commentLabel}</span><textarea aria-label={commentLabel} onChange={event => setComment(event.target.value)} required={commentRequired} rows={3} value={comment} /></label>
    <div className="issue-action-row rp-action-bar">
      <Button onClick={() => fileRef.current?.click()} type="button" variant="secondary">{photo ? 'Заменить' : 'Сделать фото или выбрать файл'}</Button>
      <input accept={ACCEPTED} aria-label="Сделать фото или выбрать файл" capture="environment" className="issue-attach-input" onChange={choose} ref={fileRef} type="file" />
      {photo ? <Button onClick={clear} type="button" variant="ghost">Удалить</Button> : null}
    </div>
    {preview && photo ? <img alt={`Предпросмотр ${photo.name}`} className="issue-attach-preview" src={preview} /> : null}
    {refreshed ? <p role="status">Поля обновлены из задачи. Проверьте выбор перед отправкой; фото и уточнение сохранены.</p> : null}
    {error ? <p role="alert">{error}</p> : null}
    {conflict && onRefreshOptions ? <Button onClick={() => void refreshFields()} type="button" variant="secondary">Загрузить актуальные поля</Button> : null}
    <Button busy={busy} disabled={busy || !photo || !defectCodes.some(item => item.code === code) || !validFields || (commentRequired && !comment.trim())} type="submit">Передать на проверку</Button>
    {onCancel ? <Button onClick={onCancel} type="button" variant="secondary">Отмена</Button> : null}
    </fieldset>
  </form>
}
