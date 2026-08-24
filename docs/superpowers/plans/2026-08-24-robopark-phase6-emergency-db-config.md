# Phase 6 Emergency DB Config + Multi-role Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move Emergency section maps into SQL with admin CRUD, open resolve/section viewing to mechanic/operator/admin/royal with role filters, add a 5s shared VIN cache with single-flight, and run a light cookie keep-alive on the last-K VIN ring — without map/telemetry UI.

**Architecture:** New Alembic migration seeds tables from `emergency_sections.json`. A config service loads role-filtered sections; renderer reads DB instead of the static file. Shared `/emergency/*` routes use an in-process cache (TTL 5s). Admin CRUD lives under `/admin/emergency/*`. Lifespan starts a keep-alive loop (90–120s, ring K=20, optional seed VIN). Web adds shared viewer pages and an admin config editor; browser does no background polling.

**Tech Stack:** FastAPI, SQLAlchemy, Alembic, Pydantic, React/TypeScript, pytest, asyncio background task.

## Global Constraints

- Payload cache TTL is exactly **5 seconds**; single-flight per VIN.
- Keep-alive: ring size **K=20**, cycle **90–120s**, inter-VIN gap **≥0.9s**; empty ring uses optional `emergency_keepalive_seed_vin` or skips.
- No browser interval polling; manual refresh only.
- No map / animated robot / streaming telemetry UI in this phase.
- Cookie write remains admin/royal only via existing integrations settings.
- Emergency VIN access is not park-scoped.
- Role defaults: mechanic ⊂ operator ⊂ admin/royal; `service_raw` admin/royal only at seed.
- Preserve existing error detail codes: `emergency_cookie_not_configured`, `emergency_cookie_invalid`.
- Work in git worktree `.worktrees/phase6-emergency` on branch `feature/phase6-emergency-db-config`.
- Prefer `gitarius` for scan/commit/push when available; otherwise `git` + Origin remote (`origin.cursor.com`).

## File map

| File | Responsibility |
|------|----------------|
| `apps/api/alembic/versions/0004_phase6_emergency_config.py` | Tables + seed from JSON |
| `apps/api/src/robopark_api/models.py` | ORM models for sections/fields/roles |
| `apps/api/src/robopark_api/services/emergency_config.py` | Load/save config, role filter, seed helpers, invalidate |
| `apps/api/src/robopark_api/services/emergency_cache.py` | TTL 5s cache + single-flight + ring touch |
| `apps/api/src/robopark_api/services/emergency_sections.py` | Renderer over DB config (keep dig/format helpers) |
| `apps/api/src/robopark_api/services/emergency_keepalive.py` | Background cookie keep-alive loop |
| `apps/api/src/robopark_api/routers/emergency.py` | Shared resolve + section endpoints |
| `apps/api/src/robopark_api/routers/admin_emergency.py` | Admin CRUD/reorder/export |
| `apps/api/src/robopark_api/routers/mechanic_emergency.py` | Thin aliases to shared handlers |
| `apps/api/src/robopark_api/deps.py` | `require_emergency_viewer` helper |
| `apps/api/src/robopark_api/schemas.py` | Admin config schemas |
| `apps/api/src/robopark_api/main.py` | Register routers; start/stop keep-alive |
| `apps/api/src/robopark_api/services/platform_settings.py` | seed VIN + keepalive meta keys |
| Web: `EmergencyViewer.tsx`, pages, `AdminEmergencyConfig.tsx`, `api.ts`, `App.tsx`, i18n | Viewer + admin editor |

---

### Task 1: Models + migration + seed

**Files:**
- Create: `apps/api/alembic/versions/0004_phase6_emergency_config.py`
- Modify: `apps/api/src/robopark_api/models.py`
- Test: `apps/api/tests/test_emergency_config_models.py`

**Interfaces:**
- Produces: ORM `EmergencySection`, `EmergencyField`, `EmergencySectionRole`; revision `0004` revises `0003`

