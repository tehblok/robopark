# Robopark Stage 0 Stabilization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task with review checkpoints.

**Goal:** Закрыть подтверждённый Emergency ACL bypass, восстановить утраченную migration/attachment parity и сделать текущий SQLite-релиз воспроизводимо проверяемым и безопаснее обновляемым до начала PostgreSQL/Redis-переработки.

**Architecture:** Этап 0 сохраняет текущий модульный монолит FastAPI + React, SQLite как временный runtime source of truth и существующие публичные REST-контракты. Исправления делаются малыми независимыми коммитами: сначала security и детерминизм, затем восстановление migration history, deploy safety, механический Ruff baseline и единая frozen verification pipeline. PostgreSQL, Redis, session-bound grants, browser subject cache и immutable releases остаются за пределами этого плана и получают отдельные specs.

**Tech Stack:** Python 3.12.13, FastAPI, SQLAlchemy 2, Alembic, pytest, Ruff, React 19, TypeScript 6, Vitest, oxlint, Vite, uv 0.11.31, npm, Docker Compose, POSIX `sh`, GitHub Actions.

**Spec:** [`docs/superpowers/specs/2026-09-01-robopark-repair-and-platform-migration-design.md`](../specs/2026-09-01-robopark-repair-and-platform-migration-design.md)

## Global Constraints

- Выполнять задачи по порядку на текущей `main`; не создавать дополнительные локальные ветки и не удалять пользовательские изменения.
- Для каждого поведенческого исправления сначала добавить или изменить тест, увидеть ожидаемый RED, затем внести минимальный production-код и увидеть GREEN.
- После каждого task запускать перечисленный regression set и делать отдельный коммит с указанным сообщением.
- Не читать, не печатать и не переносить значения `*.env`, integration credentials или пользовательские payload. Тесты используют только временные фиктивные значения.
- Не изменять и не удалять локальные `apps/api/data/*.db`, WAL/SHM и attachment-файлы. Migration gate выполняется только на snapshot-копии через `sqlite3.Connection.backup`.
- Commit `edea0280e18aaf12633eff3c94aa359ea7b7818f` использовать только как read-only reference для attachment-функции. Не cherry-pick-ить commit и не восстанавливать из него runtime blobs или несвязанные feature-изменения.
- Сохранять существующие публичные URLs, response schemas и продуктовые исключения для driver/admin/royal. Временная Emergency-проверка должна только закрыть direct-read bypass для scope-limited ролей.
- Не добавлять PostgreSQL, Redis, Emergency grant, auth throttle rewrite, browser cache isolation, immutable release switch или engine-neutral backup/restore в этот этап.
- Не исправлять семь известных frontend warnings в resource/Emergency/RobotCheck/AppShell/RequestPark: они пересекаются с Этапом 1. Gate Этапа 0 требует zero lint errors, а не zero warnings.
- Docker недоступен на текущей машине. Локальное исполнение обязано пройти API/web gates и проверку синтаксиса shell; Docker/Compose/image gates не пропускаются молча и завершаются на Docker-capable host или в CI.
- Перед каждым коммитом выполнять `git diff --check` и проверить `git diff --name-only`, чтобы runtime data и secrets не попали в индекс.

## Baseline and File Map

Подтверждённый baseline до изменений:

- backend: `3 failed, 473 passed`; три падения зависят от текущего времени в blocker history;
- Ruff: 20 lint errors и 30 неформатированных файлов;
- frontend: 87 tests и nav parity зелёные; lint падает на conditional hook, build — на unused `describe`;
- `apps/api/uv.lock` существует локально, но игнорируется Git и не соответствует `pyproject.toml`;
- Docker CLI на локальной машине отсутствует.

Порядок поставки и границы коммитов:

1. Emergency direct-read ACL hotfix.
2. Детерминированный clock для blocker history.
3. Frontend hook/build baseline.
4. Восстановление `0015_report_attachments`, storage и upload boundary.
5. Безопасный текущий ops-agent и readiness.
6. Механический Ruff lint/format baseline.
7. Frozen dependencies, immutable pins и единая verify/CI-команда.

---

## Task 1: Restore fail-closed Emergency VIN checks on direct reads

**Task interface**

- Input: authenticated user, normalized VIN, current SQLAlchemy session.
- Enforcement point: `_enforce_vin_scope(db, user, vin)` before any Emergency payload fetch.
- Output: existing snapshot/section response on allow; `403 {"detail":"emergency_vin_out_of_scope"}` on deny; existing `502 tracker_upstream_error` on Tracker failure.
- Compatibility: `/mechanic/emergency/{vin}/sections/{section_id}` remains an alias through `emergency_section_for_user` and inherits the same check.

**Files:**

- Modify: `apps/api/tests/test_emergency_router.py`
- Modify: `apps/api/src/robopark_api/routers/emergency.py`
- Verify unchanged alias: `apps/api/src/robopark_api/routers/mechanic_emergency.py`

### Step 1: Replace insecure expectations with deny-by-default router tests

In `apps/api/tests/test_emergency_router.py`:

- replace `test_snapshot_and_section_skip_vin_acl` with separate snapshot and section deny tests;
- delete `test_snapshots_after_resolve_do_not_call_acl` because that behavior belongs to the future Redis-grant design, not the temporary hotfix;
- replace `test_revoke_mid_view_keeps_snapshot_until_next_open` with a test proving every direct read re-checks current scope;
- add a mechanic alias deny test.

Use these exact assertions:

```python
def test_operator_out_of_scope_snapshot_returns_403(
    client, db_session, seed_operator, monkeypatch, emergency_payload
):
    configure_emergency(db_session, monkeypatch, emergency_payload, allow_vin=False)
    monkeypatch.setattr(emergency_scope, "vin_allowed_for_user", lambda *_a, **_k: False)
    login_as(client, "operator1", "secret")

    response = client.get("/emergency/YASADR00000000447/snapshot")

    assert response.status_code == 403
    assert response.json()["detail"] == "emergency_vin_out_of_scope"


def test_operator_out_of_scope_section_returns_403(
    client, db_session, seed_operator, monkeypatch, emergency_payload
):
    configure_emergency(db_session, monkeypatch, emergency_payload, allow_vin=False)
    monkeypatch.setattr(emergency_scope, "vin_allowed_for_user", lambda *_a, **_k: False)
    login_as(client, "operator1", "secret")

    response = client.get("/emergency/YASADR00000000447/sections/status")

    assert response.status_code == 403
    assert response.json()["detail"] == "emergency_vin_out_of_scope"


def test_mechanic_alias_out_of_scope_section_returns_403(
    client, db_session, seed_mechanic, monkeypatch, emergency_payload
):
    configure_emergency(db_session, monkeypatch, emergency_payload, allow_vin=False)
    monkeypatch.setattr(emergency_scope, "vin_allowed_for_user", lambda *_a, **_k: False)
    login_as(client, "mech1", "secret")

    response = client.get("/mechanic/emergency/YASADR00000000447/sections/status")

    assert response.status_code == 403
    assert response.json()["detail"] == "emergency_vin_out_of_scope"


def test_snapshot_rechecks_scope_after_resolve_before_redis_grants(
    client, db_session, seed_operator, monkeypatch, emergency_payload
):
    allowed = True

    def maybe_allow(*_args, **_kwargs):
        return allowed

    configure_emergency(db_session, monkeypatch, emergency_payload, allow_vin=False)
    monkeypatch.setattr(emergency_scope, "vin_allowed_for_user", maybe_allow)
    login_as(client, "operator1", "secret")

    assert client.post("/emergency/resolve", json={"robot_number": "447"}).status_code == 200
    allowed = False

    response = client.get("/emergency/YASADR00000000447/snapshot")
    assert response.status_code == 403
    assert response.json()["detail"] == "emergency_vin_out_of_scope"
```

