# SURP Work Lifecycle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace manual Tracker mutations with a durable, idempotent task lifecycle and present comments, attachments and system events as one mobile-friendly work chat.

**Architecture:** Local task state, chat messages and a generic reliable-action row are committed in one SQLite transaction. A single lease-owned background worker sends bot-authored mutations to Tracker, retries transient failures, and exposes synchronization state without blocking local work. `/work` becomes the only task dashboard and derives all status controls from explicit lifecycle commands.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2, Alembic, SQLite WAL, httpx Tracker client, React 19, TypeScript, Vitest, Playwright.

**Spec:** `docs/superpowers/specs/2026-09-15-surp-workflow-inventory-reliability-design.md`

## Global Constraints

- Product copy is `СУРП — Система управления робопарками`; developer is `tehblokdan`.
- Tracker mutations use only the configured bot token and never require a user's Tracker login.
- A user cannot choose a Tracker transition manually.
- Every mutating lifecycle command requires `Idempotency-Key` and returns the saved result on an identical retry.
- Local state and its reliable action are committed atomically before external I/O.
- Tracker failure must not block local chat, task ownership, inventory, auth or health endpoints.
- Audit records are retained; the work UI shows meaningful audit events only as timeline messages.
- SLA is exactly five elapsed hours from the Tracker timestamp at which the task entered the queued status; when Tracker lacks status history, use `created_at` and mark the source as estimated.

---

### Task 1: Durable local workflow schema

**Files:**
- Create: `apps/api/alembic/versions/0028_reliable_task_workflow.py`
- Create: `apps/api/src/robopark_api/task_workflow_models.py`
- Modify: `apps/api/src/robopark_api/collaboration_models.py`
- Modify: `apps/api/tests/test_models_migration.py`
- Create: `apps/api/tests/test_task_workflow_models.py`

**Interfaces:**
- Produces: `ReliableAction`, `TaskMessage`, `TaskAttachment`, `TaskReview`, `HiddenTask` SQLAlchemy models.
- Produces: action states `pending|sending|succeeded|retry_wait|needs_attention` and message states `saved|pending|synced|needs_attention`.
- Migrates existing `tracker_submissions` rows into `reliable_actions` with `resource_type='tracker_issue'` without deleting their result history.

- [ ] **Step 1: Write migration and model tests**

Add tests that upgrade a database at revision `0027_emergency_readings`, insert a legacy `tracker_submissions` row, upgrade to head and assert:

```python
action = session.scalar(select(ReliableAction))
assert action.resource_type == "tracker_issue"
assert action.resource_id == "SDCFLEETOPS-1"
assert action.state == "succeeded"
assert action.idempotency_key == "request-0001"
```

Also assert the following unique constraint rejects a second row:

```python
(actor_user_id, resource_type, resource_id, action, idempotency_key)
```

- [ ] **Step 2: Run the focused tests and confirm failure**

Run: `cd apps/api && uv run pytest tests/test_models_migration.py tests/test_task_workflow_models.py -q`

Expected: FAIL because revision `0028_reliable_task_workflow` and the five models do not exist.

- [ ] **Step 3: Add the schema**

Define these stable fields:

```python
class ReliableAction(Base):
    id: Mapped[str]                         # UUID string
    actor_user_id: Mapped[int]
    resource_type: Mapped[str]
    resource_id: Mapped[str]
    action: Mapped[str]
    idempotency_key: Mapped[str]
    payload_hash: Mapped[str]
    payload_json: Mapped[str]
    state: Mapped[str]
    result_json: Mapped[str | None]
    error_code: Mapped[str | None]
    attempts: Mapped[int]
    next_attempt_at: Mapped[float]
    lease_until: Mapped[float | None]
    created_at: Mapped[float]
    updated_at: Mapped[float]
```

`TaskMessage` stores `issue_key`, `kind` (`user|system|tracker`), author snapshot, text, stable external ID, synchronization state and timestamps. `TaskAttachment` stores a safe relative blob name, original name, MIME type, size and SHA-256. `TaskReview` is unique per open issue and stores `pending|returned|closed`. `HiddenTask` stores issue, park, reason, actor and timestamps with one active row per issue.

- [ ] **Step 4: Implement forward and downgrade migrations**

The upgrade creates new tables, copies legacy submissions, then drops `tracker_submissions`. The downgrade recreates `tracker_submissions`, copies only `resource_type='tracker_issue'` rows, and drops the new workflow tables in reverse FK order.

