# Robopark Reports Workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Human report chain mechanic → operator → admin: kinds (question / close-review / problem / escalation), inbox actions (return / done), in-app badge; wire mechanic Tracker «Закрыть» to auto `ticket_close_review`.

**Architecture:** Single `reports` table + `kind`; FastAPI `/reports/*`; close creates report only after successful existing `POST /tracker/issues/{key}/close`; web `/reports` replaces stub; badge on AppShell nav.

**Tech Stack:** FastAPI, SQLAlchemy, Alembic, pytest; React 19 + Vite; existing Tracker actions.

**Base:** branch from `main` after UI shell PR merge, or continue from `feature/ui-shell-dashboard` if still open — prefer new branch `feature/reports-workflow` from latest `main` that includes UI shell.

**Spec:** `docs/superpowers/specs/2026-08-24-robopark-reports-workflow-design.md`

## Global Constraints

- One model `Report` with `kind` ∈ `ticket_question` | `ticket_close_review` | `mechanic_problem` | `escalation_to_admin`
- Status ∈ `open` | `returned` | `done` (no hard-delete on «Готово»)
- Tracker key **required** for `ticket_question` and `ticket_close_review`; optional for `mechanic_problem`
- Auto report **only** on mechanic close in our UI; question/problem are manual forms
- Receiver actions: Вернуть (+ comment), Готово → `done`, Закрыть = UI-only (no API)
- Admin inbox = escalations only; no Telegram/attachments in v1
- Duplicate open `ticket_close_review` for same `(park_id, tracker_key)` → return existing
- Create close-review **only after** successful Tracker close, else 502 / no orphan report
- UI language Russian; reuse AppShell; KPI now-report stays out of «Репорты»
- Mechanic badge = count own `returned`; operator/admin badge = count inbox `open`

---

## File structure

| File | Responsibility |
|------|----------------|
| `apps/api/alembic/versions/0006_reports_workflow.py` | `reports` table |
| `apps/api/src/robopark_api/models.py` | `Report` model |
| `apps/api/src/robopark_api/services/reports.py` | create/list/return/done/escalate/badge |
| `apps/api/src/robopark_api/routers/reports.py` | HTTP API |
| `apps/api/src/robopark_api/schemas.py` | Report DTOs |
| `apps/api/src/robopark_api/routers/tracker_actions.py` | After mechanic close → create close_review |
| `apps/api/tests/test_reports.py` | API/authz/lifecycle |
| `apps/web/src/api.ts` | Client methods |
| `apps/web/src/pages/Reports.tsx` | Full UI (replace stub) |
| `apps/web/src/components/AppShell.tsx` | Badge on Репорты |
| `apps/web/src/i18n/ru.ts` | Copy |
| `apps/web/src/components/tracker/*` | Surface returned close-reviews if needed |

---

### Task 1: Report model + migration 0006

**Files:**
- Create: `apps/api/alembic/versions/0006_reports_workflow.py` (revises `0005`)
- Modify: `apps/api/src/robopark_api/models.py`
- Modify: `apps/api/tests/test_models_migration.py` (add `reports` to expected tables)

**Interfaces:**
- Produces model `Report` with columns matching spec; unique partial or app-level uniqueness for open close_review per park+key

- [ ] **Step 1: Extend migration test expectation** so `reports` is required in metadata set

- [ ] **Step 2: Run test — expect FAIL**

Run: `cd apps/api && .venv/bin/python -m pytest tests/test_models_migration.py::test_metadata_has_required_tables -v`  
Expected: FAIL (reports missing)

- [ ] **Step 3: Add model + Alembic 0006**

```python
class Report(Base):
    __tablename__ = "reports"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True, default="open")
    park_id: Mapped[int] = mapped_column(ForeignKey("parks.id"), index=True)
    author_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    target_role: Mapped[str] = mapped_column(String(32), index=True)
    tracker_key: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    tracker_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    title: Mapped[str] = mapped_column(String(256))
    body: Mapped[str] = mapped_column(Text, default="")
    parent_report_id: Mapped[int | None] = mapped_column(ForeignKey("reports.id"), nullable=True)
    return_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at / updated_at / resolved_at  # timezone-aware
```

