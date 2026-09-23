# Task workflows, media and inventory implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make mechanic claims, operator review, task photos and privileged inventory corrections reliable, visible and idempotent.

**Architecture:** A claim is a dependency-ordered group of durable `ReliableAction` rows and a pending local reservation. Tracker mutations run assignment → tag → component → transition and activate the local mechanic claim only after the final step succeeds. Timeline visibility and local media delivery are explicit server contracts; inventory photos and permanent deletion use the existing managed storage and transactional inventory models.

**Tech Stack:** FastAPI, SQLAlchemy/Alembic, PostgreSQL/SQLite tests, React 19, TypeScript, Vitest.

**Spec:** `docs/superpowers/specs/2026-09-23-classic-workflows-inventory-schedule-design.md`

## Global Constraints

- Tracker assignee is the selected operator; local task owner remains the mechanic.
- Claim steps run in this exact order: assign operator, add `diag_complete`, ensure `ROBOT_SUSPENSION`, start transition.
- A repeated request with the same idempotency key never duplicates a remote or inventory mutation.
- Mechanics never receive raw integration/audit messages.
- Protected attachments are authenticated and are never placed in the public service-worker shell cache.
- Royal may permanently delete inventory data across parks; admin only inside assigned parks.
- Run only targeted tests; no soak, full release build or OTA.

## Review Focus

- Two mechanics claim the same issue concurrently: one reservation wins and only one remote workflow is created (Task 2 test).
- Tracker fails after assignment but before transition: retry resumes at the tag step and does not reassign or double-transition (Task 2 test).
- Park has no active operator or the operator lacks a Tracker identity: claim fails before any durable mutation (Task 2 test).
- A mechanic requests a technical-only timeline item or another issue's local attachment: it is absent/forbidden (Task 4 test).
- Permanent component deletion with stock, movements and count lines either removes the full local graph or leaves all rows intact (Task 6 test).

---

### Task 1: Persist claim state and timeline visibility

**Files:**
- Create: `apps/api/alembic/versions/0037_claim_workflow_visibility.py`
- Modify: `apps/api/src/robopark_api/collaboration_models.py`
- Modify: `apps/api/src/robopark_api/task_workflow_models.py`
- Modify: `apps/api/src/robopark_api/models.py`
- Test: `apps/api/tests/test_models_migration.py`
- Test: `apps/api/tests/test_task_workflow_models.py`

**Interfaces:**
- Produces: `TrackerClaim.state: Literal['pending','active']`, `TrackerClaim.start_action_id`, `TrackerClaim.operator_user_id`.
- Produces: `TaskMessage.visibility: Literal['participants','staff']`, default `participants`.

- [ ] **Step 1: Add failing schema/model assertions**

```python
def test_claim_and_message_visibility_columns(inspector):
    claim = {column["name"] for column in inspector.get_columns("tracker_claims")}
    message = {column["name"] for column in inspector.get_columns("task_messages")}
    assert {"state", "start_action_id", "operator_user_id"} <= claim
    assert "visibility" in message
```

- [ ] **Step 2: Run the failing tests**

Run: `cd apps/api && pytest -q tests/test_models_migration.py tests/test_task_workflow_models.py`

Expected: FAIL because the new columns and constraints do not exist.

- [ ] **Step 3: Add migration and ORM fields**

Use `pending`/`active` and `participants`/`staff` check constraints. Backfill existing claims to `active` and existing messages to `participants`; foreign keys use `SET NULL` for action/operator references.

```python
state: Mapped[str] = mapped_column(String(16), default="pending", server_default="pending")
start_action_id: Mapped[str | None] = mapped_column(ForeignKey("reliable_actions.id", ondelete="SET NULL"), nullable=True)
operator_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
```

- [ ] **Step 4: Run migration/model tests and lint**

Run: `cd apps/api && pytest -q tests/test_models_migration.py tests/test_task_workflow_models.py && ruff check alembic/versions/0037_claim_workflow_visibility.py src/robopark_api/collaboration_models.py src/robopark_api/task_workflow_models.py`

- [ ] **Step 5: Commit**

```bash
git add apps/api/alembic/versions/0037_claim_workflow_visibility.py apps/api/src/robopark_api/collaboration_models.py apps/api/src/robopark_api/task_workflow_models.py apps/api/src/robopark_api/models.py apps/api/tests/test_models_migration.py apps/api/tests/test_task_workflow_models.py
git commit -m "feat(work): persist ordered claim state"
```

