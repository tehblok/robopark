# Robopark 0.2.0-rc.6: client-first reliability and managed host design

Date: 2026-09-24

Status: approved in conversation; implementation plan pending

Source branch: `codex/classic-system-overhaul`

Source commit: `e175a92b2021f96fcafe7715a06fe6f95be36539`

Target release: `0.2.0-rc.6`

## 1. Purpose

Prepare one coherent release based on the current `e175a92` code, then merge it
into `main` and build a signed OTA archive. The release must keep the interface
responsive for 200 simultaneous users, remain usable when Tracker or Wi-Fi is
slow, expose system maintenance through a safe royal-only interface, and correct
the release-contract mismatch that would otherwise make the OTA unsafe.

The installed host is not the development source of truth. It currently reports
`0.2.0-rc.5`, channel `stable`, build `ea4123a0980dc14da158`. The release being
designed is based only on `e175a92`; host mutation happens only after the branch is
merged, verified, signed and packaged.

## 2. Success criteria

- The target release is `0.2.0-rc.6`, published on channel `rc`.
- One archive supports an upgrade from installed `0.2.0-rc.5` and a clean install.
- Release metadata, migration policy and runtime all declare Alembic head
  `0038_inventory_photo_cleanup`.
- Browser actions appear immediately without page reloads; safe operations survive
  offline use and resume automatically.
- API request handling no longer owns long-running background jobs.
- Tracker polling, outbox delivery, push delivery, cleanup and metrics run in one
  dedicated `robopark-worker` service.
- PostgreSQL-backed idempotency prevents duplicate claims, stock changes and
  external Tracker mutations.
- Royal can observe and operate the host through typed, audited operations without
  receiving an arbitrary root shell.
- The same client-first behavior works in an ordinary HTTPS browser tab and in an
  installed PWA.
- The interface is checked on Chromium, Firefox and WebKit across Windows, macOS,
  Android and iPhone layouts.
- The system is designed for 200 simultaneously active sessions.

## 3. Scope and non-goals

### In scope

- Release contract and migration-head correction.
- Dedicated background worker service and health projection.
- Client-first projections, delta refresh and safe offline queue.
- Durable notification delivery and schedule-aware routing.
- Royal system monitoring, maintenance and storage management.
- Encrypted backup to the host disk and selected USB storage.
- TOTP protection for dangerous system operations.
- Unified retention and client-storage policies.
- Full classic-interface review on desktop and mobile.
- Camera and barcode scanning fallback.
- Signed OTA packaging after merge to `main`.

### Not in scope

- Tracker webhooks. The available Tracker integration cannot provide a suitable
  direct webhook, so bounded polling remains the source of external changes.
- A working Telegram bot. Release `rc.6` provides a neutral notification-channel
  contract only; Telegram remains a future consumer.
- Outsource-specific roles or employment types. All users are internal for this
  system.
- An arbitrary remote Linux terminal.
- Forced installation on the host as part of archive creation.
- Long soak or 200-user load execution without separate user approval.
- A second visual interface. Interface A remains removed.

## 4. Target architecture

### 4.1 Client

The React application uses the same client-first runtime in an installed PWA and
in a normal HTTPS browser tab. The PWA install changes presentation and storage
durability, not product capabilities.

The client owns:

- a versioned application shell in CacheStorage;
- account-and-park-scoped projections in IndexedDB;
- an offline action queue with stable client action IDs;
- local media previews and resumable-upload state;
- lightweight preferences in localStorage;
- a bounded in-memory resource cache.

Private API responses are not stored in a shared HTTP cache. Unsupported or denied
browser storage causes a graceful network-only fallback rather than a broken page.

### 4.2 API

FastAPI authenticates requests, enforces role and park scope, validates actions and
persists them transactionally. It returns the local authoritative result quickly
and does not wait for Tracker delivery. It does not own long-running polling,
cleanup, push or outbox loops.

### 4.3 Worker

A single `robopark-worker` container uses the API image and PostgreSQL database. It
runs:

- Tracker notification and closure polling;
- ReliableAction outbox delivery;
- durable Web Push delivery;
- system notification generation;
- retention and cache cleanup;
- system metric collection and aggregation;
- maintenance scheduling and health projection.

PostgreSQL leases remain defence-in-depth against accidental duplicate workers.
The worker has a separate readiness and liveness projection. A failed worker must
not make the read-only site unavailable; the interface instead reports delayed
synchronisation.

### 4.4 PostgreSQL

PostgreSQL is authoritative for users, roles, parks, claims, local task workflow,
inventory, reports, schedules, notifications, audit, outbox, delivery attempts,
maintenance operations and monitoring aggregates.

