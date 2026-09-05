import { type FormEvent, useEffect, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import {
  api,
  ApiError,
  type IntegrationSettings,
  type Park,
  type ParkRequest,
  type RegistrationPasswordSettings,
  type ScreenshotGuardSettings,
  type TrackerPolicySettings,
} from '../api'
import { Alert, Badge, PageShell, Panel } from '../components/PageShell'
import { EmptyBlock, SkeletonList, Spinner } from '../components/ui/Feedback'
import { TabPanel, Tabs, Toggle } from '../components/ui/Tabs'
import { PasswordField } from '../components/ui/PasswordField'
import { AdminOpsPanel } from '../components/admin/AdminOpsPanel'
import { useAuth } from '../auth-context'
import { mapApiError } from '../i18n/errors'
import { roleLabel, ru } from '../i18n/ru'
import { resourceStore, useCachedResource } from '../lib/resource'
import { useParkScope } from '../app/park/parkScope'
import { SlaPolicyEditor } from '../domains/insights/SlaPolicyEditor'

type TabId = 'integrations' | 'parks' | 'ops'

type AdminBootstrap = {
  parks: Park[]
  parkRequests: ParkRequest[]
  settings: IntegrationSettings | null
  trackerPolicy: TrackerPolicySettings | null
  screenshotGuard: ScreenshotGuardSettings
  screenshotGuardLive?: boolean
  pendingUserCount: number
}

const DEFAULT_SCREENSHOT_GUARD: ScreenshotGuardSettings = {
  operator: false,
  mechanic: false,
  admin: false,
  royal: false,
  driver: false,
}

async function loadScreenshotGuardSettings(): Promise<{
  settings: ScreenshotGuardSettings
  live: boolean
}> {
  try {
    return { settings: await api.screenshotGuardSettings(), live: true }
  } catch {
    return { settings: DEFAULT_SCREENSHOT_GUARD, live: false }
  }
}

async function loadAdminBootstrap(canIntegrations: boolean): Promise<AdminBootstrap> {
  const parks = await api.parks()
  if (!canIntegrations) {
    return {
      parks,
      parkRequests: [],
      settings: null,
      trackerPolicy: null,
      screenshotGuard: DEFAULT_SCREENSHOT_GUARD,
      screenshotGuardLive: false,
      pendingUserCount: 0,
    }
  }
  const [parkRequests, settings, trackerPolicy, screenshotGuardResult, pendingUsers] = await Promise.all([
    api.adminParkRequests(), api.integrationSettings(), api.trackerPolicy(),
    loadScreenshotGuardSettings(), api.adminUsers({ access_status: 'pending' }).catch(() => []),
  ])
  return {
    parks,
    parkRequests,
    settings,
    trackerPolicy,
    screenshotGuard: screenshotGuardResult.settings,
    screenshotGuardLive: screenshotGuardResult.live,
    pendingUserCount: pendingUsers.length,
  }
}

function worksBadge(ok: boolean) {
  return ok ? (
    <span className="badge badge-ok">работает</span>
  ) : (
    <span className="badge badge-warn">нет</span>
  )
}

function emergencyCookieStatus(settings: IntegrationSettings) {
  switch (settings.emergency_cookie_status) {
    case 'valid':
      return <span className="badge badge-ok">Действительна</span>
    case 'invalid':
      return <span className="badge badge-warn">Недействительна</span>
    case 'unavailable':
      return <span className="badge badge-warn">Недоступна</span>
    default:
      return <span className="badge badge-muted">Не проверена</span>
  }
}

function mergeTrackerSettings(
  current: IntegrationSettings | null,
  updated: IntegrationSettings,
): IntegrationSettings {
  if (!current) return updated
  return {
    ...current,
    tracker_token_masked: updated.tracker_token_masked,
    tracker_token_updated_at: updated.tracker_token_updated_at,
    tracker_token_encrypted: updated.tracker_token_encrypted,
  }
}

function mergeEmergencySettings(
  current: IntegrationSettings | null,
  updated: IntegrationSettings,
): IntegrationSettings {
  if (!current) return updated
  return {
    ...current,
    emergency_cookie_masked: updated.emergency_cookie_masked,
    emergency_cookie_updated_at: updated.emergency_cookie_updated_at,
    emergency_cookie_encrypted: updated.emergency_cookie_encrypted,
    emergency_cookie_valid: updated.emergency_cookie_valid,
    emergency_cookie_status: updated.emergency_cookie_status,
    emergency_cookie_checked_at: updated.emergency_cookie_checked_at,
    emergency_cookie_checked_robot: updated.emergency_cookie_checked_robot,
  }
}

export function Admin() {
  const { user } = useAuth()
  const { parkId } = useParkScope()
  const context = JSON.stringify([
    user?.id, user?.username, user?.role, user?.tracker_login,
    user?.permissions, user?.parks, parkId,
  ])
  return <AdminWorkspace key={context} bootstrapKey={`admin:bootstrap:${context}`} />
}

function AdminWorkspace({ bootstrapKey }: { bootstrapKey: string }) {
  const { user } = useAuth()
  const { parkId } = useParkScope()
  const [searchParams, setSearchParams] = useSearchParams()
  const perms = user?.permissions ?? []
  const canParks = perms.includes('parks.manage')
  const canIntegrations = perms.includes('nav.admin')
  const canOps = user?.role === 'royal'
  const requestedTab = searchParams.get('tab')
  const firstPermittedTab: TabId = canIntegrations ? 'integrations' : canParks ? 'parks' : 'ops'
  const isPermittedTab = (candidate: string | null): candidate is TabId => (
    (candidate === 'integrations' && canIntegrations)
    || (candidate === 'parks' && canParks)
    || (candidate === 'ops' && canOps)
  )
  const initialTab: TabId = isPermittedTab(requestedTab) ? requestedTab : firstPermittedTab
  const [tab, setTab] = useState<TabId>(initialTab)
  const tabPermitted = (tab === 'integrations' && canIntegrations)
    || (tab === 'parks' && canParks)
    || (tab === 'ops' && canOps)
  const bootRes = useCachedResource<AdminBootstrap>(bootstrapKey, () => loadAdminBootstrap(canIntegrations), {
    persist: false,
  })
  const boot = bootRes.data
  const settings = boot?.settings ?? null
  const active = useRef(true)
  useEffect(() => {
    active.current = true
    return () => { active.current = false }
  }, [])

  const [parks, setParks] = useState<Park[]>(boot?.parks ?? [])
  const [parkRequests, setParkRequests] = useState<ParkRequest[]>(boot?.parkRequests ?? [])
  const [trackerPolicy, setTrackerPolicy] = useState<TrackerPolicySettings | null>(
    boot?.trackerPolicy ?? null,
  )
  const [screenshotGuard, setScreenshotGuard] = useState<ScreenshotGuardSettings>(
    boot?.screenshotGuard ?? DEFAULT_SCREENSHOT_GUARD,
  )
  const [screenshotGuardLive, setScreenshotGuardLive] = useState(true)
  const [name, setName] = useState('')
  const [tag, setTag] = useState('')
  const [trackerToken, setTrackerToken] = useState('')
  const [emergencyCookie, setEmergencyCookie] = useState('')
  const [emergencyRobot, setEmergencyRobot] = useState('')
  const [registrationPassword, setRegistrationPassword] = useState('')
  const [registrationSettings, setRegistrationSettings] =
    useState<RegistrationPasswordSettings | null>(null)
  const [error, setError] = useState('')
  const [success, setSuccess] = useState('')
  const [busy, setBusy] = useState(false)
  const [trackerBusy, setTrackerBusy] = useState(false)
  const [emergencyBusy, setEmergencyBusy] = useState(false)

  useEffect(() => {
    if (!tabPermitted) setTab(firstPermittedTab)
  }, [tabPermitted, firstPermittedTab])

  useEffect(() => {
    const next = new URLSearchParams(searchParams)
    if (tab === 'integrations') next.delete('tab')
    else next.set('tab', tab)
    if (next.toString() !== searchParams.toString()) setSearchParams(next, { replace: true })
  }, [searchParams, setSearchParams, tab])

  // Integration-only cache updates preserve the other forms' unsaved edits.
  useEffect(() => {
    if (boot?.parks) setParks(boot.parks)
  }, [boot?.parks])
  useEffect(() => {
    if (boot?.parkRequests) setParkRequests(boot.parkRequests)
  }, [boot?.parkRequests])
  useEffect(() => {
    setTrackerPolicy(boot?.trackerPolicy ?? null)
  }, [boot?.trackerPolicy])
  useEffect(() => {
    if (boot?.screenshotGuard) setScreenshotGuard(boot.screenshotGuard)
  }, [boot?.screenshotGuard])
  useEffect(() => {
    setScreenshotGuardLive(boot?.screenshotGuardLive ?? true)
  }, [boot?.screenshotGuardLive])

  useEffect(() => {
    if (user?.role !== 'royal') {
      setRegistrationSettings(null)
      return
    }
    void api
      .registrationPasswordSettings()
      .then(setRegistrationSettings)
      .catch(() => setRegistrationSettings(null))
  }, [user?.role, success])

  const run = async (action: () => Promise<unknown>, message = 'Сохранено') => {
    setError('')
    setSuccess('')
    setBusy(true)
    try {
      await action()
      if (!active.current) return
      await bootRes.refresh()
      if (!active.current) return
      setSuccess(message)
    } catch {
      setError(ru.errors.generic)
    } finally {
      setBusy(false)
    }
  }

  const createPark = async (event: FormEvent) => {
    event.preventDefault()
    await run(async () => {
      await api.createPark({ name, tag })
      setName('')
      setTag('')
    }, 'Парк создан')
  }

  const applyIntegrationSettings = (
    updated: IntegrationSettings,
    merge: typeof mergeTrackerSettings,
  ) => {
    if (!active.current) return
    const current = resourceStore.get<AdminBootstrap>(bootstrapKey)
    if (!current) return
    // A mutation is authoritative for its integration. Retire older bootstrap
    // loads before publishing to the same cache used by this page and remounts.
    resourceStore.invalidate(bootstrapKey)
    resourceStore.set(bootstrapKey, {
      ...current, settings: merge(current.settings, updated),
    }, false)
  }

  const saveTrackerToken = async (event: FormEvent) => {
    event.preventDefault()
    const token = trackerToken.trim()
    if (!token || trackerBusy) return
    setError('')
    setSuccess('')
    setTrackerBusy(true)
    try {
      const updated = await api.setTrackerToken(token)
      if (!active.current) return
      applyIntegrationSettings(updated, mergeTrackerSettings)
      setTrackerToken('')
      setSuccess('Токен Tracker сохранён')
    } catch (caught) {
      setError(mapApiError(caught, ru.errors.generic))
    } finally {
      setTrackerBusy(false)
    }
  }

  const runEmergencyCheck = async (action: () => Promise<IntegrationSettings>, message: string) => {
    if (emergencyBusy) return
    setError('')
    setSuccess('')
    setEmergencyBusy(true)
    try {
      const updated = await action()
      if (!active.current) return
      applyIntegrationSettings(updated, mergeEmergencySettings)
      setSuccess(message)
    } catch (caught) {
      if (!active.current) return
      if (caught instanceof ApiError && (caught.status === 401 || caught.status === 503)) {
        try {
          applyIntegrationSettings(await api.integrationSettings(), mergeEmergencySettings)
        } catch {
          // Keep the original actionable error if the protected state refresh fails.
        }
      }
      if (!active.current) return
      setError(mapApiError(caught, ru.errors.emergency503))
    } finally {
      setEmergencyBusy(false)
    }
  }

  const saveEmergencyCookie = async (event: FormEvent) => {
    event.preventDefault()
    const cookie = emergencyCookie.trim()
    const robot = emergencyRobot.trim()
    if (!cookie || !robot) return
    await runEmergencyCheck(async () => {
      const updated = await api.setEmergencyCookie(cookie, robot)
      setEmergencyCookie('')
      return updated
    }, 'Cookie сохранена и проверена')
  }

  const checkEmergencyCookie = async () => {
    const robot = emergencyRobot.trim()
    await runEmergencyCheck(
      () => api.checkEmergencyCookie(robot || undefined),
      'Текущая cookie проверена',
    )
  }

  const saveRegistrationPassword = async (event: FormEvent) => {
    event.preventDefault()
    const next = registrationPassword.trim()
    if (!next) return
    await run(async () => {
      const updated = await api.setRegistrationPassword(next)
      setRegistrationSettings(updated)
      setRegistrationPassword('')
    }, 'Общий пароль регистрации сохранён')
  }

  const disableRegistration = async () => {
    await run(async () => {
      const updated = await api.clearRegistrationPassword()
      setRegistrationSettings(updated)
      setRegistrationPassword('')
    }, 'Регистрация закрыта')
  }

  const editPark = (parkId: number, changes: Partial<Park>) => {
    setParks((current) =>
      current.map((park) => (park.id === parkId ? { ...park, ...changes } : park)),
    )
  }

  const parseOptionalInt = (value: string) => {
    const trimmed = value.trim()
    if (!trimmed) return null
    const parsed = Number(trimmed)
    return Number.isFinite(parsed) ? parsed : null
  }

  const parkName = (parkId: number) =>
    parks.find((park) => park.id === parkId)?.name ?? `#${parkId}`

  const displayError = error || (bootRes.error ? mapApiError(bootRes.error, ru.errors.load) : '')
  const showColdSkeleton = bootRes.isLoading && !boot

  if (showColdSkeleton) {
    return (
      <PageShell subtitle="Загрузка данных…" title="Администрирование">
        <SkeletonList rows={4} />
      </PageShell>
    )
  }

  return (
    <PageShell
      actions={busy || trackerBusy || emergencyBusy ? <Spinner label="Сохранение" /> : undefined}
      subtitle="Парки, роли, пользователи, интеграции и снимок системы."
      title="Администрирование"
    >
      {displayError && <Alert tone="error">{displayError}</Alert>}
      {success && <Alert tone="success">{success}</Alert>}

      <Tabs
        items={[
          ...(canIntegrations ? [{ id: 'integrations', label: 'Интеграции' }] : []),
          ...(canParks ? [{ id: 'parks', label: 'Парки', count: parkRequests.length }] : []),
          ...(user?.role === 'royal' ? [{ id: 'ops', label: ru.ops.tab }] : []),
        ]}
        onChange={(id) => setTab(id as TabId)}
        value={tab}
      />

      {/* --- Integrations ------------------------------------------------- */}
      {canIntegrations && <TabPanel active={tab === 'integrations'}>
        {user?.role === 'royal' && (
          <Panel
            hint="Если пароль не задан, регистрация на /register закрыта."
            title="Общий пароль регистрации"
          >
            {registrationSettings && (
              <div className="stat-grid">
                <div className="stat">
                  <span className="stat-label">Статус</span>
                  <span className="stat-value">
                    {registrationSettings.configured ? (
                      <span className="badge badge-ok">открыта</span>
                    ) : (
                      <span className="badge badge-warn">закрыта</span>
                    )}
                  </span>
                </div>
                <div className="stat">
                  <span className="stat-label">Текущий пароль</span>
                  <span className="stat-value">
                    {registrationSettings.password_masked ?? 'не задан'}
                  </span>
                </div>
              </div>
            )}

            <form className="form-grid" onSubmit={saveRegistrationPassword}>
              <PasswordField
                autoComplete="new-password"
                hint="Минимум как у личного пароля: 12 символов и три класса знаков."
                label="Новый общий пароль"
                onChange={setRegistrationPassword}
                placeholder="Задайте или смените пароль для /register"
                value={registrationPassword}
              />
              <div className="form-actions">
                <button
                  className="btn"
                  disabled={busy || !registrationPassword.trim()}
                  type="submit"
                >
                  Сохранить пароль
                </button>
                {registrationSettings?.configured && (
                  <button
                    className="btn btn-secondary"
                    disabled={busy}
                    onClick={() => void disableRegistration()}
                    type="button"
                  >
                    Закрыть регистрацию
                  </button>
                )}
              </div>
            </form>
          </Panel>
        )}

        <Panel
          hint="Секреты хранятся зашифрованными. Здесь только статус — без маски значения."
          title="Секреты"
        >
          {settings && (
            <div className="stat-grid">
              <div className="stat">
                <span className="stat-label">Tracker OAuth</span>
                <span className="stat-value">
                  {worksBadge(Boolean(settings.tracker_token_masked))}
                </span>
              </div>
              <div className="stat">
                <span className="stat-label">Cookie диагностики робота</span>
                <span className="stat-value">
                  {emergencyCookieStatus(settings)}
                </span>
              </div>
            </div>
          )}

          {settings?.emergency_cookie_checked_robot && (
            <p className="panel-hint">
              Проверено: робот {settings.emergency_cookie_checked_robot}
              {settings.emergency_cookie_checked_at ? ` · ${new Date(settings.emergency_cookie_checked_at).toLocaleString('ru-RU')}` : ''}
            </p>
          )}
          {settings?.emergency_cookie_status === 'unavailable' && (
            <Alert tone="warning">Проверка сейчас недоступна. Повторите попытку.</Alert>
          )}

          <form className="form-grid" onSubmit={saveTrackerToken}>
            <label className="field">
              <span className="field-label">Tracker OAuth-токен</span>
              <input
                disabled={trackerBusy}
                onChange={(event) => setTrackerToken(event.target.value)}
                placeholder="Оставьте пустым, чтобы не менять"
                type="password"
                value={trackerToken}
              />
            </label>
            <div className="form-actions">
              <button
                className="btn"
                disabled={trackerBusy || !trackerToken.trim()}
                type="submit"
              >
                {trackerBusy ? 'Сохранение…' : 'Сохранить токен'}
              </button>
            </div>
          </form>

          <form className="form-grid" onSubmit={saveEmergencyCookie}>
            <label className="field">
              <span className="field-label">Cookie диагностики робота</span>
              <input
                disabled={emergencyBusy}
                onChange={(event) => setEmergencyCookie(event.target.value)}
                placeholder="Вставьте новую cookie для проверки"
                type="password"
                value={emergencyCookie}
              />
            </label>
            <label className="field">
              <span className="field-label">Робот для проверки</span>
              <input
                disabled={emergencyBusy}
                onChange={(event) => setEmergencyRobot(event.target.value)}
                placeholder="Например, 447 или VIN"
                value={emergencyRobot}
              />
            </label>
            <div className="form-actions">
              <button
                className="btn"
                disabled={emergencyBusy || !emergencyCookie.trim() || !emergencyRobot.trim()}
                type="submit"
              >
                {emergencyBusy ? 'Проверяем…' : 'Сохранить и проверить'}
              </button>
              <button
                className="btn btn-secondary"
                disabled={emergencyBusy}
                onClick={() => void checkEmergencyCookie()}
                type="button"
              >
                Проверить текущую
              </button>
            </div>
          </form>
        </Panel>

        {trackerPolicy && (
          <Panel hint="Влияет на то, что видят операторы и механики." title="Политика Tracker">
            <div className="toggle-list">
              <Toggle
                checked={trackerPolicy.operator_show_untagged}
                disabled={busy}
                label="Оператор видит неразмеченные тикеты"
                onChange={(next) =>
                  run(
                    () => api.updateTrackerPolicy({ operator_show_untagged: next }),
                    'Политика обновлена',
                  )
                }
              />
              <Toggle
                checked={trackerPolicy.mechanic_can_write}
                disabled={busy}
                label="Механик может писать в Tracker"
                onChange={(next) =>
                  run(
                    () => api.updateTrackerPolicy({ mechanic_can_write: next }),
                    'Политика обновлена',
                  )
                }
              />
            </div>
          </Panel>
        )}

        <Panel hint={ru.screenshotGuard.adminHint} title="Защита от скриншотов">
          {!screenshotGuardLive && (
            <Alert tone="warning">
              API не отвечает на /admin/settings/screenshot-guard — перезапустите backend
              (uvicorn). Переключатели не сохранятся, пока сервер не обновлён.
            </Alert>
          )}
          <div className="toggle-list">
            {(['operator', 'mechanic', 'driver', 'admin', 'royal'] as const).map((role) => (
              <Toggle
                key={role}
                checked={Boolean(screenshotGuard[role])}
                disabled={busy || !screenshotGuardLive}
                label={ru.screenshotGuard.adminToggle(roleLabel(role))}
                onChange={(next) =>
                  run(async () => {
                    const updated = await api.updateScreenshotGuardSettings({ [role]: next })
                    setScreenshotGuard(updated)
                    setScreenshotGuardLive(true)
                  }, 'Защита обновлена')
                }
              />
            ))}
          </div>
        </Panel>

        <Panel title="Быстрые переходы">
          <div className="link-row">
            <Link className="btn btn-secondary" to="/admin/tracker">
              Рабочий стол Tracker
            </Link>
            <Link className="btn btn-secondary" to="/emergency">
              Проверка робота
            </Link>
            <Link className="btn btn-secondary" to="/admin/emergency/config">
              Настройки проверки робота
            </Link>
          </div>
        </Panel>
      </TabPanel>}

      {canParks && (
      <TabPanel active={tab === 'parks'}>
        {user && parkId != null && <SlaPolicyEditor parkId={parkId} user={user} />}
        {parkRequests.length > 0 && (
        <Panel
          hint="Операторы запрашивают дополнительные парки из своего кабинета."
          title={`Заявки на парки (${parkRequests.length})`}
        >
          <ul className="card-list">
            {parkRequests.map((request) => (
              <li className="card action-row" key={request.id}>
                <div>
                  <div className="card-title">{request.username ?? `Пользователь #${request.user_id}`}</div>
                  <div className="card-meta">Парк: {parkName(request.park_id)}</div>
                </div>
                <div className="form-actions">
                  <button
                    className="btn"
                    disabled={busy}
                    onClick={() =>
                      run(
                        () => api.resolveParkRequest(request.id, 'approve'),
                        'Заявка одобрена',
                      )
                    }
                    type="button"
                  >
                    {ru.approve}
                  </button>
                  <button
                    className="btn btn-secondary"
                    disabled={busy}
                    onClick={() =>
                      run(
                        () => api.resolveParkRequest(request.id, 'reject'),
                        'Заявка отклонена',
                      )
                    }
                    type="button"
                  >
                    {ru.reject}
                  </button>
                </div>
              </li>
            ))}
          </ul>
        </Panel>
        )}

        <Panel hint="Тег используется в Tracker; очередь нужна для задач и поиска." title="Новый парк">
          <form className="form-grid" onSubmit={createPark}>
            <label className="field">
              <span className="field-label">Название</span>
              <input
                onChange={(event) => setName(event.target.value)}
                required
                value={name}
              />
            </label>
            <label className="field">
              <span className="field-label">Тег</span>
              <input
                onChange={(event) => setTag(event.target.value)}
                required
                value={tag}
              />
            </label>
            <div className="form-actions">
              <button className="btn" disabled={busy} type="submit">
                {ru.create}
              </button>
            </div>
          </form>
        </Panel>

        {parks.length === 0 ? (
          <EmptyBlock
            hint="Парк нужен, чтобы назначать операторов и механиков."
            icon="🏭"
            title="Парков пока нет"
          />
        ) : (
          parks.map((park) => (
            <Panel
              actions={<Badge active={park.is_active ?? true} />}
              key={park.id}
              title={park.name || `Парк #${park.id}`}
            >
              <div className="form-grid">
                <label className="field">
                  <span className="field-label">Название</span>
                  <input
                    onChange={(event) => editPark(park.id, { name: event.target.value })}
                    required
                    value={park.name}
                  />
                </label>
                <label className="field">
                  <span className="field-label">Тег</span>
                  <input
                    onChange={(event) => editPark(park.id, { tag: event.target.value })}
                    required
                    value={park.tag}
                  />
                </label>
                <label className="field">
                  <span className="field-label">Очередь Tracker</span>
                  <input
                    onChange={(event) =>
                      editPark(park.id, { tracker_queue: event.target.value })
                    }
                    placeholder="SDCFLEETOPS"
                    value={park.tracker_queue ?? ''}
                  />
                </label>
                <label className="field">
                  <span className="field-label">Приоритет</span>
                  <input
                    onChange={(event) =>
                      editPark(park.id, { tracker_priority: event.target.value || null })
                    }
                    placeholder="blocker"
                    value={park.tracker_priority ?? ''}
                  />
                </label>
                <label className="field">
                  <span className="field-label">Тип</span>
                  <input
                    onChange={(event) =>
                      editPark(park.id, { tracker_type: event.target.value || null })
                    }
                    placeholder="пусто = без фильтра"
                    value={park.tracker_type ?? ''}
                  />
                </label>
                <label className="field">
                  <span className="field-label">Group ID (Telegram)</span>
                  <input
                    onChange={(event) =>
                      editPark(park.id, { group_id: parseOptionalInt(event.target.value) })
                    }
                    value={park.group_id ?? ''}
                  />
                </label>
                <label className="field">
                  <span className="field-label">Chat ID</span>
                  <input
                    onChange={(event) =>
                      editPark(park.id, { chat_id: parseOptionalInt(event.target.value) })
                    }
                    value={park.chat_id ?? ''}
                  />
                </label>
              </div>

              <div className="toggle-list">
                <Toggle
                  checked={park.feature_blockers ?? true}
                  label="Задачи"
                  onChange={(next) => editPark(park.id, { feature_blockers: next })}
                />
                <Toggle
                  checked={park.feature_reports ?? true}
                  label="Отчёты"
                  onChange={(next) => editPark(park.id, { feature_reports: next })}
                />
                <Toggle
                  checked={park.feature_sla_repair ?? true}
                  label="SLA ремонт"
                  onChange={(next) => editPark(park.id, { feature_sla_repair: next })}
                />
                <Toggle
                  checked={park.feature_backlog_alerts ?? true}
                  label="Backlog alerts"
                  onChange={(next) => editPark(park.id, { feature_backlog_alerts: next })}
                />
              </div>

              <div className="form-actions">
                <button
                  className="btn"
                  disabled={!park.name || !park.tag || busy}
                  onClick={() =>
                    run(() =>
                      api.updatePark(park.id, {
                        name: park.name,
                        tag: park.tag,
                        tracker_queue: park.tracker_queue || null,
                        tracker_priority: park.tracker_priority || null,
                        tracker_type: park.tracker_type || null,
                        group_id: park.group_id ?? null,
                        chat_id: park.chat_id ?? null,
                        feature_blockers: park.feature_blockers,
                        feature_reports: park.feature_reports,
                        feature_sla_repair: park.feature_sla_repair,
                        feature_backlog_alerts: park.feature_backlog_alerts,
                      }),
                    )
                  }
                  type="button"
                >
                  {ru.save}
                </button>
                <button
                  className="btn btn-secondary"
                  disabled={busy}
                  onClick={() =>
                    run(() => api.updatePark(park.id, { is_active: !park.is_active }))
                  }
                  type="button"
                >
                  {park.is_active ? ru.deactivate : ru.activate}
                </button>
              </div>
            </Panel>
          ))
        )}
      </TabPanel>
      )}

      {user?.role === 'royal' && (
        <TabPanel active={tab === 'ops'}>
          <Panel hint={ru.ops.hint} title={ru.ops.title}>
            <AdminOpsPanel />
          </Panel>
        </TabPanel>
      )}
    </PageShell>
  )
}
