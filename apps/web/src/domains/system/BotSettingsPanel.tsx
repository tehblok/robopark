import { useEffect, useState } from 'react'
import { ApiError } from '../../api'
import { Alert, Panel } from '../../components/PageShell'
import { Button } from '../../design-system/actions/Button'
import { FileField } from '../../design-system/inputs/FileField'
import { botClient, type BotClient, type BotImportReport, type BotStatus } from './botApi'

function errorMessage(error: unknown): string {
  const detail = error instanceof ApiError ? error.detail : error instanceof Error ? error.message : null
  if (detail === 'bot_host_control_unavailable' || detail === 'bot_host_control_state_invalid') return 'управление сервисом недоступно. Проверьте host bridge и состояние сервиса.'
  if (detail === 'telegram_bot_token_required') return 'Сначала сохраните токен Telegram-бота.'
  if (detail === 'telegram_bot_disable_before_rotation') return 'Выключите бота перед заменой токена, затем включите его снова.'
  if (detail === 'telegram_bot_must_be_disabled') return 'Перед импортом выключите бота.'
  if (detail === 'destination_not_empty' || detail === 'destination_changed') return 'Данные уже существуют. Импорт не перезаписывает их.'
  if (detail === 'secret_key_required') return 'На сервере не настроено шифрование секретов.'
  if (error instanceof ApiError) return `Операция не выполнена (HTTP ${error.status}).`
  return 'Операция не выполнена. Проверьте соединение и повторите попытку.'
}

function runtimeLabel(state: string | undefined): string {
  if (state === 'running') return 'Работает'
  if (state === 'starting') return 'Запускается'
  if (state === 'stopped') return 'Остановлен'
  if (state === 'unavailable') return 'Не отвечает'
  return 'Не подтверждён'
}

export function BotSettingsPanel({ client = botClient, showLegacyImport = true }: { client?: BotClient; showLegacyImport?: boolean }) {
  const [status, setStatus] = useState<BotStatus | null>(null)
  const [token, setToken] = useState('')
  const [archive, setArchive] = useState<File | null>(null)
  const [preview, setPreview] = useState<BotImportReport | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [revision, setRevision] = useState(0)

  useEffect(() => {
    let active = true
    setStatus(null); setError('')
    void client.getStatus().then(value => { if (active) { setStatus(value); setError('') } })
      .catch(caught => { if (active) setError(errorMessage(caught)) })
    return () => { active = false }
  }, [client, revision])

  useEffect(() => {
    if (status?.runtime_state !== 'starting' || document.visibilityState !== 'visible') return
    const timer = window.setTimeout(() => setRevision(value => value + 1), 5_000)
    return () => window.clearTimeout(timer)
  }, [status])

  const saveToken = async () => {
    if (!token.trim()) return
    setBusy(true); setError(''); setNotice('')
    try {
      setStatus(await client.setToken(token.trim()))
      setToken('')
      setNotice('Токен сохранён в Robopark.')
    } catch (caught) { setError(errorMessage(caught)) }
    finally { setBusy(false) }
  }

  const toggle = async () => {
    if (!status) return
    setBusy(true); setError(''); setNotice('')
    try {
      const next = await client.setEnabled(!status.desired_enabled)
      setStatus(next)
      setNotice(next.desired_enabled ? 'Запуск бота запрошен. Ожидаем подтверждение процесса.' : 'Остановка бота запрошена. Данные сохранены.')
    } catch (caught) { setError(errorMessage(caught)) }
    finally { setBusy(false) }
  }

  const importArchive = async (execute: boolean) => {
    if (!archive) return
    setBusy(true); setError(''); setNotice('')
    try {
      const report = execute ? await client.executeImport(archive) : await client.previewImport(archive)
      setPreview(report)
      if (execute) setNotice('Данные бота импортированы. Исходный архив остался на вашем устройстве.')
    } catch (caught) { setError(errorMessage(caught)) }
    finally { setBusy(false) }
  }

  return <Panel density="dense" title="Telegram-бот" hint="Бот использует Tracker через основной API Robopark. Telegram-токен хранится только на сервере.">
    <div className="rp-bot-settings">
      {error && <Alert tone="error">{error}</Alert>}
      {notice && <Alert tone="success">{notice}</Alert>}
      <div className="rp-bot-settings__status">
        <div><span>Желаемое состояние</span><strong>{status ? status.desired_enabled ? 'Включён' : 'Выключен' : error ? 'Недоступно' : 'Проверяем…'}</strong></div>
        <div><span>Фактический процесс</span><strong>{runtimeLabel(status?.runtime_state)}</strong></div>
        <div><span>Токен Telegram</span><strong>{status ? status.token_configured ? 'Сохранён' : 'Не задан' : 'Не подтверждён'}</strong></div>
      </div>
      <div className="rp-bot-settings__controls">
        <label className="field"><span className="field-label">Токен Telegram-бота</span><input autoComplete="off" disabled={busy} onChange={event => setToken(event.target.value)} type="password" value={token} /></label>
        <Button busy={busy} disabled={!token.trim()} onClick={() => void saveToken()} type="button" variant="secondary">Сохранить токен</Button>
        <Button busy={busy} disabled={!status || (!status.desired_enabled && !status.token_configured)} onClick={() => void toggle()} type="button" variant={status?.desired_enabled ? 'danger' : 'primary'}>{status?.desired_enabled ? 'Выключить бота' : 'Включить бота'}</Button>
      </div>
      {showLegacyImport && <div className="rp-bot-settings__import">
        <h3>Данные существующего бота</h3>
        <p>Архив проверяется перед импортом. Существующие данные не перезаписываются; бот должен быть выключен.</p>
        <FileField accept=".zip,application/zip" disabled={busy || status?.desired_enabled} label="Архив данных бота" onChange={event => { setArchive(event.target.files?.[0] ?? null); setPreview(null) }} selectedFileLabel={archive?.name} />
        <div className="rp-bot-settings__actions">
          <Button busy={busy} disabled={!archive || status?.desired_enabled} onClick={() => void importArchive(false)} type="button" variant="secondary">Проверить архив</Button>
          {preview && <Button busy={busy} disabled={status?.desired_enabled} onClick={() => void importArchive(true)} type="button">Импортировать данные</Button>}
        </div>
        {preview && <div className="rp-bot-settings__preview"><strong>Проверено: {preview.files.length} файлов, {preview.total_bytes.toLocaleString('ru-RU')} байт</strong><ul>{preview.files.map(file => <li key={file.filename}><span>{file.filename}</span> · {file.byte_count.toLocaleString('ru-RU')} байт</li>)}</ul></div>}
      </div>}
      <Button disabled={busy} onClick={() => setRevision(value => value + 1)} type="button" variant="ghost">Обновить состояние</Button>
    </div>
  </Panel>
}