### 4.5 Tracker

Tracker remains authoritative for the external ticket status, transitions, fields,
comments and final attachments. The worker polls every 15 seconds while users or
active tasks exist, and every 60 seconds when the system is quiet. Retry backoff is
bounded at five minutes. A user mutation triggers a targeted fresh read when that
read is required for correctness.

### 4.6 Host agent

A root-owned host agent accepts only typed operations defined by strict schemas.
It never evaluates user-provided shell text. Its operation IDs are idempotent and
its progress is durable. Supported operation families are:

- application update, reinstall and rollback;
- operating-system package inspection and update;
- service restart and full-host reboot;
- backup creation, verification and restoration;
- safe and category-specific cleanup;
- diagnostics collection;
- disk and USB preparation;
- resource-limit and retention configuration.

## 5. Client-first data flow

The normal mutation flow is:

`UI -> local projection -> client batch -> API transaction -> PostgreSQL outbox -> worker -> Tracker`

The UI applies an optimistic local projection in the next render. The client sends
a small batch immediately after a 100-250 ms coalescing window. The API either
accepts the batch transactionally or returns a conflict that restores the affected
projection.

No permanent “Saved” badge is shown. The interface displays status only when the
normal path is incomplete:

- animated neutral marker: sending to Robopark;
- small clock/cloud marker: saved locally and waiting for Tracker;
- visible warning: action needs attention.

Claims, inventory mutations, handoff and review submission become final only after
the Robopark API accepts them. Comments, photo previews, defect-code drafts, report
drafts and handoff drafts may be created offline.

## 6. Task workflow

### 6.1 Claim

Claim is mechanic-only and guarded by the task mutation lock and an idempotency
key. The server performs or queues this exact dependency chain:

1. validate fresh Tracker state and local scope;
2. resolve and assign the active park operator;
3. ensure tag `diag_complete`;
4. if `components` is empty, ensure `ROBOT_SUSPENSION`;
5. transition to the semantic `start` / “In progress” state;
6. activate the local mechanic claim only when prerequisites allow it.

Repeated claim requests return the existing result. A different active owner
causes a conflict rather than reassignment.

### 6.2 Work and inventory

Chat, photos and drafts appear immediately. A stock write-off requires an active
claim and server acknowledgement. Inventory decrement and its idempotency receipt
commit in one database transaction. The part form collapses after success and shows
a short confirmation without reloading the task.

Handoff requires the current mechanic owner, a target mechanic and structured work
context. It atomically transfers the local claim and queues a human-readable
Tracker note.

### 6.3 Review

Submitting for review requires:

- the current active mechanic claim;
- a current-cycle work comment, with an optional replacement comment;
- one valid defect code;
- exactly one completion photo;
- an active operator for the park.

The local review becomes `pending`, the operator receives a durable notification,
and the outbox uploads the image, updates `theDefectCode`, writes the comment and
performs the semantic review transition.

The operator may return the task only with a reason. Acceptance changes the local
display to `closing`; final `closed` is set only after a fresh Tracker read reports
`closed`, `resolved` or `cancelled`. Direct external closure is reconciled by the
worker and releases stale local claims.

### 6.4 Chat and audit

Mechanic and operator chat shows only human messages, images and concise business
events such as claim, write-off, review submission and return reason. It never
shows action IDs, outbox states, transition names or retry diagnostics.

Technical events remain in an immutable audit and task diagnostics view available
to admin and royal. Existing bot-specific internal names are migrated toward
neutral `system_event` or `tracker_note` terminology.

## 7. Durable notifications and schedules

Notification creation and network delivery are separate. A `NotificationEvent` is
committed before delivery attempts are scheduled. Delivery state, attempts,
backoff, expiry and provider error category are durable. Restarting the worker does
not lose an eligible notification.

Current channels are in-app and Web Push. A neutral channel-consumer interface
allows a future Telegram consumer without coupling it to request handling.

Routing rules:

- mechanic and driver operational notifications apply during their shift and park;
- operator notifications cover new work, returns, operator comments and review
  tasks during their shift;
- admin receives reports, anomalies and integration problems according to scope;
- royal receives reports, anomalies, server, release and system problems even
  outside a shift;
- critical security events reach admin and royal regardless of schedule.

Schedule precedence is:

1. an explicit schedule entry or pattern;
2. an explicitly started shift;
3. a daily fallback window from 09:00 to 21:00.

The server uses the last recorded IANA timezone from the device. It does not need
geolocation permission. A configured 4-on/4-off pattern sends shift-restricted
notifications only on the generated working days.

