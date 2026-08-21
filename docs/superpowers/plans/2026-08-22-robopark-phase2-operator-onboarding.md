# Phase 2 Operator Onboarding & Parks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Operator self-registration via shared password, admin/royal approve (≥1 park) or reject, park CRUD, and operator park-request inbox — on top of the Phase 1 skeleton.

**Architecture:** Extend `users` with `access_status`; add `parks`, `user_parks`, `park_requests`. Registration is public; admin/royal manage parks and inboxes; approved operators see parks and request more. SPA routes operators by `access_status`.

**Tech Stack:** Existing Phase 1 stack — FastAPI, SQLAlchemy 2, Alembic, argon2, pytest; React + Vite + React Router.

**Origin:** Approved design `docs/superpowers/specs/2026-08-22-robopark-phase2-operator-onboarding-design.md`.

## Global Constraints

- Approvers: `admin` and `royal` only.
- Self-register always creates `role=operator`, `access_status=pending`.
- Approve requires ≥1 **active** park; reject leaves account with `rejected` (no self re-apply).
- Pending/rejected operators may log in; approved-only routes return 403.
- Shared password: env `OPERATOR_SHARED_PASSWORD` only; empty/unset → register 403.
- No Tracker, Telegram, mechanic park-password, reports/SLA in this phase.
- Do not import from `robopark_tehblokdan`.
- Follow existing patterns in `apps/api` (hermetic `conftest`, `require_user`, cookie auth) and `apps/web` (`api.ts`, `pathForRole`, `RequireRole`).

## File map

```
apps/api/src/robopark_api/
  models.py          # AccessStatus, Park, UserPark, ParkRequest; User.access_status
  config.py          # operator_shared_password
  schemas.py         # register, parks, requests, extended UserOut
  deps.py            # require_admin, require_approved_operator
  seed.py            # seed users get access_status=approved
  routers/auth.py    # register + extended me
  routers/parks.py   # NEW admin park CRUD
  routers/admin_access.py      # NEW
  routers/admin_park_requests.py  # NEW
  routers/operator_parks.py    # NEW
apps/api/alembic/versions/0002_phase2_parks_access.py
apps/api/tests/test_register.py
apps/api/tests/test_parks.py
apps/api/tests/test_access_requests.py
apps/api/tests/test_park_requests.py
apps/web/src/api.ts, routes.ts, App.tsx, pages/*
.env.example, README.md, deploy/host.env.example
```

---

### Task 1: Models + Alembic `0002` + seed `access_status`

**Files:**
- Modify: `apps/api/src/robopark_api/models.py`
- Modify: `apps/api/src/robopark_api/seed.py`
- Modify: `apps/api/tests/conftest.py` (`seed_royal` sets `access_status="approved"`)
- Create: `apps/api/alembic/versions/0002_phase2_parks_access.py`
- Create: `apps/api/tests/test_phase2_models.py`

**Interfaces:**
- Consumes: Phase 1 `User`, `Base`, Alembic env
- Produces:
  - `AccessStatus` StrEnum: `pending`, `approved`, `rejected`
  - `User.access_status: str` (default `"approved"` for migration backfill)
  - `Park`, `UserPark`, `ParkRequest` models as in spec
  - Relationships: `User.parks` via `user_parks`; `User.park_requests`

- [ ] **Step 1: Write failing tests**

```python
# apps/api/tests/test_phase2_models.py
from robopark_api.models import Base, Park, ParkRequest, User, UserPark


def test_metadata_has_phase2_tables():
    names = set(Base.metadata.tables)
    assert {"parks", "user_parks", "park_requests", "users"} <= names


def test_user_has_access_status_column():
    assert "access_status" in {c.name for c in User.__table__.columns}
```

- [ ] **Step 2: Run — expect FAIL**

Run: `cd apps/api && source .venv/bin/activate && pytest tests/test_phase2_models.py -v`  
Expected: FAIL (missing column/tables)

- [ ] **Step 3: Implement models**

Add to `models.py` (keep existing classes; extend `User`):

```python
class AccessStatus(StrEnum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"


# On User, add:
access_status: Mapped[str] = mapped_column(String(32), default=AccessStatus.approved.value)

# After AuthSession, add Park / UserPark / ParkRequest:

class Park(Base):
    __tablename__ = "parks"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128))
    tag: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class UserPark(Base):
    __tablename__ = "user_parks"
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    park_id: Mapped[int] = mapped_column(
        ForeignKey("parks.id", ondelete="CASCADE"), primary_key=True
    )


class ParkRequest(Base):
    __tablename__ = "park_requests"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    park_id: Mapped[int] = mapped_column(ForeignKey("parks.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(32), default=AccessStatus.pending.value)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    resolved_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
```

