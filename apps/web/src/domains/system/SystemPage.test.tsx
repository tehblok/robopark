import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AuthContext } from '../../auth-context'
import { ApiError, type User } from '../../api'
import { SystemPage } from './SystemPage'
import type { HostCapabilities, HostOperationContext, SystemClient, SystemJob, SystemSummary } from '../../opsApi'
import { operationReservationKey, readOperationReservation as readScopedOperationReservation, writeOperationReservation as writeScopedOperationReservation } from './operationReservation'

const revision = 'a'.repeat(64)
const kinds = [
  'ota-update', 'rollback', 'package-inspect', 'package-update',
  'service-restart', 'reboot', 'backup', 'backup-verify', 'backup-restore',
  'cleanup-preview', 'cleanup-execute', 'docker-image-preview', 'docker-image-execute',
  'builder-cache-preview', 'builder-cache-execute', 'diagnostics', 'usb-discover', 'usb-format',
  'usb-select',
] as const
const safe = new Set(['package-inspect', 'backup-verify', 'cleanup-preview', 'cleanup-execute', 'docker-image-preview', 'docker-image-execute', 'builder-cache-preview', 'builder-cache-execute', 'diagnostics', 'usb-discover', 'usb-select'])

const summary: SystemSummary = {
  sampled_at: '2026-09-25T09:00:00Z', metrics_stale: false,
  worker_health: 'worker_healthy',
  online: { total: 4, by_role: { mechanic: 3, admin: 1 }, by_park: { '7': 4 } },
  sync: { cursor_age_seconds: 12, pending_action_count: 2, oldest_pending_action_age_seconds: 20, retry_count: 1, needs_attention_count: 1, last_success_at: '2026-09-25T08:59:00Z', last_error: null, worker_lease_state: 'active' },
  push: { pending: 1, needs_attention: 0 },
  metrics: { host: {
    cpu: { state: 'ok', load_1m: 0.4, cores: 4 },
    disk: { total_bytes: 1000, free_bytes: 400 },
    memory: { total_bytes: 1000, available_bytes: 500, container_limit_bytes: 800, container_used_bytes: 300 },
    postgresql: { state: 'ok' }, container: { state: 'ok' }, tuna: { state: 'degraded' }, internet: { state: 'ok' },
    requests: { tracker: { requests: 3, errors: 1, limited: 0, average_ms: 100, max_ms: 200 } },
    storage: { floor_bytes: 100, bytes_to_reclaim: 10, category_bytes: { logs: 20 }, last_cleanup_at: 1, cleanup_failed: false },
    backup: { verified_at: 1, overdue: false, last_attempt_failed: false },
  } },
  release: { version: '0.2.0-rc.6', cleanup: { state: 'ready' } },
}

const capabilities: HostCapabilities = {
  state: 'ready', generated_at: '2026-09-25T09:00:00Z', expires_at: '2099-09-25T09:05:00Z', revision,
  operations: Object.fromEntries(kinds.map(kind => [kind, safe.has(kind)
    ? { available: true, unavailable_reason: null }
    : { available: false, unavailable_reason: 'capability_unavailable' }])) as HostCapabilities['operations'],
}
const operationContext: HostOperationContext = {
  generated_at: '2026-09-30T12:00:00Z', expires_at: '2099-09-30T12:05:00Z',
  selected_device_uuid: null, rollback_release: '0.2.0-rc.10',
  packages: ['docker-ce', 'docker-ce-cli', 'containerd.io', 'openssl'],
  services: ['robopark.service', 'robopark-tuna.service', 'docker.service'],
  devices: [{ device_uuid: '44444444-4444-4444-8444-444444444444', removable: true, mounted: false }],
  backups: [{ backup_id: '55555555-5555-4555-8555-555555555555', bytes: 8192, verified: true, created_at: '2026-09-30T10:00:00Z' }],
}

function client(overrides: Partial<SystemClient> = {}): SystemClient {
  return {
    getSummary: vi.fn().mockResolvedValue(summary),
    getHistory: vi.fn().mockResolvedValue({ active_users: [{ date: '2026-09-23', users: 5 }, { date: '2026-09-24', users: 6 }], metrics: [] }),
    getCapabilities: vi.fn().mockResolvedValue(capabilities),
    getOperation: vi.fn().mockRejectedValue(new ApiError(404, 'operation_not_found')),
    getOperations: vi.fn().mockResolvedValue({ items: [] }),
    getOperationContext: vi.fn().mockResolvedValue(operationContext),
    operationArtifactUrl: vi.fn(operationId => `/api/admin/ops/operations/${operationId}/artifact`),
    reauthorize: vi.fn().mockResolvedValue({ token: 'reauth-token', expires_in: 120 }),
    startOperation: vi.fn().mockImplementation(async payload => ({ id: payload.operation_id, kind: payload.kind, state: 'running', phase: 'accepted', progress_percent: 0, error: null })),
    ...overrides,
  }
}

const user = (role: 'admin' | 'royal'): User => ({
  id: 1, username: role, role, access_status: 'approved', permissions: ['nav.admin'], parks: [],
})
const reservationActor = user('royal')
const readOperationReservation = () => readScopedOperationReservation(reservationActor)
const writeOperationReservation = (value: Parameters<typeof writeScopedOperationReservation>[1]) => writeScopedOperationReservation(reservationActor, value)
function tree(role: 'admin' | 'royal', api = client()) {
  return <MemoryRouter><AuthContext.Provider value={{ user: user(role), loading: false, login: vi.fn(), logout: vi.fn(), refreshUser: vi.fn() }}><SystemPage client={api} /></AuthContext.Provider></MemoryRouter>
}

afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); localStorage.clear() })