### Task 2: Execute the ordered mechanic claim

**Files:**
- Modify: `apps/api/src/robopark_api/services/schedules.py`
- Modify: `apps/api/src/robopark_api/services/tracker_signatures.py`
- Modify: `apps/api/src/robopark_api/services/tracker_claims.py`
- Modify: `apps/api/src/robopark_api/services/task_lifecycle.py`
- Modify: `apps/api/src/robopark_api/services/tracker_outbox.py`
- Modify: `apps/api/src/robopark_api/services/tracker_client.py`
- Test: `apps/api/tests/test_schedules.py`
- Test: `apps/api/tests/test_task_lifecycle.py`
- Test: `apps/api/tests/test_tracker_outbox.py`

**Interfaces:**
- Produces: `resolve_active_operator(db, *, park_id: int, at: datetime | None = None) -> User | None`.
- Produces durable actions `assign_operator`, `ensure_tag`, `ensure_components`, then existing `start` with `depends_on_action_ids`.
- Consumes fields added in Task 1; sets the claim to `active` only after `start` succeeds.

- [ ] **Step 1: Write resolver and ordering tests**

```python
def test_active_operator_prefers_current_shift_then_username(db_session, park, operators):
    chosen = schedules.resolve_active_operator(db_session, park_id=park.id, at=NOW)
    assert chosen.id == operators.on_shift.id

def test_claim_creates_strict_dependency_chain(client, db_session, mechanic, park):
    response = client.post("/tracker/issues/RP-1/claim", headers={"Idempotency-Key": "claim-chain-1"})
    assert response.status_code == 200
    actions = list(db_session.scalars(select(ReliableAction).order_by(ReliableAction.created_at)))
    assert [row.action for row in actions] == ["assign_operator", "ensure_tag", "ensure_components", "start"]
```

Also test concurrent claim conflict, no operator, replay, retry after the first successful step and terminal prerequisite failure releasing the pending reservation.

- [ ] **Step 2: Run focused tests to confirm failure**

Run: `cd apps/api && pytest -q tests/test_schedules.py tests/test_task_lifecycle.py tests/test_tracker_outbox.py -k 'operator or claim or start'`

- [ ] **Step 3: Implement resolver and durable chain**

The resolver checks active approved operators in the park, joins current `ScheduleEntry(kind='shift')`, then falls back to username order. Tracker identity is `(user.tracker_login or user.username).strip()`.

```python
assign = _action(db, actor=actor, issue_key=issue_key, action="assign_operator",
                 idempotency_key=idempotency_key,
                 payload={"operator_user_id": operator.id, "login": tracker_identity(operator)})
tag = _action(db, actor=actor, issue_key=issue_key, action="ensure_tag",
              idempotency_key=idempotency_key,
              payload={"tag": "diag_complete", "depends_on_action_ids": [assign.row.id]})
component = _action(db, actor=actor, issue_key=issue_key, action="ensure_components",
                    idempotency_key=idempotency_key,
                    payload={"value": ["ROBOT_SUSPENSION"], "depends_on_action_ids": [tag.row.id]})
```

`_deliver_action` compares fresh issue state before every mutation. `ensure_tag` preserves existing tags, `ensure_components` writes only when empty, and assignment is a no-op when already correct. `_process_batch` activates or releases the linked `TrackerClaim` when the final start action succeeds or becomes terminal.

- [ ] **Step 4: Run focused tests and lint**

Run: `cd apps/api && pytest -q tests/test_schedules.py tests/test_task_lifecycle.py tests/test_tracker_outbox.py -k 'operator or claim or start' && ruff check src/robopark_api/services/schedules.py src/robopark_api/services/tracker_signatures.py src/robopark_api/services/tracker_claims.py src/robopark_api/services/task_lifecycle.py src/robopark_api/services/tracker_outbox.py src/robopark_api/services/tracker_client.py`

- [ ] **Step 5: Commit**

```bash
git add apps/api/src/robopark_api/services apps/api/tests/test_schedules.py apps/api/tests/test_task_lifecycle.py apps/api/tests/test_tracker_outbox.py
git commit -m "feat(work): order mechanic claim mutations"
```

### Task 3: Assign reviews and expose operator work