- [ ] **Step 5: Run migration/model verification**

Run: `cd apps/api && uv run pytest tests/test_models_migration.py tests/test_task_workflow_models.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add apps/api/alembic/versions/0028_reliable_task_workflow.py apps/api/src/robopark_api/task_workflow_models.py apps/api/src/robopark_api/collaboration_models.py apps/api/tests/test_models_migration.py apps/api/tests/test_task_workflow_models.py
git commit -m "feat(api): add durable task workflow state"
```

### Task 2: Generic idempotency and transactional enqueue

**Files:**
- Create: `apps/api/src/robopark_api/services/reliable_actions.py`
- Modify: `apps/api/src/robopark_api/services/tracker_submissions.py`
- Create: `apps/api/tests/test_reliable_actions.py`
- Modify: `apps/api/tests/test_tracker_collaboration.py`

**Interfaces:**
- Produces: `begin_action(db, *, actor, resource_type, resource_id, action, idempotency_key, payload) -> BeginResult`.
- Produces: `complete_action`, `schedule_retry`, `mark_needs_attention`, `claim_due_batch`.
- Consumes: `ReliableAction` from Task 1.

- [ ] **Step 1: Write failing idempotency tests**

Cover four cases: missing/short key returns 400; identical retry returns saved result; same key with changed payload returns 409; two sessions racing on the same key create exactly one row.

```python
first = begin_action(db, actor=user, resource_type="tracker_issue",
    resource_id="SDCFLEETOPS-1", action="claim", idempotency_key="claim-0001",
    payload={"owner_user_id": user.id})
second = begin_action(db, actor=user, resource_type="tracker_issue",
    resource_id="SDCFLEETOPS-1", action="claim", idempotency_key="claim-0001",
    payload={"owner_user_id": user.id})
assert first.row.id == second.row.id
```

- [ ] **Step 2: Confirm the tests fail**

Run: `cd apps/api && uv run pytest tests/test_reliable_actions.py tests/test_tracker_collaboration.py -q`

- [ ] **Step 3: Implement canonical hashing and begin semantics**

Serialize payload with `sort_keys=True`, compact separators and UTF-8; hash SHA-256. `begin_action` must only `flush()`, never commit, so callers atomically persist local state, message, audit and action.

- [ ] **Step 4: Implement leasing and retry state changes**

`claim_due_batch` reclaims expired `sending` leases, selects at most 20 due rows ordered by `next_attempt_at,id`, marks them `sending` with a 60-second lease and commits once. Retry delays are `min(300, 2 ** min(attempts, 8)) + deterministic_jitter` seconds. Authentication, missing transition and invalid payload become `needs_attention`; network, timeout, 429 and 5xx become `retry_wait`.

- [ ] **Step 5: Replace compatibility service internals**

Keep the old module importable during the refactor, but delegate its hashing/replay decisions to `reliable_actions`; remove the rule that deletes uncertain actions after 60 seconds.

- [ ] **Step 6: Verify**

