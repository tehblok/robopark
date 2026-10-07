import { useEffect, useMemo, useRef, useState } from 'react'
import { ApiError } from '../../api'
import { Alert, Panel } from '../../components/PageShell'
import { Button } from '../../design-system/actions/Button'
import { LoadingState } from '../../design-system/feedback/AsyncState'
import { botConfigClient, type BotConfigClient } from '../system/botConfigApi'

const QUEUES = ['ROBOMAINT', 'SDCWH'] as const

function message(error: unknown): string {
  if (error instanceof ApiError && error.detail === 'telegram_bot_must_be_disabled') return 'Выключите бота перед изменением дополнительных очередей.'
  if (error instanceof ApiError && (error.status === 409 || error.detail === 'bot_config_revision_conflict')) return 'Список изменился в другой вкладке. Обновите данные.'
  return 'Не удалось получить или сохранить дополнительные очереди.'
}

export function BotAuxiliaryQueuesPanel({ client = botConfigClient }: { client?: BotConfigClient }) {
  const generationRef = useRef(0)
  const [selected, setSelected] = useState<string[]>([])
  const [saved, setSaved] = useState<string[]>([])
  const [revision, setRevision] = useState('')
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  useEffect(() => {
    const generation = ++generationRef.current
    setLoading(true); setError(''); setNotice('')
    void client.read().then(document => {
      if (generationRef.current !== generation) return
      const section = document.sections.auxiliary_tracker_queues
      const rawValue = section.value
      const value = Array.isArray(rawValue)
        ? QUEUES.filter(queue => rawValue.includes(queue))
        : []
      setSelected(value); setSaved(value); setRevision(section.revision)
    }).catch(caught => {
      if (generationRef.current === generation) setError(message(caught))
    }).finally(() => {
      if (generationRef.current === generation) setLoading(false)
    })
    return () => { if (generationRef.current === generation) generationRef.current += 1 }
  }, [client])

  const dirty = useMemo(() => selected.join(',') !== saved.join(','), [saved, selected])
  const save = async () => {
    const generation = generationRef.current
    setBusy(true); setError(''); setNotice('')
    try {
      const result = await client.update('auxiliary_tracker_queues', selected, revision)
      if (generationRef.current !== generation) return
      const rawValue = result.value
      const value = Array.isArray(rawValue) ? QUEUES.filter(queue => rawValue.includes(queue)) : selected
      setSelected(value); setSaved(value); setRevision(result.revision); setNotice('Дополнительные очереди сохранены.')
    } catch (caught) {
      if (generationRef.current === generation) setError(message(caught))
    } finally {
      if (generationRef.current === generation) setBusy(false)
    }
  }

  return <Panel density="dense" hint="Бот должен быть выключен. Эти очереди разрешают поиск вне основных очередей парков." title="Дополнительные очереди Tracker">
    {loading ? <LoadingState label="Загружаем дополнительные очереди" variant="inline" /> : <div className="rp-telegram-auxiliary">
      {error ? <Alert tone="error">{error}</Alert> : null}
      {notice ? <Alert tone="success">{notice}</Alert> : null}
      {QUEUES.map(queue => <label key={queue}><input checked={selected.includes(queue)} disabled={busy || !revision} onChange={event => setSelected(current => event.target.checked ? [...current, queue].sort() : current.filter(value => value !== queue))} type="checkbox" />{queue}</label>)}
      <Button busy={busy} disabled={!revision || !dirty} onClick={() => void save()}>Сохранить очереди</Button>
    </div>}
  </Panel>
}
