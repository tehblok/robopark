# Independent whole-branch review

Reviewed target: `e5e6c9d..26e9463`, primary Armbian installer/OTA worktree. Root `VERSION=0.1.0` is authoritative. Reviewer made no implementation changes or commits.

**Verdict: changes required before production packaging/installation.** One Critical, six Important, and two Minor findings below. Target Docker/systemd/Armbian/reboot/Tuna/200-client gates remain pending; simulated tests do not close them.

Review covered the integrated installer, trust/manifest/packaging boundary, runtime and systemd generation, host CLI/doctor/repair/watchdog, updater/rollback/self-update, GitHub download/approval and API bridge, manual snapshot/restore, Royal operations UI, and capacity harness. This builds on the earlier scoped Task 5–8 reviews; it is not a claim that every line of the large diff was individually inspected. Requirements are the design and implementation plan, including design sections on signing-key rotation, retention and concurrent operations. The progress ledger does not waive those requirements.

## Critical

### C1. Installed-host manual restore replaces a live multiprocess database without a writer barrier

Locations: `apps/api/src/robopark_api/services/ops/runner.py:280–292`, `services/ops/snapshot.py:133–138`, `services/ops/context.py:63–64`; exposed without an installed-mode branch at `routers/admin_ops.py:282–304`.

The restore endpoint launches the old in-API restore. It disposes only the current process's engine, checks that the root host-maintenance marker is **absent**, unlinks the production SQLite database/WAL/SHM and copies the snapshot. The installed runtime has multiple API workers. Its background/SQL write guards consult the root marker, whereas this job sets only local API maintenance. The initiating Royal session is exempt from local maintenance (`middleware/maintenance.py:65–78`). Other API processes retain connections and background writers continue. This permits accepted writes against the old database while it is replaced and can lose writes or leave workers on inconsistent database handles. An interrupted replacement also has no host recovery transaction. Setting `restart_required=True` does not schedule a host restart, despite the UI promising one.

Evidence: safe temporary-context probe activated a real local restore job and executed an INSERT through the real guarded API SQLAlchemy engine: local maintenance `True`, host maintenance `False`, INSERT accepted. Inspection confirms `get_engine().dispose()` is process-local and the root command consumer has no manual restore command.

Expected fix: installed-host restoration must run through a root-controlled transaction using the same exclusive host-operation ownership as update, a durable maintenance marker, complete app-writer shutdown, snapshot validation, crash-safe replacement and restart/readiness of every app worker. Keep legacy local behavior separate if needed. Until implemented, fail closed for installed-host manual restore rather than offering unsafe success.

## Important

### I1. OTA and rollback stop Tuna, then only try-restart an inactive service

Locations: `deploy/systemd/robopark-tuna.service:4`, `deploy/host/robopark_host/updater.py:677,1002,1020,1034`; `rollback.py:142–156`.

Tuna `Requires=robopark.service`. Explicitly stopping the app during cutover stops Tuna. Starting/restarting the app later does not start its now-inactive reverse dependent; updater then uses `try-restart` for Tuna, which does nothing for an inactive unit. The rollback path has no explicit Tuna start at all. A healthy update or successful rollback can therefore permanently remove the only Internet ingress until an operator repairs the local host.

Evidence: real updater apply/reconcile and rollback exercised against a dependency-aware systemd adapter both reached their healthy terminal state with `tuna_active=False`. The earlier E2E adapter did not model this dependency. This is consistent with systemd's [Requires semantics](https://raw.githubusercontent.com/systemd/systemd/main/man/systemd.unit.xml), [try-restart semantics](https://raw.githubusercontent.com/systemd/systemd/main/man/systemctl.xml), and [restart propagation as TRY_RESTART](https://raw.githubusercontent.com/systemd/systemd/main/src/core/transaction.c).

Expected fix: explicitly start/restart Tuna after local app readiness on both success and rollback, including restoration of a previous Tuna unit when required. Test inactive-unit behavior and actual Requires stop propagation. Preserve the current rule that a publication failure after local health does not roll back accepted database writes; report publication degraded and make recovery actionable.

### I2. Standalone repair, watchdog and installer bypass the shared host-operation exclusion

Locations: `deploy/host/robopark_host/cli.py:131–134`, `watchdog.py:46–58`, `deploy/installer/install.sh:31–32`; contrast updater's `host.lock` at `updater.py:566`.