describe('SystemPage', () => {
  it('offers the isolated terminal document only to royal', async () => {
    const view = render(tree('royal'))
    const link = await screen.findByRole('link', { name: 'Терминал хоста' })
    expect(link).toHaveAttribute('href', '/terminal.html')
    view.rerender(tree('admin'))
    await waitFor(() => expect(screen.queryByRole('link', { name: 'Терминал хоста' })).not.toBeInTheDocument())
  })

  it('names host source failures instead of presenting missing disk and agent readings as empty data', async () => {
    render(tree('royal', client({ getSummary: vi.fn().mockResolvedValue({
      ...summary,
      metrics: { host: {
        ...summary.metrics?.host,
        disk: { total_bytes: null, free_bytes: null, source_state: 'unavailable' },
        host_health_source_state: 'unavailable',
      } },
    }) })))

    expect(await screen.findByText('Не удалось измерить диск API. Проверьте доступность каталога данных на хосте.')).toBeVisible()
    expect(screen.getByText('Снимок host agent отсутствует или повреждён. Проверьте службу host agent и её журнал.')).toBeVisible()
  })

  it('shows a failed BuildKit budget action to the owner', async () => {
    render(tree('royal', client({ getSummary: vi.fn().mockResolvedValue({
      ...summary,
      metrics: { host: {
        ...summary.metrics?.host,
        storage: { builder_cache_budget: { attempted: true, blocked: true } },
      } },
    }) })))

    expect(await screen.findByText('Ограничение кэша сборки Docker не выполнено. Проверьте журнал host agent и доступность собственного BuildKit builder Robopark.')).toBeVisible()
  })

  it('describes stale telemetry without claiming that independent host actions are unavailable', async () => {
    render(tree('royal', client({ getSummary: vi.fn().mockResolvedValue({ ...summary, metrics_stale: true }) })))

    expect(await screen.findByText('Метрики устарели. Сведения о ресурсах могут быть неактуальны; проверьте сбор метрик worker.')).toBeVisible()
    expect(screen.queryByText(/Действия хоста недоступны до свежего снимка возможностей/)).not.toBeInTheDocument()
  })

  it('explains an empty user history instead of showing an empty chart', async () => {
    render(tree('admin', client({ getHistory: vi.fn().mockResolvedValue({ active_users: [], metrics: [] }) })))

    expect(await screen.findByText('История активности ещё не собрана.')).toBeVisible()
    expect(screen.queryByRole('img', { name: 'Активные пользователи за 7 дней' })).not.toBeInTheDocument()
  })

  it('shows the first day of activity without a misleading single-bar chart', async () => {
    render(tree('admin', client({ getHistory: vi.fn().mockResolvedValue({ active_users: [{ date: '2026-09-24', users: 6 }], metrics: [] }) })))

    expect(await screen.findByText('История за один день. График появится после следующего дня.')).toBeVisible()
    expect(screen.queryByRole('img', { name: 'Активные пользователи за 7 дней' })).not.toBeInTheDocument()
    const values = screen.getByRole('table', { name: 'Активные пользователи за 7 дней — значения' })
    expect(within(values).getByText('24.09.2026')).toBeVisible()
    expect(within(values).getByText('6')).toBeVisible()
  })

  it('does not call an absent first backup verified on a clean installation', async () => {
    render(tree('royal', client({ getSummary: vi.fn().mockResolvedValue({
      ...summary, metrics: { host: { ...summary.metrics?.host, backup: { verified_at: null, overdue: false, last_attempt_failed: false } } },
    }) })))

    expect(await screen.findByText('Резервная копия: не подтверждена')).toBeVisible()
    expect(screen.queryByText('Резервная копия: проверена')).not.toBeInTheDocument()
  })

  it('shows a failed latest backup attempt even when an earlier copy was verified', async () => {
    render(tree('royal', client({ getSummary: vi.fn().mockResolvedValue({
      ...summary, metrics: { host: { ...summary.metrics?.host, backup: { verified_at: 1, overdue: false, last_attempt_failed: true } } },
    }) })))

    expect(await screen.findByText('Последняя попытка резервного копирования завершилась ошибкой.')).toBeVisible()
  })

  it('shows small free space and memory in useful units instead of rounding them to zero gigabytes', async () => {
    const mebibyte = 1024 ** 2
    render(tree('admin', client({ getSummary: vi.fn().mockResolvedValue({
      ...summary,
      metrics: { host: {
        ...summary.metrics?.host,
        disk: { total_bytes: 64 * 1024 ** 3, free_bytes: 20 * mebibyte },
        memory: { total_bytes: 4 * 1024 ** 3, available_bytes: 512 * mebibyte },
        storage: { bytes_to_reclaim: 20 * mebibyte },
      } },
    }) })))

    expect(await screen.findByText('20 МБ свободно')).toBeVisible()
    expect(screen.getByText('>99%')).toBeVisible()
    expect(screen.queryByText('100%')).not.toBeInTheDocument()
    expect(screen.getByText('512 МБ доступно')).toBeVisible()
    expect(screen.getByText('К очистке: 20 МБ')).toBeVisible()
  })

  it('shows measured storage categories without treating missing categories as zero', async () => {
    render(tree('royal', client({
      getSummary: vi.fn().mockResolvedValue({
        ...summary,
        metrics: { host: {
          ...summary.metrics?.host,
          storage: { category_bytes: { logs: 20 * 1024 ** 2, diagnostics: 5 * 1024 ** 2, live_merge: 2 * 1024 ** 2, report_attachments: 3 * 1024 ** 2, tracker_uploads: 4 * 1024 ** 2, backups: 6 * 1024 ** 2, scheduled_backups: 15 * 1024 ** 2, releases: 7 * 1024 ** 2, ota_uploads: 8 * 1024 ** 2, ota_cache: 9 * 1024 ** 2, docker_images: 10 * 1024 ** 2, buildkit_cache: 11 * 1024 ** 2, robopark_buildkit_reported: 18 * 1024 ** 2, robopark_buildkit_private_reclaimable: 17 * 1024 ** 2, docker_volumes: 12 * 1024 ** 2, journald: 13 * 1024 ** 2, postgresql_data: 14 * 1024 ** 2 } },
        } },
      }),
    })))

    const section = await screen.findByRole('region', { name: 'Занятое место на хосте' })
    expect(within(section).getByText('Журналы Robopark')).toBeVisible()
    expect(within(section).getByText('20 МБ')).toBeVisible()
    expect(within(section).getByText('Диагностика')).toBeVisible()
    expect(within(section).getByText('5 МБ')).toBeVisible()
    expect(within(section).getByText('Данные синхронизации')).toBeVisible()
    expect(within(section).getByText('2 МБ')).toBeVisible()
    expect(within(section).getByText('Вложения репортов')).toBeVisible()
    expect(within(section).getByText('3 МБ')).toBeVisible()
    expect(within(section).getByText('Файлы Tracker')).toBeVisible()
    expect(within(section).getByText('4 МБ')).toBeVisible()
    expect(within(section).getByText('Резервные копии')).toBeVisible()
    expect(within(section).getByText('6 МБ')).toBeVisible()
    expect(within(section).getByText('Локальные снимки')).toBeVisible()
    expect(within(section).getByText('15 МБ')).toBeVisible()
    expect(within(section).getByText(/ручной предпросмотр очистки ниже.*не включает локальные снимки/i)).toBeInTheDocument()
    expect(within(section).getByText('Релизы')).toBeVisible()
    expect(within(section).getByText('7 МБ')).toBeVisible()
    expect(within(section).getByText('Загрузки OTA')).toBeVisible()
    expect(within(section).getByText('8 МБ')).toBeVisible()
    expect(within(section).getByText('Кэш OTA')).toBeVisible()
    expect(within(section).getByText('9 МБ')).toBeVisible()
    expect(within(section).getByText('Образы Docker на хосте')).toBeVisible()
    expect(within(section).getByText('10 МБ')).toBeVisible()
    expect(within(section).getByText('Кэш Docker Engine (без отдельного Buildx)')).toBeVisible()
    expect(within(section).getByText('BuildKit Robopark (отчётный объём)')).toBeVisible()
    expect(within(section).getByText('18 МБ')).toBeVisible()
    expect(within(section).getByText('Из него приватный кэш, доступный для очистки')).toBeVisible()
    expect(within(section).getByText('17 МБ')).toBeVisible()
    expect(within(section).getByText(/Размеры записей BuildKit могут пересекаться/)).toBeInTheDocument()
    expect(within(section).getByText(/Автоматическая очистка кэша ограничена сборщиком Robopark/)).toBeInTheDocument()
    expect(within(section).getByText('11 МБ')).toBeVisible()
    expect(within(section).getByText('Тома Docker (данные и служебные хранилища)')).toBeVisible()
    expect(within(section).getByText('12 МБ')).toBeVisible()
    expect(within(section).getByText('Журнал systemd')).toBeVisible()
    expect(within(section).getByText('13 МБ')).toBeVisible()
    expect(within(section).getByText('Данные PostgreSQL')).toBeVisible()
    expect(within(section).getByText('14 МБ')).toBeVisible()
    expect(within(section).getByText(/Том PostgreSQL измерен отдельно/)).toBeInTheDocument()
    expect(within(section).getByText(/Цифры Docker включают все проекты хоста/)).toBeInTheDocument()
  })

  it('keeps missing host measurements available without filling the page with empty rows', async () => {
    render(tree('royal', client({ getSummary: vi.fn().mockResolvedValue({
      ...summary,
      metrics: { host: { ...summary.metrics?.host, storage: {
        inventory_failed: true,
        category_bytes: { logs: 20 * 1024 ** 2 },
      } } },
    }) })))

    const section = await screen.findByRole('region', { name: 'Занятое место на хосте' })
    expect(within(section).getByText(/Не удалось измерить хранилище хоста/)).toBeVisible()
    const measured = section.querySelector('.rp-system-storage-breakdown')
    expect(measured).toHaveTextContent('Журналы Robopark')
    expect(measured).toHaveTextContent('20 МБ')
    expect(measured).not.toHaveTextContent('Диагностика')
    const missing = within(section).getByText(/Не измерено: 16 категорий/).closest('details')
    expect(missing).not.toHaveAttribute('open')
    expect(missing).toHaveTextContent('Диагностика')
  })

  it('shows the essential storage caution and keeps detailed policy behind a disclosure', async () => {
    render(tree('royal', client()))

    const section = await screen.findByRole('region', { name: 'Занятое место на хосте' })
    expect(within(section).getByText('Размеры категорий могут пересекаться; складывать их нельзя. Тома Docker и PostgreSQL не удаляются.')).toBeVisible()
    const explanation = within(section).getByText('Как читать размеры и что входит в очистку').closest('details')
    expect(explanation).not.toHaveAttribute('open')
    expect(explanation).toHaveTextContent('Ежедневный таймер оставляет до 14 снимков')
    expect(explanation).toHaveTextContent('Автоматическая очистка кэша ограничена сборщиком Robopark')
  })

  it('names a missing storage inventory once and leaves all category names inspectable', async () => {
    render(tree('royal', client({ getSummary: vi.fn().mockResolvedValue({
      ...summary, metrics: { host: { ...summary.metrics?.host, storage: { category_bytes: {} } } },
    }) })))

    const section = await screen.findByRole('region', { name: 'Занятое место на хосте' })
    expect(within(section).getByText('Измерения занятого места пока не получены.')).toBeVisible()
    expect(Array.from(section.querySelectorAll('.rp-system-storage-breakdown')).every(list => list.closest('details'))).toBe(true)
    const missing = within(section).getByText(/Не измерено: 17 категорий/).closest('details')
    expect(missing).not.toHaveAttribute('open')
    expect(missing).toHaveTextContent('Данные PostgreSQL')
  })

  it('explains a failed host storage measurement', async () => {
    render(tree('royal', client({ getSummary: vi.fn().mockResolvedValue({
      ...summary, metrics: { host: { ...summary.metrics?.host, storage: { inventory_failed: true, category_bytes: {} } } },
    }) })))
    expect(await screen.findByText(/Не удалось измерить хранилище хоста/)).toBeVisible()
    expect(screen.getByText(/Объём PostgreSQL не измерен/)).toBeInTheDocument()
    expect(screen.getByText(/Размеры категорий могут пересекаться; складывать их нельзя/)).toBeVisible()
  })

  it('warns when host storage measurements have expired', async () => {
    render(tree('royal', client({ getSummary: vi.fn().mockResolvedValue({
      ...summary, metrics: { host: { ...summary.metrics?.host, storage: { inventory_stale: true, category_bytes: {} } } },
    }) })))
    expect(await screen.findByText(/Измерение хранилища устарело/)).toBeVisible()
  })

  it('warns when the host cleanup measurement has expired', async () => {
    render(tree('royal', client({ getSummary: vi.fn().mockResolvedValue({
      ...summary, metrics: { host: { ...summary.metrics?.host, storage: { cleanup_stale: true, category_bytes: {} } } },
    }) })))
    expect(await screen.findByText(/Измерение журналов и диагностики устарело/)).toBeVisible()
  })

  it('separates a successful cleanup from remaining disk pressure', async () => {
    render(tree('royal', client({ getSummary: vi.fn().mockResolvedValue({
      ...summary, metrics: { host: { ...summary.metrics?.host, storage: {
        cleanup_failed: false, space_pressure: true, bytes_to_reclaim: null, category_bytes: {},
      } } },
    }) })))
    expect(await screen.findByText('Очистка: без ошибок')).toBeVisible()
    expect(screen.getByText('Место: недостаточно')).toBeVisible()
    expect(screen.getByText('Объём к освобождению не измерен')).toBeVisible()
    expect(screen.queryByText('Очистка: ошибка')).not.toBeInTheDocument()
  })

  it('shows a busy host cleanup as deferred', async () => {
    render(tree('royal', client({ getSummary: vi.fn().mockResolvedValue({
      ...summary, metrics: { host: { ...summary.metrics?.host, storage: {
        cleanup_failed: false, cleanup_busy: true, category_bytes: {},
      } } },
    }) })))
    expect(await screen.findByText('Очистка: отложена')).toBeVisible()
    expect(screen.queryByText('Очистка: ошибка')).not.toBeInTheDocument()
  })

  it('keeps cleanup errors ahead of a simultaneous busy marker', async () => {
    render(tree('royal', client({ getSummary: vi.fn().mockResolvedValue({
      ...summary, metrics: { host: { ...summary.metrics?.host, storage: {
        cleanup_failed: true, cleanup_busy: true, api_cleanup_failed: true, category_bytes: {},
      } } },
    }) })))
    expect(await screen.findByText('Очистка: ошибка')).toBeVisible()
    expect(screen.queryByText('Очистка: отложена')).not.toBeInTheDocument()
  })

  it('keeps live telemetry visible and reports a host bridge failure', async () => {
    const api = client({
      getCapabilities: vi.fn().mockRejectedValue(new ApiError(503, 'host_bridge_unavailable')),
    })
    render(tree('royal', api))

    expect(await screen.findByText('Сейчас в системе')).toBeVisible()
    expect(screen.getByText('4')).toBeVisible()
    expect(screen.getByText(/host_bridge_unavailable/)).toBeVisible()
    expect(screen.queryByRole('region', { name: 'Управляемые операции' })).not.toBeInTheDocument()
  })

  it('shows fresh metrics without presenting a failed history read as zero activity', async () => {
    const getHistory = vi.fn()
      .mockRejectedValueOnce(new ApiError(503, 'history_database_unavailable'))
      .mockResolvedValue({ active_users: [{ date: '2026-09-23', users: 5 }, { date: '2026-09-24', users: 6 }], metrics: [] })
    render(tree('admin', client({ getHistory })))

    expect(await screen.findByText('Сейчас в системе')).toBeVisible()
    expect(screen.getByText(/Не удалось получить историю: HTTP 503: history_database_unavailable/)).toBeVisible()
    expect(screen.queryByRole('img', { name: 'Активные пользователи за 7 дней' })).not.toBeInTheDocument()
    fireEvent(window, new Event('focus'))
    expect(await screen.findByRole('img', { name: 'Активные пользователи за 7 дней' })).toBeVisible()
    expect(screen.queryByText(/Не удалось получить историю/)).not.toBeInTheDocument()
  })

  it('shows current telemetry while history and host bridge requests are still pending', async () => {
    const getHistory = vi.fn().mockImplementation(() => new Promise(() => {}))
    const getCapabilities = vi.fn().mockImplementation(() => new Promise(() => {}))
    render(tree('royal', client({ getHistory, getCapabilities })))

    expect(await screen.findByText('Сейчас в системе')).toBeVisible()
    expect(screen.getByText('4')).toBeVisible()
    expect(getHistory).toHaveBeenCalledOnce()
    expect(getCapabilities).toHaveBeenCalledOnce()
  })

  it('does not retain system metrics if history access is revoked', async () => {
    render(tree('admin', client({
      getHistory: vi.fn().mockRejectedValue(new ApiError(403, 'forbidden')),
    })))

    expect(await screen.findByText(/Не удалось получить свежие данные: HTTP 403/)).toBeVisible()
    expect(screen.queryByText('Сейчас в системе')).not.toBeInTheDocument()
  })

  it('clears telemetry if a pending history request later reports revoked access', async () => {
    let rejectHistory!: (reason: unknown) => void
    const historyRead = new Promise<Awaited<ReturnType<SystemClient['getHistory']>>>((_, reject) => {
      rejectHistory = reject
    })
    render(tree('admin', client({ getHistory: vi.fn().mockReturnValue(historyRead) })))

    expect(await screen.findByText('Сейчас в системе')).toBeVisible()
    await act(async () => rejectHistory(new ApiError(403, 'forbidden')))
    expect(await screen.findByText(/Не удалось получить свежие данные: HTTP 403/)).toBeVisible()
    expect(screen.queryByText('Сейчас в системе')).not.toBeInTheDocument()
  })

  it('keeps telemetry visible when only a reserved operation lookup fails', async () => {
    const id = '11111111-1111-4111-8111-111111111111'
    writeOperationReservation({ id, kind: 'diagnostics', created_at: 0, phase: 'reconciling' })
    const getOperation = vi.fn()
      .mockRejectedValueOnce(new ApiError(503, 'operation_bridge_unavailable'))
      .mockResolvedValue({ id, kind: 'diagnostics', state: 'running', phase: 'executing', progress_percent: 50, error: null })
    render(tree('royal', client({ getOperation })))

    expect(await screen.findByText('Сейчас в системе')).toBeVisible()
    expect(screen.getByText(/Не удалось получить состояние операции: HTTP 503: operation_bridge_unavailable/)).toBeVisible()
    expect(screen.queryByText(/Не удалось получить свежие данные/)).not.toBeInTheDocument()
    expect(readOperationReservation()?.id).toBe(id)
    fireEvent(window, new Event('focus'))
    expect(await screen.findByRole('progressbar', { name: 'Прогресс операции' })).toHaveAttribute('value', '50')
    expect(screen.queryByText(/Не удалось получить состояние операции/)).not.toBeInTheDocument()
    expect(readOperationReservation()?.id).toBe(id)
  })

  it('clears protected telemetry when the reserved operation lookup loses access', async () => {
    writeOperationReservation({ id: '11111111-1111-4111-8111-111111111111', kind: 'diagnostics', created_at: 0, phase: 'reconciling' })
    render(tree('royal', client({
      getOperation: vi.fn().mockRejectedValue(new ApiError(403, 'forbidden')),
    })))

    expect(await screen.findByText(/Не удалось получить свежие данные: HTTP 403/)).toBeVisible()
    expect(screen.queryByText('Сейчас в системе')).not.toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'Управляемые операции' })).not.toBeInTheDocument()
  })

  it('shows a backend status instead of endless loading when summary fails', async () => {
    render(tree('admin', client({
      getSummary: vi.fn().mockRejectedValue(new ApiError(503, 'database_unavailable')),
    })))

    expect(await screen.findByText(/HTTP 503: database_unavailable/)).toBeVisible()
    expect(screen.queryByRole('status', { name: 'Загружаем состояние системы' })).not.toBeInTheDocument()
  })

  it('does not expose arbitrary backend error text', async () => {
    render(tree('admin', client({
      getSummary: vi.fn().mockRejectedValue(new ApiError(503, 'unexpected=detail')),
    })))

    expect(await screen.findByText(/HTTP 503/)).toBeVisible()
    expect(screen.queryByText(/unexpected=detail/)).not.toBeInTheDocument()
  })

  it('shows scoped current and seven-day health to admin without loading or mutating host operations', async () => {
    const api = client()
    render(tree('admin', api))
    expect(await screen.findByText('Сейчас в системе')).toBeVisible()
    expect(screen.getByText('4')).toBeVisible()
    expect(screen.getByRole('img', { name: 'Активные пользователи за 7 дней' })).toBeVisible()
    for (const label of ['Нагрузка CPU · 1 мин', 'Память', 'Диск', 'PostgreSQL', 'Контейнеры', 'Сеть', 'Wi‑Fi', 'Очередь', 'Worker', 'Tracker', 'Tuna', 'Хранилище']) {
      expect(screen.getByText(label)).toBeVisible()
    }
    expect(screen.getByRole('link', { name: 'Открыть ошибки синхронизации' })).toHaveAttribute('href', '/work?sync=needs_attention')
    expect(api.getCapabilities).not.toHaveBeenCalled()
    expect(api.reauthorize).not.toHaveBeenCalled()
    expect(api.startOperation).not.toHaveBeenCalled()
    expect(screen.queryByRole('textbox', { name: /команд|argv/i })).not.toBeInTheDocument()
  })

  it('requires a completed preview before enabling cleanup and explains unavailable kinds', async () => {
    render(tree('royal'))
    const operations = await screen.findByRole('region', { name: 'Управляемые операции' })
    expect(within(operations).getByRole('button', { name: 'Предпросмотр очистки' })).toBeEnabled()
    expect(within(operations).getByRole('button', { name: 'Выполнить очистку' })).toBeDisabled()
    expect(within(operations).getByText('Недоступные действия (7)')).toBeVisible()
    expect(within(operations).getAllByText('Недоступно на этом хосте').length).toBe(7)
    expect(within(operations).queryByLabelText(/команд|argv/i)).not.toBeInTheDocument()
  })

  it('explains the preview-first cleanup flow and separates Docker volumes from reclaimable cache', async () => {
    render(tree('royal', client({ getSummary: vi.fn().mockResolvedValue({
      ...summary,
      metrics: { host: { ...summary.metrics?.host, storage: { category_bytes: {
        docker_volumes: 6.1 * 1024 ** 3, buildkit_cache: 0,
      } } } },
    }) })))

    expect(await screen.findByText('Тома Docker (данные и служебные хранилища)')).toBeVisible()
    expect(screen.getByText('Кэш Docker Engine (без отдельного Buildx)')).toBeVisible()
    expect(screen.getByText(/0 Б.*не означает.*Buildx/i)).toBeVisible()
    expect(screen.getByText(/1\. Запустите предпросмотр/)).toBeVisible()
    expect(screen.getByText(/6,1 ГБ/)).toBeVisible()
  })

  it('shows terminal-active as a definite refusal and clears the operation reservation', async () => {
    const api = client({ startOperation: vi.fn().mockRejectedValue(new ApiError(409, 'terminal_active')) })
    render(tree('royal', api))
    fireEvent.click(await screen.findByRole('button', { name: 'Собрать диагностику' }))
    const dialog = screen.getByRole('dialog', { name: 'Подтвердить операцию' })
    fireEvent.change(within(dialog).getByLabelText('Введите ЗАПУСТИТЬ DIAGNOSTICS'), { target: { value: 'ЗАПУСТИТЬ DIAGNOSTICS' } })
    fireEvent.change(within(dialog).getByLabelText('Пароль'), { target: { value: 'secret' } })
    fireEvent.change(within(dialog).getByLabelText('Код TOTP или восстановления'), { target: { value: '123456' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Запустить' }))

    expect(await within(dialog).findByText('Завершите активные терминальные сессии и повторите операцию.')).toBeVisible()
    expect(readOperationReservation()).toBeNull()
    expect(screen.queryByText('Проверяем получение запроса')).not.toBeInTheDocument()
  })

  it('restores terminal operation results after reload and exposes the diagnostic download directly', async () => {
    const diagnosticId = '11111111-1111-4111-8111-111111111111'
    const packageId = '22222222-2222-4222-8222-222222222222'
    const api = client({ getOperations: vi.fn().mockResolvedValue({ items: [
      { id: diagnosticId, kind: 'diagnostics', receipt_state: 'terminal', state: 'succeeded', phase: 'completed', progress_percent: 100, error: null, artifact_ready: true, host_result: { diagnostics_ready: true } },
      { id: packageId, kind: 'package-inspect', receipt_state: 'terminal', state: 'succeeded', phase: 'completed', progress_percent: 100, error: null, host_result: { package_result: { package: 'openssl', installed: true, version: '3.0.13', updated: null } } },
    ] }) })

    render(tree('royal', api))

    expect(await screen.findByRole('link', { name: 'Скачать диагностику' })).toHaveAttribute('href', `/api/admin/ops/operations/${diagnosticId}/artifact`)
    expect(screen.getByText('openssl · версия 3.0.13')).toBeVisible()
    expect(screen.getByText('Последние операции')).toBeVisible()
  })

  it('enables context-bound host actions and sends the exact selected package confirmation', async () => {
    const allAvailable = {
      ...capabilities,
      operations: Object.fromEntries(kinds.map(kind => [kind, { available: true, unavailable_reason: null }])) as HostCapabilities['operations'],
    }
    const api = client({
      getCapabilities: vi.fn().mockResolvedValue(allAvailable),
      getOperationContext: vi.fn().mockResolvedValue({ ...operationContext, selected_device_uuid: '44444444-4444-4444-8444-444444444444' }),
    })
    render(tree('royal', api))

    const operations = await screen.findByRole('region', { name: 'Управляемые операции' })
    expect(within(operations).getByRole('button', { name: 'Откатить версию' })).toBeEnabled()
    expect(within(operations).getByRole('button', { name: 'Создать резервную копию' })).toBeEnabled()
    expect(within(operations).getByRole('button', { name: 'Восстановить резервную копию' })).toBeEnabled()
    expect(within(operations).getByRole('button', { name: 'Форматировать USB' })).toBeEnabled()

    fireEvent.change(within(operations).getByRole('combobox', { name: 'Системный пакет' }), { target: { value: 'containerd.io' } })
    fireEvent.click(within(operations).getByRole('button', { name: 'Обновить пакет' }))
    const dialog = screen.getByRole('dialog', { name: 'Подтвердить операцию' })
    fireEvent.change(within(dialog).getByLabelText('Введите UPDATE PACKAGE containerd.io'), { target: { value: 'UPDATE PACKAGE containerd.io' } })
    fireEvent.change(within(dialog).getByLabelText('Пароль'), { target: { value: 'secret' } })
    fireEvent.change(within(dialog).getByLabelText('Код TOTP или восстановления'), { target: { value: '123456' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Запустить' }))

    await waitFor(() => expect(api.startOperation).toHaveBeenCalledWith(expect.objectContaining({
      kind: 'package-update', package: 'containerd.io', confirmation: 'UPDATE PACKAGE containerd.io',
    }), 'reauth-token'))
  })

  it('adopts newly available safe context choices after a host refresh', async () => {
    const allAvailable = {
      ...capabilities,
      operations: Object.fromEntries(kinds.map(kind => [kind, { available: true, unavailable_reason: null }])) as HostCapabilities['operations'],
    }
    const emptyContext: HostOperationContext = {
      ...operationContext,
      selected_device_uuid: null,
      packages: [], services: [], devices: [], backups: [],
    }
    const refreshedContext = {
      ...operationContext,
      selected_device_uuid: operationContext.devices[0].device_uuid,
    }
    const getOperationContext = vi.fn()
      .mockResolvedValueOnce(emptyContext)
      .mockResolvedValue(refreshedContext)
    const api = client({
      getCapabilities: vi.fn().mockResolvedValue(allAvailable),
      getOperationContext,
    })
    render(tree('royal', api))

    expect(await screen.findByRole('button', { name: 'Проверить резервную копию' })).toBeDisabled()
    fireEvent(window, new Event('focus'))

    await waitFor(() => expect(getOperationContext).toHaveBeenCalledTimes(2))
    expect(await screen.findByRole('combobox', { name: 'Резервная копия' })).toHaveValue(operationContext.backups[0].backup_id)
    expect(screen.getByRole('combobox', { name: 'Системный пакет' })).toHaveValue(operationContext.packages[0])
    expect(screen.getByRole('combobox', { name: 'Сервис' })).toHaveValue(operationContext.services[0])
    expect(screen.getByRole('combobox', { name: 'Обнаруженное USB-устройство' })).toHaveValue(operationContext.devices[0].device_uuid)
    expect(screen.getByRole('button', { name: 'Проверить резервную копию' })).toBeEnabled()
    expect(screen.getByRole('button', { name: 'Восстановить резервную копию' })).toBeEnabled()
  })

  it('preserves valid host choices and safely replaces choices removed by a refresh', async () => {
    const secondDevice = '66666666-6666-4666-8666-666666666666'
    const secondBackup = '77777777-7777-4777-8777-777777777777'
    const expandedContext: HostOperationContext = {
      ...operationContext,
      selected_device_uuid: operationContext.devices[0].device_uuid,
      packages: ['docker-ce', 'openssl'],
      services: ['robopark.service', 'docker.service'],
      devices: [...operationContext.devices, { device_uuid: secondDevice, removable: true, mounted: false }],
      backups: [...operationContext.backups, { backup_id: secondBackup, bytes: 4096, verified: false, created_at: null }],
    }
    const narrowedContext: HostOperationContext = {
      ...expandedContext,
      packages: ['docker-ce'], services: ['robopark.service'],
      devices: operationContext.devices, backups: operationContext.backups,
    }
    const getOperationContext = vi.fn()
      .mockResolvedValueOnce(expandedContext)
      .mockResolvedValueOnce(expandedContext)
      .mockResolvedValue(narrowedContext)
    render(tree('royal', client({ getOperationContext })))

    const packageSelect = await screen.findByRole('combobox', { name: 'Системный пакет' })
    const serviceSelect = screen.getByRole('combobox', { name: 'Сервис' })
    const deviceSelect = screen.getByRole('combobox', { name: 'Обнаруженное USB-устройство' })
    const backupSelect = screen.getByRole('combobox', { name: 'Резервная копия' })
    fireEvent.change(packageSelect, { target: { value: 'openssl' } })
    fireEvent.change(serviceSelect, { target: { value: 'docker.service' } })
    fireEvent.change(deviceSelect, { target: { value: secondDevice } })
    fireEvent.change(backupSelect, { target: { value: secondBackup } })

    fireEvent(window, new Event('focus'))
    await waitFor(() => expect(getOperationContext).toHaveBeenCalledTimes(2))
    expect(packageSelect).toHaveValue('openssl')
    expect(serviceSelect).toHaveValue('docker.service')
    expect(deviceSelect).toHaveValue(secondDevice)
    expect(backupSelect).toHaveValue(secondBackup)

    fireEvent(window, new Event('focus'))
    await waitFor(() => expect(getOperationContext).toHaveBeenCalledTimes(3))
    await waitFor(() => {
      expect(packageSelect).toHaveValue('docker-ce')
      expect(serviceSelect).toHaveValue('robopark.service')
      expect(deviceSelect).toHaveValue(operationContext.devices[0].device_uuid)
      expect(backupSelect).toHaveValue(operationContext.backups[0].backup_id)
    })
  })

  it.each([
    ['Откатить версию', 'ROLLBACK ROBOPARK', { kind: 'rollback', release: '0.2.0-rc.10' }],
    ['Перезапустить сервис', 'RESTART SERVICE robopark.service', { kind: 'service-restart', service: 'robopark.service' }],
    ['Перезагрузить хост', 'REBOOT ROBOPARK', { kind: 'reboot' }],
    ['Создать резервную копию', 'BACKUP ROBOPARK', { kind: 'backup', device_uuid: '44444444-4444-4444-8444-444444444444' }],
    ['Проверить резервную копию', 'ЗАПУСТИТЬ BACKUP-VERIFY', { kind: 'backup-verify', backup_id: '55555555-5555-4555-8555-555555555555' }],
    ['Восстановить резервную копию', 'RESTORE ROBOPARK BACKUP', { kind: 'backup-restore', backup_id: '55555555-5555-4555-8555-555555555555' }],
    ['Форматировать USB', 'FORMAT USB 44444444-4444-4444-8444-444444444444', { kind: 'usb-format', device_uuid: '44444444-4444-4444-8444-444444444444', confirmation_repeat: 'FORMAT USB 44444444-4444-4444-8444-444444444444' }],
  ])('submits %s only with its bounded host context', async (buttonLabel, phrase, expected) => {
    const allAvailable = {
      ...capabilities,
      operations: Object.fromEntries(kinds.map(kind => [kind, { available: true, unavailable_reason: null }])) as HostCapabilities['operations'],
    }
    const api = client({
      getCapabilities: vi.fn().mockResolvedValue(allAvailable),
      getOperationContext: vi.fn().mockResolvedValue({ ...operationContext, selected_device_uuid: '44444444-4444-4444-8444-444444444444' }),
    })
    render(tree('royal', api))

    fireEvent.click(await screen.findByRole('button', { name: buttonLabel }))
    const dialog = screen.getByRole('dialog', { name: 'Подтвердить операцию' })
    fireEvent.change(within(dialog).getByLabelText(`Введите ${phrase}`), { target: { value: phrase } })
    fireEvent.change(within(dialog).getByLabelText('Пароль'), { target: { value: 'secret' } })
    fireEvent.change(within(dialog).getByLabelText('Код TOTP или восстановления'), { target: { value: '123456' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Запустить' }))

    await waitFor(() => expect(api.startOperation).toHaveBeenCalledWith(expect.objectContaining({
      ...expected, confirmation: phrase,
    }), 'reauth-token'))
  })

  it('fails closed when a previously ready capability snapshot has expired', async () => {
    const api = client({
      getCapabilities: vi.fn().mockResolvedValue({
        ...capabilities, expires_at: '2020-01-01T00:00:00Z',
      }),
    })
    render(tree('royal', api))
    expect(await screen.findByRole('button', { name: 'Собрать диагностику' })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: 'Собрать диагностику' }))
    expect(api.reauthorize).not.toHaveBeenCalled()
  })

  it('disables context-bound actions as soon as the safe host context expires', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-09-30T12:00:00Z'))
    const allAvailable = {
      ...capabilities,
      operations: Object.fromEntries(kinds.map(kind => [kind, { available: true, unavailable_reason: null }])) as HostCapabilities['operations'],
    }
    render(tree('royal', client({
      getCapabilities: vi.fn().mockResolvedValue(allAvailable),
      getOperationContext: vi.fn().mockResolvedValue({ ...operationContext, expires_at: '2026-09-30T12:00:01Z' }),
    })))
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    expect(screen.getByRole('button', { name: 'Откатить версию' })).toBeEnabled()
    expect(screen.getByRole('button', { name: 'Собрать диагностику' })).toBeEnabled()

    await act(async () => { await vi.advanceTimersByTimeAsync(1_001) })

    expect(screen.getByRole('button', { name: 'Откатить версию' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Собрать диагностику' })).toBeEnabled()
    expect(screen.getByText('Безопасный контекст хоста устарел. Обновите страницу перед запуском операции.')).toBeVisible()
  })

  it('requires exact typed confirmation and password plus TOTP or recovery code, binding both calls to the revision', async () => {
    const api = client()
    render(tree('royal', api))
    fireEvent.click(await screen.findByRole('button', { name: 'Собрать диагностику' }))
    const dialog = screen.getByRole('dialog', { name: 'Подтвердить операцию' })
    const submit = within(dialog).getByRole('button', { name: 'Запустить' })
    fireEvent.change(within(dialog).getByLabelText('Введите ЗАПУСТИТЬ DIAGNOSTICS'), { target: { value: 'ЗАПУСТИТЬ DIAGNOSTICS ' } })
    fireEvent.change(within(dialog).getByLabelText('Пароль'), { target: { value: 'secret' } })
    fireEvent.change(within(dialog).getByLabelText('Код TOTP или восстановления'), { target: { value: '123456' } })
    expect(submit).toBeDisabled()
    fireEvent.change(within(dialog).getByLabelText('Введите ЗАПУСТИТЬ DIAGNOSTICS'), { target: { value: 'ЗАПУСТИТЬ DIAGNOSTICS' } })
    fireEvent.click(submit)
    await waitFor(() => expect(api.startOperation).toHaveBeenCalledOnce())
    const reauth = vi.mocked(api.reauthorize).mock.calls[0][0]
    const operation = vi.mocked(api.startOperation).mock.calls[0]
    expect(reauth).toMatchObject({ operation_kind: 'diagnostics', capability_revision: revision, password: 'secret', code: '123456' })
    expect(reauth.operation_id).toMatch(/^[a-f0-9-]{36}$/)
    expect(operation[0]).toEqual({ operation_id: reauth.operation_id, kind: 'diagnostics', capability_revision: revision, confirmation: 'ЗАПУСТИТЬ DIAGNOSTICS' })
    expect(operation[1]).toBe('reauth-token')
    expect(readOperationReservation()?.id).toBe(reauth.operation_id)
  })

  it('keeps a newly accepted operation when an older refresh without a reservation finishes later', async () => {
    let finishStaleRead!: (value: { items: SystemJob[] }) => void
    const staleRead = new Promise<{ items: SystemJob[] }>(resolve => { finishStaleRead = resolve })
    const getOperations = vi.fn()
      .mockResolvedValueOnce({ items: [] })
      .mockReturnValueOnce(staleRead)
    const api = client({ getOperations })
    render(tree('royal', api))

    const start = await screen.findByRole('button', { name: 'Собрать диагностику' })
    fireEvent(window, new Event('focus'))
    await waitFor(() => expect(getOperations).toHaveBeenCalledTimes(2))

    fireEvent.click(start)
    const dialog = screen.getByRole('dialog', { name: 'Подтвердить операцию' })
    fireEvent.change(within(dialog).getByLabelText('Введите ЗАПУСТИТЬ DIAGNOSTICS'), { target: { value: 'ЗАПУСТИТЬ DIAGNOSTICS' } })
    fireEvent.change(within(dialog).getByLabelText('Пароль'), { target: { value: 'secret' } })
    fireEvent.change(within(dialog).getByLabelText('Код TOTP или восстановления'), { target: { value: '123456' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Запустить' }))

    expect(await screen.findByRole('progressbar', { name: 'Прогресс операции' })).toHaveAttribute('value', '0')
    await act(async () => finishStaleRead({ items: [] }))
    expect(screen.getByRole('progressbar', { name: 'Прогресс операции' })).toHaveAttribute('value', '0')
    expect(start).toBeDisabled()
  })

  it('refreshes capabilities and never submits after revision drift', async () => {
    const api = client({ reauthorize: vi.fn().mockRejectedValue(new ApiError(409, 'capabilities_changed')) })
    render(tree('royal', api))
    fireEvent.click(await screen.findByRole('button', { name: 'Собрать диагностику' }))
    const dialog = screen.getByRole('dialog', { name: 'Подтвердить операцию' })
    fireEvent.change(within(dialog).getByLabelText('Введите ЗАПУСТИТЬ DIAGNOSTICS'), { target: { value: 'ЗАПУСТИТЬ DIAGNOSTICS' } })
    fireEvent.change(within(dialog).getByLabelText('Пароль'), { target: { value: 'secret' } })
    fireEvent.change(within(dialog).getByLabelText('Код TOTP или восстановления'), { target: { value: '123456' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Запустить' }))
    expect(await within(dialog).findByText('Возможности хоста изменились. Список обновлён; подтвердите операцию заново.')).toBeVisible()
    expect(api.getCapabilities).toHaveBeenCalledTimes(2)
    expect(api.startOperation).not.toHaveBeenCalled()
    expect(within(dialog).getByRole('button', { name: 'Запустить' })).toBeDisabled()
  })

  it('closes and clears an open confirmation when a background refresh changes the revision', async () => {
    const revisionB = 'b'.repeat(64)
    const getCapabilities = vi.fn()
      .mockResolvedValueOnce(capabilities)
      .mockResolvedValue({ ...capabilities, revision: revisionB })
    const api = client({ getCapabilities })
    render(tree('royal', api))
    fireEvent.click(await screen.findByRole('button', { name: 'Собрать диагностику' }))
    const dialog = screen.getByRole('dialog', { name: 'Подтвердить операцию' })
    fireEvent.change(within(dialog).getByLabelText('Введите ЗАПУСТИТЬ DIAGNOSTICS'), { target: { value: 'ЗАПУСТИТЬ DIAGNOSTICS' } })
    fireEvent.change(within(dialog).getByLabelText('Пароль'), { target: { value: 'secret' } })
    fireEvent.change(within(dialog).getByLabelText('Код TOTP или восстановления'), { target: { value: '123456' } })

    fireEvent(window, new Event('focus'))
    await waitFor(() => expect(getCapabilities).toHaveBeenCalledTimes(2))
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Подтвердить операцию' })).not.toBeInTheDocument())
    expect(api.reauthorize).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: 'Собрать диагностику' }))
    const reopened = screen.getByRole('dialog', { name: 'Подтвердить операцию' })
    expect(within(reopened).getByLabelText('Введите ЗАПУСТИТЬ DIAGNOSTICS')).toHaveValue('')
    expect(within(reopened).getByLabelText('Пароль')).toHaveValue('')
    expect(within(reopened).getByLabelText('Код TOTP или восстановления')).toHaveValue('')
  })

  it.each([
    ['lost response', new Error('response lost')],
    ['dispatch conflict', new ApiError(409, 'host_work_in_progress')],
  ])('persists before POST and reconciles the exact UUID after %s', async (_label, failure) => {
    let submittedId = ''
    const startOperation = vi.fn().mockImplementation(async payload => {
      submittedId = payload.operation_id
      expect(readOperationReservation()?.id).toBe(submittedId)
      throw failure
    })
    const api = client({ startOperation })
    const first = render(tree('royal', api))
    fireEvent.click(await screen.findByRole('button', { name: 'Собрать диагностику' }))
    const dialog = screen.getByRole('dialog', { name: 'Подтвердить операцию' })
    fireEvent.change(within(dialog).getByLabelText('Введите ЗАПУСТИТЬ DIAGNOSTICS'), { target: { value: 'ЗАПУСТИТЬ DIAGNOSTICS' } })
    fireEvent.change(within(dialog).getByLabelText('Пароль'), { target: { value: 'secret' } })
    fireEvent.change(within(dialog).getByLabelText('Код TOTP или восстановления'), { target: { value: '123456' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Запустить' }))
    await waitFor(() => expect(startOperation).toHaveBeenCalledOnce())
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Подтвердить операцию' })).not.toBeInTheDocument())
    expect(readOperationReservation()?.id).toBe(submittedId)
    first.unmount()

    const resumed = client({
      getOperation: vi.fn().mockResolvedValue({ id: submittedId, kind: 'diagnostics', state: 'running', phase: 'executing', progress_percent: 25, error: null }),
      startOperation,
    })
    render(tree('royal', resumed))
    expect(await screen.findByRole('progressbar', { name: 'Прогресс операции' })).toHaveAttribute('value', '25')
    expect(screen.getByRole('button', { name: 'Собрать диагностику' })).toBeDisabled()
    expect(startOperation).toHaveBeenCalledOnce()
  })

  it('does not reconcile a reserved UUID while its POST is still unresolved', async () => {
    let release: ((value: SystemJob) => void) | undefined
    const getOperation = vi.fn().mockRejectedValue(new ApiError(404, 'operation_not_found'))
    const startOperation = vi.fn().mockImplementation(() => new Promise<SystemJob>(resolve => { release = resolve }))
    const api = client({ getOperation, startOperation })
    render(tree('royal', api))
    fireEvent.click(await screen.findByRole('button', { name: 'Собрать диагностику' }))
    const dialog = screen.getByRole('dialog', { name: 'Подтвердить операцию' })
    fireEvent.change(within(dialog).getByLabelText('Введите ЗАПУСТИТЬ DIAGNOSTICS'), { target: { value: 'ЗАПУСТИТЬ DIAGNOSTICS' } })
    fireEvent.change(within(dialog).getByLabelText('Пароль'), { target: { value: 'secret' } })
    fireEvent.change(within(dialog).getByLabelText('Код TOTP или восстановления'), { target: { value: '123456' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Запустить' }))
    await waitFor(() => expect(startOperation).toHaveBeenCalledOnce())

    fireEvent(window, new Event('focus'))
    await act(async () => { await Promise.resolve() })
    expect(getOperation).not.toHaveBeenCalled()
    expect(readOperationReservation()).not.toBeNull()

    release?.({ id: 'accepted', kind: 'diagnostics', state: 'running', phase: 'awaiting_host', progress_percent: 0, error: null })
  })

  it('persists the exact safe draft before a slow reauthorization and never starts a second UUID', async () => {
    let release: ((value: { token: string; expires_in: number }) => void) | undefined
    const getOperation = vi.fn().mockRejectedValue(new ApiError(404, 'operation_not_found'))
    const reauthorize = vi.fn().mockImplementation(() => new Promise(resolve => { release = resolve }))
    const api = client({ getOperation, reauthorize })
    render(tree('royal', api))
    fireEvent.click(await screen.findByRole('button', { name: 'Собрать диагностику' }))
    const dialog = screen.getByRole('dialog', { name: 'Подтвердить операцию' })
    fireEvent.change(within(dialog).getByLabelText('Введите ЗАПУСТИТЬ DIAGNOSTICS'), { target: { value: 'ЗАПУСТИТЬ DIAGNOSTICS' } })
    fireEvent.change(within(dialog).getByLabelText('Пароль'), { target: { value: 'secret' } })
    fireEvent.change(within(dialog).getByLabelText('Код TOTP или восстановления'), { target: { value: '123456' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Запустить' }))
    await waitFor(() => expect(reauthorize).toHaveBeenCalledOnce())
    const stored = readOperationReservation()
    expect(stored?.draft).toEqual({
      operation_id: stored?.id, kind: 'diagnostics', capability_revision: revision,
    })
    fireEvent(window, new Event('focus'))
    await act(async () => { await Promise.resolve() })
    expect(getOperation).not.toHaveBeenCalled()
    expect(api.startOperation).not.toHaveBeenCalled()
    release?.({ token: 'reauth-token', expires_in: 120 })
  })

  it('keeps an exact safe draft locked across 404 and retries only the same UUID and payload', async () => {
    let submittedId = ''
    const getOperation = vi.fn().mockRejectedValue(new ApiError(404, 'operation_not_found'))
    const startOperation = vi.fn().mockImplementation(async payload => {
      submittedId = payload.operation_id
      throw new Error('request outcome unknown')
    })
    const api = client({ getOperation, startOperation })
    const first = render(tree('royal', api))
    fireEvent.click(await screen.findByRole('button', { name: 'Собрать диагностику' }))
    const dialog = screen.getByRole('dialog', { name: 'Подтвердить операцию' })
    fireEvent.change(within(dialog).getByLabelText('Введите ЗАПУСТИТЬ DIAGNOSTICS'), { target: { value: 'ЗАПУСТИТЬ DIAGNOSTICS' } })
    fireEvent.change(within(dialog).getByLabelText('Пароль'), { target: { value: 'secret' } })
    fireEvent.change(within(dialog).getByLabelText('Код TOTP или восстановления'), { target: { value: '123456' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Запустить' }))
    expect(await screen.findByText('Проверяем получение запроса')).toBeVisible()
    expect(readOperationReservation()?.id).toBe(submittedId)
    expect(screen.getByRole('button', { name: 'Собрать диагностику' })).toBeDisabled()

    first.unmount()
    render(tree('royal', api))
    expect(await screen.findByText(/Запрос не подтверждён/)).toBeVisible()
    expect(readOperationReservation()?.id).toBe(submittedId)
    expect(readOperationReservation()?.draft).toEqual({
      operation_id: submittedId, kind: 'diagnostics', capability_revision: revision,
    })
    expect(localStorage.getItem(operationReservationKey(reservationActor))).not.toContain('secret')
    expect(localStorage.getItem(operationReservationKey(reservationActor))).not.toContain('123456')
    expect(screen.getByRole('button', { name: 'Собрать диагностику' })).toBeDisabled()

    fireEvent.click(screen.getByRole('button', { name: 'Повторить тот же запрос' }))
    const retry = screen.getByRole('dialog', { name: 'Подтвердить операцию' })
    fireEvent.change(within(retry).getByLabelText('Введите ЗАПУСТИТЬ DIAGNOSTICS'), { target: { value: 'ЗАПУСТИТЬ DIAGNOSTICS' } })
    fireEvent.change(within(retry).getByLabelText('Пароль'), { target: { value: 'new-secret' } })
    fireEvent.change(within(retry).getByLabelText('Код TOTP или восстановления'), { target: { value: '654321' } })
    fireEvent.click(within(retry).getByRole('button', { name: 'Запустить' }))
    await waitFor(() => expect(startOperation).toHaveBeenCalledTimes(2))
    expect(vi.mocked(startOperation).mock.calls[1][0]).toEqual({
      operation_id: submittedId, kind: 'diagnostics', capability_revision: revision,
      confirmation: 'ЗАПУСТИТЬ DIAGNOSTICS',
    })
    expect(vi.mocked(api.reauthorize).mock.calls[1][0]).toMatchObject({
      operation_id: submittedId, operation_kind: 'diagnostics', capability_revision: revision,
      password: 'new-secret', code: '654321',
    })
  })

  it('keeps the unknown UUID reserved when retry reauthorization fails and reuses it after corrected credentials', async () => {
    let submittedId = ''
    const getOperation = vi.fn().mockRejectedValue(new ApiError(404, 'operation_not_found'))
    const startOperation = vi.fn()
      .mockImplementationOnce(async payload => {
        submittedId = payload.operation_id
        throw new Error('request outcome unknown')
      })
      .mockImplementationOnce(async payload => ({
        id: payload.operation_id, kind: payload.kind, state: 'running',
        phase: 'accepted', progress_percent: 0, error: null,
      }))
    const reauthorize = vi.fn()
      .mockResolvedValueOnce({ token: 'initial-token', expires_in: 120 })
      .mockRejectedValueOnce(new ApiError(401, 'invalid_totp'))
      .mockResolvedValueOnce({ token: 'retry-token', expires_in: 120 })
    const api = client({ getOperation, reauthorize, startOperation })
    const first = render(tree('royal', api))

    fireEvent.click(await screen.findByRole('button', { name: 'Собрать диагностику' }))
    let dialog = screen.getByRole('dialog', { name: 'Подтвердить операцию' })
    fireEvent.change(within(dialog).getByLabelText('Введите ЗАПУСТИТЬ DIAGNOSTICS'), { target: { value: 'ЗАПУСТИТЬ DIAGNOSTICS' } })
    fireEvent.change(within(dialog).getByLabelText('Пароль'), { target: { value: 'secret' } })
    fireEvent.change(within(dialog).getByLabelText('Код TOTP или восстановления'), { target: { value: '123456' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Запустить' }))
    expect(await screen.findByText('Проверяем получение запроса')).toBeVisible()
    first.unmount()

    render(tree('royal', api))
    expect(await screen.findByText(/Запрос не подтверждён/)).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: 'Повторить тот же запрос' }))
    dialog = screen.getByRole('dialog', { name: 'Подтвердить операцию' })
    fireEvent.change(within(dialog).getByLabelText('Введите ЗАПУСТИТЬ DIAGNOSTICS'), { target: { value: 'ЗАПУСТИТЬ DIAGNOSTICS' } })
    fireEvent.change(within(dialog).getByLabelText('Пароль'), { target: { value: 'wrong' } })
    fireEvent.change(within(dialog).getByLabelText('Код TOTP или восстановления'), { target: { value: '000000' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Запустить' }))

    expect(await within(dialog).findByText('Операция не запущена. Проверьте пароль и одноразовый код.')).toBeVisible()
    expect(readOperationReservation()?.id).toBe(submittedId)
    expect(readOperationReservation()?.draft).toEqual({
      operation_id: submittedId, kind: 'diagnostics', capability_revision: revision,
    })

    fireEvent.change(within(dialog).getByLabelText('Пароль'), { target: { value: 'secret' } })
    fireEvent.change(within(dialog).getByLabelText('Код TOTP или восстановления'), { target: { value: '654321' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Запустить' }))
    await waitFor(() => expect(startOperation).toHaveBeenCalledTimes(2))

    expect(reauthorize.mock.calls.map(([value]) => value.operation_id)).toEqual([
      submittedId, submittedId, submittedId,
    ])
    expect(startOperation.mock.calls.map(([value]) => value.operation_id)).toEqual([
      submittedId, submittedId,
    ])
  })

  it('disables operations exactly when the capability snapshot expires', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-09-25T09:00:00Z'))
    const api = client({
      getCapabilities: vi.fn().mockResolvedValue({ ...capabilities, expires_at: '2026-09-25T09:00:01Z' }),
    })
    render(tree('royal', api))
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    expect(screen.getByRole('button', { name: 'Собрать диагностику' })).toBeEnabled()
    await act(async () => { await vi.advanceTimersByTimeAsync(1_001) })
    expect(screen.getByRole('button', { name: 'Собрать диагностику' })).toBeDisabled()
    expect(api.getCapabilities).toHaveBeenCalledOnce()
  })

  it('resumes progress by stored operation UUID after reload', async () => {
    writeOperationReservation({ id: '11111111-1111-4111-8111-111111111111', kind: 'diagnostics', created_at: 0, phase: 'reconciling' })
    const api = client({ getOperation: vi.fn().mockResolvedValue({ id: '11111111-1111-4111-8111-111111111111', kind: 'diagnostics', state: 'running', phase: 'executing', progress_percent: 50, error: null }) })
    render(tree('royal', api))
    expect(await screen.findByRole('progressbar', { name: 'Прогресс операции' })).toHaveAttribute('value', '50')
    expect(screen.getByText('11111111-1111-4111-8111-111111111111')).toBeVisible()
  })

  it('shows exact paths and byte counts from a safe cleanup preview', async () => {
    const id = '11111111-1111-4111-8111-111111111111'
    writeOperationReservation({ id, kind: 'cleanup-preview', created_at: 0, phase: 'reconciling' })
    const api = client({ getOperation: vi.fn().mockResolvedValue({
      id, kind: 'cleanup-preview', state: 'succeeded', phase: 'completed', progress_percent: 100, error: null,
      host_result: { cleanup_preview: {
        plan_id: id, blocked: false, total_bytes: 4219,
        planned: [
          { category: 'logs', path: 'update.log', bytes: 123 },
          { category: 'ota_cache', path: `${'a'.repeat(64)}.ota`, bytes: 4096 },
        ],
      } },
    }) })
    render(tree('royal', api))

    expect(await screen.findByText('/var/log/robopark/update.log')).toBeVisible()
    expect(screen.getByText(`/var/lib/robopark/ops/state/ota-packages/${'a'.repeat(64)}.ota`)).toBeVisible()
    expect(screen.getByText(/4\s219 байт/)).toBeVisible()
    expect(screen.getAllByText('123 байт').length).toBeGreaterThan(0)
    expect(screen.getByText(/предпросмотр не удалил файлы/i)).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: 'Выполнить очистку' }))
    const dialog = screen.getByRole('dialog', { name: 'Подтвердить операцию' })
    fireEvent.change(within(dialog).getByLabelText('Введите CLEAN ROBOPARK'), { target: { value: 'CLEAN ROBOPARK' } })
    fireEvent.change(within(dialog).getByLabelText('Пароль'), { target: { value: 'secret' } })
    fireEvent.change(within(dialog).getByLabelText('Код TOTP или восстановления'), { target: { value: '123456' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Запустить' }))
    await waitFor(() => expect(api.startOperation).toHaveBeenCalledOnce())
    expect(api.startOperation).toHaveBeenCalledWith(expect.objectContaining({
      kind: 'cleanup-execute', plan_id: id, confirmation: 'CLEAN ROBOPARK',
    }), expect.anything())
  })

  it('shows owned Docker image candidates without enabling file cleanup', async () => {
    const id = '11111111-1111-4111-8111-111111111111'
    writeOperationReservation({ id, kind: 'docker-image-preview', created_at: 0, phase: 'reconciling' })
    const api = client({ getOperation: vi.fn().mockResolvedValue({
      id, kind: 'docker-image-preview', state: 'succeeded', phase: 'completed', progress_percent: 100, error: null,
      host_result: { docker_image_preview: {
        blocked: false, planned: [{ tag: 'robopark-web:11111111-1111-4111-8111-111111111111', reported_bytes: 4096 }],
        total_reported_bytes: 4096, unverified_tags: 1,
      } },
    }) })
    render(tree('royal', api))

    expect(await screen.findByText('robopark-web:11111111-1111-4111-8111-111111111111')).toBeVisible()
    expect(screen.getAllByText(/4\s096 байт/).length).toBe(2)
    expect(screen.getByText(/непроверенных меток: 1/i)).toBeVisible()
    expect(screen.getByText(/общие слои docker/i)).toBeVisible()
    expect(screen.getByRole('button', { name: 'Выполнить очистку' })).toBeDisabled()
  })

  it('starts exact Docker image cleanup only from a confirmed preview plan', async () => {
    const id = '11111111-1111-4111-8111-111111111111'
    writeOperationReservation({ id, kind: 'docker-image-preview', created_at: 0, phase: 'reconciling' })
    const api = client({ getOperation: vi.fn().mockResolvedValue({
      id, kind: 'docker-image-preview', state: 'succeeded', phase: 'completed', progress_percent: 100, error: null,
      host_result: { docker_image_preview: {
        plan_id: id, blocked: false,
        planned: [{ tag: `robopark-web:${id}`, reported_bytes: 4096 }],
        total_reported_bytes: 4096, unverified_tags: 0,
      } },
    }) })
    render(tree('royal', api))

    const action = await screen.findByRole('button', { name: 'Удалить показанные образы' })
    expect(action).toBeEnabled()
    expect(screen.getByText('Очистка образов Docker')).toBeVisible()
    fireEvent.click(action)
    const dialog = screen.getByRole('dialog', { name: 'Подтвердить операцию' })
    expect(within(dialog).getByText(`robopark-web:${id}`)).toBeVisible()
    expect(within(dialog).getByText(/4\s096 байт по Docker/)).toBeVisible()
    fireEvent.change(within(dialog).getByLabelText('Введите CLEAN ROBOPARK IMAGES'), { target: { value: 'CLEAN ROBOPARK IMAGES' } })
    fireEvent.change(within(dialog).getByLabelText('Пароль'), { target: { value: 'secret' } })
    fireEvent.change(within(dialog).getByLabelText('Код TOTP или восстановления'), { target: { value: '123456' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Запустить' }))

    await waitFor(() => expect(api.startOperation).toHaveBeenCalledOnce())
    expect(api.startOperation).toHaveBeenCalledWith(expect.objectContaining({
      kind: 'docker-image-execute', plan_id: id, confirmation: 'CLEAN ROBOPARK IMAGES',
    }), expect.anything())
  })

  it('asks for a new Docker image preview when the plan changed', async () => {
    const id = '11111111-1111-4111-8111-111111111111'
    writeOperationReservation({ id, kind: 'docker-image-execute', created_at: 0, phase: 'reconciling' })
    const api = client({ getOperation: vi.fn().mockResolvedValue({
      id, kind: 'docker-image-execute', state: 'failed', phase: 'failed', progress_percent: 100,
      error: 'image_plan_changed', host_result: null,
    }) })
    render(tree('royal', api))

    expect(await screen.findByText(/Создайте новый предпросмотр образов Docker/)).toBeVisible()
    expect(screen.queryByText(/Удалено меток образов:/)).not.toBeInTheDocument()
  })

  it('confirms one exact private BuildKit record from a fresh preview', async () => {
    const id = '11111111-1111-4111-8111-111111111111'
    writeOperationReservation({ id, kind: 'builder-cache-preview', created_at: 0, phase: 'reconciling' })
    const api = client({ getOperation: vi.fn().mockResolvedValue({
      id, kind: 'builder-cache-preview', state: 'succeeded', phase: 'completed', progress_percent: 100, error: null,
      host_result: { builder_cache_preview: {
        plan_id: id, blocked: false, planned: [{ id: 'private', reported_bytes: 8192 }],
        total_reported_bytes: 8192, other_candidates: 2,
      } },
    }) })
    render(tree('royal', api))

    const action = await screen.findByRole('button', { name: 'Очистить показанную запись BuildKit' })
    expect(action).toBeEnabled()
    expect(screen.getByText(/ещё кандидатов: 2/i)).toBeVisible()
    fireEvent.click(action)
    const dialog = screen.getByRole('dialog', { name: 'Подтвердить операцию' })
    expect(within(dialog).getByText('private')).toBeVisible()
    expect(within(dialog).getByText(/8\s192 байт/)).toBeVisible()
    fireEvent.change(within(dialog).getByLabelText('Введите CLEAN ROBOPARK BUILD CACHE'), { target: { value: 'CLEAN ROBOPARK BUILD CACHE' } })
    fireEvent.change(within(dialog).getByLabelText('Пароль'), { target: { value: 'secret' } })
    fireEvent.change(within(dialog).getByLabelText('Код TOTP или восстановления'), { target: { value: '123456' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Запустить' }))

    await waitFor(() => expect(api.startOperation).toHaveBeenCalledWith(expect.objectContaining({
      kind: 'builder-cache-execute', plan_id: id, confirmation: 'CLEAN ROBOPARK BUILD CACHE',
    }), expect.anything()))
  })

  it('does not claim BuildKit space was freed after an uncertain prune', async () => {
    const id = '11111111-1111-4111-8111-111111111111'
    writeOperationReservation({ id, kind: 'builder-cache-execute', created_at: 0, phase: 'reconciling' })
    const api = client({ getOperation: vi.fn().mockResolvedValue({
      id, kind: 'builder-cache-execute', state: 'failed', phase: 'failed', progress_percent: 100,
      error: 'builder_cleanup_partial', host_result: { builder_cache_result: {
        deleted: [], deleted_count: 0, uncertain_target: { id: 'private', reported_bytes: 8192 },
      } },
    }) })
    render(tree('royal', api))

    expect((await screen.findAllByText(/состояние записи BuildKit требует проверки/i)).length).toBe(2)
    expect(screen.getByText(/создайте новый предпросмотр/i)).toBeVisible()
    expect(screen.queryByText(/освобождено 8\s192 байт/i)).not.toBeInTheDocument()
  })

  it('lets the owner preview OTA cache independently of unrelated backup and release roots', async () => {
    const api = client()
    render(tree('royal', api))
    const operations = await screen.findByRole('region', { name: 'Управляемые операции' })
    expect(within(operations).getByRole('checkbox', { name: 'Кэш завершённых OTA' })).toBeChecked()
    expect(within(operations).getByRole('checkbox', { name: 'Резервные копии' })).not.toBeChecked()
    expect(within(operations).getByRole('checkbox', { name: 'Релизы' })).not.toBeChecked()
    fireEvent.click(within(operations).getByRole('checkbox', { name: 'Диагностика' }))
    fireEvent.click(within(operations).getByRole('checkbox', { name: 'Журналы Robopark' }))
    fireEvent.click(within(operations).getByRole('button', { name: 'Предпросмотр очистки' }))
    const dialog = screen.getByRole('dialog', { name: 'Подтвердить операцию' })
    fireEvent.change(within(dialog).getByLabelText('Введите ЗАПУСТИТЬ CLEANUP-PREVIEW'), { target: { value: 'ЗАПУСТИТЬ CLEANUP-PREVIEW' } })
    fireEvent.change(within(dialog).getByLabelText('Пароль'), { target: { value: 'secret' } })
    fireEvent.change(within(dialog).getByLabelText('Код TOTP или восстановления'), { target: { value: '123456' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Запустить' }))

    await waitFor(() => expect(api.startOperation).toHaveBeenCalledOnce())
    expect(api.startOperation).toHaveBeenCalledWith(expect.objectContaining({
      kind: 'cleanup-preview', categories: ['ota_cache'],
    }), expect.anything())
  })

  it('shows confirmed deletions and an uncertain target after partial cleanup', async () => {
    const id = '11111111-1111-4111-8111-111111111111'
    writeOperationReservation({ id, kind: 'cleanup-execute', created_at: 0, phase: 'reconciling' })
    const api = client({ getOperation: vi.fn().mockResolvedValue({
      id, kind: 'cleanup-execute', state: 'failed', phase: 'completed', progress_percent: 100,
      error: 'cleanup_partial', host_result: { cleanup_result: {
        deleted: [{ category: 'diagnostics', path: 'first.log', bytes: 4096 }],
        deleted_count: 1,
        uncertain_target: { category: 'ota_cache', path: `${'a'.repeat(64)}.ota`, bytes: 8192 },
      } },
    }) })
    render(tree('royal', api))

    expect(await screen.findByText(/Очистка прервана\. Удалённые файлы: 1\./)).toBeVisible()
    expect(screen.getByText('Ошибка выполнения')).toBeVisible()
    expect(screen.getByText('/var/lib/robopark/diagnostics/first.log')).toBeVisible()
    expect(screen.getByText(`/var/lib/robopark/ops/state/ota-packages/${'a'.repeat(64)}.ota`)).toBeVisible()
    expect(screen.getByText(/Состояние последней цели требует проверки/)).toBeVisible()
  })

  it('does not query or adopt an operation without an exact locally stored UUID', async () => {
    const api = client()
    render(tree('royal', api))
    await screen.findByRole('region', { name: 'Управляемые операции' })
    expect(screen.queryByRole('progressbar', { name: 'Прогресс операции' })).not.toBeInTheDocument()
    expect(localStorage.getItem(operationReservationKey(reservationActor))).toBeNull()
    expect(api.getOperation).not.toHaveBeenCalled()
  })

  it('requires an exact sanitized discovered USB UUID before enabling selection', async () => {
    writeOperationReservation({ id: '33333333-3333-4333-8333-333333333333', kind: 'usb-discover', created_at: 0, phase: 'reconciling' })
    const api = client({ getOperation: vi.fn().mockResolvedValue({
      id: '33333333-3333-4333-8333-333333333333', kind: 'usb-discover', state: 'succeeded', phase: 'completed', progress_percent: 100, error: null,
      host_result: { devices: [{ device_uuid: '44444444-4444-4444-8444-444444444444', removable: true, mounted: false }] },
    }) })
    render(tree('royal', api))
    const selectButton = await screen.findByRole('button', { name: 'Выбрать USB' })
    expect(selectButton).toBeDisabled()
    fireEvent.change(screen.getByRole('combobox', { name: 'Обнаруженное USB-устройство' }), { target: { value: '44444444-4444-4444-8444-444444444444' } })
    expect(selectButton).toBeEnabled()
    fireEvent.click(selectButton)
    const dialog = screen.getByRole('dialog', { name: 'Подтвердить операцию' })
    fireEvent.change(within(dialog).getByLabelText('Введите ЗАПУСТИТЬ USB-SELECT'), { target: { value: 'ЗАПУСТИТЬ USB-SELECT' } })
    fireEvent.change(within(dialog).getByLabelText('Пароль'), { target: { value: 'secret' } })
    fireEvent.change(within(dialog).getByLabelText('Код TOTP или восстановления'), { target: { value: '123456' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Запустить' }))
    await waitFor(() => expect(api.startOperation).toHaveBeenCalledOnce())
    expect(vi.mocked(api.startOperation).mock.calls[0][0]).toMatchObject({
      kind: 'usb-select', device_uuid: '44444444-4444-4444-8444-444444444444', confirmation: 'ЗАПУСТИТЬ USB-SELECT',
    })
  })

  it('labels partial telemetry as unknown and exposes a textual seven-day history', async () => {
    const partial: SystemSummary = {
      ...summary,
      worker_health: 'worker_heartbeat_missing',
      sync: { ...summary.sync, last_error: null, worker_lease_state: 'unknown' },
      metrics: { host: {} },
    }
    render(tree('admin', client({
      getSummary: vi.fn().mockResolvedValue(partial),
      getHistory: vi.fn().mockResolvedValue({ active_users: [{ date: '2026-09-24', users: 6 }], metrics: [] }),
    })))
    const table = await screen.findByRole('table', { name: 'Активные пользователи за 7 дней — значения' })
    expect(within(table).getByText('24.09.2026')).toBeVisible()
    expect(within(table).getByText('6')).toBeVisible()
    expect(screen.queryByText('Резервная копия: проверена')).not.toBeInTheDocument()
    expect(screen.queryByText('Очистка: без ошибок')).not.toBeInTheDocument()
    expect(screen.getByText('Резервная копия: Неизвестно')).toBeVisible()
    expect(screen.getByText('Очистка: Неизвестно')).toBeVisible()
    expect(screen.getByText('Heartbeat worker не зарегистрирован. Проверьте запуск worker и его журнал.')).toBeVisible()
    expect(screen.getByText('Снимок хоста отсутствует. Проверьте сбор метрик и связь API с host bridge.')).toBeVisible()
    expect(screen.getByText('Heartbeat отсутствует')).toBeVisible()
    for (const label of ['Нагрузка CPU · 1 мин', 'Память', 'Диск', 'Tracker', 'Хранилище']) {
      expect(within(screen.getByText(label).closest('dl')!).getByText('Снимок отсутствует')).toBeVisible()
    }
  })

  it('shows an unconfigured Tracker instead of claiming it works on a clean install', async () => {
    const fresh = {
      ...summary,
      tracker: { state: 'not_configured' },
      sync: { ...summary.sync, cursor_age_seconds: null, last_success_at: null, last_error: null },
      metrics: { host: { requests: { tracker: { requests: 0, errors: 0 } } } },
    } as SystemSummary
    render(tree('royal', client({ getSummary: vi.fn().mockResolvedValue(fresh) })))
    const queues = await screen.findByRole('region', { name: 'Очереди и интеграции' })
    const trackerCard = within(queues).getByText('Tracker').closest('dl')!
    expect(within(trackerCard).getByText('Не настроен')).toBeVisible()
    expect(within(trackerCard).queryByText('Работает')).not.toBeInTheDocument()
  })

  it('identifies an expired worker heartbeat separately from a missing one', async () => {
    render(tree('admin', client({
      getSummary: vi.fn().mockResolvedValue({
        ...summary, worker_health: 'worker_heartbeat_stale', sync: { ...summary.sync, worker_lease_state: 'stale' },
      }),
    })))

    expect(await screen.findByText('Heartbeat worker просрочен. Проверьте журнал worker и подключение к базе данных.')).toBeVisible()
    expect(screen.getByText('Heartbeat просрочен')).toBeVisible()
  })

  it('shows a stale worker metric as an error even while its heartbeat is active', async () => {
    render(tree('admin', client({
      getSummary: vi.fn().mockResolvedValue({
        ...summary, worker_health: 'worker_metric_stale',
      }),
    })))

    expect(await screen.findByText('Метрика worker просрочена. Проверьте сбор метрик и журнал worker.')).toBeVisible()
    const queues = screen.getByRole('region', { name: 'Очереди и интеграции' })
    expect(within(queues).getByText('Метрика просрочена')).toBeVisible()
    expect(within(queues).queryByText('Работает')).not.toBeInTheDocument()
  })

  it('shows a worker metric far ahead of server time as a clock error', async () => {
    render(tree('admin', client({
      getSummary: vi.fn().mockResolvedValue({
        ...summary, worker_health: 'worker_metric_future', metrics_stale: true,
      }),
    })))

    expect(await screen.findByText('Время метрики worker опережает сервер. Проверьте часы хоста и сбор метрик.')).toBeVisible()
    const queues = screen.getByRole('region', { name: 'Очереди и интеграции' })
    expect(within(queues).getByText('Ошибка времени метрики')).toBeVisible()
    expect(within(queues).queryByText('Работает')).not.toBeInTheDocument()
  })

  it('shows a degraded Linux Wi-Fi link from the host bridge', async () => {
    render(tree('admin', client({
      getSummary: vi.fn().mockResolvedValue({
        ...summary,
        metrics: { host: { ...summary.metrics?.host, wifi: { state: 'degraded' } } },
      }),
    })))

    const wifi = await screen.findByText('Wi‑Fi')
    expect(within(wifi.closest('dl')!).getByText('Требует внимания')).toBeVisible()
  })

  it('reports missing host service checks even when a resource snapshot exists', async () => {
    render(tree('admin', client({
      getSummary: vi.fn().mockResolvedValue({
        ...summary,
        metrics: { host: {
          disk: { total_bytes: 1000, free_bytes: 400 },
          memory: { total_bytes: 1000, available_bytes: 500 },
          cpu: { load_1m: 0.2, cores: 4 },
          postgresql: { state: 'ok' },
        } },
      }),
    })))

    expect(await screen.findByText('Проверки служб хоста отсутствуют или устарели. Проверьте host-health и сбор метрик.')).toBeVisible()
    expect(screen.getAllByText('Проверка не получена').length).toBeGreaterThanOrEqual(3)
  })

  it('pauses polling while hidden and resumes once without duplicate timers', async () => {
    vi.useFakeTimers()
    const api = client()
    render(tree('admin', api))
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    const initial = vi.mocked(api.getSummary).mock.calls.length
    const hidden = vi.spyOn(document, 'hidden', 'get').mockReturnValue(true)
    fireEvent(document, new Event('visibilitychange'))
    await act(async () => { await vi.advanceTimersByTimeAsync(120_000) })
    expect(api.getSummary).toHaveBeenCalledTimes(initial)
    hidden.mockReturnValue(false)
    fireEvent(document, new Event('visibilitychange'))
    fireEvent(window, new Event('focus'))
    fireEvent(window, new Event('focus'))
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    expect(api.getSummary).toHaveBeenCalledTimes(initial + 1)
    await act(async () => { await vi.advanceTimersByTimeAsync(30_000) })
    expect(api.getSummary).toHaveBeenCalledTimes(initial + 2)
  })

  it('refreshes seven-day history on entry and focus without fetching it on every operational poll', async () => {
    vi.useFakeTimers()
    const api = client()
    render(tree('admin', api))
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    expect(api.getHistory).toHaveBeenCalledTimes(1)
    await act(async () => { await vi.advanceTimersByTimeAsync(90_000) })
    expect(api.getSummary).toHaveBeenCalledTimes(4)
    expect(api.getHistory).toHaveBeenCalledTimes(1)
    fireEvent(window, new Event('focus'))
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    expect(api.getHistory).toHaveBeenCalledTimes(2)
  })

  it('retries failed history on the next operational poll', async () => {
    vi.useFakeTimers()
    const getHistory = vi.fn()
      .mockRejectedValueOnce(new ApiError(503, 'history_database_unavailable'))
      .mockResolvedValue({ active_users: [{ date: '2026-09-23', users: 5 }, { date: '2026-09-24', users: 6 }], metrics: [] })
    render(tree('admin', client({ getHistory })))
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    expect(screen.getByText('История активности недоступна')).toBeVisible()
    await act(async () => { await vi.advanceTimersByTimeAsync(30_000) })
    expect(getHistory).toHaveBeenCalledTimes(2)
    expect(screen.getByRole('img', { name: 'Активные пользователи за 7 дней' })).toBeVisible()
  })

  it('hides prior account telemetry immediately on account switch', async () => {
    let resolveSecond!: (value: SystemSummary) => void
    const second = new Promise<SystemSummary>(resolve => { resolveSecond = resolve })
    const api = client({ getSummary: vi.fn()
      .mockResolvedValueOnce({ ...summary, online: { ...summary.online, total: 17 } })
      .mockReturnValueOnce(second) })
    const scoped = (id: number) => <MemoryRouter><AuthContext.Provider value={{ user: { ...user('admin'), id }, loading: false, login: vi.fn(), logout: vi.fn(), refreshUser: vi.fn() }}><SystemPage client={api} /></AuthContext.Provider></MemoryRouter>
    const view = render(scoped(1))
    expect(await screen.findByText('17')).toBeVisible()
    view.rerender(scoped(2))
    await waitFor(() => expect(api.getSummary).toHaveBeenCalledTimes(2))
    expect(screen.queryByText('17')).not.toBeInTheDocument()
    await act(async () => resolveSecond({ ...summary, online: { ...summary.online, total: 3 } }))
    expect(await screen.findByText('3')).toBeVisible()
  })

  it('ignores a late system response from the previous account', async () => {
    let resolveFirst!: (value: SystemSummary) => void
    const first = new Promise<SystemSummary>(resolve => { resolveFirst = resolve })
    const api = client({ getSummary: vi.fn()
      .mockReturnValueOnce(first)
      .mockResolvedValueOnce({ ...summary, online: { ...summary.online, total: 3 } }) })
    const scoped = (id: number) => <MemoryRouter><AuthContext.Provider value={{ user: { ...user('admin'), id }, loading: false, login: vi.fn(), logout: vi.fn(), refreshUser: vi.fn() }}><SystemPage client={api} /></AuthContext.Provider></MemoryRouter>
    const view = render(scoped(1))
    await waitFor(() => expect(api.getSummary).toHaveBeenCalledTimes(1))
    view.rerender(scoped(2))
    expect(await screen.findByText('3')).toBeVisible()
    await act(async () => resolveFirst({ ...summary, online: { ...summary.online, total: 17 } }))
    expect(screen.queryByText('17')).not.toBeInTheDocument()
    expect(screen.getByText('3')).toBeVisible()
  })

  it('does not show another owners operation after a failed reservation lookup', async () => {
    const firstActor = user('royal')
    const secondActor = { ...firstActor, id: 2, username: 'second-royal' }
    const firstId = '11111111-1111-4111-8111-111111111111'
    const secondId = '22222222-2222-4222-8222-222222222222'
    writeScopedOperationReservation(firstActor, { id: firstId, kind: 'diagnostics', created_at: 0, phase: 'reconciling' })
    writeScopedOperationReservation(secondActor, { id: secondId, kind: 'diagnostics', created_at: 0, phase: 'reconciling' })
    const api = client({ getOperation: vi.fn().mockImplementation(async id => {
      if (id === firstId) return { id, kind: 'diagnostics', state: 'running', phase: 'executing', progress_percent: 50, error: null }
      throw new ApiError(503, 'operation_bridge_unavailable')
    }) })
    const scoped = (actor: User) => <MemoryRouter><AuthContext.Provider value={{ user: actor, loading: false, login: vi.fn(), logout: vi.fn(), refreshUser: vi.fn() }}><SystemPage client={api} /></AuthContext.Provider></MemoryRouter>
    const view = render(scoped(firstActor))
    expect(await screen.findByText(firstId)).toBeVisible()
    view.rerender(scoped(secondActor))
    expect(await screen.findByText(/Не удалось получить состояние операции/)).toBeVisible()
    expect(screen.getByText(secondId)).toBeVisible()
    expect(screen.queryByText(firstId)).not.toBeInTheDocument()
  })

  it.each([401, 403])('removes protected system data when a refresh returns %s', async status => {
    const getSummary = vi.fn()
      .mockResolvedValueOnce(summary)
      .mockRejectedValueOnce(new ApiError(status, 'forbidden'))
    const api = client({ getSummary })
    render(tree('admin', api))
    expect(await screen.findByRole('img', { name: 'Активные пользователи за 7 дней' })).toBeVisible()
    fireEvent(window, new Event('focus'))
    await waitFor(() => expect(getSummary).toHaveBeenCalledTimes(2))
    await waitFor(() => expect(screen.queryByRole('img', { name: 'Активные пользователи за 7 дней' })).not.toBeInTheDocument())
  })
})
