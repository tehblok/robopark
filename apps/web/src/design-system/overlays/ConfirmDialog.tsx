import { useEffect, useId, useState } from 'react'
import { Button } from '../actions/Button'
import { Dialog } from './Dialog'

export type ConfirmDialogProps = {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: string
  description: string
  confirmLabel: string
  cancelLabel?: string
  tone?: 'default' | 'danger'
  confirmationPhrase?: string
  pending?: boolean
  error?: string | null
  onConfirm: () => void | Promise<void>
}

export function ConfirmDialog({
  open,
  onOpenChange,
  title,
  description,
  confirmLabel,
  cancelLabel = 'Отмена',
  tone = 'default',
  confirmationPhrase,
  pending = false,
  error,
  onConfirm,
}: ConfirmDialogProps) {
  const [typedPhrase, setTypedPhrase] = useState('')
  const phraseInputId = useId()

  useEffect(() => {
    setTypedPhrase('')
  }, [confirmationPhrase, open])

  const phraseMatches = confirmationPhrase === undefined || typedPhrase === confirmationPhrase
  const footer = (
    <>
      <Button
        disabled={pending}
        onClick={() => onOpenChange(false)}
        variant="secondary"
      >
        {cancelLabel}
      </Button>
      <Button
        busy={pending}
        disabled={!phraseMatches || pending}
        onClick={() => onConfirm()}
        variant={tone === 'danger' ? 'danger' : 'primary'}
      >
        {confirmLabel}
      </Button>
    </>
  )

  return (
    <Dialog
      description={description}
      dismissible={!pending}
      footer={footer}
      onOpenChange={onOpenChange}
      open={open}
      role="alertdialog"
      title={title}
    >
      {confirmationPhrase !== undefined ? (
        <div className="rp-confirm-dialog__phrase">
          <label htmlFor={phraseInputId}>
            Введите <strong>{confirmationPhrase}</strong> для подтверждения
          </label>
          <input
            autoComplete="off"
            disabled={pending}
            id={phraseInputId}
            onChange={(event) => setTypedPhrase(event.target.value)}
            spellCheck={false}
            type="text"
            value={typedPhrase}
          />
        </div>
      ) : null}
      {error ? (
        <p aria-live="assertive" className="rp-confirm-dialog__error" role="alert">
          {error}
        </p>
      ) : null}
    </Dialog>
  )
}
