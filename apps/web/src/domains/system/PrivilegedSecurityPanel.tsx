import { useEffect, useState } from 'react'
import { ApiError } from '../../api'
import { Alert, Panel } from '../../components/PageShell'
import { Button } from '../../design-system/actions/Button'
import { StatusBadge } from '../../design-system/status/StatusBadge'
import { privilegedAuthClient, type PrivilegedAuthClient } from './privilegedAuthApi'
import { totpQrSource } from './totpQr'

function message(error: unknown): string {
  if (!(error instanceof ApiError)) return 'Не удалось настроить TOTP. Повторите попытку.'
  if (error.detail === 'invalid_credentials') return 'Неверный пароль или код из приложения.'
  if (error.detail === 'invalid_totp') return 'Код истёк или введён неверно. Введите новый код.'
  if (error.detail === 'locked') return 'Слишком много попыток. Подождите и попробуйте снова.'
  if (error.detail === 'enrollment_not_pending') return 'Сеанс настройки истёк. Начните настройку заново.'
  if (error.detail === 'already_enrolled') return 'TOTP уже настроен для этого аккаунта.'
  if (error.detail === 'secret_key_required' || error.detail === 'credential_unavailable') return 'Сервер не готов хранить ключ TOTP. Проверьте системную конфигурацию.'
  return 'Не удалось настроить TOTP. Повторите попытку.'
}

export function PrivilegedSecurityPanel({ username, client = privilegedAuthClient }: {
  username: string
  client?: PrivilegedAuthClient
}) {
  const [enrolled, setEnrolled] = useState<boolean | null>(null)
  const [statusFailed, setStatusFailed] = useState(false)
  const [statusRevision, setStatusRevision] = useState(0)
  const [secret, setSecret] = useState<string | null>(null)
  const [password, setPassword] = useState('')
  const [code, setCode] = useState('')
  const [recoveryCodes, setRecoveryCodes] = useState<string[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    let active = true
    setEnrolled(null)
    setStatusFailed(false)
    setError('')
    void client.status()
      .then(value => { if (active) setEnrolled(value.enrolled) })
      .catch(() => { if (active) { setStatusFailed(true); setError('Не удалось проверить состояние TOTP. Повторите проверку.') } })
    return () => { active = false }
  }, [client, statusRevision])

  const downloadCodes = () => {
    if (!recoveryCodes.length) return
    const url = URL.createObjectURL(new Blob([`Robopark — коды восстановления\n\n${recoveryCodes.join('\n')}\n`], { type: 'text/plain;charset=utf-8' }))
    const link = document.createElement('a')
    link.href = url
    link.download = 'robopark-recovery-codes.txt'
    document.body.append(link)
    link.click()
    link.remove()
    window.setTimeout(() => URL.revokeObjectURL(url), 0)
  }

  const begin = async () => {
    setBusy(true); setError('')
    try {
      const value = await client.beginEnrollment()
      setSecret(value.secret)
    } catch (caught) {
      if (caught instanceof ApiError && caught.detail === 'already_enrolled') setEnrolled(true)
      else setError(message(caught))
    } finally { setBusy(false) }
  }

  const confirm = async () => {
    if (!secret || !password || !/^\d{6}$/.test(code)) return
    setBusy(true); setError('')
    try {
      const value = await client.confirmEnrollment({ password, code })
      setRecoveryCodes(value.recovery_codes)
      setPassword(''); setCode(''); setSecret(null); setEnrolled(true)
    } catch (caught) { setError(message(caught)) }
    finally { setBusy(false) }
  }

  return <div className="rp-privileged-security">
    <Panel density="dense" title="Защита системных операций" hint="Для обновлений, очистки и управления хостом нужен пароль и одноразовый код.">
      {error ? <Alert tone="error">{error}</Alert> : null}
      {recoveryCodes.length ? <div className="rp-recovery-codes" role="status">
        <strong>Сохраните коды восстановления</strong>
        <p>Они показываются один раз и заменяют TOTP, если телефон недоступен.</p>
        <ul>{recoveryCodes.map(value => <li key={value}><code>{value}</code></li>)}</ul>
        <div className="rp-recovery-codes__actions"><Button onClick={downloadCodes} type="button">Скачать коды</Button><Button onClick={() => setRecoveryCodes([])} type="button" variant="secondary">Я сохранил коды</Button></div>
      </div> : enrolled === null ? statusFailed ? <Button onClick={() => setStatusRevision(value => value + 1)} type="button" variant="secondary">Повторить проверку TOTP</Button> : <p>Проверяем настройку TOTP…</p>
        : enrolled ? <StatusBadge tone="success">TOTP настроен</StatusBadge>
          : secret ? <div className="rp-totp-enrollment">
            <div className="rp-totp-enrollment__setup">
              <img alt="QR для настройки TOTP" height="208" src={totpQrSource(username, secret)} width="208" />
              <div><strong>Отсканируйте QR в приложении-аутентификаторе</strong><p>Если камера недоступна, введите ключ вручную:</p><code>{secret}</code></div>
            </div>
            <div className="rp-totp-enrollment__confirm">
              <label className="field"><span className="field-label">Текущий пароль</span><input autoComplete="current-password" disabled={busy} onChange={event => setPassword(event.target.value)} type="password" value={password} /></label>
              <label className="field"><span className="field-label">Код из приложения</span><input autoComplete="one-time-code" disabled={busy} inputMode="numeric" maxLength={6} onChange={event => setCode(event.target.value.replace(/\D/g, '').slice(0, 6))} value={code} /></label>
              <Button busy={busy} disabled={!password || !/^\d{6}$/.test(code)} onClick={() => void confirm()} type="button">Подтвердить настройку</Button>
            </div>
          </div>
            : <div className="rp-privileged-security__start"><Alert tone="warning">TOTP ещё не настроен. Системные операции нельзя подтвердить.</Alert><Button busy={busy} onClick={() => void begin()} type="button">Настроить TOTP</Button></div>}
    </Panel>
  </div>
}