Run: `cd apps/api && uv run pytest tests/test_reliable_actions.py tests/test_tracker_collaboration.py -q`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add apps/api/src/robopark_api/services/reliable_actions.py apps/api/src/robopark_api/services/tracker_submissions.py apps/api/tests/test_reliable_actions.py apps/api/tests/test_tracker_collaboration.py
git commit -m "feat(api): enqueue idempotent reliable actions"
```

### Task 3: Task timeline and staged attachments

**Files:**
- Create: `apps/api/src/robopark_api/services/task_timeline.py`
- Create: `apps/api/src/robopark_api/routers/task_timeline.py`
- Modify: `apps/api/src/robopark_api/schemas.py`
- Modify: `apps/api/src/robopark_api/main.py`
- Create: `apps/api/tests/test_task_timeline.py`
- Modify: `apps/api/tests/test_report_attachments.py`

**Interfaces:**
- Produces: `GET /tracker/issues/{key}/timeline -> list[TaskTimelineItemOut]`.
- Produces: `POST /tracker/issues/{key}/messages` and `POST /tracker/issues/{key}/message-attachments`.
- Produces: `append_system_message(db, *, issue_key, actor, text, action_id=None) -> TaskMessage`.

- [ ] **Step 1: Write failing timeline tests**

Assert one ordered response merges Tracker comments with local user/system messages, deduplicates by stable external ID, keeps attachments beside their message and exposes `sync_state` without exposing payload JSON or tokens.

- [ ] **Step 2: Write attachment safety tests**

Assert a staged upload rejects traversal names, unsupported content, empty files and files above the existing attachment limit; closing the request does not remove a successfully staged attachment.

- [ ] **Step 3: Confirm failure**

Run: `cd apps/api && uv run pytest tests/test_task_timeline.py tests/test_report_attachments.py -q`

- [ ] **Step 4: Implement timeline merge**

Use UTC timestamps and stable ordering `(created_at, source_rank, id)`. Persist imported Tracker comments as `kind='tracker'` with external IDs so refresh is idempotent. A local user message and its `ReliableAction(action='comment')` are committed together.

- [ ] **Step 5: Implement staged attachment storage**

Write blobs under the configured data directory using generated names, `O_NOFOLLOW`, mode `0600`, size/MIME validation and SHA-256. Persist `TaskAttachment` and `ReliableAction(action='attach')` in one transaction. The worker deletes a staged blob only after confirmed Tracker upload and retention expiry, not immediately after sending.

- [ ] **Step 6: Register the router and verify**

Run: `cd apps/api && uv run pytest tests/test_task_timeline.py tests/test_report_attachments.py -q`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add apps/api/src/robopark_api/services/task_timeline.py apps/api/src/robopark_api/routers/task_timeline.py apps/api/src/robopark_api/schemas.py apps/api/src/robopark_api/main.py apps/api/tests/test_task_timeline.py apps/api/tests/test_report_attachments.py
git commit -m "feat(api): add unified task timeline"
```

### Task 4: Bot outbox worker and transition resolver

**Files:**
- Create: `apps/api/src/robopark_api/services/tracker_outbox.py`
- Create: `apps/api/src/robopark_api/services/tracker_transitions.py`
- Modify: `apps/api/src/robopark_api/main.py`
- Modify: `apps/api/src/robopark_api/services/cache_cleanup.py`
- Create: `apps/api/tests/test_tracker_outbox.py`
- Create: `apps/api/tests/test_tracker_transitions.py`
- Modify: `apps/api/tests/test_cache_cleanup.py`

**Interfaces:**
- Produces: `run_tracker_outbox_loop(session_factory, stop_event, *, interval_seconds=1.0)`.
- Produces: `resolve_transition(transitions, purpose: Literal['start','review','return','close']) -> str | None`.
- Consumes: `claim_due_batch` and task timeline from Tasks 2–3.

- [ ] **Step 1: Write failing transition tests**

Use Russian and English fixtures to prove deterministic matching for `В работе/In Progress`, `Проверка/Verification`, return-to-work and final close. Assert ambiguous and absent matches return `None`, never the first arbitrary transition.

- [ ] **Step 2: Write failing worker tests**

Cover success, transient retry, permanent `needs_attention`, process restart with expired lease, duplicate delivery after upstream success, cache invalidation and graceful shutdown.

- [ ] **Step 3: Confirm failure**

Run: `cd apps/api && uv run pytest tests/test_tracker_outbox.py tests/test_tracker_transitions.py -q`

- [ ] **Step 4: Implement transition resolution**

Normalize case, `ё/е`, whitespace and punctuation. Score exact normalized ID/display above token matches. Require one unique highest score; otherwise return `None` and store `tracker_transition_missing`.

- [ ] **Step 5: Implement delivery**

Before a transition, refetch the issue and treat an already reached target status as success. Send comments as the bot using existing `_signed_tracker_text` formatting. For `attach`, upload the staged blob then add one comment with the returned temporary attachment ID. Every success appends/updates the matching timeline message and invalidates issue/comments/list caches.

- [ ] **Step 6: Start one worker under the existing lifespan job lease**

Add the loop beside session/cache cleanup only when this API worker owns `JobLease`. Shutdown sets the shared stop event and awaits the task.

- [ ] **Step 7: Add bounded retention**

Delete successful reliable actions older than 30 days and uploaded staged blobs older than 7 days; keep `needs_attention`, audit records and task messages. Limit each cleanup batch to 500 rows.

- [ ] **Step 8: Verify**