Indexes: `(target_role, status, park_id)`, `(author_user_id, created_at)`.

- [ ] **Step 4: pytest migration tests PASS**

- [ ] **Step 5: Commit**

```bash
git commit -m "feat(api): add reports table for workflow chain"
```

---

### Task 2: reports service (create / validate / duplicate)

**Files:**
- Create: `apps/api/src/robopark_api/services/reports.py`
- Create: `apps/api/tests/test_reports.py` (service-level first)

**Interfaces:**
```python
KIND_TICKET_QUESTION = "ticket_question"
KIND_TICKET_CLOSE_REVIEW = "ticket_close_review"
KIND_MECHANIC_PROBLEM = "mechanic_problem"
KIND_ESCALATION = "escalation_to_admin"
STATUS_OPEN, STATUS_RETURNED, STATUS_DONE = "open", "returned", "done"

def create_manual_report(db, *, author: User, park_id: int, kind: str, title: str, body: str, tracker_key: str | None, tracker_url: str | None) -> Report
def get_or_create_close_review(db, *, author: User, park_id: int, tracker_key: str, tracker_url: str | None, title: str, body: str = "") -> Report
```

Rules:
- Manual kinds only `ticket_question` | `mechanic_problem`; target_role=`operator`
- Require tracker_key for question; optional for problem
- `get_or_create_close_review`: if open row exists for park+key+kind close_review → return it; else insert

- [ ] **Step 1: Failing tests** for validation + duplicate close_review

- [ ] **Step 2: Implement service**

- [ ] **Step 3: pytest PASS**

- [ ] **Step 4: Commit** `feat(api): report create and close-review idempotency`

---

### Task 3: return / done / escalate / lists / badge

**Files:**
- Modify: `apps/api/src/robopark_api/services/reports.py`
- Modify: `apps/api/tests/test_reports.py`

**Interfaces:**
```python
def list_inbox(db, user: User, *, park_id: int | None = None) -> list[Report]
def list_mine(db, user: User) -> list[Report]
def get_report(db, user: User, report_id: int) -> Report  # raises LookupError/PermissionError
def return_report(db, user: User, report_id: int, comment: str) -> Report
def done_report(db, user: User, report_id: int) -> Report
def escalate_report(db, user: User, report_id: int, comment: str) -> Report
def badge_counts(db, user: User) -> dict  # {"count": int}
```

Authz:
- inbox operator: open + target_role=operator + park in user parks
- inbox admin/royal: open + target_role=admin
- return/done: same as inbox recipient for that report
- escalate: operator only; creates new open escalation with parent_report_id; original may stay open until done (prefer: leave parent `open` until operator also dones it, or auto-done parent on escalate — **v1: parent stays open**; escalate creates child; operator should done parent separately OR escalate implies parent stays for tracking — **spec: escalate creates new; parent remains open until Готово**)

- [ ] **Step 1–4: TDD** then commit `feat(api): report inbox return done escalate badge`

---

### Task 4: HTTP router `/reports`

**Files:**
- Create: `apps/api/src/robopark_api/routers/reports.py`
- Modify: `schemas.py`, `main.py`
- Modify: `tests/test_reports.py` (HTTP via TestClient)

**Endpoints:** as spec table. Map PermissionError→403, LookupError→404, ValueError→400.

- [ ] **Step 1: Router tests** (mechanic create, operator inbox, return, done, escalate, badge, cross-park 403)

- [ ] **Step 2: Implement + register**

- [ ] **Step 3: pytest PASS**

- [ ] **Step 4: Commit** `feat(api): reports HTTP API`

---

### Task 5: Wire Tracker close → close_review

**Files:**
- Modify: `apps/api/src/robopark_api/routers/tracker_actions.py` `close_issue`
- Modify: `apps/api/tests/test_tracker_actions.py` (or new cases in test_reports)