Updater and root commands hold `ops/host.lock`, but CLI repair invokes service mutations directly, watchdog restarts the app after three readiness failures without checking this lock/maintenance, and installer holds a separate `install.lock`. These entry points can restart app/Docker or replace host configuration during an update's snapshot, migration or rollback. This violates the shared exclusion required by design line 311 and can reopen writers inside an otherwise protected cutover.

Evidence: while a temporary host's real `host.lock` was held using `flock`, real CLI repair orchestration and three watchdog cycles issued two service mutations through a safe recording runner. No real services were changed.

Expected fix: define one host-operation owner and a documented acquisition/handoff order for install, repair, watchdog, snapshot, restore and update. Watchdog must skip an intentionally unavailable app while maintenance/another operation owns the host. Do not simply wrap every internal helper in the same blocking lock: installer starting a consumer while still holding the lock, or a consumer recursively taking its own lock, would deadlock. Use nonblocking busy results at standalone boundaries and explicit ownership transfer to workers.

### I3. The production release packer cannot declare migration compatibility

Locations: `scripts/release_pack.py:269–277`, `apps/api/src/robopark_api/services/ops/archives.py:38`, `deploy/host/robopark_host/release.py:370–378`; shell/workflow metadata entry points in `scripts/pack-release.sh` and `.github/workflows/release.yml`.

Packer passes only Git SHA, target migration head and creation time. `migration_compatibility` is always the default `{}`. Host correctly rejects any changed Alembic head unless signed metadata explicitly authorizes compatible reversible migration from the installed head. Therefore normal published releases that add a migration cannot be installed through the promised OTA channel. E2E fixtures bypass the production packer to inject compatibility metadata.

Evidence: real packer built a signed fixture release with a new head; resulting metadata was `{}` and the real compatibility check returned `migration_incompatible` against the old head.

Expected fix: add a safe explicit packer interface, e.g. a version-controlled release-metadata JSON file selected by a dedicated argument and propagated through shell and GitHub workflow. Strictly allowlist/schema-check fields, require the declared target to equal the actual Alembic head, and require an explicit supported `from_heads` set and reviewed reversibility assertion. Do not infer that all migrations are reversible. Verify that metadata is signed and agrees across host/API/offline verification; use this interface in migration E2E tests.

### I4. Release-signing key rotation required by the design has no implementation path

Locations: `apps/api/src/robopark_api/services/ops/archives.py:42,115–118`, `deploy/host/robopark_host/release.py:27,191`, `deploy/installer/lib/install-release.py:110–115`.

All exact manifest schemas admit no next-key/activation-version transition. Updater verifies candidate and retained previous release using one unchanged installed PEM and never journals or activates a signing-key transition. Reinstall with a new key returns `key_rotation_requires_signed_update`, but no such signed update can be produced or consumed. The required key lifecycle cannot be exercised through OTA; manual key replacement also strands verification of old-key rollback material.

Evidence: schema/source parity checked across packer/API/host/offline path. Supplying rotation metadata to real manifest construction fails `invalid_manifest`; no runtime key-transition writer exists. This concerns publisher release-signing keys (design line 226), not automatic application-secret rotation, which is explicitly out of scope at line 32.

Expected fix: define signed next-key and activation-version metadata authorized by the currently trusted key; persist a crash-safe trust transition and retain the narrowly necessary old-key authority for rollback verification. Test unauthorized/wrong-key transitions, premature activation, restart during activation, and rollback across the transition. Update all verifier schemas together.

### I5. Operational archives and records accumulate without wired age/size retention

Locations: `deploy/host/robopark_host/updater.py:495–542`, `repair.py:86–109`, `apps/api/src/robopark_api/services/ops/host_bridge.py:160–173`, `deploy/host/robopark_host/commands.py:226,299–306,358`, `github_releases.py:551`.

Updater retention removes obsolete release trees/Compose files/rollback directories only. The generic cleanup helper is never called by runtime and covers legacy staging/diagnostics paths rather than all actual admission paths. Each manual preview saves a fresh full ZIP and inspection record; GitHub artifacts/sidecars, diagnostic public ZIPs and command receipts also persist. There is no enforced age/byte budget. Repeated previews and ordinary operation eventually exhaust limited host storage and can prevent updates, recovery, or SQLite writes. Tagged old production image retention is also not addressed by deleting release directories.

Evidence: traced actual archive writers and all references to `cleanup_retained`; references are definition/tests only. Design line 287 requires bounded artifact retention. Existing tests prove two release directories, not bounded operational storage.

