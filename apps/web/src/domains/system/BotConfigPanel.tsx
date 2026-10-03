import { useEffect, useState } from 'react'
import { ApiError } from '../../api'
import { Alert, Panel } from '../../components/PageShell'
import { Button } from '../../design-system/actions/Button'
import { botConfigClient, type BotConfigClient, type BotConfigDocument, type BotConfigSection } from './botConfigApi'

const SECTIONS: { key: BotConfigSection; title: string; hint: string }[] = [
  { key: 'locations', title: 'Парки и Telegram-чаты', hint: 'Названия, теги Tracker, чаты и участие в рассылках.' },
  { key: 'users', title: 'Пользователи бота', hint: 'Сохранённые Telegram-пользователи и привязки.' },
  { key: 'roles', title: 'Роли и права', hint: 'Администраторы и доступные действия бота.' },
  { key: 'schedules', title: 'Расписание и окно отправки', hint: 'Задания, время, повторы и часы отправки.' },
  { key: 'broadcasts', title: 'Рассылки', hint: 'Кампании и их настройки без служебных счётчиков доставки.' },
  { key: 'campaigns', title: 'СК-кампании', hint: 'Правила кампаний и парки; история отправок сохраняется отдельно.' },
  { key: 'auxiliary_tracker_queues', title: 'Дополнительные очереди Tracker', hint: 'Разрешённые очереди для поиска бота вне основных парков.' },
]

function message(error: unknown): string {
  const detail = error instanceof ApiError ? error.detail : error instanceof Error ? error.message : null
  if (detail === 'bot_config_revision_conflict' || detail === 'stale_revision') return 'Настройки изменились в Telegram или другой вкладке. Обновите данные перед повторной записью.'
  if (detail === 'telegram_bot_must_be_disabled') return 'Выключите бота перед изменением этих настроек. После сохранения включите снова.'
  if (typeof detail === 'string' && detail.startsWith('bot_config_invalid_schema')) return 'Данные не соответствуют ожидаемому формату. Проверьте структуру и обязательные поля.'
  if (typeof detail === 'string' && detail.startsWith('bot_config_unsafe_state')) return 'Файл настроек нельзя безопасно прочитать или записать. Проверьте состояние хоста.'
  if (error instanceof ApiError) return `Настройки недоступны (HTTP ${error.status}).`
  return 'Не удалось сохранить настройки. Проверьте связь с Robopark.'
}

function count(value: unknown): string {
  if (Array.isArray(value)) return `${value.length} записей`
  if (value && typeof value === 'object') {
    for (const key of ['locations', 'campaigns', 'jobs', 'roles', 'users']) {
      const items = (value as Record<string, unknown>)[key]
      if (Array.isArray(items)) return `${items.length} записей`
      if (items && typeof items === 'object') return `${Object.keys(items).length} записей`
    }
    return `${Object.keys(value).length} полей`
  }
  return 'Пусто'
}

function parsedDraft(source: string | undefined, fallback: unknown): unknown {
  if (source === undefined) return fallback
  try { return JSON.parse(source) as unknown } catch { return null }
}

function scheduleWindow(value: unknown): { start_hour: number; end_hour: number; enabled: boolean } | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null
  const window = (value as Record<string, unknown>).send_window
  if (!window || typeof window !== 'object' || Array.isArray(window)) return null
  const candidate = window as Record<string, unknown>
  if (typeof candidate.start_hour !== 'number' || typeof candidate.end_hour !== 'number' || typeof candidate.enabled !== 'boolean') return null
  return candidate as { start_hour: number; end_hour: number; enabled: boolean }
}

function rowsFor(section: BotConfigSection, value: unknown): Record<string, unknown>[] | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null
  const rows = (value as Record<string, unknown>)[section === 'schedules' ? 'jobs' : 'campaigns']
  return Array.isArray(rows) && rows.every(row => row && typeof row === 'object' && !Array.isArray(row))
    ? rows as Record<string, unknown>[] : null
}