### Step 2: Run the focused tests and confirm RED

From `apps/api`:

```sh
.venv/bin/pytest -q \
  tests/test_emergency_router.py::test_operator_out_of_scope_snapshot_returns_403 \
  tests/test_emergency_router.py::test_operator_out_of_scope_section_returns_403 \
  tests/test_emergency_router.py::test_mechanic_alias_out_of_scope_section_returns_403 \
  tests/test_emergency_router.py::test_snapshot_rechecks_scope_after_resolve_before_redis_grants
```

Expected: all four fail because snapshot/section currently return `200` after skipping VIN scope.

### Step 3: Add the minimal enforcement calls

In both shared helpers, call the existing guard after normalization and before `_get_robot_payload`:

```python
def emergency_section_for_user(
    vin: str, section_id: str, user: User, db: Session
) -> EmergencySectionOut:
    try:
        vin = emergency_vin.normalize_robot_id(vin)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid_robot_number",
        ) from exc

    _enforce_vin_scope(db, user, vin)
    payload = _get_robot_payload(db, vin)
```

```python
def emergency_snapshot_for_user(vin: str, user: User, db: Session) -> EmergencySnapshotOut:
    try:
        vin = emergency_vin.normalize_robot_id(vin)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid_robot_number",
        ) from exc

    _enforce_vin_scope(db, user, vin)
    payload = _get_robot_payload(db, vin)
```

Do not edit `emergency_scope.vin_allowed_for_user`: its admin/royal/driver exceptions are deliberate current product behavior.

### Step 4: Run GREEN and Emergency regressions

```sh
cd apps/api
.venv/bin/pytest -q \
  tests/test_emergency_router.py::test_operator_out_of_scope_snapshot_returns_403 \
  tests/test_emergency_router.py::test_operator_out_of_scope_section_returns_403 \
  tests/test_emergency_router.py::test_mechanic_alias_out_of_scope_section_returns_403 \
  tests/test_emergency_router.py::test_snapshot_rechecks_scope_after_resolve_before_redis_grants
.venv/bin/pytest -q \
  tests/test_emergency_router.py \
  tests/test_mechanic_emergency.py \
  tests/test_emergency_scope.py \
  tests/test_driver_emergency_scope.py
git diff --check
```

Expected: focused tests and the four Emergency modules pass.

### Step 5: Commit

```sh
git add apps/api/src/robopark_api/routers/emergency.py apps/api/tests/test_emergency_router.py
git commit -m "fix(api): restore Emergency VIN scope on direct reads"
```

---

## Task 2: Make blocker-history time deterministic

**Task interface**

- `history_series(db: Session, *, park_id: int, days: int = 7, now: datetime | None = None) -> list[dict]`.
- `delete_old_buckets(db: Session, *, park_id: int | None = None, retention_days: int = 30, now: datetime | None = None) -> int`.
- `scan_all_parks_once(db, now=scan_time)` нормализует один `now_utc` и передаёт тот же instant в выбор bucket и retention. Existing callers remain source-compatible because `now` is optional and keyword-only.

**Files:**

- Modify: `apps/api/tests/test_blocker_history.py`
- Modify: `apps/api/src/robopark_api/services/blocker_history.py`

### Step 1: Freeze every date-sensitive test

Add a module constant:

```python
FIXED_NOW = datetime(2026, 8, 24, 14, 30, tzinfo=UTC)
```

Pass `now=FIXED_NOW` to each `history_series` or `delete_old_buckets` call that asserts inclusion/retention of fixed buckets. In `test_scan_all_parks_once_runs_retention_after_success`, capture both arguments:

```python
retention_calls: list[tuple[int, datetime]] = []

monkeypatch.setattr(
    history_svc,
    "delete_old_buckets",
    lambda db, **kwargs: retention_calls.append(
        (kwargs["retention_days"], kwargs["now"])
    ),
)

scanned = scan_all_parks_once(db_session, now=FIXED_NOW)

assert scanned == 1
assert retention_calls == [(30, FIXED_NOW)]
```

Retain boundary coverage at exactly 7/30 days and ensure `_as_utc` behavior is exercised by at least one naive `now` value.

### Step 2: Run the current regression and confirm RED

```sh
cd apps/api
.venv/bin/pytest -q tests/test_blocker_history.py
```

Expected before production change: calls with `now=` raise `TypeError`, and retention does not receive the scan instant.

### Step 3: Inject and normalize the clock

Implement cutoffs from a single normalized instant:

```python
def history_series(
    db: Session,
    *,
    park_id: int,
    days: int = 7,
    now: datetime | None = None,
) -> list[dict]:
    cutoff = _as_utc(now or datetime.now(UTC)) - timedelta(days=days)
```

```python
def delete_old_buckets(
    db: Session,
    *,
    park_id: int | None = None,
    retention_days: int = 30,
    now: datetime | None = None,
) -> int:
    cutoff = _as_utc(now or datetime.now(UTC)) - timedelta(days=retention_days)
```

In `scan_all_parks_once`:

```python
if scanned > 0:
    delete_old_buckets(db, retention_days=30, now=now_utc)
```

### Step 4: Run GREEN and backend regression

```sh
cd apps/api
.venv/bin/pytest -q tests/test_blocker_history.py
PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest -p no:cacheprovider -q
git diff --check
```

Expected: `tests/test_blocker_history.py` and the full backend suite pass.

### Step 5: Commit

```sh
git add apps/api/src/robopark_api/services/blocker_history.py apps/api/tests/test_blocker_history.py
git commit -m "fix(api): make blocker history time deterministic"
```

---

## Task 3: Restore frontend hook, lint, and build gates

**Task interface**

- `Analytics` always calls only its two context hooks.
- Operator rendering mounts `OperatorNowReport` as a child.
- Non-operator dashboard loading lives in a separate child that owns `useCachedResource`.
- Rerendering the same `Analytics` instance from operator to admin cannot change the parent hook order.

**Files:**

- Create: `apps/web/src/pages/Analytics.test.tsx`
- Modify: `apps/web/src/pages/Analytics.tsx`
- Modify: `apps/web/src/nav-permissions.test.ts`

### Step 1: Add a real role-transition regression