Run: `cd apps/api && uv run pytest tests/test_tracker_outbox.py tests/test_tracker_transitions.py tests/test_cache_cleanup.py -q`

Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add apps/api/src/robopark_api/services/tracker_outbox.py apps/api/src/robopark_api/services/tracker_transitions.py apps/api/src/robopark_api/main.py apps/api/src/robopark_api/services/cache_cleanup.py apps/api/tests/test_tracker_outbox.py apps/api/tests/test_tracker_transitions.py apps/api/tests/test_cache_cleanup.py
git commit -m "feat(api): deliver Tracker actions asynchronously"
```

### Task 5: Automatic lifecycle commands

**Files:**
- Create: `apps/api/src/robopark_api/services/task_lifecycle.py`
- Modify: `apps/api/src/robopark_api/routers/tracker_actions.py`
- Modify: `apps/api/src/robopark_api/routers/tracker_read.py`
- Modify: `apps/api/src/robopark_api/services/tracker_claims.py`
- Modify: `apps/api/src/robopark_api/services/reports.py`
- Modify: `apps/api/src/robopark_api/schemas.py`
- Modify: `apps/api/tests/test_tracker_actions.py`
- Modify: `apps/api/tests/test_tracker_claims.py`
- Create: `apps/api/tests/test_task_lifecycle.py`

**Interfaces:**
- Produces: commands `claim`, `handoff`, `submit_review`, `return_review`, `approve_review`, `hide`, `restore`.
- Produces: `TrackerActionOut.sync_state` and `TrackerIssueDetailOut.workflow`.
- Consumes: reliable actions, transition resolver, timeline and review models.

- [ ] **Step 1: Write failing claim tests**

Assert claim commits `TrackerClaim`, system message and `ReliableAction(action='start')` atomically; two identical calls create one of each; taking over from a shiftmate changes owner once and records both usernames.

- [ ] **Step 2: Write failing review tests**

Assert a mechanic cannot final-close. `submit_review` creates one pending `TaskReview`, enqueues review transition and operator mention, and retains the claim. Operator `return_review` sets returned, enqueues a reason, and leaves/reassigns the mechanic. Operator `approve_review` enqueues final close, closes review and releases the claim only after local state is committed.

- [ ] **Step 3: Write failing hide/restore tests**

Assert only admin/royal can hide or restore; hidden tasks disappear from normal list/detail access, do not affect Tracker, and retain reason/actor in audit.

- [ ] **Step 4: Confirm failure**

Run: `cd apps/api && uv run pytest tests/test_task_lifecycle.py tests/test_tracker_actions.py tests/test_tracker_claims.py -q`

- [ ] **Step 5: Implement transaction-safe claim helpers**

Remove internal commits from `claim_issue`/`release_claim`; add `flush`-based variants called inside lifecycle transactions. Legacy assign/close endpoints delegate to `claim`/`submit_review` during one release, then are removed from the web client in Task 7.

- [ ] **Step 6: Implement lifecycle endpoints**

Use explicit routes:

```text
POST /tracker/issues/{key}/claim
POST /tracker/issues/{key}/handoff
POST /tracker/issues/{key}/submit-review
POST /tracker/issues/{key}/review/return
POST /tracker/issues/{key}/review/approve
POST /tracker/issues/{key}/hide
DELETE /tracker/issues/{key}/hide
```

Each route requires `Idempotency-Key`, uses the existing per-task mutation lease and returns the locally committed workflow plus `sync_state`.

- [ ] **Step 7: Expose workflow and filter hidden tasks**

Return owner, review state, local display status, synchronization summary, queued timestamp/source and manager-only hidden metadata. Apply hidden filtering after scope enforcement and before pagination counts.

- [ ] **Step 8: Verify**

Run: `cd apps/api && uv run pytest tests/test_task_lifecycle.py tests/test_tracker_actions.py tests/test_tracker_claims.py tests/test_tracker_read.py -q`

Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add apps/api/src/robopark_api/services/task_lifecycle.py apps/api/src/robopark_api/routers/tracker_actions.py apps/api/src/robopark_api/routers/tracker_read.py apps/api/src/robopark_api/services/tracker_claims.py apps/api/src/robopark_api/services/reports.py apps/api/src/robopark_api/schemas.py apps/api/tests/test_task_lifecycle.py apps/api/tests/test_tracker_actions.py apps/api/tests/test_tracker_claims.py
git commit -m "feat(api): automate task lifecycle"
```