**Files:**
- Modify: `apps/api/src/robopark_api/services/task_lifecycle.py`
- Modify: `apps/api/src/robopark_api/routers/tracker_actions.py`
- Modify: `apps/api/src/robopark_api/routers/tracker_read.py`
- Modify: `apps/web/src/domains/work/IssueWorkbench.tsx`
- Modify: `apps/web/src/domains/work/IssueWorkbench.test.tsx`
- Test: `apps/api/tests/test_task_lifecycle.py`
- Test: `apps/api/tests/test_tracker_read.py`
- Test: `apps/api/tests/test_push.py`

**Interfaces:**
- `submit_review(db, actor, issue_key, defect_code, filename, content, content_type, comment, reviewer, idempotency_key)` stores `TaskReview.reviewer_user_id`.
- `GET /tracker/issues?owned_by_me=true` becomes role-aware: mechanic claims for mechanics, pending assigned reviews for operators.
- Push `review_task` targets `{reviewer_user_id}`.

- [ ] **Step 1: Write failing API and component tests**

```python
def test_submit_review_targets_selected_operator(client, push_service, operator):
    response = submit_valid_review(client)
    assert response.status_code == 200
    assert push_service.events[-1].target_user_ids == {operator.id}
```

```tsx
it('shows pending reviews as operator my tasks', async () => {
  renderWorkbench({ user: operator, client: apiClient({ trackerIssues: reviewAwareIssues }) })
  expect(await screen.findByRole('heading', { name: 'Ждут проверки' })).toBeVisible()
})
```

- [ ] **Step 2: Run tests to verify failure**

Run: `cd apps/api && pytest -q tests/test_task_lifecycle.py tests/test_tracker_read.py tests/test_push.py -k review`

Run: `cd apps/web && npm test -- --run src/domains/work/IssueWorkbench.test.tsx`

- [ ] **Step 3: Implement targeted review ownership**

Resolve the operator once in the route, pass the `User` into lifecycle, persist it, use it in the mention and push target, and return pending review keys for an operator's `owned_by_me` query. Render the operator switch with `Очередь / Мои задачи` and heading `Ждут проверки`.

- [ ] **Step 4: Reconcile externally closed reviews**

Extend existing `reconcile_external_closure` tests so an externally closed Tracker issue closes the local review and removes it from operator work without requiring an app action.

- [ ] **Step 5: Run tests and commit**

Run: `cd apps/api && pytest -q tests/test_task_lifecycle.py tests/test_tracker_read.py tests/test_push.py -k 'review or external_close'`

Run: `cd apps/web && npm test -- --run src/domains/work/IssueWorkbench.test.tsx`

```bash
git add apps/api/src/robopark_api/services/task_lifecycle.py apps/api/src/robopark_api/routers/tracker_actions.py apps/api/src/robopark_api/routers/tracker_read.py apps/api/tests/test_task_lifecycle.py apps/api/tests/test_tracker_read.py apps/api/tests/test_push.py apps/web/src/domains/work/IssueWorkbench.tsx apps/web/src/domains/work/IssueWorkbench.test.tsx
git commit -m "feat(work): route reviews to park operators"
```

### Task 4: Hide technical messages and serve local task photos

**Files:**
- Modify: `apps/api/src/robopark_api/services/task_timeline.py`
- Modify: `apps/api/src/robopark_api/routers/task_timeline.py`
- Modify: `apps/api/src/robopark_api/services/task_lifecycle.py`
- Modify: `apps/api/src/robopark_api/schemas.py`
- Test: `apps/api/tests/test_task_timeline.py`

**Interfaces:**
- `append_system_message(db, issue_key, actor, text, action_id=None, visibility='participants')`.
- `GET /tracker/issues/{key}/attachments/{attachment_id}/content` streams a validated local blob after issue authorization.
- Local attachment timeline URL is `/api/tracker/issues/{key}/attachments/{id}/content`.

- [ ] **Step 1: Add failing visibility and authorization tests**

```python
def test_mechanic_timeline_excludes_staff_message(client, staff_message):
    body = client.get("/tracker/issues/RP-1/timeline").json()
    assert staff_message.id not in {item["id"] for item in body}

def test_attachment_content_rejects_wrong_issue(client, attachment):
    assert client.get(f"/tracker/issues/RP-2/attachments/{attachment.id}/content").status_code == 404
```

Test allowed image response headers and traversal-safe blob lookup as well.

- [ ] **Step 2: Run failing tests**

Run: `cd apps/api && pytest -q tests/test_task_timeline.py`

