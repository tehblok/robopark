import { IDBFactory } from 'fake-indexeddb'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { storageRegistry } from './storageRegistry'
import { readReportPhotoDraft, writeReportPhotoDraft } from '../domains/reports/reportPhotoDrafts'

beforeEach(() => {
  vi.stubGlobal('indexedDB', new IDBFactory())
  localStorage.clear()
})

describe('storage registry', () => {
  it('inventories the product namespaces with ownership, schema and retention', () => {
    const entries = storageRegistry.inventory()
    expect(entries.find(entry => entry.name === 'robopark-offline')).toMatchObject({
      kind: 'indexedDB', owner: 'pwa', schema: 2,
    })
    expect(entries.find(entry => entry.name === 'robopark:report-draft:')).toMatchObject({
      kind: 'localStorage', owner: 'reports', scope: 'account-park',
    })
    expect(entries.every(entry => entry.retention && entry.owner && entry.scope)).toBe(true)
  })

  it('retires an exact scope without touching preferences', async () => {
    localStorage.setItem('robopark:report-draft:1:1', 'secret')
    localStorage.setItem('robopark:handoff:v1:1', 'secret')
    localStorage.setItem('robopark-theme', 'dark')

    const result = await storageRegistry.purgeScope({ account: '1', role: 'mechanic', permissions: 'tracker.read', park: '1', schema: 1 })

    expect(result).toBe('retired')
    expect(storageRegistry.isRetired({ account: '1', role: 'mechanic', permissions: 'tracker.read', park: '1', schema: 1 })).toBe(true)
    expect(localStorage.getItem('robopark:report-draft:1:1')).toBeNull()
    expect(localStorage.getItem('robopark:handoff:v1:1')).toBe('secret')
    expect(localStorage.getItem('robopark-theme')).toBe('dark')
  })

  it('quarantines a scoped report draft and restores it only for the same authorization scope', async () => {
    const mechanic = { account: '1', role: 'mechanic', permissions: 'tracker.read', park: '1', schema: 1 }
    const manager = { ...mechanic, role: 'manager' }
    localStorage.setItem('robopark:report-draft:1:1', JSON.stringify({ title: 'unfinished' }))

    await storageRegistry.purgeScope(mechanic)
    expect(localStorage.getItem('robopark:report-draft:1:1')).toBeNull()
    storageRegistry.activateScope(manager)
    expect(localStorage.getItem('robopark:report-draft:1:1')).toBeNull()
    storageRegistry.activateScope(mechanic)
    expect(localStorage.getItem('robopark:report-draft:1:1')).toContain('unfinished')
  })

  it('quarantines an unsubmitted report photo by exact scope and restores its attachment', async () => {
    const scope = { account: '1', principal: 'alice', parkAccess: '1', role: 'mechanic', permissions: 'reports.create', park: '1', schema: 1 }
    const key = 'robopark:report-draft:1:1'
    const ownerKey = JSON.stringify([1, 'alice', null, 'mechanic', 'approved', false, ['reports.create'], [[1, 'North']], [1, 'North']])
    await writeReportPhotoDraft({ key, ownerKey, revision: 'r1', activeForm: 'problem', trackerKey: '', title: 'pending', body: '', createdReportId: 42, attachmentKind: 'device_photo', attachment: { blob: new Blob(['private']), name: 'private.jpg', lastModified: 1 } })
    await storageRegistry.purgeScope(scope)
    expect(await readReportPhotoDraft(key)).toBeNull()
    storageRegistry.activateScope({ ...scope, parkAccess: '2' })
    expect(await readReportPhotoDraft(key)).toBeNull()
    storageRegistry.activateScope(scope)
    expect((await readReportPhotoDraft(key))?.attachment?.name).toBe('private.jpg')
  })

  it('keeps an archived draft when its original key is occupied', async () => {
    const scope = { account: '1', role: 'mechanic', permissions: 'tracker.read', park: '1', schema: 1 }
    localStorage.setItem('robopark:report-draft:1:1', 'old pending')
    await storageRegistry.purgeScope(scope)
    localStorage.setItem('robopark:report-draft:1:1', 'new pending')

    storageRegistry.activateScope(scope)
    expect(localStorage.getItem('robopark:report-draft:1:1')).toBe('new pending')
    localStorage.removeItem('robopark:report-draft:1:1')
    storageRegistry.activateScope(scope)
    expect(localStorage.getItem('robopark:report-draft:1:1')).toBe('old pending')
  })

  it('never overwrites an archived pending draft during repeated retirement', async () => {
    const scope = { account: '1', role: 'mechanic', permissions: 'tracker.read', park: '1', schema: 1 }
    localStorage.setItem('robopark:report-draft:1:1', 'old pending')
    await storageRegistry.purgeScope(scope)
    localStorage.setItem('robopark:report-draft:1:1', 'new pending')

    await storageRegistry.purgeScope(scope)
    localStorage.removeItem('robopark:report-draft:1:1')
    storageRegistry.activateScope(scope)

    expect(localStorage.getItem('robopark:report-draft:1:1')).toBe('old pending')
  })

  it('isolates comment and handoff drafts across role changes for the same principal', async () => {
    const mechanic = { account: '1', principal: 'alice', role: 'mechanic', permissions: 'tracker.read', park: '1', schema: 1 }
    const manager = { ...mechanic, role: 'manager' }
    const handoff = `robopark:handoff:v1:${JSON.stringify(['alice', 'TASK-1'])}`
    const comment = 'robopark:comment-draft:v1:alice:TASK-1'
    localStorage.setItem(handoff, 'pending handoff')
    localStorage.setItem(comment, 'pending comment')

    await storageRegistry.purgeScope(mechanic)
    expect(localStorage.getItem(handoff)).toBeNull()
    expect(localStorage.getItem(comment)).toBeNull()
    storageRegistry.activateScope(manager)
    expect(localStorage.getItem(handoff)).toBeNull()
    storageRegistry.activateScope(mechanic)
    expect(localStorage.getItem(handoff)).toBe('pending handoff')
    expect(localStorage.getItem(comment)).toBe('pending comment')
  })

  it('does not restore a handoff draft after the principal loses park access', async () => {
    const scope = { account: '1', principal: 'alice', role: 'mechanic', permissions: 'tracker.read', park: 'all', parkAccess: '1,2', schema: 1 }
    const handoff = `robopark:handoff:v1:${JSON.stringify(['alice', 'TASK-2'])}`
    localStorage.setItem(handoff, 'pending')
    await storageRegistry.purgeScope(scope)

    storageRegistry.activateScope({ ...scope, parkAccess: '1' })

    expect(localStorage.getItem(handoff)).toBeNull()
    storageRegistry.activateScope(scope)
    expect(localStorage.getItem(handoff)).toBe('pending')
  })
})