Create `Analytics.test.tsx` using `MemoryRouter`, `AuthContext.Provider` and `ParkContext.Provider`. Keep `parks=[]` and `parkId=null` so neither child starts a network request. The test must rerender the same component tree:

```tsx
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import type { User } from '../api'
import { AuthContext, type AuthContextValue } from '../auth-context'
import { ParkContext, type ParkContextValue } from '../park-context'
import { Analytics } from './Analytics'

const parkContext: ParkContextValue = {
  parkId: null,
  setParkId: vi.fn(),
  parks: [],
  parksLoading: false,
  parkLocked: false,
}

function renderForRole(role: string) {
  const user: User = {
    id: 1,
    username: 'person',
    role,
    access_status: 'approved',
    permissions: role === 'admin' ? ['nav.dashboard'] : [],
    parks: [],
  }
  const auth: AuthContextValue = {
    user,
    loading: false,
    login: vi.fn(),
    refreshUser: vi.fn(),
    logout: vi.fn(),
  }
  return (
    <MemoryRouter>
      <AuthContext.Provider value={auth}>
        <ParkContext.Provider value={parkContext}>
          <Analytics />
        </ParkContext.Provider>
      </AuthContext.Provider>
    </MemoryRouter>
  )
}

describe('Analytics', () => {
  it('can switch from operator to admin without changing hook order', () => {
    const view = render(renderForRole('operator'))
    expect(screen.getByRole('heading', { name: 'Сейчас по Tracker' })).toBeInTheDocument()

    view.rerender(renderForRole('admin'))

    expect(
      screen.getByText('Расширенная аналитика скоро появится'),
    ).toBeInTheDocument()
  })
})
```

If the heading role differs because of `PageShell`, assert the visible title text without changing product markup merely for the test.

### Step 2: Run the test and lint to confirm RED

```sh
cd apps/web
npm test -- src/pages/Analytics.test.tsx
npm run lint
```

Expected: rerender throws a React hook-order error; lint reports the conditional `useCachedResource` and the unused `describe` import.

### Step 3: Split the non-operator hook owner

Move the current non-operator resource call and JSX into a child such as:

```tsx
type DashboardAnalyticsProps = {
  role: string
  permissions: string[]
  parkId: number | null
  parksLoading: boolean
}

function DashboardAnalytics({
  role,
  permissions,
  parkId,
  parksLoading,
}: DashboardAnalyticsProps) {
  const hasDashboard = permissions.includes('nav.dashboard')
  const summaryRes = useCachedResource(
    parkId == null ? '' : `dashboard:summary:${parkId}`,
    () => api.dashboardSummary(parkId as number),
    { enabled: parkId != null && !parksLoading && hasDashboard },
  )
  const summary = summaryRes.data ?? null

  return (
    <PageShell subtitle="Раздел в разработке" title={ru.nav.analytics}>
      {hasDashboard && parkId != null && (
        <Panel
          hint="Те же цифры, что на дашборде — удобно сверить нагрузку, не уходя с раздела."
          title="Сегодня"
        >
          {summaryRes.isLoading && !summary ? (
            <SkeletonKpi />
          ) : summary ? (
            <div className="dashboard-kpi-grid">
              <div className="dashboard-kpi tone-arrived">
                <span className="dashboard-kpi-label">Пришли</span>
                <span className="dashboard-kpi-value">{summary.arrived}</span>
              </div>
              <div className="dashboard-kpi tone-done">
                <span className="dashboard-kpi-label">Ушли</span>
                <span className="dashboard-kpi-value">{summary.done}</span>
              </div>
              <div className="dashboard-kpi tone-queued">
                <span className="dashboard-kpi-label">В очереди</span>
                <span className="dashboard-kpi-value">{summary.queued}</span>
              </div>
            </div>
          ) : (
            <EmptyBlock icon="📋" title="Нет данных за сегодня" />
          )}
        </Panel>
      )}

      <EmptyBlock
        action={
          hasDashboard ? (
            <Link className="btn btn-secondary" to="/dashboard">
              Перейти на дашборд
            </Link>
          ) : undefined
        }
        hint={analyticsHint(role)}
        icon="◔"
        title="Расширенная аналитика скоро появится"
      />
    </PageShell>
  )
}

export function Analytics() {
  const { user } = useAuth()
  const { parkId, parksLoading } = useParkContext()

  if (user?.role === 'operator') {
    return <OperatorNowReport />
  }

  return (
    <DashboardAnalytics
      parkId={parkId}
      parksLoading={parksLoading}
      permissions={user?.permissions ?? []}
      role={user?.role ?? ''}
    />
  )
}
```

The code block preserves the current text, layout, query key, cache policy and API call exactly; only hook ownership changes.

Remove only `describe` from the import in `nav-permissions.test.ts`:

```ts
import { expect, it } from 'vitest'
```

### Step 4: Run all frontend gates

```sh
cd apps/web
npm test -- src/pages/Analytics.test.tsx
npm run lint
npm run build
npm test
npm run check-nav
```

Expected: every command exits `0`; lint may print the seven explicitly deferred warnings but no errors.

### Step 5: Commit

```sh
git add apps/web/src/pages/Analytics.tsx apps/web/src/pages/Analytics.test.tsx apps/web/src/nav-permissions.test.ts
git diff --check
git commit -m "fix(web): restore lint and build gates"
```

---

## Task 4: Restore migration 0015 and report attachment parity

**Task interface**

- Constants: `KIND_UI_SNAPSHOT = "ui_snapshot"`, `KIND_DEVICE_PHOTO = "device_photo"`, `KIND_CLIENT_LOG = "client_log"`, `MAX_LOG_BYTES = 64 * 1024`.
- Root/name/limit functions: `attachments_root() -> Path`, `sanitize_filename(name: str | None, *, fallback: str) -> str`, `max_bytes_for_kind(kind: str) -> int`.
- Write function: `add_attachment(db: Session, user: User, report_id: int, *, kind: str, filename: str | None, content: bytes, content_type: str | None) -> ReportAttachment`.
- Read function: `get_attachment(db: Session, user: User, report_id: int, attachment_id: int) -> tuple[ReportAttachment, Path]`.
- Preflight function: `validate_attachment_storage(db: Session) -> int`.

Contract: one attachment per `(report_id, kind)`; only the open report author uploads; existing report-view ACL controls download; image limit is the existing 15 MiB tracker limit; client log limit is 64 KiB; filesystem paths cannot escape the configured root; metadata is committed only with a successfully placed file.

**Files:**

- Create: `apps/api/alembic/versions/0015_report_attachments.py`
- Create: `apps/api/src/robopark_api/services/report_attachments.py`
- Create: `apps/api/src/robopark_api/verify_report_attachments.py`
- Create: `apps/api/tests/test_report_attachments.py`
- Modify: `apps/api/src/robopark_api/config.py`
- Modify: `apps/api/src/robopark_api/models.py`
- Modify: `apps/api/src/robopark_api/schemas.py`
- Modify: `apps/api/src/robopark_api/routers/reports.py`
- Modify: `apps/api/src/robopark_api/services/reports.py`
- Modify: `apps/api/tests/conftest.py`
- Modify: `apps/api/tests/test_models_migration.py`
- Modify: `apps/api/tests/test_dockerfile_data.py`
- Modify: `apps/api/pyproject.toml`
- Modify: `apps/web/nginx.conf`
- Modify: `deploy/docker-compose.yml`
- Modify: `.gitignore`