Use `UniqueConstraint("user_id", "park_id")` on `park_requests` **only for pending** is hard in SQLite — enforce in application (Task 6). Optional partial unique skipped for SQLite simplicity.

Migration `0002`:
- `ADD COLUMN users.access_status VARCHAR(32) NOT NULL DEFAULT 'approved'`
- create `parks`, `user_parks`, `park_requests`

In `seed.py`, when creating user set `access_status=AccessStatus.approved.value`.

In `conftest.seed_royal`, set `access_status="approved"`.

- [ ] **Step 4: Run tests + alembic**

```bash
cd apps/api && source .venv/bin/activate
pytest tests/test_phase2_models.py tests/test_models_migration.py -v
DATABASE_URL=sqlite:///./data/robopark.db alembic upgrade head
```

Expected: PASS; upgrade succeeds on fresh/existing DB.

- [ ] **Step 5: Commit**

```bash
git add apps/api/src/robopark_api/models.py apps/api/src/robopark_api/seed.py \
  apps/api/alembic/versions/0002_phase2_parks_access.py apps/api/tests/
git commit -m "feat(api): add access_status, parks, and park_requests models"
```

---

### Task 2: Config + `POST /auth/register`

**Files:**
- Modify: `apps/api/src/robopark_api/config.py`
- Modify: `apps/api/src/robopark_api/schemas.py`
- Modify: `apps/api/src/robopark_api/routers/auth.py`
- Modify: `apps/api/src/robopark_api/security.py` (add `shared_passwords_match`)
- Create: `apps/api/tests/test_register.py`
- Modify: `.env.example` (add `OPERATOR_SHARED_PASSWORD=`)

**Interfaces:**
- Consumes: `User`, `hash_password`, Settings
- Produces:
  - `Settings.operator_shared_password: str | None = None`
  - `RegisterRequest(shared_password, username, password)`
  - `POST /auth/register` → 201 `{id, username, role, access_status}` (no cookie)
  - 403 if shared password unset/wrong; 409 if username taken

- [ ] **Step 1: Failing tests**

```python
# apps/api/tests/test_register.py
def test_register_creates_pending_operator(client, test_settings, monkeypatch):
    monkeypatch.setattr(test_settings, "operator_shared_password", "gate")
    r = client.post(
        "/auth/register",
        json={
            "shared_password": "gate",
            "username": "op1",
            "password": "secret1",
        },
    )
    assert r.status_code == 201
    body = r.json()
    assert body["role"] == "operator"
    assert body["access_status"] == "pending"
    assert "robopark_session" not in r.cookies


def test_register_wrong_shared_password(client, test_settings, monkeypatch):
    monkeypatch.setattr(test_settings, "operator_shared_password", "gate")
    r = client.post(
        "/auth/register",
        json={"shared_password": "nope", "username": "op1", "password": "secret1"},
    )
    assert r.status_code == 403


def test_register_disabled_when_unset(client, test_settings, monkeypatch):
    monkeypatch.setattr(test_settings, "operator_shared_password", None)
    r = client.post(
        "/auth/register",
        json={"shared_password": "x", "username": "op1", "password": "secret1"},
    )
    assert r.status_code == 403


def test_register_duplicate_username(client, test_settings, monkeypatch, seed_royal):
    monkeypatch.setattr(test_settings, "operator_shared_password", "gate")
    r = client.post(
        "/auth/register",
        json={"shared_password": "gate", "username": "royal", "password": "secret1"},
    )
    assert r.status_code == 409
```

Ensure `client` fixture’s `test_settings` can be mutated; if Settings is frozen-like, rebuild override — prefer mutating attribute on the same object already injected via dependency_overrides.

- [ ] **Step 2: Run — expect FAIL**

- [ ] **Step 3: Implement**

`config.py`: `operator_shared_password: str | None = None`

`security.py`:

```python
import hmac

def shared_passwords_match(provided: str, expected: str | None) -> bool:
    if not expected:
        return False
    return hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))
```

`schemas.py`:

```python
class RegisterRequest(BaseModel):
    shared_password: str = Field(min_length=1, max_length=128)
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class RegisterOut(BaseModel):
    id: int
    username: str
    role: str
    access_status: str
```

`auth.py` register handler: validate shared password → check username unique → create User(`role=operator`, `access_status=pending`, hashed password) → 201 RegisterOut. Never set admin/royal/mechanic.

