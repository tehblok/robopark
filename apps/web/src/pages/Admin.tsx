import { type FormEvent, type ReactNode, useEffect, useRef, useState } from 'react'
import { Link, useLocation, useSearchParams } from 'react-router-dom'
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
import { Toggle } from '../components/ui/Tabs'
import { TabPanel as DesignTabPanel, Tabs } from '../design-system/navigation/Tabs'
import { PasswordField } from '../components/ui/PasswordField'
import { AdminOpsPanel } from '../components/admin/AdminOpsPanel'
import { HostHealthPanel } from '../components/admin/HostHealthPanel'
import { useAuth } from '../auth-context'
import { mapApiError } from '../i18n/errors'
import { roleLabel, ru } from '../i18n/ru'
import { resourceStore, useCachedResource } from '../lib/resource'
import { useParkScope } from '../app/park/parkScope'
import { ManagementNavigation } from '../domains/management/ManagementNavigation'
import { DomainPresentation } from '../app/interface/DomainPresentation'
import { MetricCard } from '../design-system/data/MetricCard'
import { StatusBadge } from '../design-system/status/StatusBadge'
import { TelegramRuntimePanel } from '../domains/telegram/TelegramRuntimePanel'
import { NativeTelegramPanel } from '../domains/telegram/NativeTelegramPanel'
import { BotAuxiliaryQueuesPanel } from '../domains/telegram/BotAuxiliaryQueuesPanel'
import { BotSettingsPanel } from '../domains/system/BotSettingsPanel'

type TabId = 'integrations' | 'telegram' | 'parks' | 'ops' | 'health'

function validParkTimezone(value: string): boolean {
  if (!value || value.trim() !== value || value.length > 64) return false
  try {
    new Intl.DateTimeFormat('en', { timeZone: value })
    return true
  } catch (error) {
    if (error instanceof RangeError) return false
    throw error
  }
}

// Keep inactive settings unmounted so host polling starts only in its own tab.
function TabPanel({ id, active, children }: { id: TabId; active: boolean; children: ReactNode }) {
  if (!active) return null
  if (id === 'parks') {
    return <div className="rp-tab-panel" id="admin-panel-parks">{children}</div>
  }
  return <DesignTabPanel id={`admin-panel-${id}`} labelledBy={`tab-${id}`} active>{children}</DesignTabPanel>
}

type AdminBootstrap = {
  parks: Park[]
  parkRequests: ParkRequest[]
  settings: IntegrationSettings | null
  trackerPolicy: TrackerPolicySettings | null
  screenshotGuard: ScreenshotGuardSettings | null
  screenshotGuardLive?: boolean
  failedSections?: AdminSection[]
}

type AdminSection = 'parkRequests' | 'integrations' | 'trackerPolicy'
const adminSectionLabels: Record<AdminSection, string> = {
  parkRequests: 'Заявки на парки',
  integrations: 'Интеграции',
  trackerPolicy: 'Политика Tracker',
}

async function loadScreenshotGuardSettings(): Promise<{
  settings: ScreenshotGuardSettings | null
  live: boolean
}> {
  try {
    return { settings: await api.screenshotGuardSettings(), live: true }
  } catch {
    return { settings: null, live: false }
  }
}