- [ ] **Step 3: Implement visibility and content endpoint**

Mark raw outbox/audit diagnostics `staff`; leave claim, handoff, review, return and completion `participants`. Filter local rows by role before merge. Resolve the attachment by issue through `TaskAttachment → TaskMessage`, then stream only `staged_attachments_root() / blob_name` with `nosniff`, private cache headers and an inline content disposition.

- [ ] **Step 4: Run tests, lint and commit**

Run: `cd apps/api && pytest -q tests/test_task_timeline.py && ruff check src/robopark_api/services/task_timeline.py src/robopark_api/routers/task_timeline.py src/robopark_api/services/task_lifecycle.py src/robopark_api/schemas.py`

```bash
git add apps/api/src/robopark_api/services/task_timeline.py apps/api/src/robopark_api/routers/task_timeline.py apps/api/src/robopark_api/services/task_lifecycle.py apps/api/src/robopark_api/schemas.py apps/api/tests/test_task_timeline.py
git commit -m "feat(chat): protect local photo previews"
```

### Task 5: Render and bound cached task photos

**Files:**
- Create: `apps/web/src/pwa/taskAttachmentCache.ts`
- Create: `apps/web/src/pwa/taskAttachmentCache.test.ts`
- Modify: `apps/web/src/domains/work/TaskTimeline.tsx`
- Modify: `apps/web/src/domains/work/TaskTimeline.test.tsx`
- Modify: `apps/web/src/api.ts`

**Interfaces:**
- `loadTaskAttachment(attachment, fetcher): Promise<string>` returns an object URL and records a bounded IndexedDB entry.
- `releaseTaskAttachment(url: string): void` revokes component-owned object URLs.
- Cache limit: 32 MiB and 64 entries; LRU eviction; user namespace cleared on logout.

- [ ] **Step 1: Write failing cache and rendering tests**

```tsx
it('renders a local image thumbnail and revokes its object URL', async () => {
  const view = render(<TaskTimeline items={[messageWithLocalImage]} />)
  expect(await screen.findByRole('img', { name: 'repair.jpg' })).toBeVisible()
  view.unmount()
  expect(URL.revokeObjectURL).toHaveBeenCalled()
})
```

Test LRU eviction at 65 entries and that non-image attachments remain links.

- [ ] **Step 2: Run failing tests**

Run: `cd apps/web && npm test -- --run src/pwa/taskAttachmentCache.test.ts src/domains/work/TaskTimeline.test.tsx`

- [ ] **Step 3: Implement bounded cache and preview**

Fetch authenticated URLs through the API wrapper, store only successful image blobs, create object URLs per mounted attachment, and render `<img loading="lazy">` inside a link to the same object/remote URL.

- [ ] **Step 4: Run tests and commit**

Run: `cd apps/web && npm test -- --run src/pwa/taskAttachmentCache.test.ts src/domains/work/TaskTimeline.test.tsx`

```bash
git add apps/web/src/pwa/taskAttachmentCache.ts apps/web/src/pwa/taskAttachmentCache.test.ts apps/web/src/domains/work/TaskTimeline.tsx apps/web/src/domains/work/TaskTimeline.test.tsx apps/web/src/api.ts
git commit -m "feat(chat): preview active task photos"
```

### Task 6: Add catalog photos and permanent inventory deletion

**Files:**
- Create: `apps/api/src/robopark_api/services/inventory_deletion.py`
- Modify: `apps/api/src/robopark_api/routers/inventory.py`
- Modify: `apps/api/src/robopark_api/services/inventory_catalog.py`
- Modify: `apps/api/src/robopark_api/services/inventory_counts.py`
- Modify: `apps/web/src/api.ts`
- Modify: `apps/web/src/domains/inventory/InventoryManageView.tsx`
- Modify: `apps/web/src/domains/inventory/InventoryManageView.test.tsx`
- Modify: `apps/web/src/domains/inventory/InventoryCountsView.tsx`
- Modify: `apps/web/src/domains/inventory/InventoryCountsView.test.tsx`
- Test: `apps/api/tests/test_inventory_catalog.py`
- Test: `apps/api/tests/test_inventory_counts.py`

**Interfaces:**
- `PUT/DELETE /inventory/catalog/components/{id}/photo`.
- `PUT/DELETE /inventory/catalog/parts/{id}/photo`.
- `DELETE /inventory/catalog/components/{id}?permanent=true`.
- `DELETE /inventory/catalog/parts/{id}?permanent=true`.
- `DELETE /inventory/counts/{id}?permanent=true`.

