import { type FormEvent, useRef, useState } from 'react'
import { Button } from '../../design-system/actions/Button'
import { mapApiError } from '../../i18n/errors'

export function ReturnReviewForm({ onSubmit, onCancel }: {
  onSubmit: (reason: string) => Promise<void>
  onCancel: () => void
}) {
  const [reason, setReason] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const submitting = useRef(false)
  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (submitting.current || !reason.trim()) return
    submitting.current = true
    setBusy(true); setError('')
    try { await onSubmit(reason.trim()) }
    catch (cause) { setError(mapApiError(cause, 'Не удалось вернуть задачу. Причина сохранена — попробуйте ещё раз.')) }
    finally { submitting.current = false; setBusy(false) }
  }
  return <form onSubmit={submit} aria-label="Возврат на доработку" aria-busy={busy}>
    <fieldset className="form-grid" disabled={busy} style={{ border: 0, padding: 0, margin: 0, minWidth: 0 }}>
      <label className="field"><span>Что нужно исправить</span><textarea required rows={3} value={reason} onChange={event => setReason(event.target.value)} /></label>
      <p>Причина появится в чате задачи. Механик сможет продолжить работу.</p>
      {error ? <p role="alert">{error}</p> : null}
      <div className="issue-action-row">
        <Button busy={busy} disabled={busy || !reason.trim()} type="submit">Вернуть задачу</Button>
        <Button type="button" variant="secondary" onClick={onCancel}>Отмена</Button>
      </div>
    </fieldset>
  </form>
}