**Behavior:**
After successful `transition_issue` close:
- If `user.role == mechanic` and user has exactly one park: `get_or_create_close_review(...)` with key, URL from issue, title like `Закрытие {key}`
- If Tracker fails: do not create report (already the case)
- If report create fails after close: log + include warning in response detail OR 500 — prefer return close OK and still try report; if report fails raise 500 with message that Tracker closed but report failed (rare). Simpler v1: create report in same request after close; on DB error 500.

Resolve `park_id` from mechanic’s single park (`get_mechanic_park`). If no park → close still succeeds, skip report (or 400). Prefer: close OK, skip report if no park.

- [ ] **Step 1: Failing test** mechanic close creates open close_review

- [ ] **Step 2: Implement**

- [ ] **Step 3: pytest PASS**

- [ ] **Step 4: Commit** `feat(api): create close-review report after mechanic ticket close`

---

### Task 6: Web API client + i18n

**Files:**
- Modify: `apps/web/src/api.ts`
- Modify: `apps/web/src/i18n/ru.ts`

**Interfaces:**
```ts
api.reportsMine(), reportsInbox(parkId?), report(id), createReport(payload),
reportReturn(id, comment), reportDone(id), reportEscalate(id, comment), reportsBadge()
```

Russian strings for kinds, statuses, buttons Вернуть/Готово/Закрыть/Эскалировать/Вопрос/Проблема.

- [ ] **Step 1: Types + methods + ru**

- [ ] **Step 2: `npm run build`**

- [ ] **Step 3: Commit** `feat(web): reports API client and Russian copy`

---

### Task 7: Reports page UI

**Files:**
- Replace: `apps/web/src/pages/Reports.tsx`
- Optionally split: `components/reports/ReportList.tsx`, `ReportDetail.tsx`, `ReportForms.tsx` if file grows

**UX:**
- Mechanic: tabs/sections mine + create forms (question / problem); show status + return_comment
- Operator: inbox list → detail with Tracker link, Вернуть (prompt comment), Готово, Закрыть (navigate back), Эскалировать
- Admin: inbox escalations + same actions (no escalate)
- Use `useParkContext` for park on create/inbox filter

- [ ] **Step 1: Implement UI**

- [ ] **Step 2: `npm run build`**

- [ ] **Step 3: Commit** `feat(web): reports inbox and forms by role`

---

### Task 8: Badge on AppShell + returned in tasks

**Files:**
- Modify: `apps/web/src/components/AppShell.tsx` — fetch badge periodically on mount / focus (no aggressive polling; load on mount + after navigation to /reports)
- Modify: tasks/tracker UI — show banner of `returned` close-reviews for mechanic (link to /reports); ensure close button already calls `api` close (existing)

- [ ] **Step 1: Badge count on nav «Репорты»**

- [ ] **Step 2: Mechanic tasks — list returned close-reviews or alert**

- [ ] **Step 3: build PASS**

- [ ] **Step 4: Commit** `feat(web): reports badge and returned close-reviews in tasks`

---

### Task 9: Docs + full verification

**Files:**
- Modify: root `README.md` — short Russian section on Репорты
- Spec status → `implemented` when done

- [ ] **Step 1: README**

- [ ] **Step 2: `cd apps/api && .venv/bin/python -m pytest -q`**

- [ ] **Step 3: `cd apps/web && npm run build`**

- [ ] **Step 4: Commit** `docs: reports workflow usage notes`

---

## Self-review (plan vs spec)

| Spec | Task |
|------|------|
| Model + kinds/statuses | 1–2 |
| Inbox return/done/escalate/badge API | 3–4 |
| Close → close_review | 5 |
| Manual question/problem UI | 6–7 |
| Badge + returned in tasks | 8 |
| No Telegram/attachments | Non-goal |
| Duplicate open close_review | 2 |

---

## Execution handoff

Plan complete and saved to `docs/superpowers/plans/2026-08-24-robopark-reports-workflow.md`.

**Two execution options:**

1. **Subagent-Driven (recommended)** — Cursor models (`composer-2.5-fast`) to save quota  
2. **Inline Execution** — this session with checkpoints  

Which approach?