### Step 1: Add migration structure tests first

Extend `test_metadata_has_required_tables` with `report_attachments`. Add a head assertion using Alembic `ScriptDirectory` and an inspector test that verifies exact columns, the cascade FK, unique pair and report index:

```python
def test_alembic_head_is_report_attachments():
    api_dir = Path(__file__).parents[1]
    script = ScriptDirectory.from_config(Config(api_dir / "alembic.ini"))
    assert script.get_heads() == ["0015_report_attachments"]


def test_migrated_report_attachments_contract(sqlite_database_url, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", sqlite_database_url)
    api_dir = Path(__file__).parents[1]
    command.upgrade(Config(api_dir / "alembic.ini"), "head")
    inspector = inspect(create_engine(sqlite_database_url, future=True))

    assert {column["name"] for column in inspector.get_columns("report_attachments")} == {
        "id",
        "report_id",
        "kind",
        "filename",
        "content_type",
        "size_bytes",
        "storage_key",
        "created_at",
    }
    foreign_keys = inspector.get_foreign_keys("report_attachments")
    assert len(foreign_keys) == 1
    assert foreign_keys[0]["constrained_columns"] == ["report_id"]
    assert foreign_keys[0]["referred_table"] == "reports"
    assert foreign_keys[0]["options"]["ondelete"] == "CASCADE"
    assert any(
        constraint["column_names"] == ["report_id", "kind"]
        for constraint in inspector.get_unique_constraints("report_attachments")
    )
    assert any(
        index["column_names"] == ["report_id"]
        for index in inspector.get_indexes("report_attachments")
    )
```

Import `ScriptDirectory` from `alembic.script`.

### Step 2: Add attachment behavior and boundary tests

Recover the focused happy-path, duplicate-kind, non-author, HTTP upload/download, royal-upload deny and invalid-MIME tests from the reference commit into `test_report_attachments.py`, then add these missing cases:

- `test_closed_report_cannot_accept_attachment`: mark a report `done`, call `add_attachment`, expect `PermissionError("forbidden")`, and assert no file appears;
- `test_cross_park_operator_cannot_download_attachment`: create a second active park and an approved operator assigned only to it, upload as the author, login as that operator and expect HTTP `403` on the direct attachment URL;
- `test_storage_key_cannot_escape_attachment_root`: insert metadata with `storage_key="../outside.bin"`, expect `LookupError("report_attachment_not_found")` even if the outside file exists;
- `test_write_failure_rolls_back_metadata`: make the configured root non-writable by monkeypatching the atomic writer to raise `OSError`, expect the exception and assert no `ReportAttachment` row remains;
- `test_validate_attachment_storage_reports_only_generic_failure`: create one missing or wrong-sized file, expect `AttachmentStorageError("report attachment storage validation failed")` and assert it contains neither `filename`, `storage_key` nor a row/file count;
- `test_upload_stops_after_kind_limit_plus_one`: upload an over-limit log and expect `400 report_attachment_too_large` without reading an unbounded request body.

Update `test_settings` in `conftest.py` with:

```python
report_attachments_dir=str(tmp_path / "report-attachments"),
```

### Step 3: Add the Nginx and Compose static contracts

In `test_dockerfile_data.py`, define repository paths and assert:

```python
REPO_ROOT = Path(__file__).resolve().parents[3]


def test_report_attachment_upload_and_persistent_storage_are_aligned():
    nginx = (REPO_ROOT / "apps/web/nginx.conf").read_text(encoding="utf-8")
    compose = (REPO_ROOT / "deploy/docker-compose.yml").read_text(encoding="utf-8")
    assert "client_max_body_size 16m;" in nginx
    assert "REPORT_ATTACHMENTS_DIR: /data/report-attachments" in compose
```

The existing `/api/admin/ops/` override must remain `512m`.

### Step 4: Run the new tests and confirm RED

```sh
cd apps/api
PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest -p no:cacheprovider \
  tests/test_models_migration.py \
  tests/test_report_attachments.py \
  tests/test_dockerfile_data.py -q
```

Expected: missing revision/module/model/schema and the current `2m`/missing storage environment make the new tests fail.

### Step 5: Restore the exact Alembic revision

Create `0015_report_attachments.py` with the existing installed revision identity:

```python
"""Report file attachments.

Revision ID: 0015
Revises: 0014_user_permissions
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015_report_attachments"
down_revision: str | None = "0014_user_permissions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "report_attachments",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("report_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("filename", sa.String(length=256), nullable=False),
        sa.Column("content_type", sa.String(length=128), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("storage_key", sa.String(length=512), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["report_id"], ["reports.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("report_id", "kind", name="uq_report_attachments_report_kind"),
    )
    op.create_index(
        op.f("ix_report_attachments_report_id"),
        "report_attachments",
        ["report_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_report_attachments_report_id"), table_name="report_attachments")
    op.drop_table("report_attachments")
```

### Step 6: Restore model, config, and response schema parity

- add `Settings.report_attachments_dir: str | None = None`;
- add `Report.attachments` with `cascade="all, delete-orphan"`;
- add `ReportAttachment` with exact migration lengths, unique constraint and cascade FK;
- add `ReportAttachmentOut` and `ReportOut.attachments`;
- eager-load attachments in `_load_report`, `list_inbox` and `list_mine` using `selectinload`.

Do not copy unrelated role/report-routing changes from the reference commit. The model relationship is:

```python
attachments: Mapped[list["ReportAttachment"]] = relationship(
    back_populates="report",
    cascade="all, delete-orphan",
    order_by="ReportAttachment.id",
)
```

and `ReportAttachment` ends with:

```python
report: Mapped[Report] = relationship(back_populates="attachments")
```

### Step 7: Implement safe attachment storage

Start from the recovered service constants and MIME helpers, but add three safety properties absent from the lost commit:

1. `_resolve_storage_key` resolves under `attachments_root()` and rejects escape;
2. `_atomic_write` writes to a sibling temporary file, fsyncs it and atomically links it into a previously absent destination;
3. any write/flush/commit failure rolls back SQL and removes only the file created for that new row.

Use this path guard:

```python
def _resolve_storage_key(storage_key: str) -> Path:
    root = attachments_root().resolve()
    candidate = (root / storage_key).resolve()
    if not candidate.is_relative_to(root):
        raise LookupError("report_attachment_not_found")
    return candidate
```

Use an exclusive temporary file and an atomic no-overwrite hard-link placement so a stale pre-existing destination is never replaced:

