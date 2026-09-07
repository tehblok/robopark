# Lifecycle hardening: C1, I1, I2

Base: `738ae4c`, branch `codex/armbian-hardening-lifecycle`. I1/I2 commit: `b73a89b`; C1 is the commit containing this report. Reviewed the exact `final-independent-review.md` supplied by the parent. Changes are restricted to this worktree; no production private key, production artifact, signing, retention or capacity work was performed.

## C1: installed restore

The installed API queues a Royal-approved, actor-bound root request with a unique restore artifact and SHA-256. The old in-process restore now fails closed in installed mode; legacy local restore remains separate. The root consumer revalidates the strict request and archive, copies the payload into root-private state, checks every member/hash/size/path/type, rejects SQLite sidecars and executable/config destinations outside the allowed snapshot schema, and checks SQLite integrity plus the installed signed release's Alembic head. Private snapshot validation uses immutable read-only SQLite and closes the connection; a WAL-mode DB cannot acquire new WAL/SHM files during validation.

The root-owned transaction holds host.lock, durably sets maintenance, stops the entire app stack, saves a complete previous data directory, stages and fsyncs the replacement, and replaces the complete data directory using recoverable renames. This includes DB/WAL/SHM lifetime and attachments. Host configuration/credentials, app releases and API job state are preserved. A root journal makes recovery idempotent; before the durable writes-resumed boundary, recovery restores the previous directory, while accepted writes after that boundary are never rolled back. App readiness precedes Tuna restart and maintenance removal. Publication failure preserves healthy local data.

`restore-check` is installed as app ExecStartPre, refusing boot during incomplete rename/recovery phases. A corrupt restore journal fails closed. Update worker and recovery entrypoints cannot take over an unfinished restore. Automatic consumer retries preserve maintenance on exhaustion; explicit root `restore --recover` retries the existing approved private transaction under a nonblocking host.lock and returns 75 when busy.

Recovery material lives in `ops/state/restores/<job_id>/{snapshot.zip,candidate,previous}`; staging/displaced directories are `var/.manual-restore-<job_id>` and `var/.manual-displaced-<job_id>`. The separate retention work must protect active restore material and account for terminal material; it is intentionally not implemented here.

## I1/I2: publication and ownership

Update success and rollback explicitly restart Tuna after local readiness. Failure to restore publication produces degraded status without undoing healthy data. The E2E OS adapter reads actual installed Tuna Requires metadata, propagates app stops to Tuna, models inactive try-restart correctly, and executes the replaced host tool source. Tests cover update, rollback, publication failure and repair.

Standalone repair/watchdog take a nonblocking host.lock and refuse mutations while maintenance or a durable command claim exists. Watchdog skips without advancing failures. Installer also owns host.lock (and retains its older install.lock compatibility guard), refuses pending operations, finishes host mutations before releasing ownership, then starts consumers/timers. The documented lock order is consumer.lock -> host.lock; workers acquire host.lock after the consumer releases it, with the private claim protecting that handoff gap. Restore runs entirely under its consumer's existing lock; internal helpers do not recursively acquire it.

## TDD and verification

- Initial unsafe restore, standalone repair/watchdog exclusion and dependency-aware Tuna cases: 7 RED -> 7 GREEN.
- Installer shared ownership and synchronous consumer handoff: 2 RED -> 2 GREEN.
- Root restore/API approval: 2 RED -> 5 GREEN.
- Restore fault suite: 27 passing cases, including 17 durable journal phases, both directory rename windows and four independent real guarded SQLAlchemy writer processes.
- Explicit root recovery after retry exhaustion: RED -> GREEN.
- Strict malformed snapshot metadata: 2 RED -> GREEN; invalid SQLite and archive-boundary regressions included.
- Standalone update takeover during interrupted restore: RED -> GREEN.
- Real WAL-mode SQLite validation side effects: RED -> GREEN.
- Full API: 770 passed in 99.98 s, one existing Starlette/httpx deprecation warning. Canonical API Ruff check + format check: PASS (180 files already formatted). No unrelated import-order or formatting changes were included.
- Intermediate full host gate: 550 pytest tests passed in 292.23 s; 38 installer scenarios passed in 97.404 s. Two earlier obsolete assertions were updated to the new restart/handoff contract before this passing run.
- Final full host gate: 552 pytest tests passed in 285.30 s; 38 installer scenarios passed in 88.785 s. Final root-restore coverage: 33 tests.
- Changed/new host and API implementation/tests checked with Ruff; inherited legacy style in state.py/watchdog.py was preserved where unrelated to this change. `git diff --check`: PASS.

## Integration and target-only gates

The M1 operational-state producer work is owned by another agent. No import of its not-yet-integrated module is added here. Parent should wire `record_backup` around the successful/failed pre-cutover `snapshot(paths, journal)` in updater.py after integration.

Target-only НЕ ВЫПОЛНЕНО: real Linux/Armbian Docker builds, actual systemd/reboot/process shutdown, live Tuna HTTPS, production restore on a device, 200-client capacity or production packaging/signing. Simulated dependency/boot/fault tests do not close those gates. The Russian operator guide now describes installed data-only restore, busy ownership and explicit restore recovery.
