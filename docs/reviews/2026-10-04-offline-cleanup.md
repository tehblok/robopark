# Concurrent offline cleanup

Date: 2026-10-04. Prepared after rc.29; not included in that release candidate.

Startup cleanup in SyncProvider can overlap an already running tab. OfflineDb
previously read entity eligibility in a readonly transaction and deleted the
record by ID in a later write transaction. A concurrent putEntity could commit a
fresh value between those operations; cleanup then deleted the fresh value.

Cleanup now reads entities, actions and media and applies all eligible deletions
in one readwrite transaction. Another connection either commits first and is
included in the snapshot, or commits after cleanup and retains its new value.
TTL, pending dependencies, media retention anchors and storage-pressure choices
are preserved. Safe evictions commit before OfflineStorageFullError is raised.
A request/delete failure aborts the transaction. No database schema changes.

Verification:

- The deterministic two-connection unit regression failed before the fix: a
  committed fresh entity became undefined. It passes with the atomic cleanup.
- OfflineDb, SyncEngine and SyncProvider: 97 tests passed.
- Native IndexedDB in Chromium, Firefox and WebKit: 9 passes (3 per engine),
  including reading the fresh value after a page reload.
- Frontend lint and production build passed; independent review found no blockers.

This fixes stale eligibility/deletion races. It does not by itself prevent a
producer from enqueuing a new action that refers to an already evicted receipt;
that separate producer contract is documented in review-comment-atomicity.