```python
def _atomic_write(destination: Path, content: bytes) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=".report-upload-",
        dir=destination.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(file_descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, destination)
    finally:
        with suppress(OSError):
            temporary.unlink()
```

Track whether `_atomic_write` completed. On failure before `db.commit()`, call `db.rollback()` and unlink the destination only when this invocation placed it. Call `db.refresh(row)` after the guarded commit block so a refresh failure can never remove a file whose metadata is already committed.

Define `max_bytes_for_kind` before reading the upload:

```python
def max_bytes_for_kind(kind: str) -> int:
    if kind == KIND_CLIENT_LOG:
        return MAX_LOG_BYTES
    if kind in {KIND_UI_SNAPSHOT, KIND_DEVICE_PHOTO}:
        return MAX_ATTACHMENT_BYTES
    raise ValueError("report_attachment_kind_invalid")
```

`validate_attachment_storage(db)` must iterate metadata, validate safe containment, existence and exact `size_bytes`, then either return the checked count to the in-process caller or raise `AttachmentStorageError("report attachment storage validation failed")`. It must never include counts, filenames, storage keys or file content in output/logs.

### Step 8: Add bounded HTTP upload/download routes

Add only attachment imports and endpoints to `routers/reports.py`. Bound the read before calling the service:

```python
@router.post(
    "/{report_id}/attachments",
    response_model=ReportAttachmentOut,
    status_code=status.HTTP_201_CREATED,
)
async def add_report_attachment(
    report_id: int,
    kind: str = Form(...),
    file: UploadFile = File(...),
    user: User = Depends(_require_report_author),
    db: Session = Depends(get_db),
) -> ReportAttachmentOut:
    limit = _run_svc(lambda: att_svc.max_bytes_for_kind(kind))
    content = await file.read(limit + 1)
    row = _run_svc(
        lambda: att_svc.add_attachment(
            db,
            user,
            report_id,
            kind=kind,
            filename=file.filename,
            content=content,
            content_type=file.content_type,
        )
    )
    return ReportAttachmentOut.model_validate(row)
```

Download uses `_run_svc(lambda: att_svc.get_attachment(db, user, report_id, attachment_id))` and returns `FileResponse(path, media_type=row.content_type, filename=row.filename)`.

### Step 9: Add a safe operator preflight command

Create `robopark_api.verify_report_attachments:main`, use `SessionLocal`, call `validate_attachment_storage`, ignore its integer return value, and print only:

```text
report attachment storage verified
```

On mismatch, exit non-zero with the same generic failure message only. Register it in `pyproject.toml`:

```toml
[project.scripts]
robopark-verify-attachments = "robopark_api.verify_report_attachments:main"
```

Production invocation after mounting the current volume and before rollout is:

```sh
docker compose -f deploy/docker-compose.yml run --rm --no-deps api \
  robopark-verify-attachments
```

If it fails, stop the rollout and restore the missing binaries from the host backup. Do not fabricate rows/files and do not log their names.

### Step 10: Persist files and align proxy limits

- remove the global `uv.lock` ignore only in Task 7; in this task add `apps/api/data/report-attachments/` to `.gitignore`;
- set `REPORT_ATTACHMENTS_DIR: /data/report-attachments` in the API Compose environment so the existing `/data` volume persists files;
- change only Nginx's default `client_max_body_size` from `2m` to `16m`; retain the ops override at `512m`.

### Step 11: Run migration, service, and report regressions

```sh
cd apps/api
PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest -p no:cacheprovider \
  tests/test_models_migration.py \
  tests/test_report_attachments.py \
  tests/test_reports.py \
  tests/test_dockerfile_data.py -q
.venv/bin/alembic heads
```

Expected head:

```text
0015_report_attachments (head)
```

### Step 12: Verify the existing SQLite history on a backup copy only

Run from `apps/api`; never run Alembic upgrade against the live file:

```sh
snapshot_dir="$(mktemp -d /private/tmp/robopark-0015-verify.XXXXXX)"
.venv/bin/python -c \
  'import sqlite3,sys; source=sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True); destination=sqlite3.connect(sys.argv[2]); source.backup(destination); destination.close(); source.close()' \
  "$PWD/data/robopark.db" "$snapshot_dir/robopark.db"
DATABASE_URL="sqlite:///$snapshot_dir/robopark.db" .venv/bin/alembic current
```

Expected: `0015_report_attachments (head)`. Preserve the original DB and attachment directory unchanged. Remove only the exact `snapshot_dir` after confirming it is under `/private/tmp/robopark-0015-verify.*`.

### Step 13: Full backend regression and commit

```sh
cd apps/api
PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest -p no:cacheprovider -q
cd ../..
git diff --check
git diff --name-only
git add \
  .gitignore \
  apps/api/alembic/versions/0015_report_attachments.py \
  apps/api/pyproject.toml \
  apps/api/src/robopark_api/config.py \
  apps/api/src/robopark_api/models.py \
  apps/api/src/robopark_api/schemas.py \
  apps/api/src/robopark_api/routers/reports.py \
  apps/api/src/robopark_api/services/report_attachments.py \
  apps/api/src/robopark_api/services/reports.py \
  apps/api/src/robopark_api/verify_report_attachments.py \
  apps/api/tests/conftest.py \
  apps/api/tests/test_dockerfile_data.py \
  apps/api/tests/test_models_migration.py \
  apps/api/tests/test_report_attachments.py \
  apps/web/nginx.conf \
  deploy/docker-compose.yml
git commit -m "fix(reports): restore attachment migration and storage"
```

Before committing, confirm `git diff --cached --name-only` contains no `apps/api/data` entry.

---

## Task 5: Preserve host secrets and wait for readiness in the current deploy path

**Task interface**

`deploy/ops-agent.sh` supports:

```text
OPS_ROOT=/data/ops
HOST_REPO=/host-repo
HOST_ENV_FILE=$HOST_REPO/deploy/host.env
READY_TIMEOUT_SECONDS=180
OPS_AGENT_COPY_MODE=auto|rsync|copy
argv[1]=watch|once
```

Defaults remain production-compatible. `once` processes at most one current request and exits for functional tests. `copy` is a deterministic test/fallback mode. A successful job is reported only after Compose `--wait` confirms API/web health.

**Files:**

- Create: `apps/api/tests/test_ops_agent.py`
- Modify: `deploy/ops-agent.sh`
- Modify: `deploy/docker-compose.yml`
- Modify: `deploy/README.md`

### Step 1: Add functional shell tests with temporary host trees

Use `subprocess.run(["sh", str(OPS_AGENT), "once"], env=test_env, check=False, timeout=5)` and temporary directories. `test_env` contains only `PATH`, `OPS_ROOT`, `HOST_REPO`, `HOST_ENV_FILE`, `READY_TIMEOUT_SECONDS`, `OPS_AGENT_COPY_MODE`, `FAKE_DOCKER_EXIT` and `FAKE_DOCKER_LOG`. Build a fake `docker` executable in a temporary `bin/` that records arguments and exits according to `FAKE_DOCKER_EXIT`.