### Task 6: Five-hour SLA and stable work ordering

**Files:**
- Modify: `apps/api/src/robopark_api/services/tracker_client.py`
- Modify: `apps/api/src/robopark_api/routers/tracker_read.py`
- Modify: `apps/api/src/robopark_api/schemas.py`
- Modify: `apps/web/src/api.ts`
- Modify: `apps/web/src/domains/work/workData.ts`
- Create: `apps/web/src/domains/work/RepairSla.tsx`
- Create: `apps/web/src/domains/work/RepairSla.test.tsx`
- Modify: `apps/web/src/domains/work/workData.test.ts`
- Modify: `apps/api/tests/test_tracker_read.py`

**Interfaces:**
- Produces: `queued_at`, `sla_deadline`, `sla_source: 'status_history'|'estimated'` on tracker issue DTOs.
- Produces: `<RepairSla deadline source now?>`.

- [ ] **Step 1: Write failing API and ordering tests**

Assert status history timestamp is preferred; fallback is `created_at`; deadline is exactly `queued_at + 5h`; missing timestamps yield null rather than fabricated zero. Verify ordering uses queued timestamp and keeps stable input order for ties.

- [ ] **Step 2: Write failing visual timer tests**

At fixed time, assert `Осталось 1 ч 30 мин`, `Просрочено на 20 мин`, and `Срок рассчитан приблизительно` for estimated source. The component must expose one status string to assistive technology and avoid per-card intervals.

- [ ] **Step 3: Confirm failure**

Run: `cd apps/api && uv run pytest tests/test_tracker_read.py -q && cd ../web && npm test -- --run src/domains/work/workData.test.ts src/domains/work/RepairSla.test.tsx`

- [ ] **Step 4: Implement server timestamps and client timer**

Use a single minute clock in `WorkPage`, pass `now` to cards, and update at the next minute boundary. Do not create a timer per task. Sort server query oldest-first and preserve the client boundary sort for malformed upstream pages.

- [ ] **Step 5: Verify and commit**

Run the commands from Step 3; expect PASS.

```bash
git add apps/api/src/robopark_api/services/tracker_client.py apps/api/src/robopark_api/routers/tracker_read.py apps/api/src/robopark_api/schemas.py apps/api/tests/test_tracker_read.py apps/web/src/api.ts apps/web/src/domains/work/workData.ts apps/web/src/domains/work/workData.test.ts apps/web/src/domains/work/RepairSla.tsx apps/web/src/domains/work/RepairSla.test.tsx
git commit -m "feat(work): show five-hour repair SLA"
```

### Task 7: Unified work chat and action forms

**Files:**
- Create: `apps/web/src/domains/work/TaskTimeline.tsx`
- Create: `apps/web/src/domains/work/TaskTimeline.test.tsx`
- Create: `apps/web/src/domains/work/TaskSyncStatus.tsx`
- Create: `apps/web/src/domains/work/TaskSyncStatus.test.tsx`
- Modify: `apps/web/src/api.ts`
- Modify: `apps/web/src/domains/work/IssueWorkbench.tsx`
- Modify: `apps/web/src/components/tracker/IssueActionsPanel.tsx`
- Modify: `apps/web/src/domains/inventory/TaskPartsPanel.tsx`
- Modify: `apps/web/src/components/tracker/TaskCollaboration.tsx`
- Modify: `apps/web/src/domains/work/IssueWorkbench.test.tsx`
- Modify: `apps/web/src/components/tracker/IssueActionsPanel.test.tsx`

**Interfaces:**
- Consumes: timeline and lifecycle endpoints from Tasks 3 and 5.
- Produces: one task chat, comment composer, nested parts/handoff disclosures and role-derived lifecycle controls.

- [ ] **Step 1: Write failing component tests**

Assert chronological rendering of user, bot and system messages with attachments; no heading/button named «История действий»; synchronization badges use `Сохранено`, `Отправляется в Tracker`, `Требует внимания`.

- [ ] **Step 2: Write failing action-layout tests**

Assert the comment form is followed by closed disclosures «Заказать запчасть» then «Передать смену». Assert no manual transitions, no separate Tracker link, mechanic sees «Передать на проверку», and operator sees return/approve only for pending review.

- [ ] **Step 3: Confirm failure**