- [ ] **Step 1: Write failing model/migration test**

```python
# apps/api/tests/test_emergency_config_models.py
from sqlalchemy import select

from robopark_api.models import EmergencyField, EmergencySection, EmergencySectionRole


def test_emergency_section_tables_exist(db_session):
    section = EmergencySection(
        id="status",
        title="Статус",
        sort_order=0,
        is_enabled=True,
        formatter=None,
        meta_json=None,
    )
    db_session.add(section)
    db_session.add(
        EmergencyField(section_id="status", path="vin", label="VIN", sort_order=0)
    )
    db_session.add(EmergencySectionRole(section_id="status", role="mechanic"))
    db_session.commit()
    assert db_session.get(EmergencySection, "status").title == "Статус"
    assert db_session.scalar(select(EmergencyField).limit(1)) is not None
```

- [ ] **Step 2: Run test — expect fail (models missing)**

Run: `cd apps/api && pytest tests/test_emergency_config_models.py -v`  
Expected: FAIL import/attribute error for `EmergencySection`

- [ ] **Step 3: Add ORM models**

Append to `models.py`:

```python
class EmergencySection(Base):
    __tablename__ = "emergency_sections"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str] = mapped_column(String(128))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    formatter: Mapped[str | None] = mapped_column(String(64), nullable=True)
    meta_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    fields: Mapped[list[EmergencyField]] = relationship(
        back_populates="section", cascade="all, delete-orphan"
    )
    roles: Mapped[list[EmergencySectionRole]] = relationship(
        back_populates="section", cascade="all, delete-orphan"
    )


class EmergencyField(Base):
    __tablename__ = "emergency_fields"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    section_id: Mapped[str] = mapped_column(
        ForeignKey("emergency_sections.id", ondelete="CASCADE")
    )
    path: Mapped[str] = mapped_column(String(256))
    label: Mapped[str] = mapped_column(String(128))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    section: Mapped[EmergencySection] = relationship(back_populates="fields")


class EmergencySectionRole(Base):
    __tablename__ = "emergency_section_roles"

    section_id: Mapped[str] = mapped_column(
        ForeignKey("emergency_sections.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[str] = mapped_column(String(32), primary_key=True)

    section: Mapped[EmergencySection] = relationship(back_populates="roles")
```

- [ ] **Step 4: Write Alembic `0004` creating tables + seeding from JSON**

In `upgrade()`:
1. `create_table` for the three tables.
2. Read `apps/api/data/emergency_sections.json`.
3. Insert sections/fields in file order (`sort_order` = index).
4. Role grants:
   - `service_raw` → `admin`, `royal` only
   - `position_route`, `metadata` → `operator`, `admin`, `royal`
   - all other seeded sections → `mechanic`, `operator`, `admin`, `royal`

`down_revision = "0003"`.

Also add unit test that after `Base.metadata.create_all` + calling a `seed_emergency_config(db, json_path)` helper, `service_raw` has no mechanic role. Put seed helper in `emergency_config.py` (can stub in Task 1 and flesh in Task 2) — for Task 1 it is fine to inline seed in migration only and keep the model test simple.

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_emergency_config_models.py -v`  
Expected: PASS (conftest `create_all` includes new models)

- [ ] **Step 6: Commit**

```bash
git add apps/api/src/robopark_api/models.py \
  apps/api/alembic/versions/0004_phase6_emergency_config.py \
  apps/api/tests/test_emergency_config_models.py