In-app notifications are retained for 30 days. Web Push attempts expire after
24 hours or immediately when the underlying event is no longer actionable.

## 8. Media and browser storage

The camera prefers the rear device. The scanning pipeline is:

1. native `BarcodeDetector` when available;
2. a lazily loaded JavaScript decoder when unavailable;
3. image upload from the gallery;
4. manual entry.

The application explains HTTPS, permission, busy-device, absent-camera and
decode-failure states. Media streams and workers are always released on exit.

Photo processing keeps the original locally until server acknowledgement, creates
an upload copy capped at 1920 pixels on the long edge at about 82% quality, and
uses resumable chunks. Failure to compress falls back to the original. QR and
document modes avoid destructive compression. After confirmed Tracker delivery,
the original and staged server file are removed; a bounded preview may remain for
14 days.

Active work, current robots, schedules, notifications and 14 days of recent task
history are available from local projections. Storage limits derive from browser
quota. Unsynchronised actions and their required media are never evicted.

## 9. Monitoring and system operations

### 9.1 Royal system page

The page presents user language first and technical detail on demand. It includes:

- current online users and breakdown by role and park;
- seven-day active-user graph;
- CPU, memory, disk, PostgreSQL and container state;
- API latency and error rate;
- Tracker poll age, outbox depth and oldest action age;
- worker, push, Tuna and internet state;
- database, attachment, log and cache growth;
- current version, Git SHA, build ID, migration head and update channel;
- last backup, cleanup, restart, update and incident.

Online means a heartbeat observed in the last two minutes. Raw metrics are stored
for 24 hours and five-minute aggregates for seven days.

Admin may view operational status and retry eligible failed actions. Royal may
also update, clean, back up, restore, restart and configure the host.

### 9.2 Dangerous-operation security

Dangerous host operations require:

- an active royal role;
- password reauthentication;
- a configured TOTP factor;
- a valid TOTP or recovery code;
- a typed confirmation phrase where destructive;
- a fresh operation ID;
- a complete immutable audit record.

Normal product work remains available before TOTP enrolment, but dangerous system
actions are blocked. Enrolment creates ten one-time recovery codes. The last royal
must retain a viable recovery path. TOTP secrets are encrypted at rest and recovery
codes are stored only as one-way hashes.

### 9.3 Maintenance and hangs

The default maintenance window is 02:00-05:00 in the host timezone. Security
updates may be prepared automatically. Full-host reboot always requires royal
confirmation. Service watchdogs use consecutive failures and bounded restart
budgets; they restart only the affected service and notify royal on repeated
failure.

The product never uses routine Linux `drop_caches`. It bounds application caches,
tracks RSS, CPU, descriptors, threads, database pools and queue age, and treats a
leak or deadlock as a service-health problem rather than a page-cache problem.

## 10. Retention and cleanup

Default server retention is:

| Data | Default |
| --- | --- |
| Unsynchronised actions and required media | Never automatically delete |
| Confirmed staged attachments | 48 hours |
| Closed task and chat cache | 14 days |
| Confirmed report files | 14 days |
| Lightweight report metadata | 90 days |
| In-app notifications | 30 days |
| Successful outbox records | 7 days |
| Failed actions | Until resolved plus 30 days |
| Raw metrics | 24 hours |
| Five-minute metric aggregates | 7 days |
| Logs | 14 days or 256 MiB |
| Verified backups | Latest three; copies older than 14 days may be removed only while a verified copy remains on each configured medium |
| Releases | Current, previous and recovery |

Users, roles, parks, inventory, schedules and active audit records are not removed
by automatic cleanup.

Royal receives both a one-click safe cleanup and category-specific cleanup. Every
cleanup first computes a preview. A large cleanup checks for a verified backup.
Referenced current, previous, recovery, incomplete-operation and unsynchronised
objects are protected.

## 11. Backup and recovery

Runtime snapshots live on the host disk. Independent encrypted backups are copied
to a royal-selected USB device identified by UUID or filesystem label. Missing USB
storage produces an alert but does not stop normal operation.

USB preparation and formatting are available through the host agent only after
double confirmation and exact-device validation. Formatting never accepts an
unresolved device glob or generic path.

The first backup setup creates an independent recovery key and offers a recovery
file for offline storage or printing. The backup does not contain its own recovery
key. Restoring on a new host requires the recovery file and royal authentication.

## 12. Release and update contract

The canonical version becomes `0.2.0-rc.6`. The active channel becomes `rc` after
the manual update; royal may later choose `stable`.

All of the following must agree before packaging:

- root `VERSION`;
- web and API package identities;
- release manifest and detached metadata;
- release notes target;
- Alembic current head;
- migration policy target and known heads;
- runtime release-status projection.