async function loadAdminBootstrap(
  tab: TabId,
  previous?: Partial<AdminBootstrap>,
): Promise<AdminBootstrap> {
  const base: AdminBootstrap = {
    parks: previous?.parks ?? [],
    parkRequests: previous?.parkRequests ?? [],
    settings: previous?.settings ?? null,
    trackerPolicy: previous?.trackerPolicy ?? null,
    screenshotGuard: previous?.screenshotGuard ?? null,
    screenshotGuardLive: previous?.screenshotGuardLive ?? false,
    failedSections: [],
  }
  if (tab === 'health' || tab === 'ops' || tab === 'telegram') return base
  if (tab === 'parks') {
    const [parksResult, parkRequestsResult] = await Promise.allSettled([
      api.parks(), api.adminParkRequests(),
    ])
    if (parksResult.status === 'rejected') throw parksResult.reason
    if (parkRequestsResult.status === 'rejected' && parkRequestsResult.reason instanceof ApiError
      && (parkRequestsResult.reason.status === 401 || parkRequestsResult.reason.status === 403)) {
      throw parkRequestsResult.reason
    }
    return {
      ...base,
      parks: parksResult.value,
      parkRequests: parkRequestsResult.status === 'fulfilled' ? parkRequestsResult.value : base.parkRequests,
      failedSections: parkRequestsResult.status === 'rejected' ? ['parkRequests'] : [],
    }
  }
  const [settingsResult, trackerPolicyResult, screenshotGuardResult] = await Promise.allSettled([
    api.integrationSettings(), api.trackerPolicy(), loadScreenshotGuardSettings(),
  ])
  const results = [settingsResult, trackerPolicyResult]
  for (const result of results) {
    if (result.status === 'rejected' && result.reason instanceof ApiError
      && (result.reason.status === 401 || result.reason.status === 403)) {
      throw result.reason
    }
  }
  const failedSections: AdminSection[] = []
  if (settingsResult.status === 'rejected') failedSections.push('integrations')
  if (trackerPolicyResult.status === 'rejected') failedSections.push('trackerPolicy')
  return {
    ...base,
    settings: settingsResult.status === 'fulfilled' ? settingsResult.value : base.settings,
    trackerPolicy: trackerPolicyResult.status === 'fulfilled' ? trackerPolicyResult.value : base.trackerPolicy,
    screenshotGuard: screenshotGuardResult.status === 'fulfilled' ? screenshotGuardResult.value.settings : base.screenshotGuard,
    screenshotGuardLive: screenshotGuardResult.status === 'fulfilled' && screenshotGuardResult.value.live,
    failedSections,
  }
}

function worksBadge(ok: boolean) {
  return ok ? (
    <StatusBadge tone="success">работает</StatusBadge>
  ) : (
    <StatusBadge tone="warning">нет</StatusBadge>
  )
}