git commit -m "feat(api): add Emergency config tables and seed migration"
```

---

### Task 2: Config service + role filter

**Files:**
- Create: `apps/api/src/robopark_api/services/emergency_config.py`
- Test: `apps/api/tests/test_emergency_config_service.py`

**Interfaces:**
- Consumes: `EmergencySection`, `EmergencyField`, `EmergencySectionRole`
- Produces:
  - `list_sections_for_role(db, role: str) -> list[tuple[str, str]]`
  - `get_section_config(db, section_id: str) -> dict | None`
  - `role_can_view_section(db, role: str, section_id: str) -> bool`
  - `invalidate_config_cache() -> None`
  - optional short in-process config cache invalidated on writes

- [ ] **Step 1: Failing tests**

```python
def test_list_sections_filters_by_role(db_session):
    # seed two sections: status for mechanic+admin, service_raw for admin only
    ...
    assert [s[0] for s in list_sections_for_role(db_session, "mechanic")] == ["status"]
    assert "service_raw" in [s[0] for s in list_sections_for_role(db_session, "admin")]


def test_disabled_section_hidden(db_session):
    ...
    assert list_sections_for_role(db_session, "mechanic") == []
```

- [ ] **Step 2: Run — FAIL**

- [ ] **Step 3: Implement `emergency_config.py`**

Query enabled sections joined to roles for the given role, ordered by `sort_order`. `get_section_config` returns `{id,title,formatter,meta,fields:[{path,label}],roles:[...]}`.

- [ ] **Step 4: Tests PASS**

- [ ] **Step 5: Commit**

```bash
git commit -m "feat(api): Emergency config service with role filters"
```

---

### Task 3: Payload cache (TTL 5s, single-flight)

**Files:**
- Create: `apps/api/src/robopark_api/services/emergency_cache.py`
- Modify: `apps/api/src/robopark_api/services/platform_settings.py` (ring helpers)
- Test: `apps/api/tests/test_emergency_cache.py`

**Interfaces:**
- Produces:
  - `get_robot_payload(*, db, vin: str) -> dict` — uses cookie + client; cache TTL 5s; single-flight; updates ring; sets cookie validity
  - `touch_keepalive_ring(db, vin: str) -> None`
  - `get_keepalive_ring(db) -> list[str]`
  - `invalidate_vin(vin: str) -> None`
  - `clear_cache_for_tests() -> None`

- [ ] **Step 1: Failing tests with monkeypatched client**

```python
def test_cache_reuses_payload_within_ttl(db_session, monkeypatch):
    calls = {"n": 0}
    def fake_fetch(**kwargs):
        calls["n"] += 1
        return {"vin": kwargs["vin"]}
    monkeypatch.setattr(emergency_client, "fetch_robot_payload", fake_fetch)
    settings_svc.set_setting(db_session, settings_svc.EMERGENCY_COOKIE_KEY, "c")
    a = emergency_cache.get_robot_payload(db=db_session, vin="YASADR00000000447")
    b = emergency_cache.get_robot_payload(db=db_session, vin="YASADR00000000447")
    assert a == b and calls["n"] == 1


def test_cache_expires_after_ttl(db_session, monkeypatch):
    # freeze/advance time via monkeypatch of time.monotonic
    ...
    assert calls["n"] == 2


def test_single_flight(db_session, monkeypatch):
    # two threads/concurrent calls → one upstream
    ...
