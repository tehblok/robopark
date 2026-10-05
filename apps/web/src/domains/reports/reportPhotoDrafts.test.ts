// @vitest-environment node
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { IDBDatabase, IDBFactory, IDBObjectStore } from 'fake-indexeddb'
import { clearReportPhotoDrafts, deleteReportPhotoDraft, quarantineReportPhotoDraft, quarantineReportPhotoDraftsForScope, readReportPhotoDraft, restoreReportPhotoDraftsForScope, writeReportPhotoDraft, type ReportPhotoDraft } from './reportPhotoDrafts'

function draft(key = 'robopark:report-draft:1:7', revision = 'one'): ReportPhotoDraft {
  return { key, revision, ownerKey: 'user-1-park-7', activeForm: 'problem', title: 'Колесо', body: 'Не едет', trackerKey: '', createdReportId: 42,
    attachmentKind: 'device_photo', attachment: { blob: new Blob(['photo'], { type: 'image/jpeg' }), name: 'robot.jpg', lastModified: 123 } }
}

function injectBeforeNextReadwrite(next: ReportPhotoDraft) {
  const original = IDBDatabase.prototype.transaction
  const transaction = vi.spyOn(IDBDatabase.prototype, 'transaction').mockImplementation(function (this: IDBDatabase, storeNames, mode, options) {
    if (mode === 'readwrite') {
      transaction.mockRestore()
      const external = original.call(this, ['drafts', 'generations'], 'readwrite')
      external.objectStore('drafts').put(next)
    }
    return original.call(this, storeNames, mode, options)
  })
  return transaction
}

const scope = { account: '1', principal: 'alice', parkAccess: '7', role: 'mechanic', permissions: 'reports.create', park: '7', schema: 1 }
const scopedOwner = JSON.stringify([1, 'alice', null, 'mechanic', 'approved', false, ['reports.create'], [[7, 'North']], [7, 'North']])
const replacementOwner = JSON.stringify([1, 'bob', null, 'mechanic', 'approved', false, ['reports.create'], [[7, 'North']], [7, 'North']])

