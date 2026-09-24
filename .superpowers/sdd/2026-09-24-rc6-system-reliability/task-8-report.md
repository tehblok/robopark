# Task 8 — client storage and optimistic batching

Status: implemented and verified in the assigned worktree.

Changes:
- Added a registry inventory for every discovered product-owned IndexedDB and localStorage namespace, including owner, schema, retention, and scope. `purgeScope(scope)` requires an exact scope and retires it without deleting pending actions or media.
- Preserved actions, media, and entities across an older-to-newer offline scope schema. Scope switches hide old records and reject late writes while leaving durable pending work recoverable after exact reauthorization. Entity projections now retain for 14 days by default.
- Archived report, handoff, and comment drafts under the exact authorization identity on logout or role/permission/park-access change. They restore only for that same identity. Unclaimed share-target blobs remain ephemeral and are cleared on logout.
- Added next-render optimistic projections and 150 ms action batching, with rollback on conflict. Denied/full IndexedDB uses direct network delivery and only reports success after server acknowledgement.
- Removed the permanent “Всё отправлено” sync label; the center now shows sending, waiting, or attention when applicable.

Verification:
- `npm test -- --run src/pwa/storageRegistry.test.ts src/pwa/offlineDb.test.ts src/pwa/syncEngine.test.ts src/pwa/SyncProvider.test.tsx src/pwa/SyncCenter.test.tsx src/shared/auth/protectedBrowserStorage.test.ts src/lib/indexedResourceStore.test.ts src/lib/deviceResourceCache.test.ts src/auth.test.tsx src/components/reports/ReportForms.test.tsx`: 10 files, 91 tests passed.
- `npx tsc -b --pretty false`: passed.
- Targeted `npx oxlint` on changed implementation files: exit 0; two existing Fast Refresh warnings in `SyncProvider.tsx`.
- `git diff --check`: passed.

Limits: Network-only mode has no durable offline queue; failed sends reject and roll back the provisional projection. Retired offline scopes are intentionally retained on disk until safe, acknowledged cleanup is implemented or an explicit wipe is requested. The registry reports the global share inbox as `ephemeral-global`.