```

- [ ] **Step 2–4: Implement with `threading.Lock` + per-VIN Event/future; TTL=5.0; on `EmergencyAuthError` invalidate + `set_emergency_cookie_valid(False)` and re-raise**

Ring storage: JSON list in `platform_settings` key `emergency_keepalive_ring` (max 20, move-to-end).

- [ ] **Step 5: Commit**

```bash
git commit -m "feat(api): shared Emergency VIN cache with 5s TTL"
```

---

### Task 4: Renderer uses DB config

**Files:**
- Modify: `apps/api/src/robopark_api/services/emergency_sections.py`
- Test: `apps/api/tests/test_emergency_sections.py` (extend)

**Interfaces:**
- Produces:
  - `list_sections(db, role: str) -> list[tuple[str,str]]` (delegate to config)
  - `render_section(db, payload, section_id: str) -> dict` (raises `KeyError` if missing/disabled)

- [ ] **Step 1: Update tests to pass `db_session` and seed a section**
- [ ] **Step 2: Change functions to require `db: Session`; remove file-only `_load_sections` as primary path; keep dig/format helpers**
- [ ] **Step 3: Implement `fields_and_top_level_leftovers` formatter minimally (fields + leftover top-level keys not covered) or document defer — **implement** so `service_raw` works**
- [ ] **Step 4: Tests PASS + commit**

```bash
git commit -m "feat(api): render Emergency sections from DB config"
```

---

### Task 5: Shared `/emergency/*` router + mechanic aliases

**Files:**
- Create: `apps/api/src/robopark_api/routers/emergency.py`
- Modify: `apps/api/src/robopark_api/deps.py`
- Modify: `apps/api/src/robopark_api/routers/mechanic_emergency.py`
- Modify: `apps/api/src/robopark_api/main.py`
- Test: `apps/api/tests/test_emergency_router.py`

**Interfaces:**
- Produces: `require_emergency_viewer(user) -> User` allowing approved mechanic/operator or admin/royal
- Endpoints mirror existing resolve/section but filter sections by role and use cache

- [ ] **Step 1: Failing API tests**

```python
def test_operator_resolve_hides_service_raw(client, db_session, seed_operator, monkeypatch):
    # seed config + cookie; monkeypatch cache/client
    ...
    client.post("/auth/login", json={...})
    r = client.post("/emergency/resolve", json={"robot_number": "447"})
    assert r.status_code == 200
    ids = [s["id"] for s in r.json()["sections"]]
    assert "service_raw" not in ids


def test_mechanic_alias_still_works(client, ...):
    r = client.post("/mechanic/emergency/resolve", json={"robot_number": "447"})
    assert r.status_code == 200
```

- [ ] **Step 2–4: Implement router; aliases call same service functions; register in `main.py`**
- [ ] **Step 5: Commit**

```bash
git commit -m "feat(api): shared /emergency routes with role ACL"
```

---

### Task 6: Admin Emergency CRUD API

**Files:**
- Create: `apps/api/src/robopark_api/routers/admin_emergency.py`
- Modify: `apps/api/src/robopark_api/schemas.py`
- Modify: `apps/api/src/robopark_api/main.py`
- Test: `apps/api/tests/test_admin_emergency.py`

**Interfaces (schemas):**
- `EmergencyFieldAdminOut`, `EmergencySectionAdminOut`, create/update payloads with `roles: list[str]`, `fields`, `is_enabled`, `formatter`, `meta`
- Endpoints per design spec

- [ ] **Step 1: Tests for list/create/patch/delete/reorder + operator 403**
- [ ] **Step 2–4: Implement; call `invalidate_config_cache()` after every write**
- [ ] **Step 5: Export endpoint returns JSON `{sections: {...}}` compatible with seed file shape**
- [ ] **Step 6: Commit**

```bash
git commit -m "feat(api): admin Emergency section/field CRUD"
```

---

### Task 7: Keep-alive background job

**Files:**
- Create: `apps/api/src/robopark_api/services/emergency_keepalive.py`
- Modify: `apps/api/src/robopark_api/main.py` lifespan
- Modify: `apps/api/src/robopark_api/services/platform_settings.py` (`EMERGENCY_KEEPALIVE_SEED_VIN_KEY`, `last_ok` meta)
- Test: `apps/api/tests/test_emergency_keepalive.py`

**Interfaces:**
- `async def run_keepalive_loop(stop_event: asyncio.Event) -> None`
- `def keepalive_once(db) -> None` (sync body for tests)

- [ ] **Step 1: Test empty ring + no seed → no upstream calls**
- [ ] **Step 2: Test ring VIN → one fetch; 401 → cookie invalid**
- [ ] **Step 3: Implement loop with sleep 90–120s (use 90 in tests via injectable interval)**
- [ ] **Step 4: Start task in lifespan; cancel on shutdown**
- [ ] **Step 5: Commit**

```bash
git commit -m "feat(api): Emergency cookie keep-alive on VIN ring"
```

---

### Task 8: Web API client + shared Emergency viewer

**Files:**
- Modify: `apps/web/src/api.ts`
- Create: `apps/web/src/components/emergency/EmergencyViewer.tsx`
- Create: `apps/web/src/pages/OperatorEmergency.tsx`
- Create: `apps/web/src/pages/AdminEmergency.tsx`
- Modify: `apps/web/src/pages/MechanicEmergency.tsx`
- Modify: `apps/web/src/pages/Operator.tsx`, `Mechanic.tsx`, `Admin.tsx`, `App.tsx`
- Modify: `apps/web/src/i18n/ru.ts`

**Interfaces:**
- `api.emergencyResolve`, `api.emergencySection` → `/emergency/*`
- Viewer props: `backTo: string`

- [ ] **Step 1: Add API methods + types**
- [ ] **Step 2: Extract shared viewer (robot input, sections, detail, manual refresh button)**
- [ ] **Step 3: Wire routes `/operator/emergency`, `/admin/emergency`; keep `/mechanic/emergency`**
- [ ] **Step 4: `npm run build` PASS**
- [ ] **Step 5: Commit**

```bash
git commit -m "feat(web): multi-role Emergency viewer"
```

---

### Task 9: Admin Emergency config UI

**Files:**
- Create: `apps/web/src/pages/AdminEmergencyConfig.tsx`
- Modify: `apps/web/src/api.ts`, `App.tsx`, `Admin.tsx`
- Modify: `apps/web/src/index.css` (minimal editor layout only)

- [ ] **Step 1: API methods for admin CRUD/export**
- [ ] **Step 2: Page: list sections, edit title/enabled/roles, edit fields, reorder, export download**
- [ ] **Step 3: Link from Admin hub «Конфиг Emergency»**
- [ ] **Step 4: Build PASS + commit**

```bash
git commit -m "feat(web): admin Emergency config editor"
```

---

### Task 10: Docs + full verification

**Files:**
- Modify: `README.md` (Phase 6 section)
- Tests: full `pytest` + `npm run build`

**Verification matrix:**
- Role ACL: mechanic cannot open `service_raw` (404/403)
- Operator sees `position_route`, not `service_raw`
- Admin sees all enabled
- Cache: two rapid requests → one upstream (unit)
- Keep-alive empty ring idle
- Keep-alive 401 flips validity
- Admin CRUD 403 for operator
- Legacy `/mechanic/emergency/resolve` still 200
- No browser timers polling Emergency

- [ ] **Step 1: Run `cd apps/api && pytest` — all green**
- [ ] **Step 2: Run `cd apps/web && npm run build` — success**
- [ ] **Step 3: Update README Phase 6**
- [ ] **Step 4: Commit docs**

```bash
git commit -m "docs: Phase 6 Emergency DB config notes"
```

---

## Spec coverage checklist

| Spec item | Task |
|-----------|------|
| SQL tables + seed from JSON | 1 |
| Role filter mechanic ⊂ operator ⊂ admin | 2, 5 |
| Shared `/emergency/*` | 5 |
| Admin CRUD + export | 6, 9 |
| Cache TTL 5s + single-flight | 3 |
| Keep-alive ring K=20 | 3, 7 |
| Seed VIN when ring empty | 7 |
| Multi-role web viewers | 8 |
| Admin config UI | 9 |
| No map UI / no browser poll | 8–9 constraints |
| Mechanic aliases | 5 |
| README | 10 |

## Out of scope reminders

- Map / HUD graphics / WebSocket telemetry
- Telegram notify bot keep-alive (in-process API job only)
- Dropping `/mechanic/emergency/*` (aliases stay this phase)
