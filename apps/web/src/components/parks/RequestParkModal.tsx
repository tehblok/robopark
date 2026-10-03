import { type FormEvent, useEffect, useRef, useState } from 'react'
import { api, type Park } from '../../api'
import { Alert } from '../PageShell'
import { EmptyBlock, SkeletonList, Spinner } from '../ui/Feedback'
import { mapApiError } from '../../i18n/errors'
import { ru } from '../../i18n/ru'
import { resourceStore, useCachedResource } from '../../lib/resource'
import { Dialog } from '../../design-system/overlays/Dialog'

const EMPTY_PARKS: Park[] = []

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
  const closeTimer = useRef<number | null>(null)

  useEffect(() => () => {
    if (closeTimer.current !== null) window.clearTimeout(closeTimer.current)
    closeTimer.current = null
  }, [open])

  const availableRes = useCachedResource<Park[]>(
    open ? 'operator:available-parks' : '',
    () => api.availableParks(),
    { enabled: open },
  )

  const available = availableRes.data ?? EMPTY_PARKS
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
      closeTimer.current = window.setTimeout(() => {
        closeTimer.current = null
        onClose()
      }, 900)
    } catch {
      setSubmitError(ru.parks.requestParkError)
    } finally {
      setSubmitting(false)
    }
  }

  if (!open) return null

  return (
    <Dialog
      closeLabel={ru.nav.close}
      description={ru.parks.requestParkHint}
      dismissible={!submitting}
      onOpenChange={next => { if (!next) onClose() }}
      open={open}
      title={ru.parks.requestPark}
    >
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
            <div className="form-actions">
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
    </Dialog>
  )
}
