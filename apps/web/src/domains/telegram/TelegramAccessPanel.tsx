import { useEffect, useRef, useState } from 'react'
import { request } from '../../api'
import { Alert } from '../../components/PageShell'
import { Button } from '../../design-system/actions/Button'
import { LoadingState } from '../../design-system/feedback/AsyncState'

type AccessRequest = { id: number; status: string; park: { id: number; name: string } }
type AccessState = {
  access_status: string
  available_parks: { id: number; name: string }[]
  assigned_parks: { id: number; name: string }[]
  requests: AccessRequest[]
}
const telegramAccessClient = {
  get: () => request<AccessState>('/access'),
  create: (parkId: number) => request<AccessRequest>('/access/requests', { method: 'POST', body: JSON.stringify({ park_id: parkId }) }),
}

export function TelegramAccessPanel({ client = telegramAccessClient, onRefresh }: { client?: typeof telegramAccessClient; onRefresh: () => Promise<unknown> }) {
  const [data, setData] = useState<AccessState | null>(null)
  const [parkId, setParkId] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const generation = useRef(0)
  useEffect(() => {
    const current = ++generation.current
    setData(null); setError(''); setBusy(false); setParkId('')
    void client.get().then(value => { if (generation.current === current) setData(value) })
      .catch(() => { if (generation.current === current) setError('Не удалось загрузить заявки. Повторите проверку.') })
    return () => { generation.current += 1 }
  }, [client])

  const perform = async (create = false) => {
    const current = generation.current
    setBusy(true); setError('')
    try {
      if (create) await client.create(Number(parkId))
      const state = await client.get()
      if (generation.current !== current) return
      setData(state); setParkId('')
      await onRefresh()
    } catch { if (generation.current === current) setError('Не удалось обновить заявку. Проверьте соединение и повторите.') }
    finally { if (generation.current === current) setBusy(false) }
  }
  const pending = new Set(data?.requests.filter(item => item.status === 'pending').map(item => item.park.id))
  const parks = data?.available_parks.filter(park => !pending.has(park.id)) ?? []
  return <div className="rp-telegram-account">
    <h2>Заявка в парк</h2>
    <p>Выберите парк. Любой из его администраторов сможет одобрить заявку на сайте или в Telegram.</p>
    {error ? <Alert tone="error">{error}</Alert> : null}
    {!data && !error ? <LoadingState label="Загружаем заявки" variant="inline" /> : null}
    {data?.requests.map(item => <p key={item.id}>{item.park.name}: {item.status === 'pending' ? 'ожидает одобрения' : item.status === 'approved' ? 'одобрена' : 'отклонена'}</p>)}
    {parks.length ? <>
      <label className="field"><span className="field-label">Парк для доступа</span><select onChange={event => setParkId(event.target.value)} value={parkId}><option value="">Выберите парк</option>{parks.map(park => <option key={park.id} value={park.id}>{park.name}</option>)}</select></label>
      <Button busy={busy} disabled={!parkId} onClick={() => void perform(true)}>Запросить доступ</Button>
    </> : null}
    <Button disabled={busy} onClick={() => void perform()} variant="secondary">Проверить одобрение</Button>
  </div>
}