export function BotConfigPanel({ client = botConfigClient }: { client?: BotConfigClient }) {
  const [config, setConfig] = useState<BotConfigDocument | null>(null)
  const [drafts, setDrafts] = useState<Partial<Record<BotConfigSection, string>>>({})
  const [busy, setBusy] = useState<BotConfigSection | null>(null)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [revision, setRevision] = useState(0)

  useEffect(() => {
    let active = true
    setConfig(null); setError('')
    void client.read().then(value => {
      if (active) { setConfig(value); setDrafts({}); setError('') }
    }).catch(caught => { if (active) setError(caught instanceof ApiError ? message(caught) : 'Не удалось загрузить настройки. Проверьте связь с Robopark.') })
    return () => { active = false }
  }, [client, revision])

  const save = async (section: BotConfigSection, value: unknown) => {
    const current = config?.sections[section]
    if (!current || busy) return
    setBusy(section); setError(''); setNotice('')
    try {
      const saved = await client.update(section, value, current.revision)
      setConfig(previous => previous ? {
        ...previous,
        sections: { ...previous.sections, [section]: saved },
      } : previous)
      setDrafts(previous => ({ ...previous, [section]: undefined }))
      setNotice('Настройки сохранены. Telegram-бот использует тот же каталог данных.')
    } catch (caught) { setError(message(caught)) }
    finally { setBusy(null) }
  }

  const saveDraft = (section: BotConfigSection) => {
    const source = drafts[section]
    if (source === undefined) return
    try {
      void save(section, JSON.parse(source) as unknown)
    } catch { setError('Некорректный JSON. Проверьте запятые и кавычки перед сохранением.') }
  }

  const changeScheduleWindow = (field: 'start_hour' | 'end_hour' | 'enabled', value: number | boolean) => {
    const current = parsedDraft(drafts.schedules, config?.sections.schedules.value)
    const window = scheduleWindow(current)
    if (!window || !current || typeof current !== 'object' || Array.isArray(current)) return
    setDrafts(previous => ({ ...previous, schedules: JSON.stringify({
      ...current,
      send_window: { ...window, [field]: value },
    }, null, 2) }))
  }

  const changeRow = (section: 'schedules' | 'broadcasts' | 'campaigns', index: number, field: 'enabled' | 'fire_at', value: boolean | string) => {
    const current = parsedDraft(drafts[section], config?.sections[section].value)
    const rows = rowsFor(section, current)
    if (!rows || !current || typeof current !== 'object' || Array.isArray(current) || !rows[index]) return
    const next = rows.map((row, position) => position === index ? { ...row, [field]: value } : row)
    setDrafts(previous => ({ ...previous, [section]: JSON.stringify({ ...current, [section === 'schedules' ? 'jobs' : 'campaigns']: next }, null, 2) }))
  }

  const sections = config?.sections
  return <Panel density="dense" title="Настройки бота" hint="Telegram и сайт работают с одним состоянием. Изменения с защитой от перезаписи более свежей версии.">
    <div className="rp-bot-config">
      {error && <Alert tone="error">{error}</Alert>}
      {notice && <Alert tone="success">{notice}</Alert>}
      {!sections ? !error && <p>Загружаем настройки…</p> : <>
        <div className="rp-bot-config__quick">
          <div><strong>Профиль отправки</strong><p>Выбор между рабочими и тестовыми чатами.</p><select aria-label="Профиль отправки" disabled={busy !== null} onChange={event => void save('profile', event.target.value)} value={String(sections.profile.value)}><option value="prod">Рабочие чаты</option><option value="test">Тестовые чаты</option></select></div>
          <div><strong>Диспетчер</strong><p>{sections.dispatcher_pause.value ? 'Приостановлен' : 'Работает'}</p><Button busy={busy === 'dispatcher_pause'} disabled={busy !== null} onClick={() => void save('dispatcher_pause', !sections.dispatcher_pause.value)} type="button" variant="secondary">{sections.dispatcher_pause.value ? 'Возобновить диспетчер' : 'Приостановить диспетчер'}</Button></div>
          <div><strong>Отправка</strong><p>{sections.send_pause.value ? 'Отправка приостановлена' : 'Отправка работает'}</p><Button busy={busy === 'send_pause'} disabled={busy !== null} onClick={() => void save('send_pause', !sections.send_pause.value)} type="button" variant="secondary">{sections.send_pause.value ? 'Возобновить отправку' : 'Приостановить отправку'}</Button></div>
        </div>
        <p className="rp-bot-config__hint">Структурные настройки меняются при выключенном боте. Перед записью сохраните копию данных. Редактор показывает точный формат, который читает Telegram-бот.</p>
        <div className="rp-bot-config__sections">{SECTIONS.map(({ key, title, hint }) => {
          const section = sections[key]
          const draft = drafts[key]
          const editedValue = parsedDraft(draft, section.value)
          const window = key === 'schedules' ? scheduleWindow(editedValue) : null
          const rowSection = key === 'schedules' || key === 'broadcasts' || key === 'campaigns' ? key : null
          const rows = rowSection ? rowsFor(rowSection, editedValue) : null
          return <details key={key} className="rp-bot-config__section"><summary><span><strong>{title}</strong><small>{hint}</small></span><span className="rp-bot-config__count">{count(section.value)}</span></summary>
            <div className="rp-bot-config__editor">
              {key === 'auxiliary_tracker_queues' && Array.isArray(editedValue) && <div className="rp-bot-config__form">
                <label className="field"><span className="field-label">Очереди Tracker через запятую</span><input aria-label="Очереди Tracker через запятую" autoCapitalize="characters" onChange={event => setDrafts(previous => ({ ...previous, [key]: JSON.stringify(event.target.value.split(',').map(item => item.trim().toUpperCase()).filter(Boolean)) }))} type="text" value={editedValue.join(', ')} /></label>
                <p>Добавляйте только очереди, которые бот должен читать. Доступ к ним будет у его поискового шлюза.</p>
                <Button busy={busy === key} disabled={busy !== null || draft === undefined} onClick={() => saveDraft(key)} type="button">Сохранить дополнительные очереди</Button>
              </div>}
              {key === 'schedules' && window && <div className="rp-bot-config__form">
                <strong>Окно отправки</strong>
                <div className="rp-bot-config__form-row"><label className="field"><span className="field-label">Начало отправки, час</span><input aria-label="Начало отправки, час" max={23} min={0} onChange={event => changeScheduleWindow('start_hour', Number(event.target.value))} type="number" value={window.start_hour} /></label><label className="field"><span className="field-label">Конец отправки, час</span><input aria-label="Конец отправки, час" max={23} min={0} onChange={event => changeScheduleWindow('end_hour', Number(event.target.value))} type="number" value={window.end_hour} /></label></div>
                <label className="rp-bot-config__check"><input checked={window.enabled} onChange={event => changeScheduleWindow('enabled', event.target.checked)} type="checkbox" />Использовать окно отправки</label>
                <Button busy={busy === key} disabled={busy !== null || draft === undefined} onClick={() => saveDraft(key)} type="button">Сохранить окно отправки</Button>
              </div>}
              {rows && rowSection && <div className="rp-bot-config__form">
                <strong>{key === 'schedules' ? 'Задания' : key === 'broadcasts' ? 'Рассылки' : 'СК-кампании'}</strong>
                {rows.length === 0 ? <p>Пока нет записей. Добавить новые можно в Telegram или через расширенные настройки.</p> : <div className="rp-bot-config__rows">{rows.map((row, index) => {
                  const label = String(row.label ?? row.tag ?? row.id ?? `№ ${index + 1}`)
                  const kind = key === 'schedules' ? 'Задание' : key === 'broadcasts' ? 'Рассылка' : 'Кампания'
                  return <div className="rp-bot-config__row" key={String(row.id ?? index)}>
                    <label className="rp-bot-config__check"><input aria-label={`${kind} ${label} включена`} checked={row.enabled === true} onChange={event => changeRow(rowSection, index, 'enabled', event.target.checked)} type="checkbox" /><span>{label}</span></label>
                    <input aria-label={`${kind} ${label}: время`} onChange={event => changeRow(rowSection, index, 'fire_at', event.target.value)} type="time" value={typeof row.fire_at === 'string' ? row.fire_at : ''} />
                  </div>
                })}</div>}
                <Button busy={busy === key} disabled={busy !== null || draft === undefined} onClick={() => saveDraft(key)} type="button">Сохранить {key === 'schedules' ? 'расписание' : key === 'broadcasts' ? 'рассылки' : 'СК-кампании'}</Button>
              </div>}
              <details className="rp-bot-config__advanced"><summary>Расширенные настройки: JSON</summary>
              <label className="field"><span className="field-label">{title}: данные JSON</span><textarea aria-label={`${title}: данные JSON`} onChange={event => setDrafts(previous => ({ ...previous, [key]: event.target.value }))} rows={12} spellCheck={false} value={draft ?? JSON.stringify(section.value, null, 2)} /></label>
              <div className="rp-bot-config__actions"><Button busy={busy === key} disabled={busy !== null || draft === undefined} onClick={() => saveDraft(key)} type="button">Сохранить JSON: {title.toLowerCase()}</Button><Button disabled={busy !== null || draft === undefined} onClick={() => setDrafts(previous => ({ ...previous, [key]: undefined }))} type="button" variant="secondary">Отменить</Button></div>
              </details>
            </div>
          </details>
        })}</div>
      </>}
      <Button disabled={busy !== null} onClick={() => setRevision(value => value + 1)} type="button" variant="ghost">Обновить настройки</Button>
    </div>
  </Panel>
}
