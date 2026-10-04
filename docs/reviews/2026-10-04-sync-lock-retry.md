# Retry durable work after sync-lock contention

Date: 2026-10-04.

One rc.29 responsive CI run saw the second mechanic's optimistic handoff but no
matching server message within 15 seconds. Six original mobile repetitions and
six desktop/mobile repetitions with failure diagnostics passed locally; the
original CI artifact did not contain a trace or the queue state. It therefore
does not prove which transport condition occurred in that run.

Code inspection found a deterministic lost-wakeup path consistent with the
symptom. SyncCoordinator returns false when the global Web Lock or fallback
lease is busy. SyncEngine previously returned without scheduling another attempt.
An account switch can leave the previous engine holding the global lock until
its in-flight operation finishes. If both the new engine's startup and 150 ms
enqueue attempts hit that lock, the durable action stays ready until another
external focus/online event.

Regression tests reproduced no retry for a ready action and for an explicitly
requested retry of attention-state media. The fix schedules one retry after one
second only while eligible actions/media remain. Normal idle and attention-only
queues do not start polling. A manual retry preserves its media retry intent.
A further review regression proved that an automatic focus/online wake during
the delay could cancel a manual media retry. The intent is now retained across
failed acquisitions and intervening wakes, and consumed inside an acquired pump and restored if the fallback lease is lost
before completion. Restoration preserves a newer manual request; an acquired
permanent upload error does not restart itself. Disposal clears that intent, cancels the timer and is checked again after
asynchronous queue reads.

Focused engine verification: 69 tests passed after the fix; the combined
engine/coordinator/provider suite passed 80 tests. Lint and TypeScript checks
passed. Desktop and mobile lifecycles passed in Chromium, Firefox and WebKit
with the contention retry; the final sticky manual intent is covered by the
deterministic interleaving regression and the final CI gate. The browser lifecycle keeps its original server-confirmation
assertions. Failure-only diagnostics retain the last 100 sync exchanges, a
bounded two-second server/IndexedDB snapshot attempt, and a Playwright trace.
These artifacts contain only isolated fixture data and do not collect production
credentials or records.
