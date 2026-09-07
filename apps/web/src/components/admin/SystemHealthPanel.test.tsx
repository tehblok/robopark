import { act, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { resourceStore } from '../../lib/resource'
import { SystemHealthPanel } from './SystemHealthPanel'
import { healthFixture, mockOpsServer } from './opsTestFixtures'

describe('SystemHealthPanel', () => {
  beforeEach(() => resourceStore.clearAll())
  afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks() })
  it('shows degraded checks, rollback and backup age with visible text and icons', async () => {
    mockOpsServer()
    render(<SystemHealthPanel onRepair={() => undefined} busy={false} />)
    expect(await screen.findByText('Сервис Tuna')).toBeVisible()
    expect(screen.getByText('Есть проблемы')).toBeVisible()
    expect(screen.getByText('Ошибка')).toBeVisible()
    expect(screen.getByText(/Восстановлена предыдущая версия/)).toBeVisible()
    expect(screen.getByText(/1 ч\. назад/)).toBeVisible()
    expect(screen.getByText(/aaaaaaa/)).toBeVisible()
    expect(document.querySelector('svg')).toBeInTheDocument()
  })
  it('keeps last good health during focus refresh failure without duplicate mount fetch', async () => {
    const fetchMock = mockOpsServer()
    render(<SystemHealthPanel onRepair={() => undefined} busy={false} />)
    await screen.findByText('Сервис Tuna')
    expect(fetchMock).toHaveBeenCalledTimes(1)
    fetchMock.mockRejectedValue(new Error('offline'))
    await act(async () => { fireEvent(window, new Event('focus')) })
    expect(screen.getByText('Сервис Tuna')).toBeVisible()
    expect(await screen.findByText(/Нет связи.*последние/)).toBeVisible()
    expect(screen.queryByRole('button', { name: /обновить/i })).not.toBeInTheDocument()
  })
  it('shows unknown and stale host data without exposing raw messages', async () => {
    mockOpsServer({ '/admin/ops/system-health': { ...healthFixture, generated_at: '2020-01-01T00:00:00Z', checks: [{ code: 'dns', status: 'failed', message: 'Traceback /srv/secret.json {"token":"secret"}' }], last_backup: { status: 'unknown', completed_at: null } } })
    render(<SystemHealthPanel onRepair={() => undefined} busy={false} />)
    expect(await screen.findByText(/Данные устарели/)).toBeVisible()
    expect(document.body).not.toHaveTextContent(/Traceback|secret|\/srv/)
    expect(screen.getByText(/Резервная копия: нет данных/)).toBeVisible()
  })
})