Required tests:

1. `test_copy_fallback_preserves_live_env_and_updates_regular_file`:
   - live repo contains `deploy/host.env` and `config/.env.production` with sentinel text;
   - staging contains conflicting files with the same secret-looking names plus a changed ordinary file;
   - run with `OPS_AGENT_COPY_MODE=copy`;
   - assert both live env sentinels are unchanged, staging secret files are removed, and the ordinary file is updated.
2. `test_success_waits_for_ready_services`:
   - fake Docker exits `0`;
   - assert captured argv equals `compose -f /temporary/host/deploy/docker-compose.yml up -d --build --wait --wait-timeout 180 api web`, with the test's actual temporary host path substituted by `str(host_repo / "deploy/docker-compose.yml")`;
   - assert result JSON is `{"job_id": "job-1", "ok": true}`.
3. `test_compose_failure_is_reported`:
   - fake Docker exits non-zero;
   - assert result JSON has `ok: false` and `error: compose_failed`.
4. `test_unsafe_source_is_rejected_without_touching_host_repo`:
   - parameterize a source outside `$OPS_ROOT/staging/`, a `staging/../outside` traversal and a symlink resolving outside staging;
   - assert `unsafe_src` and byte-for-byte unchanged host files.

### Step 2: Run tests and confirm RED

```sh
cd apps/api
.venv/bin/pytest -q tests/test_ops_agent.py
```

Expected: current script ignores test overrides, loops forever, copy fallback can remove live env, and Compose lacks `--wait`.

### Step 3: Refactor the agent into one-job and watch modes

At the top, make paths configurable without weakening production defaults:

```sh
OPS_ROOT="${OPS_ROOT:-/data/ops}"
JOB_FLAG="$OPS_ROOT/rebuild.requested"
RESULT_FILE="$OPS_ROOT/rebuild.result"
HOST_REPO="${HOST_REPO:-/host-repo}"
COMPOSE_FILE="${COMPOSE_FILE:-$HOST_REPO/deploy/docker-compose.yml}"
HOST_ENV_FILE="${HOST_ENV_FILE:-$HOST_REPO/deploy/host.env}"
READY_TIMEOUT_SECONDS="${READY_TIMEOUT_SECONDS:-180}"
OPS_AGENT_COPY_MODE="${OPS_AGENT_COPY_MODE:-auto}"
MODE="${1:-watch}"
```

Extract `process_job`. In `watch`, call `process_job || true` then sleep. In `once`, call it once and exit. Keep JSON sanitization, but replace the textual source-prefix check with canonical containment:

```sh
if [ ! -d "$SRC" ] || [ ! -d "$OPS_ROOT/staging" ]; then
  write_result "${JOB_ID:-unknown}" false "staging_missing"
  return 1
fi
STAGING_ROOT_REAL=$(realpath "$OPS_ROOT/staging") || {
  write_result "${JOB_ID:-unknown}" false "unsafe_src"
  return 1
}
SRC_REAL=$(realpath "$SRC") || {
  write_result "${JOB_ID:-unknown}" false "unsafe_src"
  return 1
}
case "$SRC_REAL" in
  "$STAGING_ROOT_REAL"/*) SRC="$SRC_REAL" ;;
  *) write_result "${JOB_ID:-unknown}" false "unsafe_src"; return 1 ;;
esac
```

Both the macOS test host and the Alpine ops image provide `realpath`. This rejects `..` traversal and symlinks resolving outside staging before any sanitization or copy.

### Step 4: Sanitize staging, never the live checkout

Before either rsync or cp, remove secret-looking regular files/symlinks only below the already validated `$SRC`:

```sh
sanitize_staging() {
  find "$SRC" \
    \( -type f -o -type l \) \
    \( -name 'host.env' -o -name 'tuna.env' -o -name '.env' -o -name '.env.*' \) \
    ! -name '*.env.example' \
    -exec rm -f -- {} \;
}
```

If sanitization or copy fails, write `sanitize_failed` or `copy_failed`, remove only the job flag, and leave `$HOST_REPO` cleanup untouched. Delete the current command that recursively searches `$HOST_REPO` for env-named files.

Select copy mode explicitly:

```sh
case "$OPS_AGENT_COPY_MODE" in
  auto) command -v rsync >/dev/null 2>&1 && copy_mode=rsync || copy_mode=copy ;;
  rsync|copy) copy_mode="$OPS_AGENT_COPY_MODE" ;;
  *) write_result "$JOB_ID" false "invalid_copy_mode"; return 1 ;;
esac
```

Rsync keeps runtime/tooling excludes; cp copies the already sanitized staging tree.

### Step 5: Require readiness before success

Export the resolved `HOST_ENV_FILE` and run:

```sh
if docker compose -f "$COMPOSE_FILE" up -d --build \
  --wait --wait-timeout "$READY_TIMEOUT_SECONDS" api web; then
  write_result "$JOB_ID" true
else
  write_result "$JOB_ID" false "compose_failed"
fi
```

Change the API Compose healthcheck URL from `/health` to `/health/ready`. Do not make soft integration degradation fail readiness; the existing endpoint returns `503` only for a broken database.

### Step 6: Document the one-time bootstrap boundary

The release containing this fix cannot safely rely on the already running old fallback agent. Add an explicit first-upgrade section to `deploy/README.md`:

```sh
# Run from the Robopark checkout root on the host.
git pull --ff-only origin main
test -s deploy/host.env
HOST_ENV_FILE=./host.env docker compose -f deploy/docker-compose.yml \
  up -d --build --wait --wait-timeout 180 api web ops-agent
```

State that this first upgrade is a protected/manual host operation with a host backup of `deploy/host.env`; later Royal ZIP updates can use the fixed current agent. Do not include env contents in instructions or logs.

### Step 7: Run functional and ops regressions

```sh
cd apps/api
.venv/bin/pytest -q tests/test_ops_agent.py tests/test_ops_runner.py tests/test_ops_http.py
cd ../..
sh -n deploy/ops-agent.sh
git diff --check
```

Expected: all tests pass and POSIX shell syntax is valid.

### Step 8: Commit

```sh
git add apps/api/tests/test_ops_agent.py deploy/ops-agent.sh deploy/docker-compose.yml deploy/README.md
git commit -m "fix(deploy): preserve host secrets and wait for readiness"
```

---

## Task 6: Restore a mechanical Ruff lint/format baseline

**Task interface**

- `ruff check .` returns zero errors without `noqa` additions or unsafe fixes.
- `ruff format --check .` returns zero unformatted files.
- Production behavior and public APIs remain unchanged.

**Files:**