- [ ] **Step 4: pytest `tests/test_register.py` — PASS**

- [ ] **Step 5: Commit** `feat(api): add operator self-registration with shared password`

---

### Task 3: Extended `/auth/me` + role deps

**Files:**
- Modify: `apps/api/src/robopark_api/schemas.py` (`UserOut`, `ParkOut`)
- Modify: `apps/api/src/robopark_api/routers/auth.py` (`me`)
- Modify: `apps/api/src/robopark_api/deps.py`
- Modify: `apps/api/tests/test_auth.py` (assert `access_status` / `parks` on me)
- Create helpers in conftest: `seed_pending_operator`, `login_as(client, username, password)`

**Interfaces:**
- Produces:
  - `ParkOut(id, name, tag)`
  - `UserOut(id, username, role, access_status, parks: list[ParkOut])`
  - `require_admin(user) -> User` — role in `royal`,`admin` else 403
  - `require_approved_operator(user) -> User` — role operator + access_status approved else 403

- [ ] **Step 1: Failing test**

```python
def test_me_includes_access_status_and_parks(client, seed_royal):
    client.post("/auth/login", json={"username": "royal", "password": "secret"})
    me = client.get("/auth/me").json()
    assert me["access_status"] == "approved"
    assert me["parks"] == []
```

- [ ] **Step 2: Run — FAIL** (missing fields)

- [ ] **Step 3: Implement**

```python
# deps.py
def require_admin(user: User = Depends(require_user)) -> User:
    if user.role not in (UserRole.royal.value, UserRole.admin.value):
        raise HTTPException(status_code=403)
    return user


def require_approved_operator(user: User = Depends(require_user)) -> User:
    if user.role != UserRole.operator.value or user.access_status != AccessStatus.approved.value:
        raise HTTPException(status_code=403)
    return user
```

`me`: load parks via join `UserPark`→`Park` for the user; return `UserOut(...)`.

Update existing auth tests that compare exact `UserOut` dicts.

- [ ] **Step 4: Full auth+register suite PASS**

- [ ] **Step 5: Commit** `feat(api): extend /auth/me and add admin/operator deps`

---

### Task 4: Park CRUD API

**Files:**
- Create: `apps/api/src/robopark_api/routers/parks.py`
- Modify: `apps/api/src/robopark_api/main.py` (include router)
- Create: `apps/api/tests/test_parks.py`

**Interfaces:**
- `GET /parks` → list all (admin); include inactive
- `POST /parks` `{name, tag}` → 201 ParkOut (+ `is_active: true`)
- `PATCH /parks/{id}` `{name?, tag?, is_active?}` → ParkOut
- All require `require_admin`

- [ ] **Step 1: Tests**

```python
def test_admin_creates_and_lists_park(client, seed_royal):
    client.post("/auth/login", json={"username": "royal", "password": "secret"})
    created = client.post("/parks", json={"name": "Next", "tag": "Next"})
    assert created.status_code == 201
    assert created.json()["tag"] == "Next"
    listed = client.get("/parks")
    assert listed.status_code == 200
    assert any(p["tag"] == "Next" for p in listed.json())


def test_operator_cannot_create_park(client, test_settings, monkeypatch):
    monkeypatch.setattr(test_settings, "operator_shared_password", "gate")
    client.post("/auth/register", json={
        "shared_password": "gate", "username": "op1", "password": "secret1"
    })
    client.post("/auth/login", json={"username": "op1", "password": "secret1"})
    assert client.post("/parks", json={"name": "X", "tag": "x"}).status_code == 403
```

- [ ] **Step 2–4: Implement router, wire main, PASS, commit**  
`feat(api): add admin park CRUD`

Schemas: `ParkCreate`, `ParkUpdate`, `ParkOut` with `id, name, tag, is_active`.

Duplicate tag → 409.

---

### Task 5: Access-request approve / reject

**Files:**
- Create: `apps/api/src/robopark_api/routers/admin_access.py`
- Create: `apps/api/tests/test_access_requests.py`
- Modify: `main.py`

**Interfaces:**
- `GET /admin/access-requests` → users with `role=operator` and `access_status in (pending, rejected)` (include both for visibility)
- `POST /admin/access-requests/{user_id}/approve` body `{park_ids: list[int]}` (≥1 active parks)
- `POST /admin/access-requests/{user_id}/reject`

- [ ] **Step 1: Tests**

