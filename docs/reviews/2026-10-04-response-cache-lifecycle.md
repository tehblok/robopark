# Response cache lifecycle review — 2026-10-04

Scope: in-process generation metadata and the lock-free shared-result read path.

## Findings and fixes

- `invalidate_prefix()` allocated `_key_generations` entries for every cached key, even when no flight needed fencing. A 5,000-key invalidation emptied the value store but retained 5,000 generation records. Prefix invalidation now mirrors point invalidation: it advances a generation only for an active retired flight and removes metadata for cache-only keys.
- Shared payloads and their file revision were previously observed separately. This affected fast reads and the leader path after `merge_load()`: an atomic replacement between value return and `stat()` could pair an old payload with the new revision, allowing L1 to accept stale data for its TTL. `LiveMergeStore` now carries a file identity and timestamp with every cache-hit, follower, leader, and stale return. Fast reads use a stable stat/read/stat snapshot; leaders capture the published identity while holding the merge locks. The public `merge_load()` return contract remains unchanged, including rich leader values when `shared_payload` writes a projection.

The existing stale-flight fence remains intact: prefix invalidation retires an active flight, allows a new flight to publish, and releases the retired generation metadata when the old flight completes.

## Verification

Before the fix, focused regressions failed with 5,000 retained generation entries, `old` returned from both fast-read APIs after an interleaved replacement, and a rich leader value retained against a newer shared revision. After the fix:

```text
apps/api/tests/test_response_cache.py (focused): 7 passed
apps/api/tests/test_live_merge.py + apps/api/tests/test_response_cache.py: 58 passed
apps/api/tests/test_live_merge_multiprocess.py + test_session_release.py: 8 passed
ruff check --no-cache (changed Python files): passed
```

If the result file changes during both bounded snapshot attempts, the operation reports a cache miss and continues through the existing merge/load path. L1 compares the complete file identity, so an atomic replacement with the same `mtime` is still rejected. Local invalidation retains its existing generation and retired-flight fence.

Independent review found the leader-return race during this work; it was reproduced and fixed. The final reviewed diff has no remaining reproducible findings in the examined scope. The full API suite is running separately.
