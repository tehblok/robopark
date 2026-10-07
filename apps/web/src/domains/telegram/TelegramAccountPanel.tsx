import { useEffect, useRef, useState } from 'react'
import { ApiError } from '../../api'
import { Alert } from '../../components/PageShell'
import { Button } from '../../design-system/actions/Button'
import { LoadingState } from '../../design-system/feedback/AsyncState'
import { nativeTelegramClient, type NativeTelegramClient, type TelegramAccount, type TelegramLinkCode } from './nativeTelegramApi'
import './telegram.css'

function accountError(error: unknown): string {
  if (error instanceof ApiError && (error.status === 401 || error.status === 403)) return 'Привязка недоступна для этой учётной записи.'
  if (error instanceof ApiError && error.status === 409) return 'Состояние привязки изменилось. Закройте окно и откройте его снова.'
  return 'Не удалось выполнить запрос. Проверьте соединение и повторите попытку.'
}

export function TelegramAccountPanel({ client = nativeTelegramClient }: { client?: NativeTelegramClient }) {
  const [account, setAccount] = useState<TelegramAccount | null>(null)
  const [linkCode, setLinkCode] = useState<TelegramLinkCode | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const generationRef = useRef(0)

  useEffect(() => {
    const generation = ++generationRef.current
    setLoading(true); setBusy(false); setAccount(null); setLinkCode(null); setError('')
    void client.getAccount()
      .then(value => { if (generationRef.current === generation) setAccount(value) })
      .catch(caught => { if (generationRef.current === generation) setError(accountError(caught)) })
      .finally(() => { if (generationRef.current === generation) setLoading(false) })
    return () => { if (generationRef.current === generation) generationRef.current += 1 }
  }, [client])

  const generate = async () => {
    const generation = generationRef.current
    setBusy(true); setError(''); setLinkCode(null)
    try {
      const value = await client.createLinkCode()
      if (generationRef.current === generation) setLinkCode(value)
    } catch (caught) { if (generationRef.current === generation) setError(accountError(caught)) }
    finally { if (generationRef.current === generation) setBusy(false) }
  }

  const unlink = async () => {
    const generation = generationRef.current
    setBusy(true); setError(''); setLinkCode(null)
    try {
      await client.unlinkAccount()
      if (generationRef.current === generation) setAccount({ linked: false, telegram_user_id: null })
    } catch (caught) { if (generationRef.current === generation) setError(accountError(caught)) }
    finally { if (generationRef.current === generation) setBusy(false) }
  }

  if (loading) return <LoadingState label="Проверяем привязку Telegram" variant="inline" />
  return <div className="rp-telegram-account">
    {error ? <Alert tone="error">{error}</Alert> : null}
    {account?.linked ? <>
      <p><strong>Telegram привязан</strong></p>
      <p className="rp-telegram-muted">ID: {account.telegram_user_id}</p>
      <Button busy={busy} onClick={() => void unlink()} variant="danger">Отвязать Telegram</Button>
    </> : <>
      <p><strong>Telegram не привязан</strong></p>
      <p className="rp-telegram-muted">Создайте одноразовый код и отправьте его боту. Код действует 10 минут.</p>
      <Button busy={busy} onClick={() => void generate()} variant="secondary">Получить код привязки</Button>
      {linkCode ? <div className="rp-telegram-link-code" role="status">
        <code>{linkCode.code}</code>
        <p>Отправьте <strong>/link {linkCode.code}</strong> боту в личном чате.</p>
        <small>Действует до {new Date(linkCode.expires_at).toLocaleString('ru-RU')}.</small>
      </div> : null}
    </>}
  </div>
}