Run: `cd apps/web && npm test -- --run src/domains/work/TaskTimeline.test.tsx src/domains/work/TaskSyncStatus.test.tsx src/domains/work/IssueWorkbench.test.tsx src/components/tracker/IssueActionsPanel.test.tsx`

- [ ] **Step 4: Implement API types and timeline**

Add typed methods for timeline, message, attachment and lifecycle routes. Render attachment links inside their message. Refresh immediately from local API after mutation; background polling is visibility-aware and coalesced through the existing resource store.

- [ ] **Step 5: Simplify the task body**

Remove the old significant-comment/history split, manual transition disclosure and external button. Keep the ticket number itself linked. Remove author, created, updated, tags and queue from the visible detail metadata. Preserve robot, local status, owner and SLA.

- [ ] **Step 6: Embed parts and handoff below the composer**

Rename «Использовать запчасть» to «Заказать запчасть» in this context. Keep both panels closed initially on phone and desktop. Their successful actions append system messages and refresh the same timeline.

- [ ] **Step 7: Verify and commit**

Run the command from Step 3; expect PASS.

```bash
git add apps/web/src/api.ts apps/web/src/domains/work/TaskTimeline.tsx apps/web/src/domains/work/TaskTimeline.test.tsx apps/web/src/domains/work/TaskSyncStatus.tsx apps/web/src/domains/work/TaskSyncStatus.test.tsx apps/web/src/domains/work/IssueWorkbench.tsx apps/web/src/domains/work/IssueWorkbench.test.tsx apps/web/src/components/tracker/IssueActionsPanel.tsx apps/web/src/components/tracker/IssueActionsPanel.test.tsx apps/web/src/domains/inventory/TaskPartsPanel.tsx apps/web/src/components/tracker/TaskCollaboration.tsx
git commit -m "feat(work): unify task chat and actions"
```

### Task 8: Merge dashboard into Work and update branding/navigation

**Files:**
- Modify: `apps/web/src/app/routing/routeManifest.ts`
- Modify: `apps/web/src/app/routing/AppRouter.tsx`
- Modify: `apps/web/src/domains/work/WorkPage.tsx`
- Modify: `apps/web/src/domains/work/WorkFilters.tsx`
- Modify: `apps/web/src/domains/work/workUrl.ts`
- Modify: `apps/web/src/i18n/ru.ts`
- Modify: `apps/web/index.html`
- Modify: `apps/web/src/components/ScreenshotGuard/screenshotGuardLogic.ts`
- Modify: `apps/web/src/app/routing/routeManifest.test.ts`
- Modify: `apps/web/src/domains/work/WorkPage.test.tsx`
- Modify: `apps/web/src/domains/work/WorkFilters.test.tsx`
- Modify: `apps/web/src/app/shell/AppShell.test.tsx`

**Interfaces:**
- Consumes: workflow/SLA DTOs from Tasks 5–6.
- Produces: one `/work` operational route, queued-first view, pinned owned tasks and final product copy.

- [ ] **Step 1: Write failing routing and branding tests**

Assert `/overview`, `/dashboard`, `/operator` and the old admin Tracker dashboard redirect to `/work` with `park` retained. Assert no navigation item named «Рабочий стол Startrek». Assert title and shell display `СУРП`, expanded name in accessible text and `tehblokdan` in the about/footer surface.

- [ ] **Step 2: Write failing work-list tests**

Assert default URL state is `status=queued`; no manual status selector is rendered; owned active tasks are pinned above queue results and deduplicated; hidden tasks are absent; operator mobile priorities are Overview/Work/Robots/Campaigns.

- [ ] **Step 3: Confirm failure**

Run: `cd apps/web && npm test -- --run src/app/routing/routeManifest.test.ts src/domains/work/WorkPage.test.tsx src/domains/work/WorkFilters.test.tsx src/app/shell/AppShell.test.tsx`

- [ ] **Step 4: Move the useful dashboard summary**

Render a compact, collapsible summary above the work split view using existing dashboard data. It must not trigger extra Tracker queries when collapsed and must use cached local/summary endpoints when open.

- [ ] **Step 5: Replace status selection with fixed queue context**

Keep robot/assignee/deep-link restrictions and reset behavior, but remove the status `<select>`. Non-default status URLs remain accepted for notification links and manager diagnostics without becoming a user-facing manual control.

- [ ] **Step 6: Pin owned tasks and update navigation**