- Safe lint/import fixes currently identified in:
  `apps/api/src/robopark_api/deps.py`,
  `apps/api/src/robopark_api/routers/admin_ops.py`,
  `apps/api/src/robopark_api/routers/auth.py`,
  `apps/api/src/robopark_api/routers/reports.py`,
  `apps/api/src/robopark_api/routers/tracker_actions.py`,
  `apps/api/src/robopark_api/routers/tracker_read.py`,
  `apps/api/src/robopark_api/seed.py`,
  `apps/api/src/robopark_api/services/emergency_scope.py`,
  `apps/api/src/robopark_api/services/ops/reconcile.py`,
  `apps/api/src/robopark_api/services/ops/runner.py`,
  `apps/api/src/robopark_api/services/ops/snapshot.py`,
  `apps/api/src/robopark_api/services/rbac_seed.py`,
  `apps/api/tests/test_admin_user_access.py`,
  `apps/api/tests/test_emergency_cookie_report.py`,
  `apps/api/tests/test_reports.py`,
  `apps/api/tests/test_security_hardening.py`,
  `apps/api/tests/test_tracker_actions.py`.
- Formatter baseline currently identified in:
  `apps/api/alembic/versions/0013_rbac_roles.py`,
  `apps/api/src/robopark_api/crypto.py`,
  `apps/api/src/robopark_api/deps.py`,
  `apps/api/src/robopark_api/middleware/maintenance.py`,
  `apps/api/src/robopark_api/models.py`,
  `apps/api/src/robopark_api/routers/admin_ops.py`,
  `apps/api/src/robopark_api/routers/admin_roles.py`,
  `apps/api/src/robopark_api/routers/admin_users.py`,
  `apps/api/src/robopark_api/routers/auth.py`,
  `apps/api/src/robopark_api/routers/mechanic_robots.py`,
  `apps/api/src/robopark_api/routers/tracker_actions.py`,
  `apps/api/src/robopark_api/routers/tracker_read.py`,
  `apps/api/src/robopark_api/seed.py`,
  `apps/api/src/robopark_api/services/emergency_snapshot.py`,
  `apps/api/src/robopark_api/services/ops/archives.py`,
  `apps/api/src/robopark_api/services/rbac.py`,
  `apps/api/src/robopark_api/services/tracker_signatures.py`,
  `apps/api/tests/test_admin_user_access.py`,
  `apps/api/tests/test_crypto_secrets.py`,
  `apps/api/tests/test_driver_emergency_scope.py`,
  `apps/api/tests/test_emergency_config_service.py`,
  `apps/api/tests/test_mechanic_tasks.py`,
  `apps/api/tests/test_ops_http.py`,
  `apps/api/tests/test_ops_runner.py`,
  `apps/api/tests/test_security_seed.py`,
  `apps/api/tests/test_tracker_actions.py`,
  `apps/api/tests/test_tracker_cache.py`,
  `apps/api/tests/test_tracker_policy.py`,
  `apps/api/tests/test_tracker_read.py`,
  `apps/api/tests/test_tracker_signatures.py`.
- After Tasks 1–5, format the whole `apps/api` tree once so new and already edited Python files share the same baseline; review any additional path Ruff reports before staging it.

### Step 1: Record precondition and run safe automatic fixes

```sh
cd apps/api
.venv/bin/ruff check . --output-format concise
.venv/bin/ruff check . --fix
```

Expected first command: the known errors or the remaining subset after prior edits. Do not use `--unsafe-fixes`.

### Step 2: Apply the four explicit semantic-neutral cleanups

- `routers/admin_ops.py`: import `suppress` from `contextlib` and replace the broad cleanup `try/except/pass` with `with suppress(Exception):`;
- `routers/reports.py`: in both exception handlers inside `_run_svc`, raise the mapped `HTTPException` with `from None`;
- `services/ops/runner.py`: return `path.name in _SECRET_BASENAMES` directly;
- `services/ops/snapshot.py`: use `with suppress(OSError):` for best-effort unlink.

Do not add blanket lint ignores. Safe automatic fixes remove only unused imports and sort import blocks.

### Step 3: Format once and inspect the mechanical diff

```sh
cd apps/api
.venv/bin/ruff format .
.venv/bin/ruff check .
.venv/bin/ruff format --check .
git diff --check
git diff --stat
```

Inspect every non-whitespace hunk; expected non-format changes are limited to the four cleanups and safe lint removals/import ordering.

### Step 4: Run the full backend suite

```sh
cd apps/api
PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest -p no:cacheprovider -q
```

Expected: full suite passes.

### Step 5: Commit the baseline separately

```sh
cd ../..
git add apps/api/alembic apps/api/src apps/api/tests
git diff --cached --check
git commit -m "style(api): restore ruff baseline"
```

Do not stage runtime data, `.venv`, caches or unrelated frontend/deploy files.

---

## Task 7: Freeze dependencies, pin artifacts, and unify verification

**Task interface**

```text
./scripts/verify.sh api     # frozen API sync + Ruff + pytest
./scripts/verify.sh web     # npm ci + lint + build + tests + nav
./scripts/verify.sh docker  # shell/Compose contracts + production images
./scripts/verify.sh         # all targets; canonical local/CI gate
```

No target may silently skip a requested tool. Missing Docker makes `docker`/`all` fail clearly. CI executes the same canonical no-argument command.

**Files:**

- Create/track: `apps/api/uv.lock`
- Create: `scripts/verify.sh`
- Modify: `.gitignore`
- Modify: `apps/api/Dockerfile`
- Modify: `apps/api/tests/test_dockerfile_data.py`
- Modify: `apps/web/Dockerfile`
- Modify: `deploy/docker-compose.yml`
- Modify: `.github/workflows/ci.yml`
- Modify: `README.md`
- Modify: `deploy/README.md`

### Step 1: Add frozen-build contract tests

Extend `test_dockerfile_data.py` to assert:

- API Dockerfile copies `uv.lock`, runs `uv sync --frozen --no-dev`, does not contain `pip install --no-cache-dir . pytest`, and does not install `curl` through apt;
- API healthcheck targets `/health/ready` using Python stdlib;
- API/web base `FROM` lines contain `@sha256:`;
- Compose ops-agent image contains `@sha256:` and API Compose healthcheck targets `/health/ready` without curl;
- workflow references `./scripts/verify.sh`, uses full 40-character action SHAs, and no longer runs ad-hoc `uv pip install`.

Run RED:

```sh
cd apps/api
.venv/bin/pytest -q tests/test_dockerfile_data.py
```

Expected: current mutable Dockerfiles/workflow fail the new assertions.

### Step 2: Track and regenerate the Python lock

Remove only the `uv.lock` line from `.gitignore`, then from `apps/api`:

```sh
uv lock
uv lock --check
uv sync --frozen --extra dev
rg -n 'name = "python-multipart"' uv.lock
```

Expected: lock check succeeds and `python-multipart` is present. Keep uv at `0.11.31` in CI and documentation.

### Step 3: Make the API image frozen and production-only

Pin the verified base index digest:

```dockerfile
FROM python:3.12-slim@sha256:09f7da3bc104798d0afb40bc08d23ab2da20a76130cec1f2ef170848f5d85217

ARG UV_VERSION=0.11.31
RUN pip install --no-cache-dir "uv==${UV_VERSION}"

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    PATH=/app/.venv/bin:$PATH

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
COPY alembic.ini ./
COPY alembic ./alembic
COPY data/emergency_sections.json ./data/emergency_sections.json
RUN uv sync --frozen --no-dev
```

