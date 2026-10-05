# Lifecycle handoff form

The real cross-browser workflow reproduced two outgoing handoff commands after
a Firefox double click. The form only guarded with React state and retained the
accepted payload; a durable enqueue could complete before the second click and
produce a fresh mutation id for the unchanged form.

The form now guards synchronously with a ref and consumes an accepted draft.
Edits made during the request remain intact, and failures retain input for retry.
Lifecycle forms are keyed by owner/issue, matching the isolation of legacy
handoff notes; input no longer follows navigation to another task/account.
The callback resolves after durable enqueue or confirmed network fallback.

Three newly added unit cases failed on the prior implementation. All 13 form
tests now pass, including preserving in-flight edits and retry after rejection.
The original browser double-click test passed five times in each of Chromium,
Firefox and WebKit (15 total). The full frontend suite passed: 2,851 tests in
194 files. Lint and the production TypeScript build passed. Read-only callback
review found no new blocker.

The preceding full 666-case local cross-browser run is NOT a passing gate. It
found this Firefox failure; later the Linux VM OOM-killed its Vite process while
a second browser container was running (memory.events oom_kill=1). Its remaining
connection failures are invalid app evidence; the run was stopped. Subsequent
browser containers must run serially in this 4 GiB local VM. CI jobs have their
own runners. Full cross-browser acceptance remains pending.

Lifecycle drafts are still in-memory. Unsubmitted text is not promised to survive
navigation/reload; durable accepted commands remain in the existing sync queue.
