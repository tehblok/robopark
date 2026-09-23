import { type ChangeEvent, type FormEvent, useEffect, useRef, useState } from 'react'
import type { DefectCode } from '../../api'
import { Button } from '../../design-system/actions/Button'
import { mapApiError } from '../../i18n/errors'

const ACCEPTED = 'image/jpeg,image/png,image/webp'
const MAX_BYTES = 15 * 1024 * 1024

export type SubmitReviewValue = { defectCode: string; photo: File; comment?: string }

export function SubmitReviewForm({ defectCodes, hasQualifyingComment, onSubmit, onCancel }: {
  defectCodes: readonly DefectCode[]
  hasQualifyingComment: boolean
  onSubmit: (value: SubmitReviewValue) => Promise<void>
  onCancel?: () => void
}) {
  const [code, setCode] = useState('')
  const [comment, setComment] = useState('')
  const [photo, setPhoto] = useState<File | null>(null)
  const [preview, setPreview] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
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
    if (!photo || !defectCodes.some(item => item.code === code) || (!hasQualifyingComment && !cleanComment)) return
    submitting.current = true
    setBusy(true); setError('')
    try {
      await onSubmit({ defectCode: code, photo, comment: cleanComment || undefined })
      setCode(''); setComment(''); clear()
    } catch (cause) { setError(mapApiError(cause, 'Не удалось передать задачу на проверку. Данные сохранены в форме — попробуйте ещё раз.')) }
    finally { submitting.current = false; setBusy(false) }
  }
  const commentLabel = hasQualifyingComment ? 'Добавить уточнение' : 'Комментарий о выполненной работе'
  return <form onSubmit={submit} aria-busy={busy}>
    <fieldset className="form-grid rp-form-stack--mobile" disabled={busy}>
    {!hasQualifyingComment ? <p>Напишите, что было сделано перед передачей на проверку</p> : null}
    <label className="field"><span>Код дефекта</span><input aria-label="Код дефекта" list="task-defect-codes" onChange={event => setCode(event.target.value.trim().toUpperCase())} required type="search" value={code} /></label>
    <datalist id="task-defect-codes">{defectCodes.map(item => <option key={item.code} value={item.code}>{item.label}</option>)}</datalist>
    {code && !defectCodes.some(item => item.code === code) ? <p role="alert">Выберите один код из списка.</p> : null}
    <label className="field"><span>{commentLabel}</span><textarea aria-label={commentLabel} onChange={event => setComment(event.target.value)} required={!hasQualifyingComment} rows={3} value={comment} /></label>
    <div className="issue-action-row rp-action-bar">
      <Button onClick={() => fileRef.current?.click()} type="button" variant="secondary">{photo ? 'Заменить' : 'Сделать фото или выбрать файл'}</Button>
      <input accept={ACCEPTED} aria-label="Сделать фото или выбрать файл" className="issue-attach-input" onChange={choose} ref={fileRef} type="file" />
      {photo ? <Button onClick={clear} type="button" variant="ghost">Удалить</Button> : null}
    </div>
    {preview && photo ? <img alt={`Предпросмотр ${photo.name}`} className="issue-attach-preview" src={preview} /> : null}
    {error ? <p role="alert">{error}</p> : null}
    <Button busy={busy} disabled={busy || !photo || !defectCodes.some(item => item.code === code) || (!hasQualifyingComment && !comment.trim())} type="submit">Передать на проверку</Button>
    {onCancel ? <Button onClick={onCancel} type="button" variant="secondary">Отмена</Button> : null}
    </fieldset>
  </form>
}