Retain the unprivileged user/entrypoint setup. Remove apt/curl. Replace healthcheck with:

```dockerfile
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=4)"]
```

No dev extra is installed, so pytest must be absent from the image.

### Step 4: Pin web and ops-agent images

Use these full index digests:

```dockerfile
FROM node:24-alpine@sha256:e67514e5d0f6c46656005e1b693b2ec9d52e80b641307de684d4a015ba7a4eaf AS build
```

```dockerfile
FROM nginx:1.29-alpine@sha256:5616878291a2eed594aee8db4dade5878cf7edcb475e59193904b198d9b830de
```

and in Compose:

```yaml
ops-agent:
  image: docker:27-cli@sha256:851f91d241214e7c6db86513b270d58776379aacc5eb9c4a87e5b47115e3065c
```

Change the API Compose healthcheck to the same Python stdlib readiness probe so it remains valid after curl removal.

### Step 5: Create the canonical verification script

Create executable `scripts/verify.sh` with POSIX `sh`, `set -eu`, target validation and these exact bodies:

```sh
run_api() {
  (
    cd apps/api
    uv sync --frozen --extra dev
    uv run --frozen --extra dev ruff check .
    uv run --frozen --extra dev ruff format --check .
    PYTHONDONTWRITEBYTECODE=1 uv run --frozen --extra dev \
      python -m pytest -p no:cacheprovider -q
  )
}

run_web() {
  (
    cd apps/web
    npm ci
    npm run lint
    npm run build
    npm test
    npm run check-nav
  )
}

run_docker() {
  command -v docker >/dev/null 2>&1 || {
    echo "docker is required for the docker verification target" >&2
    return 127
  }
  sh -n deploy/ops-agent.sh
  HOST_ENV_FILE=./host.env.example \
    docker compose -f deploy/docker-compose.yml config --quiet
  docker build -t robopark-api:verify apps/api
  docker build -t robopark-web:verify apps/web
  docker run --rm --entrypoint python robopark-api:verify -c \
    "import importlib.util, multipart, robopark_api; assert importlib.util.find_spec('pytest') is None"
}
```

Dispatch `api`, `web`, `docker`, or default `all`; invalid targets print usage and exit `2`. Resolve repository root from the script path before running so invocation works outside the checkout directory.

### Step 6: Collapse CI onto the same command and pin actions

Replace the three drift-prone jobs with one `verify` job on `ubuntu-latest`, setup exact tool versions, then call the script. Use:

```yaml
- uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262 # v4
- uses: astral-sh/setup-uv@e58605a9b6da7c637471fab8847a5e5a6b8df081 # v5
  with:
    version: 0.11.31
    enable-cache: true
- uses: actions/setup-node@49933ea5288caeca8642d1e84afbd3f7d6820020 # v4
  with:
    node-version: 24.18.0
    cache: npm
    cache-dependency-path: apps/web/package-lock.json
- name: Verify repository
  run: ./scripts/verify.sh
```

Retain trigger and concurrency configuration. Add a reasonable job timeout, at least 30 minutes, because the canonical gate builds both images.

### Step 7: Document one source of truth

Update root and deploy READMEs:

- local API gate: `./scripts/verify.sh api`;
- local web gate: `./scripts/verify.sh web`;
- release/CI gate: `./scripts/verify.sh` on a Docker-capable host;
- lock updates require `cd apps/api && uv lock && uv lock --check`;
- digest updates are deliberate dependency changes and require a green full gate.

Do not document an environment variable that skips Docker or frozen locking.

### Step 8: Run local available gates

On the current Docker-less workstation:

```sh
./scripts/verify.sh api
./scripts/verify.sh web
sh -n deploy/ops-agent.sh
```

Then intentionally confirm the Docker target fails clearly rather than passing falsely:

```sh
./scripts/verify.sh docker
```

Expected locally: exit `127` and the explicit Docker requirement message. On a Docker-capable host/CI, the same command must pass.

### Step 9: Run the Docker-capable canonical gate twice

On CI or a Docker-capable clean checkout:

```sh
./scripts/verify.sh
./scripts/verify.sh
```

Expected on both consecutive runs: Ruff, backend, frontend, navigation, Compose config, both image builds and the production dependency assertion pass.

### Step 10: Commit

```sh
git add \
  .github/workflows/ci.yml \
  .gitignore \
  README.md \
  apps/api/Dockerfile \
  apps/api/tests/test_dockerfile_data.py \
  apps/api/uv.lock \
  apps/web/Dockerfile \
  deploy/README.md \
  deploy/docker-compose.yml \
  scripts/verify.sh
git diff --cached --check
git commit -m "build: freeze dependencies and unify verification"
```

---

## Final Stage 0 Review Checkpoint

### Step 1: Verify commit boundaries and repository hygiene

```sh
git status --short --branch
git log --oneline origin/main..HEAD
git diff origin/main...HEAD --check
git diff --name-only origin/main...HEAD
git ls-files apps/api/data
```

Expected:

- only the approved spec/plan plus seven Stage 0 commits are ahead of origin;
- no secret, DB, WAL/SHM, local attachment or cache files are tracked;
- `apps/api/data/emergency_sections.json` is the only intended tracked runtime-adjacent data file.

### Step 2: Review security and migration invariants

```sh
cd apps/api
.venv/bin/pytest -q \
  tests/test_emergency_router.py \
  tests/test_mechanic_emergency.py \
  tests/test_emergency_scope.py \
  tests/test_driver_emergency_scope.py \
  tests/test_models_migration.py \
  tests/test_report_attachments.py \
  tests/test_ops_agent.py
.venv/bin/alembic heads
```

Expected: tests pass and the sole head is `0015_report_attachments`.

### Step 3: Request independent code review

Invoke `superpowers:requesting-code-review` against the full Stage 0 range. Review priorities:

1. Emergency scope call ordering and legacy alias coverage;
2. attachment authorization, path containment, SQL/filesystem rollback and migration identity;
3. ops-agent never deleting host-owned env and never reporting success before readiness;
4. lock/image/action pins and absence of pytest in production image;
5. no accidental implementation of deferred PostgreSQL/Redis/browser-cache work.

Resolve any valid findings with focused tests and amend only the relevant task commit or add a clearly named follow-up commit.

### Step 4: Completion evidence

Before claiming Этап 0 complete, invoke `superpowers:verification-before-completion` and record:

- exact local API/web/static command outputs;
- exact Docker-capable/CI canonical gate result, including two consecutive passes;
- copied-SQLite `alembic current` result;
- final `git status --short --branch`;
- any external production preflight still requiring the host operator, especially missing attachment binaries.

Do not describe Stage 0 as fully complete while Docker/CI or production attachment preflight is unverified. Do not begin the PostgreSQL/Redis stages until this checkpoint is green and the next subsystem design is explicitly approved.