```python
def test_approve_requires_parks_and_sets_approved(client, seed_royal, test_settings, monkeypatch):
    monkeypatch.setattr(test_settings, "operator_shared_password", "gate")
    client.post("/auth/login", json={"username": "royal", "password": "secret"})
    park_id = client.post("/parks", json={"name": "Next", "tag": "Next"}).json()["id"]
    client.post("/auth/logout")
    client.post("/auth/register", json={
        "shared_password": "gate", "username": "op1", "password": "secret1"
    })
    client.post("/auth/login", json={"username": "royal", "password": "secret"})
    bad = client.post("/admin/access-requests/2/approve", json={"park_ids": []})
    assert bad.status_code == 422 or bad.status_code == 400
    # use actual pending user id from DB or list endpoint
    inbox = client.get("/admin/access-requests").json()
    uid = next(u["id"] for u in inbox if u["username"] == "op1")
    assert client.post(
        f"/admin/access-requests/{uid}/approve", json={"park_ids": [park_id]}
    ).status_code == 204
    client.post("/auth/logout")
    client.post("/auth/login", json={"username": "op1", "password": "secret1"})
    me = client.get("/auth/me").json()
    assert me["access_status"] == "approved"
    assert any(p["id"] == park_id for p in me["parks"])


def test_reject_sets_rejected(client, seed_royal, test_settings, monkeypatch):
    monkeypatch.setattr(test_settings, "operator_shared_password", "gate")
    client.post(
        "/auth/register",
        json={"shared_password": "gate", "username": "op2", "password": "secret1"},
    )
    client.post("/auth/login", json={"username": "royal", "password": "secret"})
    inbox = client.get("/admin/access-requests").json()
    uid = next(u["id"] for u in inbox if u["username"] == "op2")
    assert client.post(f"/admin/access-requests/{uid}/reject").status_code == 204
    client.post("/auth/logout")
    client.post("/auth/login", json={"username": "op2", "password": "secret1"})
    assert client.get("/auth/me").json()["access_status"] == "rejected"
```

Approve empty `park_ids`: validate with Pydantic `Field(min_length=1)` → 422.

Inactive park id → 400.

Non-pending user approve → 400.

- [ ] **Step 2–4: Implement, PASS, commit**  
`feat(api): add access-request approve and reject`

---

### Task 6: Park-request operator + admin inbox

**Files:**
- Create: `apps/api/src/robopark_api/routers/operator_parks.py`
- Create: `apps/api/src/robopark_api/routers/admin_park_requests.py`
- Create: `apps/api/tests/test_park_requests.py`
- Modify: `main.py`

**Interfaces:**
- `GET /operator/parks` — assigned parks
- `GET /operator/park-requests` — own requests
- `POST /operator/park-requests` `{park_id}` — pending request
- `GET /admin/park-requests?status=pending` (default pending)
- `POST /admin/park-requests/{id}/approve|reject`

Rules in POST create:
- caller must be approved operator (deps)
- park exists and `is_active`
- not already in `user_parks`
- no existing `pending` for same pair → else 409

Approve: set status approved, `resolved_at=now`, `resolved_by=admin.id`, insert `UserPark`.

- [ ] **Step 1: Write full tests** covering create, duplicate pending 409, already-member 400, admin approve adds park, reject does not, pending operator gets 403 on POST.

- [ ] **Step 2–4: Implement, PASS entire API suite, commit**  
`feat(api): add park-request flow for operators and admins`

---

### Task 7: Web — register + status routing

**Files:**
- Modify: `apps/web/src/api.ts` (`User` type, `register`)
- Modify: `apps/web/src/routes.ts` (`pathForUser(user)` using role + access_status)
- Modify: `apps/web/src/pages/Login.tsx` (link to `/register`; navigate via `pathForUser`)
- Modify: `apps/web/src/pages/Home.tsx`
- Create: `apps/web/src/pages/Register.tsx`
- Create: `apps/web/src/pages/OperatorPending.tsx`
- Create: `apps/web/src/pages/OperatorRejected.tsx`
- Modify: `apps/web/src/App.tsx`
- Modify: `apps/web/src/auth.tsx` / `auth-context.ts` if User type changes

**Interfaces:**
- `pathForUser(user: {role, access_status}): string`
  - royal/admin → `/admin`
  - operator pending → `/operator/pending`
  - operator rejected → `/operator/rejected`
  - operator approved → `/operator`
  - mechanic → `/mechanic`
  - else → `NO_CABINET_PATH`

- [ ] **Step 1: Update types and routing helpers; `npm run build` must fail until pages exist or pass with stubs**