beforeEach(async () => { vi.stubGlobal('indexedDB', new IDBFactory()); await clearReportPhotoDrafts() })
describe('persistent report photos', () => {
  it('restores bytes, metadata and the already-created report only within its user and park', async () => {
    await writeReportPhotoDraft(draft())
    const restored = await readReportPhotoDraft('robopark:report-draft:1:7')
    expect(restored).toMatchObject({ title: 'Колесо', createdReportId: 42, attachment: { name: 'robot.jpg', lastModified: 123 } })
    expect(await restored?.attachment?.blob.text()).toBe('photo')
    expect(await readReportPhotoDraft('robopark:report-draft:2:7')).toBeNull()
    expect(await readReportPhotoDraft('robopark:report-draft:1:8')).toBeNull()
  })
  it('clears only the revision that was successfully submitted', async () => {
    await writeReportPhotoDraft(draft())
    await writeReportPhotoDraft({ ...draft(undefined, 'two'), title: 'Новая правка' })
    await deleteReportPhotoDraft('robopark:report-draft:1:7', 'one')
    expect((await readReportPhotoDraft('robopark:report-draft:1:7'))?.title).toBe('Новая правка')
    await deleteReportPhotoDraft('robopark:report-draft:1:7', 'two')
    expect(await readReportPhotoDraft('robopark:report-draft:1:7')).toBeNull()
  })
  it('revokes queued saves when logout or owner deletion occurs', async () => {
    const save = writeReportPhotoDraft(draft())
    const clear = clearReportPhotoDrafts()
    await Promise.all([save, clear])
    expect(await readReportPhotoDraft('robopark:report-draft:1:7')).toBeNull()
    const nextSave = writeReportPhotoDraft(draft())
    const remove = deleteReportPhotoDraft('robopark:report-draft:1:7')
    await Promise.all([nextSave, remove])
    expect(await readReportPhotoDraft('robopark:report-draft:1:7')).toBeNull()
  })
  it('rejects oversized files without replacing the previous stored draft', async () => {
    await writeReportPhotoDraft(draft())
    const huge = { ...draft(), attachment: { name: 'huge.jpg', lastModified: 0, blob: new Blob([new Uint8Array(15 * 1024 * 1024 + 1)]) } }
    await expect(writeReportPhotoDraft(huge)).rejects.toThrow(/15 МиБ/)
    expect(await (await readReportPhotoDraft('robopark:report-draft:1:7'))?.attachment?.blob.text()).toBe('photo')
  })
  it('bounds total records without silently deleting incomplete reports', async () => {
    for (let i = 0; i < 8; i++) await writeReportPhotoDraft(draft(`robopark:report-draft:${i}:7`))
    await expect(writeReportPhotoDraft(draft('robopark:report-draft:9:7'))).rejects.toThrow(/Удалите/)
    expect(await readReportPhotoDraft('robopark:report-draft:0:7')).not.toBeNull()
  })
  it('bounds text and total photo bytes as well as each individual photo', async () => {
    await expect(writeReportPhotoDraft({ ...draft(), body: 'x'.repeat(256 * 1024) })).rejects.toThrow(/Текст/)
    for (let i = 0; i < 4; i++) await writeReportPhotoDraft({ ...draft(`large:${i}`), attachment: { name: 'photo.jpg', lastModified: 0, blob: new Blob([new Uint8Array(15 * 1024 * 1024)]) } })
    await expect(writeReportPhotoDraft(draft('large:4'))).rejects.toThrow(/Удалите/)
    expect(await readReportPhotoDraft('large:0')).not.toBeNull()
  })

  it('rejects a revoked writer from another tab but allows a fresh authorized session', async () => {
    await writeReportPhotoDraft(draft())
    vi.resetModules()
    const oldTab = await import('./reportPhotoDrafts')
    await oldTab.readReportPhotoDraft('robopark:report-draft:1:7')
    await clearReportPhotoDrafts()
    await expect(oldTab.writeReportPhotoDraft(draft())).rejects.toThrow(/Сеанс/)
    await expect(oldTab.writeReportPhotoDraft(draft('unseen-park'))).rejects.toThrow(/Сеанс/)
    expect(await readReportPhotoDraft('robopark:report-draft:1:7')).toBeNull()
    vi.resetModules()
    const newSession = await import('./reportPhotoDrafts')
    await newSession.readReportPhotoDraft('robopark:report-draft:1:7')
    await newSession.writeReportPhotoDraft({ ...draft(), title: 'Новый сеанс' })
    expect((await newSession.readReportPhotoDraft('robopark:report-draft:1:7'))?.title).toBe('Новый сеанс')
  })
  it('revokes another tab on a scoped owner change without removing other parks', async () => {
    await writeReportPhotoDraft(draft())
    await writeReportPhotoDraft(draft('robopark:report-draft:1:8'))
    vi.resetModules()
    const oldTab = await import('./reportPhotoDrafts')
    await oldTab.readReportPhotoDraft('robopark:report-draft:1:7')
    await deleteReportPhotoDraft('robopark:report-draft:1:7')
    await expect(oldTab.writeReportPhotoDraft(draft())).rejects.toThrow(/Сеанс/)
    expect(await readReportPhotoDraft('robopark:report-draft:1:7')).toBeNull()
    expect(await readReportPhotoDraft('robopark:report-draft:1:8')).not.toBeNull()
  })

  it.each(['logout', 'owner change'])('does not rebind an old session when %s happens during its generation read', async (revocation) => {
    await writeReportPhotoDraft(draft())
    let clearing!: Promise<void>
    const originalGet = IDBObjectStore.prototype.get
    const get = vi.spyOn(IDBObjectStore.prototype, 'get').mockImplementationOnce(function (this: IDBObjectStore, key) {
      const request = originalGet.call(this, key)
      // The transaction has started, but its generation reply has not arrived.
      clearing = revocation === 'logout' ? clearReportPhotoDrafts() : deleteReportPhotoDraft('robopark:report-draft:1:7')
      return request
    })
    await writeReportPhotoDraft({ ...draft(), title: 'Старый запрос' })
    await clearing
    get.mockRestore()
    expect(await readReportPhotoDraft('robopark:report-draft:1:7')).toBeNull()
    await writeReportPhotoDraft({ ...draft(), title: 'Новый сеанс после выхода' })
    expect((await readReportPhotoDraft('robopark:report-draft:1:7'))?.title).toBe('Новый сеанс после выхода')
  })

  it('counts the preserved previous owner against the record budget', async () => {
    for (let i = 0; i < 8; i++) await writeReportPhotoDraft(draft(`owner-count:${i}`))
    const replacement = { ...draft('owner-count:0'), ownerKey: 'new-owner', title: 'New principal' }
    await expect(writeReportPhotoDraft(replacement)).rejects.toThrow(/Удалите/)
    expect(await readReportPhotoDraft('owner-count:0')).toMatchObject({ ownerKey: 'user-1-park-7' })
    expect(await readReportPhotoDraft('owner-count:0:retired:user-1-park-7')).toBeNull()
  })

  it('counts the preserved previous owner photo against the byte budget', async () => {
    const photo = new Blob([new Uint8Array(15 * 1024 * 1024)])
    for (let i = 0; i < 4; i++) {
      await writeReportPhotoDraft({ ...draft(`owner-bytes:${i}`), attachment: { name: 'photo.jpg', lastModified: 0, blob: photo } })
    }
    await expect(writeReportPhotoDraft({ ...draft('owner-bytes:0'), ownerKey: 'new-owner' })).rejects.toThrow(/Удалите/)
    expect((await readReportPhotoDraft('owner-bytes:0'))?.attachment?.blob.size).toBe(photo.size)
  })

  it('preserves the previous owner when the replacement fits the budget', async () => {
    await writeReportPhotoDraft(draft('owner-fits'))
    await writeReportPhotoDraft({ ...draft('owner-fits'), ownerKey: 'new-owner', title: 'New principal' })
    expect(await readReportPhotoDraft('owner-fits')).toMatchObject({ ownerKey: 'new-owner', title: 'New principal' })
    expect(await readReportPhotoDraft('owner-fits:retired:user-1-park-7')).toMatchObject({ ownerKey: 'user-1-park-7' })
  })

  it('does not double count an archived slot overwritten by the owner handoff', async () => {
    const active = draft('archive-replaced')
    await writeReportPhotoDraft({ ...active, key: 'archive-replaced:retired:user-1-park-7', title: 'Older archive' })
    await writeReportPhotoDraft({ ...active, title: 'Latest previous owner' })
    for (let i = 0; i < 6; i++) await writeReportPhotoDraft(draft(`other:${i}`))
    await writeReportPhotoDraft({ ...active, ownerKey: 'new-owner' })
    expect(await readReportPhotoDraft('archive-replaced:retired:user-1-park-7')).toMatchObject({ title: 'Latest previous owner' })
    await expect(writeReportPhotoDraft(draft('ninth-slot'))).rejects.toThrow(/Удалите/)
  })

  it('allows the same owner to replace a photo when all record slots are used', async () => {
    for (let i = 0; i < 8; i++) await writeReportPhotoDraft(draft(`same-owner:${i}`))
    await writeReportPhotoDraft({ ...draft('same-owner:0'), title: 'Updated in place' })
    expect(await readReportPhotoDraft('same-owner:0')).toMatchObject({ title: 'Updated in place' })
  })

  it('quarantines the latest same-owner draft written after the scope scan', async () => {
    const key = 'scope-quarantine-race'
    const original = { ...draft(key), ownerKey: scopedOwner, title: 'Before scan' }
    const latest = { ...original, revision: 'two', title: 'Written by another tab' }
    await writeReportPhotoDraft(original)
    const transaction = injectBeforeNextReadwrite(latest)

    await quarantineReportPhotoDraftsForScope(scope)

    transaction.mockRestore()
    expect(await readReportPhotoDraft(key)).toBeNull()
    expect(await readReportPhotoDraft(`${key}:retired:${encodeURIComponent(scopedOwner)}`))
      .toMatchObject({ revision: 'two', title: 'Written by another tab' })
  })

  it('does not quarantine a replacement owner written after the scope scan', async () => {
    const key = 'scope-owner-race'
    const original = { ...draft(key), ownerKey: scopedOwner, title: 'Alice before scan' }
    const replacement = { ...original, ownerKey: replacementOwner, revision: 'bob', title: 'Bob in another tab' }
    await writeReportPhotoDraft(original)
    const transaction = injectBeforeNextReadwrite(replacement)

    const quarantine = quarantineReportPhotoDraftsForScope(scope)
    // This call captures the old generation before the queued quarantine runs.
    const staleWrite = writeReportPhotoDraft({ ...original, revision: 'alice-late', title: 'Late Alice write' })
    await Promise.all([quarantine, staleWrite])

    transaction.mockRestore()
    expect(await readReportPhotoDraft(key)).toMatchObject({ ownerKey: replacementOwner, revision: 'bob', title: 'Bob in another tab' })
    expect(await readReportPhotoDraft(`${key}:retired:${encodeURIComponent(scopedOwner)}`)).toBeNull()
  })

  it('restores the latest archived draft written after the scope scan', async () => {
    const key = 'scope-restore-race'
    const original = { ...draft(key), ownerKey: scopedOwner, title: 'Before scan' }
    const archivedKey = `${key}:retired:${encodeURIComponent(scopedOwner)}`
    await writeReportPhotoDraft(original)
    await quarantineReportPhotoDraft(key, scopedOwner)
    const latest = { ...original, key: archivedKey, revision: 'two', title: 'Written by another tab' }
    const transaction = injectBeforeNextReadwrite(latest)

    await restoreReportPhotoDraftsForScope(scope)

    transaction.mockRestore()
    expect(await readReportPhotoDraft(key)).toMatchObject({ revision: 'two', title: 'Written by another tab' })
    expect(await readReportPhotoDraft(archivedKey)).toBeNull()
  })

})
