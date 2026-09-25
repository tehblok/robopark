import { IDBFactory } from 'fake-indexeddb'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { storageRegistry } from './storageRegistry'
import { clearReportPhotoDrafts, readReportPhotoDraft, writeReportPhotoDraft } from '../domains/reports/reportPhotoDrafts'

beforeEach(async () => {
  vi.stubGlobal('indexedDB', new IDBFactory())
  localStorage.clear()
  await clearReportPhotoDrafts()
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
    expect(entries.find(entry => entry.name === 'robopark:system-operation:')).toMatchObject({
      kind: 'localStorage', owner: 'system', scope: 'account',
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

  it.each(['admin', 'royal'])('quarantines an unmounted %s photo with null selected park before a reused account can overwrite it', async role => {
    const original = { account: '1', principal: 'alice', parkAccess: '7', role, permissions: 'reports.create', park: 'all', schema: 1 }
    const replacement = { ...original, principal: 'bob' }
    const key = 'robopark:report-draft:1:7'
    const owner = (principal: string) => JSON.stringify([1, principal, null, role, 'approved', false, ['reports.create'], [[7, 'North']], null])
    const draft = (principal: string, name: string) => ({ key, ownerKey: owner(principal), revision: name, activeForm: 'problem' as const, trackerKey: '', title: name, body: '', createdReportId: 42, attachmentKind: 'device_photo' as const, attachment: { blob: new Blob([name]), name, lastModified: 1 } })
    await writeReportPhotoDraft(draft('alice', 'alice.jpg'))
    await storageRegistry.purgeScope(original)
    expect(await readReportPhotoDraft(key)).toBeNull()

    storageRegistry.activateScope(replacement)
    await writeReportPhotoDraft(draft('bob', 'bob.jpg'))
    expect((await readReportPhotoDraft(key))?.attachment?.name).toBe('bob.jpg')
    await storageRegistry.purgeScope(replacement)
    storageRegistry.activateScope(original)
    expect((await readReportPhotoDraft(key))?.attachment?.name).toBe('alice.jpg')
  })

  it('archives an occupied photo before a replacement principal writes the reused physical key', async () => {
    const key = 'robopark:report-draft:1:7'
    const scope = (principal: string) => ({ account: '1', principal, parkAccess: '7', role: 'admin', permissions: 'reports.create', park: 'all', schema: 1 })
    const draft = (principal: string) => ({ key, ownerKey: JSON.stringify([1, principal, null, 'admin', 'approved', false, ['reports.create'], [[7, 'North']], null]), revision: principal, activeForm: 'problem' as const, trackerKey: '', title: principal, body: '', createdReportId: 42, attachmentKind: 'device_photo' as const, attachment: { blob: new Blob([principal]), name: `${principal}.jpg`, lastModified: 1 } })
    await writeReportPhotoDraft(draft('alice'))
    await writeReportPhotoDraft(draft('bob'))
    expect((await readReportPhotoDraft(key))?.attachment?.name).toBe('bob.jpg')
    await expect(writeReportPhotoDraft({ ...draft('bob'), title: 'continued', revision: 'bob-2' })).resolves.toBeUndefined()
    await storageRegistry.purgeScope(scope('bob'))
    storageRegistry.activateScope(scope('alice'))
    expect((await readReportPhotoDraft(key))?.attachment?.name).toBe('alice.jpg')
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