The target head is `0038_inventory_photo_cleanup`. Upgrade compatibility includes
the known source heads `0036_audit_remediation_state`,
`0037_claim_workflow_visibility` and `0038_inventory_photo_cleanup`. Unknown or
multiple heads fail before host or database mutation.

The release uses the existing signing key trusted by the installed host. The
installer compares the trusted public-key fingerprint before stopping services.
Key rotation is not part of `rc.6`.

Update transaction:

1. verify signature, identity, compatibility and resources;
2. stage and build the candidate separately;
3. run read-only readiness checks;
4. quiesce new writes;
5. create and verify the PostgreSQL snapshot;
6. migrate and start the candidate;
7. run database, API, web, worker, login and Tracker-read smoke checks;
8. publish the candidate and resume writes.

Any failure before writes resume automatically restores the snapshot and previous
release. After writes resume, database rollback is royal-controlled because an
automatic rollback could lose newly accepted actions. The durable journal resumes
an interrupted update after power loss.

## 13. Interface acceptance

Release `rc.6` contains only the improved classic interface with:

- system, light and dark themes;
- comfortable and compact densities;
- shared buttons, tabs, forms, panels, dialogs, spacing and radius tokens;
- responsive layouts without horizontal clipping or page reloads after mutation.

Supported product platforms are Windows, macOS, Android and iPhone. Automated
browser coverage uses:

- Chromium for Chrome and Yandex Browser behavior;
- Firefox for Windows and desktop Firefox;
- WebKit for Safari and all iPhone browsers.

The full role/theme/density/viewport matrix runs on Chromium. Firefox and WebKit
run critical journeys and geometry checks. Representative viewports are 360, 390,
412, 768, 1024, 1366 and 1920 pixels. Real-device smoke includes at least one
OnePlus device and one iPhone before host installation.

## 14. Performance targets

The design target is 200 simultaneous active sessions.

- revisiting an already cached screen: no more than 100 ms target;
- optimistic visible mutation: next render frame, without reload;
- Robopark API mutation acknowledgement: p95 no more than 500 ms;
- warm ordinary API read: p95 no more than 300 ms;
- cold working-screen load: p95 no more than 2 seconds;
- unexpected error rate under the approved load test: below 1%;
- zero lost accepted actions;
- Tracker polling and external traffic remain bounded independently of user count;
- host memory reaches a steady bound rather than growing indefinitely.

Runtime limits are selected from detected resources. The 8 GiB profile remains
conservative; larger Orin hosts receive higher concurrency without a different
application build. Hardware acceleration is optional and must not be required for
correctness.

## 15. Delivery and verification

Implementation occurs as independent test-first commits on
`codex/classic-system-overhaul`, beginning at `e175a92`.

Required short gates include focused API tests, web unit tests, type checking,
production build, release-contract checks, migration tests, worker lifecycle tests
and the agreed browser compatibility set. An independent code review follows.

After the branch passes its short gates:

1. merge it into `main`;
2. build the signed archive from the exact clean `main` SHA;
3. verify the archive with the standalone verifier and trusted public key;
4. record SHA, migration head, manifest digest and artifact digest;
5. provide the archive without installing it automatically.

The 200-session benchmark, long soak, destructive installer rehearsal and live
host update require separate user approval. Historical evidence is never treated
as evidence for the new source SHA.

## 16. Failure behavior

- Tracker unavailable: local accepted work remains visible; outbox retries; user
  sees only a quiet waiting indicator.
- Permanent Tracker rejection: action becomes `needs_attention`; admin or royal
  sees the reason and may retry after correcting the cause.
- Worker unavailable: API remains available; the system page and user indicator
  show delayed synchronization.
- PostgreSQL unavailable: mutations fail closed; the client keeps safe drafts and
  retries only after connectivity returns.
- Storage quota exhausted: evict confirmed old previews and closed projections;
  never evict unsynchronised actions.
- Push provider unavailable: in-app event remains durable and network delivery
  resumes from PostgreSQL.
- USB missing: local verified backup remains; royal is alerted.
- Update interrupted: durable journal resumes or restores before writes resume.

## 17. Explicit consistency decisions

- `e175a92` is the implementation base; the installed host is not.
- `rc.6` is a prerelease and therefore uses channel `rc`.
- Tracker has no direct webhook in this release.
- The classic interface is the only interface mode.
- All users are internal; no outsource dimension is added.
- No arbitrary shell is exposed through the site.
- Client-first does not mean unsafe local-only completion of claims, inventory or
  review submission.
- Final task closure is always reconciled from Tracker.
