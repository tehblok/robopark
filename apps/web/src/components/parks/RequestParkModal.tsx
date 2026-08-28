import { type FormEvent, useEffect, useState } from 'react'
import { api, type Park } from '../../api'
import { Alert } from '../PageShell'
import { EmptyBlock, SkeletonList, Spinner } from '../ui/Feedback'
import { mapApiError } from '../../i18n/errors'
import { ru } from '../../i18n/ru'
import { resourceStore, useCachedResource } from '../../lib/resource'

export function RequestParkModal({
  open,
  onClose,
  onSubmitted,
}: {
  open: boolean
  onClose: () => void
  onSubmitted?: () => void
}) {
  const [parkId, setParkId] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [submitError, setSubmitError] = useState('')
  const [success, setSuccess] = useState('')

  const availableRes = useCachedResource<Park[]>(
    open ? 'operator:available-parks' : '',
    () => api.availableParks(),
    { enabled: open },
  )

  const available = availableRes.data ?? []
  const loading = open && availableRes.isLoading && !availableRes.data
  const loadError = availableRes.error
    ? mapApiError(availableRes.error, ru.errors.load)
    : ''

  useEffect(() => {
    if (!open) {
      setParkId('')
      setSubmitError('')
      setSuccess('')
      return
    }
    if (available.length && !parkId) {
      setParkId(String(available[0].id))
    }
  }, [open, available, parkId])

  useEffect(() => {
    if (!open) return
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !submitting) onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose, submitting])

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (!parkId) return
    setSubmitError('')
    setSuccess('')
    setSubmitting(true)
    try {
      await api.requestPark(Number(parkId))
      setSuccess(ru.parks.requestParkSuccess)
      resourceStore.invalidate('operator:available-parks')
      resourceStore.invalidate('operator:park-requests')
      resourceStore.invalidate('operator:parks')
      await availableRes.refresh()
      onSubmitted?.()
      window.setTimeout(() => onClose(), 900)
    } catch {
      setSubmitError(ru.parks.requestParkError)
    } finally {
      setSubmitting(false)
    }
  }

  if (!open) return null

  return (
    <>
      <button
        aria-label={ru.nav.close}
        className="modal-backdrop"
        disabled={submitting}
        onClick={onClose}
        type="button"
      />
      <div
        aria-labelledby="request-park-title"
        aria-modal="true"
        className="modal-panel"
        role="dialog"
      >
        <header className="modal-head">
          <h2 id="request-park-title">{ru.parks.requestPark}</h2>
          <button
            aria-label={ru.nav.close}
            className="btn-ghost modal-close"
            disabled={submitting}
            onClick={onClose}
            type="button"
          >
            ×
          </button>
        </header>

        <p className="modal-hint">{ru.parks.requestParkHint}</p>

        {loadError && <Alert tone="error">{loadError}</Alert>}
        {submitError && <Alert tone="error">{submitError}</Alert>}
        {success && <Alert tone="success">{success}</Alert>}

        {loading ? (
          <SkeletonList rows={2} />
        ) : available.length ? (
          <form className="form-grid" onSubmit={(event) => void submit(event)}>
            <label className="field">
              <span className="field-label">{ru.nav.park}</span>
              <select
                aria-label={ru.nav.park}
                disabled={submitting}
                onChange={(event) => setParkId(event.target.value)}
                value={parkId}
              >
                {available.map((park) => (
                  <option key={park.id} value={park.id}>
                    {park.name} ({park.tag})
                  </option>
                ))}
              </select>
            </label>
            <div className="form-actions modal-actions">
              <button className="btn btn-secondary" disabled={submitting} onClick={onClose} type="button">
                {ru.nav.close}
              </button>
              <button className="btn" disabled={!parkId || submitting} type="submit">
                {submitting ? <Spinner label={ru.parks.requestParkSubmitting} /> : ru.parks.requestParkSubmit}
              </button>
            </div>
          </form>
        ) : (
          <EmptyBlock hint={ru.parks.requestParkEmptyHint} icon="✓" title={ru.parks.requestParkEmpty} />
        )}
      </div>
    </>
  )
}
