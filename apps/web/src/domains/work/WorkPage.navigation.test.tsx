import { act, render, waitFor } from '@testing-library/react'
import type { ComponentProps } from 'react'
import { BrowserRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ParkScopeContext } from '../../app/park/parkScope'
import { AuthContext } from '../../auth-context'
import type { User } from '../../api'
import type { IssueWorkbench } from './IssueWorkbench'
import { WorkPage } from './WorkPage'

type WorkbenchProps = ComponentProps<typeof IssueWorkbench>
let current: WorkbenchProps
vi.mock('./IssueWorkbench', () => ({ IssueWorkbench: (props: WorkbenchProps) => { current = props; return null } }))

const park = { id: 7, name: 'Север', timezone: 'Europe/Moscow', tag: 'north', tracker_queue: 'ROBOPARK', is_active: true }
const user: User = { id: 3, username: 'mechanic', role: 'mechanic', access_status: 'approved', permissions: ['nav.tasks', 'tracker.read'], parks: [park] }

function openWork() {
  window.history.replaceState({}, '', '/work/ROBOPARK-200?park=7&status=queued&page=2')
  render(<BrowserRouter><AuthContext.Provider value={{ user, loading: false, login: vi.fn(), logout: vi.fn(), refreshUser: vi.fn() }}>
    <ParkScopeContext.Provider value={{ parkId: 7, selectedPark: park, parks: [park], loading: false, locked: false, setParkId: vi.fn(), refreshParks: vi.fn() }}>
      <Routes><Route path="/work/:issueKey" element={<WorkPage />} /></Routes>
    </ParkScopeContext.Provider>
  </AuthContext.Provider></BrowserRouter>)
  return current
}

afterEach(() => { window.history.replaceState({}, '', '/') })

describe('WorkPage navigation ownership', () => {
  it('does not let an old tab callback undo a related-task navigation before React commits', () => {
    const previous = openWork()
    act(() => {
      previous.onOpenRelatedIssue!('ROBOPARK-201')
      previous.onStateChange({ ...previous.state, detailTab: 'check' })
    })
    expect(window.location.pathname).toBe('/work/ROBOPARK-201')
    expect(new URLSearchParams(window.location.search).get('blocker')).toBe('ROBOPARK-200')
  })

  it('restores the previous task and enables its controls after browser Back', async () => {
    const previous = openWork()
    act(() => previous.onOpenRelatedIssue!('ROBOPARK-201'))
    expect(window.location.pathname).toBe('/work/ROBOPARK-201')
    act(() => window.history.back())
    await waitFor(() => expect(current.issueKey).toBe('ROBOPARK-200'))
    expect(window.location.pathname).toBe('/work/ROBOPARK-200')
    expect(Object.fromEntries(new URLSearchParams(window.location.search))).toEqual({ park: '7', status: 'queued', page: '2' })
    act(() => current.onStateChange({ ...current.state, detailTab: 'check' }))
    expect(window.location.pathname).toBe('/work/ROBOPARK-200')
    expect(new URLSearchParams(window.location.search).get('view')).toBe('check')
  })

  it('rejects a retained callback after a route commit while accepting the new task controls', () => {
    const previous = openWork()
    act(() => previous.onOpenRelatedIssue!('ROBOPARK-201'))
    act(() => previous.onStateChange({ ...previous.state, detailTab: 'check' }))
    expect(window.location.pathname).toBe('/work/ROBOPARK-201')
    act(() => current.onStateChange({ ...current.state, detailTab: 'check' }))
    expect(window.location.pathname).toBe('/work/ROBOPARK-201')
    expect(new URLSearchParams(window.location.search).get('view')).toBe('check')
    expect(new URLSearchParams(window.location.search).get('page')).toBe('2')
  })
})