Fetch owned active tasks through one server query independent of current park/status filters, scope them by permissions, and merge/deduplicate by issue key. Update operator mobile priorities to the approved order.

- [ ] **Step 7: Apply branding and verify**

Run the command from Step 3 plus `cd apps/web && npm run check:nav && npm run build`.

Expected: PASS and production build succeeds.

- [ ] **Step 8: Commit**

```bash
git add apps/web/src/app/routing/routeManifest.ts apps/web/src/app/routing/AppRouter.tsx apps/web/src/domains/work/WorkPage.tsx apps/web/src/domains/work/WorkFilters.tsx apps/web/src/domains/work/workUrl.ts apps/web/src/i18n/ru.ts apps/web/index.html apps/web/src/components/ScreenshotGuard/screenshotGuardLogic.ts apps/web/src/app/routing/routeManifest.test.ts apps/web/src/domains/work/WorkPage.test.tsx apps/web/src/domains/work/WorkFilters.test.tsx apps/web/src/app/shell/AppShell.test.tsx
git commit -m "feat(web): make Work the SURP operations home"
```

### Task 9: Full lifecycle browser and resilience verification

**Files:**
- Modify: `apps/api/tests/browser_diagnostic_bridge.py`
- Create: `apps/web/e2e/operational/task-lifecycle.spec.ts`
- Modify: `apps/web/e2e/operational/task-card.spec.ts`
- Modify: `apps/web/e2e/operational/task-collaboration.spec.ts`
- Create: `apps/api/tests/test_task_workflow_load.py`
- Modify: `scripts/capacity_benchmark.py`
- Create: `docs/capacity/2026-09-15-task-workflow-200-records.json`

**Interfaces:**
- Exercises all public contracts produced by Tasks 1–8.
- Produces reproducible evidence for desktop, mobile, retries and 200-session load.

- [ ] **Step 1: Add a deterministic bot/Tracker test bridge**

Expose test-only controls for Tracker availability, transition fixtures and delivery counts. Never enable these controls outside the existing test environment guard.

- [ ] **Step 2: Write the full browser lifecycle**

Cover mechanic claim, duplicate click, comment/photo, parts disclosure, handoff, submit review, operator return, resubmit and operator approve. Assert the visible timeline order and that each upstream operation count is one.

- [ ] **Step 3: Add mobile and accessibility coverage**

Run at 390×844 and desktop. Verify action order, no horizontal overflow, 44px touch targets, focus after disclosure open, keyboard submission and accessible sync announcements.

- [ ] **Step 4: Add failure/recovery browser coverage**

Disable Tracker, claim locally, assert `Отправляется в Tracker`, restore Tracker, wait for `Сохранено`, reload the page and assert one transition and retained timeline.

- [ ] **Step 5: Add API load coverage**

Simulate 200 authenticated sessions with a mix of list, detail, timeline and duplicate claims. Assert local endpoint p95 stays within the repository capacity threshold, worker backlog drains, and upstream calls are bounded by unique action IDs rather than session count.

- [ ] **Step 6: Run focused browser and load tests**

Run:

```bash
cd apps/web
npx playwright test e2e/operational/task-lifecycle.spec.ts e2e/operational/task-card.spec.ts e2e/operational/task-collaboration.spec.ts
cd ../api
uv run pytest tests/test_task_workflow_load.py -q
```

Expected: PASS.

- [ ] **Step 7: Run full verification**

Run:

```bash
./scripts/verify.sh api
cd apps/web && npm run lint && npm test -- --run && npm run check:nav && npm run build
```

Expected: all API and web tests pass, lint/nav checks are clean, and production build succeeds.

- [ ] **Step 8: Commit verification evidence**

```bash
git add apps/api/tests/browser_diagnostic_bridge.py apps/api/tests/test_task_workflow_load.py apps/web/e2e/operational/task-lifecycle.spec.ts apps/web/e2e/operational/task-card.spec.ts apps/web/e2e/operational/task-collaboration.spec.ts scripts/capacity_benchmark.py docs/capacity/2026-09-15-task-workflow-200-records.json
git commit -m "test: verify SURP task lifecycle under load"
```

## Completion Gate

Before starting the inventory plan, verify the migrated copy of a real `0.1.33` database upgrades and rolls back to the pre-cutover snapshot; verify the bot transition mapping against the configured production queue; and obtain an independent code review of authorization, idempotency, attachment storage and worker recovery.
