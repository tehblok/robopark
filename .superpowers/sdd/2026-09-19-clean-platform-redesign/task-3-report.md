# Task 3 report — server and device cache architecture

## Result

Implemented bounded server/device caching without Redis while preserving the existing
optimistic mutation, idempotency, outbox, stale-if-error and offline behavior.

- Server cache families now have entry, byte, TTL/stale limits and common
  hit/miss/load/error/eviction/bytes/entries/latency/invalidation metrics.
- Cross-worker live-merge blobs are schema-tagged and namespace-bounded; the existing
  file-lock single-flight remains the shared-worker coordinator.
- Tracker issue invalidation now removes only matching issue/comments/transitions,
  known list projections and shared projection blobs; unrelated lists survive.
- Emergency payload L1 is byte- and entry-bounded and reports the same metrics.
- `/changes` and the large authenticated `/tracker/issues` response support private
  ETag/304 revalidation; the web change-feed retains validators.
- IndexedDB L2 is isolated by account, role/permission fingerprint, selected park and
  schema. It uses TTL/LRU/entry/byte limits, quota-pressure eviction, generation guards,
  targeted deletion and rejects original-photo keys.
- Login/logout/account/role/permission/park changes and 401/403 purge protected memory
  and IndexedDB data; late generations cannot republish it.
- Hidden/offline tabs stop change polling. Retry backoff, jitter and `Retry-After`
  survive focus/online/visibility events. The service worker continues to exclude all
  `/api/` and authenticated resources.

## RED evidence

- `pytest ... test_response_cache.py test_tracker_cache.py`: collection failed with
  `ModuleNotFoundError: robopark_api.services.cache_policy`; after the first contract
  existed, targeted invalidation exposed the prior broad list clear.
- `vitest ... indexedResourceStore.test.ts`: module absent before implementation.
- `pytest ... test_change_revisions.py`: expected `private, no-cache`/ETag but received
  `private, no-store` without 304 support.
- `vitest ... changeFeed.test.ts -t Retry-After`: six loads occurred before the retry
  deadline instead of one.
- `pytest ... test_tracker_read_list_issues`: authenticated large GET lacked
  `Cache-Control`/ETag and 304 handling.

## GREEN evidence

- Focused API cache/change/emergency/live-merge suites: `103 passed` and later focused
  post-review suite `69 passed`.
- Focused web resource/auth/park/IndexedDB/change-feed/200-viewer suites: `107 passed`;
  SW contract: `2 passed`.
- Full API: `./scripts/verify.sh api` -> `1773 passed, 7 skipped, 21 warnings`.
- Full web: `npm test` -> `146 files, 2070 tests passed`.
- `npm run build` passed; `npm run lint` exited 0 with 21 pre-existing warnings.
- `node --test scripts/build-sw.test.mjs` -> `2 passed`.
- `git diff --check` and scoped Ruff checks passed before commit.

## Self-review

No Critical or Important scoped findings remain. The review caught and fixed a
late-caller single-flight race, unbounded Tracker projection metadata, selected-park
L2 isolation, targeted IndexedDB deletion, Emergency byte accounting, and missing ETag
coverage for the large Tracker list.

## SHA and risks

- SHA: `HEAD` (`feat(cache): add bounded server and device caching`); the immutable hash
  is reported after commit because a commit cannot contain its own hash.
- The change feed remains a small authenticated revision poll (with scope-level
  revalidation) rather than a push transport; object-level Tracker server invalidation
  is targeted, and mounted clients refetch only their affected scope.
- Browser quota behavior varies by engine; tests cover quota exceptions and deterministic
  LRU fallback, while real-device quota telemetry belongs to Task 8 load/soak evidence.
- The lint command still reports the repository's 21 existing warnings; no new lint
  error was introduced.