```ts
export type Park = { id: number; name: string; tag: string }
export type User = {
  id: number
  username: string
  role: string
  access_status: string
  parks: Park[]
}

export const api = {
  me: () => request<User>('/auth/me'),
  login: (username: string, password: string) =>
    request<void>('/auth/login', {
      method: 'POST',
      body: JSON.stringify({ username, password }),
    }),
  logout: () => request<void>('/auth/logout', { method: 'POST' }),
  register: (shared_password: string, username: string, password: string) =>
    request<User>('/auth/register', {
      method: 'POST',
      body: JSON.stringify({ shared_password, username, password }),
    }),
}
```

Ensure API `RegisterOut` includes `access_status` and `parks: []` so the web `User` type is one shape (amend register response in this task if Task 2 omitted `parks`).

- [ ] **Step 2: Pages**

`Register.tsx`: form fields shared_password, username, password → `api.register` → navigate `/login` with success message or auto-redirect login.

`OperatorPending.tsx`: `<h1>Awaiting approval</h1>`

`OperatorRejected.tsx`: `<h1>Access rejected</h1>`

`RequireOperatorStatus` or fold into routes: `/operator` only if approved; `/operator/pending` only if pending; etc. Wrong status → redirect via `pathForUser`.

- [ ] **Step 3: `npm run build` PASS**

- [ ] **Step 4: Commit** `feat(web): add register and operator access-status routes`

---

### Task 8: Web — operator parks UI + admin inboxes

**Files:**
- Modify: `apps/web/src/pages/Operator.tsx`
- Modify: `apps/web/src/pages/Admin.tsx` (sections or subroutes `/admin/parks`, `/admin/access-requests`, `/admin/park-requests`)
- Modify: `apps/web/src/api.ts` (park + inbox methods)
- Modify: `apps/web/src/App.tsx` if subroutes

**Operator page:**
- List `user.parks` (from me) or fetch `GET /operator/parks`
- Form: select park from `GET /parks` is admin-only — **operators need a list of requestable parks**. Spec gives admin `GET /parks` only. **Add** `GET /operator/available-parks` returning active parks not already assigned (and not pending request). Implement in API in this task with tests (small addition).

```python
# GET /operator/available-parks — require_approved_operator
# active parks where id not in user_parks and no pending park_request for user
```

- Submit `POST /operator/park-requests`
- List own requests from `GET /operator/park-requests`

**Admin page (simple sections on `/admin`):**
- Create park form + list + toggle active
- Access inbox: list pending; approve (multi-select parks) / reject
- Park-request inbox: approve / reject buttons

Keep UI minimal (forms + lists), no design system expansion.

- [ ] **Step 1: API available-parks test + endpoint**

- [ ] **Step 2: Wire web api + pages**

- [ ] **Step 3: `npm run build` + API pytest PASS**

- [ ] **Step 4: Commit** `feat(web): operator parks and admin onboarding inboxes`

---

### Task 9: Docs + env examples

**Files:**
- Modify: `README.md` — Phase 2 register/approve/parks flows
- Modify: `.env.example` — `OPERATOR_SHARED_PASSWORD=`
- Modify: `deploy/host.env.example` — same key
- Modify: `docs` cross-link to Phase 2 spec (one line in README)

- [ ] **Step 1: Update docs**

- [ ] **Step 2: Manual checklist** (document results in commit message or leave for implementer report):
  1. Register with shared password → pending screen
  2. Admin creates park, approves with that park → operator cabinet
  3. Reject path → rejected screen
  4. Park-request approve/reject
  5. Unset shared password → register 403

- [ ] **Step 3: Commit** `docs: document Phase 2 operator onboarding`

---

## Spec coverage

| Spec item | Task |
|-----------|------|
| `access_status` + parks tables | 1 |
| Shared password register | 2 |
| Extended `/auth/me` | 3 |
| Park CRUD | 4 |
| Access approve/reject ≥1 park | 5 |
| Park-request loop | 6 |
| `/register`, pending/rejected routes | 7 |
| Operator + admin UIs | 8 |
| available-parks for operator UX | 8 |
| README / env | 9 |
| Non-goals (no Tracker/Telegram/…) | Global Constraints |

## Deferred

- Live deploy / SSH
- Tracker fields on parks
- Mechanic onboarding
- Self-serve re-apply after reject

## Execution note

Prefer TDD on API tasks 1–6 and 8’s available-parks. Web tasks: `npm run build` as gate. Use feature branch / worktree; `gitarius` for push if available. Do not push unless asked.