Expected fix: wire a root-controlled retention pass with explicit owned directories and age plus byte limits. Protect active/approved candidates, current/previous release and compatible recovery material. Prune only positively owned images/artifacts. For receipts, preserve replay protection using conservative retention or durable compact tombstones rather than blindly deleting consumed-command identity. Exercise actual upload, GitHub, diagnostic, inspection and receipt directories and disk-pressure behavior in tests; surface their usage in diagnostics.

### I6. Capacity write traffic becomes no-op ORM updates after the first request

Locations: `scripts/capacity-gate.py:265`, `apps/api/src/robopark_api/routers/parks.py:72–96`.

Every write sends the same park name `capacity-isolated-load`. SQLAlchemy does not emit UPDATE for the unchanged value after the first request. The single effective write occurs in warmup, so the measured run can advertise a read/write profile while effectively measuring reads. This cannot establish the planned SQLite writer-contention capacity gate for 200 clients.

Evidence: 30 successful calls to the real park PATCH handler in isolated SQLite produced exactly **one SQL UPDATE**, counted at the engine boundary.

Expected fix: use a bounded deterministic unique value for each mutation of the harness-owned disposable park, such as a run/worker/sequence-based name. The ID must remain tied to successful fixture creation and cleanup. This produces a real DB write each time without touching user parks or creating external side effects. Test SQL-write counts after warmup rather than counting only HTTP PATCH responses. Real target capacity remains pending after this correction.

## Minor

### M1. Diagnostics/public-state contracts disagree with their producers

Locations: `deploy/host/robopark_host/doctor.py:325,614`, `updater.py:789–804`, `apps/api/src/robopark_api/services/ops/host_bridge.py:140–149`.

Doctor reads `state/updater.json`, whereas runtime writes updater journal/host-status files. No runtime writer creates the `last-backup.json` consumed by doctor/public state after successful snapshot. Rollback publishes `previous_restored`, while API accepts `rolled_back`, so the UI loses the rollback result. Health version regex also drops valid RC release versions. Align producers and readers through shared bounded schemas; test health after real snapshot, update, rollback and RC install instead of only fabricated public JSON. Otherwise warnings and missing backup/version/update status remain misleading despite successful operations.

### M2. Reloading the Royal UI during host maintenance cannot bootstrap the existing session

Locations: `apps/api/src/robopark_api/middleware/maintenance.py:52–63`, `apps/web/src/auth.tsx:30–45`.

The maintenance GET allowlist includes operation polling but omits `/auth/me`. A refreshed page's first identity request receives 503, AuthProvider sets user to null, and the protected Royal view cannot reconnect while maintenance continues. Existing mounted polling can work, so ordinary tests miss the fresh-page case. Allow an authenticated, genuinely read-only identity bootstrap with session-touch disabled (do not restore a general Royal write exemption), or preserve an explicit reconnecting state and safely retry when maintenance ends. Cover refresh/reconnect in a UI integration test.

## Verification and limitations

Fresh safe suites run in the reviewed worktree:

1. Focused signed-successor/recovery/archive security selection from `tests/host/test_end_to_end_update.py` and `tests/host/test_packaging.py`: **43 passed, 117 deselected (12.98 s)**. Selection was `signed_successor_source or both_releases_broken or symlink or bad_signature or wrong_key or tamper`.
2. `tests/host/test_capacity_harness.py`, `test_capacity_profiles.py`, `apps/api/tests/test_ops_host_bridge.py`, `test_ops_snapshot.py`: **77 passed (6.86 s)**, one existing Starlette/httpx deprecation warning.
3. Safe extra probes documented with the respective findings: dependency-aware Tuna state on update/rollback, host-lock exclusion bypass, real packer migration metadata rejection, rotation metadata rejection, real ORM write count, and local-restore/host-maintenance mismatch. These used temporary paths and generated fixture keys/recording adapters; no production secrets, remote mutation or host service control.

Passing focused suites do not negate the demonstrated integration gaps. Earlier scoped reviews provide useful confidence in strict request parsing, signed extraction, actor binding, bounded subprocess/download cleanup and root-private artifacts; they are not a substitute for the missing end-to-end contracts above. No claim of production readiness, ARM image-build success, systemd reboot correctness, live Tuna publication or 200-user capacity is made. The Task 11 checklist's real-target gates must remain explicitly unfulfilled until actually exercised.
