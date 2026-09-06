import { resourceStore } from '../../lib/resource'
import { render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api, ApiError } from '../../api'
import { AdminOpsPanel } from './AdminOpsPanel'

describe('AdminOpsPanel', () => {
  afterEach(() => { vi.restoreAllMocks(); resourceStore.clearAll() })

  it('describes the robot diagnostics cookie without exposing the Emergency implementation name', () => {
    vi.spyOn(api, 'opsJob').mockRejectedValue(new ApiError(404, 'ops_job_not_found'))

    render(<AdminOpsPanel />)

    expect(screen.getByText(/cookie диагностики робота придётся ввести заново/i)).toBeVisible()
    expect(document.body).not.toHaveTextContent(/Tracker\/Emergency/i)
  })
})