- [ ] **Step 1: Write failing permission, photo and cascade tests**

```python
def test_royal_permanently_deletes_part_graph(client, seeded_part_graph):
    response = client.delete(f"/inventory/catalog/parts/{seeded_part_graph.part_id}?permanent=true")
    assert response.status_code == 204
    assert seeded_part_graph.remaining_rows() == 0

def test_admin_cannot_delete_foreign_park_count(admin_client, foreign_count):
    assert admin_client.delete(f"/inventory/counts/{foreign_count.id}?permanent=true").status_code == 403
```

Also test photo JPEG/PNG/WebP validation, replace cleanup, component cascade and transaction rollback when a delete step fails.

- [ ] **Step 2: Run failing API tests**

Run: `cd apps/api && pytest -q tests/test_inventory_catalog.py tests/test_inventory_counts.py -k 'photo or permanent or delete'`

- [ ] **Step 3: Implement managed photos and deletion service**

Reuse inventory media validation/storage helpers. Delete dependents in explicit child-to-parent order inside one transaction; unlink managed files only after the transaction succeeds. Reject non-royal global deletion and enforce admin park scope for park documents.

- [ ] **Step 4: Add frontend photo controls and destructive confirmations**

```tsx
<input accept="image/jpeg,image/png,image/webp" capture="environment" type="file" onChange={selectPhoto} />
```

Show preview/replace/remove in create and global edit flows. Add named confirmation dialogs for part, component and count deletion; update local resource state without navigation reload.

- [ ] **Step 5: Run API and UI tests**

Run: `cd apps/api && pytest -q tests/test_inventory_catalog.py tests/test_inventory_counts.py -k 'photo or permanent or delete'`

Run: `cd apps/web && npm test -- --run src/domains/inventory/InventoryManageView.test.tsx src/domains/inventory/InventoryCountsView.test.tsx`

- [ ] **Step 6: Commit**

```bash
git add apps/api/src/robopark_api/routers/inventory.py apps/api/src/robopark_api/services/inventory_catalog.py apps/api/src/robopark_api/services/inventory_counts.py apps/api/src/robopark_api/services/inventory_deletion.py apps/api/tests/test_inventory_catalog.py apps/api/tests/test_inventory_counts.py apps/web/src/api.ts apps/web/src/domains/inventory
git commit -m "feat(inventory): manage photos and permanent corrections"
```

### Task 7: Complete write-off feedback and collapse

**Files:**
- Modify: `apps/web/src/domains/inventory/TaskPartsPanel.tsx`
- Modify: `apps/web/src/domains/inventory/TaskPartsPanel.test.tsx`
- Modify: `apps/web/src/domains/work/IssueWorkbench.tsx`
- Modify: `apps/web/src/domains/work/IssueWorkbench.test.tsx`

**Interfaces:**
- `TaskPartsPanel.onWritten(receipt: string)` reports the completed write-off.
- Parent closes `partsOpen` only after success and announces `Запчасть списана`.

- [ ] **Step 1: Add failing interaction test**

```tsx
it('announces a write-off and collapses the form after success', async () => {
  renderWorkbench({ client: writeoffClient })
  await writeOffSelectedPart()
  expect(screen.getByRole('status')).toHaveTextContent('Запчасть списана')
  expect(screen.queryByRole('form', { name: 'Списание запчасти' })).not.toBeInTheDocument()
})
```

- [ ] **Step 2: Run, implement, rerun and commit**

Run: `cd apps/web && npm test -- --run src/domains/inventory/TaskPartsPanel.test.tsx src/domains/work/IssueWorkbench.test.tsx`

Update the callback contract, clear receipt state only on the next open, close the disclosure after success, and keep error/retry paths open.

Run: `cd apps/web && npm test -- --run src/domains/inventory/TaskPartsPanel.test.tsx src/domains/work/IssueWorkbench.test.tsx`

```bash
git add apps/web/src/domains/inventory/TaskPartsPanel.tsx apps/web/src/domains/inventory/TaskPartsPanel.test.tsx apps/web/src/domains/work/IssueWorkbench.tsx apps/web/src/domains/work/IssueWorkbench.test.tsx
git commit -m "fix(work): confirm and collapse part writeoff"
```
