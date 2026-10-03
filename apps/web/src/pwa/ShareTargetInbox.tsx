import { useEffect, useState } from 'react'
import { Button } from '../design-system/actions/Button'
import { clearPendingShareTargetId } from './shareTargetIntent'
import { isAuthTransitionStorageKey, openShareTargetInbox, type ShareDraft, type ShareTargetStore } from './shareTargetStore'
import './ShareTargetInbox.css'

export function ShareTargetInbox({ accountId, shareId, inbox: providedInbox, onAttachTask, onAttachReport, onVerifyAccount }: {
  accountId: number
  shareId: string | null
  inbox?: ShareTargetStore
  onAttachTask?: (taskKey: string, draft: ShareDraft) => Promise<void>
  onAttachReport?: (draft: ShareDraft) => Promise<void>
  onVerifyAccount?: () => Promise<boolean>
}) {
  const [inbox, setInbox] = useState<ShareTargetStore | null>(providedInbox ?? null)
  const [claimed, setClaimed] = useState<{ accountId: number; shareId: string; draft: ShareDraft } | null>(null)
  const [kind, setKind] = useState<'task' | 'report'>('task')
  const [taskKey, setTaskKey] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    let active = true
    let owned: ShareTargetStore | null = null
    if (!shareId) return () => { active = false }
    if (providedInbox) { setInbox(providedInbox); return () => { active = false } }
    if (typeof indexedDB === 'undefined') return () => { active = false }
    void openShareTargetInbox().then(value => { owned = value; if (active) setInbox(value); else value.close?.() }).catch(() => undefined)
    return () => { active = false; owned?.close?.() }
  }, [providedInbox, shareId])

  useEffect(() => {
    if (!inbox || !shareId) return
    let active = true
    void inbox.claim(shareId, accountId).then(draft => {
      if (active) {
        setClaimed(draft ? { accountId, shareId, draft } : null)
        if (!draft) clearPendingShareTargetId(shareId)
      }
    }).catch(() => undefined)
    return () => { active = false }
  }, [inbox, accountId, shareId])

  useEffect(() => {
    const changed = (event: StorageEvent) => {
      if (isAuthTransitionStorageKey(event.key)) setClaimed(null)
    }
    window.addEventListener('storage', changed)
    return () => window.removeEventListener('storage', changed)
  }, [])

  const draft = claimed?.accountId === accountId && claimed.shareId === shareId ? claimed.draft : null
  if (!inbox || !draft || !shareId) return null
  const refresh = async () => {
    const next = await inbox.claim(shareId, accountId)
    setClaimed(next ? { accountId, shareId, draft: next } : null)
    if (!next) clearPendingShareTargetId(shareId)
  }
  const attach = async () => {
    const target = taskKey.trim().toUpperCase()
    if (kind === 'task' && !target) return
    setBusy(true); setError('')
    try {
      if (onVerifyAccount && !await onVerifyAccount()) {
        setError('Сессия изменилась. Войдите в нужный аккаунт и откройте фото снова.')
        return
      }
      if (kind === 'task' && onAttachTask) {
        await onAttachTask(target, draft)
        await inbox.discard(accountId, draft.id)
      } else if (kind === 'report' && onAttachReport) {
        await onAttachReport(draft)
        await inbox.discard(accountId, draft.id)
      } else {
        await inbox.assign(accountId, draft.id, kind === 'task' ? { kind, target } : { kind, target: null })
      }
      await refresh()
    } catch {
      setError('Не удалось прикрепить фото. Черновик сохранён на устройстве.')
    } finally { setBusy(false) }
  }
  const discard = async () => {
    await inbox.discard(accountId, draft.id)
    await refresh()
  }

  return (
    <aside aria-label="Полученное фото" className="rp-share-inbox">
      <div><strong>Получено фото</strong><span>{draft.name}</span></div>
      <label>Куда прикрепить
        <select onChange={event => setKind(event.target.value as 'task' | 'report')} value={kind}>
          <option value="task">К задаче</option><option value="report">К репорту</option>
        </select>
      </label>
      {kind === 'task' ? <label>Номер задачи<input onChange={event => setTaskKey(event.target.value)} placeholder="SDCFLEETOPS-123" value={taskKey} /></label> : null}
      <div className="rp-share-inbox__actions">
        <Button busy={busy} disabled={busy || kind === 'task' && !taskKey.trim()} onClick={() => void attach()} size="compact">Прикрепить</Button>
        <Button aria-label={`Удалить черновик ${draft.name}`} disabled={busy} onClick={() => void discard()} size="compact" variant="ghost">Отменить</Button>
      </div>
      {error ? <p role="alert">{error}</p> : null}
    </aside>
  )
}
