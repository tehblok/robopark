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

## Round 1 review fixes

Status: all eight review findings addressed with focused regressions.

- Offline keys now include principal and park-access fingerprint. Exact-scope v1→v2 migration preserves actions, media, and entities, and collision migration retains the newer destination. Legacy records lacking provable principal/park identity remain durable but quarantined rather than being assigned to a different login.
- `refreshUser()` clears unclaimed global share-target images on identity replacement. Legacy device-wide recent-robot history is cleared on auth transition.
- Report photo drafts, including attachments and acknowledged report IDs, are quarantined in IndexedDB on exact-scope retirement and restored only to that same owner. The mounted Reports/ReportForms access-change path no longer destructively deletes them.
- Production workbench comment/handoff timeline items subscribe to action state and remove rejected provisional items on conflict, attention, or cancellation.
- Quota/denial per-write fallback now uses one 150 ms network-only batcher and waits for server confirmation. A successful partial network-only batch remains in waiting state, not offline.

Verification: bounded Vitest selection (8 files, 209 tests passed), `npx tsc --noEmit -p tsconfig.app.json` passed, `git diff --check` passed. No E2E or long-running tests were run.

Remaining limitation: records written under the original five-field offline scope format cannot be safely attributed to a principal or park-access set; they are retained for explicit recovery rather than auto-claimed. Network-only fallback still cannot durably queue a failed transport.

## Round 2 re-review fixes

Status: all four re-review findings addressed with focused red/green regressions.

- Registry retirement now matches admin and royal report-photo drafts whose selected park is null to the exact `all` scope. A replacement principal writing the same physical account/park draft key first archives the previous owner's photo and keeps the replacement draft writable.
- Schema migration now resolves collisions by terminal state and revision before timestamps. The losing lower-schema record is deleted in the same IndexedDB transaction, preventing cleanup from resurrecting stale actions, media, or entities on reopen.
- Sync state combines durable action/media counts and conflicts with the 150 ms quota-fallback batch. A confirmed network-only action cannot hide a durable queue or falsely enable a service-worker update.
- Successful bootstrap clears only the provenance-free legacy recent-robot key before publishing the authenticated user; account-scoped v2 recents remain untouched.

Verification: bounded Vitest selection (9 files, 217 tests passed), `npx tsc --noEmit -p tsconfig.app.json` passed, `git diff --check` passed. No E2E or long-running tests were run.
