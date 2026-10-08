import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError } from '../../api'
import { Alert, Panel } from '../../components/PageShell'
import { Button } from '../../design-system/actions/Button'
import { LoadingState } from '../../design-system/feedback/AsyncState'
import {
  nativeTelegramClient,
  type NativeTelegramClient,
  type TelegramMigrationConflict,
  type TelegramMigrationPreview,
} from './nativeTelegramApi'

const SOURCE_LABELS: Record<TelegramMigrationConflict['source'], string> = {
  locations: 'локации', schedules: 'расписания', broadcasts: 'рассылки', campaigns: 'кампании',
}

const CONFLICT_LABELS: Record<TelegramMigrationConflict['reason'], string> = {
  park_not_found: 'парк с таким тегом не найден',
  destination_conflict: 'у парка уже настроен другой чат',
  location_not_found: 'локация не найдена',
  unsupported_once: 'одноразовое расписание не переносится',
  unsupported_shape: 'формат записи не поддерживается',
  already_imported: 'запись уже перенесена',
}

function jobSchedule(item: TelegramMigrationPreview['jobs'][number]): string {
  if (item.schedule === 'daily') return item.time ?? 'время не задано'
  const hour = (value: number | null) => value == null ? '—' : `${String(value).padStart(2, '0')}:00`
  return `каждый час ${hour(item.start_hour)}–${hour(item.end_hour)}`
}

function migrationError(error: unknown): string {
  if (error instanceof ApiError && error.detail === 'legacy_bot_sources_conflict') {
    return 'В двух старых папках найдены разные настройки бота. Перенос остановлен: сначала нужно сверить эти источники. Существующие чаты и задания не изменены.'
  }
  if (error instanceof ApiError && error.detail === 'legacy_bot_config_invalid') {
    return 'Старый файл настроек повреждён или имеет неподдерживаемый формат. Исправьте источник и обновите предпросмотр.'
  }
  return 'Не удалось проверить или перенести старые конфигурации Telegram.'
}

export function NativeTelegramMigrationPanel({
  client = nativeTelegramClient,
  onApplied,
}: {
  client?: NativeTelegramClient
  onApplied: () => Promise<void>
}) {
  const generationRef = useRef(0)
  const [preview, setPreview] = useState<TelegramMigrationPreview | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [showArchive, setShowArchive] = useState(false)

  const load = useCallback(async () => {
    const generation = ++generationRef.current
    setLoading(true); setError(''); setNotice('')
    try {
      const value = await client.getMigrationPreview()
      if (generationRef.current === generation) setPreview(value)
    } catch (caught) {
      if (generationRef.current === generation) setError(migrationError(caught))
    } finally {
      if (generationRef.current === generation) setLoading(false)
    }
  }, [client])

  useEffect(() => {
    void load()
    return () => { generationRef.current += 1 }
  }, [load])

  const apply = async () => {
    if (!preview) return
    const generation = generationRef.current
    setBusy(true); setError(''); setNotice('')
    try {
      const result = await client.applyMigration(preview.fingerprint)
      if (generationRef.current !== generation) return
      setPreview({ ...result, already_applied: true })
      setNotice(`Перенос выполнен: создано ${result.counts.jobs} выключенных заданий.`)
      try {
        await onApplied()
      } catch {
        if (generationRef.current === generation) setError('Перенос выполнен, но свежие настройки не загрузились. Обновите вкладку.')
      }
    } catch (caught) {
      if (generationRef.current !== generation) return
      if (caught instanceof ApiError && caught.status === 409 && ['migration_fingerprint_changed', 'migration_state_changed'].includes(caught.detail ?? '')) {
        try {
          const fresh = await client.getMigrationPreview()
          if (generationRef.current === generation) {
            setPreview(fresh)
            setError('Файлы или настройки парков изменились. Предпросмотр обновлён — проверьте его перед повторным переносом.')
          }
        } catch {
          if (generationRef.current === generation) setError('Старые файлы изменились, но новый предпросмотр загрузить не удалось.')
        }
      } else {
        setError(migrationError(caught))
      }
    } finally {
      if (generationRef.current === generation) setBusy(false)
    }
  }

  return <Panel
    density="dense"
    hint="Здесь переносится конфигурация старого бота. Текущие рассылки и их состояние находятся в разделе «Задания»."
    title="Перенос старых конфигураций"
  >
    {loading ? <LoadingState label="Проверяем старые конфигурации" variant="inline" /> : null}
    {error ? <Alert tone="error">{error}</Alert> : null}
    {notice ? <Alert tone="success">{notice}</Alert> : null}
    {!preview && !loading ? <Button disabled={busy} onClick={() => void load()} variant="secondary">Обновить предпросмотр</Button> : null}
    {preview && !loading ? <div className="rp-telegram-migration">
      {preview.already_applied ? <>
        <Alert tone="info">Перенос завершён: {preview.counts.jobs} заданий. Управляйте ими ниже в разделе «Задания». Архив переноса не отражает их текущее состояние.</Alert>
        <Button variant="secondary" onClick={() => setShowArchive(value => !value)}>{showArchive ? 'Скрыть архив переноса' : 'Показать архив переноса'}</Button>
      </> : null}
      {!preview.already_applied || showArchive ? <>
      <p className="rp-telegram-muted">
        Найдено: чатов — {preview.counts.park_updates}, заданий — {preview.counts.jobs}, конфликтов — {preview.counts.conflicts}, пропущено — {preview.counts.skipped}.
      </p>
      <p>{preview.already_applied ? 'Снимок на момент переноса. Задания первоначально создавались выключенными; их актуальное состояние смотрите в разделе «Задания».' : 'Будут добавлены только записи с точным совпадением тега парка. Существующие чаты не перезаписываются, задания создаются выключенными.'}</p>

      {preview.park_updates.length > 0 ? <section>
        <h3>Совпавшие чаты парков</h3>
        <ul>{preview.park_updates.map(item => <li key={`${item.park_id}:${item.location_key}`}>
          <strong>{item.park_tag}</strong> ← {item.location_key}: чат {item.chat_id}{item.thread_id == null ? '' : `, тема ${item.thread_id}`}
        </li>)}</ul>
      </section> : null}

      {preview.jobs.length > 0 ? <section>
        <h3>{preview.already_applied ? 'Архив перенесённых заданий' : 'Задания для переноса'}</h3>
        <ul>{preview.jobs.map(item => <li key={item.source_ref}>
          <strong>{item.title}</strong> — парк {item.park_tag}, {jobSchedule(item)}{preview.already_applied ? '' : ', выключено'}
        </li>)}</ul>
      </section> : null}

      {preview.conflicts.length > 0 ? <section>
        <h3>Конфликты и непереносимые записи</h3>
        <ul>{preview.conflicts.map((item, index) => <li key={`${item.source}:${item.source_id ?? 'none'}:${index}`}>
          {SOURCE_LABELS[item.source]}{item.source_id ? ` ${item.source_id}` : ''}{item.park_tag ? `, парк ${item.park_tag}` : ''}: {CONFLICT_LABELS[item.reason]}
        </li>)}</ul>
      </section> : null}

      <div className="rp-telegram-actions">
        <Button
          busy={busy}
          disabled={preview.already_applied || preview.counts.jobs + preview.counts.park_updates === 0}
          onClick={() => void apply()}
        >
          Перенести {preview.counts.jobs} выключенных заданий
        </Button>
        <Button disabled={busy} onClick={() => void load()} variant="secondary">Обновить предпросмотр</Button>
      </div>
      </> : null}
    </div> : null}
  </Panel>
}
