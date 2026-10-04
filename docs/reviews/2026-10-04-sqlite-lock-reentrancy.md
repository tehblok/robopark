# SQLite nested lock bucket collision

Date: 2026-10-04

Full cross-browser CI run 37229816154 retained the failing mobile repair-start
trace. The outer offline-sync receipt key and the inner Tracker claim key
mapped to the same one of 64 bounded SQLite lock files. Reopening that file and
taking flock through a second descriptor made the synchronous request wait on
itself. The trace contained three attempts, each delayed for about five seconds
before idempotency_lock_busy; the action did not reach the upstream service.

The process lock now uses the physical bucket key and remains an RLock. A
thread-local nesting count makes only the outer holder open, flock and close
the descriptor. Other threads still acquire the process lock, and other
processes still contend on flock. No new lock files, bucket count, API timeout,
PostgreSQL path, or idempotency receipt semantics are introduced.

Verification:
- A deterministic regression using the colliding CI keys failed with the old
  code and passed after the fix.
- The lock suite checks nested cleanup and exclusion of another thread while
  the outer lock remains held: 13 tests passed.
- Combined lock and offline-sync regressions: 40 passed.
- The exact Linux WebKit mobile repair lifecycle passed (27.7 seconds).
- Ruff passed for both changed API files.

The accompanying reconnect browser-test correction covers the valid zero to
29,999 ms resume jitter. No product polling schedule was changed. Its hook
suite passed 10 tests and the exact WebKit scenario passed.

The full CI run passed core/API/host/dependency checks and all four responsive
shards; its two cross-browser failures are not treated as a passing release
gate. A new full run is required after these corrections.
