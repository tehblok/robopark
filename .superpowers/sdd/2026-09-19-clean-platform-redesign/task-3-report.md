# Task 3 report — server and device cache architecture

## Result

Implemented bounded server/device caching without Redis while preserving the existing
optimistic mutation, idempotency, outbox, stale-if-error and offline behavior.

- Server cache families now have entry, byte, TTL/stale limits and common
  hit/miss/load/error/eviction/bytes/entries/latency/invalidation metrics.
- Cross-worker live-merge blobs are schema-tagged and namespace-bounded; the existing
  file-lock single-flight remains the shared-worker coordinator.
- Tracker read-only invalidation removes only matching issue/comments/transitions and
  known projections; mutations retire the three list families so membership changes
  (including closed-to-open) cannot leave a missing issue cached.
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

- Base implementation SHA: `486a0751741663b860dc269b55d14d3be6ca5d04`
  (`feat(cache): add bounded server and device caching`). The review SHA is reported in
  the completion handoff because a commit cannot contain its own hash.
- The change feed remains a small authenticated revision poll (with scope-level
  revalidation) rather than a push transport; object-level Tracker server invalidation
  is targeted, and mounted clients refetch only their affected scope.
- Browser quota behavior varies by engine; tests cover quota exceptions and deterministic
  LRU fallback, while real-device quota telemetry belongs to Task 8 load/soak evidence.
- The lint command still reports the repository's 21 existing warnings; no new lint
  error was introduced.

## Review round 1

All nine findings were reproduced and closed with regression coverage:

- IndexedDB hydration captures the exact store and generation, preserves `updatedAt`,
  rejects late authorization-scope reads, purges crash-left scopes, and enforces its
  entry/byte ceiling globally across the database.
- Tracker mutations retire membership-sensitive list families while ordinary targeted
  invalidation preserves unrelated projections. Cache generations prevent blocked
  pre-mutation loaders from republishing stale projections across local/shared tiers.
- The configured stale bound is passed through live merge; a short-stale regression
  proves a three-second-old result cannot be served under a two-second policy.
- Tracker list transport now sends `If-None-Match` and returns the typed cached payload
  on 304 from a bounded validator cache.
- Live-merge result and lock/error metadata are bounded. Shared gauges include bytes,
  entries and evictions, and invalidation metrics retain their reason.
- Resource polling removes timers while hidden/offline, resumes once, respects backoff
  and `Retry-After`, and 200 mounted consumers plus route/UI-mode remounts issue one
  fresh GET total.

Review RED evidence included the late IDB read, blocked Tracker list loader, missing
shared stale parameter, absent Tracker request validator, retained hidden timer and the
former synthetic 200-viewer test. Review GREEN evidence: focused API `78 passed`,
focused web `62 passed`, full web `147 files / 2075 passed`, production build and SW
`2 passed`; final full API `1776 passed, 7 skipped, 21 warnings`.

Residual risk: IndexedDB quota thresholds vary by browser, and cross-process live merge
uses bounded filesystem metadata rather than Redis by design. Both paths now expose
deterministic eviction behavior and metrics for Task 8 soak validation.

## Review round 2

All four reproduced findings are closed with direct regression coverage:

- Resource hydration now captures both the device-store generation/scope and a bounded
  per-key resource version. A concurrent `set`, exact/prefix invalidation, eviction or
  global clear retires the disk read before it can overwrite memory or notify consumers.
- Response-cache invalidation and clear detach and retire the old per-key flight. Existing
  waiters still complete from their captured flight object, while callers arriving after
  invalidation start or join only the new generation; the retired result is never stored,
  and an unreplaced retired flight releases its per-key generation metadata.
- Tracker list ETag/payload validators capture an authorization generation. Login, logout,
  401/403 and successful permission/role refresh increment that generation and clear the
  bounded map, so a late old-account response cannot repopulate it.
- The former synthetic mode prop was removed. A real AppRouter test now mounts the actual
  AppShell/resource owner, navigates `/work?park=7` to `/robots?park=7`, switches to
  interface mode A, and observes exactly one fresh badge GET throughout. Badge loading is
  also deferred until park scope is settled, and route cleanup retires only pending work
  rather than deleting a still-fresh value.

Round-2 RED evidence: a blocked hydration overwrote a newer value and restored an
invalidated key; post-invalidation callers joined the blocked old server flight; an
old-account Tracker response repopulated its validator; and the real route transition
duplicated the badge GET before the park-scope/owner fix.

Round-2 GREEN evidence: focused API `57 passed`; focused web `166 passed`; full API
`1779 passed, 7 skipped, 21 warnings`; full web `147 files / 2079 passed`; production
build and SW `2 passed`; lint and Ruff/format exited 0. The lint warnings are the existing
repository warnings. Residual risks remain browser-specific quota behavior and the
filesystem shared tier already called out above; neither round-2 race remains open.

## Review round 3

The route/interface acceptance test now uses the same interface-provider boundary as
production: the shared `renderApp` helper mounts `InterfaceModeProvider` with the current
account id. After the real `/work?park=7` to `/robots?park=7` navigation, the test clicks
the actual mode radio, waits for lazy interface CSS to finish and for
`html[data-interface="task-first"]` to be applied, verifies the account-scoped preference,
and only then confirms that the badge GET count remains one.

RED evidence: with the prior helper (no provider), the store-only assertion passed but
the new DOM assertion timed out because `data-interface` was absent. GREEN evidence:
the focused transition test passed; all render-helper consumers passed `136/136`; full
web passed `147 files / 2079 tests`; production build and lint passed (existing warnings
only). No production file changed in this review round.
