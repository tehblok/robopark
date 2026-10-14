import { useRef, useState, type FormEvent } from 'react'
import { api, type AnnouncementRequest, type AnnouncementReceipt } from '../api'
import { Button } from '../design-system/actions/Button'
import { Panel } from '../design-system/layout/PageLayout'
import { FormField } from '../design-system/forms/FormField'

export function AnnouncementComposer({ send = api.notificationAnnounce }: { send?: (value: AnnouncementRequest) => Promise<AnnouncementReceipt> }) {
  const [kind, setKind] = useState<AnnouncementRequest['event_type']>('announcement')
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [receipt, setReceipt] = useState<AnnouncementReceipt | null>(null)
  const pending = useRef(false)
  const attempt = useRef<{ content: string; id: string } | null>(null)
  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (pending.current) return
    const message = text.trim()
    if (!message || message.length > 1000) return
    if (navigator.onLine === false) { setError('Для отправки объявления нужна связь с сервером. Текст сохранён в форме.'); return }
    const content = JSON.stringify([kind, message])
    if (attempt.current?.content !== content) attempt.current = { content, id: crypto.randomUUID() }
    pending.current = true; setBusy(true); setError(''); setReceipt(null)
    try {
      const response = await send({ request_id: attempt.current.id, event_type: kind, protected_text: message })
      setReceipt(response); setText(''); attempt.current = null
    } catch { setError('Не удалось подтвердить отправку. Повторите: повторное нажатие не создаст дубликат сообщения.') }
    finally { pending.current = false; setBusy(false) }
  }
  return <Panel title="Объявление сотрудникам" description="Все активные сотрудники получат сообщение в приложении. Системные баннеры зависят от настроек устройства.">
    <form className="rp-announcement-form" onSubmit={event => void submit(event)}>
      <FormField id="announcement-kind" label="Тип сообщения"><select disabled={busy} value={kind} onChange={event => { setKind(event.target.value as AnnouncementRequest['event_type']); setReceipt(null) }}>
        <option value="announcement">Объявление</option><option value="maintenance">Технические работы</option><option value="action_required">Призыв к действию</option>
      </select></FormField>
      <FormField id="announcement-text" label="Текст сообщения" required hint={`${text.length}/1000 · Укажите, что произошло и что нужно сделать сотруднику.`}><textarea disabled={busy} value={text} maxLength={1000} rows={4} onChange={event => { setText(event.target.value); setReceipt(null) }} /></FormField>
      {error ? <p role="alert">{error}</p> : null}
      {receipt ? <p role="status">Объявление принято сервером. Новых сообщений: {receipt.counters.inbox_created}.</p> : null}
      <Button type="submit" busy={busy} disabled={!text.trim()}>Отправить сотрудникам</Button>
    </form>
  </Panel>
}
