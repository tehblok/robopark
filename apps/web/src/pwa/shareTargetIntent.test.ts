import { beforeEach, describe, expect, it, vi } from 'vitest'
import { captureShareTargetId, clearLegacyShareTargetNotice, clearPendingShareTargetId, hasLegacyShareTargetNotice, pendingShareTargetId } from './shareTargetIntent'

const ID = '01234567-89ab-4cde-8fab-0123456789ab'

describe('share target intent', () => {
  beforeEach(() => {
    sessionStorage.clear()
    history.replaceState({}, '', '/')
  })

  it('keeps only the launched share in this tab across login navigation', () => {
    history.replaceState({}, '', `/?shared=${ID}&park=7`)
    captureShareTargetId()
    expect(location.pathname + location.search).toBe('/?park=7')
    expect(pendingShareTargetId()).toBe(ID)
    history.replaceState({}, '', '/login')
    expect(pendingShareTargetId()).toBe(ID)
    clearPendingShareTargetId(ID)
    expect(pendingShareTargetId()).toBeNull()
  })

  it('never imports a legacy global share marker as a photo capability', () => {
    history.replaceState({}, '', '/?shared=1')
    captureShareTargetId()
    expect(pendingShareTargetId()).toBeNull()
    expect(hasLegacyShareTargetNotice()).toBe(true)
    expect(location.search).toBe('')
    clearLegacyShareTargetNotice()
    expect(hasLegacyShareTargetNotice()).toBe(false)
  })

  it('keeps both incoming photo intents until each is resolved', () => {
    const second = '11234567-89ab-4cde-8fab-0123456789ab'
    history.replaceState({}, '', `/?shared=${ID}`)
    captureShareTargetId()
    history.replaceState({}, '', `/?shared=${second}`)
    captureShareTargetId()
    expect(pendingShareTargetId()).toBe(ID)
    clearPendingShareTargetId(ID)
    expect(pendingShareTargetId()).toBe(second)
  })

  it('leaves the photo capability in the URL when session storage is unavailable', () => {
    sessionStorage.setItem('robopark:share-target-ids', JSON.stringify(['21234567-89ab-4cde-8fab-0123456789ab']))
    history.replaceState({}, '', `/?shared=${ID}`)
    const spy = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('blocked') })
    try {
      captureShareTargetId()
      expect(location.search).toBe(`?shared=${ID}`)
      expect(pendingShareTargetId()).toBe(ID)
    } finally { spy.mockRestore() }
  })
})