function emergencyCookieStatus(settings: IntegrationSettings) {
  switch (settings.emergency_cookie_status) {
    case 'valid':
      return <StatusBadge tone="success">Действительна</StatusBadge>
    case 'invalid':
      return <StatusBadge tone="critical">Недействительна</StatusBadge>
    case 'unavailable':
      return <StatusBadge tone="warning">Недоступна</StatusBadge>
    default:
      return <StatusBadge tone="neutral">Не проверена</StatusBadge>
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
  const [searchParams] = useSearchParams()
  const requestedPark = Number(searchParams.get('park'))
  const contextParkId = parkId ?? (Number.isInteger(requestedPark) && requestedPark > 0 ? requestedPark : null)
  const context = JSON.stringify([
    user?.id, user?.username, user?.role, user?.tracker_login,
    user?.permissions, user?.parks, contextParkId,
  ])
  return <div className="rp-management"><DomainPresentation route="admin-settings"><AdminWorkspace key={context} bootstrapKey={`admin:bootstrap:${context}`} /></DomainPresentation></div>
}

function AdminWorkspace({ bootstrapKey }: { bootstrapKey: string }) {
  const { user } = useAuth()
  const { hash } = useLocation()
  const { parkId, refreshParks } = useParkScope()
  const [searchParams, setSearchParams] = useSearchParams()
  const perms = user?.permissions ?? []
  const canParks = perms.includes('parks.manage')
  const canIntegrations = perms.includes('nav.admin')
  const canTelegram = user?.role === 'admin' || user?.role === 'royal'
  const canOps = user?.role === 'royal'
  const requestedTab = searchParams.get('tab')
  const firstPermittedTab: TabId = canIntegrations ? 'integrations' : canTelegram ? 'telegram' : canParks ? 'parks' : 'ops'
  const isPermittedTab = (candidate: string | null): candidate is TabId => (
    (candidate === 'integrations' && canIntegrations)
    || (candidate === 'telegram' && canTelegram)
    || (candidate === 'health' && canIntegrations)
    || (candidate === 'parks' && canParks)
    || (candidate === 'ops' && canOps)
  )
  const tab: TabId = isPermittedTab(requestedTab) ? requestedTab : firstPermittedTab
  const setTab = (nextTab: TabId) => {
    setSearchParams((current) => {
      const next = new URLSearchParams(current)
      if (nextTab === 'integrations') next.delete('tab')
      else next.set('tab', nextTab)
      return next
    }, { replace: true })
  }
  const bootRes = useCachedResource<Partial<AdminBootstrap>>(
    bootstrapKey,
    () => loadAdminBootstrap(tab, resourceStore.get<Partial<AdminBootstrap>>(bootstrapKey)),
    {
      persist: false,
      // Background snapshots must not replace unsaved settings drafts.
      refreshIntervalMs: 0,
    },
  )
  const boot = bootRes.data
  const refreshBootstrap = bootRes.refresh
  const settings = boot?.settings ?? null
  const active = useRef(true)
  const previousTab = useRef(tab)
  const pendingParkEdits = useRef<Map<number, Partial<Park>>>(new Map())
  useEffect(() => {
    if (previousTab.current === tab) return
    previousTab.current = tab
    void refreshBootstrap()
  }, [tab, refreshBootstrap])
  useEffect(() => {
    active.current = true
    return () => { active.current = false }
  }, [])

  const [parks, setParks] = useState<Park[]>(boot?.parks ?? [])
  const [parkRequests, setParkRequests] = useState<ParkRequest[]>(boot?.parkRequests ?? [])
  const [trackerPolicy, setTrackerPolicy] = useState<TrackerPolicySettings | null>(
    boot?.trackerPolicy ?? null,
  )
  const [screenshotGuard, setScreenshotGuard] = useState<ScreenshotGuardSettings | null>(
    boot?.screenshotGuard ?? null,
  )
  const [screenshotGuardLive, setScreenshotGuardLive] = useState(boot?.screenshotGuardLive === true)
  const [name, setName] = useState('')
  const [tag, setTag] = useState('')
  const [timezone, setTimezone] = useState('Europe/Moscow')
  const [newTrackerQueue, setNewTrackerQueue] = useState('')
  const [trackerToken, setTrackerToken] = useState('')
  const [emergencyCookie, setEmergencyCookie] = useState('')
  const [emergencyRobot, setEmergencyRobot] = useState('')
  const [parkSearch, setParkSearch] = useState('')
  const [editingParkId, setEditingParkId] = useState<number | null>(null)
  const [createParkOpen, setCreateParkOpen] = useState(false)
  const [registrationPassword, setRegistrationPassword] = useState('')
  const [registrationSettings, setRegistrationSettings] =
    useState<RegistrationPasswordSettings | null>(null)
  const [error, setError] = useState('')
  const [success, setSuccess] = useState('')
  const [busy, setBusy] = useState(false)
  const [trackerBusy, setTrackerBusy] = useState(false)
  const [emergencyBusy, setEmergencyBusy] = useState(false)

  useEffect(() => {
    if (hash !== '#tracker-token' || tab !== 'integrations' || bootRes.isLoading) return
    document.getElementById('tracker-token')?.scrollIntoView?.({ block: 'center' })
  }, [boot, bootRes.isLoading, hash, registrationSettings, tab])

  useEffect(() => {
    if (requestedTab === tab || (tab === 'integrations' && requestedTab === null)) return
    setSearchParams((current) => {
      const next = new URLSearchParams(current)
      if (tab === 'integrations') next.delete('tab')
      else next.set('tab', tab)
      return next
    }, { replace: true })
  }, [requestedTab, setSearchParams, tab])

  // Integration-only cache updates preserve the other forms' unsaved edits.
  useEffect(() => {
    if (boot?.parks) {
      setParks(boot.parks.map((park) => ({ ...park, ...pendingParkEdits.current.get(park.id) })))
    }
  }, [boot?.parks])
  useEffect(() => {
    if (boot?.parkRequests) setParkRequests(boot.parkRequests)
  }, [boot?.parkRequests])
  useEffect(() => {
    setTrackerPolicy(boot?.trackerPolicy ?? null)
  }, [boot?.trackerPolicy])
  useEffect(() => {
    setScreenshotGuard(boot?.screenshotGuard ?? null)
  }, [boot?.screenshotGuard])
  useEffect(() => {
    setScreenshotGuardLive(boot?.screenshotGuardLive === true)
  }, [boot?.screenshotGuardLive])

  useEffect(() => {
    if (user?.role !== 'royal') {
      setRegistrationSettings(null)
      return
    }
    if (tab !== 'integrations') return
    void api
      .registrationPasswordSettings()
      .then(setRegistrationSettings)
      .catch(() => setRegistrationSettings(null))
  }, [user?.role, tab, success])

  const run = async (action: () => Promise<unknown>, message = '') => {
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

  const runParkMutation = (action: () => Promise<unknown>, message = '') => run(async () => {
    await action()
    await refreshParks().catch(() => undefined)
  }, message)

  const createPark = async (event: FormEvent) => {
    event.preventDefault()
    if (!validParkTimezone(timezone)) return
    await runParkMutation(async () => {
      await api.createPark({
        name, tag, timezone,
        tracker_queue: newTrackerQueue.trim() || null,
      })
      setName('')
      setTag('')
      setNewTrackerQueue('')
      setCreateParkOpen(false)
    }, 'Парк создан')
  }

  const applyIntegrationSettings = (
    updated: IntegrationSettings,
    merge: typeof mergeTrackerSettings,
  ) => {
    if (!active.current) return
    const current = resourceStore.get<Partial<AdminBootstrap>>(bootstrapKey)
    // A mutation is authoritative for its integration. Retire older bootstrap
    // loads before publishing to the same cache used by this page and remounts.
    resourceStore.invalidate(bootstrapKey)
    resourceStore.set(bootstrapKey, {
      ...current, settings: merge(current?.settings ?? null, updated),
      failedSections: current?.failedSections?.filter(section => section !== 'integrations'),
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
    if (!cookie) return
    await runEmergencyCheck(async () => {
      const updated = await api.setEmergencyCookie(cookie)
      setEmergencyCookie('')
      return updated
    }, 'Cookie сохранена')
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
    pendingParkEdits.current.set(parkId, { ...pendingParkEdits.current.get(parkId), ...changes })
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
      <ManagementNavigation />
      {displayError && <Alert tone="error">{displayError}</Alert>}
      {boot?.failedSections && boot.failedSections.length > 0 && (
        <Alert tone="warning">Не удалось загрузить: {boot.failedSections.map(section => adminSectionLabels[section]).join(', ')}. Ранее полученные сведения могут быть устаревшими.</Alert>
      )}
      {success && <Alert tone="success">{success}</Alert>}

      {tab !== 'parks' && <Tabs
        ariaLabel="Разделы настроек"
        wrapOnPhone
        panelIdFor={id => `admin-panel-${id}`}
        items={[
          ...(canIntegrations ? [{ id: 'integrations', label: 'Интеграции' }] : []),
          ...(canIntegrations ? [{ id: 'health', label: 'Состояние сервера' }] : []),
          ...(canTelegram ? [{ id: 'telegram', label: 'Telegram' }] : []),
          ...(user?.role === 'royal' ? [{ id: 'ops', label: ru.ops.tab }] : []),
        ]}
        onChange={(id) => setTab(id as TabId)}
        value={tab}
      />}

      {/* --- Integrations ------------------------------------------------- */}
      {canIntegrations && <TabPanel id="health" active={tab === 'health'}><HostHealthPanel /></TabPanel>}
      {canTelegram && <TabPanel id="telegram" active={tab === 'telegram'}>
        {user?.role === 'royal' ? <BotSettingsPanel showLegacyImport={false} /> : null}
        {user?.role === 'royal' ? <BotAuxiliaryQueuesPanel /> : null}
        <TelegramRuntimePanel royal={user?.role === 'royal'} />
        <NativeTelegramPanel initialParkId={parkId} showMigration={user?.role === 'royal'} />
      </TabPanel>}
      {canIntegrations && <TabPanel id="integrations" active={tab === 'integrations'}>
        {user?.role === 'royal' && (
          <Panel
            collapsible
            hint="Если пароль не задан, регистрация на /register закрыта."
            storageKey="admin-registration-password"
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
                    {registrationSettings.configured ? 'Установлен' : 'Не задан'}
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
          collapsible
          hint="Секреты хранятся зашифрованными. Здесь только статус — без маски значения."
          storageKey="admin-integration-secrets"
          title="Секреты"
        >
          {settings && (
            <div className="rp-management-metrics">
              <MetricCard label="Tracker OAuth" value={worksBadge(Boolean(settings.tracker_token_masked))} />
              <MetricCard label="Проверка cookie" value={emergencyCookieStatus(settings)} />
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
                id="tracker-token"
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
                disabled={emergencyBusy || !emergencyCookie.trim()}
                type="submit"
              >
                {emergencyBusy ? 'Сохраняем…' : 'Сохранить cookie'}
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
          <Panel density="dense" hint="Влияет на то, что видят операторы и механики." title="Политика Tracker">
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

        <Panel collapsible density="dense" hint={ru.screenshotGuard.adminHint} storageKey="admin-screenshot-guard" title="Защита от скриншотов">
          {!screenshotGuard || !screenshotGuardLive ? <>
            <p role="status">{bootRes.isRevalidating
              ? 'Загрузка состояния защиты…'
              : 'Состояние защиты не загружено. Изменения недоступны, пока сервер не вернёт текущие настройки.'}</p>
            <button className="btn btn-secondary" disabled={bootRes.isRevalidating} onClick={() => void bootRes.refresh()} type="button">Повторить загрузку настроек</button>
          </> :
          <div className="toggle-list">
            {(['operator', 'mechanic', 'driver', 'admin', 'royal'] as const).map((role) => (
              <Toggle
                key={role}
                checked={Boolean(screenshotGuard[role])}
                disabled={busy || !screenshotGuardLive}
                label={ru.screenshotGuard.adminToggle(roleLabel(role))}
                onChange={(next) => {
                  if (!screenshotGuard || !screenshotGuardLive) return
                  return run(async () => {
                    const updated = await api.updateScreenshotGuardSettings({ [role]: next })
                    setScreenshotGuard(updated)
                    setScreenshotGuardLive(true)
                  }, 'Защита обновлена')
                }}
              />
            ))}
          </div>}
        </Panel>

        <Panel density="dense" title="Быстрые переходы">
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
            <Link className="btn btn-secondary" to="/admin/emergency/config?tab=errors">
              Разметка и игнорирование ошибок
            </Link>
          </div>
        </Panel>
      </TabPanel>}

      {canParks && (
      <TabPanel id="parks" active={tab === 'parks'}>
        <Panel actions={<button className="btn btn-secondary" onClick={() => { setCreateParkOpen(true); setEditingParkId(null) }} type="button">Добавить парк</button>} hint="Найдите парк и откройте его настройки." title="Парки">
          <p>SLA — 5 рабочих часов от входа задачи в очередь, ежедневно с 09:00 до 21:00 по времени парка.</p>
          <label className="field"><span className="field-label">Поиск</span><input aria-label="Поиск парков" onChange={(event) => setParkSearch(event.target.value)} role="searchbox" value={parkSearch} /></label>
          <ul className="card-list">{parks.filter((park) => `${park.name} ${park.tag}`.toLowerCase().includes(parkSearch.trim().toLowerCase())).map((park) => <li className="card action-row" key={park.id}><div><div className="card-title">{park.name}</div><div className="card-meta">{park.tag}</div></div><button aria-label={`Открыть парк ${park.name}`} className="btn btn-secondary" onClick={() => { setEditingParkId(park.id); setCreateParkOpen(false) }} type="button">Открыть</button></li>)}</ul>
        </Panel>
        {parkRequests.length > 0 && (
        <Panel
          collapsible
          hint="Операторы запрашивают дополнительные парки из своего кабинета."
          storageKey="admin-park-requests"
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

        {createParkOpen ? <Panel actions={<button className="btn btn-ghost" onClick={() => setCreateParkOpen(false)} type="button">Закрыть</button>} hint="Тег используется в Tracker; очередь нужна для задач и поиска." title="Новый парк">
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
            <label className="field">
              <span className="field-label">Часовой пояс парка</span>
              <input
                aria-invalid={!validParkTimezone(timezone)}
                onChange={(event) => setTimezone(event.target.value)}
                placeholder="Europe/Moscow"
                required
                value={timezone}
              />
              <span className="field-hint">Формат IANA, например Asia/Yekaterinburg. SLA: 5 часов ежедневно с 09:00 до 21:00 по времени парка.</span>
              {!validParkTimezone(timezone) && <span className="field-hint" role="alert">Укажите действительный часовой пояс IANA.</span>}
            </label>
            <label className="field">
              <span className="field-label">Очередь Tracker</span>
              <input
                autoCapitalize="characters"
                onChange={(event) => setNewTrackerQueue(event.target.value)}
                placeholder="SDCFLEETOPS"
                value={newTrackerQueue}
              />
              <span className="field-hint">Если очередь ещё не создана, её можно указать позже в настройках парка. Пока она пуста, задачи Tracker не появятся.</span>
            </label>
            <div className="form-actions">
              <button className="btn" disabled={busy || !validParkTimezone(timezone)} type="submit">
                {ru.create}
              </button>
            </div>
          </form>
        </Panel> : null}

        {parks.length === 0 ? (
          <EmptyBlock
            hint="Парк нужен, чтобы назначать операторов и механиков."
            icon="🏭"
            title="Парков пока нет"
          />
        ) : (
          parks.filter((park) => park.id === editingParkId).map((park) => (
            <Panel
              actions={<><Badge active={park.is_active ?? true} /><button className="btn btn-ghost" onClick={() => setEditingParkId(null)} type="button">Закрыть</button></>}
              key={park.id}
              title={`Редактор: ${park.name || `Парк #${park.id}`}`}
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
                  <span className="field-label">Часовой пояс парка</span>
                  <input
                    aria-invalid={!validParkTimezone(park.timezone)}
                    onChange={(event) => editPark(park.id, { timezone: event.target.value })}
                    required
                    value={park.timezone}
                  />
                  <span className="field-hint">Формат IANA. Новые задачи получат эту зону при входе в очередь; уже зафиксированные сроки SLA сохраняют исходную зону.</span>
                  {!validParkTimezone(park.timezone) && <span className="field-hint" role="alert">Укажите действительный часовой пояс IANA.</span>}
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
                  <span className="field-label">Chat ID Telegram</span>
                  <input
                    readOnly
                    value={park.chat_id ?? ''}
                  />
                  <span className="field-hint">
                    {canTelegram
                      ? <>Управляется в разделе <Link to={`/admin/settings?park=${park.id}&tab=telegram`}>Telegram</Link>.</>
                      : 'Управляется администратором в разделе Telegram.'}
                  </span>
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
                  disabled={!park.name || !park.tag || busy || !validParkTimezone(park.timezone)}
                  onClick={() =>
                    runParkMutation(async () => {
                      await api.updatePark(park.id, {
                        name: park.name,
                        tag: park.tag,
                        timezone: park.timezone,
                        tracker_queue: park.tracker_queue || null,
                        tracker_priority: park.tracker_priority || null,
                        tracker_type: park.tracker_type || null,
                        group_id: park.group_id ?? null,
                        feature_blockers: park.feature_blockers,
                        feature_reports: park.feature_reports,
                        feature_sla_repair: park.feature_sla_repair,
                        feature_backlog_alerts: park.feature_backlog_alerts,
                      })
                      pendingParkEdits.current.delete(park.id)
                    })
                  }
                  type="button"
                >
                  {ru.save}
                </button>
                <button
                  className="btn btn-secondary"
                  disabled={busy}
                  onClick={() =>
                    runParkMutation(() => api.updatePark(park.id, { is_active: !park.is_active }))
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
        <TabPanel id="ops" active={tab === 'ops'}>
          <Panel collapsible hint={ru.ops.hint} storageKey="admin-ops" title={ru.ops.title}>
            <AdminOpsPanel />
          </Panel>
        </TabPanel>
      )}
    </PageShell>
  )
}
