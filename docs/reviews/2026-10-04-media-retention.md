# Media upload bounds and retention review

## Fixed contracts

- Chunk requests are authorized before their body is consumed. A declared body over 1 MiB is rejected immediately, and a chunked body is read only until the accumulated bytes cross 1 MiB. The remaining ASGI stream is not buffered and the storage service is not called.
- Completion still renames a staged part to the deterministic `<upload-id>.ready` name before committing the database row. A commit exception is rolled back, while the ready file is left in place because the exception may represent either a failed commit or a lost acknowledgement after a durable commit. A fresh session therefore decides the outcome: a durable completion replays successfully; an incomplete expired row is reclaimed with both its recorded part name and its deterministic ready name.
- Expired-session reinitialization uses the same two-name cleanup, so a prior failed completion cannot leave an unreferenced ready blob when the media identity is reused.

## Pending dependency retention

Completed media bound to a nonterminal offline action is retained for 30 days from `completed_at`. A confirmed or rejected action keeps the existing seven-day retention from `dependency_terminal_at`; an unbound completed upload keeps the existing seven-day retention from `completed_at`. The 30-day bound limits disk and quota retention. It does not release quota immediately when a client cancels a local action.

The client keeps the original Blob with the stable media ID, action ID, and idempotency key. If cleanup wins after 30 days, the next action attempt receives the existing `media_dependency_pending` conflict, reuploads the Blob, and retries the same action identity. If the local Blob is no longer available, the existing conflict/attention flow requires the user to supply the photo again.

Cleanup and dependency consumption share the media reservation fence. The service freshly checks actor, media ID, issue key, completion state, and bound device/action, then copies the bytes while holding that fence. It releases the fence before Tracker or notification calls. Receipt handling checks for a durable receipt before reading media and rechecks under the per-action receipt lock before dispatch, so concurrent retries cannot repeat the external action. A terminal receipt replay does not require the retained media file.

The dependency lookup does not use a PostgreSQL row lock. The reservation fence already serializes the lookup and compare-and-set binding, while `FOR UPDATE` would survive fence release in the caller-owned transaction and let a retry block the global media fence. The conditional binding update remains the database-level conflict check.

## Verification

- `apps/api/tests/test_media_uploads.py` covers multi-message early abort, commit failure and ambiguous success with fresh sessions, orphan cleanup during cleanup and reinitialization, the 30-day bound, and cleanup/read serialization.
- `apps/api/tests/test_offline_sync.py` covers cleanup-winning reupload with stable action identity, receipt replay without a second dispatch, and atomic submit-review comment/review effects.
- `apps/api/tests/postgres/test_schema_and_workflows.py` leaves the first consumer transaction open and proves that a second consumer of the same row and an unrelated media consumer both finish without waiting on a retained row lock.

No physical hardware or host installation was exercised in this slice. Real
PostgreSQL verification used the supported temporary Docker fixture and cleaned
it up after each run.

Final targeted validation: 64 media/offline tests passed; the real PostgreSQL
regression failed before removing FOR UPDATE and passed afterwards. Ruff check,
format check and diff check passed. Independent re-review found no remaining
reproducible blocker in this slice.

The full PostgreSQL suite initially found an outdated three-argument dispatch
mock. Updating it to the four-argument consumed-media contract (with an explicit
None assertion for a comment action) restored the concurrent receipt replay
test. Final PostgreSQL acceptance: 26 passed; no fixture containers remained.
