import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { SystemVersionPanel } from './SystemVersionPanel'
import { releaseStatusFixture } from './opsTestFixtures'

describe('SystemVersionPanel', () => {
  it('shows expired support as information without blocking operations', () => {
    render(<SystemVersionPanel value={{ ...releaseStatusFixture, support_status: 'expired', supported_until: '2026-01-01T00:00:00Z' }} />)
    expect(screen.getByText('Поддержка завершена')).toBeVisible()
    expect(screen.getByText('1.0.0')).toBeVisible()
  })
})
